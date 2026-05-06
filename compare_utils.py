#!/usr/bin/env python3
"""
COMPARE-PAVE: Shared Utility Suite
==================================
VERSION: 1.8.9 (Waterproof Plotting & 6-Plot Suite)
"""

import os
import re
import csv
import gc
import warnings
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patheffects as path_effects
from scipy.stats import pearsonr
from mpl_toolkits.axes_grid1 import make_axes_locatable

# 1. Suppress specific Cartopy facecolor warnings
warnings.filterwarnings("ignore", category=UserWarning, module="cartopy.mpl.feature_artist")

# 2. Force Headless Backend
plt.switch_backend('Agg')

try:
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
    HAS_CARTOPY = True
except ImportError:
    HAS_CARTOPY = False

try:
    import sunpy.visualization.colormaps as cm
    HAS_SUNPY = True
except ImportError:
    HAS_SUNPY = False

GOES_REGEX = re.compile(r"OR_(?P<dsn>.*?)_(?P<sat>G1[89]).*?s(?P<start>\d{14})")

# --- MATCHING & IDENTITY KEYS ---

def get_start_end_key(filename):
    """Extracts start/end timestamps for pairing files."""
    match = re.search(r"(_s\d{14,}.*?)(?:\.nc|$)", filename)
    return match.group(1) if match else filename

def get_identity_start_key(filename):
    """Extracts product identity and start time for soft matching."""
    match = re.search(r"^(.*?_s\d{14})", filename)
    return match.group(1) if match else filename

# --- GEOSPATIAL & VISUAL UTILS ---

def get_suvi_cmap(prod_name):
    """Maps SUVI channels to specific solar physics colormaps."""
    name = prod_name.lower()
    if HAS_SUNPY:
        if 'fe093' in name: return 'sdoaia94'
        if 'fe131' in name: return 'sdoaia131'
        if 'fe171' in name: return 'sdoaia171'
        if 'fe195' in name: return 'sdoaia193'
        if 'fe284' in name: return 'sdoaia211'
        if 'he303' in name: return 'sdoaia304'
    if 'fe093' in name: return 'Greens_r'
    if 'fe131' in name: return 'GnBu_r'
    if 'fe171' in name: return 'afmhot'
    if 'fe195' in name: return 'copper'
    if 'fe284' in name: return 'bone'
    if 'he303' in name: return 'Reds_r'
    return 'hot'

def get_coords_for_var(ds, var_name):
    """Identifies lat/lon coordinate variables for non-projected data."""
    prefix = var_name.split('_')[0] + '_' if '_' in var_name else ''
    if f"{prefix}lat" in ds.variables and f"{prefix}lon" in ds.variables:
        return f"{prefix}lat", f"{prefix}lon"
    if "lat" in ds.variables and "lon" in ds.variables:
        return "lat", "lon"
    lat_v = next((v for v in ds.variables if 'lat' in v.lower()), None)
    lon_v = next((v for v in ds.variables if 'lon' in v.lower()), None)
    return lat_v, lon_v

# --- CORE PLOTTING ENGINE ---

def execute_visual_comparison(data_p, data_g, var, tmp_dir, pair_info, strategy_label, proj=None, extent=None, origin='upper', cmap='viridis'):
    """Memory-safe 6-plot engine with log-density and viridis-difference mapping."""
    mask_p, mask_g = np.isfinite(data_p), np.isfinite(data_g)
    common = np.logical_and(mask_p, mask_g)

    # 1. Core Calculations
    diff_array = data_g - data_p
    valid_diffs = diff_array[np.isfinite(diff_array)]

    # Mismatch Logic: 0=White (Match), 1=Green (Error)
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

    # Setup Product Kwargs
    kwargs = {'cmap': cmap, 'aspect': 'equal', 'origin': origin}
    v_p, v_g = data_p[np.isfinite(data_p)], data_g[np.isfinite(data_g)]
    if len(v_p) > 0 and len(v_g) > 0:
        kwargs['vmin'] = min(np.nanpercentile(v_p, 1), np.nanpercentile(v_g, 1))
        kwargs['vmax'] = max(np.nanpercentile(v_p, 99), np.nanpercentile(v_g, 99))

    # Setup Difference Kwargs (Viridis, Linear, with Identity Fix)
    if len(valid_diffs) > 0:
        d_min, d_max = np.nanmin(valid_diffs), np.nanmax(valid_diffs)
        if d_min == d_max: d_min, d_max = d_min - 0.1, d_max + 0.1
    else: d_min, d_max = -1, 1

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
        size = 18 if upscale else 11 # Upscaled for standalone exports
        ax.text(0.01, 0.99, label, transform=ax.transAxes, color='black', weight='bold',
                fontsize=size, va='top', path_effects=halo, zorder=20)

    def _add_cbar(im, ax, label=None):
        if is_geo: return plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04, label=label)
        div = make_axes_locatable(ax)
        return plt.colorbar(im, cax=div.append_axes("right", size="5%", pad=0.05), label=label)

    try:
        # --- 1. STANDALONE EXPORTS (6 Files) ---
        spatial_exports = [('PREM', data_p, kwargs), ('GCCS', data_g, kwargs),
                           ('DIFF', diff_array, diff_kwargs), ('MISMATCH', mismatch_mask, mismatch_kwargs)]

        for suff, d, kw in spatial_exports:
            fig_i = plt.figure(figsize=(10, 10 * data_ratio))
            try:
                ax_i = fig_i.add_subplot(111, projection=proj if is_geo else None)
                if is_geo: _setup_geo_ax(ax_i)
                im_i = ax_i.imshow(d, **kw)
                _add_corner_labels(ax_i, suff, upscale=True)
                if suff != 'MISMATCH': _add_cbar(im_i, ax_i)
                fig_i.savefig(tmp_dir / f"{var}_{suff}.png", dpi=100, bbox_inches='tight')
            finally:
                plt.close(fig_i)

        # Standalone Density Scatter (Viridis, Log)
        fig_scat = plt.figure(figsize=(10, 8))
        try:
            ax_scat = fig_scat.add_subplot(111)
            if len(v_p) > 0:
                im_scat = ax_scat.hexbin(data_p[common], data_g[common], gridsize=60, cmap='viridis', mincnt=1, bins='log')
                _add_cbar(im_scat, ax_scat, label='log10(count)')
            ax_scat.set_title(f"{var} Correlation ($R^2$: {r_sq:.4f})", weight='bold')
            _add_corner_labels(ax_scat, "SCATTER", upscale=True)
            fig_scat.savefig(tmp_dir / f"{var}_SCATTER.png", dpi=100, bbox_inches='tight')
        finally:
            plt.close(fig_scat)

        # Standalone Histogram
        fig_hist = plt.figure(figsize=(10, 6))
        try:
            ax_hist = fig_hist.add_subplot(111)
            if len(valid_diffs) > 0:
                ax_hist.hist(valid_diffs, bins=100, color='gray', edgecolor='black', log=True)
                ax_hist.axvline(0, color='red', linestyle='--')
            ax_hist.set_title(f"{var} Delta Distribution", weight='bold')
            _add_corner_labels(ax_hist, "HIST", upscale=True)
            fig_hist.savefig(tmp_dir / f"{var}_HIST.png", dpi=100, bbox_inches='tight')
        finally:
            plt.close(fig_hist)

        # --- 2. 3x2 DASHBOARD ---
        fig = plt.figure(figsize=(18, 24))
        try:
            plt.suptitle(f"{product_dsn} | {var}\n{pair_info}", fontsize=14, weight='bold', y=0.98)
            ax1 = fig.add_subplot(321, projection=proj if is_geo else None) # On-Prem
            ax2 = fig.add_subplot(322, projection=proj if is_geo else None) # GCCS
            ax3 = fig.add_subplot(323, projection=proj if is_geo else None) # Mismatch
            ax4 = fig.add_subplot(324)                                     # Density
            ax5 = fig.add_subplot(325, projection=proj if is_geo else None) # Difference
            ax6 = fig.add_subplot(326)                                     # Histogram

            dash_map = [(ax1, data_p, kwargs, "On-Prem"), (ax2, data_g, kwargs, "GCCS"),
                        (ax3, mismatch_mask, mismatch_kwargs, "Mismatch (Green=Error)"),
                        (ax5, diff_array, diff_kwargs, "Difference (GCCS - PREM)")]

            for ax, data, kw, tit in dash_map:
                ax.set_title(tit, weight='bold')
                if is_geo: _setup_geo_ax(ax)
                im = ax.imshow(data, **kw)
                if "Mismatch" not in tit: _add_cbar(im, ax)

            if len(v_p) > 0:
                im4 = ax4.hexbin(data_p[common], data_g[common], gridsize=60, cmap='viridis', mincnt=1, bins='log')
                ax4.set_title(f"Correlation ($R^2$: {r_sq:.4f})", weight='bold')
                ax4.set_xlabel("On-Prem"); ax4.set_ylabel("GCCS")
                _add_cbar(im4, ax4, label='log10(count)')

            if len(valid_diffs) > 0:
                ax6.hist(valid_diffs, bins=100, color='gray', edgecolor='black', log=True)
                ax6.axvline(0, color='red', linestyle='--')
                ax6.set_title("Distribution of Delta", weight='bold')
                ax6.set_xlabel("Delta Value"); ax6.set_ylabel("Freq (Log)")

            plt.tight_layout(rect=[0, 0.03, 1, 0.95])
            plt.savefig(tmp_dir / f"{var}_comparison.png", dpi=100)
        finally:
            plt.close(fig)

    except Exception as e:
        plt.close('all')
        raise e

    return [{'Metric': 'r-squared correlation', 'Value': r_sq}]

# --- AGGREGATION ENGINE ---

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
                    vals.mean() if not vals.empty else 0, vals.median() if not vals.empty else 0, g['Value'].isna().sum()]
            ts = []
            for _, r in g.iterrows(): ts.extend([r['Start'], r['Value']])
            f.write(",".join(map(str, line)) + "," + ",".join(map(str, ts)) + "\n")
