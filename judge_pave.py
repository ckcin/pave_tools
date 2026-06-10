#!/usr/bin/env python3
"""
JUDGE-PAVE: Quality Gate Verdict Engine
=======================================
VERSION: 1.2.0 (Outlier Filename Tracing)
"""

import argparse
import pandas as pd
import numpy as np
import sys
from pathlib import Path
from pave_utils import Logger, setup_interrupt_handler

# ==============================================================================
# QUALITY GATE CONFIGURATION
# ==============================================================================
# PASS: All individual values in the timeseries must be >= this
SCI_PASS_THRESHOLD = 0.990

# FAIL: If any individual value is below this, it's a hard rejection
SCI_FAIL_LIMIT = 0.900

# VERDICT ICONS
ICON_PASS = "👍 PASS"
ICON_CHECK = "⚠️ CHECK"
ICON_FAIL = "👎 FAIL"
# ==============================================================================

class PaveJudge:
    def __init__(self, args, log):
        self.stats_dir = Path(args.stats_fld).resolve()
        self.log = log

        self.stats_file = self.stats_dir / "stats_summary.csv"
        self.meta_file = self.stats_dir / "metadata_audit.csv"
        self.verdict_file = self.stats_dir / "pave_final_verdict.csv"

    def _judge_science(self):
        """
        Tiered Science Logic (v1.2.0):
        - Scans alternating T/V pairs (Start Time / Value) starting at index 10.
        - FAIL: Any single data point < SCI_FAIL_LIMIT (0.900).
        - FAIL: Product-wide average (all points) < SCI_PASS_THRESHOLD (0.990).
        - CHECK: Product average >= 0.990, but one or more individual points < 0.990.
        - Logs the precise filename string (OR_DSN_SAT_sTIME) for outliers.
        """
        science_results = {}
        if not self.stats_file.exists():
            self.log.warn(f"Science stats file missing ({self.stats_file.name}).")
            return {}

        try:
            with open(self.stats_file, 'r') as f:
                first_line = f.readline()
                col_count = len(first_line.split(',')) + 50

            df = pd.read_csv(
                self.stats_file,
                names=range(col_count),
                skipinitialspace=True,
                low_memory=False,
                skiprows=1
            )
        except Exception as e:
            self.log.error(f"Failed to parse {self.stats_file.name}: {e}")
            return {}

        if df.empty:
            return {}

        # 0:Product, 1:Variable, 2:Sat, 3:Metric, 4:Count, 5:Min, 6:Max, 7:Mean, 8:Median, 9:NaN, 10:T1, 11:V1...
        df = df.rename(columns={0: 'Product', 1: 'Variable', 2: 'Sat', 3: 'Metric'})
        df['Metric'] = df['Metric'].astype(str).str.lower().str.strip()
        df_r2 = df[df['Metric'].str.contains('r-squared|r2', case=False, na=False)]

        if df_r2.empty:
            self.log.warn(f"No R-Squared metrics found in {self.stats_file.name}.")
            return {}

        for prod_base, group in df_r2.groupby('Product'):
            status = "PASS"
            details = []
            all_raw_points = []

            for _, row in group.iterrows():
                var_name = str(row['Variable'])
                sat = str(row['Sat'])
                prod_dsn = str(row['Product'])

                # Extract paired arrays of Timestamps and Values
                times = row.iloc[10::2].values
                vals = row.iloc[11::2].values

                for t, v in zip(times, vals):
                    if pd.isna(v): continue
                    try:
                        val_float = float(v)
                    except (ValueError, TypeError):
                        continue

                    all_raw_points.append(val_float)

                    if val_float < SCI_FAIL_LIMIT:
                        status = "FAIL"
                        filename_hint = f"OR_{prod_dsn}_{sat}_s{str(t).strip()}"
                        details.append(f"Fail {var_name} [{filename_hint}]: {val_float:.4f}")

                    elif val_float < SCI_PASS_THRESHOLD and status != "FAIL":
                        status = "CHECK"
                        filename_hint = f"OR_{prod_dsn}_{sat}_s{str(t).strip()}"
                        details.append(f"Check {var_name} [{filename_hint}]: {val_float:.4f}")

            if not all_raw_points:
                science_results[prod_base] = {"status": "CHECK", "details": ["No numeric timeseries found"]}
                continue

            product_avg = sum(all_raw_points) / len(all_raw_points)

            if product_avg < SCI_PASS_THRESHOLD:
                status = "FAIL"
                details.insert(0, f"Low Product Average R2: {product_avg:.4f}")

            science_results[prod_base] = {
                "status": status,
                "details": details
            }

        return science_results

    def _judge_metadata(self):
        """Nuanced Metadata Gate: FAIL on ERRORS, CHECK on WARNINGS."""
        meta_results = {}
        if not self.meta_file.exists():
            return {}

        df = pd.read_csv(self.meta_file)
        if df.empty:
            return {}

        for product, group in df.groupby('Product'):
            levels = group['Level'].unique()
            if "ERROR" in levels:
                status, issues = "FAIL", group[group['Level'] == "ERROR"]
                details = [f"ERR in {r['Group']}" for _, r in issues.iterrows()]
            elif "WARNING" in levels:
                status, issues = "CHECK", group[group['Level'] == "WARNING"]
                details = [f"WARN in {r['Group']}" for _, r in issues.iterrows()]
            else:
                status, details = "PASS", ["Ignore/Known only"]

            meta_results[product] = {"status": status, "details": details}
        return meta_results

    def execute(self):
        self.log.info(f"Judging Quality Gates (PASS >= {SCI_PASS_THRESHOLD}, FAIL < {SCI_FAIL_LIMIT})")
        self.log.info("Verifying all points in timeseries...")

        sci_map = self._judge_science()
        meta_map = self._judge_metadata()

        all_products = sorted(set(sci_map.keys()) | set(meta_map.keys()))
        final_rows = []

        self.log.info("-" * 110)
        self.log.info(f"{'PRODUCT (DSN)':<45} | {'SCIENCE':<10} | {'METADATA':<10} | {'VERDICT'}")
        self.log.info("-" * 110)

        for prod in all_products:
            # Default to CHECK if science metrics are missing but product exists in retrieval/meta
            s_res = sci_map.get(prod, {"status": "CHECK", "details": ["Missing science metrics"]})
            m_res = meta_map.get(prod, {"status": "PASS", "details": ["No metadata diffs"]})

            # Verdict Escalation: FAIL > CHECK > PASS
            if s_res['status'] == "FAIL" or m_res['status'] == "FAIL":
                verdict, icon = "FAIL", ICON_FAIL
            elif s_res['status'] == "CHECK" or m_res['status'] == "CHECK":
                verdict, icon = "CHECK", ICON_CHECK
            else:
                verdict, icon = "PASS", ICON_PASS

            self.log.info(f"{prod:<45} | {s_res['status']:<10} | {m_res['status']:<10} | {icon}")

            final_rows.append({
                "Product": prod,
                "Science_Gate": s_res['status'],
                "Meta_Gate": m_res['status'],
                "Verdict": verdict,
                "Science_Issues": "; ".join(s_res['details']),
                "Meta_Issues": "; ".join(m_res['details'])
            })

        pd.DataFrame(final_rows).to_csv(self.verdict_file, index=False)
        self.log.info("-" * 110)
        self.log.info(f"Final Verdict Report: {self.verdict_file}")

# ==============================================================================
# CLI ENTRY POINT
# ==============================================================================

def parse_args():
    parser = argparse.ArgumentParser(prog="judge_pave.py")
    parser.add_argument("stats_fld", help="Stats directory containing summary CSVs")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-d", "--debug", action="store_true")
    parser.add_argument("-q", "--quiet", action="store_true")
    return parser.parse_args()

def main():
    args = parse_args()
    log = Logger("DEBUG" if args.debug else "VERBOSE" if args.verbose else "QUIET" if args.quiet else "INFO")
    setup_interrupt_handler(log)
    PaveJudge(args, log).execute()

if __name__ == "__main__":
    main()
