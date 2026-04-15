#!/usr/bin/env python3
"""
RETRIEVE-PAVE: Data Collection Engine
=====================================
VERSION: 1.3.1 (NC-Only Enforcement & IP Preservation)
"""

import argparse
import sys
import os
import shutil
import subprocess
import tarfile
import re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

# Shared Infrastructure
from pave_utils import (
    Logger, resolve_meta, get_on_prem_tag, run_s3_sync,
    get_gpas_date, get_start_key, prune_empty_folders,
    setup_interrupt_handler,
    GCCS_BUCKET, GCCS_IP_BUCKET, GCCS_PREFIX, PREM_BUCKET, EGRESS_ROOT
)

def normalize_channels(channels):
    """Ensures channel inputs are in the cXX format."""
    if not channels: return []
    normalized = []
    for c in channels:
        c_str = str(c).lower()
        if c_str.startswith('c'): normalized.append(c_str)
        else: normalized.append(f"c{c_str.zfill(2)}")
    return normalized

def match_folder(folder_name, meta, scenes, channels):
    """Filters S3 prefixes based on product, scene, and channel criteria."""
    fname = folder_name.lower()
    pk = meta['prod_base'].lower()
    if pk not in fname: return False

    if "ABI" in meta['instr'].upper():
        s_list = [s.lower() for s in scenes] if scenes else ['f', 'c', 'm1', 'm2']
        s_pattern = f"({'|'.join(s_list)})"
        c_pattern = f"-({'|'.join(channels)})" if channels else "(-c\\d{2})?"
        if not re.search(f"^{pk}.*{s_pattern}{c_pattern}$", fname):
            return False
    return True

def get_gccs_products(args, gccs_path, log):
    """Discovers and retrieves GCCS products from S3 (NetCDF only)."""
    log.info("GCCS Discovery & Retrieval")
    user_channels = normalize_channels(args.channels)
    discovery_list = []

    for prod_name in args.products:
        meta = resolve_meta(prod_name)
        meta['prod_base'] = prod_name.split('-')[-1] if '-' in prod_name else prod_name

        for sat in [18, 19]:
            base_prefix = f"{GCCS_PREFIX}/GOES-{sat}/{meta['level']}/{meta['instr']}/"
            for bucket in [GCCS_BUCKET, GCCS_IP_BUCKET]:
                cmd = ["aws", "s3api", "list-objects-v2", "--profile", "geocloud", "--bucket", bucket,
                       "--prefix", base_prefix, "--delimiter", "/", "--query", "CommonPrefixes[].Prefix", "--output", "text"]
                res = subprocess.run(cmd, capture_output=True, text=True)
                listing = res.stdout.strip().split()
                if not listing or "None" in listing: continue

                for pref in listing:
                    if match_folder(Path(pref).name.lower(), meta, args.scenes, user_channels):
                        discovery_list.append((bucket, pref, Path(pref).name.lower(), meta['instr']))

    with ThreadPoolExecutor(max_workers=args.threads) as executor:
        for ts in args.times:
            year, doy = ts[:4], ts[4:7]
            for bucket_name, pref, folder_name, instr in discovery_list:
                dest = gccs_path / instr / folder_name / year / doy
                dest.mkdir(parents=True, exist_ok=True)
                # Restricted to .nc files only
                executor.submit(run_s3_sync, f"s3://{bucket_name}/{pref}{year}/{doy}/",
                               dest, f"*_s{ts}*.nc", log, label=f"Sync GCCS: {folder_name}")

def get_on_prem_products(args, gccs_path, prem_path, log):
    """Mirrors On-Prem data based on GCCS identities (NetCDF only)."""
    log.info("On-Prem Mirroring & Restructuring")
    user_channels = [c.upper() for c in normalize_channels(args.channels)]
    sync_map = {}

    for prod in args.products:
        meta = resolve_meta(prod); instr = meta['instr']
        key = (instr, meta['level'].lower(), instr if instr != "SEIS" else "SEISS", prod.lower())
        if key not in sync_map: sync_map[key] = []

        base_tag = get_on_prem_tag(prod); include_patterns = []
        if "ABI" in instr.upper():
            target_channels = user_channels if user_channels else [""]
            for ch in target_channels:
                if args.scenes:
                    for s in args.scenes: include_patterns.append(f"{base_tag}*{s.upper()}*-M*{ch}")
                else:
                    include_patterns.append(f"{base_tag}*-M*{ch}")
        else:
            include_patterns.append(f"{base_tag}*")

        for pattern in include_patterns:
            sync_map[key].append(pattern)

    tmp_sync_dir = prem_path / ".tmp_sync"
    tmp_sync_dir.mkdir(parents=True, exist_ok=True)

    with ThreadPoolExecutor(max_workers=args.threads) as executor:
        for ts in args.times:
            year, doy, gpas_str = ts[:4], ts[4:7], get_gpas_date(ts)
            for (instr_name, level_str, instr_gpas, p_name), include_tags in sync_map.items():
                # Restricted to .nc files only
                patterns = [f"*{tag}*_s{ts}*.nc" for tag in include_tags]
                for sat in [18, 19]:
                    executor.submit(run_s3_sync, f"s3://{PREM_BUCKET}/op/GOES-{sat}/{level_str}/{instr_gpas}/{year}/{gpas_str}/",
                                   tmp_sync_dir, patterns, log, label=f"Sync On-Prem: {p_name}")

    # Restructure into symmetric folder tree
    gccs_map = {}
    if gccs_path.exists():
        for gccs_file in gccs_path.rglob("*.nc"):
            gccs_map[gccs_file.name.split('_s')[0]] = gccs_file.parent.relative_to(gccs_path)

    for f_path in list(tmp_sync_dir.rglob("*.nc")):
        identity = f_path.name.split('_s')[0]
        if identity in gccs_map:
            dest_dir = prem_path / gccs_map[identity]; dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f_path), str(dest_dir / f_path.name))
        else:
            log.warn(f"Orphan file: {f_path.name}")

    if tmp_sync_dir.exists(): shutil.rmtree(tmp_sync_dir)

def extract_ips(args, prem_path, gccs_path, log):
    """Extracts NetCDF members from IP tarballs and manages preservation."""
    gccs_ip_refs = list(gccs_path.rglob("*I_ABI*.nc"))
    if not gccs_ip_refs: return
    log.info(f"Targeted IP Recovery ({len(gccs_ip_refs)} files)")

    preserve_ip = getattr(args, 'preserve_ip', False)
    ip_data_dir = prem_path.parent / "ip_data"
    if preserve_ip:
        ip_data_dir.mkdir(parents=True, exist_ok=True)

    with ThreadPoolExecutor(max_workers=args.threads) as executor:
        for ts in args.times:
            for sat in [18, 19]:
                tar_name = f"GOES-{sat}_ABI_L2_IntermediateProducts_day{ts[4:7]}_hour{ts[7:9]}.tar"
                executor.submit(run_s3_sync, f"s3://{EGRESS_ROOT}/GOES-{sat}/", prem_path, tar_name, log, label=f"IP Tar Sync")

    for tar_path in prem_path.glob("*.tar"):
        try:
            with tarfile.open(tar_path, 'r') as tf:
                members = tf.getmembers()
                for ip_ref in gccs_ip_refs:
                    target = prem_path / ip_ref.relative_to(gccs_path)
                    if target.exists(): continue
                    for m in members:
                        if get_start_key(ip_ref.name) in m.name:
                            target.parent.mkdir(parents=True, exist_ok=True)
                            with tf.extractfile(m) as s, open(target, 'wb') as d: d.write(s.read())
                            break
        finally:
            if tar_path.exists():
                if preserve_ip:
                    # Relocate to ip_data/
                    log.info(f"Preserving IP Tar: {tar_path.name} -> ip_data/")
                    shutil.move(str(tar_path), str(ip_data_dir / tar_path.name))
                else:
                    os.remove(tar_path)

def check_symmetry(args, gccs_path, prem_path, log):
    """Final audit to verify file pair counts."""
    log.info("Retrieval Symmetry Audit")
    log.info(f"{'PRODUCT':<35} | {'STANDARD (G|P)':<14} | {'IP (G|P)':<12}")
    log.info("-" * 75)

    prem_index = {get_start_key(p.name).upper() for p in prem_path.rglob("*.nc") if any(ts in p.name for ts in args.times)}
    audit_map = {}
    if not gccs_path.exists():
        log.error("GCCS path does not exist. Retrieval failed.")
        return False

    for gccs_file in gccs_path.rglob("*.nc"):
        if not any(ts in gccs_file.name for ts in args.times): continue
        parts = gccs_file.relative_to(gccs_path).parts; identity = f"{parts[0]}/{parts[1]}"
        if identity not in audit_map: audit_map[identity] = [0, 0, 0, 0]

        is_ip = "I_ABI" in gccs_file.name
        if is_ip: audit_map[identity][2] += 1
        else: audit_map[identity][0] += 1

        if get_start_key(gccs_file.name).upper() in prem_index:
            if is_ip: audit_map[identity][3] += 1
            else: audit_map[identity][1] += 1

    for identity, counts in sorted(audit_map.items()):
        g_s, p_s, g_i, p_i = counts
        match = (g_s == p_s and g_i == p_i)
        log.info(f"{'OK' if match else '!!'} {identity:<32} | {g_s:>5} | {p_s:<6} | {g_i:>4} | {p_i:<5}")
    return True

def run_collection(args, log):
    dest_root = Path(getattr(args, 'dest', '.'))
    gccs, prem = dest_root / "gccs", dest_root / "prem"
    gccs.mkdir(parents=True, exist_ok=True); prem.mkdir(parents=True, exist_ok=True)

    if not getattr(args, 'skip_gccs', False):
        get_gccs_products(args, gccs, log); prune_empty_folders(gccs)

    get_on_prem_products(args, gccs, prem, log); extract_ips(args, prem, gccs, log); prune_empty_folders(prem)
    return check_symmetry(args, gccs, prem, log)

def parse_args():
    parser = argparse.ArgumentParser(prog="retrieve_pave.py")
    parser.add_argument("products", nargs="+", help="Product shortnames")
    parser.add_argument("--times", nargs="+", required=True, help="10-digit timestamps")
    parser.add_argument("--scenes", nargs="*", help="Scene filters (f, c, m1, m2)")
    parser.add_argument("--channels", nargs="*", help="Channel list (01-16)")
    parser.add_argument("--dest", default=".", help="Workspace root folder")
    parser.add_argument("--preserve-ip", action="store_true", help="Preserve IP tars in ip_data/")
    parser.add_argument("-j", "--threads", type=int, default=8, help="Sync threads")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("-d", "--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("-q", "--quiet", action="store_true", help="Minimal logging")
    return parser.parse_args()

def main():
    args = parse_args()
    lvl = "DEBUG" if args.debug else "VERBOSE" if args.verbose else "QUIET" if args.quiet else "INFO"
    log = Logger(lvl); setup_interrupt_handler(log); run_collection(args, log)

if __name__ == "__main__":
    main()