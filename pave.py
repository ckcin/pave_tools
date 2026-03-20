#!/usr/bin/env python3
"""
PAVE: Product/Algorithm Verification Exercise (Orchestrator)
============================================================
The master suite controller for Retrieval, Metadata, and Science.

VERSION: 1.2.3 (Scoping Fix & Science Integration)
"""

import argparse
import sys
from pathlib import Path

# Sub-module imports
import retrieve_pave
import meta_pave
import science_pave
from pave_utils import Logger, setup_interrupt_handler

# =============================================================================
# CLI ARGUMENT DEFINITION
# =============================================================================

def parse_args():
    """Defines the master CLI for the full PAVE suite."""
    parser = argparse.ArgumentParser(prog="pave.py", description="PAVE Suite Orchestrator")

    # Core Identification
    parser.add_argument("products", nargs="+", help="Product shortnames (e.g., radc acmc)")
    parser.add_argument("--times", nargs="+", required=True, help="10-digit timestamps (YYYYDDDHH)")
    parser.add_argument("--scenes", nargs="*", choices=['f', 'c', 'm1', 'm2'], help="ABI scene filter")

    # Workspace Config
    parser.add_argument("--prefix", default="validation", help="Folder prefix")
    parser.add_argument("--tag", default="test", help="Unique tag for this run")

    # Pipeline Toggles
    parser.add_argument("--skip-retrieve", action="store_true", help="Skip data collection")
    parser.add_argument("--skip-meta", action="store_true", help="Skip metadata comparison")
    parser.add_argument("--skip-science", action="store_true", help="Skip Glance analysis")

    # Global Options
    parser.add_argument("--fork", action="store_true", help="Enable parallel processing in Glance")
    parser.add_argument("-j", "--threads", type=int, default=8, help="Threads for retrieval")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    parser.add_argument("-d", "--debug", action="store_true", help="Debug output")

    return parser.parse_args()

# =============================================================================
# MAIN ORCHESTRATION
# =============================================================================

def main():
    # 1. Initialize arguments and logging
    args = parse_args()
    lvl = "DEBUG" if args.debug else "VERBOSE" if args.verbose else "INFO"
    log = Logger(lvl)

    # 2. Enable Graceful Interrupts (Ctrl+C)
    setup_interrupt_handler(log)

    # 3. Workspace Setup
    # Path format: validation_202607712_test
    workspace = Path(f"{args.prefix}_{args.times[0]}_{args.tag}")
    log.info(f"PAVE Orchestrator Active. Workspace: {workspace}")
    workspace.mkdir(exist_ok=True)

    # 4. STAGE 1: Retrieval
    if not args.skip_retrieve:
        log.info(">>> STAGE 1: Data Collection & Retrieval")
        # Inject the destination into args for the sub-module
        args.dest = workspace
        retrieve_pave.run_collection(args, log)
    else:
        log.warn("Skipping Stage 1: Retrieval")

    # 5. STAGE 2: Metadata Analysis
    if not args.skip_meta:
        log.info(">>> STAGE 2: Metadata Analysis Verification")
        report_path = workspace / f"metadata_discrepancies_{args.tag}.csv"

        # Build local namespace for meta_pave
        meta_args = argparse.Namespace(
            prem_fld=workspace/"prem",
            gccs_fld=workspace/"gccs",
            output=report_path,
            overwrite=True,
            debug=args.debug,
            verbose=args.verbose,
            quiet=False
        )
        analyzer = meta_pave.MetaAnalyzer(meta_args, log)
        analyzer.run()
    else:
        log.warn("Skipping Stage 2: Metadata Analysis")

    # 6. STAGE 3: Science Level Comparison (Glance)
    if not args.skip_science:
        log.info(">>> STAGE 3: Science Level Comparison (Glance)")

        # Build local namespace for science_pave
        sci_args = argparse.Namespace(
            prem_fld=workspace/"prem",
            gccs_fld=workspace/"gccs",
            dest_fld=workspace/"science_reports",
            fork=args.fork,
            bin="glance",
            debug=args.debug,
            verbose=args.verbose,
            quiet=False
        )
        science_engine = science_pave.ScienceAnalyzer(sci_args, log)
        science_engine.execute()
    else:
        log.warn("Skipping Stage 3: Science Comparison")

    log.info(f"PAVE Run Complete. Results: {workspace.absolute()}")

if __name__ == "__main__":
    main()
