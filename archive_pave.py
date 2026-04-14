#!/usr/bin/env python3
"""
ARCHIVE-PAVE: Standalone Workspace Compression Utility
======================================================
VERSION: 1.1.2 (IP Tarball Relocation)
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
    # 1. IP RELOCATION: Move large tarballs out of 'prem' prior to archiving
    if path.name == "prem":
        ip_tars = list(path.glob("*.tar"))
        if ip_tars:
            log.info(f"Relocating {len(ip_tars)} IP Tarballs to Workspace Root...")
            for tar in ip_tars:
                dest = path.parent / tar.name
                try:
                    shutil.move(str(tar), str(dest))
                    log.verbose(f"  Moved: {tar.name}")
                except Exception as e:
                    log.warn(f"  Failed to move {tar.name}: {e}")

    # 2. Inventory the source
    source_files = [f for f in path.rglob("*") if f.is_file()]

    if not source_files:
        log.info(f"Cleanup: Removing empty folder structure {path.name}")
        try:
            shutil.rmtree(path)
            log.verbose(f"Purged empty directory: {path}")
        except Exception as e:
            log.warn(f"Failed to remove empty directory {path.name}: {e}")
        return

    # 3. Create Archive
    tar_name = f"{path.name}.tar.gz"
    tar_path = path.parent / tar_name
    
    log.info(f"Archiving {path.name} ({len(source_files)} files) -> {tar_name}")

    try:
        with tarfile.open(tar_path, "w:gz") as tar:
            tar.add(path, arcname=path.name)

        # 4. Verification Gate
        with tarfile.open(tar_path, "r:gz") as tar_check:
            archive_members = [m for m in tar_check.getmembers() if m.isfile()]

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
        description="Compress PAVE data folders and delete sources. Relocates IP tars."
    )
    parser.add_argument("path", help="Path to a PAVE workspace root or a specific folder")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-q", "--quiet", action="store_true")
    return parser.parse_args()

def main():
    args = parse_args()
    log = Logger("VERBOSE" if args.verbose else "QUIET" if args.quiet else "INFO")
    setup_interrupt_handler(log)
    run_archive(args.path, log)

if __name__ == "__main__":
    main()
