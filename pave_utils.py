#!/usr/bin/env python3
"""
PAVE UTILS: Shared Infrastructure Module
========================================
VERSION: 1.5.3 (added product family grouping with dynamic channel splitting)
"""

import os
import re
import datetime
import subprocess
import shlex
import sys
import signal
import tarfile
import shutil
from pathlib import Path

# GLOBAL S3 CONFIG
GCCS_BUCKET = "gccs-products"
GCCS_IP_BUCKET = "gccs-intermediate-products"
GCCS_PREFIX = "GCCS/op"
PREM_BUCKET = "geoproducts-ops"
EGRESS_ROOT = "geoegress/egresout/DOE1L2IP"

# Global Regex for Product Identification
GOES_REGEX = re.compile(r"OR_(?P<dsn>.*?)_(?P<sat>G1[89]).*?s(?P<start>\d{14})")

PRODUCT_MAP = {
    # --- ABI STANDARD (IMAGES) ---
    "cmip":  {"instr": "ABI",  "level": "L2",  "tag": "CMIP",  "comp_type": "standard"},
    "mcmip": {"instr": "ABI",  "level": "L2",  "tag": "MCMIP", "comp_type": "standard"},
    "rad":   {"instr": "ABI",  "level": "L1b", "tag": "Rad",   "comp_type": "standard"},

    # --- ABI/GLM SPARSE (COLLOCATION PATH) ---
    "dmw":   {"instr": "ABI",  "level": "L2",  "tag": "DMW",   "comp_type": "sparse"},
    "dmwv":  {"instr": "ABI",  "level": "L2",  "tag": "DMWV",  "comp_type": "sparse"},
    "lcfa":  {"instr": "GLM",  "level": "L2",  "tag": "LCFA",  "comp_type": "sparse"},
    "fed":   {"instr": "GLM",  "level": "L2",  "tag": "FED",   "comp_type": "sparse"},

    # --- SPACE WEATHER TIME-SERIES (TEMPORAL ALIGNMENT) ---
    "geof":  {"instr": "MAG",  "level": "L1b", "tag": "GEOF",  "comp_type": "timeseries"},
    "sfeu":  {"instr": "EXIS", "level": "L1b", "tag": "SFEU",  "comp_type": "timeseries"},
    "sfxr":  {"instr": "EXIS", "level": "L1b", "tag": "SFXR",  "comp_type": "timeseries"},
    "ehis":  {"instr": "SEIS", "level": "L1b", "tag": "EHIS",  "comp_type": "timeseries"},
    "mpsl":  {"instr": "SEIS", "level": "L1b", "tag": "MPSL",  "comp_type": "timeseries"},
    "mpsh":  {"instr": "SEIS", "level": "L1b", "tag": "MPSH",  "comp_type": "timeseries"},
    "sgps":  {"instr": "SEIS", "level": "L1b", "tag": "SGPS",  "comp_type": "timeseries"},

    # --- SPACE WEATHER IMAGERY (STANDARD) ---
    "fe093": {"instr": "SUVI", "level": "L1b", "tag": "Fe093", "comp_type": "standard"},
    "fe131": {"instr": "SUVI", "level": "L1b", "tag": "Fe131", "comp_type": "standard"},
    "fe171": {"instr": "SUVI", "level": "L1b", "tag": "Fe171", "comp_type": "standard"},
    "fe195": {"instr": "SUVI", "level": "L1b", "tag": "Fe195", "comp_type": "standard"},
    "fe284": {"instr": "SUVI", "level": "L1b", "tag": "Fe284", "comp_type": "standard"},
    "he303": {"instr": "SUVI", "level": "L1b", "tag": "He303", "comp_type": "standard"},
}

# =============================================================================
# PRODUCT FAMILY GROUPINGS & PATTERN MATCHING
# =============================================================================
PRODUCT_FAMILIES = {
    "Sounding": ["LVMP", "LVTP", "DSI", "TPW", "LSP"],
    "CloudHeight": ["ACH", "CTP"],
    "COMP": ["COD", "CPS"],
    "Cloud_ACT": ["ACT"],
    "Cloud_ECBH": ["ECBH"],
    "Cloud_EOCH": ["EOCH"],
    "Cloud_CCL": ["CCL"],
    "Radiation": ["RSR", "DSR", "PAR", "SWR", "ERBCLMF"],
    "SurfaceAlbedo": ["LSA", "BRF", "NBAR", "BRDFF20", "NBARF20"],
    "DerivedMotion": ["DMW", "DMWV"],
    "Aerosol_ADP": ["ADP"],
    "Aerosol_AOD": ["AOD"],
    "Cryo_AICE": ["AICE"],
    "Cryo_AITA": ["AITA"],
    "CMIP": ["CMIP"],
    "MCMIP": ["MCMIP"],
    "RAD": ["RAD"],
    "SST": ["SST"],
    "RRQPE": ["RRQPE"],
    "FDC": ["FDC"],
    "FSC": ["FSC"],
    "LST": ["LST", "CLST"],
    "ESC": ["ESC"],
    "ESU": ["ESU"],
    "ETE": ["ETE"]
}

def get_family_for_product(product_dsn):
    """
    Pattern-matches a specific product scene (e.g., 'ACHC' or 'LVMPM1')
    to its parent Product Family. Automatically splits RAD and CMIP by channel.
    """
    prod_upper = product_dsn.upper()
    assigned_family = prod_upper

    # 1. Match against the dictionary
    for family, members in PRODUCT_FAMILIES.items():
        # Sort by length descending to prevent short-prefix false positives
        for m in sorted(members, key=len, reverse=True):
            if prod_upper.startswith(m.upper()):
                assigned_family = family
                break
        if assigned_family != prod_upper:
            break

    # 2. Dynamic Channel Splitting for RAD and CMIP
    if assigned_family in ["CMIP", "RAD"]:
        # Search the product string for the channel indicator (e.g., 'C01', 'C13')
        ch_match = re.search(r'(C\d{2})', prod_upper)
        if ch_match:
            assigned_family = f"{assigned_family}_{ch_match.group(1)}"

    return assigned_family

def get_products_in_family(family_name):
    """Returns the base prefixes for the scheduler."""
    return PRODUCT_FAMILIES.get(family_name, [])

# =============================================================================
# LOGGING ENGINE
# =============================================================================
class Logger:
    def __init__(self, level="INFO", use_colors=None):
        self.levels = {
            "DEBUG": 0, "VERBOSE": 1, "INFO": 2,
            "QUIET": 3, "WARN": 3, "ERROR": 4
        }
        self.current_level = self.levels.get(level.upper(), 2)

        if use_colors is not None:
            has_colors = use_colors
        else:
            is_a_tty = sys.stdout.isatty() if hasattr(sys.stdout, 'isatty') else False
            has_no_color_env = "NO_COLOR" in os.environ
            has_colors = is_a_tty and not has_no_color_env

        if has_colors:
            self.colors = {
                "DEBUG": "\033[94m", "VERBOSE": "\033[36m", "INFO": "\033[92m",
                "WARN": "\033[93m", "ERROR": "\033[91m", "RESET": "\033[0m"
            }
        else:
            self.colors = {
                "DEBUG": "", "VERBOSE": "", "INFO": "",
                "WARN": "", "ERROR": "", "RESET": ""
            }

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

# =============================================================================
# TERMINATION & SIGNAL HANDLING
# =============================================================================

def setup_interrupt_handler(logger=None):
    """Configures the application to exit cleanly on Ctrl+C."""
    def signal_handler(sig, frame):
        msg = "\n[INTERRUPT] Execution halted by user. Cleaning up and exiting..."
        if logger:
            logger.warn(msg)
        else:
            print(msg)
        sys.exit(0)
    signal.signal(signal.SIGINT, signal_handler)

# =============================================================================
# SYMMETRY ENGINE
# =============================================================================

def print_symmetry_table(prem_fld, gccs_fld, log, relax_match=False):
    """Generates an operational summary matrix tracking data balance between nodes."""
    p_root = Path(prem_fld).resolve()
    g_root = Path(gccs_fld).resolve()

    p_files = list(p_root.rglob("*.nc"))
    g_files = list(g_root.rglob("*.nc"))

    # Pre-build gccs lookup: rel_dir -> {filename: path}
    g_lookup = {}
    for gf in g_files:
        rel_dir = gf.parent.relative_to(g_root)
        if rel_dir not in g_lookup:
            g_lookup[rel_dir] = {}
        g_lookup[rel_dir][gf.name] = gf

    stats = {}

    for pf in p_files:
        m = GOES_REGEX.search(pf.name)
        prod = m.group('dsn') if m else "Unknown"

        if prod not in stats:
            stats[prod] = {"prem": 0, "gccs": 0, "pairs": 0}
        stats[prod]["prem"] += 1

        rel_dir = pf.relative_to(p_root).parent

        if rel_dir in g_lookup:
            # FEATURE UPDATE: Evaluate spatial parity using relaxed start time mapping keys
            if relax_match and "_e" in pf.name:
                m_key = pf.name.split('_e')[0]
                matches = [f for fname, f in g_lookup[rel_dir].items() if fname.startswith(m_key) and fname.endswith('_e*.nc')]
            else:
                m_key = pf.name.split('_c')[0] if "_c" in pf.name else pf.name
                if "_c" in pf.name:
                    matches = [f for fname, f in g_lookup[rel_dir].items() if fname.startswith(m_key) and fname.endswith('_c*.nc')]
                else:
                    matches = [g_lookup[rel_dir][pf.name]] if pf.name in g_lookup[rel_dir] else []

            if matches:
                stats[prod]["pairs"] += 1

    for gf in g_files:
        m = GOES_REGEX.search(gf.name)
        prod = m.group('dsn') if m else "Unknown"
        if prod not in stats:
            stats[prod] = {"prem": 0, "gccs": 0, "pairs": 0}
        stats[prod]["gccs"] += 1

    headers = ["Product", "On-Prem", "GCCS", "Matched"]
    rows = []
    totals = [0, 0, 0]

    for prod in sorted(stats.keys()):
        s = stats[prod]
        rows.append([prod, s['prem'], s['gccs'], s['pairs']])
        totals[0] += s['prem']; totals[1] += s['gccs']; totals[2] += s['pairs']

    rows.append(["TOTAL", totals[0], totals[1], totals[2]])

    widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            widths[i] = max(widths[i], len(str(val)))

    sep = "-+-".join("-" * w for w in widths)
    log.info("Symmetry Inventory Table:")
    log.info(" | ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    log.info(sep)
    for row in rows[:-1]:
        log.info(" | ".join(str(val).ljust(widths[i]) for i, val in enumerate(row)))
    log.info(sep)
    log.info(" | ".join(str(val).ljust(widths[i]) for i, val in enumerate(rows[-1])))

# =============================================================================
# FILE SYSTEM UTILITIES
# =============================================================================

def archive_directory(path, logger):
    if not path.exists(): return
    source_files = [f for f in path.rglob("*") if f.is_file()]
    if not source_files: return

    tar_path = path.parent / f"{path.name}.tar.gz"
    logger.info(f"Cleanup: Archiving {path.name} ({len(source_files)} files)")

    try:
        with tarfile.open(tar_path, "w:gz") as tar: tar.add(path, arcname=path.name)
        with tarfile.open(tar_path, "r:gz") as tar_check: archive_members = [m for m in tar_check.getmembers() if m.isfile()]

        if len(archive_members) == len(source_files):
            shutil.rmtree(path)
            logger.verbose(f"Cleanup: Successfully removed source folder {path.name}")
    except Exception as e:
        logger.warn(f"Cleanup: Process failed for {path.name}: {e}")

def resolve_meta(folder_name):
    f_low = folder_name.lower()
    best_match = None

    for key in PRODUCT_MAP.keys():
        if key in f_low:
            if best_match is None or len(key) > len(best_match):
                best_match = key

    if best_match:
        return PRODUCT_MAP[best_match]

    return {
        "instr": "ABI",
        "level": "L2",
        "tag": folder_name.upper(),
        "comp_type": "standard"
    }

def get_on_prem_tag(folder_name):
    base = folder_name.split('-')[0].lower()
    meta = resolve_meta(base)
    match_key = next((k for k, v in PRODUCT_MAP.items() if v == meta), None)
    scene_suffix = base[len(match_key):].upper() if match_key else ""
    return f"{meta['tag']}{scene_suffix}"

def run_s3_sync(src, dest, patterns, logger, profile="geocloud", label=None):
    if label: logger.debug(label)
    if isinstance(patterns, str): patterns = [patterns]
    source_uri = src if src.endswith('/') else f"{src}/"
    cmd = ["aws", "s3", "sync", source_uri, str(dest), "--exclude", "*"]
    for pat in patterns: cmd += ["--include", pat]
    cmd += ["--profile", profile, "--no-progress"]
    logger.debug(f"  [CLI EXEC] {shlex.join(cmd)}")
    return subprocess.run(cmd, capture_output=True, text=True)

def get_gpas_date(timestamp):
    year, doy = int(timestamp[:4]), int(timestamp[4:7])
    d = datetime.date(year, 1, 1) + datetime.timedelta(days=doy - 1)
    return d.strftime("%b/%Y%m%d").lower()

def get_start_key(filename):
    return filename.split('_e')[0]

def prune_empty_folders(path, logger=None):
    if not path.exists(): return
    for root, dirs, files in os.walk(path, topdown=False):
        for name in dirs:
            dir_path = Path(root) / name
            try:
                if not any(dir_path.iterdir()):
                    dir_path.rmdir()
                    if logger: logger.debug(f"Pruned empty folder: {dir_path.name}")
            except (OSError, StopIteration): pass
