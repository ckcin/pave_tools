#!/usr/bin/env python3
"""
SCIENCE-PAVE: Science Level Comparison Engine
==============================================
VERSION: 1.5.5 (Cumulative Status Decoding)
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

    def decode_glance_status(self, code):
        """Interprets cumulative Glance exit codes to estimate file counts."""
        if code == 0:
            return "Perfect Match"

        # Check for pure multiples of Exit 80 (No Variables Found)
        if code % 80 == 0:
            count = code // 80
            return f"Skipped: {count} files had no common variables (Exit 80)"

        # Check for pure multiples of Exit 4 (Differences Found)
        if code % 4 == 0:
            count = code // 4
            return f"Analysis Complete: {count} files had differences (Exit 4)"

        # Fallback for mixed codes or unknown statuses
        return f"Non-fatal status (Exit {code})"

    def run_glance_report(self, p_prod_dir, g_prod_dir):
        rel_path = p_prod_dir.relative_to(self.prem_root)
        report_dest = self.dest_root / rel_path
        report_dest.mkdir(parents=True, exist_ok=True)

        cmd = [self.glance_bin, "report", "--nolonlat"]
        if self.is_debug or self.is_verbose:
            cmd.append("--verbose")
        if self.use_fork:
            cmd.append("--fork")

        cmd += ["-p", str(report_dest), str(p_prod_dir), str(g_prod_dir), "--stripfromname", "e.*"]

        self.log.info(f"Generating Science Report: {rel_path}")

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["MPLBACKEND"] = "Agg"

        with tempfile.NamedTemporaryFile(mode='w+', suffix='.out') as f_out, \
             tempfile.NamedTemporaryFile(mode='w+', suffix='.err') as f_err:

            result = subprocess.run(cmd, stdout=f_out, stderr=f_err, env=env)

            # 1. FATAL SYSTEM ERROR
            if result.returncode == 1:
                f_out.seek(0)
                f_err.seek(0)
                self._flare_error(rel_path, result.returncode, cmd, f_out.read(), f_err.read())
                self.log.error(f"!!! GLANCE FATAL ERROR: {rel_path} (Exit: 1) !!!")

            # 2. NON-FATAL STATUS (Decoded for Batch Reporting)
            elif result.returncode != 0:
                decoded_msg = self.decode_glance_status(result.returncode)
                self.log.warn(f"  [STATUS {result.returncode}] {decoded_msg} for {rel_path}.")
                return

            # 3. SUCCESS
            else:
                self.log.verbose(f"  Success: {rel_path}")

    def _flare_error(self, rel_path, code, cmd, stdout, stderr):
        clean_err = self._scrub_output(stderr)
        msg = f"\n{'!'*80}\nGLANCE HARD ERROR | {rel_path} | Code: {code}\n{'-'*80}\nCALL: {shlex.join(cmd)}\n{'-'*80}\nSTDERR: {clean_err}\n{'!'*80}\n"
        sys.__stderr__.write(msg)
        sys.__stderr__.flush()

    def execute(self):
        if not self.dest_root.exists():
            self.dest_root.mkdir(parents=True, exist_ok=True)

        for instr_dir in [d for d in self.prem_root.iterdir() if d.is_dir()]:
            for prod_dir in [p for p in instr_dir.iterdir() if p.is_dir()]:
                g_prod_dir = self.gccs_root / prod_dir.relative_to(self.prem_root)
                if g_prod_dir.exists():
                    try:
                        self.run_glance_report(prod_dir, g_prod_dir)
                    except (Exception, SystemExit) as e:
                        self.log.warn(f"Execution stalled for {prod_dir.name}: {e}")
                        continue
        self.log.info("Science Level Comparison Complete.")

def parse_args():
    parser = argparse.ArgumentParser(prog="science_pave.py", description="Generate Glance reports.")
    parser.add_argument("prem_fld", help="On-Prem root")
    parser.add_argument("gccs_fld", help="GCCS root")
    parser.add_argument("dest_fld", help="Report destination")
    parser.add_argument("--fork", action="store_true", help="Parallelize report generation")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-d", "--debug", action="store_true")
    parser.add_argument("-q", "--quiet", action="store_true")
    parser.add_argument("--bin", default="glance", help="Glance path")
    return parser.parse_args()

def main():
    args = parse_args()

    if args.debug:
        lvl = "DEBUG"
    elif args.verbose:
        lvl = "VERBOSE"
    elif args.quiet:
        lvl = "QUIET"
    else:
        lvl = "INFO"

    log = Logger(lvl)
    setup_interrupt_handler(log)
    ScienceAnalyzer(args, log).execute()

if __name__ == "__main__":
    main()
