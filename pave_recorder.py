#!/usr/bin/env python3
"""
PAVE-RECORDER: Long-Term Artifact Generator
===========================================
VERSION: 1.0.0 (Diurnal PDF Compiler)
"""

import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['MPLBACKEND'] = 'Agg'

import re
import argparse
from pathlib import Path
from collections import defaultdict
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from datetime import datetime

try:
    from pave_utils import Logger, setup_interrupt_handler
except ImportError:
    class DummyLogger:
        def __init__(self, *args, **kwargs): pass
        def debug(self, m): print(f"[DEBUG] {m}")
        def info(self, m): print(f"[INFO] {m}")
        def verbose(self, m): print(f"[VERBOSE] {m}")
        def warn(self, m): print(f"[WARN] {m}")
        def error(self, m): print(f"[ERROR] {m}")
    Logger = DummyLogger
    def setup_interrupt_handler(log=None): pass

# Expected naming convention: *ABI-L2-<prod>-M6_G<sat>_<var>_<YYYYdddhh>_comparison.png
FILE_PATTERN = re.compile(
    r"ABI-L2-(?P<prod>[A-Za-z0-9]+).*?_G(?P<sat>\d{2})_(?P<var>.+?)_(?P<time>\d{9})_comparison\.png$"
)

def build_pdf_artifact(prod, sat, var_dict, out_dir, log):
    """Compiles the filtered images into a single multi-page PDF."""
    pdf_filename = f"PAVE_Record_{prod}_G{sat}.pdf"
    pdf_path = out_dir / pdf_filename

    log.info(f"Assembling Artifact: {pdf_filename}...")

    with PdfPages(pdf_path) as pdf:
        # --- 1. Generate Cover Page ---
        fig = plt.figure(figsize=(11, 8.5))
        fig.text(0.5, 0.65, "PAVE Long-Term Verification Record", ha='center', va='center', fontsize=26, weight='bold')
        fig.text(0.5, 0.55, f"Product: {prod}", ha='center', va='center', fontsize=20)
        fig.text(0.5, 0.50, f"Satellite: GOES-{sat}", ha='center', va='center', fontsize=18)
        fig.text(0.5, 0.35, f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC", ha='center', va='center', fontsize=12, color='gray')

        # Calculate total images included
        total_images = sum(len(hh_dict) for hh_dict in var_dict.values())
        fig.text(0.5, 0.30, f"Includes {len(var_dict)} Variables | {total_images} Total Diurnal Snapshots", ha='center', va='center', fontsize=12, color='gray')

        pdf.savefig(fig)
        plt.close(fig)

        # --- 2. Append Diurnal Images per Variable ---
        for var in sorted(var_dict.keys()):
            log.verbose(f"  -> Processing variable: {var}")
            hh_dict = var_dict[var]

            # Sort chronologically by hour (00 -> 23)
            for hh in sorted(hh_dict.keys()):
                yyyyddd, img_path = hh_dict[hh]

                try:
                    # Read the image to get native aspect ratio
                    img = plt.imread(img_path)
                    h, w = img.shape[:2]

                    # Dynamically size the figure to match the image precisely (100 DPI base)
                    fig = plt.figure(figsize=(w/100, h/100), dpi=100)
                    ax = fig.add_axes([0, 0, 1, 1])
                    ax.axis('off')

                    ax.imshow(img)

                    # Add footer annotation for artifact tracking
                    ax.text(0.01, 0.01, f"Artifact: {img_path.name}", transform=ax.transAxes, color='black',
                            fontsize=10, weight='bold', bbox=dict(facecolor='white', alpha=0.7, edgecolor='none'))

                    pdf.savefig(fig)
                    plt.close(fig)
                except Exception as e:
                    log.warn(f"Failed to append image {img_path.name}: {e}")
                    plt.close('all')

def run_recorder(base_path, out_dir, log):
    """Walks directory, filters images based on target logic, and dispatches to PDF generator."""
    if not base_path.exists():
        log.error(f"Provided path does not exist: {base_path}")
        return

    out_dir.mkdir(parents=True, exist_ok=True)

    log.info(f"Scanning directory tree at {base_path}...")

    # Sorting ensures that if multiple files have the exact same YYYYdddhh,
    # the lexicographically "last" one (e.g. highest minute/second if included in parent folder) is evaluated last.
    png_files = sorted(list(base_path.rglob("*_comparison.png")))

    log.info(f"Discovered {len(png_files)} potential comparison files. Filtering by naming convention...")

    # Dictionary Structure: records[prod][sat][var][hh] = (yyyyddd, file_path)
    records = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))

    matched_count = 0

    for f in png_files:
        match = FILE_PATTERN.search(f.name)
        if not match:
            continue

        prod = match.group('prod')
        sat = match.group('sat')
        var = match.group('var')
        time_str = match.group('time')  # 9 digits: YYYYdddhh

        yyyyddd = int(time_str[:7])
        hh = time_str[7:]

        matched_count += 1

        # FILTER LOGIC: Only use the highest YYYYddd for each hh.
        # If there's a tie (>=), overwrite it with the newly encountered one (which is the "last" due to prior sorting).
        current_best = records[prod][sat][var].get(hh)
        if not current_best or yyyyddd >= current_best[0]:
            records[prod][sat][var][hh] = (yyyyddd, f)

    if matched_count == 0:
        log.warn("No files matched the required *ABI-L2-<prod>-M6_G<sat>_<var>_<YYYYdddhh>_comparison.png format.")
        return

    log.info(f"Successfully filtered {matched_count} images down to the highest diurnal records.")

    # Generate a PDF for each Product & Satellite pair
    for prod, sats in records.items():
        for sat, var_dict in sats.items():
            build_pdf_artifact(prod, sat, var_dict, out_dir, log)

    log.info(f"PAVE Recorder execution complete. Artifacts saved to: {out_dir}")

def main():
    parser = argparse.ArgumentParser(description="PAVE-RECORDER: Long-Term Diurnal PDF Artifact Generator")
    parser.add_argument("--path", required=True, help="Base directory containing the comparison images to walk")
    parser.add_argument("--out", default="./pave_artifacts", help="Output directory for generated PDFs")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("-q", "--quiet", action="store_true", help="Restrict logging to warnings/errors")

    args = parser.parse_args()

    log_level = "VERBOSE" if args.verbose else "QUIET" if args.quiet else "INFO"
    log = Logger(log_level)
    setup_interrupt_handler(log)

    base_path = Path(args.path).resolve()
    out_dir = Path(args.out).resolve()

    run_recorder(base_path, out_dir, log)

if __name__ == "__main__":
    main()
