#!/usr/bin/env python3
"""
PAVE: Product/Algorithm Verification Exercise (Orchestrator)
============================================================
VERSION: 1.2.9 (Native Stats Integration)
"""

import argparse
import sys
from pathlib import Path

# Sub-module imports
import retrieve_pave
import meta_pave
import science_pave
import stats_pave
from pave_utils import Logger, setup_interrupt_handler

def parse_args():
    parser = argparse.ArgumentParser(prog="pave.py")
    parser.add_argument("products", nargs="+", help="Product shortnames")
    parser.add_argument("--times", nargs="+", required=True, help="YYYYDDDHH timestamps")
    parser.add_argument("--scenes", nargs="*", choices=['f', 'c', 'm1', 'm2'])
    parser.add_argument("--prefix", default="py")
    parser.add_argument("--tag", default="test")
    parser.add_argument("--skip-retrieve", action="store_true")
    parser.add_argument("--skip-meta", action="store_true")
    parser.add_argument("--skip-science", action="store_true")
    parser.add_argument("--skip-stats", action="store_true") # Added toggle
    parser.add_argument("--fork", action="store_true")
    parser.add_argument("-j", "--threads", type=int, default=8)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-d", "--debug", action="store_true")
    return parser.parse_args()

def main():
    args = parse_args()
    log = Logger("DEBUG" if args.debug else "VERBOSE" if args.verbose else "INFO")
    setup_interrupt_handler(log)

    workspace = Path(f"{args.prefix}_{args.times[0]}_{args.tag}")
    log.info(f"PAVE Orchestrator Active. Workspace: {workspace}")
    workspace.mkdir(exist_ok=True)

    # 1. Retrieval & Gatekeeper
    retrieval_success = False
    if not args.skip_retrieve:
        log.info(">>> STAGE 1: Data Collection & Retrieval")
        args.dest = workspace
        retrieval_success = retrieve_pave.run_collection(args, log)
    else:
        log.warn("Skipping Stage 1. Validating existing data...")
        retrieval_success = retrieve_pave.check_symmetry(args, workspace/"gccs", workspace/"prem", log)

    if not retrieval_success:
        log.error("!!! SYMMETRY VALIDATION FAILED !!!")
        sys.exit(1)

    # 2. Metadata Analysis
    if not args.skip_meta:
        log.info(">>> STAGE 2: Metadata Analysis Verification")
        meta_args = argparse.Namespace(
            prem_fld=workspace/"prem", gccs_fld=workspace/"gccs",
            output=workspace / f"metadata_discrepancies_{args.tag}.csv",
            overwrite=True, debug=args.debug, verbose=args.verbose, quiet=False
        )
        meta_pave.MetaAnalyzer(meta_args, log).run()

    # 3. Science Comparison (Glance)
    if not args.skip_science:
        log.info(">>> STAGE 3: Science Level Comparison (Glance)")
        sci_args = argparse.Namespace(
            prem_fld=workspace/"prem", gccs_fld=workspace/"gccs",
            dest_fld=workspace/"glance", fork=args.fork,
            bin="glance", debug=args.debug, verbose=args.verbose, quiet=False
        )
        science_pave.ScienceAnalyzer(sci_args, log).execute()

    # 4. Statistical Summary (The Final Verdict)
    if not args.skip_stats:
        log.info(">>> STAGE 4: Statistical Summary Harvest")

        # Defensively check if the glance folder exists before proceeding
        glance_folder = workspace / "glance"
        if not glance_folder.exists():
            log.warn(f"!!! CANNOT RUN STAGE 4: {glance_folder} does not exist.")
            log.warn("Did Stage 3 (Glance) run successfully?")
        else:
            stats_args = argparse.Namespace(
                basepath=workspace,
                output_file=workspace / f"glance_summary_{args.tag}.csv",
                verbose=args.verbose, debug=args.debug,
                quiet=False, table=True, product=args.products
            )
            # Call the analyzer
            stats_pave.StatsAnalyzer(stats_args, log).execute()

    log.info(f"PAVE Run Complete. Results: {workspace.absolute()}")

if __name__ == "__main__":
    main()
