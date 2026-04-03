#!/usr/bin/env python3
"""
JUDGE-PAVE: Quality Gate Verdict Engine
=======================================
VERSION: 1.0.1 (Pandas Dtype Robustness Fix)
"""

import argparse
import pandas as pd
import sys
from pathlib import Path
from pave_utils import Logger, setup_interrupt_handler

class PaveJudge:
    def __init__(self, args, log):
        self.stats_dir = Path(args.stats_fld).resolve()
        self.threshold = getattr(args, 'threshold', 0.990)
        self.log = log

        self.stats_file = self.stats_dir / "glance_stats_summary.csv"
        self.meta_file = self.stats_dir / "metadata_audit.csv"
        self.verdict_file = self.stats_dir / "pave_final_verdict.csv"

    def _judge_science(self):
        science_results = {}
        if not self.stats_file.exists():
            self.log.warn("Science stats file missing. Gate FAIL.")
            return {}

        # Read CSV with initial space skipping to handle formatting variations
        df = pd.read_csv(self.stats_file, skipinitialspace=True)

        if df.empty:
            self.log.warn("Science stats file is empty. Gate FAIL.")
            return {}

        # Convert Metric to string to avoid AttributeError on numeric/NaN columns
        df['Metric'] = df['Metric'].astype(str)

        # Filter for r-squared metrics
        df_r2 = df[df['Metric'].str.contains('r-squared', case=False, na=False)]

        for product, group in df_r2.groupby('Product/Var'):
            # Extract base product name (strip variable suffix)
            prod_base = product.split('/')[0]
            if prod_base not in science_results:
                science_results[prod_base] = {"status": "PASS", "details": []}

            # Check every variable against threshold using the Mean column
            min_r2 = group['Mean'].min()
            if min_r2 < self.threshold:
                science_results[prod_base]["status"] = "FAIL"
                var_name = product.split('/')[-1]
                detail = f"Low R2 ({var_name}: {min_r2:.4f})"
                science_results[prod_base]["details"].append(detail)

        return science_results

    def _judge_metadata(self):
        meta_results = {}
        if not self.meta_file.exists():
            self.log.info("No metadata differences file found. Gate PASS.")
            return {}

        df = pd.read_csv(self.meta_file)

        if df.empty:
            return {}

        for product, group in df.groupby('Product'):
            # Gate fails on any row marked ERROR or WARNING
            bad_notes = group[group['Level'].isin(['ERROR', 'WARNING'])]

            if not bad_notes.empty:
                issue_list = []
                for _, row in bad_notes.iterrows():
                    issue_list.append(f"{row['Level']} in {row['Group']}")

                meta_results[product] = {
                    "status": "FAIL",
                    "details": issue_list
                }
            else:
                meta_results[product] = {
                    "status": "PASS",
                    "details": ["Known/Ignore only"]
                }

        return meta_results

    def execute(self):
        self.log.info(f"Judging Quality Gates (Threshold: {self.threshold})")

        sci_map = self._judge_science()
        meta_map = self._judge_metadata()

        # Combine all unique products found in either science or metadata audits
        all_products = sorted(set(sci_map.keys()) | set(meta_map.keys()))
        final_rows = []

        self.log.info("-" * 90)
        header = f"{'PRODUCT':<30} | {'SCIENCE':<10} | {'METADATA':<10} | {'VERDICT'}"
        self.log.info(header)
        self.log.info("-" * 90)

        for prod in all_products:
            s_res = sci_map.get(prod, {"status": "PASS", "details": ["No data"]})
            m_res = meta_map.get(prod, {"status": "PASS", "details": ["No diffs"]})

            # Final verdict logic: Both gates must pass
            if s_res['status'] == "PASS" and m_res['status'] == "PASS":
                is_pass = True
            else:
                is_pass = False

            verdict_icon = '👍 PASS' if is_pass else '👎 FAIL'
            row_log = f"{prod:<30} | {s_res['status']:<10} | {m_res['status']:<10} | {verdict_icon}"
            self.log.info(row_log)

            # Prepare row for CSV report
            final_rows.append({
                "Product": prod,
                "Science_Gate": s_res['status'],
                "Meta_Gate": m_res['status'],
                "Verdict": "PASS" if is_pass else "FAIL",
                "Science_Issues": "; ".join(s_res['details']),
                "Meta_Issues": "; ".join(m_res['details'])
            })

        # Save the final consolidated report
        verdict_df = pd.DataFrame(final_rows)
        verdict_df.to_csv(self.verdict_file, index=False)

        self.log.info("-" * 90)
        self.log.info(f"Final Verdict Report saved to: {self.verdict_file}")

def parse_args():
    parser = argparse.ArgumentParser(
        prog="judge_pave.py",
        description="Final verdict engine for GCCS vs On-Prem quality gates."
    )
    parser.add_argument(
        "stats_fld",
        help="Directory containing stats and metadata CSVs"
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.990,
        help="Minimum R-Squared Mean value for a science PASS"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )
    parser.add_argument(
        "-d", "--debug",
        action="store_true",
        help="Enable debug logging"
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Only show WARNING and ERROR logs"
    )
    return parser.parse_args()

def main():
    args = parse_args()

    if args.debug:
        lvl = "DEBUG"
    elif args.verbose:
        lvl = "VERBOSE"
    elif args.quiet:
        lvl = "QUIET"
    else:
        lvl = "INFO"

    log = Logger(lvl)
    setup_interrupt_handler(log)

    judge = PaveJudge(args, log)
    judge.execute()

if __name__ == "__main__":
    main()
