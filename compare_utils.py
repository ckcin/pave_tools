#!/usr/bin/env python3
"""
COMPARE-PAVE: Shared Utility Suite
==================================
VERSION: 1.37.0 (Restored Structure + High-Performance Fast Mode Integration)
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
from matplotlib.gridspec import GridSpec
from scipy.stats import pearsonr, binned_statistic_2d
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

def grid_sparse_component(lon, lat, val, lon_edges, lat_edges, is_bitset=False):
    if is_bitset:
        grid, _, _, _ = binned_statistic_2d(lon, lat, val, statistic='max', bins=[lon_edges, lat_edges])
        return grid.T
    else:
        cnt, _, _ = np.histogram2d(lon, lat, bins=[lon_edges, lat_edges])
        sm, _, _ = np.histogram2d(lon, lat, bins=[lon_edges, lat_edges], weights=val)
        with np.errstate(divide='ignore', invalid='ignore'):
            return np.where(cnt > 0, sm / cnt, np.nan).T

# --- CORE PLOTTING ENGINES ---

def execute_visual_comparison(data_p, data_g, var, tmp_dir, pair_info, strategy_label, proj=None, extent=None, origin='upper', cmap='viridis', is_bitset=False, fast_mode=False):
    """Memory-safe plotting engine with smart bitset collapse, colormap quantization, and fast striding."""

    data_p = np.squeeze(data_p)
    data_g = np.squeeze(data_g)

    # FEATURE FIX: Smart Multi-Dimensional Collapse for Bitsets
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        while data_p.ndim > 2:
            if data_p.shape[0] < data_p.shape[-1]:
                data_p = np.nanmax(data_p, axis=0) if is_bitset else data_p[0, ...]
            else:
                data_p = np.nanmax(data_p, axis=-1) if is_bitset else data_p[..., 0]

        while data_g.ndim > 2:
            if data_g.shape[0] < data_g.shape[-1]:
                data_g = np.nanmax(data_g, axis=0) if is_bitset else data_g[0, ...]
            else:
                data_g = np.nanmax(data_g, axis=-1) if is_bitset else data_g[..., 0]

    mask_p, mask_g = np.isfinite(data_p), np.isfinite(data_g)
    common = np.logical_and(mask_p, mask_g)
    only_one_mask = np.logical_xor(mask_p, mask_g)
    only_one_frac = np.count_nonzero(only_one_mask) / data_p.size if data_p.size > 0 else 0.0

    diff_array = data_g - data_p
    valid_diffs = diff_array[np.isfinite(diff_array)]
    abs_diffs = np.abs(valid_diffs) if len(valid_diffs) > 0 else np.array([])

    mismatch_mask = np.where(np.abs(diff_array) > 1e-6, 1, 0)
    mismatch_cmap = plt.matplotlib.colors.ListedColormap(['white', 'lime'])

    r_sq = 0.0
    r_sq_is_na = True
    num_common = np.count_nonzero(common)

    # PERFORMANCE FIX: Pre-calculate the 500k sample arrays to prevent hexbin overload
    samp_p, samp_g = np.array([]), np.array([])

    if num_common > 1:
        s_size = min(num_common, 500_000)
        flat_idx = np.flatnonzero(common)
        sample_idx = np.random.choice(flat_idx, size=s_size, replace=False)
        try:
            samp_p = data_p.ravel()[sample_idx]
            samp_g = data_g.ravel()[sample_idx]
            if np.any(samp_p != samp_p[0]) and np.any(samp_g != samp_g[0]):
                r_sq = float(pearsonr(samp_p, samp_g)[0] ** 2)
                r_sq_is_na = False
        except:
            r_sq = 0.0
            r_sq_is_na = True

    m = GOES_REGEX.search(pair_info)
    product_dsn = m.group('dsn') if m else "Unknown"

    h, w = data_p.shape
    data_ratio = h / w if w > 0 else 1
    is_geo = HAS_CARTOPY and proj is not None

    kwargs = {'aspect': 'equal', 'origin': origin}

    # FEATURE FIX: Quantize the continuous colormap into discrete blocks to prevent smearing
    if is_bitset:
        try:
            kwargs['cmap'] = plt.get_cmap(cmap, 16)
        except AttributeError:
            kwargs['cmap'] = plt.cm.get_cmap(cmap, 16)
        kwargs['interpolation'] = 'nearest'
    else:
        kwargs['cmap'] = cmap

    # FEATURE FIX: Handle completely blank/NaN arrays caused by FillValue masking
    v_p, v_g = data_p[np.isfinite(data_p)], data_g[np.isfinite(data_g)]
    if len(v_p) > 0 and len(v_g) > 0:
        if is_bitset or len(np.unique(v_p)) <= 25:
            kwargs['vmin'] = min(np.nanmin(v_p), np.nanmin(v_g))
            kwargs['vmax'] = max(np.nanmax(v_p), np.nanmax(v_g))
        else:
            kwargs['vmin'] = min(np.nanpercentile(v_p, 1), np.nanpercentile(v_g, 1))
            kwargs['vmax'] = max(np.nanpercentile(v_p, 99), np.nanpercentile(v_g, 99))

        if kwargs['vmin'] == kwargs['vmax']:
            kwargs['vmin'] -= 0.1
            kwargs['vmax'] += 0.1
    else:
        kwargs['vmin'], kwargs['vmax'] = 0, 1

    if len(valid_diffs) > 0:
        d_min, d_max = np.nanmin(valid_diffs), np.nanmax(valid_diffs)
        if d_min == d_max: d_min, d_max = d_min - 0.1, d_max + 0.1
    else: d_min, d_max = -1, 1

    diff_kwargs = {'cmap': 'viridis', 'aspect': 'equal', 'origin': origin, 'vmin': d_min, 'vmax': d_max}
    mismatch_kwargs = {'cmap': mismatch_cmap, 'origin': origin, 'vmin': 0, 'vmax': 1, 'aspect': 'equal'}

    if is_bitset:
        diff_kwargs['interpolation'] = 'nearest'
        mismatch_kwargs['interpolation'] = 'nearest'

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

    def _add_cbar(im, ax, label=None):
        if is_geo: return plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04, label=label)
        div = make_axes_locatable(ax)
        return plt.colorbar(im, cax=div.append_axes("right", size="5%", pad=0.05), label=label)

    # --- PERFORMANCE: DOWN-SAMPLE VISUAL ARRAYS FOR PLOTTING SPEED ---
    # We do NOT downsample bitsets to avoid destroying precise categorical coordinate maps
    plot_step = max(1, max(h, w) // 1200) if not is_bitset else 1
    plot_p = data_p[::plot_step, ::plot_step] if plot_step > 1 else data_p
    plot_g = data_g[::plot_step, ::plot_step] if plot_step > 1 else data_g
    plot_diff = diff_array[::plot_step, ::plot_step] if plot_step > 1 else diff_array
    plot_mismatch = mismatch_mask[::plot_step, ::plot_step] if plot_step > 1 else mismatch_mask

    # PERFORMANCE FIX: Allow bypass of standalone generation to save heavy I/O
    if not fast_mode:
        try:
            # --- 1. STANDALONE COMPONENTS ---
            spatial_exports = [
                ('1_GCCS', 'GCCS', plot_g, kwargs),
                ('2_PREM', 'PREM', plot_p, kwargs),
                ('3_DIFF', 'DIFF', plot_diff, diff_kwargs),
                ('4_MISMATCH', 'MISMATCH', plot_mismatch, mismatch_kwargs)
            ]

            for file_suff, label, d, kw in spatial_exports:
                fig_i = plt.figure(figsize=(10, 10 * data_ratio))
                try:
                    ax_i = fig_i.add_subplot(111, projection=proj if is_geo else None)
                    if is_geo: _setup_geo_ax(ax_i)
                    im_i = ax_i.imshow(d, **kw)
                    _add_corner_labels(ax_i, label, upscale=True)
                    if label != 'MISMATCH': _add_cbar(im_i, ax_i)
                    fig_i.savefig(tmp_dir / f"{var}_{file_suff}.png", dpi=100, bbox_inches='tight')
                finally:
                    plt.close(fig_i)

            fig_scat = plt.figure(figsize=(10, 8))
            try:
                ax_scat = fig_scat.add_subplot(111)
                ax_scat.set_box_aspect(1)
                if len(samp_p) > 0 and not r_sq_is_na:
                    im_scat = ax_scat.hexbin(samp_p, samp_g, gridsize=60, cmap='viridis', mincnt=1, bins='log')
                    _add_cbar(im_scat, ax_scat, label='log10(count)')
                    ax_scat.set_title(f"{var} Correlation ($R^2$: {r_sq:.4f})", weight='bold', fontsize=12)
                    limits = [kwargs['vmin'], kwargs['vmax']]
                    ax_scat.plot(limits, limits, color='red', linestyle='--', linewidth=1.5, alpha=0.6, zorder=5)
                    ax_scat.set_xlim(limits); ax_scat.set_ylim(limits)
                else:
                    ax_scat.set_title(f"{var} Correlation ($R^2$: N/A)", weight='bold', fontsize=12)
                    ax_scat.text(0.5, 0.5, "Data Constant or N/A\nNo Variance to Plot", ha='center', va='center', color='gray', weight='bold')
                _add_corner_labels(ax_scat, "SCATTER", upscale=True)
                fig_scat.savefig(tmp_dir / f"{var}_5_SCATTER.png", dpi=100, bbox_inches='tight')
            finally:
                plt.close(fig_scat)

            fig_hist = plt.figure(figsize=(10, 6))
            try:
                ax_hist = fig_hist.add_subplot(111)
                ax_hist.set_box_aspect(1)
                if len(valid_diffs) > 0:
                    ax_hist.hist(valid_diffs, bins=100, color='gray', edgecolor='black')
                    ax_hist.axvline(0, color='red', linestyle='--')
                ax_hist.set_title(f"{var} Delta Distribution", weight='bold', fontsize=12)
                _add_corner_labels(ax_hist, "HIST", upscale=True)
                fig_hist.savefig(tmp_dir / f"{var}_6_HIST.png", dpi=100, bbox_inches='tight')
            finally:
                plt.close(fig_hist)
        except Exception:
            pass

    # --- 2. MASTER DASHBOARD (Landscape Optimization Format) ---
    fig = plt.figure(figsize=(24, 12))
    try:
        prem_name, gccs_name = pair_info.split(" <-> ", 1) if " <-> " in pair_info else (pair_info, "Unknown")
        unified_title = f"{product_dsn} | {var.upper()}\nPrem: {prem_name}\nGCCS: {gccs_name}"

        plt.suptitle(unified_title, fontsize=14, weight='bold', y=0.97, linespacing=1.3)

        gs = GridSpec(2, 8, figure=fig)

        ax1 = fig.add_subplot(gs[0, 0:2], projection=proj if is_geo else None)
        ax2 = fig.add_subplot(gs[0, 2:4], projection=proj if is_geo else None)
        ax4 = fig.add_subplot(gs[0, 4:6])

        ax3 = fig.add_subplot(gs[1, 0:2], projection=proj if is_geo else None)
        ax5 = fig.add_subplot(gs[1, 2:4], projection=proj if is_geo else None)
        ax6 = fig.add_subplot(gs[1, 4:6])

        ax_table = fig.add_subplot(gs[0:2, 6:8])

        dash_map = [(ax1, plot_p, kwargs, "On-Prem"), (ax2, plot_g, kwargs, "GCCS"),
                    (ax3, plot_mismatch, mismatch_kwargs, "Mismatch (Green=Error)"),
                    (ax5, plot_diff, diff_kwargs, "Difference (GCCS - PREM)")]

        for ax, d, kw, tit in dash_map:
            ax.set_title(tit, weight='bold', fontsize=12)
            if is_geo: _setup_geo_ax(ax)
            im = ax.imshow(d, **kw)
            if "Mismatch" not in tit: _add_cbar(im, ax)

        # Scatter
        ax4.set_box_aspect(1)
        ax4.set_xlabel("On-Prem", fontsize=11); ax4.set_ylabel("GCCS", fontsize=11)
        if len(samp_p) > 0 and not r_sq_is_na:
            im4 = ax4.hexbin(samp_p, samp_g, gridsize=60, cmap='viridis', mincnt=1, bins='log')
            ax4.set_title(f"Correlation ($R^2$: {r_sq:.4f})", weight='bold', fontsize=12)
            _add_cbar(im4, ax4, label='log10(count)')
            axis_bounds = [kwargs['vmin'], kwargs['vmax']]
            ax4.plot(axis_bounds, axis_bounds, color='red', linestyle='--', linewidth=1.5, alpha=0.6, zorder=5)
            ax4.set_xlim(axis_bounds); ax4.set_ylim(axis_bounds)
        else:
            ax4.set_title("Correlation ($R^2$: N/A)", weight='bold', fontsize=12)
            ax4.text(0.5, 0.5, "Data Constant or N/A\nNo Variance to Plot", ha='center', va='center', color='gray', weight='bold', transform=ax4.transAxes)

        # Histogram
        ax6.set_box_aspect(1)
        ax6.set_xlabel("Delta Value", fontsize=11); ax6.set_ylabel("Frequency", fontsize=11)
        if len(valid_diffs) > 0:
            ax6.hist(valid_diffs, bins=100, color='gray', edgecolor='black')
            ax6.axvline(0, color='red', linestyle='--')
            ax6.set_title("Distribution of Delta", weight='bold', fontsize=12)
        else:
            ax6.set_title("Distribution of Delta (N/A)", weight='bold', fontsize=12)
            ax6.text(0.5, 0.5, "No Finite Deltas Available", ha='center', va='center', color='gray', weight='bold', transform=ax6.transAxes)

        # Table Component
        ax_table.axis('off')
        ax_table.set_title("Comprehensive Statistical Summary", weight='bold', pad=10, fontsize=14)

        num_mismatches = np.count_nonzero(mismatch_mask == 1)
        prem_nans = np.count_nonzero(~mask_p)
        gccs_nans = np.count_nonzero(~mask_g)

        p_max = np.nanmax(data_p) if np.any(mask_p) else np.nan
        g_max = np.nanmax(data_g) if np.any(mask_g) else np.nan
        p_min = np.nanmin(data_p) if np.any(mask_p) else np.nan
        g_min = np.nanmin(data_g) if np.any(mask_g) else np.nan
        p_mean = np.nanmean(data_p) if np.any(mask_p) else np.nan
        g_mean = np.nanmean(data_g) if np.any(mask_g) else np.nan
        p_med = np.nanmedian(data_p) if np.any(mask_p) else np.nan
        g_med = np.nanmedian(data_g) if np.any(mask_g) else np.nan

        table_content = [
            ["Statistical Metric Description", "Observed Value (Prem / GCCS)"],
            ["Dataset Matrix\nDimensions", f"{data_p.shape}"],
            ["Total Bounding\nPixels", f"{data_p.size:,}"],
            ["Valid Common\nIntersections", f"{num_common:,}"],
            ["Mismatched Pixels\n(>1e-6 Threshold)", f"{num_mismatches:,}"],
            ["Coefficient of\nDetermination ($R^2$)", "N/A" if r_sq_is_na else f"{r_sq:.4f}"],
            ["Finite in Only\nOne Fraction (XOR)", f"{only_one_frac:.6f}"],
            ["Observed Maximum\nValue", f"Prem: {p_max:.4f}\nGCCS: {g_max:.4f}"],
            ["Observed Mean\nValue", f"Prem: {p_mean:.4f}\nGCCS: {g_mean:.4f}"],
            ["Observed Median\nValue", f"Prem: {p_med:.4f}\nGCCS: {g_med:.4f}"],
            ["Observed Minimum\nValue", f"Prem: {p_min:.4f}\nGCCS: {g_min:.4f}"],
            ["Missing Observations\n(NaNs)", f"Prem: {prem_nans:,}\nGCCS: {gccs_nans:,}"],
            ["Maximum Delta\n($GCCS - PREM$)", f"{np.nanmax(valid_diffs):.4f}" if len(valid_diffs) > 0 else "N/A"],
            ["Minimum Delta\n($GCCS - PREM$)", f"{np.nanmin(valid_diffs):.4f}" if len(valid_diffs) > 0 else "N/A"],
            ["Mean Delta\nError Bias", f"{np.nanmean(valid_diffs):.4f}" if len(valid_diffs) > 0 else "N/A"],
            ["Maximum Absolute\nDifference ($|\\Delta|$)", f"{np.max(abs_diffs):.4f}" if len(abs_diffs) > 0 else "N/A"],
            ["Minimum Absolute\nDifference ($|\\Delta|$)", f"{np.min(abs_diffs):.4f}" if len(abs_diffs) > 0 else "N/A"],
            ["Mean Absolute\nError Dispersion", f"{np.mean(abs_diffs):.4f}" if len(abs_diffs) > 0 else "N/A"]
        ]

        metric_table = ax_table.table(
            cellText=table_content,
            loc='center',
            cellLoc='center',
            colWidths=[0.55, 0.45],
            bbox=[0.0, 0.0, 1.0, 0.95]
        )
        metric_table.auto_set_font_size(False)
        metric_table.set_fontsize(12)

        plt.tight_layout(rect=[0, 0.05, 1, 0.95])

        if r_sq_is_na: b_color = 'lightgray'; banner_text = "R-Squared Correlation: N/A"
        elif r_sq >= 0.98: b_color = 'palegreen'; banner_text = f"R-Squared Correlation: {r_sq:.4f}"
        elif r_sq >= 0.90: b_color = 'moccasin'; banner_text = f"R-Squared Correlation: {r_sq:.4f}"
        else: b_color = 'lightcoral'; banner_text = f"R-Squared Correlation: {r_sq:.4f}"

        fig.text(0.5, 0.03, banner_text, ha='center', va='center', fontsize=22, weight='bold',
                 bbox=dict(facecolor=b_color, edgecolor='black', boxstyle='round,pad=0.5', alpha=0.9))

        plt.savefig(tmp_dir / f"{var}_comparison.png", dpi=100)
    finally:
        plt.close(fig)

    return [{'Metric': 'r-squared correlation', 'Value': np.nan if r_sq_is_na else r_sq}]


def execute_1d_scatter_dashboard(data_p, data_g, var, tmp_dir, pair_info, fast_mode=False):
    """Specialized rendering engine for raw 1D arrays."""
    mask_p, mask_g = np.isfinite(data_p), np.isfinite(data_g)
    common = np.logical_and(mask_p, mask_g)
    only_one_mask = np.logical_xor(mask_p, mask_g)
    only_one_frac = np.count_nonzero(only_one_mask) / data_p.size if data_p.size > 0 else 0.0

    diff_array = data_g - data_p
    valid_diffs = diff_array[np.isfinite(diff_array)]
    abs_diffs = np.abs(valid_diffs) if len(valid_diffs) > 0 else np.array([])
    mismatch_mask = np.where(np.abs(diff_array) > 1e-6, 1, 0)

    r_sq = 0.0
    r_sq_is_na = True
    num_common = np.count_nonzero(common)
    samp_p, samp_g = np.array([]), np.array([])

    if num_common > 1:
        s_size = min(num_common, 500_000)
        flat_idx = np.flatnonzero(common)
        sample_idx = np.random.choice(flat_idx, size=s_size, replace=False)
        try:
            samp_p = data_p[sample_idx]
            samp_g = data_g[sample_idx]
            if np.any(samp_p != samp_p[0]) and np.any(samp_g != samp_g[0]):
                r_sq = float(pearsonr(samp_p, samp_g)[0] ** 2)
                r_sq_is_na = False
        except:
            pass

    m = GOES_REGEX.search(pair_info)
    product_dsn = m.group('dsn') if m else "Unknown"

    fig = plt.figure(figsize=(16, 8))
    try:
        prem_name, gccs_name = pair_info.split(" <-> ", 1) if " <-> " in pair_info else (pair_info, "Unknown")
        unified_title = f"{product_dsn} | {var.upper()} (1D Collocated Track)\nPrem: {prem_name}\nGCCS: {gccs_name}"
        plt.suptitle(unified_title, fontsize=14, weight='bold', y=0.97, linespacing=1.3)

        gs = GridSpec(1, 3, figure=fig)

        ax_scat = fig.add_subplot(gs[0, 0])
        ax_hist = fig.add_subplot(gs[0, 1])
        ax_table = fig.add_subplot(gs[0, 2])

        # Scatter Plot
        ax_scat.set_box_aspect(1)
        ax_scat.set_xlabel("On-Prem Values", fontsize=11); ax_scat.set_ylabel("GCCS Values", fontsize=11)
        if len(samp_p) > 0:
            im_scat = ax_scat.hexbin(samp_p, samp_g, gridsize=40, cmap='viridis', mincnt=1, bins='log')
            ax_scat.set_title(f"Correlation ($R^2$: {'N/A' if r_sq_is_na else f'{r_sq:.4f}'})", weight='bold', fontsize=12)
            plt.colorbar(im_scat, ax=ax_scat, label='log10(count)', fraction=0.046, pad=0.04)
            axis_bounds = [min(ax_scat.get_xlim()[0], ax_scat.get_ylim()[0]), max(ax_scat.get_xlim()[1], ax_scat.get_ylim()[1])]
            ax_scat.plot(axis_bounds, axis_bounds, color='red', linestyle='--', linewidth=1.5, alpha=0.6, zorder=5)
            ax_scat.set_xlim(axis_bounds); ax_scat.set_ylim(axis_bounds)
        else:
            ax_scat.set_title("Correlation ($R^2$: N/A)", weight='bold', fontsize=12)
            ax_scat.text(0.5, 0.5, "Data Constant or N/A", ha='center', va='center', color='gray')

        # Histogram
        ax_hist.set_box_aspect(1)
        ax_hist.set_title("Distribution of Delta (GCCS - Prem)", weight='bold', fontsize=12)
        ax_hist.set_xlabel("Delta Value", fontsize=11); ax_hist.set_ylabel("Frequency", fontsize=11)
        if len(valid_diffs) > 0:
            ax_hist.hist(valid_diffs, bins=50, color='gray', edgecolor='black')
            ax_hist.axvline(0, color='red', linestyle='--')
        else:
            ax_hist.text(0.5, 0.5, "No finite deltas available.", ha='center', va='center', color='gray')

        # Analytics Table
        ax_table.axis('off')
        ax_table.set_title("1D Track Statistics", weight='bold', pad=10, fontsize=14)

        num_mismatches = np.count_nonzero(mismatch_mask == 1)
        prem_nans = np.count_nonzero(~mask_p)
        gccs_nans = np.count_nonzero(~mask_g)

        table_content = [
            ["Metric", "Value"],
            ["Total Observations", f"{data_p.size:,}"],
            ["Valid Common Intersections", f"{num_common:,}"],
            ["Mismatched Points (>1e-6)", f"{num_mismatches:,}"],
            ["R-Squared ($R^2$)", "N/A" if r_sq_is_na else f"{r_sq:.4f}"],
            ["Maximum Delta Bias", f"{np.nanmax(valid_diffs):.4f}" if len(valid_diffs) > 0 else "N/A"],
            ["Mean Delta Bias", f"{np.nanmean(valid_diffs):.4f}" if len(valid_diffs) > 0 else "N/A"],
            ["Mean Absolute Dispersion", f"{np.mean(abs_diffs):.4f}" if len(abs_diffs) > 0 else "N/A"]
        ]

        track_table = ax_table.table(
            cellText=table_content,
            loc='center',
            cellLoc='center',
            colWidths=[0.55, 0.45],
            bbox=[0.0, 0.1, 1.0, 0.8]
        )
        track_table.auto_set_font_size(False)
        track_table.set_fontsize(11)

        plt.tight_layout(rect=[0, 0.05, 1, 0.95])

        b_color = 'lightgray' if r_sq_is_na else 'palegreen' if r_sq >= 0.95 else 'moccasin' if r_sq >= 0.85 else 'lightcoral'
        banner_text = f"1D Track Correlation: {'N/A' if r_sq_is_na else f'{r_sq:.4f}'}"
        fig.text(0.5, 0.04, banner_text, ha='center', va='center', fontsize=16, weight='bold',
                 bbox=dict(facecolor=b_color, edgecolor='black', boxstyle='round,pad=0.5', alpha=0.9))

        plt.savefig(tmp_dir / f"{var}_comparison.png", dpi=100)
    finally:
        plt.close(fig)

    return [{'Metric': 'r-squared correlation', 'Value': np.nan if r_sq_is_na else r_sq}]


def compare_sparse_vectors(ds_p, ds_g, vt, v1, v2, tmp_dir, pair_info, instr, prod_name, fast_mode=False):
    """Generates an asymmetric master dashboard for sparse vector datasets using wind barbs."""
    lat_v, lon_v = get_coords_for_var(ds_p, v1)
    if not lat_v or not lon_v: return

    try:
        lat_p, lon_p = ds_p[lat_v].values.ravel(), ds_p[lon_v].values.ravel()
        spd_p, dir_p = ds_p[v1].values.astype(np.float32).ravel(), ds_p[v2].values.astype(np.float32).ravel()
        lat_g, lon_g = ds_g[lat_v].values.ravel(), ds_g[lon_v].values.ravel()
        spd_g, dir_g = ds_g[v1].values.astype(np.float32).ravel(), ds_g[v2].values.astype(np.float32).ravel()
    except:
        return

    mask_p = np.isfinite(lat_p) & np.isfinite(lon_p) & np.isfinite(spd_p) & np.isfinite(dir_p) & (spd_p >= 0)
    mask_g = np.isfinite(lat_g) & np.isfinite(lon_g) & np.isfinite(spd_g) & np.isfinite(dir_g) & (spd_g >= 0)

    def _convert_to_uv(spd, heading):
        rad = heading * np.pi / 180.0
        u = -spd * np.sin(rad)
        v = -spd * -np.cos(rad)
        return u, v

    u_p, v_p = _convert_to_uv(spd_p[mask_p], dir_p[mask_p])
    u_g, v_g = _convert_to_uv(spd_g[mask_g], dir_g[mask_g])

    min_lon = min(np.nanmin(lon_p), np.nanmin(lon_g)) if len(lon_p)>0 else -180
    max_lon = max(np.nanmax(lon_p), np.nanmax(lon_g)) if len(lon_p)>0 else 180
    min_lat = min(np.nanmin(lat_p), np.nanmin(lat_g)) if len(lat_p)>0 else -90
    max_lat = max(np.nanmax(lat_p), np.nanmax(lat_g)) if len(lat_p)>0 else 90

    lon_edges = np.linspace(min_lon, max_lon, 101)
    lat_edges = np.linspace(min_lat, max_lat, 101)

    grid_u_p = grid_sparse_component(lon_p[mask_p], lat_p[mask_p], u_p, lon_edges, lat_edges)
    grid_v_p = grid_sparse_component(lon_p[mask_p], lat_p[mask_p], v_p, lon_edges, lat_edges)
    grid_u_g = grid_sparse_component(lon_g[mask_g], lat_g[mask_g], u_g, lon_edges, lat_edges)
    grid_v_g = grid_sparse_component(lon_g[mask_g], lat_g[mask_g], v_g, lon_edges, lat_edges)

    grid_spd_p = np.sqrt(grid_u_p**2 + grid_v_p**2)
    grid_spd_g = np.sqrt(grid_u_g**2 + grid_v_g**2)
    diff_array = grid_spd_g - grid_spd_p
    valid_diffs = diff_array[np.isfinite(diff_array)]
    abs_diffs = np.abs(valid_diffs) if len(valid_diffs) > 0 else np.array([])

    mismatch_mask = np.where(np.abs(diff_array) > 0.5, 1, 0)
    mismatch_cmap = plt.matplotlib.colors.ListedColormap(['white', 'lime'])

    common = np.isfinite(grid_spd_p) & np.isfinite(grid_spd_g)
    mask_p_grid, mask_g_grid = np.isfinite(grid_spd_p), np.isfinite(grid_spd_g)
    only_one_grid = np.logical_xor(mask_p_grid, mask_g_grid)
    only_one_frac_g = np.count_nonzero(only_one_grid) / grid_spd_p.size if grid_spd_p.size > 0 else 0.0

    r_sq = 0.0
    r_sq_is_na = True
    num_common = np.count_nonzero(common)
    samp_p, samp_g = np.array([]), np.array([])

    if num_common > 1:
        s_size = min(num_common, 500_000)
        flat_idx = np.flatnonzero(common)
        sample_idx = np.random.choice(flat_idx, size=s_size, replace=False)
        try:
            samp_p = grid_spd_p.ravel()[sample_idx]
            samp_g = grid_spd_g.ravel()[sample_idx]
            r_sq = float(pearsonr(samp_p, samp_g)[0] ** 2)
            r_sq_is_na = False
        except:
            pass

    proj = ccrs.PlateCarree() if HAS_CARTOPY else None
    is_geo = HAS_CARTOPY

    def _setup_geo_ax(ax):
        if is_geo:
            ax.add_feature(cfeature.COASTLINE, color='black', linewidth=0.7, zorder=10)
            ax.add_feature(cfeature.BORDERS, edgecolor='black', linewidth=0.4, linestyle=':', zorder=10)

    def _add_local_cbar(im, ax, label=None):
        if is_geo: return plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04, label=label)
        div = make_axes_locatable(ax)
        return plt.colorbar(im, cax=div.append_axes("right", size="5%", pad=0.05), label=label)

    extent = [min_lon, max_lon, min_lat, max_lat]
    kwargs = {'cmap': 'viridis', 'origin': 'lower', 'extent': extent, 'aspect': 'equal'}
    if is_geo: kwargs['transform'] = ccrs.PlateCarree()

    if np.any(np.isfinite(grid_spd_p)) and np.any(np.isfinite(grid_spd_g)):
        vmax = max(np.nanpercentile(grid_spd_p, 99), np.nanpercentile(grid_spd_g, 99))
        kwargs['vmin'], kwargs['vmax'] = 0, vmax

    d_min = np.nanmin(valid_diffs) if len(valid_diffs) > 0 else -1
    d_max = np.nanmax(valid_diffs) if len(valid_diffs) > 0 else 1

    # --- 1. STANDALONE VECTOR COMPONENT EXPORTS ---
    if not fast_mode:
        try:
            # 1_GCCS
            fig_i = plt.figure(figsize=(10, 10))
            ax_i = fig_i.add_subplot(111, projection=proj)
            if is_geo: _setup_geo_ax(ax_i)
            ax_i.set_title("GCCS Wind Barbs", weight='bold')
            if len(u_g) > 0:
                step_g = max(1, len(u_g) // 600)
                b_kw = {'cmap': 'viridis', 'length': 5, 'linewidth': 0.6}
                if is_geo: b_kw['transform'] = ccrs.PlateCarree()
                im_i = ax_i.barbs(lon_g[mask_g][::step_g], lat_g[mask_g][::step_g], u_g[::step_g], v_g[::step_g], spd_g[mask_g][::step_g], **b_kw)
                _add_local_cbar(im_i, ax_i, label='Wind Speed (m/s)')
            ax_i.set_xlim(min_lon, max_lon); ax_i.set_ylim(min_lat, max_lat)
            fig_i.savefig(tmp_dir / f"{v1}_1_GCCS.png", dpi=100, bbox_inches='tight')
            plt.close(fig_i)

            # 2_PREM
            fig_i = plt.figure(figsize=(10, 10))
            ax_i = fig_i.add_subplot(111, projection=proj)
            if is_geo: _setup_geo_ax(ax_i)
            ax_i.set_title("PREM Wind Barbs", weight='bold')
            if len(u_p) > 0:
                step_p = max(1, len(u_p) // 600)
                b_kw = {'cmap': 'viridis', 'length': 5, 'linewidth': 0.6}
                if is_geo: b_kw['transform'] = ccrs.PlateCarree()
                im_i = ax_i.barbs(lon_p[mask_p][::step_p], lat_p[mask_p][::step_p], u_p[::step_p], v_p[::step_p], spd_p[mask_p][::step_p], **b_kw)
                _add_local_cbar(im_i, ax_i, label='Wind Speed (m/s)')
            ax_i.set_xlim(min_lon, max_lon); ax_i.set_ylim(min_lat, max_lat)
            fig_i.savefig(tmp_dir / f"{v1}_2_PREM.png", dpi=100, bbox_inches='tight')
            plt.close(fig_i)

            # 3_DIFF
            fig_i = plt.figure(figsize=(10, 10))
            ax_i = fig_i.add_subplot(111, projection=proj)
            if is_geo: _setup_geo_ax(ax_i)
            ax_i.set_title("DIFF Vector Magnitude", weight='bold')
            im_i = ax_i.imshow(diff_array, cmap='coolwarm', origin='lower', extent=extent, aspect='equal', vmin=d_min, vmax=d_max, transform=ccrs.PlateCarree() if is_geo else None)
            _add_local_cbar(im_i, ax_i, label='Delta (m/s)')
            fig_i.savefig(tmp_dir / f"{v1}_3_DIFF.png", dpi=100, bbox_inches='tight')
            plt.close(fig_i)

            # 4_MISMATCH
            fig_i = plt.figure(figsize=(10, 10))
            ax_i = fig_i.add_subplot(111, projection=proj)
            if is_geo: _setup_geo_ax(ax_i)
            ax_i.set_title("MISMATCH Mask", weight='bold')
            im_i = ax_i.imshow(mismatch_mask, cmap=mismatch_cmap, origin='lower', extent=extent, aspect='equal', vmin=0, vmax=1, transform=ccrs.PlateCarree() if is_geo else None)
            fig_i.savefig(tmp_dir / f"{v1}_4_MISMATCH.png", dpi=100, bbox_inches='tight')
            plt.close(fig_i)

            # 5_SCATTER
            fig_scat = plt.figure(figsize=(10, 8))
            ax_scat = fig_scat.add_subplot(111)
            ax_scat.set_box_aspect(1)
            if len(samp_p) > 0 and not r_sq_is_na:
                im_scat = ax_scat.hexbin(samp_p, samp_g, gridsize=40, cmap='viridis', mincnt=1, bins='log')
                _add_local_cbar(im_scat, ax_scat, label='log10(count)')
                ax_scat.set_title(f"{v1} Correlation ($R^2$: {'N/A' if r_sq_is_na else f'{r_sq:.4f}'})", weight='bold', fontsize=12)
                limits = [min(ax_scat.get_xlim()[0], ax_scat.get_ylim()[0]), max(ax_scat.get_xlim()[1], ax_scat.get_ylim()[1])]
                ax_scat.plot(limits, limits, color='red', linestyle='--', linewidth=1.5, alpha=0.6, zorder=5)
                ax_scat.set_xlim(limits); ax_scat.set_ylim(limits)
            else:
                ax_scat.set_title(f"{v1} Correlation ($R^2$: N/A)", weight='bold', fontsize=12)
                ax_scat.text(0.5, 0.5, "Data Constant or N/A", ha='center', va='center', color='gray')
            fig_scat.savefig(tmp_dir / f"{v1}_5_SCATTER.png", dpi=100, bbox_inches='tight')
            plt.close(fig_scat)

            # 6_HIST
            fig_hist = plt.figure(figsize=(10, 6))
            ax_hist = fig_hist.add_subplot(111)
            ax_hist.set_box_aspect(1)
            if len(valid_diffs) > 0:
                ax_hist.hist(valid_diffs, bins=50, color='gray', edgecolor='black')
                ax_hist.axvline(0, color='red', linestyle='--')
            ax_hist.set_title(f"{v1} Vector Distribution", weight='bold', fontsize=12)
            fig_hist.savefig(tmp_dir / f"{v1}_6_HIST.png", dpi=100, bbox_inches='tight')
            plt.close(fig_hist)
        except Exception as e_indiv:
            pass # Fail gracefully, preserve master dashboard

    # --- 2. MASTER DASHBOARD ---
    fig = plt.figure(figsize=(24, 12))
    try:
        m = GOES_REGEX.search(pair_info)
        product_dsn = m.group('dsn') if m else "Unknown"
        prem_name, gccs_name = pair_info.split(" <-> ", 1) if " <-> " in pair_info else (pair_info, "Unknown")

        plt.suptitle(f"{product_dsn} | {v1.upper()} VECTOR DASHBOARD\nPrem: {prem_name}\nGCCS: {gccs_name}",
                     fontsize=14, weight='bold', y=0.97, linespacing=1.3)

        gs = GridSpec(2, 8, figure=fig)

        ax1 = fig.add_subplot(gs[0, 0:2], projection=proj)
        ax2 = fig.add_subplot(gs[0, 2:4], projection=proj)
        ax4 = fig.add_subplot(gs[0, 4:6])

        ax3 = fig.add_subplot(gs[1, 0:2], projection=proj)
        ax5 = fig.add_subplot(gs[1, 2:4], projection=proj)
        ax6 = fig.add_subplot(gs[1, 4:6])

        ax_table = fig.add_subplot(gs[0:2, 6:8])

        # Cell 1: On-Prem Wind Field Flow Map (Barbs)
        ax1.set_title("On-Prem Wind Barbs", weight='bold', fontsize=12)
        if is_geo: _setup_geo_ax(ax1)
        if len(u_p) > 0:
            step_p = max(1, len(u_p) // 600)
            b_kw = {'cmap': 'viridis', 'length': 5, 'linewidth': 0.6}
            if is_geo: b_kw['transform'] = ccrs.PlateCarree()
            im1 = ax1.barbs(lon_p[mask_p][::step_p], lat_p[mask_p][::step_p], u_p[::step_p], v_p[::step_p],
                             spd_p[mask_p][::step_p], **b_kw)
        ax1.set_xlim(min_lon, max_lon); ax1.set_ylim(min_lat, max_lat); ax1.grid(True, linestyle=':', alpha=0.5)

        # Cell 2: GCCS Wind Field Flow Map (Barbs)
        ax2.set_title("GCCS Wind Barbs", weight='bold', fontsize=12)
        if is_geo: _setup_geo_ax(ax2)
        if len(u_g) > 0:
            step_g = max(1, len(u_g) // 600)
            b_kw = {'cmap': 'viridis', 'length': 5, 'linewidth': 0.6}
            if is_geo: b_kw['transform'] = ccrs.PlateCarree()
            im2 = ax2.barbs(lon_g[mask_g][::step_g], lat_g[mask_g][::step_g], u_g[::step_g], v_g[::step_g],
                             spd_g[mask_g][::step_g], **b_kw)
            _add_local_cbar(im2, ax2, label='Wind Speed (m/s)')
        ax2.set_xlim(min_lon, max_lon); ax2.set_ylim(min_lat, max_lat); ax2.grid(True, linestyle=':', alpha=0.5)

        # Cell 3: Flow Deviation Mask
        ax3.set_title("Flow Deviation Mask (Green=Error > 0.5m/s)", weight='bold', fontsize=12)
        if is_geo: _setup_geo_ax(ax3)
        im3_mask = ax3.imshow(mismatch_mask, cmap=mismatch_cmap, origin='lower', extent=extent, aspect='equal', vmin=0, vmax=1, transform=ccrs.PlateCarree() if is_geo else None)

        # Cell 5: Speed Difference Map
        ax5.set_title("Difference (GCCS - PREM Speed)", weight='bold', fontsize=12)
        if is_geo: _setup_geo_ax(ax5)
        im5 = ax5.imshow(diff_array, cmap='coolwarm', origin='lower', extent=extent, aspect='equal', vmin=d_min, vmax=d_max, transform=ccrs.PlateCarree() if is_geo else None)
        _add_local_cbar(im5, ax5, label='Delta (m/s)')

        # Cell 4: Speed Scatter Correlation Plot
        ax4.set_box_aspect(1)
        ax4.set_xlabel("On-Prem Speed (m/s)", fontsize=11); ax4.set_ylabel("GCCS Speed (m/s)", fontsize=11)
        if len(samp_p) > 0 and not r_sq_is_na:
            im4 = ax4.hexbin(samp_p, samp_g, gridsize=40, cmap='viridis', mincnt=1, bins='log')
            ax4.set_title(f"Speed Correlation ($R^2$: {'N/A' if r_sq_is_na else f'{r_sq:.4f}'})", weight='bold', fontsize=12)
            _add_local_cbar(im4, ax4, label='log10(count)')
            axis_bounds = [min(ax4.get_xlim()[0], ax4.get_ylim()[0]), max(ax4.get_xlim()[1], ax4.get_ylim()[1])]
            ax4.plot(axis_bounds, axis_bounds, color='red', linestyle='--', linewidth=1.5, alpha=0.6, zorder=5)
            ax4.set_xlim(axis_bounds); ax4.set_ylim(axis_bounds)
        else:
            ax4.set_title("Speed Correlation ($R^2$: N/A)", weight='bold', fontsize=12)
            ax4.text(0.5, 0.5, "No overlapping tracking data matrix segments found.", ha='center', va='center', color='gray')

        # Cell 6: Speed Delta Histogram
        ax6.set_box_aspect(1)
        ax6.set_title("Distribution of Speed Delta", weight='bold', fontsize=12)
        ax6.set_xlabel("Delta Value (m/s)", fontsize=11); ax6.set_ylabel("Frequency", fontsize=11)
        if len(valid_diffs) > 0:
            ax6.hist(valid_diffs, bins=50, color='gray', edgecolor='black')
            ax6.axvline(0, color='red', linestyle='--')
        else:
            ax6.text(0.5, 0.5, "No finite delta components available.", ha='center', va='center', color='gray')

        # Cell 7: Full Height Table Component
        ax_table.axis('off')
        ax_table.set_title("Vector Statistical Metrics", weight='bold', pad=10, fontsize=14)

        num_mismatches = np.count_nonzero(mismatch_mask == 1)
        prem_nans_g = np.count_nonzero(~mask_p_grid)
        gccs_nans_g = np.count_nonzero(~mask_g_grid)
        p_max_g = np.nanmax(grid_spd_p) if np.any(mask_p_grid) else np.nan
        g_max_g = np.nanmax(grid_spd_g) if np.any(mask_g_grid) else np.nan
        p_min_g = np.nanmin(grid_spd_p) if np.any(mask_p_grid) else np.nan
        g_min_g = np.nanmin(grid_spd_g) if np.any(mask_g_grid) else np.nan
        p_mean_g = np.nanmean(grid_spd_p) if np.any(mask_p_grid) else np.nan
        g_mean_g = np.nanmean(grid_spd_g) if np.any(mask_g_grid) else np.nan
        p_med_g = np.nanmedian(grid_spd_p) if np.any(mask_p_grid) else np.nan
        g_med_g = np.nanmedian(grid_spd_g) if np.any(mask_g_grid) else np.nan

        table_content = [
            ["Vector Metric Description", "Observed Target Value"],
            ["Binned Matrix Grid\nDimensions", f"{grid_spd_p.shape}"],
            ["Total Grid Spaces", f"{grid_spd_p.size:,}"],
            ["Valid Binned\nIntersections", f"{num_common:,}"],
            ["Deviated Cells\n(>0.5m/s Margin)", f"{num_mismatches:,}"],
            ["Calculated Speed R² Fit", "N/A" if r_sq_is_na else f"{r_sq:.4f}"],
            ["Finite in Only\nOne Fraction (XOR)", f"{only_one_frac_g:.6f}"],
            ["Binned Maximum Speed", f"Prem: {p_max_g:.4f}\nGCCS: {g_max_g:.4f}"],
            ["Binned Mean Speed", f"Prem: {p_mean_g:.4f}\nGCCS: {g_mean_g:.4f}"],
            ["Binned Median Speed", f"Prem: {p_med_g:.4f}\nGCCS: {g_med_g:.4f}"],
            ["Binned Minimum Speed", f"Prem: {p_min_g:.4f}\nGCCS: {g_min_g:.4f}"],
            ["Empty Unpopulated\nGrid Cells", f"Prem: {prem_nans_g:,}\nGCCS: {gccs_nans_g:,}"],
            ["Maximum Delta\n($GCCS - PREM$)", f"{np.nanmax(valid_diffs):.4f}" if len(valid_diffs) > 0 else "N/A"],
            ["Minimum Delta\n($GCCS - PREM$)", f"{np.nanmin(valid_diffs):.4f}" if len(valid_diffs) > 0 else "N/A"],
            ["Mean Delta Wind Bias", f"{np.nanmean(valid_diffs):.4f}" if len(valid_diffs) > 0 else "N/A"],
            ["Maximum Absolute\nDifference ($|\\Delta|$)", f"{np.max(abs_diffs):.4f}" if len(abs_diffs) > 0 else "N/A"],
            ["Minimum Absolute\nDifference ($|\\Delta|$)", f"{np.min(abs_diffs):.4f}" if len(abs_diffs) > 0 else "N/A"],
            ["Mean Absolute Speed\nError Dispersion", f"{np.mean(abs_diffs):.4f}" if len(abs_diffs) > 0 else "N/A"]
        ]

        vector_table = ax_table.table(
            cellText=table_content,
            loc='center',
            cellLoc='center',
            colWidths=[0.55, 0.45],
            bbox=[0.0, 0.0, 1.0, 0.95]
        )
        vector_table.auto_set_font_size(False)
        vector_table.set_fontsize(12)

        plt.tight_layout(rect=[0, 0.05, 1, 0.95])

        b_color = 'lightgray' if r_sq_is_na else 'palegreen' if r_sq >= 0.95 else 'moccasin' if r_sq >= 0.85 else 'lightcoral'
        banner_text = f"Vector Correlation Check: {'N/A' if r_sq_is_na else f'{r_sq:.4f}'}"
        fig.text(0.5, 0.03, banner_text, ha='center', va='center', fontsize=22, weight='bold',
                 bbox=dict(facecolor=b_color, edgecolor='black', boxstyle='round,pad=0.5', alpha=0.9))

        plt.savefig(tmp_dir / f"{v1}_comparison.png", dpi=100)
    finally:
        plt.close(fig)

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
