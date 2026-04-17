#!/usr/bin/env python3
"""
PAVE: Product Analysis & Verification Engine
============================================
VERSION: 1.2.3 (Metadata Parameter Alignment)
"""

import argparse
import sys
from pathlib import Path
from pave_utils import Logger, setup_interrupt_handler

def parse_args():
    parser = argparse.ArgumentParser(prog="pave.py", description="Orchestrate the GOES-R PAVE pipeline.")

    # 1. Selection Criteria
    parser.add_argument("products", nargs="+", help="Product shortnames")
    parser.add_argument("--times", nargs="+", required=True, help="10-digit timestamps")
    parser.add_argument("--scenes", nargs="*", choices=['f', 'c', 'm1', 'm2'], help="Scene filter")
    parser.add_argument("--channels", nargs="*", help="Channel filter")

    # 2. Workspace Construction
    parser.add_argument("--prefix", help="Prefix for the job folder name")
    parser.add_argument("--tag", help="Suffix/Tag for the job folder name")
    parser.add_argument("--base-dir", default=".", help="Root directory for workspace")

    # 3. Skip Switches
    parser.add_argument("--skip-retrieve", action="store_true", help="Skip STAGE 1 - data retrieval")
    parser.add_argument("--skip-meta", action="store_true", help="Skip STAGE 2 - metadata comparisons")
    parser.add_argument("--skip-science", action="store_true", help="Skip STAGE 3 - run glance utility")
    parser.add_argument("--skip-collocate", action="store_true", help="Skip STAGE 4 - run collocation for DMW/GLM")
    parser.add_argument("--skip-stats", action="store_true", help="Skip STAGE 5 - run summary tool on glance results")
    parser.add_argument("--skip-judge", action="store_true", help="Skip STAGE 6 - run judgement stage")

    # 4. Engine Selection
    parser.add_argument("--use-compare", action="store_true", help="Use lightweight compare_pave.py instead of Glance")

    # 5. Operational Flags
    parser.add_argument("--preserve-ip", action="store_true", help="Move IP tars to ip_data/ instead of deleting")
    parser.add_argument("-j", "--threads", type=int, default=8, help="S3 sync threads")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logs")
    parser.add_argument("-d", "--debug", action="store_true", help="Debug logs")
    parser.add_argument("-q", "--quiet", action="store_true", help="Warn/Error only")
    parser.add_argument("--bin", default="glance", help="Path to glance")

    return parser.parse_args()

def initialize_workspace(args, log):
    parts = []
    if args.prefix:
        parts.append(args.prefix)

    parts.append(args.times[0])

    if args.tag:
        parts.append(args.tag)

    job_folder_name = "_".join(parts)
    workspace_root = Path(args.base_dir).resolve() / job_folder_name

    paths = {
        "root": workspace_root,
        "gccs": workspace_root / "gccs",
        "prem": workspace_root / "prem",
        "glance": workspace_root / "glance",
        "coll": workspace_root / "collocation",
        "stats": workspace_root / "stats"
    }

    log.info(f"Initializing Workspace: {job_folder_name}")
    for p in paths.values():
        p.mkdir(parents=True, exist_ok=True)

    return paths

def main():
    args = parse_args()

    # Determine Log Level
    lvl = "DEBUG" if args.debug else "VERBOSE" if args.verbose else "QUIET" if args.quiet else "INFO"

    log = Logger(lvl)
    setup_interrupt_handler(log)

    ws = initialize_workspace(args, log)

    # --- STAGE 1: RETRIEVAL ---
    if not args.skip_retrieve:
        log.info("--- STAGE 1: RETRIEVAL ---")
        import retrieve_pave
        args.dest = str(ws['root'])
        retrieve_pave.run_collection(args, log)

    # --- STAGE 2: METADATA AUDIT ---
    if not args.skip_meta:
        log.info("--- STAGE 2: METADATA AUDIT ---")
        from meta_pave import MetadataAuditor
        # Updated to use dest_fld to match Version 1.5.0
        meta_args = argparse.Namespace(
            prem_fld=ws['prem'], 
            gccs_fld=ws['gccs'], 
            dest_fld=ws['stats'], 
            quiet=args.quiet, 
            verbose=args.verbose, 
            debug=args.debug
        )
        MetadataAuditor(meta_args, log).execute()

    # --- STAGE 3: SCIENCE REPORTS ---
    if args.use_compare:
        log.info("--- STAGE 3/5: LIGHTWEIGHT COMPARISON ---")
        import compare_pave
        comp_args = argparse.Namespace(
            prem_fld=ws['prem'],
            gccs_fld=ws['gccs'],
            dest_fld=ws['glance'], 
            stats_fld=ws['stats'], 
            threads=args.threads,
            verbose=args.verbose,
            debug=args.debug
        )
        compare_pave.PaveComparator(comp_args, log).execute()
        args.skip_stats = True 
    elif not args.skip_science:
        log.info("--- STAGE 3: SCIENCE REPORTS (GLANCE) ---")
        from science_pave import ScienceAnalyzer
        sci_args = argparse.Namespace(
            prem_fld=ws['prem'], 
            gccs_fld=ws['gccs'], 
            dest_fld=ws['glance'], 
            bin=args.bin, 
            fork=True, 
            debug=args.debug, 
            verbose=args.verbose, 
            quiet=args.quiet
        )
        ScienceAnalyzer(sci_args, log).execute()

    # --- STAGE 4: COLLOCATION ---
    is_sparse = any(p.upper().startswith(('DMW', 'GLM', 'SUVI')) for p in args.products)
    if is_sparse and not args.skip_collocate:
        log.info("--- STAGE 4: COLLOCATION ---")
        from collocate_pave import CollocationAnalyzer
        coll_args = argparse.Namespace(
            prem_fld=ws['prem'], 
            gccs_fld=ws['gccs'], 
            coll_fld=ws['coll'], 
            dest_fld=ws['glance'] / "collocated", 
            cfg_fld="./glance_configs", 
            bin=args.bin, 
            verbose=args.verbose, 
            debug=args.debug, 
            quiet=args.quiet
        )
        CollocationAnalyzer(coll_args, log).execute()

    # --- STAGE 5: STATISTICS HARVESTING ---
    if not args.skip_stats:
        log.info("--- STAGE 5: STATISTICS HARVESTING ---")
        if any(ws['glance'].iterdir()):
            from stats_pave import StatsHarvester
            stats_args = argparse.Namespace(
                glance_fld=ws['glance'], 
                dest_fld=ws['stats'], 
                quiet=args.quiet, 
                verbose=args.verbose, 
                debug=args.debug
            )
            StatsHarvester(stats_args, log).execute()

    # --- STAGE 6: FINAL VERDICT ---
    if not args.skip_judge:
        log.info("--- STAGE 6: FINAL VERDICT ---")
        from judge_pave import PaveJudge
        judge_args = argparse.Namespace(
            stats_fld=ws['stats'], 
            quiet=args.quiet, 
            verbose=args.verbose, 
            debug=args.debug
        )
        PaveJudge(judge_args, log).execute()

    log.info(f"PAVE Pipeline Complete. Workspace: {ws['root']}")

if __name__ == "__main__":
    main()
