#!/usr/bin/env python3
"""
RETRIEVE-PAVE: Data Collection Engine
=====================================
Specialized tool for GOES-R product retrieval.
"""

import argparse
import sys
import os
import re
import shutil
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from pave_utils import (
    Logger, resolve_meta, get_on_prem_tag, run_s3_sync, 
    get_gpas_date, get_start_key, prune_empty_folders
)

def parse_args():
    parser = argparse.ArgumentParser(prog="retrieve_pave.py", description="Collect GOES-R products.")
    parser.add_argument("products", nargs="+", help="Product shortnames")
    parser.add_argument("--times", nargs="+", required=True, help="10-digit timestamps")
    parser.add_argument("--scenes", nargs="*", choices=['f', 'c', 'm1', 'm2'])
    parser.add_argument("--dest", default=".", help="Root directory for downloaded data")
    parser.add_argument("-j", "--threads", type=int, default=8)
    parser.add_argument("-q", "--quiet", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-d", "--debug", action="store_true")
    parser.add_argument("--skip_gccs", action="store_true")
    return parser.parse_args()

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
            sat_id = f"GOES-{sat}"
            base_prefix = f"GCCS/op/{sat_id}/{level}/{instr}/"
            cmd = ["aws", "s3api", "list-objects-v2", "--profile", "geocloud", "--bucket", "gccs-products", 
                   "--prefix", base_prefix, "--delimiter", "/", "--query", "CommonPrefixes[].Prefix", "--output", "text"]
            res = subprocess.run(cmd, capture_output=True, text=True)
            listing = res.stdout.strip().split()
            if not listing or "None" in listing: continue
            for pref in listing:
                folder_name = Path(pref).name.lower()
                base_name = folder_name.split('-')[0]
                matched = False
                for prod_key in target_keys:
                    if base_name.startswith(prod_key):
                        if instr == "ABI":
                            if args.scenes and not any(base_name.endswith(s.lower()) for s in args.scenes): continue
                            if not args.scenes and not any(base_name.endswith(s) for s in ['f', 'c', 'm1', 'm2']): continue
                        discovery_list.append((pref, folder_name, instr))
                        matched = True; break
                    if matched: break

    with ThreadPoolExecutor(max_workers=args.threads) as executor:
        for ts in args.times:
            year, doy = ts[:4], ts[4:7]
            for pref, folder_name, instr in discovery_list:
                dest = gccs_path / instr / folder_name / year / doy
                dest.mkdir(parents=True, exist_ok=True)
                executor.submit(run_s3_sync, f"s3://gccs-products/{pref}{year}/{doy}/", dest, f"*_s{ts}*", log, label=f"Sync: {folder_name}")

def get_on_prem_products(args, gccs_path, prem_path, log):
    if not gccs_path.exists(): return
    log.info("On-Prem Mirroring & Restructuring")
    filing_guide, sync_map = [], {}
    for instr_dir in [d for d in gccs_path.iterdir() if d.is_dir()]:
        for prod_folder in [p for p in instr_dir.iterdir() if p.is_dir()]:
            base_low = prod_folder.name.split('-')[0].lower()
            filing_guide.append((base_low, instr_dir.name, prod_folder.name))
            meta = resolve_meta(prod_folder.name)
            key = (instr_dir.name, meta['level'].lower(), instr_dir.name if instr_dir.name != "SEIS" else "SEISS")
            if key not in sync_map: sync_map[key] = []
            sync_map[key].append(get_on_prem_tag(prod_folder.name))

    tmp_sync_dir = prem_path / ".tmp_sync"
    tmp_sync_dir.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=args.threads) as executor:
        for ts in args.times:
            year, doy, gpas_str = ts[:4], ts[4:7], get_gpas_date(ts)
            for (instr_name, level_str, instr_gpas), include_tags in sync_map.items():
                patterns = [f"*{tag}*_s{ts}*" for tag in include_tags]
                for sat in [18, 19]:
                    gpas_src = f"s3://geoproducts-ops/op/GOES-{sat}/{level_str}/{instr_gpas}/{year}/{gpas_str}/"
                    executor.submit(run_s3_sync, gpas_src, tmp_sync_dir, patterns, log, label=f"Sync: On-Prem {instr_name}")

    all_files = list(tmp_sync_dir.glob("*.nc"))
    guide_sorted = sorted(filing_guide, key=lambda x: len(x[0]), reverse=True)
    for f_path in all_files:
        filename, f_lower = f_path.name, f_path.name.lower()
        for base_low, instr_name, prod_actual in guide_sorted:
            if base_low in f_lower:
                date_match = re.search(r'_s(\d{4})(\d{3})', filename)
                if date_match:
                    f_year, f_doy = date_match.groups()
                    dest = prem_path / instr_name / prod_actual / f_year / f_doy
                    dest.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(f_path), str(dest / filename))
                    break
    if tmp_sync_dir.exists(): shutil.rmtree(tmp_sync_dir)

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
        prune_empty_folders(gccs, log)
    get_on_prem_products(args, gccs, prem, log)
    prune_empty_folders(prem, log)
    check_symmetry(args, gccs, prem, log)

def main():
    args = parse_args()
    lvl = "DEBUG" if args.debug else "VERBOSE" if args.verbose else "QUIET" if args.quiet else "INFO"
    log = Logger(lvl)
    run_collection(args, log)

if __name__ == "__main__":
    main()
