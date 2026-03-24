#!/usr/bin/env python3
"""
COLLOCATE-PAVE: Sparse Data Alignment & Comparison
==================================================
VERSION: 1.0.2 (Internal Absolute Path Resolution for Configs)
"""

import os
import subprocess
import shlex
import argparse
import sys
import tempfile
import shutil
from pathlib import Path

try:
    from pave_utils import Logger, setup_interrupt_handler
    import netCDF4 as nc
except ImportError:
    print("CRITICAL: Missing dependencies (pave_utils or netCDF4).")
    sys.exit(1)

# =============================================================================
# PRODUCT CONFIGURATION MAP
# =============================================================================
PRODUCT_MAP = {
    "DMW": {
        "coll_cfg": "dmw_collocate.py",
        "rpt_cfg": "dmw_report.py"
    },
    "DMWV": {
        "coll_cfg": "dmw_collocate.py",
        "rpt_cfg": "dmw_report.py"
    }
}

# =============================================================================
# CORE ENGINE
# =============================================================================

class CollocationAnalyzer:
    def __init__(self, args, log):
        self.prem_root = Path(args.prem_fld).resolve()
        self.gccs_root = Path(args.gccs_fld).resolve()
        self.coll_root = Path(args.coll_fld).resolve()
        self.dest_root = Path(args.dest_fld).resolve()

        # --- SELF-HEALING PATH RESOLUTION ---
        provided_cfg = Path(args.cfg_fld)
        if not provided_cfg.is_absolute() and not provided_cfg.exists():
            # If relative path fails from CWD, anchor it to this script's location
            script_dir = Path(__file__).parent.resolve()
            self.cfg_root = (script_dir / provided_cfg).resolve()
            log.debug(f"Config path adjusted relative to script: {self.cfg_root}")
        else:
            self.cfg_root = provided_cfg.resolve()

        self.glance_bin = getattr(args, 'bin', 'glance')
        self.is_debug = getattr(args, 'debug', False)
        self.is_verbose = getattr(args, 'verbose', False)
        self.log = log

    def _has_data(self, file_path, var_name="lon"):
        try:
            with nc.Dataset(file_path, 'r') as ds:
                if var_name not in ds.variables: return False
                return ds.variables[var_name].size > 0
        except Exception:
            return False

    def run_collocation(self, prem_file, gccs_file, coll_cfg):
        """Stage 1: Run 'glance collocate'."""
        rel_path = gccs_file.relative_to(self.gccs_root)
        p_coll_dest = self.coll_root / "coll_prem" / rel_path.parent
        g_coll_dest = self.coll_root / "coll_gccs" / rel_path.parent
        p_coll_dest.mkdir(parents=True, exist_ok=True)
        g_coll_dest.mkdir(parents=True, exist_ok=True)

        config_file = self.cfg_root / coll_cfg
        if not config_file.exists():
            self.log.error(f"Config Missing: {config_file}")
            return None, None

        with tempfile.TemporaryDirectory() as tmp_dir:
            cmd = [self.glance_bin, "collocate"]
            if self.is_debug or self.is_verbose: cmd.append("--verbose")

            cmd += [
                "-c", str(config_file),
                "-p", tmp_dir,
                str(gccs_file), str(prem_file)
            ]

            self.log.info(f"  [COLLOCATE] Processing: {gccs_file.name}")
            self.log.debug(f"{cmd}")

            # Passthrough debug if requested, otherwise capture
            if self.is_debug:
                result = subprocess.run(cmd)
                raw_out, raw_err = "[Passthrough]", "[Passthrough]"
            else:
                result = subprocess.run(cmd, capture_output=True, text=True)
                raw_out, raw_err = result.stdout, result.stderr

            if result.returncode != 0:
                self.log.error(f"!!! GLANCE CRASHED (Code {result.returncode}) for {rel_path} !!!")
                self._flare_crash(rel_path, result.returncode, cmd, raw_out, raw_err)
                return None, None

            p_res = list(Path(tmp_dir).glob(f"*{prem_file.stem}-collocated.nc"))
            g_res = list(Path(tmp_dir).glob(f"*{gccs_file.stem}-collocated.nc"))

            if not p_res or not g_res:
                return None, None

            p_final = p_coll_dest / prem_file.name
            g_final = g_coll_dest / gccs_file.name
            shutil.move(str(p_res[0]), str(p_final))
            shutil.move(str(g_res[0]), str(g_final))

            return p_final, g_final

    def _flare_crash(self, rel_path, code, cmd, stdout, stderr):
        flare_msg = (
            f"\n{'#'*80}\n"
            f"FATAL ERROR (Exit: {code})\n"
            f"FILE: {rel_path}\n"
            f"COMMAND: {shlex.join(cmd)}\n"
            f"{'-'*80}\n"
            f"RAW STDERR:\n{stderr}\n"
            f"{'-'*80}\n"
            f"RAW STDOUT:\n{stdout}\n"
            f"{'#'*80}\n"
        )
        sys.__stderr__.write(flare_msg)
        sys.__stderr__.flush()

    def run_report(self, p_dir, g_dir, rpt_cfg, rel_prod_path):
        report_dest = self.dest_root / rel_prod_path
        report_dest.mkdir(parents=True, exist_ok=True)
        config_file = self.cfg_root / rpt_cfg

        cmd = [self.glance_bin, "report", "-c", str(config_file),
               "-p", str(report_dest), "--stripfromname", "e.*", str(p_dir), str(g_dir)]

        if self.is_debug: cmd.append("--verbose")

        self.log.info(f"Generating Collocated Report: {rel_prod_path}")
        subprocess.run(cmd, capture_output=not self.is_debug)

    def execute(self):
        for instr_dir in [d for d in self.gccs_root.iterdir() if d.is_dir()]:
            for prod_dir in [p for p in instr_dir.iterdir() if p.is_dir()]:
                matched_key = next((k for k in PRODUCT_MAP if k in prod_dir.name.upper()), None)
                if not matched_key: continue

                configs = PRODUCT_MAP[matched_key]
                self.log.info(f"Starting Collocation: {prod_dir.name}")

                collocated_count = 0
                for gccs_file in prod_dir.rglob("*.nc"):
                    rel_file = gccs_file.relative_to(self.gccs_root)
                    search_name = gccs_file.name.split('_s')[0]
                    prem_dir = self.prem_root / rel_file.parent

                    try:
                        prem_file = next(prem_dir.glob(f"{search_name}*.nc"))
                    except StopIteration:
                        continue

                    if not self._has_data(prem_file) or not self._has_data(gccs_file):
                        continue

                    p_coll, g_coll = self.run_collocation(prem_file, gccs_file, configs['coll_cfg'])
                    if p_coll: collocated_count += 1

                if collocated_count > 0:
                    rel_prod = prod_dir.relative_to(self.gccs_root)
                    p_coll_dir = self.coll_root / "coll_prem" / rel_prod
                    g_coll_dir = self.coll_root / "coll_gccs" / rel_prod

                    for p_leaf in [d for d in p_coll_dir.rglob("*") if d.is_dir() and any(d.glob("*.nc"))]:
                        g_leaf = g_coll_dir / p_leaf.relative_to(p_coll_dir)
                        if g_leaf.exists():
                            self.run_report(p_leaf, g_leaf, configs['rpt_cfg'], rel_prod / p_leaf.relative_to(p_coll_dir))

# =============================================================================
# MAIN / PARSER
# =============================================================================

def parse_args():
    parser = argparse.ArgumentParser(prog="collocate_pave.py")
    parser.add_argument("prem_fld")
    parser.add_argument("gccs_fld")
    parser.add_argument("coll_fld")
    parser.add_argument("dest_fld")
    parser.add_argument("--cfg_fld", required=True)
    parser.add_argument("--bin", default="glance")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-d", "--debug", action="store_true")
    return parser.parse_args()

def main():
    args = parse_args()
    log = Logger("DEBUG" if args.debug else "VERBOSE" if args.verbose else "INFO")
    setup_interrupt_handler(log)
    CollocationAnalyzer(args, log).execute()

if __name__ == "__main__":
    main()
