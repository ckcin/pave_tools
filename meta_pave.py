#!/usr/bin/env python3
"""
META-PAVE: Metadata Analysis Verification Engine
================================================
Standalone utility to compare NetCDF metadata between On-Prem and GCCS products.

DEVELOPMENT NOTE:
    This tool was developed with the assistance of Gemini 3 Flash (Paid Tier).

AUTHOR: Nick Carrasco
VERSION: 1.0.3 (Log Alignment Release)
"""

import os
import re
import csv
import argparse
import sys
import datetime
from pathlib import Path

try:
    from netCDF4 import Dataset
    import numpy as np
    HAS_LIBS = True
except ImportError:
    HAS_LIBS = False

# =============================================================================
# LOGGING (Synchronized with pave.py)
# =============================================================================

class Logger:
    def __init__(self, level="INFO"):
        self.levels = {"DEBUG": 0, "VERBOSE": 1, "INFO": 2, "QUIET": 3, "WARN": 3, "ERROR": 4}
        self.current_level = self.levels.get(level.upper(), 2)
        self.colors = {
            "DEBUG": "\033[94m",   # Blue
            "VERBOSE": "\033[36m", # Cyan
            "INFO": "\033[92m",    # Green
            "WARN": "\033[93m",    # Yellow
            "ERROR": "\033[91m",   # Red
            "RESET": "\033[0m"
        }

    def _msg(self, level, text):
        if self.levels.get(level, 2) >= self.current_level:
            ts = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            display_level = "WARN" if level == "QUIET" else level
            print(f"{ts} {self.colors.get(display_level, '')}[{display_level:<7}]{self.colors['RESET']} {text}", flush=True)

    def debug(self, text):   self._msg("DEBUG", text)
    def verbose(self, text): self._msg("VERBOSE", text)
    def info(self, text):    self._msg("INFO", text)
    def warn(self, text):    self._msg("WARN", text)
    def error(self, text):   self._msg("ERROR", text); sys.exit(1)

log = Logger()

# =============================================================================
# CLI ARGUMENT DEFINITION
# =============================================================================

def parse_args():
    """Defines the CLI interface for the metadata analyzer."""
    parser = argparse.ArgumentParser(
        prog="meta_pave.py",
        description="Compares NetCDF metadata between two folders and generates a discrepancy report."
    )
    # Positional Arguments
    parser.add_argument("prem_fld", help="Folder containing data produced On-Prem")
    parser.add_argument("gccs_fld", help="Folder containing data produced by GCCS")
    parser.add_argument("output", help="Filename for the CSV results")

    # Options (Aligned with pave.py)
    parser.add_argument("-q", "--quiet", action="store_true", help="Quiet mode (only WARN/ERROR)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose mode")
    parser.add_argument("-d", "--debug", action="store_true", help="Full debug mode")
    parser.add_argument("-O", "--overwrite", action="store_true", help="Overwrite existing output file")

    return parser.parse_args()

# =============================================================================
# CONFIGURATION & FILTERS
# =============================================================================

REPORT_HEADERS = ["Level", "Product", "StartTime", "Group", "Difference", "From_On_Prem", "From_GCCS", "Notes"]

WARN_STRINGS = [":data_name", ":dataset_name"]
IGNORE_STRINGS = [":date_created", ":id", ":production_site", "timeline_id", "date_created"]
KNOWN_STRINGS = ["algorithm_dynamic_input_data_container:"]

# =============================================================================
# CORE ANALYSIS ENGINE
# =============================================================================

class MetaAnalyzer:
    def __init__(self, args):
        self.prem_fld = Path(args.prem_fld)
        self.gccs_fld = Path(args.gccs_fld)
        self.output_file = args.output
        self.results = []

    def filter_diff(self, group, key):
        """Categorizes the discrepancy level based on predefined patterns."""
        identity = f"{group}:{key}"
        if any(s in identity for s in IGNORE_STRINGS): return "IGNORE"
        if any(s in identity for s in WARN_STRINGS):   return "WARNING"
        if any(s in identity for s in KNOWN_STRINGS):  return "KNOWN"
        return "ERROR"

    def values_match(self, p, g):
        """Robust comparison that handles scalars, arrays, NaNs, and String types."""
        if p is g: return True
        if p is None or g is None: return False

        if isinstance(p, np.ndarray) or isinstance(g, np.ndarray):
            p_arr, g_arr = np.asanyarray(p), np.asanyarray(g)
            if p_arr.shape != g_arr.shape: return False
            # Prevent isnan error on non-floating point types (e.g. Strings)
            if np.issubdtype(p_arr.dtype, np.floating):
                return np.array_equal(p_arr, g_arr, equal_nan=True)
            return np.array_equal(p_arr, g_arr)

        try:
            return p == g
        except ValueError:
            return np.array_equal(p, g)

    def compare_dicts(self, group_name, prem_dict, gccs_dict, prod_info):
        """Generic dictionary comparison for attributes/dimensions."""
        all_keys = set(prem_dict.keys()) | set(gccs_dict.keys())
        for key in sorted(all_keys):
            p_val = prem_dict.get(key)
            g_val = gccs_dict.get(key)

            if not self.values_match(p_val, g_val):
                diff_type = "MISMATCH"
                if p_val is None: diff_type = "GCCS ONLY"
                elif g_val is None: diff_type = "PREM ONLY"

                level = self.filter_diff(group_name, key)
                self.results.append([
                    level, prod_info['name'], prod_info['time'],
                    f"{group_name}:{key}", diff_type,
                    str(p_val) if p_val is not None else "",
                    str(g_val) if g_val is not None else "", ""
                ])

    def analyze_pair(self, p_file, g_file, prod_info):
        """Deep dive into file metadata."""
        log.debug(f"Comparing pair: {p_file.name} vs {g_file.name}")
        try:
            with Dataset(p_file, 'r') as ds_p, Dataset(g_file, 'r') as ds_g:
                # 1. Dimensions
                log.verbose(f"  Checking Dimensions for {prod_info['name']}")
                self.compare_dicts("Dimensions",
                    {k: d.size for k, d in ds_p.dimensions.items()},
                    {k: d.size for k, d in ds_g.dimensions.items()}, prod_info)

                # 2. Global Attributes
                log.verbose(f"  Checking Global Attributes for {prod_info['name']}")
                self.compare_dicts("Global Attributes",
                    {k: ds_p.getncattr(k) for k in ds_p.ncattrs()},
                    {k: ds_g.getncattr(k) for k in ds_g.ncattrs()}, prod_info)

                # 3. Variables & Attributes
                p_vars, g_vars = set(ds_p.variables.keys()), set(ds_g.variables.keys())
                self.compare_dicts("Variables", {v: "Present" for v in p_vars}, {v: "Present" for v in g_vars}, prod_info)

                for var in sorted(p_vars & g_vars):
                    log.debug(f"    Scanning Attributes for Variable: {var}")
                    self.compare_dicts(f"Variable:{var}",
                        {k: ds_p.variables[var].getncattr(k) for k in ds_p.variables[var].ncattrs()},
                        {k: ds_g.variables[var].getncattr(k) for k in ds_g.variables[var].ncattrs()}, prod_info)
        except Exception as e:
            log.warn(f"Analysis failed for {p_file.name}: {e}")

    def run(self):
        if not HAS_LIBS:
            log.error("Missing required libraries. Run: pip install netCDF4 numpy")

        log.info("Phase 5: Metadata Analysis Verification Engine Start")

        # Traverse On-Prem folders
        for p_file in self.prem_fld.rglob("*.nc"):
            match = re.search(r'OR_(.*)_s(\d{10})', p_file.name)
            if not match: continue
            prod_name, start_time = match.groups()

            # Match GCCS counterpart
            gccs_matches = list(self.gccs_fld.rglob(f"*_s{start_time}*"))
            if not gccs_matches:
                log.warn(f"No GCCS counterpart found for: {p_file.name}")
                continue

            self.analyze_pair(p_file, gccs_matches[0], {'name': prod_name, 'time': start_time})

        # Generate CSV
        log.info(f"Writing analysis report to: {self.output_file}")
        with open(self.output_file, 'w', newline='') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(REPORT_HEADERS)
            writer.writerows(self.results)

        log.info("Phase 5: Metadata Analysis Complete.")

# =============================================================================
# MAIN EXECUTION
# =============================================================================

def main():
    args = parse_args()

    # Priority Ladder for Log Levels
    if args.debug: lvl = "DEBUG"
    elif args.verbose: lvl = "VERBOSE"
    elif args.quiet: lvl = "QUIET"
    else: lvl = "INFO"

    global log
    log = Logger(lvl)

    if Path(args.output).exists() and not args.overwrite:
        confirm = input(f"Output file '{args.output}' exists. Overwrite? (y/N): ")
        if confirm.lower() != 'y':
            log.info("Aborting.")
            sys.exit(1)

    analyzer = MetaAnalyzer(args)
    analyzer.run()

if __name__ == "__main__":
    main()
