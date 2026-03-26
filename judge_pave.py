#!/usr/bin/env python3
"""
JUDGE-PAVE: Quality Gate Verdict Engine
=======================================
VERSION: 1.0.0 (Stage 6 - PASS/FAIL Analysis)
Rules:
1. Science: R-Squared correlation must be >= threshold for all vars.
2. Metadata: No ERROR or WARNING levels allowed.
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
        """Checks R-Squared values from Stage 5 output."""
        science_results = {}
        if not self.stats_file.exists():
            self.log.warn("Science stats file missing. Defaulting to FAIL for science gate.")
            return {}

        df = pd.read_csv(self.stats_file, skipinitialspace=True)
        # Filter for r-squared metrics
        df_r2 = df[df['Metric'].str.contains('r-squared', case=False, na=False)]

        for product, group in df_r2.groupby('Product/Var'):
            prod_base = product.split('/')[0]
            if prod_base not in science_results:
                science_results[prod_base] = {"status": "PASS", "details": []}
            
            # Check every variable against threshold
            min_r2 = group['Mean'].min() # Using Mean as the representative value
            if min_r2 < self.threshold:
                science_results[prod_base]["status"] = "FAIL"
                var_name = product.split('/')[-1]
                science_results[prod_base]["details"].append(f"Low Correlation ({var_name}: {min_r2:.4f})")
        
        return science_results

    def _judge_metadata(self):
        """Checks audit levels from Stage 2 output."""
        meta_results = {}
        if not self.meta_file.exists():
            self.log.info("No metadata differences found (file missing). Metadata gate PASS.")
            return {}

        df = pd.read_csv(self.meta_file)
        # Fail on WARNING or ERROR
        failures = df[df['Level'].isin(['ERROR', 'WARNING'])]

        for product, group in df.groupby('Product'):
            bad_notes = group[group['Level'].isin(['ERROR', 'WARNING'])]
            if not bad_notes.empty:
                meta_results[product] = {
                    "status": "FAIL", 
                    "details": [f"{row['Level']} in {row['Group']}" for _, row in bad_notes.iterrows()]
                }
            else:
                meta_results[product] = {"status": "PASS", "details": ["Known/Ignore only"]}
        
        return meta_results

    def execute(self):
        self.log.info(f"Executing Final Verdict (R2 Threshold: {self.threshold})")
        
        sci_map = self._judge_science()
        meta_map = self._judge_metadata()
        
        all_products = set(sci_map.keys()) | set(meta_map.keys())
        final_rows = []

        self.log.info("-" * 90)
        self.log.info(f"{'PRODUCT':<30} | {'SCIENCE':<10} | {'METADATA':<10} | {'VERDICT'}")
        self.log.info("-" * 90)

        for prod in sorted(all_products):
            s_res = sci_map.get(prod, {"status": "PASS", "details": ["No data"]})
            m_res = meta_map.get(prod, {"status": "PASS", "details": ["No diffs"]})
            
            is_pass = s_res['status'] == "PASS" and m_res['status'] == "PASS"
            verdict = "👍 PASS" if is_pass else "👎 FAIL"
            
            # Log to console
            self.log.info(f"{prod:<30} | {s_res['status']:<10} | {m_res['status']:<10} | {verdict}")
            
            # Prepare for CSV
            final_rows.append({
                "Product": prod,
                "Science_Gate": s_res['status'],
                "Meta_Gate": m_res['status'],
                "Verdict": "PASS" if is_pass else "FAIL",
                "Science_Issues": "; ".join(s_res['details']),
                "Meta_Issues": "; ".join(m_res['details'])
            })

        # Save the Verdict Report
        verdict_df = pd.DataFrame(final_rows)
        verdict_df.to_csv(self.verdict_file, index=False)
        self.log.info("-" * 90)
        self.log.info(f"Final Verdict Report saved to: {self.verdict_file}")

def main():
    parser = argparse.ArgumentParser(prog="judge_pave.py")
    parser.add_argument("stats_fld")
    parser.add_argument("--threshold", type=float, default=0.990)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-d", "--debug", action="store_true")
    parser.add_argument("-q", "--quiet", action="store_true")
    
    args = parser.parse_args()
    lvl = "DEBUG" if args.debug else "VERBOSE" if args.verbose else "QUIET" if args.quiet else "INFO"
    log = Logger(lvl)
    setup_interrupt_handler(log)
    PaveJudge(args, log).execute()

if __name__ == "__main__":
    main()
