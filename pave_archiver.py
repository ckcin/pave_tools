#!/usr/bin/env python3
"""
PAVE-ARCHIVER: Unified Workspace Lifecycle Manager
==================================================
VERSION: 2.10.0 (Combined Satellite PDF Records & Per-Sat Summaries)
"""

import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['MPLBACKEND'] = 'Agg'

import argparse
import tarfile
import shutil
import re
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
import pandas as pd
import numpy as np

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

# Upgraded to 14 digits to capture YYYYdddhhmmss and prevent OS-level overwriting
FILE_PATTERN = re.compile(
    r"(?:OR_)?(?:ABI-L2-|I_)?(?P<prod>[A-Za-z0-9\-]+).*?_G(?P<sat>\d{2})_(?P<var>.+?)_(?P<time>\d{14})_comparison\.png$"
)

# ==========================================
# PHASE 1: ARTIFACT HARVESTING & ARCHIVING
# ==========================================

def harvest_dashboard(workspace, dash_dir, log):
    """Extracts comparison artifacts, filtering for only the latest scene per variable, then DOY-groups them."""
    validation_dir = workspace / "validation"
    if not validation_dir.exists():
        return

    png_files = list(validation_dir.rglob("*_comparison.png"))

    if not png_files:
        return

    log.info(f"Harvesting artifacts into Dashboard: {dash_dir.name}...")

    # Track the latest file per unique (DSN/Scene, Sat, Variable) combo
    latest_files = {}
    unmatched_files = []

    for f in png_files:
        parent_stem = f.parent.name
        var_name = f.name.replace('_comparison.png', '')

        m = re.search(r"OR_(?P<dsn>.+?)_G(?P<sat>\d{2}).*?_s(?P<time>\d{14})", parent_stem)

        if m:
            dsn = m.group('dsn')
            sat = m.group('sat')
            time_str = m.group('time')
            time_int = int(time_str)
            yyyyddd = time_str[:7]

            key = (dsn, sat, var_name)

            if key not in latest_files or time_int > latest_files[key][0]:
                latest_files[key] = (time_int, f, time_str, yyyyddd)
        else:
            unmatched_files.append(f)

    total_copied = 0

    # 1. Export only the most recent matched scene files
    for (dsn, sat, var_name), (time_int, f, time_str, yyyyddd) in latest_files.items():
        new_name = f"OR_{dsn}_G{sat}_{var_name}_{time_str}_comparison.png"
        target_dir = dash_dir / yyyyddd
        target_dir.mkdir(parents=True, exist_ok=True)
        dest = target_dir / new_name
        shutil.copy2(f, dest)
        total_copied += 1

    # 2. Export any unmatched files normally
    for f in unmatched_files:
        parent_stem = f.parent.name
        new_name = f"{parent_stem}_{f.name}"
        target_dir = dash_dir / "Unknown_DOY"
        target_dir.mkdir(parents=True, exist_ok=True)
        dest = target_dir / new_name
        shutil.copy2(f, dest)
        total_copied += 1

    log.info(f"Filtered {len(png_files)} raw validation artifacts down to {total_copied} latest-scene artifacts.")

def archive_folder(folder_path, log):
    """Safely compresses a directory to tar.gz and removes the original if successful."""
    if not folder_path.exists() or not folder_path.is_dir():
        return

    if not any(folder_path.iterdir()):
        shutil.rmtree(folder_path)
        return

    tar_path = folder_path.parent / f"{folder_path.name}.tar.gz"
    log.info(f"Compressing {folder_path.name}/ into {tar_path.name}...")

    try:
        with tarfile.open(tar_path, "w:gz") as tar:
            tar.add(folder_path, arcname=folder_path.name)

        if tar_path.exists() and tar_path.stat().st_size > 0:
            shutil.rmtree(folder_path)
            log.verbose(f"  -> Validation passed. Removed original {folder_path.name}/")
        else:
            log.error(f"  -> CRITICAL: Verification failed for {tar_path.name}. Preserving original directory.")
    except Exception as e:
        log.error(f"Failed to archive {folder_path.name}: {e}")

def process_workspace(workspace, args, log):
    """Executes the lifecycle sweep on a single PAVE workspace."""
    workspace = Path(workspace).resolve()
    if not workspace.exists() or not workspace.is_dir():
        log.warn(f"Workspace not found: {workspace}")
        return

    log.info(f"--- Lifecycle Sweep: {workspace.name} ---")

    if args.clean_validation:
        dash_dir = Path(args.dashboard).resolve() if args.dashboard else workspace / "dashboard"
        harvest_dashboard(workspace, dash_dir, log)

    core_folders = ["prem", "gccs", "collocation", "logs"]
    if args.clean_validation: core_folders.append("validation")
    if args.clean_glance: core_folders.append("glance")

    for f_name in core_folders:
        folder_path = workspace / f_name
        if folder_path.exists():
            archive_folder(folder_path, log)


# ==========================================
# PHASE 2: LONG-TERM RECORD GENERATION
# ==========================================

def get_variable_stats(stats_df, prod, sat=None, var=None):
    """Extracts and averages the target metrics for a specific variable. Handles combined satellite averaging if sat=None."""
    if stats_df is None or stats_df.empty:
        return np.nan, np.nan, np.nan

    subset = stats_df[stats_df['Product'] == prod]
    if var:
        subset = subset[subset['Variable'] == var]
    if sat:
        subset = subset[subset['Sat'] == sat]

    if subset.empty:
        return np.nan, np.nan, np.nan

    r2_vals = subset[subset['Metric'].str.contains('r-squared', case=False, na=False)]['Mean']
    err_vals = subset[subset['Metric'].str.contains('mean abs error', case=False, na=False)]['Mean']
    range_vals = subset[subset['Metric'].str.contains('range', case=False, na=False)]['Max']

    avg_r2 = r2_vals.mean() if not r2_vals.empty else np.nan
    avg_err = err_vals.mean() if not err_vals.empty else np.nan
    max_range = range_vals.max() if not range_vals.empty else np.nan

    return avg_r2, avg_err, max_range

def _draw_summary_page(pdf, title, subtitle, prod, var_list, stats_df, sat_filter=None):
    """Helper function to draw a standardized summary table page."""
    fig = plt.figure(figsize=(11, 8.5))
    fig.text(0.5, 0.90, title, ha='center', va='center', fontsize=26, weight='bold')
    fig.text(0.5, 0.83, f"Product: {prod}", ha='center', va='center', fontsize=20)
    fig.text(0.5, 0.78, subtitle, ha='center', va='center', fontsize=16, color='gray')

    ax_table = fig.add_axes([0.1, 0.05, 0.8, 0.65])
    ax_table.axis('off')

    table_data = [["Variable Name", "Avg R-Squared", "Avg Err Dispersion", "Value Range Limit"]]
    cell_colors = [["#40466e"] * 4]

    r2_tracker = []
    err_tracker = []

    for var in sorted(var_list):
        avg_r2, avg_err, val_range = get_variable_stats(stats_df, prod, sat=sat_filter, var=var)

        if pd.notna(avg_r2): r2_tracker.append(avg_r2)
        if pd.notna(avg_err): err_tracker.append(avg_err)

        r2_str = f"{avg_r2:.4f}" if pd.notna(avg_r2) else "N/A"
        err_str = f"{avg_err:.4f}" if pd.notna(avg_err) else "N/A"
        range_str = f"{val_range:.4f}" if pd.notna(val_range) else "N/A"

        row = [var, r2_str, err_str, range_str]

        if pd.isna(avg_r2): color = "lightgray"
        elif avg_r2 >= 0.95: color = "palegreen"
        elif avg_r2 >= 0.85: color = "moccasin"
        else: color = "lightcoral"

        table_data.append(row)
        cell_colors.append([color] * 4)

    if r2_tracker:
        overall_r2 = np.mean(r2_tracker)
        overall_err = np.mean(err_tracker) if err_tracker else np.nan
        overall_color = "palegreen" if overall_r2 >= 0.95 else "moccasin" if overall_r2 >= 0.85 else "lightcoral"

        table_data.append(["OVERALL AVERAGE", f"{overall_r2:.4f}", f"{overall_err:.4f}" if pd.notna(overall_err) else "N/A", "N/A"])
        cell_colors.append([overall_color] * 4)

    if len(table_data) > 1:
        table = ax_table.table(cellText=table_data, cellColours=cell_colors, loc='center', cellLoc='center', colWidths=[0.4, 0.2, 0.2, 0.2])
        table.auto_set_font_size(False)
        table.set_fontsize(10)
        table.scale(1, 1.5)

        for j in range(4):
            table[(0, j)].get_text().set_color('white')
            table[(0, j)].get_text().set_weight('bold')

        if r2_tracker:
            last_row_idx = len(table_data) - 1
            for j in range(4):
                table[(last_row_idx, j)].get_text().set_weight('bold')
    else:
        fig.text(0.5, 0.35, "No statistical data available for table generation.", ha='center', va='center', fontsize=12, color='gray')

    pdf.savefig(fig)
    plt.close(fig)

def build_pdf_artifact(prod, sats_dict, out_dir, stats_df, log):
    """Compiles chronological images and summary tables for both satellites into a single PDF."""
    pdf_filename = f"PAVE_Record_{prod}.pdf"
    pdf_path = out_dir / pdf_filename

    log.info(f"Assembling Combined Execution Artifact: {pdf_filename}...")

    with PdfPages(pdf_path) as pdf:
        # 1. Gather master list of variables and stats for the combined cover page
        all_vars = set()
        total_images = 0
        sats_present = list(sats_dict.keys())

        for sat_data in sats_dict.values():
            all_vars.update(sat_data.keys())
            total_images += sum(len(items) for items in sat_data.values())

        all_vars = sorted(list(all_vars))
        gen_time = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

        # 2. Render Master Cover Page (Combined Average across all Satellites)
        master_subtitle = f"Satellites: {', '.join([f'G{s}' for s in sats_present])} | Generated: {gen_time} UTC | {total_images} Total Snapshots"
        _draw_summary_page(pdf, "PAVE Long-Term Verification Record (Combined)", master_subtitle, prod, all_vars, stats_df, sat_filter=None)

        # 3. Process Per-Satellite Pages (Forcing G19 first, then G18)
        for target_sat in ["19", "18"]:
            if target_sat not in sats_dict:
                continue

            sat_vars = sats_dict[target_sat]
            sat_total_imgs = sum(len(items) for items in sat_vars.values())

            # 4. Render Satellite-Specific Summary Page
            sat_subtitle = f"Satellite: GOES-{target_sat} Isolated Summary | {sat_total_imgs} Snapshots"
            _draw_summary_page(pdf, f"GOES-{target_sat} Breakdown", sat_subtitle, prod, sat_vars.keys(), stats_df, sat_filter=target_sat)

            # 5. Append Images for this Satellite
            for var in sorted(sat_vars.keys()):
                log.verbose(f"  -> Processing variable: {var} (G{target_sat})")
                for _, img_path in sat_vars[var]:
                    try:
                        img = plt.imread(img_path)
                        h, w = img.shape[:2]

                        fig = plt.figure(figsize=(w/100, h/100), dpi=100)
                        ax = fig.add_axes([0, 0, 1, 1])
                        ax.axis('off')

                        ax.imshow(img)
                        ax.text(0.01, 0.01, f"Artifact: {img_path.name}", transform=ax.transAxes, color='black',
                                fontsize=10, weight='bold', bbox=dict(facecolor='white', alpha=0.7, edgecolor='none'))

                        pdf.savefig(fig)
                        plt.close(fig)
                    except Exception as e:
                        log.warn(f"Failed to append image {img_path.name}: {e}")
                        plt.close('all')

def run_recorder(dashboard_dir, record_dir, stats_df, log):
    """Walks the dashboard directory, limits to the 3 absolute most recent images per var, and dispatches to PDF generator."""
    if not dashboard_dir.exists():
        log.error(f"Dashboard path does not exist for recording: {dashboard_dir}")
        return

    if stats_df is None or stats_df.empty:
        log.warn("No statistical baseline data was extracted. Tables will render as N/A.")
    else:
        log.info(f"Loaded statistical baseline containing {len(stats_df)} records.")

    record_dir.mkdir(parents=True, exist_ok=True)
    png_files = sorted(list(dashboard_dir.rglob("*_comparison.png")))

    log.info(f"Discovered {len(png_files)} dashboard files. Filtering history for the 3 most recent runs overall...")

    # Structure: records[prod][sat][var] = [(full_time_int, filepath), ...]
    records = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    matched_count = 0

    for f in png_files:
        match = FILE_PATTERN.search(f.name)
        if not match: continue

        prod, sat, var, time_str = match.group('prod'), match.group('sat'), match.group('var'), match.group('time')
        full_time_int = int(time_str)

        records[prod][sat][var].append((full_time_int, f))
        matched_count += 1

    if matched_count == 0:
        log.warn("No files matched the required PAVE comparison naming format.")
        return

    # Trim logic: Keep only the 3 most recent runs overall per variable
    for prod, sats in records.items():
        for sat, var_dict in sats.items():
            for var in var_dict:
                recent_three = sorted(var_dict[var], key=lambda x: x[0], reverse=True)[:3]
                var_dict[var] = sorted(recent_three, key=lambda x: x[0])

    log.info(f"Successfully filtered images down to the 3 most recent executions overall per variable.")

    # Pass the entire sats dictionary for a single product to allow combined plotting
    for prod, sats_dict in records.items():
        build_pdf_artifact(prod, sats_dict, record_dir, stats_df, log)

def main():
    parser = argparse.ArgumentParser(description="PAVE-ARCHIVER: Unified Workspace Lifecycle Manager")
    parser.add_argument("workspaces", nargs="+", help="Paths to PAVE workspace directories to archive")

    # Archiver Flags
    parser.add_argument("--clean-validation", action="store_true", help="Harvest dashboard items, then archive and remove the validation/ folder")
    parser.add_argument("--clean-glance", action="store_true", help="Archive and remove the legacy glance/ folder")

    # Dashboard & Record Destinations
    parser.add_argument("--dashboard", type=str, help="Optional: Shared path to aggregate all dashboard comparison images globally")
    parser.add_argument("--record", type=str, help="Optional: Trigger PDF generation and output artifacts to this path")

    # Logging
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("-q", "--quiet", action="store_true", help="Restrict logging to warnings/errors")

    args = parser.parse_args()

    log_level = "VERBOSE" if args.verbose else "QUIET" if args.quiet else "INFO"
    log = Logger(log_level)
    setup_interrupt_handler(log)

    master_stats_list = []

    # 1. Execute Workspace Archival & Harvester
    for ws in args.workspaces:
        ws_path = Path(ws).resolve()

        # --- PRE-HARVEST STATS EXTRACTION ---
        if args.record:
            possible_stats = [
                ws_path / "stats" / "stats_summary.csv",
                ws_path / "stats_summary.csv",
                ws_path / "stats" / "glance_stats_summary.csv",
                ws_path / "glance_stats_summary.csv"
            ]

            stats_target = None
            for p in possible_stats:
                if p.exists():
                    stats_target = p
                    break

            if stats_target:
                try:
                    df = pd.read_csv(stats_target)
                    if 'Sat' in df.columns:
                        df['Sat'] = df['Sat'].astype(str).str.zfill(2)
                    master_stats_list.append(df)
                    log.debug(f"Successfully extracted memory stats from {ws_path.name} using {stats_target.name}")
                except Exception as e:
                    log.warn(f"Failed to read stats in {ws_path.name}: {e}")

        process_workspace(ws_path, args, log)

    # 2. Execute PDF Generation (If triggered)
    if args.record:
        if args.dashboard:
            combined_stats_df = pd.concat(master_stats_list, ignore_index=True) if master_stats_list else None
            run_recorder(Path(args.dashboard).resolve(), Path(args.record).resolve(), combined_stats_df, log)
        else:
            log.warn("Cannot generate central diurnal records without a shared --dashboard source path.")

if __name__ == "__main__":
    main()
