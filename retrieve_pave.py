#!/usr/bin/env python3
"""
RETRIEVE-PAVE: Data Collection Engine
=====================================
VERSION: 1.1.2 (Twin-Based Mirroring)
"""

import argparse
import sys
import os
import re
import shutil
import subprocess
import tarfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

# Shared Infrastructure
from pave_utils import (
    Logger, resolve_meta, get_on_prem_tag, run_s3_sync,
    get_gpas_date, get_start_key, prune_empty_folders,
    setup_interrupt_handler,
    GCCS_BUCKET, GCCS_IP_BUCKET, GCCS_PREFIX, PREM_BUCKET, EGRESS_ROOT
)

# =============================================================================
# CLI ARGUMENT DEFINITION
# =============================================================================

def parse_args():
    parser = argparse.ArgumentParser(prog="retrieve_pave.py", description="Collect GOES-R products.")
    parser.add_argument("products", nargs="+", help="Product shortnames")
    parser.add_argument("--times", nargs="+", required=True, help="10-digit timestamps (YYYYDDDHH)")
    parser.add_argument("--scenes", nargs="*", choices=['f', 'c', 'm1', 'm2'], help="Scene filter")
    parser.add_argument("--dest", default=".", help="Workspace root")
    parser.add_argument("-j", "--threads", type=int, default=8, help="Concurrent threads")
    parser.add_argument("-q", "--quiet", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-d", "--debug", action="store_true")
    parser.add_argument("--skip-gccs", action="store_true")
    return parser.parse_args()

# =============================================================================
# COLLECTION LOGIC
# =============================================================================

def get_gccs_products(args, gccs_path, log):
    log.info("GCCS Discovery & Retrieval")
    combos = {}
    for prod in args.products:
        meta = resolve_meta(prod)
        key = (meta['instr'], meta['level'])
        if key not in combos: combos[key] = []
        combos[key].append(prod.lower())

    discovery_list = []
    for (instr, level), target_keys in combos.items():
        for sat in [18, 19]:
            base_prefix = f"{GCCS_PREFIX}/GOES-{sat}/{level}/{instr}/"
            for bucket in [GCCS_BUCKET, GCCS_IP_BUCKET]:
                cmd = ["aws", "s3api", "list-objects-v2", "--profile", "geocloud", "--bucket", bucket,
                       "--prefix", base_prefix, "--delimiter", "/", "--query", "CommonPrefixes[].Prefix", "--output", "text"]
                res = subprocess.run(cmd, capture_output=True, text=True)
                listing = res.stdout.strip().split()
                if not listing or "None" in listing: continue

                for pref in listing:
                    folder_name = Path(pref).name.lower()
                    base_name = folder_name.split('-')[0]
                    for prod_key in target_keys:
                        if base_name.startswith(prod_key):
                            if args.scenes:
                                if not any(base_name.endswith(s.lower()) for s in args.scenes): continue
                            discovery_list.append((bucket, pref, folder_name, instr))
                            break

    with ThreadPoolExecutor(max_workers=args.threads) as executor:
        for ts in args.times:
            year, doy = ts[:4], ts[4:7]
            for bucket_name, pref, folder_name, instr in discovery_list:
                dest = gccs_path / instr / folder_name / year / doy
                dest.mkdir(parents=True, exist_ok=True)
                executor.submit(run_s3_sync, f"s3://{bucket_name}/{pref}{year}/{doy}/",
                               dest, f"*_s{ts}*", log, label=f"Sync GCCS: {folder_name}")

def get_on_prem_products(args, gccs_path, prem_path, log):
    log.info("On-Prem Mirroring & Restructuring")

    # 1. Build Targeted Sync Map
    sync_map = {}
    for prod in args.products:
        meta = resolve_meta(prod)
        instr = meta['instr']
        key = (instr, meta['level'].lower(), instr if instr != "SEIS" else "SEISS")
        if key not in sync_map: sync_map[key] = []

        base_tag = get_on_prem_tag(prod)
        if args.scenes:
            for s in args.scenes:
                sync_map[key].append(f"{base_tag}{s.upper()}")
        else:
            sync_map[key].append(base_tag)

    # 2. Sync to Flat Temp Directory
    tmp_sync_dir = prem_path / ".tmp_sync"
    tmp_sync_dir.mkdir(parents=True, exist_ok=True)

    with ThreadPoolExecutor(max_workers=args.threads) as executor:
        for ts in args.times:
            year, doy, gpas_str = ts[:4], ts[4:7], get_gpas_date(ts)
            for (instr_name, level_str, instr_gpas), include_tags in sync_map.items():
                patterns = [f"*{tag}*_s{ts}*" for tag in include_tags]
                for sat in [18, 19]:
                    gpas_src = f"s3://{PREM_BUCKET}/op/GOES-{sat}/{level_str}/{instr_gpas}/{year}/{gpas_str}/"
                    executor.submit(run_s3_sync, gpas_src, tmp_sync_dir, patterns, log, label=f"Sync On-Prem: {instr_name}")

    # 3. Mirroring: Map GCCS Identities to their relative paths
    # gccs_map[IdentityPrefix] = RelativePath
    gccs_map = {}
    if gccs_path.exists():
        for gccs_file in gccs_path.rglob("*.nc"):
            # Identity is everything before _s (Product_Scene_Mode_Sat)
            identity = gccs_file.name.split('_s')[0]
            # Capture the relative directory (Instrument/Folder/Year/Doy)
            gccs_map[identity] = gccs_file.parent.relative_to(gccs_path)

    # 4. Restructure based on the GCCS Map
    all_files = list(tmp_sync_dir.rglob("*.nc"))
    for f_path in all_files:
        filename = f_path.name
        identity = filename.split('_s')[0]

        if identity in gccs_map:
            dest_dir = prem_path / gccs_map[identity]
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f_path), str(dest_dir / filename))
        else:
            log.warn(f"Orphan file: {filename} (No twin found in GCCS tree)")

    if tmp_sync_dir.exists(): shutil.rmtree(tmp_sync_dir)

# =============================================================================
# WRAPPERS & AUDIT (Silent Pruning)
# =============================================================================

def extract_ips(args, prem_path, gccs_path, log):
    gccs_ip_refs = list(gccs_path.rglob("*I_ABI*.nc"))
    if not gccs_ip_refs: return
    log.info(f"Targeted IP Recovery ({len(gccs_ip_refs)} files)")
    with ThreadPoolExecutor(max_workers=args.threads) as executor:
        for ts in args.times:
            doy = ts[4:7]
            for sat in [18, 19]:
                tar = f"GOES-{sat}_ABI_L2_IntermediateProducts_day{doy}_hour{ts[7:9]}.tar"
                executor.submit(run_s3_sync, f"s3://{EGRESS_ROOT}/GOES-{sat}/", prem_path, tar, log, label=f"IP Tar Sync")

    for tar_path in prem_path.glob("*.tar"):
        try:
            with tarfile.open(tar_path, 'r') as tf:
                members = tf.getmembers()
                for ip_ref in gccs_ip_refs:
                    target = prem_path / ip_ref.relative_to(gccs_path)
                    if target.exists(): continue
                    search_key = get_start_key(ip_ref.name)
                    for m in members:
                        if search_key in m.name:
                            target.parent.mkdir(parents=True, exist_ok=True)
                            with tf.extractfile(m) as s, open(target, 'wb') as d: d.write(s.read())
                            break
        finally:
            if tar_path.exists(): os.remove(tar_path)

def check_symmetry(args, gccs_path, prem_path, log):
    log.info("Retrieval Symmetry Audit")
    log.info(f"{'PRODUCT':<35} | {'STANDARD (G|P)':<14} | {'IP (G|P)':<12}")
    log.info("-" * 75)
    prem_index = {get_start_key(p.name).upper() for p in prem_path.rglob("*.nc") if any(ts in p.name for ts in args.times)}
    audit_map = {}
    if not gccs_path.exists(): return
    for gccs_file in gccs_path.rglob("*.nc"):
        if not any(ts in gccs_file.name for ts in args.times): continue
        parts = gccs_file.relative_to(gccs_path).parts
        identity = f"{parts[0]}/{parts[1]}"
        if identity not in audit_map: audit_map[identity] = [0, 0, 0, 0]
        gccs_key = get_start_key(gccs_file.name).upper()
        is_ip = "I_ABI" in gccs_file.name
        if is_ip: audit_map[identity][2] += 1
        else: audit_map[identity][0] += 1
        if gccs_key in prem_index:
            if is_ip: audit_map[identity][3] += 1
            else: audit_map[identity][1] += 1
    for identity, counts in sorted(audit_map.items()):
        g_s, p_s, g_i, p_i = counts
        status = "OK" if (g_s == p_s and g_i == p_i) else "!!"
        log.info(f"{status} {identity:<32} | {g_s:>5} | {p_s:<6} | {g_i:>4} | {p_i:<5}")

def run_collection(args, log):
    gccs, prem = Path(args.dest) / "gccs", Path(args.dest) / "prem"
    if not getattr(args, 'skip_gccs', False):
        get_gccs_products(args, gccs, log)
        prune_empty_folders(gccs)
    get_on_prem_products(args, gccs, prem, log)
    extract_ips(args, prem, gccs, log)
    prune_empty_folders(prem)
    check_symmetry(args, gccs, prem, log)

def main():
    args = parse_args()
    log = Logger("DEBUG" if args.debug else "VERBOSE" if args.verbose else "INFO")
    setup_interrupt_handler(log)
    run_collection(args, log)

if __name__ == "__main__":
    main()
