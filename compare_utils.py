#!/usr/bin/env python3
"""
COMPARE-PAVE: Shared Utility Suite
==================================
VERSION: 1.7.6 (Corner Labels + Restored 2x2 Dashboard)
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
    match = re.search(r"(_s\d{14,}.*?)(?:\.nc|$)", filename)
    return match.group(1) if match else filename

def get_identity_start_key(filename):
    match = re.search(r"^(.*?_s\d{14})", filename)
    return match.group(1) if match else filename

# --- GEOSPATIAL & VISUAL UTILS ---

def get_suvi_cmap(prod_name):
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
    """The master 2x2 grid engine with updated corner labels and restored dashboard."""
    mask_p, mask_g = np.isfinite(data_p), np.isfinite(data_g)
    common = np.logical_and(mask_p, mask_g)
    
    r_sq = 0.0
    num_common = np.count_nonzero(common)
    if num_common > 1:
        s_size = min(num_common, 500_000)
        flat_idx = np.flatnonzero(common)
        sample_idx = np.random.choice(flat_idx, size=s_size, replace=False)
        try:
            samp_p, samp_g = data_p.ravel()[sample_idx], data_g.ravel()[sample_idx]
            if np.all(samp_p == samp_p[0]) or np.all(samp_g == samp_g[0]):
                r_sq = 1.0 if np.array_equal(samp_p, samp_g) else 0.0
            else:
                r_sq = float(pearsonr(samp_p, samp_g)[0] ** 2)
        except: r_sq = 0.0

    # Extract Metadata for labeling
    m = GOES_REGEX.search(pair_info)
    product_dsn = m.group('dsn') if m else "Unknown"
    start_time = f"s{m.group('start')}" if m else ""

    plot_p = data_p[..., 0] if data_p.ndim > 2 else data_p
    plot_g = data_g[..., 0] if data_g.ndim > 2 else data_g
    plot_diff = plot_g - plot_p
    valid_diffs = plot_diff[np.isfinite(plot_diff)]

    h, w = plot_p.shape
    data_ratio = h / w if w > 0 else 1
    is_geo = HAS_CARTOPY and proj is not None
    
    # Scaling
    kwargs = {'cmap': cmap, 'aspect': 'equal', 'origin': origin}
    v_p, v_g = plot_p[np.isfinite(plot_p)], plot_g[np.isfinite(plot_g)]
    if len(v_p) > 0 and len(v_g) > 0:
        kwargs['vmin'] = min(np.nanpercentile(v_p, 1), np.nanpercentile(v_g, 1))
        kwargs['vmax'] = max(np.nanpercentile(v_p, 99), np.nanpercentile(v_g, 99))

    diff_vmax = np.nanmax(np.abs(valid_diffs)) if len(valid_diffs) > 0 else 1
    diff_kwargs = {'cmap': 'bwr', 'aspect': 'equal', 'origin': origin, 'vmin': -diff_vmax, 'vmax': diff_vmax}
    
    if extent: kwargs['extent'] = diff_kwargs['extent'] = extent
    if is_geo: kwargs['transform'] = diff_kwargs['transform'] = proj

    halo = [path_effects.withStroke(linewidth=2, foreground='white')]

    def _setup_geo_ax(ax):
        if is_geo:
            try:
                ax.add_feature(cfeature.COASTLINE, color='lightgrey', linewidth=0.6, facecolor='none')
                ax.add_feature(cfeature.STATES, edgecolor='lightgrey', linewidth=0.4, linestyle=':', facecolor='none')
            except: pass

    def _add_corner_labels(ax, source_label):
        ax.text(0.01, 0.99, source_label, transform=ax.transAxes, color='black', weight='bold', va='top', ha='left', path_effects=halo)
        ax.text(0.01, 0.01, start_time, transform=ax.transAxes, color='black', fontsize=9, va='bottom', ha='left', path_effects=halo)

    def _add_cbar(im, ax):
        if is_geo: return plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
        div = make_axes_locatable(ax)
        return plt.colorbar(im, cax=div.append_axes("right", size="5%", pad=0.05))

    # --- 1. STANDALONE EXPORTS ---
    ind_h = 10 * data_ratio
    for label, data, kw, suff in [('GCCS', plot_g, kwargs, 'GCCS'), ('On-Prem', plot_p, kwargs, 'PREM'), ('Diff (G-P)', plot_diff, diff_kwargs, 'DIFF')]:
        fig_i = plt.figure(figsize=(10, ind_h))
        ax_i = fig_i.add_subplot(111, projection=proj) if is_geo else fig_i.add_subplot(111)
        ax_i.set_title(f"{product_dsn} | {var}", fontsize=12, color='black', weight='bold', pad=12)
        _setup_geo_ax(ax_i)
        im_i = ax_i.imshow(data, **kw)
        _add_corner_labels(ax_i, label)
        _add_cbar(im_i, ax_i)
        fig_i.savefig(tmp_dir / f"{var}_{suff}.png", dpi=100, bbox_inches='tight')
        plt.close(fig_i)

    # --- 2. 2x2 DASHBOARD ---
    fig_w = 18.0
    w_ax = (fig_w - 2.0) / 2.2
    fig_h_comb = max(min(2 * (w_ax * data_ratio) + 3.0, 40), 8)
    fig = plt.figure(figsize=(fig_w, fig_h_comb))
    plt.suptitle(f"{product_dsn} | {var}\n{pair_info}", fontsize=12, weight='bold', y=0.98)

    ax1 = fig.add_subplot(221, projection=proj if is_geo else None)
    ax2 = fig.add_subplot(222, projection=proj if is_geo else None)
    ax3 = fig.add_subplot(223, projection=proj if is_geo else None)
    ax4 = fig.add_subplot(224)
    
    dash_map = [(ax1, plot_p, kwargs, "On-Prem (Operational)"), 
                (ax2, plot_g, kwargs, "GCCS (Cloud)"), 
                (ax3, plot_diff, diff_kwargs, "Difference (G-P)")]

    for ax, data, kw, tit in dash_map:
        ax.set_title(tit, color='black', fontsize=10)
        _setup_geo_ax(ax)
        im = ax.imshow(data, **kw)
        _add_cbar(im, ax)

    ax4.set_title("Histogram", color='black', fontsize=10)
    if len(valid_diffs) > 0:
        ax4.hist(valid_diffs, bins=50, color='dimgray', edgecolor='black')
        ax4.axvline(0, color='red', linestyle='--')
    
    if is_geo:
        sm = plt.cm.ScalarMappable(cmap='viridis'); sm.set_array([])
        plt.colorbar(sm, ax=ax4, fraction=0.046, pad=0.04).ax.set_visible(False)
    else:
        make_axes_locatable(ax4).append_axes("right", size="5%", pad=0.05).axis('off')

    plt.tight_layout(rect=[0, 0.03, 1, 0.95])
    plt.savefig(tmp_dir / f"{var}_comparison.png", dpi=90)
    plt.close(fig)
    
    return [{'Metric': 'r-squared correlation', 'Value': r_sq}]

# --- AGGREGATION ENGINE ---
def write_aggregated_summary(dest_root, stats_root, log):
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
