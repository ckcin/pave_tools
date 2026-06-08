#!/usr/bin/env python3
"""
META-PAVE: Metadata Analysis Verification Engine
================================================
VERSION: 1.5.2 (Smart De-duplicated Reporting)
"""

import os
import re
import csv
import argparse
import sys
from pathlib import Path

import numpy as np
import xarray as xr
from pave_utils import Logger, setup_interrupt_handler

# =============================================================================
# CONFIGURATION
# =============================================================================

# Keys that MUST be bit-identical
CRITICAL_KEYS = [
    "_FillValue",
    "valid_range",
    "valid_min",
    "valid_max",
    "scale_factor",
    "add_offset"
]

# Tolerance for "Grey" numerical values
NUMERIC_TOLERANCE = 1e-6

# Severity Search Keys
IGNORE_STRINGS = [
    "date_created",
    "id",
    "production_site",
    "production_cluster",
    "dataset_name",
    "timeline_id"
]

WARN_STRINGS = [
    "data_name",
    "title",
    "summary"
]

KNOWN_STRINGS = [
    "algorithm_dynamic_input_data_container"
]

class MetadataAuditor:
    def __init__(self, args, log):
        self.prem_fld = Path(args.prem_fld).resolve()
        self.gccs_fld = Path(args.gccs_fld).resolve()
        self.dest_fld = Path(args.dest_fld).resolve()
        self.log = log
        self.summary_csv = self.dest_fld / "metadata_audit_summary.csv"
        self.is_verbose = getattr(args, 'verbose', False)

        # Tracking for de-duplication
        self.reported_fingerprints = set()

    def determine_status(self, identity):
        """Tiered severity logic for mismatches."""
        if any(s in identity for s in IGNORE_STRINGS):
            return "IGNORE"

        if any(s in identity for s in KNOWN_STRINGS):
            return "KNOWN"

        if any(s in identity for s in WARN_STRINGS):
            return "WARNING"

        return "ERROR"

    def values_match(self, key, p, g):
        """Handles exact vs fuzzy matching based on key importance."""
        if p is g:
            return True

        if p is None or g is None:
            return False

        if isinstance(p, (np.ndarray, list, float, int, np.number)):
            p_arr = np.asanyarray(p)
            g_arr = np.asanyarray(g)

            if p_arr.shape != g_arr.shape:
                return False

            is_critical = any(ck in key for ck in CRITICAL_KEYS)

            if is_critical:
                return np.array_equal(p_arr, g_arr, equal_nan=True)
            else:
                return np.allclose(p_arr, g_arr, atol=NUMERIC_TOLERANCE, equal_nan=True)

        return str(p).strip() == str(g).strip()

    def compare_attributes(self, group_name, p_dict, g_dict):
        """Compares attribute sets and identifies tiered issues."""
        issues = []
        all_keys = set(p_dict.keys()) | set(g_dict.keys())

        for key in sorted(all_keys):
            p_val = p_dict.get(key)
            g_val = g_dict.get(key)

            if not self.values_match(key, p_val, g_val):
                identity = f"{group_name}:{key}"
                status = self.determine_status(identity)

                issues.append({
                    "Attribute": identity,
                    "Status": status,
                    "Prem": str(p_val),
                    "GCCS": str(g_val)
                })
        return issues

    def audit_file_pair(self, p_file, g_file):
        """Full inventory audit: Dimensions, Globals, and Variables."""
        file_issues = []
        try:
            with xr.open_dataset(p_file) as ds_p, xr.open_dataset(g_file) as ds_g:
                # 1. Audit Dimensions (Silent sizes check)
                p_dims = {k: v for k, v in ds_p.sizes.items()}
                g_dims = {k: v for k, v in ds_g.sizes.items()}
                file_issues.extend(self.compare_attributes("Dimensions", p_dims, g_dims))

                # 2. Audit Global Attributes
                file_issues.extend(self.compare_attributes("Global", ds_p.attrs, ds_g.attrs))

                # 3. Audit Variable Attributes
                common_vars = set(ds_p.variables.keys()) & set(ds_g.variables.keys())
                for var in sorted(common_vars):
                    file_issues.extend(self.compare_attributes(
                        f"Variable:{var}",
                        ds_p.variables[var].attrs,
                        ds_g.variables[var].attrs
                    ))

            return file_issues
        except Exception as e:
            self.log.debug(f"      [FAILED] Metadata read error: {e}")
            return [{"Attribute": "FILE_READ", "Status": "ERROR", "Prem": str(e), "GCCS": "N/A"}]

    def execute(self):
        """Scans workspace and generates the de-duplicated metadata audit report."""
        if not self.dest_fld.exists():
            self.dest_fld.mkdir(parents=True, exist_ok=True)

        self.log.info("Starting Metadata Audit (Version 1.5.2 Smart De-duplication)")

        with open(self.summary_csv, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(["File", "Attribute", "Status", "On-Prem Value", "GCCS Value"])

        nc_files = list(self.prem_fld.rglob("*.nc"))
        total_unique_issues = 0

        for p_file in nc_files:
            rel_path = p_file.relative_to(self.prem_fld)

            # Identify Product Type
            m_key = p_file.name.split('_c')[0] if "_c" in p_file.name else p_file.name

            gccs_twin_dir = self.gccs_fld / rel_path.parent
            if not gccs_twin_dir.exists():
                continue

            matches = list(gccs_twin_dir.glob(f"{m_key}_c*.nc")) if "_c" in p_file.name else \
                      [gccs_twin_dir / p_file.name] if (gccs_twin_dir / p_file.name).exists() else []

            if matches:
                g_file = matches[0]
                issues = self.audit_file_pair(p_file, g_file)

                if issues:
                    for issue in issues:
                        # Fingerprint: (Product, Attribute, Status, PremValue, GCCSValue)
                        # This allows anomalies (different values) to be reported while skipping repetitive identical errors.
                        fingerprint = (m_key, issue['Attribute'], issue['Status'], issue['Prem'], issue['GCCS'])

                        if fingerprint not in self.reported_fingerprints:
                            self.reported_fingerprints.add(fingerprint)
                            total_unique_issues += 1

                            with open(self.summary_csv, 'a', newline='') as f:
                                writer = csv.writer(f)
                                writer.writerow([
                                    p_file.name,
                                    issue['Attribute'],
                                    issue['Status'],
                                    issue['Prem'],
                                    issue['GCCS']
                                ])

                if self.is_verbose:
                    self.log.verbose(f"  Audited: {p_file.name}")

        status_msg = f"Audit Complete. Total Unique Discrepancies: {total_unique_issues}"
        if total_unique_issues > 0:
            self.log.warn(status_msg)
        else:
            self.log.info(status_msg)

        self.log.info(f"Metadata Report: {self.summary_csv}")

def parse_args():
    parser = argparse.ArgumentParser(prog="meta_pave.py")
    parser.add_argument("prem_fld")
    parser.add_argument("gccs_fld")
    parser.add_argument("dest_fld")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-d", "--debug", action="store_true")
    parser.add_argument("-q", "--quiet", action="store_true")
    return parser.parse_args()

def main():
    args = parse_args()
    lvl = "DEBUG" if args.debug else "VERBOSE" if args.verbose else "QUIET" if args.quiet else "INFO"
    log = Logger(lvl)
    setup_interrupt_handler(log)
    MetadataAuditor(args, log).execute()

if __name__ == "__main__":
    main()
