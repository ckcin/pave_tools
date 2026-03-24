#!/usr/bin/env python3
"""
PAVE: Product Analysis & Verification Engine
============================================
The master orchestrator for GOES-R product comparison.
Coordinates Retrieval, Audit, Science, Collocation, and Stats.

VERSION: 1.1.3 (Full Orchestration & Workspace Construction)
"""

import argparse
import sys
from pathlib import Path

# Shared Infrastructure
from pave_utils import Logger, setup_interrupt_handler

# =============================================================================
# CLI ARGUMENT DEFINITION
# =============================================================================

def parse_args():
    parser = argparse.ArgumentParser(
        prog="pave.py",
        description="Orchestrate the GOES-R PAVE pipeline."
    )

    # 1. Selection Criteria
    parser.add_argument("products", nargs="+", help="Product shortnames (e.g., RadF DMW ABI-L2-LST)")
    parser.add_argument("--times", nargs="+", required=True, help="10-digit timestamps (YYYYDDDHH)")
    parser.add_argument("--scenes", nargs="*", choices=['f', 'c', 'm1', 'm2'], help="Scene filter")
    parser.add_argument("--channels", nargs="*", help="Channel filter (e.g., 01 13 or C01 C13)")

    # 2. Workspace Construction
    parser.add_argument("--prefix", help="Prefix for the job folder name")
    parser.add_argument("--tag", help="Suffix/Tag for the job folder name")
    parser.add_argument("--base-dir", default=".", help="Root directory where the job workspace is created")

    # 3. Skip Switches (For iterative testing)
    parser.add_argument("--skip-retrieve", action="store_true", help="Skip S3 sync/retrieval stage")
    parser.add_argument("--skip-meta", action="store_true", help="Skip metadata header auditing")
    parser.add_argument("--skip-science", action="store_true", help="Skip standard science level reports")
    parser.add_argument("--skip-collocate", action="store_true", help="Skip DMW/GLM collocation stage")
    parser.add_argument("--skip-stats", action="store_true", help="Skip final statistics harvesting")

    # 4. Operational Flags
    parser.add_argument("-j", "--threads", type=int, default=8, help="Concurrent threads for retrieval")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("-d", "--debug", action="store_true", help="Enable debug/passthrough logging")
    parser.add_argument("--bin", default="glance", help="Path to the glance executable")

    return parser.parse_args()

# =============================================================================
# WORKSPACE INITIALIZATION
# =============================================================================

def initialize_workspace(args, log):
    """
    Constructs the job directory: [PREFIX]_[FIRST_TS]_[TAG]
    And ensures all standard subdirectories exist.
    """
    # Build the folder name parts
    parts = []
    if args.prefix:
        parts.append(args.prefix)

    # Use the first timestamp provided as the anchor
    parts.append(args.times[0])

    if args.tag:
        parts.append(args.tag)

    job_folder_name = "_".join(parts)
    workspace_root = Path(args.base_dir) / job_folder_name

    # Define standard tree
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

# =============================================================================
# MAIN ORCHESTRATION
# =============================================================================

def main():
    args = parse_args()

    # Set logging level
    lvl = "DEBUG" if args.debug else "VERBOSE" if args.verbose else "INFO"
    log = Logger(lvl)

    # Enable graceful Ctrl+C handling
    setup_interrupt_handler(log)

    # Step 0: Create the Job Environment
    ws = initialize_workspace(args, log)

    # Step 1: Retrieval (S3 Mirroring)
    if not args.skip_retrieve:
        log.info("--- STAGE 1: RETRIEVAL ---")
        import retrieve_pave
        # Update destination to our newly created workspace root
        args.dest = str(ws['root'])
        retrieve_pave.run_collection(args, log)
    else:
        log.info("--- STAGE 1: RETRIEVAL (SKIPPED) ---")

    # Step 2: Metadata Audit (CSV headers)
    if not args.skip_meta:
        log.info("--- STAGE 2: METADATA AUDIT ---")
        from meta_pave import MetadataAuditor
        meta_args = argparse.Namespace(
            prem_fld=ws['prem'],
            gccs_fld=ws['gccs'],
            dest_fld=ws['stats']
        )
        MetadataAuditor(meta_args, log).execute()
    else:
        log.info("--- STAGE 2: METADATA AUDIT (SKIPPED) ---")

    # Step 3: Science Reports (UW-Glance)
    if not args.skip_science:
        log.info("--- STAGE 3: SCIENCE REPORTS ---")
        from science_pave import ScienceAnalyzer
        sci_args = argparse.Namespace(
            prem_fld=ws['prem'],
            gccs_fld=ws['gccs'],
            dest_fld=ws['glance'],
            bin=args.bin,
            fork=True,
            debug=args.debug,
            verbose=args.verbose
        )
        ScienceAnalyzer(sci_args, log).execute()
    else:
        log.info("--- STAGE 3: SCIENCE REPORTS (SKIPPED) ---")

    # Step 4: Collocation (Sparse DMW/GLM Data)
    is_sparse = any(p.upper().startswith(('DMW', 'GLM')) for p in args.products)
    if is_sparse and not args.skip_collocate:
        log.info("--- STAGE 4: COLLOCATION ---")
        from collocate_pave import CollocationAnalyzer
        coll_args = argparse.Namespace(
            prem_fld=ws['prem'],
            gccs_fld=ws['gccs'],
            coll_fld=ws['coll'],
            dest_fld=ws['glance'] / "collocated",
            cfg_fld="./glance_configs", # Ensure this path is correct for your environment
            bin=args.bin,
            verbose=args.verbose
        )
        CollocationAnalyzer(coll_args, log).execute()
    elif is_sparse:
        log.info("--- STAGE 4: COLLOCATION (SKIPPED) ---")

    # Step 5: Statistics Harvesting (Global Summary)
    if not args.skip_stats:
        log.info("--- STAGE 5: STATISTICS HARVESTING ---")

        # Defensive check: Ensure we have reports to harvest
        if any(ws['glance'].iterdir()):
            from stats_pave import StatsHarvester
            stats_args = argparse.Namespace(
                glance_fld=ws['glance'],
                dest_fld=ws['stats']
            )
            StatsHarvester(stats_args, log).execute()
        else:
            log.warn("Skipping Stats: The glance directory is empty.")
    else:
        log.info("--- STAGE 5: STATISTICS HARVESTING (SKIPPED) ---")

    log.info(f"PAVE Pipeline Complete. Data Root: {ws['root']}")

if __name__ == "__main__":
    main()
