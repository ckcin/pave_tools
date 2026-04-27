#!/usr/bin/env python3
"""
COMPARE-PAVE: Lightweight Science Analysis Engine
=================================================
VERSION: 1.4.0 (Strategy Routing + Flattened CSV Support)
"""

import os
import argparse
import warnings
import sys
import time
import re
import csv
import gc
import shutil
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import pandas as pd
import xarray as xr
import matplotlib.pyplot as plt
from scipy.spatial import cKDTree
from scipy.stats import pearsonr
from matplotlib.colors import ListedColormap

# Shared Infrastructure
from pave_utils import Logger, setup_interrupt_handler, resolve_meta

# Suppress Matplotlib overhead
warnings.filterwarnings("ignore")
plt.switch_backend('Agg')

GOES_REGEX = re.compile(r"OR_(?P<dsn>.*?)_(?P<sat>G1[89]).*?s(?P<start>\d{14})")


# --- COMPARISON STRATEGIES ---

def _compare_standard(ds_p, ds_g, var, tmp_dir, pair_info):
    """Standard 2D Array Comparison."""
    data_p = ds_p[var].values.astype(np.float32)
    data_g = ds_g[var].values.astype(np.float32)

    mask_p = np.isfinite(data_p)
    mask_g = np.isfinite(data_g)
    common = np.logical_and(mask_p, mask_g)

    mismatch_count = np.sum(np.logical_xor(mask_p, mask_g))
    f1_frac = mismatch_count / data_p.size if data_p.size > 0 else 0

    r_sq = 0.0
    num_common = np.count_nonzero(common)

    if num_common > 1:
        s_size = min(num_common, 500_000)
        flat_idx = np.flatnonzero(common)
        sample_idx = np.random.choice(flat_idx, size=s_size, replace=False)

        sample_p = data_p.ravel()[sample_idx]
        sample_g = data_g.ravel()[sample_idx]

        r_val, _ = pearsonr(sample_p, sample_g)
        r_sq = r_val ** 2
        sample_p = sample_g = None # Clear sample arrays

    # Standard Plot Slicing
    plot_p = data_p[..., 0] if data_p.ndim > 2 else data_p
    plot_g = data_g[..., 0] if data_g.ndim > 2 else data_g
    plot_diff = plot_p - plot_g

    fig = plt.figure(figsize=(18, 10))
    plt.suptitle(f"Variable: {var}\n{pair_info} (Standard)", fontsize=8)

    # (Axes 1-4 rendering remains consistent with v1.2.1)
    ax1 = fig.add_subplot(221)
    ax1.set_title("PREM")
    im1 = ax1.imshow(plot_p, cmap='viridis')
    plt.colorbar(im1, ax=ax1)

    ax2 = fig.add_subplot(222)
    ax2.set_title("GCCS")
    im2 = ax2.imshow(plot_g, cmap='viridis')
    plt.colorbar(im2, ax=ax2)

    ax3 = fig.add_subplot(223)
    ax3.set_title("Difference (GCCS - PREM)")
    vmax = np.nanmax(np.abs(plot_diff)) if not np.isnan(plot_diff).all() else 1
    im3 = ax3.imshow(plot_diff, cmap='bwr', vmin=-vmax, vmax=vmax)
    plt.colorbar(im3, ax=ax3)

    plt.savefig(tmp_dir / f"{var}_comparison.png", dpi=90)
    plt.close(fig)
    del data_p, data_g, mask_p, mask_g, common, plot_p, plot_g, plot_diff

    return [
        {'Metric': 'r-squared correlation', 'Value': r_sq},
        {'Metric': 'finite_in_only_one_fraction', 'Value': f1_frac}
    ]

def _compare_sparse(ds_p, ds_g, var, tmp_dir, pair_info):
    """Sparse Spatial Comparison using cKDTree."""
    lat_var = next((v for v in ds_p.variables if 'lat' in v.lower()), None)
    lon_var = next((v for v in ds_p.variables if 'lon' in v.lower()), None)

    if not lat_var or not lon_var:
        raise ValueError("Missing lat/lon for sparse comparison.")

    coords_p = np.column_stack((ds_p[lat_var].values, ds_p[lon_var].values))
    coords_g = np.column_stack((ds_g[lat_var].values, ds_g[lon_var].values))

    tree_p = cKDTree(coords_p)
    distances, indices = tree_p.query(coords_g, distance_upper_bound=0.01)

    valid = distances != np.inf
    mismatch_count = len(coords_g) - np.sum(valid) + len(coords_p) - np.sum(valid)
    total_points = len(coords_g) + len(coords_p)
    f1_frac = mismatch_count / total_points if total_points > 0 else 0

    r_sq = 0.0
    if np.any(valid):
        matched_p_vals = ds_p[var].values[indices[valid]]
        matched_g_vals = ds_g[var].values[valid]

        if len(matched_p_vals) > 1:
            r_val, _ = pearsonr(matched_p_vals, matched_g_vals)
            r_sq = r_val ** 2

        diff = matched_g_vals - matched_p_vals

        fig = plt.figure(figsize=(10, 8))
        plt.scatter(coords_g[valid, 1], coords_g[valid, 0], c=diff, cmap='bwr', s=5)
        plt.colorbar(label='Difference (GCCS - PREM)')
        plt.title(f"Variable: {var}\n{pair_info} (Sparse Spatial)", fontsize=8)
        plt.savefig(tmp_dir / f"{var}_comparison.png", dpi=90)
        plt.close(fig)

    return [
        {'Metric': 'r-squared correlation', 'Value': r_sq},
        {'Metric': 'finite_in_only_one_fraction', 'Value': f1_frac}
    ]

def _compare_timeseries(ds_p, ds_g, var, tmp_dir, pair_info):
    """Time-Series Comparison using pandas merge_asof."""
    time_var = next((v for v in ds_p.variables if 'time' in v.lower()), None)
    if not time_var:
        raise ValueError("Missing time variable for timeseries comparison.")

    df_p = pd.DataFrame({'time': ds_p[time_var].values, 'P_Val': ds_p[var].values}).dropna().sort_values('time')
    df_g = pd.DataFrame({'time': ds_g[time_var].values, 'G_Val': ds_g[var].values}).dropna().sort_values('time')

    # Merge on nearest timestamp allowing small misalignment
    merged = pd.merge_asof(df_g, df_p, on='time', direction='nearest', tolerance=pd.Timedelta('1s')).dropna()

    mismatch_count = len(df_g) - len(merged) + len(df_p) - len(merged)
    total_points = len(df_g) + len(df_p)
    f1_frac = mismatch_count / total_points if total_points > 0 else 0

    r_sq = 0.0
    if len(merged) > 1:
        r_val, _ = pearsonr(merged['P_Val'], merged['G_Val'])
        r_sq = r_val ** 2

        merged['diff'] = merged['G_Val'] - merged['P_Val']

        fig = plt.figure(figsize=(12, 4))
        plt.plot(merged['time'], merged['diff'], label='Diff (G-P)', color='red', linewidth=1)
        plt.axhline(0, color='black', linestyle='--')
        plt.title(f"Variable: {var}\n{pair_info} (Time-Series)", fontsize=8)
        plt.savefig(tmp_dir / f"{var}_comparison.png", dpi=90)
        plt.close(fig)

    return [
        {'Metric': 'r-squared correlation', 'Value': r_sq},
        {'Metric': 'finite_in_only_one_fraction', 'Value': f1_frac}
    ]


# --- ENGINE ORCHESTRATOR ---

def process_file_pair(p_file, g_file, dest_root, prem_root):
    """Worker: Generates plots and local raw stats using appropriate strategy."""
    results = []
    pair_info = f"{p_file.name} <-> {g_file.name}"

    m = GOES_REGEX.search(p_file.name)
    if not m:
        return None, f"Metadata Error: {pair_info}"

    prod_name = m.group('dsn')
    meta = {
        'Product': prod_name,
        'Sat': m.group('sat'),
        'Start': m.group('start')
    }

    global_meta = resolve_meta(prod_name)
    comp_strategy = global_meta.get('comp_type', 'image').lower()

    rel_dir = p_file.relative_to(prem_root).parent
    final_dir = dest_root / rel_dir / p_file.stem
    tmp_dir = dest_root / rel_dir / f"{p_file.stem}.partial"

    if final_dir.exists():
        return [], pair_info

    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    try:
        with xr.open_dataset(p_file, cache=False) as ds_p, \
             xr.open_dataset(g_file, cache=False) as ds_g:

            # Variable selection logic based on strategy
            if comp_strategy == 'sparse':
                lat_v = next((v for v in ds_p.variables if 'lat' in v.lower()), None)
                lon_v = next((v for v in ds_p.variables if 'lon' in v.lower()), None)
                variables = [v for v in ds_p.data_vars if ds_p[v].ndim == 1 and v not in [lat_v, lon_v]]
            elif comp_strategy == 'timeseries':
                time_v = next((v for v in ds_p.variables if 'time' in v.lower()), None)
                variables = [v for v in ds_p.data_vars if ds_p[v].ndim == 1 and v != time_v]
            else: # Standard Image
                variables = [v for v in ds_p.data_vars if ds_p[v].ndim >= 2]

            for var in variables:
                try:
                    # Route to correct comparison logic
                    if comp_strategy == 'sparse':
                        var_metrics = _compare_sparse(ds_p, ds_g, var, tmp_dir, pair_info)
                    elif comp_strategy == 'timeseries':
                        var_metrics = _compare_timeseries(ds_p, ds_g, var, tmp_dir, pair_info)
                    else:
                        var_metrics = _compare_standard(ds_p, ds_g, var, tmp_dir, pair_info)

                    # Ensure format perfectly matches stats_pave.py expectations
                    for m_dict in var_metrics:
                        results.append({
                            **meta, 'Variable': var,
                            'Metric': m_dict['Metric'], 'Value': m_dict['Value']
                        })

                except MemoryError:
                    results.append({**meta, 'Variable': var, 'Metric': 'r-squared correlation', 'Value': np.nan})
                except Exception as ve:
                    # Skip problematic variables but continue evaluating others
                    continue
                finally:
                    gc.collect()

        # Local stats record EXACTLY as written in v1.2.2
        with open(tmp_dir / "stats.csv", 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['Product', 'Variable', 'Sat', 'Metric', 'Start', 'Value'])
            writer.writeheader()
            writer.writerows(results)

        tmp_dir.rename(final_dir)
        return results, pair_info

    except Exception as e:
        if tmp_dir.exists(): shutil.rmtree(tmp_dir)
        return None, f"FAILED: {pair_info} -> {str(e)}"
    finally:
        plt.close('all')

class PaveComparator:
    def __init__(self, args, log):
        self.prem_root = Path(args.prem_fld).resolve()
        self.gccs_root = Path(args.gccs_fld).resolve()
        self.dest_root = Path(args.dest_fld).resolve()

        stats_val = getattr(args, 'stats_fld', None)
        self.stats_root = Path(stats_val if stats_val else args.dest_fld).resolve()

        self.threads = getattr(args, 'threads', 4)
        self.log = log
        self.summary_csv = self.stats_root / "glance_stats_summary.csv"

    def _cleanup_partial_runs(self):
        """Removes orphaned .partial folders from previous crashes."""
        for p_dir in self.dest_root.rglob("*.partial"):
            if p_dir.is_dir(): shutil.rmtree(p_dir)

    def _write_aggregated_summary(self):
        """
        Reformatting Logic:
        Scrapes local stats, calculates overall stats, and flattens time-series.
        (Remains identical to v1.2.2 to ensure matching format with stats_pave.py)
        """
        all_raw_data = []
        self.log.info("Collecting and reformatting cumulative statistics...")

        # 1. Scrape all local records
        for local_csv in self.dest_root.rglob("stats.csv"):
            try:
                with open(local_csv, 'r') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        all_raw_data.append(row)
            except Exception: continue

        if not all_raw_data:
            self.log.warn("No statistical records found to aggregate.")
            return

        # 2. Reformat into Time-Series mirrored format
        df = pd.DataFrame(all_raw_data).sort_values('Start')
        df['Value'] = pd.to_numeric(df['Value'], errors='coerce')

        header = "Product,Variable,Sat,Metric,Count,Min,Max,Mean,Median,NaN,T1,V1,T2,V2,T3,V3...\n"

        if not self.stats_root.exists():
            self.stats_root.mkdir(parents=True, exist_ok=True)

        with open(self.summary_csv, 'w') as f:
            f.write(header)

            # Mirroring statistics gathering logic
            groups = df.groupby(['Product', 'Variable', 'Metric', 'Sat'], sort=False)
            for (prod, var, metric, sat), group in groups:
                vals = group['Value'].dropna()

                # Metadata columns
                meta = [
                    prod, var, sat, metric,
                    str(len(group)),
                    f"{vals.min():.8f}" if not vals.empty else "NaN",
                    f"{vals.max():.8f}" if not vals.empty else "NaN",
                    f"{vals.mean():.8f}" if not vals.empty else "NaN",
                    f"{vals.median():.8f}" if not vals.empty else "NaN",
                    str(group['Value'].isna().sum())
                ]

                # Flattened Time-Series
                ts_pairs = []
                for _, row in group.iterrows():
                    ts_pairs.append(str(row['Start']))
                    ts_pairs.append(str(row['Value']))

                f.write(",".join(meta) + "," + ",".join(ts_pairs) + "\n")

    def execute(self):
        if not self.dest_root.exists():
            self.dest_root.mkdir(parents=True, exist_ok=True)

        self._cleanup_partial_runs()

        nc_files = list(self.prem_root.rglob("*.nc"))
        tasks = []
        skipped = 0

        for pf in nc_files:
            rel = pf.relative_to(self.prem_root).parent
            if (self.dest_root / rel / pf.stem).exists():
                skipped += 1; continue

            g_dir = self.gccs_root / rel
            if not g_dir.exists(): continue
            m_key = pf.name.split('_c')[0] if "_c" in pf.name else pf.name
            matches = list(g_dir.glob(f"{m_key}_c*.nc")) if "_c" in pf.name else \
                      [g_dir / pf.name] if (g_dir / pf.name).exists() else []
            if matches: tasks.append((pf, matches[0]))

        if skipped > 0:
            self.log.info(f"Compare-PAVE: Resuming. Skipping {skipped} existing results.")

        if tasks:
            self.log.info(f"Compare-PAVE: Processing {len(tasks)} file pairs.")
            with ProcessPoolExecutor(max_workers=self.threads) as executor:
                futures = {executor.submit(process_file_pair, p, g, self.dest_root, self.prem_root): p.name for p, g in tasks}
                for fut in as_completed(futures):
                    try: res, info = fut.result()
                    except Exception as e: self.log.error(f"Critical Worker Error: {e}")

        # Final Reformatting Step
        self._write_aggregated_summary()
        self.log.info(f"Final Summary Rebuilt: {self.summary_csv}")

def parse_args():
    parser = argparse.ArgumentParser(prog="compare_pave.py")
    parser.add_argument("prem_fld")
    parser.add_argument("gccs_fld")
    parser.add_argument("dest_fld")
    parser.add_argument("--stats-fld", help="CSV destination")
    parser.add_argument("-j", "--threads", type=int, default=4)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-d", "--debug", action="store_true")
    return parser.parse_args()

def main():
    args = parse_args()
    lvl = "DEBUG" if args.debug else "VERBOSE" if args.verbose else "INFO"
    log = Logger(lvl)
    setup_interrupt_handler(log)
    PaveComparator(args, log).execute()

if __name__ == "__main__":
    main()
