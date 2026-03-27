#!/usr/bin/env python3
"""
SCIENCE-PAVE: Science Level Comparison Engine
==============================================
VERSION: 1.5.4 (Complete Resilience - Only Exit 1 Fatal)
"""

import os
import subprocess
import shlex
import argparse
import warnings
import sys
import tempfile
from pathlib import Path
from pave_utils import Logger, setup_interrupt_handler

warnings.filterwarnings("ignore", message="No artists with labels found to put in legend")

class ScienceAnalyzer:
    def __init__(self, args, log):
        self.prem_root = Path(args.prem_fld).resolve()
        self.gccs_root = Path(args.gccs_fld).resolve()
        self.dest_root = Path(args.dest_fld).resolve()
        self.glance_bin = getattr(args, 'bin', 'glance')
        self.use_fork = getattr(args, 'fork', False)
        self.is_debug = getattr(args, 'debug', False)
        self.is_verbose = getattr(args, 'verbose', False)
        self.log = log

    def _scrub_output(self, text):
        if not text:
            return ""
        return "\n".join([l for l in text.splitlines() if "No artists with labels found" not in l]).strip()

    def run_glance_report(self, pdir, gdir):
        rel = pdir.relative_to(self.prem_root)
        report_d = self.dest_root / rel
        report_d.mkdir(parents=True, exist_ok=True)
        cmd = [self.glance_bin, "report", "--nolonlat"]
        if self.is_debug or self.is_verbose:
            cmd.append("--verbose")
        if self.use_fork:
            cmd.append("--fork")
        cmd += ["-p", str(report_d), str(pdir), str(gdir), "--stripfromname", "e.*"]
        
        self.log.info(f"Generating Science Report: {rel}")
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["MPLBACKEND"] = "Agg"
        
        with tempfile.NamedTemporaryFile(mode='w+', suffix='.out') as fout, \
             tempfile.NamedTemporaryFile(mode='w+', suffix='.err') as ferr:
            res = subprocess.run(cmd, stdout=fout, stderr=ferr, env=env)
            
            if res.returncode == 1:
                fout.seek(0)
                ferr.seek(0)
                self._flare_error(rel, res.returncode, cmd, fout.read(), ferr.read())
                self.log.error(f"!!! GLANCE FATAL ERROR: {rel} (Exit: 1) !!!")
            elif res.returncode != 0:
                self.log.warn(f"  [SKIPPED] Glance status {res.returncode} for {rel}.")
            else:
                self.log.verbose(f"  Success: {rel}")

    def _flare_error(self, rel, code, cmd, out, err):
        msg = f"\n{'!'*80}\nGLANCE HARD ERROR | {rel} | Code: {code}\n{'-'*80}\nCALL: {shlex.join(cmd)}\n{'-'*80}\nSTDERR: {self._scrub_output(err)}\n{'!'*80}\n"
        sys.__stderr__.write(msg)
        sys.__stderr__.flush()

    def execute(self):
        if not self.dest_root.exists():
            self.dest_root.mkdir(parents=True, exist_ok=True)
        for prod_dir in [p for d in self.prem_root.iterdir() if d.is_dir() for p in d.iterdir() if p.is_dir()]:
            gdir = self.gccs_root / prod_dir.relative_to(self.prem_root)
            if gdir.exists():
                try:
                    self.run_glance_report(prod_dir, gdir)
                except (Exception, SystemExit) as e:
                    self.log.warn(f"Stalled {prod_dir.name}: {e}")
        self.log.info("Science Level Comparison Complete.")

def parse_args():
    parser = argparse.ArgumentParser(prog="science_pave.py")
    parser.add_argument("prem_fld", help="On-Prem root")
    parser.add_argument("gccs_fld", help="GCCS root")
    parser.add_argument("dest_fld", help="Report dest")
    parser.add_argument("--fork", action="store_true", help="Fork reports")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose")
    parser.add_argument("-d", "--debug", action="store_true", help="Debug")
    parser.add_argument("-q", "--quiet", action="store_true", help="Quiet")
    parser.add_argument("--bin", default="glance", help="Glance bin")
    return parser.parse_args()

def main():
    args = parse_args()
    lvl = "DEBUG" if args.debug else "VERBOSE" if args.verbose else "QUIET" if args.quiet else "INFO"
    log = Logger(lvl)
    setup_interrupt_handler(log)
    ScienceAnalyzer(args, log).execute()

if __name__ == "__main__":
    main()
