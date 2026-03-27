#!/usr/bin/env python3
"""
COLLOCATE-PAVE: Sparse Data Alignment & Comparison
==================================================
VERSION: 1.0.7 (Complete Resilience - Only Exit 1 Fatal)
"""

import os
import subprocess
import shlex
import argparse
import sys
import tempfile
import shutil
from pathlib import Path
from pave_utils import Logger, setup_interrupt_handler

try:
    import netCDF4 as nc
except ImportError:
    print("CRITICAL: netCDF4 library missing.")
    sys.exit(1)

PRODUCT_MAP = {
    "DMW": {"coll_cfg": "dmw_collocate.py", "rpt_cfg": "dmw_report.py"}, 
    "DMWV": {"coll_cfg": "dmw_collocate.py", "rpt_cfg": "dmw_report.py"}
}

class CollocationAnalyzer:
    def __init__(self, args, log):
        self.prem_root = Path(args.prem_fld).resolve()
        self.gccs_root = Path(args.gccs_fld).resolve()
        self.coll_root = Path(args.coll_fld).resolve()
        self.dest_root = Path(args.dest_fld).resolve()
        pcfg = Path(args.cfg_fld)
        if pcfg.is_absolute() or pcfg.exists():
            self.cfg_root = pcfg.resolve()
        else:
            self.cfg_root = (Path(__file__).parent / pcfg).resolve()
        self.glance_bin = getattr(args, 'bin', 'glance')
        self.is_debug = getattr(args, 'debug', False)
        self.is_verbose = getattr(args, 'verbose', False)
        self.log = log

    def _has_data(self, f, v="lon"):
        try:
            with nc.Dataset(f, 'r') as ds:
                return v in ds.variables and ds.variables[v].size > 0
        except Exception:
            return False

    def run_collocation(self, pf, gf, cfg):
        rel = gf.relative_to(self.gccs_root)
        pd = self.coll_root / "coll_prem" / rel.parent
        gd = self.coll_root / "coll_gccs" / rel.parent
        pd.mkdir(parents=True, exist_ok=True)
        gd.mkdir(parents=True, exist_ok=True)
        cmd = [self.glance_bin, "collocate", "-c", str(self.cfg_root / cfg), "-p"]
        with tempfile.TemporaryDirectory() as tmp:
            cmd += [tmp, str(gf), str(pf)]
            if self.is_debug or self.is_verbose:
                cmd.append("--verbose")
            self.log.info(f"  [COLLOCATE] Processing: {gf.name}")
            res = subprocess.run(cmd, capture_output=True, text=True)
            
            if res.returncode == 1:
                self._flare_crash(gf.name, res.returncode, cmd, res.stdout, res.stderr)
                self.log.error(f"!!! COLLOCATION FATAL ERROR: {gf.name} (Exit: 1) !!!")
                return None, None
            elif res.returncode != 0:
                self.log.warn(f"  [SKIPPED] Collocation status {res.returncode} for {gf.name}.")
                return None, None
                
            pres = list(Path(tmp).glob(f"*{pf.stem}-collocated.nc"))
            gres = list(Path(tmp).glob(f"*{gf.stem}-collocated.nc"))
            if not pres or not gres:
                return None, None
            
            pfinal, gfinal = pd / pf.name, gd / gf.name
            shutil.move(str(pres[0]), str(pfinal))
            shutil.move(str(gres[0]), str(gfinal))
            return pfinal, gfinal

    def _flare_crash(self, f, code, cmd, out, err):
        msg = f"\n{'#'*80}\nCOLLOCATION FATAL | {f} | Code: {code}\n{'-'*80}\nCALL: {shlex.join(cmd)}\n{'-'*80}\nSTDERR: {err}\n{'#'*80}\n"
        sys.__stderr__.write(msg)
        sys.__stderr__.flush()

    def execute(self):
        for prod_dir in [p for d in self.gccs_root.iterdir() if d.is_dir() for p in d.iterdir() if p.is_dir()]:
            match = next((k for k in PRODUCT_MAP if k in prod_dir.name.upper()), None)
            if not match:
                continue
            configs = PRODUCT_MAP[match]
            self.log.info(f"Starting Collocation: {prod_dir.name}")
            count = 0
            for gf in prod_dir.rglob("*.nc"):
                pf_dir = self.prem_root / gf.relative_to(self.gccs_root).parent
                try:
                    pf = next(pf_dir.glob(f"{gf.name.split('_s')[0]}*.nc"))
                    if self._has_data(pf) and self._has_data(gf):
                        p_coll, g_coll = self.run_collocation(pf, gf, configs['coll_cfg'])
                        if p_coll:
                            count += 1
                except StopIteration:
                    continue
            if count > 0:
                rel_prod = prod_dir.relative_to(self.gccs_root)
                p_c_d = self.coll_root / "coll_prem" / rel_prod
                g_c_d = self.coll_root / "coll_gccs" / rel_prod
                for pl in [d for d in p_c_d.rglob("*") if d.is_dir() and any(d.glob("*.nc"))]:
                    gl = g_c_d / pl.relative_to(p_c_d)
                    if gl.exists():
                        rdest = self.dest_root / rel_prod / pl.relative_to(p_c_d)
                        rdest.mkdir(parents=True, exist_ok=True)
                        subprocess.run([self.glance_bin, "report", "-c", str(self.cfg_root / configs['rpt_cfg']), "-p", str(rdest), "--stripfromname", "e.*", str(pl), str(gl)], capture_output=not self.is_debug)

def parse_args():
    parser = argparse.ArgumentParser(prog="collocate_pave.py")
    parser.add_argument("prem_fld", help="On-Prem root")
    parser.add_argument("gccs_fld", help="GCCS root")
    parser.add_argument("coll_fld", help="Collocation workspace")
    parser.add_argument("dest_fld", help="Report destination")
    parser.add_argument("--cfg_fld", required=True, help="Glance config folder")
    parser.add_argument("--bin", default="glance", help="Glance binary")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose")
    parser.add_argument("-d", "--debug", action="store_true", help="Debug")
    parser.add_argument("-q", "--quiet", action="store_true", help="Quiet")
    return parser.parse_args()

def main():
    args = parse_args()
    lvl = "DEBUG" if args.debug else "VERBOSE" if args.verbose else "QUIET" if args.quiet else "INFO"
    log = Logger(lvl)
    setup_interrupt_handler(log)
    CollocationAnalyzer(args, log).execute()

if __name__ == "__main__":
    main()
