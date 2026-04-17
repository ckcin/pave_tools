#!/usr/bin/env python3
"""
COMPARE-PAVE: Lightweight Science Analysis Engine
=================================================
VERSION: 1.1.6 (Enhanced Verbose Reporting)
"""

import os
import argparse
import warnings
import sys
import time
import re
import csv
import gc
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import pandas as pd
import xarray as xr
import matplotlib.pyplot as plt
from scipy.stats import pearsonr
from matplotlib.colors import ListedColormap

# Shared Infrastructure
from pave_utils import Logger, setup_interrupt_handler

# Suppress standard Matplotlib noise
warnings.filterwarnings("ignore")

# Metadata extraction regex
GOES_REGEX = re.compile(r"OR_(?P<dsn>.*?)_(?P<sat>G1[89]).*?s(?P<start>\d{14})")

def process_file_pair(p_file, g_file, dest_root, prem_root):
    """Worker: Memory-optimized comparison logic with pair-string reporting."""
    results = []

    # Build pair identifier for logging
    pair_info = f"{p_file.name} <-> {g_file.name}"

    m = GOES_REGEX.search(p_file.name)
    if not m:
        return None, f"Metadata Error: {pair_info}"

    meta = {
        'Product': m.group('dsn'),
        'Sat': m.group('sat'),
        'Start': m.group('start')
    }

    rel_dir = p_file.relative_to(prem_root).parent
    out_dir = dest_root / rel_dir / p_file.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        with xr.open_dataset(p_file) as ds_p, xr.open_dataset(g_file) as ds_g:
            variables = [v for v in ds_p.data_vars if ds_p[v].ndim >= 2]

            for var in variables:
                try:
                    da_p = ds_p[var]
                    da_g = ds_g[var]

                    # Load data into memory for stats
                    data_p = da_p.values
                    data_g = da_g.values

                    mask_p = np.isfinite(data_p)
                    mask_g = np.isfinite(data_g)
                    common = np.logical_and(mask_p, mask_g)

                    mismatch_count = np.sum(np.logical_xor(mask_p, mask_g))
                    f1_frac = mismatch_count / data_p.size if data_p.size > 0 else 0

                    r_sq = 0.0
                    if np.count_nonzero(common) > 1:
                        r_val, _ = pearsonr(data_p[common], data_g[common])
                        r_sq = r_val ** 2

                    # Diagnostic plotting logic
                    plot_p = data_p[..., 0] if data_p.ndim > 2 else data_p
                    plot_g = data_g[..., 0] if data_g.ndim > 2 else data_g
                    plot_diff = plot_p - plot_g

                    fig = plt.figure(figsize=(18, 10))
                    t_suffix = " (Layer 0)" if data_p.ndim > 2 else ""
                    plt.suptitle(f"Variable: {var}{t_suffix}\n{pair_info}", fontsize=8)

                    ax1 = fig.add_subplot(2, 3, 1)
                    im1 = ax1.imshow(plot_p, cmap='viridis')
                    ax1.set_title("On-Prem (A)")
                    plt.colorbar(im1, ax=ax1)

                    ax2 = fig.add_subplot(2, 3, 2)
                    im2 = ax2.imshow(plot_g, cmap='viridis')
                    ax2.set_title("GCCS (B)")
                    plt.colorbar(im2, ax=ax2)

                    ax3 = fig.add_subplot(2, 3, 3)
                    im3 = ax3.imshow(plot_diff, cmap='RdBu_r')
                    ax3.set_title("Difference (A-B)")
                    plt.colorbar(im3, ax=ax3)

                    ax4 = fig.add_subplot(2, 3, 4)
                    m_map = np.zeros_like(plot_p, dtype=np.int8)
                    m_map[np.isfinite(plot_p) & ~np.isfinite(plot_g)] = 1
                    m_map[~np.isfinite(plot_p) & np.isfinite(plot_g)] = 2
                    ax4.imshow(m_map, cmap=ListedColormap(['white', 'red', 'blue']))
                    ax4.set_title(f"Mismatch Map (Frac: {f1_frac:.6f})")

                    ax5 = fig.add_subplot(2, 3, 5)
                    if np.count_nonzero(common) > 0:
                        s_size = min(len(data_p[common]), 1_000_000)
                        indices = np.random.choice(len(data_p[common]), s_size, replace=False)
                        ax5.hist2d(data_p[common][indices], data_g[common][indices], bins=50, cmap='Blues', cmin=1)
                        ax5.set_xlabel("On-Prem")
                        ax5.set_ylabel("GCCS")
                    ax5.set_title(f"Density Plot (R²: {r_sq:.6f})")

                    plt.savefig(out_dir / f"{var}_comparison.png", dpi=100)
                    plt.close(fig)

                    # Explicit memory cleanup
                    del data_p, data_g, mask_p, mask_g, common, plot_p, plot_g, plot_diff
                    gc.collect()

                    results.append({**meta, 'Variable': var, 'Metric': 'r-squared correlation', 'Value': r_sq})
                    results.append({**meta, 'Variable': var, 'Metric': 'finite_in_only_one_fraction', 'Value': f1_frac})

                except MemoryError:
                    results.append({**meta, 'Variable': var, 'Metric': 'r-squared correlation', 'Value': np.nan})
                    results.append({**meta, 'Variable': var, 'Metric': 'finite_in_only_one_fraction', 'Value': np.nan})
                    continue

        return results, pair_info

    except Exception as e:
        return None, f"FAILED: {pair_info} -> {str(e)}"

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

    def _write_aggregated_csv(self, all_results):
        """Aggregates raw results into the Stats-Pave format."""
        if not self.stats_root.exists():
            self.stats_root.mkdir(parents=True, exist_ok=True)

        df = pd.DataFrame(all_results).sort_values('Start')
        header = "Product,Variable,Sat,Metric,Count,Min,Max,Mean,Median,NaN,T1,V1,T2,V2,T3,V3...\n"

        with open(self.summary_csv, 'w') as f:
            f.write(header)
            for (prod, var, metric, sat), group in df.groupby(['Product', 'Variable', 'Metric', 'Sat'], sort=False):
                vals = pd.to_numeric(group['Value'], errors='coerce').dropna()
                if vals.empty:
                    continue

                meta_fields = [
                    prod, var, sat, metric, str(len(vals)),
                    f"{vals.min():.8f}", f"{vals.max():.8f}",
                    f"{vals.mean():.8f}", f"{vals.median():.8f}",
                    str(group['Value'].isna().sum())
                ]

                ts_flat = []
                for _, row in group.iterrows():
                    ts_flat.append(str(row['Start']))
                    ts_flat.append(str(row['Value']))

                f.write(",".join(meta_fields) + "," + ",".join(ts_flat) + "\n")

    def execute(self):
        if not self.dest_root.exists():
            self.dest_root.mkdir(parents=True, exist_ok=True)

        nc_files = list(self.prem_root.rglob("*.nc"))
        tasks = []

        for p_file in nc_files:
            rel_dir = p_file.relative_to(self.prem_root).parent
            gccs_twin_dir = self.gccs_root / rel_dir
            if not gccs_twin_dir.exists():
                continue

            m_key = p_file.name.split('_c')[0] if "_c" in p_file.name else p_file.name
            matches = list(gccs_twin_dir.glob(f"{m_key}_c*.nc")) if "_c" in p_file.name else \
                      [gccs_twin_dir / p_file.name] if (gccs_twin_dir / p_file.name).exists() else []

            if matches:
                tasks.append((p_file, matches[0]))

        all_results = []
        self.log.info(f"Compare-PAVE: Orchestrating {len(tasks)} file pairs on {self.threads} cores.")

        with ProcessPoolExecutor(max_workers=self.threads) as executor:
            future_to_file = {executor.submit(process_file_pair, p, g, self.dest_root, self.prem_root): p.name for p, g in tasks}

            for future in as_completed(future_to_file):
                try:
                    res_data, pair_info = future.result()

                    if res_data:
                        # Success log now includes both filenames
                        self.log.verbose(f"  Processed: {pair_info}")
                        all_results.extend(res_data)
                    else:
                        # Error message already includes the pair_info
                        self.log.warn(f"  {pair_info}")

                except Exception as e:
                    self.log.error(f"  Critical Process Failure: {str(e)}")

        if all_results:
            self._write_aggregated_csv(all_results)
            self.log.info(f"Aggregated Comparison Complete: {self.summary_csv}")

def parse_args():
    parser = argparse.ArgumentParser(prog="compare_pave.py")
    parser.add_argument("prem_fld")
    parser.add_argument("gccs_fld")
    parser.add_argument("dest_fld")
    parser.add_argument("--stats-fld", help="Redirect summary CSV to this folder")
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
