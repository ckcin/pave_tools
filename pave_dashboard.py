#!/usr/bin/env python3
"""
PAVE-DASHBOARD: Visual Report Consolidation Tool
================================================
VERSION: 1.6.0 (Strict DOY-Only Subfolder Architecture)
"""

import os
import re
import shutil
import argparse
import sys
from pathlib import Path

# Natively bind to your unified running infrastructure
from pave_utils import Logger, setup_interrupt_handler

# Captures parameters from your root folders: e.g., ACM_2026137010
WORKSPACE_REGEX = re.compile(
    r"^(?P<prod>[A-Za-z0-9_]+)_(?P<year>\d{4})(?P<doy>\d{3})(?P<hour>\d{2})\d+(?P<tag>_.*)?$"
)

# Extracts satellite ID safely from the native filename inside validation
FILE_SAT_REGEX = re.compile(r"_(?P<sat>G1[89])_")

def extract_workspace_meta(validation_dir, log):
    """
    Climbs upward from the deep validation subfolder to locate and 
    parse the true PAVE execution root workspace name.
    """
    current = validation_dir.resolve()
    while current != current.parent:
        match = WORKSPACE_REGEX.match(current.name)
        if match:
            log.debug(f"Successfully matched workspace root folder: '{current.name}'")
            return current.name, match
        current = current.parent
    return None, None

def discover_scene_tag(image_path, base_prod, log):
    """
    Inspects the directory path between 'validation' and the file to find the 
    instrument/product subfolder (e.g. acmc, acmf) and isolate the scene code (c, f, m1, m2).
    """
    path_parts = [p.lower() for p in image_path.parts]
    base_lower = base_prod.lower()

    for part in path_parts:
        if base_lower in part and part != base_lower:
            scene_suffix = part.replace(base_lower, "").strip()
            if scene_suffix in ['f', 'c', 'm1', 'm2']:
                log.debug(f"Isolated scene identifier via path tracking: '{scene_suffix.upper()}'")
                return f"_{scene_suffix.upper()}"
    return ""

def harvest_workspace(search_path, output_path, log):
    root_path = Path(search_path).resolve()
    dest_path = Path(output_path).resolve()

    if not root_path.exists():
        log.warn(f"Search path does not exist: {root_path}")
        return

    log.info(f"Scanning directory tree: {root_path}")
    dest_path.mkdir(parents=True, exist_ok=True)

    # Natively find all deep 'validation' endpoints under the CLI inputs
    validation_dirs = [p for p in root_path.rglob("validation") if p.is_dir()]
    
    if not validation_dirs:
        log.warn(f"No active 'validation' folders found under {root_path.name}")
        return

    log.info(f"Discovered {len(validation_dirs)} 'validation' groups to evaluate.")
    copied_count = 0

    for val_dir in validation_dirs:
        ws_name, match = extract_workspace_meta(val_dir, log)
        
        if not match:
            log.warn(f"SKIPPED deep node '{val_dir}' because no ancestor folder matched the PAVE workspace naming schema.")
            continue

        prod = match.group('prod')
        doy_group = match.group('doy')
        timestamp = f"{match.group('year')}{doy_group}{match.group('hour')}"
        custom_tag = match.group('tag') if match.group('tag') else ""
        
        # Enforced strict DOY sub-folder positioning directly under destination root
        target_dest_dir = dest_path / doy_group
        target_dest_dir.mkdir(parents=True, exist_ok=True)
        
        # Deep glob targets clean master "_comparison.png" charts
        dashboards = list(val_dir.glob("**/*_comparison.png"))

        if not dashboards:
            log.debug(f"No '*_comparison.png' dashboards generated yet inside: {ws_name}")
            continue

        # Filter out standalone numeric component frames (_1_GCCS, _2_PREM, etc.)
        valid_dashboards = [img for img in dashboards if not re.search(r"_\d_[A-Za-z0-9_]+_comparison\.png$", img.name)]

        if not valid_dashboards:
            log.debug(f"No true master dashboards found inside: {ws_name}")
            continue

        log.info(f"Harvesting {len(valid_dashboards)} charts from '{ws_name}' -> Subfolder: {doy_group}/")

        for img in valid_dashboards:
            var_name = img.name.replace("_comparison.png", "")
            
            scene_tag = discover_scene_tag(img, prod, log)
            full_prod_name = f"{prod}{scene_tag}{custom_tag.upper()}"

            sat_match = FILE_SAT_REGEX.search(img.name) or FILE_SAT_REGEX.search(str(img))
            sat_id = sat_match.group('sat') if sat_match else "GXX"

            new_filename = f"{full_prod_name}_{var_name}_{sat_id}_{timestamp}_comparison.png"
            target_file_path = target_dest_dir / new_filename

            try:
                shutil.copy2(str(img), str(target_file_path))
                copied_count += 1
                log.debug(f"  --> Consolidated: {doy_group}/{new_filename}")
            except Exception as e:
                log.warn(f"  Failed to capture {img.name}: {e}")

    log.info(f"Harvest complete! Successfully consolidated {copied_count} dashboards into isolated DOY matrices under: {dest_path}")

def main():
    parser = argparse.ArgumentParser(
        prog="pave_dashboard.py",
        description="Extract and structure PAVE 3x2 dashboards into clean day-of-year subfolders."
    )
    parser.add_argument(
        "paths", 
        nargs="+", 
        help="One or more run workspace roots to scan recursively."
    )
    parser.add_argument(
        "-o", "--output", 
        required=True, 
        help="Target parent folder to dump the clean DOY subfolder matrix."
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose tracking visibility")
    parser.add_argument("-d", "--debug", action="store_true", help="Deep diagnostic metrics tracing")
    parser.add_argument("-q", "--quiet", action="store_true", help="Restrict engine print feedback")

    args = parser.parse_args()
    
    lvl = "DEBUG" if args.debug else "VERBOSE" if args.verbose else "QUIET" if args.quiet else "INFO"
    log = Logger(lvl)
    setup_interrupt_handler(log)

    log.info("=========================================")
    log.info("  PAVE DASHBOARD CONSOLIDATOR INITIALIZED")
    log.info(f"  Target Destination: {os.path.abspath(args.output)}")
    log.info("=========================================")

    for search_root in args.paths:
        harvest_workspace(search_root, args.output, log)

if __name__ == "__main__":
    main()
