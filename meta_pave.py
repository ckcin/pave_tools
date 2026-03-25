#!/usr/bin/env python3
"""
META-PAVE: Metadata Analysis Verification Engine
================================================
VERSION: 1.3.5 (Default Overwrite & Mirror Matching)
Hierarchy:
  1. Navigate to equivalent GCCS subfolder.
  2. Match file by Full Identity Prefix (OR_..._sYYYYdddhhmmsss).
"""

import os
import re
import csv
import argparse
import sys
from pathlib import Path

# Shared Infrastructure
from pave_utils import Logger, setup_interrupt_handler

try:
    from netCDF4 import Dataset
    import numpy as np
    HAS_LIBS = True
except ImportError:
    HAS_LIBS = False

# =============================================================================
# CONFIGURATION
# =============================================================================

REPORT_HEADERS = ["Level", "Product", "StartTime", "Group", "Difference", "From_On_Prem", "From_GCCS", "Notes"]
WARN_STRINGS = [":data_name", ":dataset_name"]
IGNORE_STRINGS = [":date_created", ":id", ":production_site", "timeline_id", "date_created"]
KNOWN_STRINGS = ["algorithm_dynamic_input_data_container:"]

# =============================================================================
# CORE AUDIT ENGINE
# =============================================================================

class MetadataAuditor:
    def __init__(self, args, log):
        self.prem_fld = Path(args.prem_fld).resolve()
        self.gccs_fld = Path(args.gccs_fld).resolve()
        self.log = log
        self.results = []

        # Robust pathing for standalone 'output' vs orchestrator 'dest_fld'
        raw_dest = getattr(args, 'output', getattr(args, 'dest_fld', None))
        if not raw_dest:
            self.log.error("No output destination provided.")

        dest_path = Path(raw_dest)
        if dest_path.is_dir():
            self.output_file = dest_path / "metadata_audit.csv"
        else:
            self.output_file = dest_path

    def filter_diff(self, group, key):
        identity = f"{group}:{key}"
        if any(s in identity for s in IGNORE_STRINGS): return "IGNORE"
        if any(s in identity for s in WARN_STRINGS):   return "WARNING"
        if any(s in identity for s in KNOWN_STRINGS):  return "KNOWN"
        return "ERROR"

    def values_match(self, p, g):
        """Robust comparison handling scalars, arrays, and strings."""
        if p is g: return True
        if p is None or g is None: return False
        if isinstance(p, (np.ndarray, list)):
            p_arr, g_arr = np.asanyarray(p), np.asanyarray(g)
            if p_arr.shape != g_arr.shape: return False
            if np.issubdtype(p_arr.dtype, np.floating):
                return np.array_equal(p_arr, g_arr, equal_nan=True)
            return np.array_equal(p_arr, g_arr)
        try: return p == g
        except ValueError: return np.array_equal(p, g)

    def compare_dicts(self, group_name, prem_dict, gccs_dict, prod_info):
        all_keys = set(prem_dict.keys()) | set(gccs_dict.keys())
        for key in sorted(all_keys):
            p_val, g_val = prem_dict.get(key), gccs_dict.get(key)
            if not self.values_match(p_val, g_val):
                diff_type = "GCCS ONLY" if p_val is None else "PREM ONLY" if g_val is None else "MISMATCH"
                level = self.filter_diff(group_name, key)
                self.results.append([level, prod_info['name'], prod_info['time'],
                                   f"{group_name}:{key}", diff_type, str(p_val), str(g_val), ""])

    def analyze_pair(self, p_file, g_file, prod_info):
        self.log.debug(f"Comparing pair: {p_file.name}")
        try:
            with Dataset(p_file, 'r') as ds_p, Dataset(g_file, 'r') as ds_g:
                # Audit Dimensions
                self.compare_dicts("Dimensions", {k: d.size for k, d in ds_p.dimensions.items()},
                                 {k: d.size for k, d in ds_g.dimensions.items()}, prod_info)
                # Audit Global Attributes
                self.compare_dicts("Global Attributes", {k: ds_p.getncattr(k) for k in ds_p.ncattrs()},
                                 {k: ds_g.getncattr(k) for k in ds_g.ncattrs()}, prod_info)
                # Audit Variable-level Attributes
                for var in sorted(set(ds_p.variables.keys()) & set(ds_g.variables.keys())):
                    self.compare_dicts(f"Variable:{var}",
                                     {k: ds_p.variables[var].getncattr(k) for k in ds_p.variables[var].ncattrs()},
                                     {k: ds_g.variables[var].getncattr(k) for k in ds_g.variables[var].ncattrs()}, prod_info)
        except Exception as e:
            self.log.warn(f"Analysis failed for {p_file.name}: {e}")

    def execute(self):
        """Standard execution loop using mirrored folder-anchored matching."""
        self.log.info("Metadata Analysis Verification (Deep Audit)")

        if not HAS_LIBS:
            self.log.error("Missing required libraries: netCDF4, numpy")
            return

        # 1. Main Discovery Loop
        for p_file in self.prem_fld.rglob("*.nc"):
            rel_path = p_file.relative_to(self.prem_fld)
            gccs_twin_dir = self.gccs_fld / rel_path.parent

            if not gccs_twin_dir.exists():
                self.log.debug(f"  Skipping: Mirrored GCCS folder not found: {rel_path.parent}")
                continue

            match = re.search(r'^(OR_.*_s\d{14})', p_file.name)
            if not match: continue

            identity_prefix = match.group(1)
            parts = identity_prefix.split('_')
            prod_name, start_time = parts[1], parts[3][1:]

            # Match specifically in the mirrored folder
            gccs_matches = list(gccs_twin_dir.glob(f"{identity_prefix}*.nc"))
            if not gccs_matches:
                self.log.warn(f"  GCCS Mirror Missing: {identity_prefix} in {rel_path.parent}")
                continue

            self.analyze_pair(p_file, gccs_matches[0], {'name': prod_name, 'time': start_time})

        # 2. Save Results (Overwrite by default)
        if self.results:
            with open(self.output_file, 'w', newline='') as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(REPORT_HEADERS)
                writer.writerows(self.results)
            self.log.info(f"Report generated: {self.output_file}")
        else:
            self.log.info("No metadata discrepancies found.")

# =============================================================================
# CLI PARSER
# =============================================================================

def parse_args():
    parser = argparse.ArgumentParser(
        prog="meta_pave.py",
        description="Compares NetCDF metadata between mirrored folders and generates a report."
    )
    # Positional Arguments
    parser.add_argument("prem_fld", help="Folder containing On-Prem data")
    parser.add_argument("gccs_fld", help="Folder containing GCCS data")
    parser.add_argument("output", help="CSV report filename or destination folder")

    # Options
    parser.add_argument("-q", "--quiet", action="store_true", help="Only display WARNING/ERROR logs")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("-d", "--debug", action="store_true", help="Enable debug logging")

    return parser.parse_args()

def main():
    args = parse_args()
    lvl = "DEBUG" if args.debug else "VERBOSE" if args.verbose else "QUIET" if args.quiet else "INFO"
    log = Logger(lvl)

    setup_interrupt_handler(log)
    MetadataAuditor(args, log).execute()

if __name__ == "__main__":
    main()
