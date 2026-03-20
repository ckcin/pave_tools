#!/usr/bin/env python3
"""
PAVE UTILS: Shared Infrastructure Module
========================================
VERSION: 1.2.1
"""

import os
import re
import datetime
import subprocess
import shlex
from pathlib import Path

# GLOBAL S3 CONFIG
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

def resolve_meta(folder_name):
    f_low = folder_name.lower().split('-')[0]
    best_match = None
    for key in PRODUCT_MAP.keys():
        if f_low.startswith(key):
            if best_match is None or len(key) > len(best_match): best_match = key
    if best_match: return PRODUCT_MAP[best_match]
    return {"instr": "ABI", "level": "L2", "tag": folder_name.upper()}

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
