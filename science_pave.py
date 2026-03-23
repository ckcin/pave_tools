#!/usr/bin/env python3
"""
SCIENCE-PAVE: Science Level Comparison Engine
==============================================
Wrapper for UW-Glance to generate product-level HTML reports.

VERSION: 1.2.0 (Corrected --fork behavior)
"""

import os
import subprocess
import shlex
import argparse
from pathlib import Path

# Shared Infrastructure
from pave_utils import Logger, setup_interrupt_handler

# =============================================================================
# CLI ARGUMENT DEFINITION
# =============================================================================

def parse_args():
    """Defines the CLI interface for the science analyzer."""
    parser = argparse.ArgumentParser(
        prog="science_pave.py",
        description="Executes 'glance report' with output suppression on success."
    )
    # Positional Arguments
    parser.add_argument("prem_fld", help="Root folder for On-Prem data")
    parser.add_argument("gccs_fld", help="Root folder for GCCS data")
    parser.add_argument("dest_fld", help="Target folder for Glance reports")

    # Options
    parser.add_argument("--fork", action="store_true", help="Enable glance's internal parallel processing")
    parser.add_argument("-q", "--quiet", action="store_true", help="Only show WARN/ERROR")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose messaging")
    parser.add_argument("-d", "--debug", action="store_true", help="Enable debug messaging")

    # Glance Specifics
    parser.add_argument("--bin", default="glance", help="Path to the glance executable")

    return parser.parse_args()

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
        self.log = log

    def run_glance_report(self, p_prod_dir, g_prod_dir):
        """
        Runs 'glance report' and captures output.
        Only displays output if the command fails (returncode != 0).
        """
        rel_path = p_prod_dir.relative_to(self.prem_root)
        report_dest = self.dest_root / rel_path
        report_dest.mkdir(parents=True, exist_ok=True)

        # Build command list
        cmd = [
            self.glance_bin,
            "report",
            "--nolonlat"
        ]

        if self.use_fork:
            cmd.append("--fork")

        cmd += [
            "-p", str(report_dest),
            str(p_prod_dir),
            str(g_prod_dir),
            "--stripfromname", "e.*"
        ]

        self.log.info(f"Generating Science Report: {rel_path}{' (Parallel/Fork active)' if self.use_fork else ''}")
        self.log.debug(f"  [EXEC] {shlex.join(cmd)}")

        # Capture all output silently
        result = subprocess.run(cmd, capture_output=True, text=True)

        # Only report if something went wrong
        if result.returncode != 0:
            self.log.warn(f"!!! GLANCE ERROR DETECTED for {rel_path} !!!")
            if result.stdout:
                self.log.warn(f"--- STDOUT ---\n{result.stdout.strip()}")
            if result.stderr:
                self.log.warn(f"--- STDERR ---\n{result.stderr.strip()}")
        else:
            self.log.verbose(f"  Success. Report built: {report_dest}/index.html")

    def execute(self):
        """Iterates through Product directories and triggers the report tool."""
        self.log.info("Science Level Comparison Verification")

        if not self.prem_root.exists():
            self.log.error(f"On-Prem root not found: {self.prem_root}")

        # Traversal: Root -> Instrument -> Product
        for instr_dir in [d for d in self.prem_root.iterdir() if d.is_dir()]:
            for prod_dir in [p for p in instr_dir.iterdir() if p.is_dir()]:

                rel_prod_path = prod_dir.relative_to(self.prem_root)
                g_prod_dir = self.gccs_root / rel_prod_path

                if g_prod_dir.exists():
                    self.run_glance_report(prod_dir, g_prod_dir)
                else:
                    self.log.debug(f"No GCCS peer for: {rel_prod_path}")

        self.log.info(f"Science Level Comparison Complete. Reports: {self.dest_root}")

# =============================================================================
# MAIN EXECUTION
# =============================================================================

def main():
    args = parse_args()

    lvl = "DEBUG" if args.debug else "VERBOSE" if args.verbose else "QUIET" if args.quiet else "INFO"
    log = Logger(lvl)

    # Enable graceful Ctrl+C termination from pave_utils
    setup_interrupt_handler(log)

    analyzer = ScienceAnalyzer(args, log)
    analyzer.execute()

if __name__ == "__main__":
    main()
