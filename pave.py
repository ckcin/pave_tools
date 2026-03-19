#!/usr/bin/env python3
"""
PAVE: Product/Algorithm Verification Exercise
==============================================
A synchronization and orchestration engine for GOES-R satellite products.

DEVELOPMENT NOTE:
    This tool was developed with the assistance of Gemini 3 Flash (Paid Tier).

CURRENT STATUS: v0.3.2 (Verification Gating)
Focus: Adding --verify_only and fixing check_symmetry parameters.

CHRONICLE:
- Feature: Added --verify_only flag to skip data collection and only run the audit.
- Bug Fix: Fixed parameter mismatch in check_symmetry call.
- Logic: Maintained 10-digit timestamp (YYYYDDDhhm) and ABI scene filtering.
- Hygiene: Maintained recursive pruning and JIT Tarball extraction.

AUTHOR: Nick Carrasco
VERSION: 0.3.2 (2026)
"""

import argparse
import sys
import os
import re
import datetime
import tarfile
import subprocess
import signal
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

# Optional but recommended for recursive child process management
try:
    import psutil
except ImportError:
    psutil = None

# =============================================================================
# GLOBAL CONFIGURATION & PRODUCT REGISTRY
# =============================================================================

GCCS_BUCKET = "gccs-products"
GCCS_IP_BUCKET = "gccs-intermediate-products"
GCCS_PREFIX = "GCCS/op"
PREM_BUCKET = "geoproducts-ops"
EGRESS_ROOT = "geoegress/egresout/DOE1L2IP"

PRODUCT_MAP = {
    "rad":   {"instr": "ABI",  "level": "L1b"},
    "geof":  {"instr": "MAG",  "level": "L1b"},
    "sfeu":  {"instr": "EXIS", "level": "L1b"},
    "sfxr":  {"instr": "EXIS", "level": "L1b"},
    "ehis":  {"instr": "SEIS", "level": "L1b"},
    "mpsl":  {"instr": "SEIS", "level": "L1b"},
    "mpsh":  {"instr": "SEIS", "level": "L1b"},
    "sgps":  {"instr": "SEIS", "level": "L1b"},
    "fe093": {"instr": "SUVI", "level": "L1b"},
    "fe131": {"instr": "SUVI", "level": "L1b"},
    "fe171": {"instr": "SUVI", "level": "L1b"},
    "fe195": {"instr": "SUVI", "level": "L1b"},
    "fe284": {"instr": "SUVI", "level": "L1b"},
    "he303": {"instr": "SUVI", "level": "L1b"},
    "lcfa":  {"instr": "GLM",  "level": "L2"},
    "fed":   {"instr": "GLM",  "level": "L2"},
}

# =============================================================================
# LOGGING & HYGIENE
# =============================================================================

class Logger:
    def __init__(self, level="INFO"):
        self.levels = {"DEBUG": 0, "INFO": 2, "WARN": 3, "ERROR": 4}
        self.current_level = self.levels.get(level.upper(), 2)
        self.colors = {"DEBUG": "\033[94m", "INFO": "\033[92m", "WARN": "\033[93m", "ERROR": "\033[91m", "RESET": "\033[0m"}

    def _msg(self, level, text):
        if self.levels.get(level, 2) >= self.current_level:
            ts = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            print(f"{ts} {self.colors.get(level, '')}[{level:<7}]{self.colors['RESET']} {text}", flush=True)

    def debug(self, text):   self._msg("DEBUG", text)
    def info(self, text):    self._msg("INFO", text)
    def warn(self, text):    self._msg("WARN", text)
    def error(self, text):   self._msg("ERROR", text); sys.exit(1)

log = Logger()

def prune_empty_folders(path):
    if not path.exists(): return
    for root, dirs, files in os.walk(path, topdown=False):
        for name in dirs:
            dir_path = Path(root) / name
            if not any(dir_path.iterdir()):
                dir_path.rmdir()

# =============================================================================
# HELPERS
# =============================================================================

def parse_args():
    parser = argparse.ArgumentParser(prog="pave.py", description=__doc__)
    parser.add_argument("products", nargs="+", help="Product shortnames")
    parser.add_argument("--times", nargs="+", required=True, metavar="YYYYDDDHHm", help="10-digit timestamps")
    parser.add_argument("--scenes", nargs="*", choices=['f', 'c', 'm1', 'm2'], help="ABI scene filter")
    parser.add_argument("--prefix", default="validation")
    parser.add_argument("--tag", default="test")
    parser.add_argument("-j", "--threads", type=int, default=8)
    parser.add_argument("--verify_only", action="store_true", help="Skip retrieval and only run symmetry check")
    parser.add_argument("-d", "--debug", action="store_true")
    return parser.parse_args()

def get_gpas_date(timestamp):
    year, doy = int(timestamp[:4]), int(timestamp[4:7])
    d = datetime.date(year, 1, 1) + datetime.timedelta(days=doy - 1)
    return d.strftime("%b/%Y%m%d").lower()

def resolve_meta(folder_name):
    base = folder_name.split('-')[0].lower()
    for key, meta in PRODUCT_MAP.items():
        if base.startswith(key): return meta
    return {"instr": "ABI", "level": "L2"}

def resolve_scene_id(folder_name):
    match = re.search(r'^[a-z]{3,4}(f|c|m1|m2)', folder_name.lower())
    return match.group(1) if match else None

def get_start_key(filename):
    return filename.split('_e')[0]

def run_s3_sync(src, dest, pattern, profile="geocloud", label=None):
    if label: log.info(label)
    source_uri = src if src.endswith('/') else f"{src}/"
    cmd = ["aws", "s3", "sync", source_uri, str(dest), "--exclude", "*", "--include", pattern, "--profile", profile, "--no-progress"]
    return subprocess.run(cmd, capture_output=True, text=True)

# =============================================================================
# CORE ENGINE PHASES
# =============================================================================

def get_gccs_products(args, gccs_path, threads):
    log.info(f"Phase 1: GCCS Discovery & Retrieval: {args.products}")
    with ThreadPoolExecutor(max_workers=threads) as executor:
        for ts in args.times:
            year, doy = ts[:4], ts[4:7]
            for sat in [18, 19]:
                sat_id = f"GOES-{sat}"
                for prod in args.products:
                    meta = resolve_meta(prod)
                    disc_prefix = f"{GCCS_PREFIX}/{sat_id}/{meta['level']}/{meta['instr']}/{prod.lower()}"
                    cmd = ["aws", "s3api", "list-objects-v2", "--profile", "geocloud", "--bucket", GCCS_BUCKET,
                           "--prefix", disc_prefix, "--delimiter", "/", "--query", "CommonPrefixes[].Prefix", "--output", "text"]
                    res = subprocess.run(cmd, capture_output=True, text=True)
                    prefixes = res.stdout.strip().split()
                    for pref in prefixes:
                        if pref == "None": continue
                        folder_name = Path(pref).name
                        if args.scenes and meta['instr'] == "ABI":
                            scene_id = resolve_scene_id(folder_name)
                            if not scene_id or scene_id not in args.scenes: continue

                        product_root = gccs_path / meta['instr'] / folder_name
                        date_leaf = product_root / year / doy
                        date_leaf.mkdir(parents=True, exist_ok=True)
                        pat = f"*_s{ts}*"
                        s3_src = f"s3://{GCCS_BUCKET}/{pref}{year}/{doy}/"
                        executor.submit(run_s3_sync, s3_src, date_leaf, pat, label=f"Retrieving GCCS: {sat_id} | {folder_name}")
                        if meta['level'].upper() == "L2":
                            s3_ip_src = f"s3://{GCCS_IP_BUCKET}/{pref}{year}/{doy}/"
                            executor.submit(run_s3_sync, s3_ip_src, product_root, pat, label=f"Retrieving GCCS IP: {sat_id} | {folder_name}")

def get_on_prem_products(args, gccs_path, prem_path, threads):
    if not gccs_path.exists(): return
    log.info("Phase 2: On-Prem Mirroring (Reference-Driven)")
    with ThreadPoolExecutor(max_workers=threads) as executor:
        for ts in args.times:
            year, doy, gpas_str = ts[:4], ts[4:7], get_gpas_date(ts)
            for instr_dir in [d for d in gccs_path.iterdir() if d.is_dir()]:
                for prod_folder in [p for p in instr_dir.iterdir() if p.is_dir()]:
                    date_leaf_gccs = prod_folder / year / doy
                    if not date_leaf_gccs.exists(): continue

                    base_prod = prod_folder.name.split('-')[0]
                    cased_tag = "Rad" if base_prod.lower() == "rad" else base_prod.upper()

                    meta = resolve_meta(prod_folder.name)
                    level_str, instr_gpas = meta['level'].lower(), (instr_dir.name if instr_dir.name != "SEIS" else "SEISS")
                    dest = prem_path / instr_dir.name / prod_folder.name / year / doy
                    dest.mkdir(parents=True, exist_ok=True)
                    pat = f"*{cased_tag}*_s{ts}*"
                    for sat in [18, 19]:
                        sat_id = f"GOES-{sat}"
                        gpas_src = f"s3://{PREM_BUCKET}/op/{sat_id}/{level_str}/{instr_gpas}/{year}/{gpas_str}/"
                        executor.submit(run_s3_sync, gpas_src, dest, pat, label=f"Mirroring On-Prem: {sat_id} | {prod_folder.name}")

def extract_ips(args, prem_path, gccs_path):
    log.info("Phase 3: Targeted IP Recovery")
    gccs_ip_refs = list(gccs_path.rglob("*I_ABI*.nc"))
    if not gccs_ip_refs: return
    with ThreadPoolExecutor(max_workers=args.threads) as executor:
        for ts in args.times:
            doy = ts[4:7]
            for sat in [18, 19]:
                tar = f"GOES-{sat}_ABI_L2_IntermediateProducts_day{doy}_hour{ts[7:9]}.tar"
                executor.submit(run_s3_sync, f"s3://{EGRESS_ROOT}/GOES-{sat}/", prem_path, tar, label=f"Retrieving Tarball: {tar}")
    tarballs = list(prem_path.glob("*.tar"))
    for tar_path in tarballs:
        tar_sat = "G18" if "GOES-18" in tar_path.name else "G19" if "GOES-19" in tar_path.name else None
        try:
            with tarfile.open(tar_path, 'r') as tf:
                members = tf.getmembers()
                for ip_ref in gccs_ip_refs:
                    if tar_sat and tar_sat not in ip_ref.name: continue
                    target = prem_path / ip_ref.relative_to(gccs_path)
                    if target.exists(): continue
                    search_key = get_start_key(ip_ref.name)
                    for m in members:
                        if search_key in m.name:
                            target.parent.mkdir(parents=True, exist_ok=True)
                            with tf.extractfile(m) as s, open(target, 'wb') as d: d.write(s.read())
                            break
        except Exception as e: log.warn(f"Tar Error {tar_path.name}: {e}")

def check_symmetry(args, gccs_path, prem_path):
    log.info("Phase 4: Verification of Retrieval Symmetry")
    log.info(f"{'PRODUCT (NO DATE)':<35} | {'STANDARD (G|P)':<14} | {'IP (G|P)':<12}")
    log.info("-" * 75)

    # Pre-index Prem files for fast lookup
    prem_index = {get_start_key(p.name).upper() for p in prem_path.rglob("*.nc") if any(ts in p.name for ts in args.times)}
    audit_map = {}

    if not gccs_path.exists():
        log.warn("GCCS path not found. Cannot verify.")
        return

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

def main():
    global log
    args = parse_args()
    if args.debug: log = Logger("DEBUG")

    root = Path(f"{args.prefix}_{args.times[0]}_{args.tag}")
    gccs, prem = root / "gccs", root / "prem"

    if not args.verify_only:
        get_gccs_products(args, gccs, args.threads)
        prune_empty_folders(gccs)

        get_on_prem_products(args, gccs, prem, args.threads)
        prune_empty_folders(prem)

        extract_ips(args, prem, gccs)

    check_symmetry(args, gccs, prem)
    log.info(f"v0.3.2 Run Complete. Workspace: {root.absolute()}")

if __name__ == "__main__":
    main()
