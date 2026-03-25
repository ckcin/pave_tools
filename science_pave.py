#!/usr/bin/env python3
"""
SCIENCE-PAVE: Science Level Comparison Engine
==============================================
VERSION: 1.5.0 (Headless Agg Backend & Silent Verbose Capture)
Optimized for batch performance using MPLBACKEND=Agg.
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

# Suppress matplotlib/legend warnings in the parent process
warnings.filterwarnings("ignore", message="No artists with labels found to put in legend")

# =============================================================================
# CORE ANALYSIS ENGINE
# =============================================================================

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
        if not text: return ""
        lines = text.splitlines()
        filtered = [l for l in lines if "No artists with labels found" not in l]
        return "\n".join(filtered).strip()

    def run_glance_report(self, p_prod_dir, g_prod_dir):
        """Runs glance with Agg backend and silent verbose capture."""
        rel_path = p_prod_dir.relative_to(self.prem_root)
        report_dest = self.dest_root / rel_path
        report_dest.mkdir(parents=True, exist_ok=True)
        index_file = report_dest / "index.html"

        # 1. Build Command
        cmd = [self.glance_bin, "report", "--nolonlat"]
        if self.is_debug or self.is_verbose:
            cmd.append("--verbose")
        if self.use_fork:
            cmd.append("--fork")
        cmd += ["-p", str(report_dest), str(p_prod_dir), str(g_prod_dir), "--stripfromname", "e.*"]

        self.log.info(f"Generating Science Report: {rel_path}")

        # 2. Configure Environment (The 'Agg' Optimization)
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["MPLBACKEND"] = "Agg" # Force non-interactive rendering for speed/stability

        # 3. Execute and Capture
        with tempfile.NamedTemporaryFile(mode='w+', suffix='.out') as f_out, \
             tempfile.NamedTemporaryFile(mode='w+', suffix='.err') as f_err:

            result = subprocess.run(cmd, stdout=f_out, stderr=f_err, env=env)

            # Check for failures
            if result.returncode == 4:
                self.log.warn(f"  [WARNING] {rel_path} exited with status 4 (Comparison Differences).")
                return

            if result.returncode != 0 or not index_file.exists():
                f_out.seek(0); raw_stdout = f_out.read()
                f_err.seek(0); raw_stderr = f_err.read()

                self.log.error(f"!!! FAILURE: {rel_path} (Exit: {result.returncode}) !!!")
                self._flare_error(rel_path, result.returncode, cmd, raw_stdout, raw_stderr)
            else:
                self.log.verbose(f"  Success: {index_file}")

    def _flare_error(self, rel_path, code, cmd, stdout, stderr):
        clean_out = self._scrub_output(stdout)
        clean_err = self._scrub_output(stderr)
        flare_msg = (
            f"\n{'!'*80}\n"
            f"GLANCE REPORT FATAL ERROR (Exit: {code})\n"
            f"PRODUCT: {rel_path}\n"
            f"DEBUG CMD: {shlex.join(cmd)}\n"
            f"{'-'*80}\n"
            f"CAPTURED VERBOSE LOGS:\n"
            f"{clean_err if clean_err else '[No Stderr Content]'}\n"
            f"{'-'*80}\n"
            f"STDOUT SUMMARY:\n"
            f"{clean_out if clean_out else '[No Stdout Content]'}\n"
            f"{'!'*80}\n"
        )
        sys.__stderr__.write(flare_msg)
        sys.__stderr__.flush()

    def execute(self):
        if not self.dest_root.exists():
            self.dest_root.mkdir(parents=True, exist_ok=True)

        for instr_dir in [d for d in self.prem_root.iterdir() if d.is_dir()]:
            for prod_dir in [p for p in instr_dir.iterdir() if p.is_dir()]:
                rel_prod_path = prod_dir.relative_to(self.prem_root)
                g_prod_dir = self.gccs_root / rel_prod_path

                if g_prod_dir.exists():
                    try:
                        self.run_glance_report(prod_dir, g_prod_dir)
                    except (Exception, SystemExit) as e:
                        self.log.warn(f"Execution stalled for {rel_prod_path}. ({type(e).__name__})")
                        continue

        self.log.info("Science Level Comparison Complete.")

# =============================================================================
# ENTRY POINT
# =============================================================================

def parse_args():
    parser = argparse.ArgumentParser(prog="science_pave.py")
    parser.add_argument("prem_fld")
    parser.add_argument("gccs_fld")
    parser.add_argument("dest_fld")
    parser.add_argument("--fork", action="store_true")
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
