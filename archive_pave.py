#!/usr/bin/env python3
"""
ARCHIVE-PAVE: Standalone Workspace Compression Utility
======================================================
VERSION: 1.1.1 (Empty Folder Purge)
"""

import os
import tarfile
import shutil
import argparse
import sys
from pathlib import Path
from pave_utils import Logger, setup_interrupt_handler

# Folders we are allowed to compress and delete
TARGET_FOLDERS = ["gccs", "prem", "glance", "collocation", "coll"]

def run_archive(folder_path, log):
    path = Path(folder_path).resolve()

    if not path.exists():
        log.warn(f"Path does not exist: {path}")
        return

    # Check if the path is a PAVE root (contains multiple targets)
    sub_targets = [d for d in path.iterdir() if d.is_dir() and d.name in TARGET_FOLDERS]

    if sub_targets:
        log.info(f"Detected PAVE Workspace at {path.name}. Processing {len(sub_targets)} potential targets.")
        for target in sub_targets:
            perform_compression(target, log)
    else:
        # Otherwise, just process the single directory provided
        perform_compression(path, log)

def perform_compression(path, log):
    # 1. Inventory the source
    # We look for any file recursively to determine if data exists
    source_files = [f for f in path.rglob("*") if f.is_file()]

    if not source_files:
        # NEW LOGIC: If no files are found, simply delete the directory tree
        log.info(f"Cleanup: Removing empty folder structure {path.name}")
        try:
            shutil.rmtree(path)
            log.verbose(f"Purged empty directory: {path}")
        except Exception as e:
            log.warn(f"Failed to remove empty directory {path.name}: {e}")
        return

    # 2. Proceed with archiving if files exist
    tar_name = f"{path.name}.tar.gz"
    tar_path = path.parent / tar_name
    
    log.info(f"Archiving {path.name} ({len(source_files)} files) -> {tar_name}")

    try:
        # 3. Create the compressed archive
        with tarfile.open(tar_path, "w:gz") as tar:
            tar.add(path, arcname=path.name)

        # 4. Verify the archive count
        with tarfile.open(tar_path, "r:gz") as tar_check:
            archive_members = [m for m in tar_check.getmembers() if m.isfile()]

        # 5. Verification Gate
        if len(archive_members) == len(source_files):
            log.verbose(f"Verification Successful for {path.name}.")
            shutil.rmtree(path)
            log.info(f"Purged source folder: {path.name}")
        else:
            log.warn(f"!!! VERIFICATION FAILED for {path.name} !!!")
            log.warn(f"Source: {len(source_files)} vs Archive: {len(archive_members)}")
            log.warn("Source folder was preserved for safety.")

    except Exception as e:
        log.warn(f"Archive process failed for {path.name}: {e}")

def parse_args():
    parser = argparse.ArgumentParser(
        prog="archive_pave.py",
        description="Compress PAVE data folders and delete sources. Empty folders are removed."
    )
    parser.add_argument("path", help="Path to a PAVE workspace root or a specific folder")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-q", "--quiet", action="store_true")
    return parser.parse_args()

def main():
    args = parse_args()

    if args.verbose:
        lvl = "VERBOSE"
    elif args.quiet:
        lvl = "QUIET"
    else:
        lvl = "INFO"

    log = Logger(lvl)
    setup_interrupt_handler(log)

    run_archive(args.path, log)

if __name__ == "__main__":
    main()
