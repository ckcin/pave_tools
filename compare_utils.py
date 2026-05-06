#!/usr/bin/env python3
"""
COMPARE-PAVE: Shared Utility Suite
==================================
VERSION: 1.8.8 (Robust Viridis Mapping for Zero Deltas)
"""

import os
import re
import csv
import warnings
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patheffects as path_effects
from scipy.stats import pearsonr
from mpl_toolkits.axes_grid1 import make_axes_locatable

# Standard setup
warnings.filterwarnings("ignore", category=UserWarning, module="cartopy.mpl.feature_artist")
plt.switch_backend('Agg')

try:
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
    HAS_CARTOPY = True
except ImportError:
    HAS_CARTOPY = False

GOES_REGEX = re.compile(r"OR_(?P<dsn>.*?)_(?P<sat>G1[89]).*?s(?P<start>\d{14})")

# [get_start_end_key, get_identity_start_key, get_suvi_cmap, get_coords_for_var remain identical to v1.8.7]

def execute_visual_comparison(data_p, data_g, var, tmp_dir, pair_info, strategy_label, proj=None, extent=None, origin='upper', cmap='viridis'):
    """The master 6-plot engine with robust Viridis mapping for zero-delta values."""
    mask_p, mask_g = np.isfinite(data_p), np.isfinite(data_g)
    common = np.logical_and(mask_p, mask_g)

    # 1. Calculations
    diff_array = data_g - data_p
    valid_diffs = diff_array[np.isfinite(diff_array)]

    # Mismatch plot: 0=White (Match), 1=Green (Error)
    mismatch_mask = np.where(np.abs(diff_array) > 1e-6, 1, 0)
    mismatch_cmap = plt.matplotlib.colors.ListedColormap(['white', 'lime'])

    r_sq = 0.0
    num_common = np.count_nonzero(common)
    if num_common > 1:
        s_size = min(num_common, 500_000)
        flat_idx = np.flatnonzero(common)
        sample_idx = np.random.choice(flat_idx, size=s_size, replace=False)
        try:
            samp_p, samp_g = data_p.ravel()[sample_idx], data_g.ravel()[sample_idx]
            r_sq = float(pearsonr(samp_p, samp_g)[0] ** 2)
        except: r_sq = 0.0

    m = GOES_REGEX.search(pair_info)
    product_dsn = m.group('dsn') if m else "Unknown"
    start_time = f"s{m.group('start')}" if m else ""

    h, w = data_p.shape
    data_ratio = h / w if w > 0 else 1
    is_geo = HAS_CARTOPY and proj is not None

    # Setup Standard Product Kwargs
    kwargs = {'cmap': cmap, 'aspect': 'equal', 'origin': origin}
    v_p, v_g = data_p[np.isfinite(data_p)], data_g[np.isfinite(data_g)]
    if len(v_p) > 0 and len(v_g) > 0:
        kwargs['vmin'] = min(np.nanpercentile(v_p, 1), np.nanpercentile(v_g, 1))
        kwargs['vmax'] = max(np.nanpercentile(v_p, 99), np.nanpercentile(v_g, 99))

    # --- Robust Difference Kwargs Fix ---
    if len(valid_diffs) > 0:
        d_min, d_max = np.nanmin(valid_diffs), np.nanmax(valid_diffs)
        # If products are identical, expand range slightly so 0 gets a color
        if d_min == d_max:
            d_min, d_max = d_min - 0.1, d_max + 0.1
    else:
        d_min, d_max = -1, 1

    diff_kwargs = {'cmap': 'viridis', 'aspect': 'equal', 'origin': origin, 'vmin': d_min, 'vmax': d_max}
    mismatch_kwargs = {'cmap': mismatch_cmap, 'origin': origin, 'vmin': 0, 'vmax': 1, 'aspect': 'equal'}

    if extent: kwargs['extent'] = diff_kwargs['extent'] = mismatch_kwargs['extent'] = extent
    if is_geo: kwargs['transform'] = diff_kwargs['transform'] = mismatch_kwargs['transform'] = proj

    halo = [path_effects.withStroke(linewidth=2, foreground='white')]

    def _setup_geo_ax(ax):
        if is_geo:
            ax.add_feature(cfeature.COASTLINE, color='black', linewidth=0.7, zorder=10)
            ax.add_feature(cfeature.STATES, edgecolor='black', linewidth=0.4, linestyle=':', zorder=10)

    def _add_corner_labels(ax, label, upscale=False):
        size = 18 if upscale else 11
        ax.text(0.01, 0.99, label, transform=ax.transAxes, color='black', weight='bold',
                fontsize=size, va='top', path_effects=halo, zorder=20)

    def _add_cbar(im, ax):
        if is_geo: return plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
        div = make_axes_locatable(ax)
        return plt.colorbar(im, cax=div.append_axes("right", size="5%", pad=0.05))

    # --- 1. STANDALONE EXPORTS (6 Total) ---
    spatial_exports = [('PREM', data_p, kwargs), ('GCCS', data_g, kwargs),
                       ('DIFF', diff_array, diff_kwargs), ('MISMATCH', mismatch_mask, mismatch_kwargs)]

    for suff, d, kw in spatial_exports:
        fig_i = plt.figure(figsize=(10, 10 * data_ratio))
        ax_i = fig_i.add_subplot(111, projection=proj if is_geo else None)
        if is_geo: _setup_geo_ax(ax_i)
        im_i = ax_i.imshow(d, **kw)
        _add_corner_labels(ax_i, suff, upscale=True)
        if suff != 'MISMATCH': _add_cbar(im_i, ax_i)
        fig_i.savefig(tmp_dir / f"{var}_{suff}.png", dpi=100, bbox_inches='tight')
        plt.close(fig_i)

    # Standalone Density Scatter (Viridis, Log Scale)
    fig_scat = plt.figure(figsize=(10, 8))
    ax_scat = fig_scat.add_subplot(111)
    if len(v_p) > 0:
        im_scat = ax_scat.hexbin(data_p[common], data_g[common], gridsize=60, cmap='viridis', mincnt=1, bins='log')
        plt.colorbar(im_scat, ax=ax_scat, label='log10(count)')
    ax_scat.set_title(f"{var} Correlation ($R^2$: {r_sq:.4f})", weight='bold')
    _add_corner_labels(ax_scat, "SCATTER", upscale=True)
    fig_scat.savefig(tmp_dir / f"{var}_SCATTER.png", dpi=100, bbox_inches='tight')
    plt.close(fig_scat)

    # Standalone Histogram
    fig_hist = plt.figure(figsize=(10, 6))
    ax_hist = fig_hist.add_subplot(111)
    if len(valid_diffs) > 0:
        ax_hist.hist(valid_diffs, bins=100, color='gray', edgecolor='black', log=True)
        ax_hist.axvline(0, color='red', linestyle='--')
    ax_hist.set_title(f"{var} Delta Distribution", weight='bold')
    _add_corner_labels(ax_hist, "HIST", upscale=True)
    fig_hist.savefig(tmp_dir / f"{var}_HIST.png", dpi=100, bbox_inches='tight')
    plt.close(fig_hist)

    # --- 2. 3x2 DASHBOARD ---
    fig = plt.figure(figsize=(18, 24))
    plt.suptitle(f"{product_dsn} | {var}\n{pair_info}", fontsize=14, weight='bold', y=0.98)

    ax1 = fig.add_subplot(321, projection=proj if is_geo else None) # On-Prem
    ax2 = fig.add_subplot(322, projection=proj if is_geo else None) # GCCS
    ax3 = fig.add_subplot(323, projection=proj if is_geo else None) # Mismatch
    ax4 = fig.add_subplot(324)                                     # Density
    ax5 = fig.add_subplot(325, projection=proj if is_geo else None) # Difference (Viridis)
    ax6 = fig.add_subplot(326)                                     # Histogram

    dash_map = [(ax1, data_p, kwargs, "On-Prem"),
                (ax2, data_g, kwargs, "GCCS"),
                (ax3, mismatch_mask, mismatch_kwargs, "Mismatch (Green=Error)"),
                (ax5, diff_array, diff_kwargs, "Difference (GCCS - PREM)")]

    for ax, data, kw, tit in dash_map:
        ax.set_title(tit, weight='bold')
        if is_geo: _setup_geo_ax(ax)
        im = ax.imshow(data, **kw)
        if "Mismatch" not in tit: _add_cbar(im, ax)

    # Viridis Density Scatter (Log Scale)
    if len(v_p) > 0:
        im4 = ax4.hexbin(data_p[common], data_g[common], gridsize=60, cmap='viridis', mincnt=1, bins='log')
        ax4.set_title(f"Correlation ($R^2$: {r_sq:.4f})", weight='bold')
        ax4.set_xlabel("On-Prem"); ax4.set_ylabel("GCCS")
        plt.colorbar(im4, ax=ax4, label='log10(count)')

    # Histogram (Log Scale)
    if len(valid_diffs) > 0:
        ax6.hist(valid_diffs, bins=100, color='gray', edgecolor='black', log=True)
        ax6.axvline(0, color='red', linestyle='--')
        ax6.set_title("Distribution of Delta", weight='bold')
        ax6.set_xlabel("Delta Value"); ax6.set_ylabel("Freq (Log)")

    plt.tight_layout(rect=[0, 0.03, 1, 0.95])
    plt.savefig(tmp_dir / f"{var}_comparison.png", dpi=100)
    plt.close(fig)

    return [{'Metric': 'r-squared correlation', 'Value': r_sq}]

def write_aggregated_summary(dest_root, stats_root, log):
    """Compiles individual stats.csv files into master summary"""
    all_raw = []
    for l_csv in dest_root.rglob("stats.csv"):
        try:
            with open(l_csv, 'r') as f:
                for row in csv.DictReader(f): all_raw.append(row)
        except: continue
    if not all_raw: return
    df = pd.DataFrame(all_raw).sort_values('Start')
    df['Value'] = pd.to_numeric(df['Value'], errors='coerce')
    summary_file = stats_root / "glance_stats_summary.csv"
    with open(summary_file, 'w') as f:
        f.write("Product,Variable,Sat,Metric,Count,Min,Max,Mean,Median,NaN,T1,V1,T2,V2...\n")
        for (p, v, m, s), g in df.groupby(['Product', 'Variable', 'Metric', 'Sat'], sort=False):
            vals = g['Value'].dropna()
            line = [p, v, s, m, len(g), vals.min() if not vals.empty else 0, vals.max() if not vals.empty else 0,
                    vals.mean() if not vals.empty else 0, vals.median() if not vals.median() else 0, g['Value'].isna().sum()]
            ts = []
            for _, r in g.iterrows(): ts.extend([r['Start'], r['Value']])
            f.write(",".join(map(str, line)) + "," + ",".join(map(str, ts)) + "\n")
