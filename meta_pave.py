#!/usr/bin/env python3
"""
META-PAVE: Metadata Analysis Verification Engine
================================================
Metadata comparison tool for NetCDF GOES-R products.
"""

import os
import re
import csv
import argparse
import sys
from pathlib import Path

from pave_utils import Logger, resolve_meta

try:
    from netCDF4 import Dataset
    import numpy as np
    HAS_LIBS = True
except ImportError:
    HAS_LIBS = False

REPORT_HEADERS = ["Level", "Product", "StartTime", "Group", "Difference", "From_On_Prem", "From_GCCS", "Notes"]
WARN_STRINGS = [":data_name", ":dataset_name"]
IGNORE_STRINGS = [":date_created", ":id", ":production_site", "timeline_id", "date_created"]
KNOWN_STRINGS = ["algorithm_dynamic_input_data_container:"]

def parse_args():
    parser = argparse.ArgumentParser(prog="meta_pave.py", description="Metadata analysis tool.")
    parser.add_argument("prem_fld", help="Folder containing On-Prem data")
    parser.add_argument("gccs_fld", help="Folder containing GCCS data")
    parser.add_argument("output", help="CSV report filename")
    parser.add_argument("-q", "--quiet", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-d", "--debug", action="store_true")
    parser.add_argument("-O", "--overwrite", action="store_true")
    return parser.parse_args()

class MetaAnalyzer:
    def __init__(self, args, log):
        self.prem_fld = Path(args.prem_fld)
        self.gccs_fld = Path(args.gccs_fld)
        self.output_file = args.output
        self.log = log
        self.results = []

    def filter_diff(self, group, key):
        identity = f"{group}:{key}"
        if any(s in identity for s in IGNORE_STRINGS): return "IGNORE"
        if any(s in identity for s in WARN_STRINGS):   return "WARNING"
        if any(s in identity for s in KNOWN_STRINGS):  return "KNOWN"
        return "ERROR"

    def values_match(self, p, g):
        if p is g: return True
        if p is None or g is None: return False
        if isinstance(p, np.ndarray) or isinstance(g, np.ndarray):
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
                self.compare_dicts("Dimensions", {k: d.size for k, d in ds_p.dimensions.items()},
                                 {k: d.size for k, d in ds_g.dimensions.items()}, prod_info)
                self.compare_dicts("Global Attributes", {k: ds_p.getncattr(k) for k in ds_p.ncattrs()},
                                 {k: ds_g.getncattr(k) for k in ds_g.ncattrs()}, prod_info)
                for var in sorted(set(ds_p.variables.keys()) & set(ds_g.variables.keys())):
                    self.compare_dicts(f"Variable:{var}", {k: ds_p.variables[var].getncattr(k) for k in ds_p.variables[var].ncattrs()},
                                     {k: ds_g.variables[var].getncattr(k) for k in ds_g.variables[var].ncattrs()}, prod_info)
        except Exception as e: self.log.warn(f"Analysis failed: {e}")

    def run(self):
        self.log.info("Metadata Analysis Verification")
        for p_file in self.prem_fld.rglob("*.nc"):
            match = re.search(r'OR_(.*)_s(\d{10})', p_file.name)
            if not match: continue
            prod_name, start_time = match.groups()
            gccs_matches = list(self.gccs_fld.rglob(f"*_s{start_time}*"))
            if not gccs_matches: continue
            self.analyze_pair(p_file, gccs_matches[0], {'name': prod_name, 'time': start_time})

        with open(self.output_file, 'w', newline='') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(REPORT_HEADERS)
            writer.writerows(self.results)
        self.log.info(f"Report generated: {self.output_file}")

def main():
    args = parse_args()
    lvl = "DEBUG" if args.debug else "VERBOSE" if args.verbose else "QUIET" if args.quiet else "INFO"
    log = Logger(lvl)
    if not HAS_LIBS: log.error("Missing netCDF4/numpy.")
    if Path(args.output).exists() and not args.overwrite: sys.exit("Output exists. Use -O.")
    analyzer = MetaAnalyzer(args, log)
    analyzer.run()

if __name__ == "__main__":
    main()
