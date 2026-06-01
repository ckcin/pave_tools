#!/usr/bin/env python3
"""
COMPARE-PAVE: Shared Utility Suite
==================================
VERSION: 1.21.1 (Whitespace Compression & Expanded Statistical Analysis Tables - High Vis Fonts)
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

# --- CORE PLOTTING ENGINES ---

def execute_visual_comparison(data_p, data_g, var, tmp_dir, pair_info, strategy_label, proj=None, extent=None, origin='upper', cmap='viridis'):
    """Memory-safe plotting engine with optimized row scaling and expanded summary metrics table."""

    # Squeeze out empty leading singleton dimensions
    data_p = np.squeeze(data_p)
    data_g = np.squeeze(data_g)

    if data_p.ndim > 2: data_p = data_p[0, ...]
    if data_g.ndim > 2: data_g = data_g[0, ...]

    mask_p, mask_g = np.isfinite(data_p), np.isfinite(data_g)
    common = np.logical_and(mask_p, mask_g)
    only_one_mask = np.logical_xor(mask_p, mask_g)
    only_one_frac = np.count_nonzero(only_one_mask) / data_p.size if data_p.size > 0 else 0.0

    # Core Arrays Differential Calculations
    diff_array = data_g - data_p
    valid_diffs = diff_array[np.isfinite(diff_array)]
    abs_diffs = np.abs(valid_diffs) if len(valid_diffs) > 0 else np.array([])

    mismatch_mask = np.where(np.abs(diff_array) > 1e-6, 1, 0)
    mismatch_cmap = plt.matplotlib.colors.ListedColormap(['white', 'lime'])

    r_sq = 0.0
    r_sq_is_na = True
    num_common = np.count_nonzero(common)

    if num_common > 1:
        s_size = min(num_common, 500_000)
        flat_idx = np.flatnonzero(common)
        sample_idx = np.random.choice(flat_idx, size=s_size, replace=False)
        try:
            samp_p, samp_g = data_p.ravel()[sample_idx], data_g.ravel()[sample_idx]
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

    kwargs = {'cmap': cmap, 'aspect': 'equal', 'origin': origin}
    v_p, v_g = data_p[np.isfinite(data_p)], data_g[np.isfinite(data_g)]
    if len(v_p) > 0 and len(v_g) > 0:
        kwargs['vmin'] = min(np.nanpercentile(v_p, 1), np.nanpercentile(v_g, 1))
        kwargs['vmax'] = max(np.nanpercentile(v_p, 99), np.nanpercentile(v_g, 99))

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
        size = 18 if upscale else 11
        ax.text(0.01, 0.99, label, transform=ax.transAxes, color='black', weight='bold',
                fontsize=size, va='top', path_effects=halo, zorder=20)

    def _add_cbar(im, ax, label=None):
        if is_geo: return plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04, label=label)
        div = make_axes_locatable(ax)
        return plt.colorbar(im, cax=div.append_axes("right", size="5%", pad=0.05), label=label)

    try:
        # --- 1. STANDALONE COMPONENTS ---
        spatial_exports = [
            ('1_GCCS', 'GCCS', data_g, kwargs),
            ('2_PREM', 'PREM', data_p, kwargs),
            ('3_DIFF', 'DIFF', diff_array, diff_kwargs),
            ('4_MISMATCH', 'MISMATCH', mismatch_mask, mismatch_kwargs)
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
            if len(v_p) > 0 and not r_sq_is_na:
                im_scat = ax_scat.hexbin(data_p[common], data_g[common], gridsize=60, cmap='viridis', mincnt=1, bins='log')
                _add_cbar(im_scat, ax_scat, label='log10(count)')
                ax_scat.set_title(f"{var} Correlation ($R^2$: {r_sq:.4f})", weight='bold', fontsize=12)
                limits = [min(ax_scat.get_xlim()[0], ax_scat.get_ylim()[0]), max(ax_scat.get_xlim()[1], ax_scat.get_ylim()[1])]
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

        # --- 2. MASTER DASHBOARD (Squeezed 18x20 Canvas Layout) ---
        fig = plt.figure(figsize=(18, 20))
        try:
            prem_name, gccs_name = pair_info.split(" <-> ", 1) if " <-> " in pair_info else (pair_info, "Unknown")
            unified_title = f"{product_dsn} | {var.upper()}\nPrem: {prem_name}\nGCCS: {gccs_name}"

            # INCREASED MAIN TITLE FONT SIZE HERE
            plt.suptitle(unified_title, fontsize=22, weight='bold', y=0.98, linespacing=1.3)

            gs = GridSpec(3, 6, figure=fig)

            ax1 = fig.add_subplot(gs[0, 0:3], projection=proj if is_geo else None)
            ax2 = fig.add_subplot(gs[0, 3:6], projection=proj if is_geo else None)
            ax3 = fig.add_subplot(gs[1, 0:3], projection=proj if is_geo else None)
            ax5 = fig.add_subplot(gs[1, 3:6], projection=proj if is_geo else None)

            ax4 = fig.add_subplot(gs[2, 0:2])
            ax6 = fig.add_subplot(gs[2, 2:4])
            ax_table = fig.add_subplot(gs[2, 4:6])

            dash_map = [(ax1, data_p, kwargs, "On-Prem"), (ax2, data_g, kwargs, "GCCS"),
                        (ax3, mismatch_mask, mismatch_kwargs, "Mismatch (Green=Error)"),
                        (ax5, diff_array, diff_kwargs, "Difference (GCCS - PREM)")]

            for ax, data, kw, tit in dash_map:
                ax.set_title(tit, weight='bold', fontsize=12)
                if is_geo: _setup_geo_ax(ax)
                im = ax.imshow(data, **kw)
                if "Mismatch" not in tit: _add_cbar(im, ax)

            # Cell 4: Scatter
            ax4.set_box_aspect(1)
            ax4.set_xlabel("On-Prem", fontsize=11); ax4.set_ylabel("GCCS", fontsize=11)
            if len(v_p) > 0 and not r_sq_is_na:
                im4 = ax4.hexbin(data_p[common], data_g[common], gridsize=60, cmap='viridis', mincnt=1, bins='log')
                ax4.set_title(f"Correlation ($R^2$: {r_sq:.4f})", weight='bold', fontsize=12)
                _add_cbar(im4, ax4, label='log10(count)')
                axis_bounds = [min(ax4.get_xlim()[0], ax4.get_ylim()[0]), max(ax4.get_xlim()[1], ax4.get_ylim()[1])]
                ax4.plot(axis_bounds, axis_bounds, color='red', linestyle='--', linewidth=1.5, alpha=0.6, zorder=5)
                ax4.set_xlim(axis_bounds); ax4.set_ylim(axis_bounds)
            else:
                ax4.set_title("Correlation ($R^2$: N/A)", weight='bold', fontsize=12)
                ax4.text(0.5, 0.5, "Data Constant or N/A\nNo Variance to Plot", ha='center', va='center', color='gray', weight='bold', transform=ax4.transAxes)

            # Cell 6: Histogram
            ax6.set_box_aspect(1)
            ax6.set_xlabel("Delta Value", fontsize=11); ax6.set_ylabel("Frequency", fontsize=11)
            if len(valid_diffs) > 0:
                ax6.hist(valid_diffs, bins=100, color='gray', edgecolor='black')
                ax6.axvline(0, color='red', linestyle='--')
                ax6.set_title("Distribution of Delta", weight='bold', fontsize=12)
            else:
                ax6.set_title("Distribution of Delta (N/A)", weight='bold', fontsize=12)
                ax6.text(0.5, 0.5, "No Finite Deltas Available", ha='center', va='center', color='gray', weight='bold', transform=ax6.transAxes)

            # Cell 7: Table Component
            ax_table.axis('off')
            ax_table.set_title("Comprehensive Statistical Summary", weight='bold', pad=5, fontsize=12)

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
                ["Dataset Matrix Dimensions", f"{data_p.shape}"],
                ["Total Bounding Pixels", f"{data_p.size:,}"],
                ["Valid Common Intersections", f"{num_common:,}"],
                ["Mismatched Pixels (>1e-6 Threshold)", f"{num_mismatches:,}"],
                ["Coefficient of Determination ($R^2$)", "N/A" if r_sq_is_na else f"{r_sq:.4f}"],
                ["Finite in Only One Fraction (XOR)", f"{only_one_frac:.6f}"],
                ["Observed Maximum Value", f"{p_max:.4f} / {g_max:.4f}"],
                ["Observed Mean Value", f"{p_mean:.4f} / {g_mean:.4f}"],
                ["Observed Median Value", f"{p_med:.4f} / {g_med:.4f}"],
                ["Observed Minimum Value", f"{p_min:.4f} / {g_min:.4f}"],
                ["Missing Observations (NaNs)", f"{prem_nans:,} / {gccs_nans:,}"],
                ["Maximum Delta ($GCCS - PREM$)", f"{np.nanmax(valid_diffs):.4f}" if len(valid_diffs) > 0 else "N/A"],
                ["Minimum Delta ($GCCS - PREM$)", f"{np.nanmin(valid_diffs):.4f}" if len(valid_diffs) > 0 else "N/A"],
                ["Mean Delta Error Bias", f"{np.nanmean(valid_diffs):.4f}" if len(valid_diffs) > 0 else "N/A"],
                ["Maximum Absolute Difference ($|\\Delta|$)", f"{np.max(abs_diffs):.4f}" if len(abs_diffs) > 0 else "N/A"],
                ["Minimum Absolute Difference ($|\\Delta|$)", f"{np.min(abs_diffs):.4f}" if len(abs_diffs) > 0 else "N/A"],
                ["Mean Absolute Error Dispersion", f"{np.mean(abs_diffs):.4f}" if len(abs_diffs) > 0 else "N/A"]
            ]

            metric_table = ax_table.table(cellText=table_content, loc='center', cellLoc='center', colWidths=[0.60, 0.40])
            metric_table.auto_set_font_size(False)

            # INCREASED TABLE FONT AND SCALE HERE
            metric_table.set_fontsize(12)
            metric_table.scale(1.0, 2.0)

            plt.tight_layout(rect=[0, 0.04, 1, 0.96])

            if r_sq_is_na: b_color = 'lightgray'; banner_text = "R-Squared Correlation: N/A"
            elif r_sq >= 0.98: b_color = 'palegreen'; banner_text = f"R-Squared Correlation: {r_sq:.4f}"
            elif r_sq >= 0.90: b_color = 'moccasin'; banner_text = f"R-Squared Correlation: {r_sq:.4f}"
            else: b_color = 'lightcoral'; banner_text = f"R-Squared Correlation: {r_sq:.4f}"

            fig.text(0.5, 0.025, banner_text, ha='center', va='center', fontsize=22, weight='bold',
                     bbox=dict(facecolor=b_color, edgecolor='black', boxstyle='round,pad=0.5', alpha=0.9))

            plt.savefig(tmp_dir / f"{var}_comparison.png", dpi=100)
        finally:
            plt.close(fig)

    except Exception as e:
        plt.close('all')
        raise e

    return [{'Metric': 'r-squared correlation', 'Value': np.nan if r_sq_is_na else r_sq}]


def compare_sparse_vectors(ds_p, ds_g, vt, v1, v2, tmp_dir, pair_info, instr, prod_name):
    """Generates an asymmetric master dashboard for sparse vector datasets with square spatial and analytical panels."""
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

    def _grid_component(lon, lat, val):
        cnt, _, _ = np.histogram2d(lon, lat, bins=[lon_edges, lat_edges])
        sm, _, _ = np.histogram2d(lon, lat, bins=[lon_edges, lat_edges], weights=val)
        with np.errstate(divide='ignore', invalid='ignore'):
            return np.where(cnt > 0, sm / cnt, np.nan).T

    grid_u_p = _grid_component(lon_p[mask_p], lat_p[mask_p], u_p)
    grid_v_p = _grid_component(lon_p[mask_p], lat_p[mask_p], v_p)
    grid_u_g = _grid_component(lon_g[mask_g], lat_g[mask_g], u_g)
    grid_v_g = _grid_component(lon_g[mask_g], lat_g[mask_g], v_g)

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
    if num_common > 1:
        try:
            r_sq = float(pearsonr(grid_spd_p[common], grid_spd_g[common])[0] ** 2)
            r_sq_is_na = False
        except:
            pass

    fig = plt.figure(figsize=(18, 20))
    try:
        m = GOES_REGEX.search(pair_info)
        product_dsn = m.group('dsn') if m else "Unknown"
        prem_name, gccs_name = pair_info.split(" <-> ", 1) if " <-> " in pair_info else (pair_info, "Unknown")

        # INCREASED SPARSE TITLE FONT SIZE HERE
        plt.suptitle(f"{product_dsn} | {v1.upper()} VECTOR DASHBOARD\nPrem: {prem_name}\nGCCS: {gccs_name}",
                     fontsize=22, weight='bold', y=0.98, linespacing=1.3)

        gs = GridSpec(3, 6, figure=fig)

        ax1 = fig.add_subplot(gs[0, 0:3])
        ax2 = fig.add_subplot(gs[0, 3:6])
        ax3 = fig.add_subplot(gs[1, 0:3])
        ax5 = fig.add_subplot(gs[1, 3:6])

        ax4 = fig.add_subplot(gs[2, 0:2])
        ax6 = fig.add_subplot(gs[2, 2:4])
        ax_table = fig.add_subplot(gs[2, 4:6])

        extent = [min_lon, max_lon, min_lat, max_lat]
        kwargs = {'cmap': 'viridis', 'origin': 'lower', 'extent': extent, 'aspect': 'equal'}

        if np.any(np.isfinite(grid_spd_p)) and np.any(np.isfinite(grid_spd_g)):
            vmax = max(np.nanpercentile(grid_spd_p, 99), np.nanpercentile(grid_spd_g, 99))
            kwargs['vmin'], kwargs['vmax'] = 0, vmax

        # Cell 1: On-Prem
        ax1.set_title("On-Prem Wind Vector Flow", weight='bold', fontsize=12)
        if len(u_p) > 0:
            step_p = max(1, len(u_p) // 600)
            im1 = ax1.quiver(lon_p[mask_p][::step_p], lat_p[mask_p][::step_p], u_p[::step_p], v_p[::step_p],
                             spd_p[mask_p][::step_p], cmap='viridis', scale=400, width=0.003)
        ax1.set_xlim(min_lon, max_lon); ax1.set_ylim(min_lat, max_lat); ax1.grid(True, linestyle=':', alpha=0.5)

        # Cell 2: GCCS
        ax2.set_title("GCCS Wind Vector Flow", weight='bold', fontsize=12)
        if len(u_g) > 0:
            step_g = max(1, len(u_g) // 600)
            im2 = ax2.quiver(lon_g[mask_g][::step_g], lat_g[mask_g][::step_g], u_g[::step_g], v_g[::step_g],
                             spd_g[mask_g][::step_g], cmap='viridis', scale=400, width=0.003)
            div2 = make_axes_locatable(ax2)
            plt.colorbar(im2, cax=div2.append_axes("right", size="5%", pad=0.05), label='Wind Speed (m/s)')
        ax2.set_xlim(min_lon, max_lon); ax2.set_ylim(min_lat, max_lat); ax2.grid(True, linestyle=':', alpha=0.5)

        # Cell 3: Difference
        ax3.set_title("Difference (GCCS - PREM Speed)", weight='bold', fontsize=12)
        d_min = np.nanmin(valid_diffs) if len(valid_diffs) > 0 else -1
        d_max = np.nanmax(valid_diffs) if len(valid_diffs) > 0 else 1
        im3 = ax3.imshow(diff_array, cmap='coolwarm', origin='lower', extent=extent, aspect='equal', vmin=d_min, vmax=d_max)
        div3 = make_axes_locatable(ax3)
        plt.colorbar(im3, cax=div3.append_axes("right", size="5%", pad=0.05), label='Delta (m/s)')

        # Cell 4: Scatter
        ax4.set_box_aspect(1)
        ax4.set_xlabel("On-Prem Speed (m/s)", fontsize=11); ax4.set_ylabel("GCCS Speed (m/s)", fontsize=11)
        if np.count_nonzero(common) > 0:
            im4 = ax4.hexbin(grid_spd_p[common], grid_spd_g[common], gridsize=40, cmap='viridis', mincnt=1, bins='log')
            ax4.set_title(f"Speed Correlation ($R^2$: {'N/A' if r_sq_is_na else f'{r_sq:.4f}'})", weight='bold', fontsize=12)
            _add_cbar(im4, ax4, label='log10(count)')
            axis_bounds = [min(ax4.get_xlim()[0], ax4.get_ylim()[0]), max(ax4.get_xlim()[1], ax4.get_ylim()[1])]
            ax4.plot(axis_bounds, axis_bounds, color='red', linestyle='--', linewidth=1.5, alpha=0.6, zorder=5)
            ax4.set_xlim(axis_bounds); ax4.set_ylim(axis_bounds)
        else:
            ax4.set_title("Speed Correlation ($R^2$: N/A)", weight='bold', fontsize=12)
            ax4.text(0.5, 0.5, "No overlapping tracking data matrix segments found.", ha='center', va='center', color='gray')

        # Cell 5: Mask
        ax5.set_title("Flow Deviation Mask (Green=Error > 0.5m/s)", weight='bold', fontsize=12)
        ax5.imshow(mismatch_mask, cmap=mismatch_cmap, origin='lower', extent=extent, aspect='equal', vmin=0, vmax=1)

        # Cell 6: Histogram
        ax6.set_box_aspect(1)
        ax6.set_title("Distribution of Speed Delta", weight='bold', fontsize=12)
        ax6.set_xlabel("Delta Value (m/s)", fontsize=11); ax6.set_ylabel("Frequency", fontsize=11)
        if len(valid_diffs) > 0:
            ax6.hist(valid_diffs, bins=50, color='gray', edgecolor='black')
            ax6.axvline(0, color='red', linestyle='--')
        else:
            ax6.text(0.5, 0.5, "No finite delta components available.", ha='center', va='center', color='gray')

        # Cell 7: Table Component
        ax_table.axis('off')
        ax_table.set_title("Vector Statistical Metrics", weight='bold', pad=5, fontsize=12)

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
            ["Vector Metric Description", "Observed Value (Prem / GCCS)"],
            ["Binned Matrix Grid Dimensions", f"{grid_spd_p.shape}"],
            ["Total Grid Spaces", f"{grid_spd_p.size:,}"],
            ["Valid Binned Intersections", f"{num_common:,}"],
            ["Deviated Cells (>0.5m/s Margin)", f"{num_mismatches:,}"],
            ["Calculated Speed R² Fit", "N/A" if r_sq_is_na else f"{r_sq:.4f}"],
            ["Finite in Only One Fraction (XOR)", f"{only_one_frac_g:.6f}"],
            ["Binned Maximum Speed", f"{p_max_g:.4f} / {g_max_g:.4f}"],
            ["Binned Mean Speed", f"{p_mean_g:.4f} / {g_mean_g:.4f}"],
            ["Binned Median Speed", f"{p_med_g:.4f} / {g_med_g:.4f}"],
            ["Binned Minimum Speed", f"{p_min_g:.4f} / {g_min_g:.4f}"],
            ["Empty Unpopulated Grid Cells", f"{prem_nans_g:,} / {gccs_nans_g:,}"],
            ["Maximum Delta ($GCCS - PREM$)", f"{np.nanmax(valid_diffs):.4f}" if len(valid_diffs) > 0 else "N/A"],
            ["Minimum Delta ($GCCS - PREM$)", f"{np.nanmin(valid_diffs):.4f}" if len(valid_diffs) > 0 else "N/A"],
            ["Mean Delta Wind Bias", f"{np.nanmean(valid_diffs):.4f}" if len(valid_diffs) > 0 else "N/A"],
            ["Maximum Absolute Difference ($|\\Delta|$)", f"{np.max(abs_diffs):.4f}" if len(abs_diffs) > 0 else "N/A"],
            ["Minimum Absolute Difference ($|\\Delta|$)", f"{np.min(abs_diffs):.4f}" if len(abs_diffs) > 0 else "N/A"],
            ["Mean Absolute Speed Error Dispersion", f"{np.mean(abs_diffs):.4f}" if len(abs_diffs) > 0 else "N/A"]
        ]

        vector_table = ax_table.table(cellText=table_content, loc='center', cellLoc='center', colWidths=[0.60, 0.40])
        vector_table.auto_set_font_size(False)

        # INCREASED TABLE FONT AND SCALE HERE
        vector_table.set_fontsize(13)
        vector_table.scale(1.0, 2.2)

        plt.tight_layout(rect=[0, 0.04, 1, 0.96])

        b_color = 'lightgray' if r_sq_is_na else 'palegreen' if r_sq >= 0.95 else 'moccasin' if r_sq >= 0.85 else 'lightcoral'
        banner_text = f"Vector Correlation Check: {'N/A' if r_sq_is_na else f'{r_sq:.4f}'}"
        fig.text(0.5, 0.025, banner_text, ha='center', va='center', fontsize=22, weight='bold',
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
