#!/usr/bin/env python3
"""
SCIENCE-PAVE: Science Level Comparison Engine
==============================================
VERSION: 1.4.6 (Base Destination Folder Initialization)
"""

import os
import subprocess
import shlex
import argparse
import warnings
import sys
import tempfile
from pathlib import Path

try:
    from pave_utils import Logger, setup_interrupt_handler
except ImportError:
    print("CRITICAL: pave_utils.py not found.")
    sys.exit(1)

# Suppress warnings in the parent process
warnings.filterwarnings("ignore", message="No artists with labels found to put in legend")

# =============================================================================
# CORE ANALYSIS ENGINE
# =============================================================================

class ScienceAnalyzer:
    def __init__(self, args, log):
        self.prem_root = Path(args.prem_fld)
        self.gccs_root = Path(args.gccs_fld)
        self.dest_root = Path(args.dest_fld)
        self.glance_bin = getattr(args, 'bin', 'glance')
        self.use_fork = getattr(args, 'fork', False)
        self.is_debug = getattr(args, 'debug', False)
        self.log = log

    def _scrub_output(self, text):
        if not text: return ""
        return "\n".join([l for l in text.splitlines() if "No artists with labels found" not in l]).strip()

    def run_glance_report(self, p_prod_dir, g_prod_dir):
        """Runs glance and explicitly prevents Code 4 from triggering errors."""
        rel_path = p_prod_dir.relative_to(self.prem_root)
        report_dest = self.dest_root / rel_path
        report_dest.mkdir(parents=True, exist_ok=True)
        index_file = report_dest / "index.html"

        cmd = [self.glance_bin, "report", "--nolonlat"]
        if self.use_fork: cmd.append("--fork")
        cmd += ["-p", str(report_dest), str(p_prod_dir), str(g_prod_dir), "--stripfromname", "e.*"]

        self.log.info(f"Generating Science Report: {rel_path}")

        with tempfile.NamedTemporaryFile(mode='w+', suffix='.out') as f_out, \
             tempfile.NamedTemporaryFile(mode='w+', suffix='.err') as f_err:

            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            result = subprocess.run(cmd, stdout=f_out, stderr=f_err, env=env)

            f_out.seek(0); raw_stdout = f_out.read()
            f_err.seek(0); raw_stderr = f_err.read()

        # The Code 4 Guard
        if result.returncode == 4:
            self.log.warn(f"  [WARNING] {rel_path} exited with status 4. Moving to next product.")
            return

        if not index_file.exists() and result.returncode != 0:
            self.log.error(f"!!! FAILURE: {rel_path} (Exit: {result.returncode}) !!!")
            self._flare_error(rel_path, result.returncode, cmd, raw_stdout, raw_stderr)
        elif index_file.exists():
            status = "Success" if result.returncode == 0 else f"Success (Status {result.returncode})"
            self.log.verbose(f"  {status}: {index_file}")

    def _flare_error(self, rel_path, code, cmd, stdout, stderr):
        clean_out = self._scrub_output(stdout)
        clean_err = self._scrub_output(stderr)
        flare_msg = (
            f"\n{'!'*70}\n"
            f"REPORT MISSING: {rel_path} (Exit: {code})\n"
            f"DEBUG CMD: {shlex.join(cmd)}\n"
            f"{'-'*70}\n"
            f"STDOUT: {clean_out if clean_out else '[Empty]'}\n"
            f"STDERR: {clean_err if clean_err else '[Empty]'}\n"
            f"{'!'*70}\n"
        )
        sys.__stderr__.write(flare_msg)
        sys.__stderr__.flush()

    def execute(self):
        """Loop through directories and force continuity even on SystemExit."""
        # NEW FIX: Ensure the base destination folder (the "glance" folder) exists
        if not self.dest_root.exists():
            self.log.verbose(f"Creating base science destination: {self.dest_root}")
            self.dest_root.mkdir(parents=True, exist_ok=True)

        if not self.prem_root.exists():
            self.log.error(f"On-Prem root not found: {self.prem_root}")
            return

        for instr_dir in [d for d in self.prem_root.iterdir() if d.is_dir()]:
            for prod_dir in [p for p in instr_dir.iterdir() if p.is_dir()]:
                rel_prod_path = prod_dir.relative_to(self.prem_root)
                g_prod_dir = self.gccs_root / rel_prod_path

                if g_prod_dir.exists():
                    try:
                        self.run_glance_report(prod_dir, g_prod_dir)
                    except (Exception, SystemExit) as e:
                        self.log.warn(f"Execution failed for {rel_prod_path}, skipping. ({type(e).__name__})")
                        continue

        self.log.info("Science Level Comparison Complete.")

# =============================================================================
# MAIN / PARSER
# =============================================================================

def parse_args():
    parser = argparse.ArgumentParser(prog="science_pave.py")
    parser.add_argument("prem_fld", help="Root for On-Prem")
    parser.add_argument("gccs_fld", help="Root for GCCS")
    parser.add_argument("dest_fld", help="Report destination")
    parser.add_argument("--fork", action="store_true")
    parser.add_argument("-q", "--quiet", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-d", "--debug", action="store_true")
    parser.add_argument("--bin", default="glance")
    return parser.parse_args()

def main():
    args = parse_args()
    lvl = "DEBUG" if args.debug else "VERBOSE" if args.verbose else "INFO"
    log = Logger(lvl)
    setup_interrupt_handler(log)
    ScienceAnalyzer(args, log).execute()

if __name__ == "__main__":
    main()
