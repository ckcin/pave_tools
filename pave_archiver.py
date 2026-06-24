#!/usr/bin/env python3
"""
PAVE-ARCHIVER: Unified Workspace Lifecycle Manager
==================================================
VERSION: 3.8.0 (Strict Per-Channel PDF Isolation)

LIFECYCLE & REPORTING ARCHITECTURE:
-----------------------------------
This engine operates in two distinct phases to manage disk space and generate
long-term verification artifacts.

PHASE 1: Workspace Cleanup & Dashboard Harvesting
   - Evaluates completed PAVE workspaces and identifies comparison artifacts (*_comparison.png).
   - Prevents dashboard bloat by filtering images down to the single latest scene.
   - TARBALL PURGE: Actively deletes massive IP .tar files and ip_data/ folders to prevent storage leaks.
   - Compresses heavy spatial/data directories into .tar.gz archives.

PHASE 2: Historical Crawling & Long-Term Record Generation
   - Safely parses ragged CSVs to prevent Pandas Multi-Index shifting bugs.
   - SCENE MERGING & CHANNEL ISOLATION: Forces channels to separate into distinct PDFs
     (e.g. DMW_C08 vs DMW_C10) regardless of upstream utility grouping logic.
   - TABLE PAGINATION: Cleanly spans variable lists across multiple pages (capped at 20 rows).
   - DEBUG ENGINE: Traces string matching and data frames ONLY when --debug is explicitly passed.
"""

import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['MPLBACKEND'] = 'Agg'

import argparse
import tarfile
import shutil
import re
import csv
import time as time_mod
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone

# Restored to global scope for clean linting and stable execution
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

try:
    from pave_utils import Logger, setup_interrupt_handler, get_family_for_product
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
    def get_family_for_product(prod): return prod.upper()

FILE_PATTERN = re.compile(
    r"^OR_(?P<prod>.+?)_G(?P<sat>\d{2})_(?P<var>.+?)_(?P<time>\d{14})_comparison\.png$"
)

# Global toggle for deep logging configuration
DEBUG_STATS_MODE = False

# ==========================================
# UTILITY: SCENE PARSING & FILTERING
# ==========================================

PARENT_TIME_PATTERN = re.compile(r"OR_(?P<dsn>.+?)_G(?P<sat>\d{2}).*?_s(?P<time>\d{14})")

def clean_product_name(prod_str):
    """Normalizes wild GOES namings by stripping L1b/L2 prefixes, mode, and scene tags, but PRESERVING the channel."""
    clean = str(prod_str)

    # Make mode optional in case a product is named ABI-L2-DMWF-C08 instead of -M6C08
    ch_match = re.search(r'-(?:M\d+)?(C\d+)', clean, flags=re.IGNORECASE)
    ch_suffix = f"_{ch_match.group(1).upper()}" if ch_match else ""

    clean = re.sub(r'^(ABI-L[12][a-zA-Z]?-|I_ABI-L[12][a-zA-Z]?-|I_)', '', clean, flags=re.IGNORECASE)
    clean = re.sub(r'-(?:M\d+)?(C\d+)?$', '', clean, flags=re.IGNORECASE)
    clean = re.sub(r'(F|C|M1|M2)$', '', clean, flags=re.IGNORECASE)

    return f"{clean.strip().upper()}{ch_suffix}"

def extract_run_datetime(path):
    """Parse a comparison artifact timestamp from the file name or its parent folder."""
    match = FILE_PATTERN.search(path.name)
    if match:
        try:
            time_str = match.group('time')[:13]
            return datetime.strptime(time_str, "%Y%j%H%M%S")
        except ValueError:
            pass

    match = PARENT_TIME_PATTERN.search(path.parent.name)
    if match:
        try:
            time_str = match.group('time')[:13]
            return datetime.strptime(time_str, "%Y%j%H%M%S")
        except ValueError:
            pass

    return None

def get_recent_run_dates(paths, max_dates=3):
    """Returns the latest N distinct run dates present in the provided comparison files."""
    run_dates = set()
    for f in paths:
        dt = extract_run_datetime(f)
        if dt:
            run_dates.add(dt.date())
    if not run_dates:
        return set()
    return set(sorted(run_dates, reverse=True)[:max_dates])

# ==========================================
# PHASE 1: ARTIFACT HARVESTING & ARCHIVING
# ==========================================

def harvest_dashboard(workspace, dash_dir, log):
    """Extracts comparison artifacts from the most recent 3 run dates, filtering for only the latest scene per variable, then DOY-groups them."""
    validation_dir = workspace / "validation"
    if not validation_dir.exists():
        return

    png_files = list(validation_dir.rglob("*_comparison.png"))

    if not png_files:
        return

    recent_dates = get_recent_run_dates(png_files, max_dates=3)
    if not recent_dates:
        log.info(f"No recent run dates could be determined from {validation_dir.name}/")
        return

    filtered_files = [f for f in png_files if extract_run_datetime(f) and extract_run_datetime(f).date() in recent_dates]

    if not filtered_files:
        log.info(f"No validation artifacts found for the latest {len(recent_dates)} run dates in {validation_dir.name}/")
        return

    log.info(f"Harvesting {len(filtered_files)}/{len(png_files)} artifacts from the latest {len(recent_dates)} run dates into Dashboard: {dash_dir.name}...")

    latest_files = {}
    unmatched_files = []

    for f in filtered_files:
        m = FILE_PATTERN.search(f.name)
        if m:
            dsn = m.group('prod')
            sat = m.group('sat')
            var_name = m.group('var')
            time_str = m.group('time')
            time_int = int(time_str)
            yyyyddd = time_str[:7]

            key = (dsn, sat, var_name)
            if key not in latest_files or time_int > latest_files[key][0]:
                latest_files[key] = (time_int, f, time_str, yyyyddd)
        else:
            unmatched_files.append(f)

    total_copied = 0

    for (dsn, sat, var_name), (time_int, f, time_str, yyyyddd) in latest_files.items():
        new_name = f"OR_{dsn}_G{sat}_{var_name}_{time_str}_comparison.png"
        target_dir = dash_dir / yyyyddd
        target_dir.mkdir(parents=True, exist_ok=True)
        dest = target_dir / new_name
        shutil.copy2(f, dest)
        total_copied += 1

    for f in unmatched_files:
        parent_stem = f.parent.name
        new_name = f"{parent_stem}_{f.name}"
        target_dir = dash_dir / "Unknown_DOY"
        target_dir.mkdir(parents=True, exist_ok=True)
        dest = target_dir / new_name
        shutil.copy2(f, dest)
        total_copied += 1

    log.info(f"Filtered {len(png_files)} raw validation artifacts down to {total_copied} latest-scene artifacts.")

def purge_ip_tarballs(workspace, log):
    """Actively hunts and destroys massive .tar files and ip_data/ folders to prevent storage leaks."""
    ip_data_dir = workspace / "ip_data"
    if ip_data_dir.exists():
        log.verbose(f"Purging preserved IP data directory to reclaim space: {ip_data_dir.name}/")
        shutil.rmtree(ip_data_dir, ignore_errors=True)

    for data_dir in [workspace / "prem", workspace / "gccs"]:
        if data_dir.exists():
            for tar_file in data_dir.rglob("*.tar"):
                try:
                    log.verbose(f"Deleting leftover IP tarball: {tar_file.name}")
                    tar_file.unlink()
                except Exception as e:
                    log.debug(f"Could not delete tarball {tar_file.name}: {e}")

def archive_folder(folder_path, log):
    """Safely compresses a directory to tar.gz and removes the original if successful."""
    if not folder_path.exists() or not folder_path.is_dir():
        return

    if not any(folder_path.iterdir()):
        shutil.rmtree(folder_path)
        return

    tar_path = folder_path.parent / f"{folder_path.name}.tar.gz"
    log.verbose(f"Compressing {folder_path.name}/ into {tar_path.name}...")

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
    """Executes the lifecycle sweep on a single PAVE workspace and identifies active Product Families."""
    workspace = Path(workspace).resolve()
    if not workspace.exists() or not workspace.is_dir():
        log.warn(f"Workspace not found: {workspace}")
        return set()

    log.info(f"--- Lifecycle Sweep: {workspace.name} ---")

    active_families = set()

    try:
        parts = workspace.name.split('_')
        base_product = parts[0]

        ch_tag = ""
        for p in parts:
            if p.upper().startswith("CH") and p[2:].isdigit():
                ch_tag = f"_C{p[2:]}"
                break

        norm_prod = clean_product_name(base_product)

        # Guardrail: Check if a channel tag naturally embedded itself in the parsed name
        ch_match = re.search(r'(_C\d{2})$', norm_prod)
        if ch_match and not ch_tag:
            ch_tag = ch_match.group(1)

        # Isolate the base name from the channel to ensure utility grouping doesn't accidentally squash it
        base_norm = re.sub(r'_C\d{2}$', '', norm_prod)
        fam = get_family_for_product(base_norm)
        base_family = fam if fam else base_norm

        # Explicitly force the channel tag back onto the resolved family name!
        active_families.add(f"{base_family}{ch_tag}")
    except Exception as e:
        log.debug(f"Could not parse workspace name for family: {e}")

    val_dir = workspace / "validation"
    if val_dir.exists():
        for f in val_dir.rglob("*_comparison.png"):
            m = FILE_PATTERN.search(f.name)
            if m:
                norm_prod = clean_product_name(m.group('prod'))

                ch_match = re.search(r'(_C\d{2})$', norm_prod)
                ch_tag = ch_match.group(1) if ch_match else ""

                base_norm = re.sub(r'_C\d{2}$', '', norm_prod)
                fam = get_family_for_product(base_norm)
                base_family = fam if fam else base_norm

                active_families.add(f"{base_family}{ch_tag}")

    if args.clean_validation:
        dash_dir = Path(args.dashboard).resolve() if args.dashboard else workspace / "dashboard"
        harvest_dashboard(workspace, dash_dir, log)

    # Nuke the massive IP tarballs before archiving starts
    purge_ip_tarballs(workspace, log)

    core_folders = ["prem", "gccs", "collocation", "logs"]
    if args.clean_validation: core_folders.append("validation")
    if args.clean_glance: core_folders.append("glance")

    for f_name in core_folders:
        folder_path = workspace / f_name
        if folder_path.exists():
            archive_folder(folder_path, log)

    return active_families


# ==========================================
# PHASE 2: LONG-TERM RECORD GENERATION
# ==========================================

def get_variable_stats(stats_df, prod, sat=None, var=None):
    """Extracts and averages target metrics with a deep diagnostic tracing engine for validation."""
    if stats_df is None or stats_df.empty:
        if DEBUG_STATS_MODE:
            print(f"[STATS_ASSEMBLY_DEBUG] Query abandoned for '{prod}': Base database dataframe is empty.")
        return np.nan, np.nan, np.nan

    clean_target_prod = clean_product_name(prod)
    df_prods = stats_df['Product'].astype(str).apply(clean_product_name)

    subset = stats_df[df_prods == clean_target_prod]

    if var:
        subset = subset[subset['Variable'].astype(str).str.strip() == str(var).strip()]
    if sat:
        subset = subset[subset['Sat'].astype(str).str.strip() == str(sat).strip()]

    if subset.empty:
        if DEBUG_STATS_MODE:
            print(f"[STATS_ASSEMBLY_DEBUG] Lookup Match Failure -> Zero entries fetched for Key: '{clean_target_prod}', Var: '{var}', Sat: '{sat or 'ALL'}'")
        return np.nan, np.nan, np.nan

    def clean_series(series):
        s = pd.to_numeric(series, errors='coerce')
        return s.replace([np.inf, -np.inf], np.nan).dropna()

    r2_rows = subset[subset['Metric'].str.contains('r-squared', case=False, na=False)]
    err_rows = subset[subset['Metric'].str.contains('mean abs error', case=False, na=False)]
    range_rows = subset[subset['Metric'].str.contains('range', case=False, na=False)]

    r2_vals = clean_series(r2_rows['Mean'])
    err_vals = clean_series(err_rows['Mean'])
    range_vals = clean_series(range_rows['Max'])

    avg_r2 = r2_vals.mean() if not r2_vals.empty else np.nan
    avg_err = err_vals.mean() if not err_vals.empty else np.nan
    max_range = range_vals.max() if not range_vals.empty else np.nan

    if DEBUG_STATS_MODE:
        print(f"[STATS_ASSEMBLY_DEBUG] Mapping -> {clean_target_prod} | Variable: {var} | Satellite Scope: {sat or 'COMBINED'}")
        print(f"  |-> CSV Row Counts Found: R2 Rows={len(r2_rows)}, MAE Rows={len(err_rows)}, Range Rows={len(range_rows)}")
        print(f"  |-> Purged Mathematical Arrays: Valid_R2={list(r2_vals)}, Valid_MAE={list(err_vals)}, Valid_Range={list(range_vals)}")
        print(f"  |-> Final Floating Outputs: Avg_R2={avg_r2}, Avg_MAE={avg_err}, Max_Range={max_range}")

    return avg_r2, avg_err, max_range

def _draw_summary_page(pdf, title, subtitle, family, var_tuple_list, stats_df, sat_filter=None):
    """Helper function to draw summary tables, featuring automated pagination to prevent overflowing large suites."""
    header_row = ["Product: Variable", "Avg R-Squared", "Avg Err Dispersion", "Value Range Limit"]
    header_color = ["#40466e"] * 4

    all_data_rows = []
    all_data_colors = []

    unique_vars = set()
    for prod, var in var_tuple_list:
        unique_vars.add((clean_product_name(prod), var))

    if DEBUG_STATS_MODE:
        print(f"\n[STATS_ASSEMBLY_DEBUG] === Beginning Rendering Evaluation Matrix for Table: '{title}' ===")
        print(f"[STATS_ASSEMBLY_DEBUG] Isolated unique layout items discovered: {sorted(list(unique_vars))}")

    for (clean_prod, var) in sorted(list(unique_vars), key=lambda x: (x[0], x[1])):
        avg_r2, avg_err, val_range = get_variable_stats(stats_df, clean_prod, sat=sat_filter, var=var)

        r2_str = f"{avg_r2:.4f}" if pd.notna(avg_r2) else "N/A"
        err_str = f"{avg_err:.4f}" if pd.notna(avg_err) else "N/A"
        range_str = f"{val_range:.4f}" if pd.notna(val_range) else "N/A"

        display_name = f"{clean_prod}: {var}"
        row = [display_name, r2_str, err_str, range_str]

        if pd.isna(avg_r2): color = "lightgray"
        elif avg_r2 >= 0.95: color = "palegreen"
        elif avg_r2 >= 0.85: color = "moccasin"
        else: color = "lightcoral"

        all_data_rows.append(row)
        all_data_colors.append([color] * 4)

    if not all_data_rows:
        fig = plt.figure(figsize=(11, 8.5))
        fig.text(0.5, 0.90, title, ha='center', va='center', fontsize=26, weight='bold')
        fig.text(0.5, 0.83, f"Product Family: {family}", ha='center', va='center', fontsize=20)
        fig.text(0.5, 0.78, subtitle, ha='center', va='center', fontsize=16, color='gray')
        fig.text(0.5, 0.35, "No statistical data available for table generation.", ha='center', va='center', fontsize=12, color='gray')
        pdf.savefig(fig)
        plt.close(fig)
        return

    MAX_ROWS_PER_PAGE = 20
    total_pages = max(1, (len(all_data_rows) - 1) // MAX_ROWS_PER_PAGE + 1)

    for i in range(total_pages):
        start_idx = i * MAX_ROWS_PER_PAGE
        end_idx = start_idx + MAX_ROWS_PER_PAGE
        chunk_rows = all_data_rows[start_idx:end_idx]
        chunk_colors = all_data_colors[start_idx:end_idx]

        fig = plt.figure(figsize=(11, 8.5))
        fig.text(0.5, 0.90, title, ha='center', va='center', fontsize=26, weight='bold')
        fig.text(0.5, 0.83, f"Product Family: {family}", ha='center', va='center', fontsize=20)

        page_subtitle = subtitle + (f" | Page {i+1} of {total_pages}" if total_pages > 1 else "")
        fig.text(0.5, 0.78, page_subtitle, ha='center', va='center', fontsize=16, color='gray')

        ax_table = fig.add_axes([0.1, 0.05, 0.8, 0.65])
        ax_table.axis('off')

        table_data = [header_row] + chunk_rows
        cell_colors = [header_color] + chunk_colors

        table = ax_table.table(cellText=table_data, cellColours=cell_colors, loc='center', cellLoc='center', colWidths=[0.4, 0.2, 0.2, 0.2])
        table.auto_set_font_size(False)
        table.set_fontsize(10)

        table.scale(1, min(1.8, 40 / len(table_data)))

        for j in range(4):
            table[(0, j)].get_text().set_color('white')
            table[(0, j)].get_text().set_weight('bold')

        pdf.savefig(fig)
        plt.close(fig)

def build_pdf_artifact(family, sats_dict, out_dir, stats_df, log):
    """Compiles grouped chronological images and summary tables for a Product Family into a single PDF."""
    pdf_filename = f"PAVE_Record_{family}.pdf"
    pdf_path = out_dir / pdf_filename

    log.info(f"Assembling Product Family Artifact: {pdf_filename}...")

    with PdfPages(pdf_path) as pdf:
        all_var_tuples = set()
        total_images = 0
        sats_present = list(sats_dict.keys())

        for sat_data in sats_dict.values():
            all_var_tuples.update(sat_data.keys())
            total_images += sum(len(items) for items in sat_data.values())

        gen_time = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

        master_subtitle = f"Satellites: {', '.join([f'G{s}' for s in sats_present])} | Generated: {gen_time} UTC | {total_images} Total Snapshots"
        _draw_summary_page(pdf, "PAVE Long-Term Verification Record (Combined)", master_subtitle, family, all_var_tuples, stats_df, sat_filter=None)

        for target_sat in ["19", "18"]:
            if target_sat not in sats_dict:
                continue

            sat_vars = sats_dict[target_sat]
            sat_total_imgs = sum(len(items) for items in sat_vars.values())

            sat_subtitle = f"Satellite: GOES-{target_sat} Isolated Summary | {sat_total_imgs} Snapshots"
            _draw_summary_page(pdf, f"GOES-{target_sat} Breakdown", sat_subtitle, family, sat_vars.keys(), stats_df, sat_filter=target_sat)

            for (prod, var) in sorted(sat_vars.keys(), key=lambda x: (x[0], x[1])):
                log.verbose(f"  -> Processing variable: {prod}: {var} (G{target_sat})")
                for _, img_path in sat_vars[(prod, var)]:
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

def run_recorder(dashboard_dir, record_dir, stats_df, active_families, log):
    """Walks the dashboard directory, limits to 3 recent images per var, and dispatches to PDF generator ONLY for active families."""
    if not dashboard_dir.exists():
        log.error(f"Dashboard path does not exist for recording: {dashboard_dir}")
        return

    if stats_df is None or stats_df.empty:
        log.warn("No statistical baseline data was extracted. Tables will render as N/A.")
    else:
        log.info(f"Loaded statistical baseline containing {len(stats_df)} records.")

    record_dir.mkdir(parents=True, exist_ok=True)
    png_files = sorted(list(dashboard_dir.rglob("*_comparison.png")))

    recent_dates = get_recent_run_dates(png_files, max_dates=3)
    filtered_files = [f for f in png_files if extract_run_datetime(f) and extract_run_datetime(f).date() in recent_dates]

    log.info(f"Discovered {len(filtered_files)}/{len(png_files)} files from latest {len(recent_dates)} run dates. Filtering history...")

    records = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    matched_count = 0

    for f in filtered_files:
        match = FILE_PATTERN.search(f.name)
        if not match: continue

        prod, sat, var, time_str = match.group('prod'), match.group('sat'), match.group('var'), match.group('time')

        norm_prod = clean_product_name(prod)

        ch_match = re.search(r'(_C\d{2})$', norm_prod)
        ch_tag = ch_match.group(1) if ch_match else ""

        base_norm = re.sub(r'_C\d{2}$', '', norm_prod)
        fam = get_family_for_product(base_norm)
        base_family = fam if fam else base_norm

        # Explicitly force the channel tag back onto the resolved family name!
        family = f"{base_family}{ch_tag}"

        full_time_int = int(time_str)

        records[family][sat][(prod, var)].append((full_time_int, f))
        matched_count += 1

    if matched_count == 0:
        log.warn("No files matched the required PAVE comparison naming format.")
        return

    for family, sats in records.items():
        for sat, var_dict in sats.items():
            for prod_var_tuple in var_dict:
                recent_three = sorted(var_dict[prod_var_tuple], key=lambda x: x[0], reverse=True)[:3]
                var_dict[prod_var_tuple] = sorted(recent_three, key=lambda x: x[0])

    log.info(f"Successfully grouped and trimmed dashboard artifacts.")

    updated_count = 0
    for family, sats_dict in records.items():
        if family in active_families:
            build_pdf_artifact(family, sats_dict, record_dir, stats_df, log)
            updated_count += 1

    if updated_count == 0:
        log.warn("No dashboard artifacts matched the active product families. No PDFs generated.")
    else:
        log.info(f"Successfully generated/updated {updated_count} PDF records.")

def main():
    global DEBUG_STATS_MODE
    parser = argparse.ArgumentParser(description="PAVE-ARCHIVER: Unified Workspace Lifecycle Manager")
    parser.add_argument("workspaces", nargs="+", help="Paths to PAVE workspace directories to archive")

    parser.add_argument("--clean-validation", action="store_true", help="Harvest dashboard items, then archive and remove the validation/ folder")
    parser.add_argument("--clean-glance", action="store_true", help="Archive and remove the legacy glance/ folder")

    parser.add_argument("--dashboard", type=str, help="Optional: Shared path to aggregate all dashboard comparison images globally")
    parser.add_argument("--record", type=str, help="Optional: Trigger PDF generation and output artifacts to this path")
    parser.add_argument("--debug-stats", action="store_true", help="Activate deep text and tracking diagnostic logs for summary CSV parsing math")

    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("-d", "--debug", action="store_true", help="Enable debug logging and metrics tracing")
    parser.add_argument("-q", "--quiet", action="store_true", help="Restrict logging to warnings/errors")

    args = parser.parse_args()

    log_level = "DEBUG" if args.debug else "VERBOSE" if args.verbose else "QUIET" if args.quiet else "INFO"
    log = Logger(log_level)
    setup_interrupt_handler(log)

    if args.debug_stats or args.debug:
        DEBUG_STATS_MODE = True
        log.info("[DEBUG ENGINE ACTIVATED] Statistics query paths will dump to stdout.")

    master_stats_list = []
    active_families = set()

    for ws in args.workspaces:
        ws_path = Path(ws).resolve()
        fams = process_workspace(ws_path, args, log)
        active_families.update(fams)

    if not active_families:
        log.warn("No active product families identified in the workspaces. PDF records will not be updated.")
    else:
        log.info(f"Targeting PDF updates for active families: {', '.join(active_families)}")

    if args.record and args.workspaces and active_families:
        workspace_root = Path(args.workspaces[0]).resolve().parent
        log.info("Crawling master workspace root for historical statistical records (last 7 days)...")

        seven_days_ago = time_mod.time() - (7 * 86400)

        stat_files = []
        for sf in workspace_root.rglob("*stats_summary.csv"):
            try:
                if sf.stat().st_mtime > seven_days_ago:
                    stat_files.append(sf)
            except Exception:
                pass

        columns_to_keep = ['Product', 'Variable', 'Sat', 'Metric', 'Count', 'Min', 'Max', 'Mean', 'Median', 'NaN_Count']

        for sf in stat_files:
            try:
                parsed_data = []
                with open(sf, 'r') as f:
                    reader = csv.reader(f)
                    header = next(reader, None)
                    if not header: continue

                    for row in reader:
                        if len(row) >= 10:
                            cleaned_row = []
                            for idx, val in enumerate(row[:10]):
                                val_str = str(val).strip()
                                if idx in [5, 6, 7, 8]:
                                    if re.match(r'^\s*(n/?a|none|nan|null)\s*$', val_str, re.IGNORECASE):
                                        cleaned_row.append('')
                                    else:
                                        cleaned_row.append(val_str)
                                else:
                                    cleaned_row.append(val_str)
                            parsed_data.append(cleaned_row)

                if parsed_data:
                    df = pd.DataFrame(parsed_data, columns=columns_to_keep)

                    for numeric_col in ['Mean', 'Max']:
                        df[numeric_col] = pd.to_numeric(df[numeric_col], errors='coerce')

                    df['Sat'] = df['Sat'].astype(str).str.replace('G', '', case=False).str.zfill(2)

                    master_stats_list.append(df)
            except Exception as e:
                log.debug(f"Failed to safely parse ragged CSV {sf.name}: {e}")

        if master_stats_list:
            combined_stats_df = pd.concat(master_stats_list, ignore_index=True)
            if DEBUG_STATS_MODE:
                print(f"[STATS_ASSEMBLY_DEBUG] Master database generated. Unique rows: {len(combined_stats_df)}")
                print(f"[STATS_ASSEMBLY_DEBUG] Active Product tags discovered in database: {list(combined_stats_df['Product'].unique())}")
        else:
            log.warn("No historical stats files found! PDF tables will render as N/A.")
            combined_stats_df = None

        if args.dashboard:
            run_recorder(Path(args.dashboard).resolve(), Path(args.record).resolve(), combined_stats_df, active_families, log)
        else:
            log.warn("Cannot generate central diurnal records without a shared --dashboard source path.")

if __name__ == "__main__":
    main()
