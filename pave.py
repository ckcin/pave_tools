#!/usr/bin/env python3
"""
PAVE: Product/Algorithm Verification Exercise (Orchestrator)
============================================================
The master suite controller for Retrieval and Analysis.
"""

import argparse
import sys
from pathlib import Path

# Sub-module imports
import retrieve_pave
import meta_pave
from pave_utils import Logger

def parse_args():
    parser = argparse.ArgumentParser(prog="pave.py", description="PAVE Suite Orchestrator")
    parser.add_argument("products", nargs="+", help="Product shortnames")
    parser.add_argument("--times", nargs="+", required=True, help="10-digit timestamps")
    parser.add_argument("--scenes", nargs="*", choices=['f', 'c', 'm1', 'm2'])
    parser.add_argument("--prefix", default="validation")
    parser.add_argument("--tag", default="test")
    parser.add_argument("--skip-retrieve", action="store_true", help="Skip retrieval stage")
    parser.add_argument("--skip-meta", action="store_true", help="Skip metadata stage")
    parser.add_argument("--skip-science", action="store_true", help="Skip science stage")
    parser.add_argument("-j", "--threads", type=int, default=8)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-d", "--debug", action="store_true")
    return parser.parse_args()

def main():
    args = parse_args()
    lvl = "DEBUG" if args.debug else "VERBOSE" if args.verbose else "INFO"
    log = Logger(lvl)
    
    workspace = Path(f"{args.prefix}_{args.times[0]}_{args.tag}")
    log.info(f"PAVE Orchestrator Active. Workspace: {workspace}")
    workspace.mkdir(exist_ok=True)

    # STAGE 1: Retrieval
    if not args.skip_retrieve:
        log.info(">>> STAGE 1: Data Collection & Retrieval")
        args.dest = workspace 
        # Ensure retrieve_pave knows to look in workspace
        retrieve_pave.run_collection(args, log)
    else:
        log.warn("Skipping Stage 1: Retrieval")

    # STAGE 2: Metadata Analysis
    if not args.skip_meta:
        log.info(">>> STAGE 2: Metadata Analysis Verification")
        report_path = workspace / f"metadata_discrepancies_{args.tag}.csv"
        # Create Namespace for analyzer
        meta_args = argparse.Namespace(
            prem_fld=workspace/"prem", 
            gccs_fld=workspace/"gccs", 
            output=report_path,
            overwrite=True
        )
        analyzer = meta_pave.MetaAnalyzer(meta_args, log)
        analyzer.run()
    else:
        log.warn("Skipping Stage 2: Metadata Analysis")

    # STAGE 3: Science Level Comparison
    if not args.skip_science:
        log.info(">>> STAGE 3: Science Level Comparison (Glance)")
        log.info("Placeholder: Pending Glance logic integration.")
    
    log.info(f"PAVE Run Complete. Results: {workspace.absolute()}")

if __name__ == "__main__":
    main()
