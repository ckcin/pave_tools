#!/usr/bin/env python3
"""
PAVE: Product/Algorithm Verification Exercise
==============================================
A synchronization and orchestration engine for GOES-R satellite products.

DEVELOPMENT NOTE:
    This tool was developed with the assistance of Gemini 3 Flash (Paid Tier).

CURRENT STATUS: v1.0.6 (Metadata Resolution Fix)
Focus: Fixing the L1b Rad radiance retrieval by restoring prefix-aware metadata resolution.

CHRONICLE:
- Logic Fix: resolve_meta restored to greedy prefix matching (prevents Rad/L2 fall-through).
- Feature: Maintained prune_empty_folders and standalone CLI verbosity flags.
- Logic: get_on_prem_tag correctly appends scene to the hard-wired PRODUCT_MAP['tag'].
- Hygiene: All sync and discovery logs remain at DEBUG/VERBOSE.

AUTHOR: Nick Carrasco
VERSION: 1.0.6 (2026)
"""

import argparse
import sys
import os
import re
import datetime
import tarfile
import subprocess
import shutil
import shlex
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

# =============================================================================
# GLOBAL CONFIGURATION & PRODUCT REGISTRY
# =============================================================================

GCCS_BUCKET = "gccs-products"
GCCS_IP_BUCKET = "gccs-intermediate-products"
GCCS_PREFIX = "GCCS/op"
PREM_BUCKET = "geoproducts-ops"
EGRESS_ROOT = "geoegress/egresout/DOE1L2IP"

PRODUCT_MAP = {
    "rad":   {"instr": "ABI",  "level": "L1b", "tag": "Rad"},
    "cmi":   {"instr": "ABI",  "level": "L2",  "tag": "CMIP"},
    "cmip":  {"instr": "ABI",  "level": "L2",  "tag": "CMIP"},
    "dmw":   {"instr": "ABI",  "level": "L2",  "tag": "DMW"},
    "acm":   {"instr": "ABI",  "level": "L2",  "tag": "ACM"},
    "geof":  {"instr": "MAG",  "level": "L1b", "tag": "GEOF"},
    "sfeu":  {"instr": "EXIS", "level": "L1b", "tag": "SFEU"},
    "sfxr":  {"instr": "EXIS", "level": "L1b", "tag": "SFXR"},
    "ehis":  {"instr": "SEIS", "level": "L1b", "tag": "EHIS"},
    "mpsl":  {"instr": "SEIS", "level": "L1b", "tag": "MPSL"},
    "mpsh":  {"instr": "SEIS", "level": "L1b", "tag": "MPSH"},
    "sgps":  {"instr": "SEIS", "level": "L1b", "tag": "SGPS"},
    "fe093": {"instr": "SUVI", "level": "L1b", "tag": "Fe093"},
    "fe131": {"instr": "SUVI", "level": "L1b", "tag": "Fe131"},
    "fe171": {"instr": "SUVI", "level": "L1b", "tag": "Fe171"},
    "fe195": {"instr": "SUVI", "level": "L1b", "tag": "Fe195"},
    "fe284": {"instr": "SUVI", "level": "L1b", "tag": "Fe284"},
    "he303": {"instr": "SUVI", "level": "L1b", "tag": "He303"},
    "lcfa":  {"instr": "GLM",  "level": "L2",  "tag": "LCFA"},
    "fed":   {"instr": "GLM",  "level": "L2",  "tag": "FED"},
}

# =============================================================================
# LOGGING & HYGIENE
# =============================================================================

class Logger:
    def __init__(self, level="INFO"):
        self.levels = {"DEBUG": 0, "VERBOSE": 1, "INFO": 2, "QUIET": 3, "WARN": 3, "ERROR": 4}
        self.current_level = self.levels.get(level.upper(), 2)
        self.colors = {"DEBUG": "\033[94m", "VERBOSE": "\033[36m", "INFO": "\033[92m", "WARN": "\033[93m", "ERROR": "\033[91m", "RESET": "\033[0m"}

    def _msg(self, level, text):
        if self.levels.get(level, 2) >= self.current_level:
            ts = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            display_level = "WARN" if level == "QUIET" else level
            print(f"{ts} {self.colors.get(display_level, '')}[{display_level:<7}]{self.colors['RESET']} {text}", flush=True)

    def debug(self, text):   self._msg("DEBUG", text)
    def verbose(self, text): self._msg("VERBOSE", text)
    def info(self, text):    self._msg("INFO", text)
    def warn(self, text):    self._msg("WARN", text)
    def error(self, text):   self._msg("ERROR", text); sys.exit(1)

log = Logger()

def prune_empty_folders(path):
    if not path.exists(): return
    for root, dirs, files in os.walk(path, topdown=False):
        for name in dirs:
            dir_path = Path(root) / name
            try:
                if not any(dir_path.iterdir()):
                    dir_path.rmdir()
                    log.debug(f"Pruned empty folder: {dir_path.relative_to(path.parent)}")
            except (OSError, StopIteration): pass

# =============================================================================
# HELPERS
# =============================================================================

def parse_args():
    parser = argparse.ArgumentParser(prog="pave.py", description=__doc__)
    parser.add_argument("products", nargs="+", help="Product shortnames")
    parser.add_argument("--times", nargs="+", required=True, metavar="YYYYDDDHHm", help="10-digit timestamps")
    parser.add_argument("--scenes", nargs="*", choices=['f', 'c', 'm1', 'm2'], help="Optional ABI scene filter")
    parser.add_argument("--prefix", default="validation")
    parser.add_argument("--tag", default="test")
    parser.add_argument("-j", "--threads", type=int, default=8)
    parser.add_argument("-q", "--quiet", action="store_true", help="Only WARN/ERROR")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose")
    parser.add_argument("-d", "--debug", action="store_true", help="Debug")
    parser.add_argument("--verify_only", action="store_true", help="Audit only")
    parser.add_argument("--skip_gccs", action="store_true", help="Bypass Phase 1")
    return parser.parse_args()

def get_gpas_date(timestamp):
    year, doy = int(timestamp[:4]), int(timestamp[4:7])
    d = datetime.date(year, 1, 1) + datetime.timedelta(days=doy - 1)
    return d.strftime("%b/%Y%m%d").lower()

def resolve_meta(folder_name):
    """Greedy prefix-aware metadata resolution."""
    f_low = folder_name.lower().split('-')[0]
    best_match = None
    for key, meta in PRODUCT_MAP.items():
        if f_low.startswith(key):
            if best_match is None or len(key) > len(best_match):
                best_match = key
    if best_match: return PRODUCT_MAP[best_match]
    return {"instr": "ABI", "level": "L2", "tag": folder_name.upper()}

def get_on_prem_tag(folder_name):
    """Combines hard-wired tag from map with scene suffix."""
    base = folder_name.split('-')[0].lower()
    meta = resolve_meta(base)
    # Extract scene by stripping the matching key from the base
    # Find the key that resolved this meta
    match_key = next((k for k, v in PRODUCT_MAP.items() if v == meta), None)
    scene_suffix = base[len(match_key):].upper() if match_key else ""
    return f"{meta['tag']}{scene_suffix}"

def get_start_key(filename):
    return filename.split('_e')[0]

def run_s3_sync(src, dest, patterns, profile="geocloud", label=None):
    if label: log.debug(label)
    if isinstance(patterns, str): patterns = [patterns]
    source_uri = src if src.endswith('/') else f"{src}/"
    cmd = ["aws", "s3", "sync", source_uri, str(dest), "--exclude", "*"]
    for pat in patterns: cmd += ["--include", pat]
    cmd += ["--profile", profile, "--no-progress"]
    log.debug(f"  [CLI EXEC] {shlex.join(cmd)}")
    return subprocess.run(cmd, capture_output=True, text=True)

# =============================================================================
# CORE ENGINE
# =============================================================================

def get_gccs_products(args, gccs_path, threads):
    """Phase 1: GCCS Discovery & Retrieval."""
    log.info(f"Phase 1: GCCS Discovery & Retrieval Start")
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
            base_prefix = f"{GCCS_PREFIX}/{sat_id}/{level}/{instr}/"
            log.verbose(f"Scanning GCCS {sat_id} {instr}/{level}...")
            for bucket in [GCCS_BUCKET, GCCS_IP_BUCKET]:
                cmd = ["aws", "s3api", "list-objects-v2", "--profile", "geocloud", "--bucket", bucket,
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
                                if args.scenes:
                                    if not any(base_name.endswith(s.lower()) for s in args.scenes): continue
                                else:
                                    if not any(base_name.endswith(s) for s in ['f', 'c', 'm1', 'm2']): continue
                            discovery_list.append((bucket, pref, folder_name, instr))
                            log.debug(f"  [FOUND] {bucket}/{folder_name}")
                            matched = True; break
                        if matched: break

    log.info(f"Phase 1: Found {len(discovery_list)} folders. Launching Sync...")
    with ThreadPoolExecutor(max_workers=threads) as executor:
        for ts in args.times:
            year, doy = ts[:4], ts[4:7]
            for bucket_name, pref, folder_name, instr in discovery_list:
                sat_tag = "GOES-18" if "GOES-18" in pref else "GOES-19"
                product_root = gccs_path / instr / folder_name
                date_leaf = product_root / year / doy
                date_leaf.mkdir(parents=True, exist_ok=True)
                pat = f"*_s{ts}*"
                executor.submit(run_s3_sync, f"s3://{bucket_name}/{pref}{year}/{doy}/", date_leaf, pat, label=f"Sync: {sat_tag} | {folder_name}")

def get_on_prem_products(args, gccs_path, prem_path, threads):
    """Phase 2: On-Prem Mirroring."""
    if not gccs_path.exists(): return
    log.info("Phase 2: On-Prem Mirroring Start")
    filing_guide, sync_map = [], {}
    for instr_dir in [d for d in gccs_path.iterdir() if d.is_dir()]:
        for prod_folder in [p for p in instr_dir.iterdir() if p.is_dir()]:
            # Use prefix-aware resolution to correctly identify L1b vs L2
            meta = resolve_meta(prod_folder.name)
            base_low = prod_folder.name.split('-')[0].lower()
            filing_guide.append((base_low, instr_dir.name, prod_folder.name))

            key = (instr_dir.name, meta['level'].lower(), instr_dir.name if instr_dir.name != "SEIS" else "SEISS")
            if key not in sync_map: sync_map[key] = []
            sync_map[key].append(get_on_prem_tag(prod_folder.name))

    tmp_sync_dir = prem_path / ".tmp_sync"
    tmp_sync_dir.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=threads) as executor:
        for ts in args.times:
            year, doy, gpas_str = ts[:4], ts[4:7], get_gpas_date(ts)
            ts_match = f"_s{ts}"
            for (instr_name, level_str, instr_gpas), include_tags in sync_map.items():
                log.verbose(f"Syncing On-Prem {instr_name}/{level_str.upper()} with tags: {include_tags}")
                patterns = [f"*{tag}*{ts_match}*" for tag in include_tags]
                for sat in [18, 19]:
                    gpas_src = f"s3://{PREM_BUCKET}/op/GOES-{sat}/{level_str}/{instr_gpas}/{year}/{gpas_str}/"
                    executor.submit(run_s3_sync, gpas_src, tmp_sync_dir, patterns, label=f"Sync: GOES-{sat} | {instr_name}")

    log.info("Phase 2: Restructuring local files...")
    all_files = list(tmp_sync_dir.glob("*.nc"))
    guide_sorted = sorted(filing_guide, key=lambda x: len(x[0]), reverse=True)
    for f_path in all_files:
        filename, f_lower, matched = f_path.name, f_path.name.lower(), False
        for base_low, instr_name, prod_actual in guide_sorted:
            if base_low in f_lower:
                date_match = re.search(r'_s(\d{4})(\d{3})', filename)
                if date_match:
                    f_year, f_doy = date_match.groups()
                    dest = prem_path / instr_name / prod_actual / f_year / f_doy
                    dest.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(f_path), str(dest / filename))
                    matched = True
                    log.debug(f"  [FILE MATCH] {filename} -> {prod_actual}")
                    break
        if not matched: log.warn(f"  [ORPHANED] Could not file product: {filename}")
    if tmp_sync_dir.exists(): shutil.rmtree(tmp_sync_dir)

def extract_ips(args, prem_path, gccs_path):
    if not gccs_path.exists(): return
    gccs_ip_refs = list(gccs_path.rglob("*I_ABI*.nc"))
    if not gccs_ip_refs: return
    log.info(f"Phase 3: Targeted IP Recovery ({len(gccs_ip_refs)} files)")
    with ThreadPoolExecutor(max_workers=args.threads) as executor:
        for ts in args.times:
            doy = ts[4:7]
            for sat in [18, 19]:
                tar = f"GOES-{sat}_ABI_L2_IntermediateProducts_day{doy}_hour{ts[7:9]}.tar"
                executor.submit(run_s3_sync, f"s3://{EGRESS_ROOT}/GOES-{sat}/", prem_path, tar, label=f"Sync: {tar}")
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
    log.info("Phase 4: Retrieval Symmetry Audit")
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

def main():
    args = parse_args()
    if args.debug: lvl = "DEBUG"
    elif args.verbose: lvl = "VERBOSE"
    elif args.quiet: lvl = "QUIET"
    else: lvl = "INFO"
    global log
    log = Logger(lvl)
    root = Path(f"{args.prefix}_{args.times[0]}_{args.tag}")
    gccs, prem = root / "gccs", root / "prem"
    if not args.verify_only:
        if not args.skip_gccs:
            get_gccs_products(args, gccs, args.threads)
            prune_empty_folders(gccs)
        get_on_prem_products(args, gccs, prem, args.threads)
        prune_empty_folders(prem)
        extract_ips(args, prem, gccs)
    check_symmetry(args, gccs, prem)
    log.info(f"v1.0.6 Run Complete. Workspace: {root.absolute()}")

if __name__ == "__main__":
    main()
