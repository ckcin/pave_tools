#!/usr/bin/env python3
"""
PAVE: Product/Algorithm Verification Exercise
==============================================
A synchronization and orchestration engine for GOES-R satellite products.

DEVELOPMENT NOTE: 
    This tool was developed with the assistance of Gemini 3 Flash (Paid Tier).

CURRENT STATUS: v0.1.22 (Development Phase)
Focus: Start-Time Matching (Resolves _e and _c drift in Audit/Extraction).

CHANGELOG:
- Definitive Start-Time Matching: Keys are now truncated at '_e', matching only on '_s'.
- Phase 3 Optimization: Tarball extraction now uses the same start-time key logic.
- Phase 4 Audit: Aggregated counts now reflect functional identity, ignoring ground-clock drift.

AUTHOR: Nick Carrasco
VERSION: 0.1.22 (2026)
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

# Attempt psutil for robust process tree termination
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
# FEEDBACK & LOGGING SYSTEM
# =============================================================================

class Logger:
    def __init__(self, level="info"):
        self.levels = {"debug": 0, "verbose": 1, "info": 2, "quiet": 3}
        self.current_level = self.levels.get(level, 2)
        self.colors = {
            "debug": "\033[94m", "verbose": "\033[96m", "info": "\033[92m", 
            "warn": "\033[93m", "error": "\033[91m", "reset": "\033[0m"
        }

    def _msg(self, level, text):
        if self.levels.get(level, 2) >= self.current_level:
            ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            c = self.colors.get(level, self.colors["reset"])
            print(f"[{ts}] {c}[{level.upper():<7}]{self.colors['reset']} {text}", flush=True)

    def debug(self, text): self._msg("debug", text)
    def verbose(self, text): self._msg("verbose", text)
    def info(self, text): self._msg("info", text)
    def warn(self, text): self._msg("warn", text)
    def error(self, text): 
        self._msg("error", text)
        sys.exit(1)

log = Logger()

# =============================================================================
# TERMINATION HANDLER
# =============================================================================

def shutdown_handler(sig, frame):
    log.warn("Interrupt received (Ctrl-C). Cleaning up child processes...")
    if psutil:
        try:
            parent = psutil.Process(os.getpid())
            for child in parent.children(recursive=True):
                child.terminate()
            psutil.wait_procs(parent.children(), timeout=2)
        except Exception as e:
            log.debug(f"Shutdown error: {e}")
    log.error("PAVE Aborted by user.")

signal.signal(signal.SIGINT, shutdown_handler)

# =============================================================================
# CLI & HELPERS
# =============================================================================

def parse_args():
    parser = argparse.ArgumentParser(prog="pave.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("products", nargs="+", help="Product shortnames")
    parser.add_argument("--times", nargs="+", required=True, metavar="YYYYDDDHH", help="Timestamps")
    parser.add_argument("--scenes", nargs="*", choices=['f', 'c', 'm1', 'm2'], help="Optional ABI scene filter")
    parser.add_argument("--prefix", default="validation")
    parser.add_argument("--tag", default="test")
    parser.add_argument("-j", "--threads", type=int, default=8)
    parser.add_argument("--force_nodd", action="store_true")
    parser.add_argument("--verify_only", action="store_true")
    parser.add_argument("--extract_only", action="store_true")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-q", "--quiet", action="store_true")
    group.add_argument("-v", "--verbose", action="store_true")
    group.add_argument("-d", "--debug", action="store_true")
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
    """
    Truncates a GOES-R filename at the end-timestamp to create a match-key.
    Example: OR_ABI-L1b..._G18_s2026075120000 -> (Discards _e and _c)
    """
    return filename.split('_e')[0]

def run_s3_sync(src, dest, include_pattern=None, profile="geocloud", no_sign=False):
    cmd = ["aws", "s3", "sync", src, str(dest)]
    if profile and not no_sign: cmd += ["--profile", profile]
    if no_sign: cmd += ["--no-sign-request"]
    if include_pattern: cmd += ["--exclude", "*", "--include", include_pattern]
    cmd += ["--no-progress"]
    return subprocess.run(cmd, capture_output=True, text=True)

# =============================================================================
# CORE ENGINE PHASES
# =============================================================================

def get_gccs_products(args, gccs_path, executor):
    """Phase 1: Discovery & Retrieval."""
    log.info(f"Phase 1: GCCS Discovery & Retrieval for: {args.products}")
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
                    local_name = Path(pref).name 
                    if args.scenes and meta['instr'] == "ABI" and meta['level'] == "L2":
                        scene_id = resolve_scene_id(local_name)
                        if not scene_id or scene_id not in args.scenes: continue
                    product_root = gccs_path / meta['instr'] / local_name
                    date_leaf = product_root / year / doy
                    date_leaf.mkdir(parents=True, exist_ok=True)
                    log.verbose(f"Syncing GCCS: {local_name}")
                    executor.submit(run_s3_sync, f"s3://{GCCS_BUCKET}/{pref}", date_leaf, f"*_s{ts}*")
                    if meta['level'] == "L2":
                        executor.submit(run_s3_sync, f"s3://{GCCS_IP_BUCKET}/{pref}", product_root, f"*_s{ts}*")

def get_on_prem_products(args, gccs_path, prem_path, executor):
    """Phase 2: Mirroring."""
    log.info("Phase 2: On-Prem Mirroring")
    for ts in args.times:
        year, doy, gpas_str = ts[:4], ts[4:7], get_gpas_date(ts)
        for sat in [18, 19]:
            tar = f"GOES-{sat}_ABI_L2_IntermediateProducts_day{doy}_hour{ts[7:9]}.tar"
            executor.submit(run_s3_sync, f"s3://{EGRESS_ROOT}/GOES-{sat}/", prem_path, tar)
        if not gccs_path.exists(): return
        for leaf in gccs_path.rglob("*"):
            if not leaf.is_dir() or not list(leaf.glob(f"*_s{ts}*.nc")): continue
            prod_folder = leaf.parents[1].name
            meta = resolve_meta(prod_folder)
            instr_gpas = meta['instr'] if meta['instr'] != "SEIS" else "SEISS"
            if args.scenes and meta['instr'] == "ABI" and meta['level'] == "L2":
                scene_id = resolve_scene_id(prod_folder)
                if not scene_id or scene_id not in args.scenes: continue
            product_root_prem = prem_path / meta['instr'] / prod_folder
            date_leaf_prem = product_root_prem / year / doy
            chan = re.search(r'-c(\d{2})', prod_folder)
            pat = f"*{prod_folder.split('-')[0].upper()}*{f'*C{chan.group(1)}*' if chan else ''}_s{ts}*"
            for sat in [18, 19]:
                gpas_src = f"s3://{PREM_BUCKET}/op/GOES-{sat}/{meta['level'].lower()}/{instr_gpas}/{year}/{gpas_str}/"
                log.verbose(f"Mirroring On-Prem: {prod_folder} (G{sat})")
                if meta['level'] == "L2":
                    product_root_prem.mkdir(parents=True, exist_ok=True)
                    executor.submit(run_s3_sync, gpas_src, product_root_prem, pat)
                else:
                    date_leaf_prem.mkdir(parents=True, exist_ok=True)
                    executor.submit(run_s3_sync, gpas_src, date_leaf_prem, pat)

def extract_ips(prem_path, gccs_path):
    """Phase 3: Targeted IP Extraction using Start-Time keys."""
    log.info("Phase 3: Targeted IP Extraction")
    tarballs = list(prem_path.glob("*.tar"))
    if not tarballs: return
    gccs_ip_refs = list(gccs_path.rglob("*I_ABI*.nc"))
    for tar_path in tarballs:
        tar_sat = "G18" if "GOES-18" in tar_path.name else "G19" if "GOES-19" in tar_path.name else None
        try:
            with tarfile.open(tar_path, 'r') as tf:
                members = tf.getmembers()
                for ip_ref in gccs_ip_refs:
                    if tar_sat and tar_sat not in ip_ref.name: continue
                    target = prem_path / ip_ref.relative_to(gccs_path)
                    if target.exists(): continue
                    
                    # v0.1.22: Truncate at _e to match only on _s
                    search_key = get_start_key(ip_ref.name)
                    
                    for m in members:
                        if search_key in m.name:
                            target.parent.mkdir(parents=True, exist_ok=True)
                            with tf.extractfile(m) as s, open(target, 'wb') as d: d.write(s.read())
                            break
        except Exception as e: log.warn(f"Tar Error {tar_path.name}: {e}")

def check_symmetry(args, gccs_path, prem_path):
    """Phase 4: Mandatory Smart Audit using Start-Time Keys."""
    log.info("Phase 4: Verification of Retrieval Symmetry (Start-Time Audit)")
    log.info(f"{'PRODUCT (NO DATE)':<35} | {'STANDARD (G|P)':<14} | {'IP (G|P)':<12}")
    log.info("-" * 70)
    
    # Pre-index PREM by Start-Time Key (_s)
    prem_index = set()
    if prem_path.exists():
        for p_file in prem_path.rglob("*.nc"):
            if not any(ts in p_file.name for ts in args.times): continue
            prem_index.add(get_start_key(p_file.name))

    audit_map = {}
    if not gccs_path.exists(): return

    for gccs_file in gccs_path.rglob("*.nc"):
        if not any(ts in gccs_file.name for ts in args.times): continue
        parts = gccs_file.relative_to(gccs_path).parts
        identity = f"{parts[0]}/{parts[1]}"
        meta = resolve_meta(parts[1])
        if args.scenes and meta['instr'] == "ABI" and meta['level'] == "L2":
            scene_id = resolve_scene_id(parts[1])
            if not scene_id or scene_id not in args.scenes: continue

        if identity not in audit_map: audit_map[identity] = [0, 0, 0, 0]

        gccs_key = get_start_key(gccs_file.name)
        is_ip = "I_ABI" in gccs_file.name

        # Count GCCS
        if is_ip: audit_map[identity][2] += 1
        else: audit_map[identity][0] += 1
        
        # Match PREM using Functional Start-Time Key
        if gccs_key in prem_index:
            if is_ip: audit_map[identity][3] += 1
            else: audit_map[identity][1] += 1

    matched_count = 0
    for identity, counts in sorted(audit_map.items()):
        g_s, p_s, g_i, p_i = counts
        is_match = (g_s == p_s and g_i == p_i)
        status = "OK" if is_match else "!!"
        if is_match: matched_count += 1
        log.info(f"{status} {identity:<32} | {g_s:>5} | {p_s:<6} | {g_i:>4} | {p_i:<5}")

    summary_msg = f"Audit: {matched_count}/{len(audit_map)} Products Perfectly Synchronized."
    if matched_count == len(audit_map): log.info(summary_msg)
    else: log.warn(summary_msg)

# =============================================================================
# MAIN
# =============================================================================

def main():
    global log
    args = parse_args()
    if args.debug: log = Logger("debug")
    elif args.verbose: log = Logger("verbose")
    elif args.quiet: log = Logger("quiet")
    else: log = Logger("info")
    root = Path(f"{args.prefix}_{args.times[0][:9]}_{args.tag}")
    gccs, prem = root / "gccs", root / "prem"
    try:
        if not args.verify_only and not args.extract_only:
            with ThreadPoolExecutor(max_workers=args.threads) as executor:
                get_gccs_products(args, gccs, executor)
                get_on_prem_products(args, gccs, prem, executor)
        if not args.verify_only:
            extract_ips(prem, gccs)
        check_symmetry(args, gccs, prem)
    except KeyboardInterrupt: pass

if __name__ == "__main__":
    main()