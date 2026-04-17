#!/usr/bin/env python3
"""
SCIENCE-PAVE: Science Level Comparison Engine
==============================================
VERSION: 1.6.2 (Restored Flare Error & Full Variable Audit)
"""

import os
import subprocess
import shlex
import argparse
import warnings
import sys
import tempfile
import time
from pathlib import Path
from pave_utils import Logger, setup_interrupt_handler

# Suppress common Matplotlib legend warnings from filling up the logs
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
        """Removes repetitive Matplotlib warnings from stderr strings."""
        if not text:
            return ""
        return "\n".join([l for l in text.splitlines() if "No artists with labels found" not in l]).strip()

    def decode_glance_status(self, code):
        """Interprets cumulative Glance exit codes to estimate file counts."""
        if code == 0:
            return "Perfect Match"
        if code % 80 == 0:
            return f"Skipped: {code // 80} files had no common variables (Exit 80)"
        if code % 4 == 0:
            return f"Analysis Complete: {code // 4} files had differences (Exit 4)"
        return f"Non-fatal status (Exit {code})"

    def run_glance_report(self, p_prod_dir, g_prod_dir, progress_str=""):
        """Executes Glance for ALL variables while optimizing thread usage."""
        rel_path = p_prod_dir.relative_to(self.prem_root)
        report_dest = self.dest_root / rel_path
        report_dest.mkdir(parents=True, exist_ok=True)

        cmd = [self.glance_bin, "report", "--nolonlat"]
        if self.is_debug or self.is_verbose:
            cmd.append("--verbose")
        if self.use_fork:
            cmd.append("--fork")

        cmd += ["-p", str(report_dest), str(p_prod_dir), str(g_prod_dir), "--stripfromname", "e.*"]

        self.log.info(f"{progress_str} Generating Science Report: {rel_path}")
        self.log.debug(f"  [CLI EXEC] {shlex.join(cmd)}")

        start_time = time.perf_counter()

        # HIGH-PERFORMANCE ENVIRONMENT
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["MPLBACKEND"] = "Agg" # Fast non-interactive backend

        # Stop internal library thread-storms to focus CPU on Glance's --fork processes
        env["MKL_NUM_THREADS"] = "1"
        env["OPENBLAS_NUM_THREADS"] = "1"
        env["OMP_NUM_THREADS"] = "1"
        env["VECLIB_MAXIMUM_THREADS"] = "1"
        env["NUMEXPR_NUM_THREADS"] = "1"

        with tempfile.NamedTemporaryFile(mode='w+', suffix='.out') as f_out, \
             tempfile.NamedTemporaryFile(mode='w+', suffix='.err') as f_err:

            result = subprocess.run(cmd, stdout=f_out, stderr=f_err, env=env)
            elapsed = time.perf_counter() - start_time

            # 1. FATAL SYSTEM ERROR: Restored _flare_error call
            if result.returncode == 1:
                f_out.seek(0)
                f_err.seek(0)
                self._flare_error(rel_path, result.returncode, cmd, f_out.read(), f_err.read())
                self.log.error(f"!!! GLANCE FATAL ERROR: {rel_path} (Exit: 1) !!!")

            # 2. NON-FATAL STATUS
            elif result.returncode != 0:
                decoded_msg = self.decode_glance_status(result.returncode)
                self.log.warn(f"  [STATUS {result.returncode}] {decoded_msg} (Duration: {elapsed:.2f}s)")

            # 3. SUCCESS
            else:
                self.log.debug(f"  Success: {rel_path} (Duration: {elapsed:.2f}s)")

    def _flare_error(self, rel_path, code, cmd, stdout, stderr):
        """Critical failure logging to stderr including exact CLI call."""
        clean_err = self._scrub_output(stderr)
        msg = f"\n{'!'*80}\nGLANCE HARD ERROR | {rel_path} | Code: {code}\n{'-'*80}\nCALL: {shlex.join(cmd)}\n{'-'*80}\nSTDERR: {clean_err}\n{'!'*80}\n"
        sys.__stderr__.write(msg)
        sys.__stderr__.flush()

    def execute(self):
        """Scans workspace and manages the sequential product queue with progress tracking."""
        if not self.dest_root.exists():
            self.dest_root.mkdir(parents=True, exist_ok=True)

        work_queue = []
        for instr_dir in [d for d in self.prem_root.iterdir() if d.is_dir()]:
            for prod_dir in [p for p in instr_dir.iterdir() if p.is_dir()]:
                g_prod_dir = self.gccs_root / prod_dir.relative_to(self.prem_root)
                if g_prod_dir.exists():
                    work_queue.append((prod_dir, g_prod_dir))

        total_prods = len(work_queue)
        self.log.info(f"Science Analysis Queue: {total_prods} products found (Full Variable Audit).")

        for i, (p_dir, g_dir) in enumerate(work_queue, 1):
            try:
                self.run_glance_report(p_dir, g_dir, progress_str=f"[{i}/{total_prods}]")
            except (Exception, SystemExit) as e:
                self.log.warn(f"Execution stalled for {p_dir.name}: {e}")
                continue

        self.log.info("Science Level Comparison Complete.")

def parse_args():
    parser = argparse.ArgumentParser(prog="science_pave.py", description="Generate Glance reports.")
    parser.add_argument("prem_fld", help="On-Prem root folder")
    parser.add_argument("gccs_fld", help="GCCS root folder")
    parser.add_argument("dest_fld", help="Report destination folder")
    parser.add_argument("--fork", action="store_true", help="Parallelize report generation")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logs")
    parser.add_argument("-d", "--debug", action="store_true", help="Debug logs")
    parser.add_argument("-q", "--quiet", action="store_true", help="Minimal output")
    parser.add_argument("--bin", default="glance", help="Glance binary path")
    return parser.parse_args()

def main():
    args = parse_args()
    log = Logger("DEBUG" if args.debug else "VERBOSE" if args.verbose else "QUIET" if args.quiet else "INFO")
    setup_interrupt_handler(log)
    ScienceAnalyzer(args, log).execute()

if __name__ == "__main__":
    main()
