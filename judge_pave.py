#!/usr/bin/env python3
"""
JUDGE-PAVE: Quality Gate Verdict Engine
=======================================
VERSION: 1.0.0 (PASS/FAIL Analysis)
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
        sci = {}
        if not self.stats_file.exists():
            self.log.warn("Stats file missing. Gate FAIL.")
            return {}
        df = pd.read_csv(self.stats_file, skipinitialspace=True)
        dfr2 = df[df['Metric'].str.contains('r-squared', case=False, na=False)]
        for prod, group in dfr2.groupby('Product/Var'):
            pb = prod.split('/')[0]
            if pb not in sci:
                sci[pb] = {"status": "PASS", "details": []}
            min_r2 = group['Mean'].min()
            if min_r2 < self.threshold:
                sci[pb]["status"] = "FAIL"
                sci[pb]["details"].append(f"Low R2 ({prod.split('/')[-1]}: {min_r2:.4f})")
        return sci

    def _judge_metadata(self):
        meta = {}
        if not self.meta_file.exists():
            self.log.info("No audit file. Gate PASS.")
            return {}
        df = pd.read_csv(self.meta_file)
        for prod, group in df.groupby('Product'):
            bad = group[group['Level'].isin(['ERROR', 'WARNING'])]
            if not bad.empty:
                meta[prod] = {"status": "FAIL", "details": [f"{r['Level']} in {r['Group']}" for _, r in bad.iterrows()]}
            else:
                meta[prod] = {"status": "PASS", "details": ["Known/Ignore only"]}
        return meta

    def execute(self):
        self.log.info(f"Judging Quality Gates (Threshold: {self.threshold})")
        smap, mmap = self._judge_science(), self._judge_metadata()
        allp = sorted(set(smap.keys()) | set(mmap.keys()))
        final = []
        self.log.info("-" * 90)
        self.log.info(f"{'PRODUCT':<30} | {'SCIENCE':<10} | {'METADATA':<10} | {'VERDICT'}")
        self.log.info("-" * 90)
        for p in allp:
            sr, mr = smap.get(p, {"status": "PASS", "details": ["No data"]}), mmap.get(p, {"status": "PASS", "details": ["No diffs"]})
            isp = sr['status'] == "PASS" and mr['status'] == "PASS"
            self.log.info(f"{p:<30} | {sr['status']:<10} | {mr['status']:<10} | {'👍 PASS' if isp else '👎 FAIL'}")
            final.append({"Product": p, "Science_Gate": sr['status'], "Meta_Gate": mr['status'], "Verdict": "PASS" if isp else "FAIL", "Science_Issues": "; ".join(sr['details']), "Meta_Issues": "; ".join(mr['details'])})
        pd.DataFrame(final).to_csv(self.verdict_file, index=False)
        self.log.info("-" * 90)
        self.log.info(f"Verdict saved: {self.verdict_file}")

def parse_args():
    parser = argparse.ArgumentParser(prog="judge_pave.py")
    parser.add_argument("stats_fld", help="Stats directory")
    parser.add_argument("--threshold", type=float, default=0.990, help="R2 Gate")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose")
    parser.add_argument("-d", "--debug", action="store_true", help="Debug")
    parser.add_argument("-q", "--quiet", action="store_true", help="Quiet")
    return parser.parse_args()

def main():
    args = parse_args()
    lvl = "DEBUG" if args.debug else "VERBOSE" if args.verbose else "QUIET" if args.quiet else "INFO"
    log = Logger(lvl)
    setup_interrupt_handler(log)
    PaveJudge(args, log).execute()

if __name__ == "__main__":
    main()
