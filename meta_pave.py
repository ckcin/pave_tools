#!/usr/bin/env python3
"""
META-PAVE: Metadata Analysis Verification Engine
================================================
VERSION: 1.4.1 (Tiered Comparison Logic)
"""

import os
import re
import csv
import argparse
import sys
import math
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

# Keys that MUST be bit-identical
CRITICAL_KEYS = ["_FillValue", "valid_range", "valid_min", "valid_max", "scale_factor", "add_offset"]

# Tolerance for "Grey" numerical values (e.g., percentages, mean stats)
NUMERIC_TOLERANCE = 1e-6

WARN_STRINGS = [":data_name", "title", "summary"]
IGNORE_STRINGS = [":date_created", ":id", ":production_site", ":production_cluster", ":dataset_name", ":timeline_id"]
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
        self.goes_regex = re.compile(r"OR_(?P<dsn>.*?)_(?P<sat>G1[89]).*?s(?P<start>\d{14})")

        raw_dest = getattr(args, 'output', getattr(args, 'dest_fld', None))
        if not raw_dest:
            self.log.error("No output destination provided.")
            sys.exit(1)

        dest_path = Path(raw_dest)
        self.output_file = dest_path / "metadata_audit.csv" if dest_path.is_dir() else dest_path

    def filter_diff(self, group, key, p_val, g_val):
        """Tiered severity logic for mismatches."""
        identity = f"{group}:{key}"

        # 1. Ignored fields
        if any(s in identity for s in IGNORE_STRINGS): return "IGNORE"

        # 2. String comparison - Note potential typo fixes in GCCS
        if isinstance(p_val, str) and isinstance(g_val, str):
            if "Global Attributes" in group or "long_name" in key:
                return "NOTE"  # Fixing a typo in a description is a NOTE, not a FAIL
            return "WARNING"

        # 3. Numerical comparison
        if any(s in key for s in CRITICAL_KEYS):
            return "ERROR" # FillValues and Ranges must be exact

        return "WARNING" # Other numeric drifts are warnings

    def values_match(self, key, p, g):
        """Handles exact vs fuzzy matching based on key importance."""
        if p is g: return True
        if p is None or g is None: return False

        # Handle Numeric Arrays/Scalars
        if isinstance(p, (np.ndarray, list, float, int, np.number)):
            p_arr, g_arr = np.asanyarray(p), np.asanyarray(g)
            if p_arr.shape != g_arr.shape: return False

            # Check if this is a CRITICAL key requiring exact matching
            is_critical = any(ck in key for ck in CRITICAL_KEYS)

            if is_critical:
                return np.array_equal(p_arr, g_arr, equal_nan=True)
            else:
                # Fuzzy matching for non-critical numbers (e.g., percentages)
                return np.allclose(p_arr, g_arr, atol=NUMERIC_TOLERANCE, equal_nan=True)

        # Standard comparison for strings/others
        try: return p == g
        except ValueError: return np.array_equal(p, g)

    def compare_dicts(self, group_name, prem_dict, gccs_dict, prod_info):
        all_keys = set(prem_dict.keys()) | set(gccs_dict.keys())
        for key in sorted(all_keys):
            p_val, g_val = prem_dict.get(key), gccs_dict.get(key)

            # Use the new context-aware values_match
            if not self.values_match(key, p_val, g_val):
                diff_type = "GCCS ONLY" if p_val is None else "PREM ONLY" if g_val is None else "MISMATCH"
                level = self.filter_diff(group_name, key, p_val, g_val)

                # Add context for strings
                note = "Potential typo fix/improvement" if level == "NOTE" else ""

                self.results.append([level, prod_info['dsn'], prod_info['time'],
                                   f"{group_name}:{key}", diff_type, str(p_val), str(g_val), note])

    def analyze_pair(self, p_file, g_file, prod_info):
        self.log.debug(f"Comparing pair: {p_file.name}")
        try:
            with Dataset(p_file, 'r') as ds_p, Dataset(g_file, 'r') as ds_g:
                self.compare_dicts("Dimensions", {k: d.size for k, d in ds_p.dimensions.items()},
                                 {k: d.size for k, d in ds_g.dimensions.items()}, prod_info)
                self.compare_dicts("Global Attributes", {k: ds_p.getncattr(k) for k in ds_p.ncattrs()},
                                 {k: ds_g.getncattr(k) for k in ds_g.ncattrs()}, prod_info)
                for var in sorted(set(ds_p.variables.keys()) & set(ds_g.variables.keys())):
                    self.compare_dicts(f"Variable:{var}",
                                     {k: ds_p.variables[var].getncattr(k) for k in ds_p.variables[var].ncattrs()},
                                     {k: ds_g.variables[var].getncattr(k) for k in ds_g.variables[var].ncattrs()}, prod_info)
        except Exception as e:
            self.log.warn(f"Analysis failed for {p_file.name}: {e}")

    def execute(self):
        self.log.info("Metadata Analysis Verification (Tiered Logic Enabled)")
        if not HAS_LIBS:
            self.log.error("Missing required libraries: netCDF4, numpy")
            return

        for p_file in self.prem_fld.rglob("*.nc"):
            rel_path = p_file.relative_to(self.prem_fld)
            gccs_twin_dir = self.gccs_fld / rel_path.parent
            if not gccs_twin_dir.exists(): continue

            match = self.goes_regex.search(p_file.name)
            if not match: continue

            prod_info = {
                'dsn': match.group('dsn'),
                'time': match.group('start'),
                'prefix': f"OR_{match.group('dsn')}_{match.group('sat')}_s{match.group('start')}"
            }

            gccs_matches = list(gccs_twin_dir.glob(f"{prod_info['prefix']}*.nc"))
            if gccs_matches:
                self.analyze_pair(p_file, gccs_matches[0], prod_info)

        if self.results:
            with open(self.output_file, 'w', newline='') as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(REPORT_HEADERS)
                writer.writerows(self.results)
            self.log.info(f"Report generated: {self.output_file}")
        else:
            self.log.info("No metadata discrepancies found.")

def parse_args():
    parser = argparse.ArgumentParser(prog="meta_pave.py")
    parser.add_argument("prem_fld", help="Folder containing On-Prem data")
    parser.add_argument("gccs_fld", help="Folder containing GCCS data")
    parser.add_argument("output", help="CSV report filename or destination folder")
    parser.add_argument("-q", "--quiet", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-d", "--debug", action="store_true")
    return parser.parse_args()

def main():
    args = parse_args()
    lvl = "DEBUG" if args.debug else "VERBOSE" if args.verbose else "QUIET" if args.quiet else "INFO"
    log = Logger(lvl); setup_interrupt_handler(log)
    MetadataAuditor(args, log).execute()

if __name__ == "__main__":
    main()
