#!/usr/bin/env python3
"""
COMPARE-PAVE: Lightweight Science Analysis Engine
=================================================
VERSION: 1.6.3 (Cartopy Axes Alignment Fix)
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
from scipy.stats import pearsonr
from mpl_toolkits.axes_grid1 import make_axes_locatable

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

from pave_utils import Logger, setup_interrupt_handler, resolve_meta

warnings.filterwarnings("ignore")
plt.switch_backend('Agg')

GOES_REGEX = re.compile(r"OR_(?P<dsn>.*?)_(?P<sat>G1[89]).*?s(?P<start>\d{14})")

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


# --- CORE COMPARISON ENGINE ---

def _execute_standard_comparison(data_p, data_g, var, tmp_dir, pair_info, strategy_label, proj=None, extent=None, origin='upper', cmap='viridis'):
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
        sample_p = sample_g = None

    plot_p = data_p[..., 0] if data_p.ndim > 2 else data_p
    plot_g = data_g[..., 0] if data_g.ndim > 2 else data_g
    plot_diff = plot_g - plot_p

    h, w = plot_p.shape
    data_ratio = h / w if w > 0 else 1
    valid_diffs = plot_diff[np.isfinite(plot_diff)]

    is_geo = HAS_CARTOPY and proj is not None
    kwargs = {'cmap': cmap, 'aspect': 'equal', 'origin': origin}

    valid_p = plot_p[np.isfinite(plot_p)]
    valid_g = plot_g[np.isfinite(plot_g)]
    if len(valid_p) > 0 and len(valid_g) > 0:
        kwargs['vmin'] = min(np.nanpercentile(valid_p, 1), np.nanpercentile(valid_g, 1))
        kwargs['vmax'] = max(np.nanpercentile(valid_p, 99), np.nanpercentile(valid_g, 99))

    diff_vmax = np.nanmax(np.abs(valid_diffs)) if len(valid_diffs) > 0 else 1
    diff_kwargs = {'cmap': 'bwr', 'aspect': 'equal', 'origin': origin, 'vmin': -diff_vmax, 'vmax': diff_vmax}

    if extent is not None:
        kwargs['extent'] = extent
        diff_kwargs['extent'] = extent
    if is_geo:
        kwargs['transform'] = proj
        diff_kwargs['transform'] = proj

    def _setup_geo_ax(ax):
        if is_geo:
            try:
                ax.add_feature(cfeature.COASTLINE, color='cyan', linewidth=0.5)
                ax.add_feature(cfeature.STATES, edgecolor='cyan', linewidth=0.5, linestyle=':')
            except Exception: pass

    def _add_cbar(im, ax):
        if is_geo: return plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
        div = make_axes_locatable(ax)
        cax = div.append_axes("right", size="5%", pad=0.05)
        return plt.colorbar(im, cax=cax)

    # --- 1. GENERATE INDIVIDUAL STANDALONE PLOTS ---
    ind_h = 10 * data_ratio

    fig_i = plt.figure(figsize=(10, ind_h))
    ax_i = fig_i.add_subplot(111, projection=proj) if is_geo else fig_i.add_subplot(111)
    ax_i.set_title(f"{var} - PREM\n{pair_info}", fontsize=9)
    _setup_geo_ax(ax_i)
    im_i = ax_i.imshow(plot_p, **kwargs)
    _add_cbar(im_i, ax_i)
    fig_i.savefig(tmp_dir / f"{var}_PREM.png", dpi=90, bbox_inches='tight')
    plt.close(fig_i)

    fig_i = plt.figure(figsize=(10, ind_h))
    ax_i = fig_i.add_subplot(111, projection=proj) if is_geo else fig_i.add_subplot(111)
    ax_i.set_title(f"{var} - GCCS\n{pair_info}", fontsize=9)
    _setup_geo_ax(ax_i)
    im_i = ax_i.imshow(plot_g, **kwargs)
    _add_cbar(im_i, ax_i)
    fig_i.savefig(tmp_dir / f"{var}_GCCS.png", dpi=90, bbox_inches='tight')
    plt.close(fig_i)

    fig_i = plt.figure(figsize=(10, ind_h))
    ax_i = fig_i.add_subplot(111, projection=proj) if is_geo else fig_i.add_subplot(111)
    ax_i.set_title(f"{var} - Difference (GCCS - PREM)\n{pair_info}", fontsize=9)
    _setup_geo_ax(ax_i)
    im_i = ax_i.imshow(plot_diff, **diff_kwargs)
    _add_cbar(im_i, ax_i)
    fig_i.savefig(tmp_dir / f"{var}_DIFF.png", dpi=90, bbox_inches='tight')
    plt.close(fig_i)

    fig_i, ax_i = plt.subplots(figsize=(10, 6))
    ax_i.set_title(f"{var} - Histogram of Differences\n{pair_info}", fontsize=9)
    if len(valid_diffs) > 0:
        ax_i.hist(valid_diffs, bins=50, color='dimgray', edgecolor='black')
        ax_i.axvline(0, color='red', linestyle='--', linewidth=1.5, label='Zero Bias')
        ax_i.set_xlabel("Difference (GCCS - PREM)")
        ax_i.set_ylabel("Frequency")
        ax_i.legend()
    else:
        ax_i.text(0.5, 0.5, "No overlapping valid data", ha='center', va='center')
    fig_i.savefig(tmp_dir / f"{var}_HIST.png", dpi=90, bbox_inches='tight')
    plt.close(fig_i)


    # --- 2. GENERATE COMBINED 2x2 GRID ---
    fig_width = 18.0
    w_ax = (fig_width - 2.0) / 2.2
    h_ax = w_ax * data_ratio
    fig_height = max(min(2 * h_ax + 3.0, 40), 8)

    fig = plt.figure(figsize=(fig_width, fig_height))
    plt.suptitle(f"Variable: {var}\n{pair_info} ({strategy_label})", fontsize=10, y=0.97)

    ax1 = fig.add_subplot(221, projection=proj) if is_geo else fig.add_subplot(221)
    ax2 = fig.add_subplot(222, projection=proj) if is_geo else fig.add_subplot(222)
    ax3 = fig.add_subplot(223, projection=proj) if is_geo else fig.add_subplot(223)
    ax4 = fig.add_subplot(224)

    ax1.set_title("PREM")
    _setup_geo_ax(ax1)
    im1 = ax1.imshow(plot_p, **kwargs)
    _add_cbar(im1, ax1)

    ax2.set_title("GCCS")
    _setup_geo_ax(ax2)
    im2 = ax2.imshow(plot_g, **kwargs)
    _add_cbar(im2, ax2)

    ax3.set_title("Difference (GCCS - PREM)")
    _setup_geo_ax(ax3)
    im3 = ax3.imshow(plot_diff, **diff_kwargs)
    _add_cbar(im3, ax3)

    ax4.set_title("Histogram of Differences")
    if len(valid_diffs) > 0:
        ax4.hist(valid_diffs, bins=50, color='dimgray', edgecolor='black')
        ax4.axvline(0, color='red', linestyle='--', linewidth=1.5, label='Zero Bias')
        ax4.set_xlabel("Difference (GCCS - PREM)")
        ax4.set_ylabel("Frequency")
        ax4.legend()
    else:
        ax4.text(0.5, 0.5, "No overlapping valid data", ha='center', va='center')

    # FIX: Use a standalone Dummy Mappable so Cartopy axes aren't shifted by the Histogram
    if is_geo:
        sm = plt.cm.ScalarMappable(cmap='viridis')
        sm.set_array([])
        cbar = plt.colorbar(sm, ax=ax4, fraction=0.046, pad=0.04)
        cbar.ax.set_visible(False)
    else:
        div4 = make_axes_locatable(ax4)
        cax4 = div4.append_axes("right", size="5%", pad=0.05)
        cax4.axis('off')

    plt.tight_layout(rect=[0, 0.03, 1, 0.95])
    plt.savefig(tmp_dir / f"{var}_comparison.png", dpi=90)
    plt.close(fig)

    del data_p, data_g, mask_p, mask_g, common, plot_p, plot_g, plot_diff, valid_diffs
    return [{'Metric': 'r-squared correlation', 'Value': r_sq}, {'Metric': 'finite_in_only_one_fraction', 'Value': f1_frac}]

# --- ROUTING STRATEGIES ---

def _get_coords_for_var(ds, var_name):
    prefix = var_name.split('_')[0] + '_' if '_' in var_name else ''
    if f"{prefix}lat" in ds.variables and f"{prefix}lon" in ds.variables: return f"{prefix}lat", f"{prefix}lon"
    if "lat" in ds.variables and "lon" in ds.variables: return "lat", "lon"
    return next((v for v in ds.variables if 'lat' in v.lower()), None), next((v for v in ds.variables if 'lon' in v.lower()), None)

def _compare_standard(ds_p, ds_g, var, tmp_dir, pair_info, instr, prod_name):
    data_p = ds_p[var].values.astype(np.float32)
    data_g = ds_g[var].values.astype(np.float32)

    proj, extent, cmap = None, None, 'viridis'

    if HAS_CARTOPY and instr == "ABI":
        try:
            if 'goes_imager_projection' in ds_p.variables:
                gip = ds_p['goes_imager_projection']
                h = gip.attrs.get('perspective_point_height', 35786023.0)
                lon_0 = gip.attrs.get('longitude_of_projection_origin', -75.0)
                sweep = gip.attrs.get('sweep_angle_axis', 'x')
                proj = ccrs.Geostationary(central_longitude=lon_0, satellite_height=h, sweep_axis=sweep)

                x = ds_p['x'].values * h
                y = ds_p['y'].values * h
                extent = [x.min(), x.max(), y.min(), y.max()]
        except Exception: pass

    if instr == 'SUVI':
        cmap = get_suvi_cmap(prod_name)

    return _execute_standard_comparison(data_p, data_g, var, tmp_dir, pair_info, "Standard", proj=proj, extent=extent, origin='upper', cmap=cmap)

def _compare_sparse(ds_p, ds_g, var, tmp_dir, pair_info, instr, prod_name):
    lat_var, lon_var = _get_coords_for_var(ds_p, var)
    if not lat_var or not lon_var: raise ValueError(f"Could not locate coordinate variables for {var}")
    if ds_p[lat_var].size != ds_p[var].size: raise ValueError(f"Size mismatch: Data {ds_p[var].size} vs Coords {ds_p[lat_var].size}")

    coords_p = np.column_stack((ds_p[lat_var].values.ravel(), ds_p[lon_var].values.ravel()))
    coords_g = np.column_stack((ds_g[lat_var].values.ravel(), ds_g[lon_var].values.ravel()))
    vals_p = ds_p[var].values.astype(np.float32).ravel()
    vals_g = ds_g[var].values.astype(np.float32).ravel()

    valid_p = np.isfinite(coords_p).all(axis=1) & np.isfinite(vals_p)
    valid_g = np.isfinite(coords_g).all(axis=1) & np.isfinite(vals_g)
    coords_p, vals_p = coords_p[valid_p], vals_p[valid_p]
    coords_g, vals_g = coords_g[valid_g], vals_g[valid_g]

    if len(coords_p) == 0 and len(coords_g) == 0:
        return _execute_standard_comparison(np.array([[np.nan]]), np.array([[np.nan]]), var, tmp_dir, pair_info, "Sparse Gridded (Empty)")

    min_lat = min(np.nanmin(coords_p[:, 0]) if len(coords_p) else 90, np.nanmin(coords_g[:, 0]) if len(coords_g) else 90)
    max_lat = max(np.nanmax(coords_p[:, 0]) if len(coords_p) else -90, np.nanmax(coords_g[:, 0]) if len(coords_g) else -90)
    min_lon = min(np.nanmin(coords_p[:, 1]) if len(coords_p) else 180, np.nanmin(coords_g[:, 1]) if len(coords_g) else 180)
    max_lon = max(np.nanmax(coords_p[:, 1]) if len(coords_p) else -180, np.nanmax(coords_g[:, 1]) if len(coords_g) else -180)

    if max_lat - min_lat < 1e-4: min_lat -= 0.1; max_lat += 0.1
    if max_lon - min_lon < 1e-4: min_lon -= 0.1; max_lon += 0.1

    bins_lon = 500
    lon_range, lat_range = max_lon - min_lon, max_lat - min_lat
    bins_lat = max(min(int(bins_lon * (lat_range / lon_range)), 2000), 100) if lon_range > 0 else bins_lon

    lon_edges, lat_edges = np.linspace(min_lon, max_lon, bins_lon + 1), np.linspace(min_lat, max_lat, bins_lat + 1)

    counts_p, _, _ = np.histogram2d(coords_p[:, 1], coords_p[:, 0], bins=[lon_edges, lat_edges])
    sums_p, _, _ = np.histogram2d(coords_p[:, 1], coords_p[:, 0], bins=[lon_edges, lat_edges], weights=vals_p)
    counts_g, _, _ = np.histogram2d(coords_g[:, 1], coords_g[:, 0], bins=[lon_edges, lat_edges])
    sums_g, _, _ = np.histogram2d(coords_g[:, 1], coords_g[:, 0], bins=[lon_edges, lat_edges], weights=vals_g)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        grid_p = np.where(counts_p > 0, sums_p / counts_p, np.nan).T
        grid_g = np.where(counts_g > 0, sums_g / counts_g, np.nan).T

    proj = ccrs.PlateCarree() if HAS_CARTOPY else None
    extent = [min_lon, max_lon, min_lat, max_lat]

    return _execute_standard_comparison(grid_p, grid_g, var, tmp_dir, pair_info, "Sparse Gridded", proj=proj, extent=extent, origin='lower')

def _compare_sparse_vectors(ds_p, ds_g, vec_type, var1, var2, tmp_dir, pair_info, instr, prod_name):
    lat_var, lon_var = _get_coords_for_var(ds_p, var1)
    if not lat_var or not lon_var: return

    coords_p = np.column_stack((ds_p[lat_var].values.ravel(), ds_p[lon_var].values.ravel()))
    coords_g = np.column_stack((ds_g[lat_var].values.ravel(), ds_g[lon_var].values.ravel()))

    val1_p, val2_p = ds_p[var1].values.astype(np.float32).ravel(), ds_p[var2].values.astype(np.float32).ravel()
    val1_g, val2_g = ds_g[var1].values.astype(np.float32).ravel(), ds_g[var2].values.astype(np.float32).ravel()

    if vec_type == 'speed_dir':
        speed_p, dir_p = val1_p, val2_p
        speed_g, dir_g = val1_g, val2_g
        u_p, v_p = -speed_p * np.sin(np.radians(dir_p)), -speed_p * np.cos(np.radians(dir_p))
        u_g, v_g = -speed_g * np.sin(np.radians(dir_g)), -speed_g * np.cos(np.radians(dir_g))
    else:
        u_p, v_p = val1_p, val2_p
        u_g, v_g = val1_g, val2_g
        speed_p, speed_g = np.sqrt(u_p**2 + v_p**2), np.sqrt(u_g**2 + v_g**2)

    valid_p = np.isfinite(coords_p).all(axis=1) & np.isfinite(u_p) & np.isfinite(v_p)
    valid_g = np.isfinite(coords_g).all(axis=1) & np.isfinite(u_g) & np.isfinite(v_g)
    coords_p, u_p, v_p, speed_p = coords_p[valid_p], u_p[valid_p], v_p[valid_p], speed_p[valid_p]
    coords_g, u_g, v_g, speed_g = coords_g[valid_g], u_g[valid_g], v_g[valid_g], speed_g[valid_g]

    if len(coords_p) == 0 and len(coords_g) == 0: return

    min_lat = min(np.nanmin(coords_p[:, 0]) if len(coords_p) else 90, np.nanmin(coords_g[:, 0]) if len(coords_g) else 90)
    max_lat = max(np.nanmax(coords_p[:, 0]) if len(coords_p) else -90, np.nanmax(coords_g[:, 0]) if len(coords_g) else -90)
    min_lon = min(np.nanmin(coords_p[:, 1]) if len(coords_p) else 180, np.nanmin(coords_g[:, 1]) if len(coords_g) else 180)
    max_lon = max(np.nanmax(coords_p[:, 1]) if len(coords_p) else -180, np.nanmax(coords_g[:, 1]) if len(coords_g) else -180)

    bins_lon = 45
    lon_range, lat_range = max_lon - min_lon if max_lon > min_lon else 1, max_lat - min_lat if max_lat > min_lat else 1
    bins_lat = max(min(int(bins_lon * (lat_range / lon_range)), 80), 10)

    lon_edges, lat_edges = np.linspace(min_lon, max_lon, bins_lon + 1), np.linspace(min_lat, max_lat, bins_lat + 1)
    X, Y = np.meshgrid((lon_edges[:-1] + lon_edges[1:])/2, (lat_edges[:-1] + lat_edges[1:])/2)

    counts_p, _, _ = np.histogram2d(coords_p[:, 1], coords_p[:, 0], bins=[lon_edges, lat_edges])
    u_sums_p, _, _ = np.histogram2d(coords_p[:, 1], coords_p[:, 0], bins=[lon_edges, lat_edges], weights=u_p)
    v_sums_p, _, _ = np.histogram2d(coords_p[:, 1], coords_p[:, 0], bins=[lon_edges, lat_edges], weights=v_p)
    spd_sums_p, _, _ = np.histogram2d(coords_p[:, 1], coords_p[:, 0], bins=[lon_edges, lat_edges], weights=speed_p)

    counts_g, _, _ = np.histogram2d(coords_g[:, 1], coords_g[:, 0], bins=[lon_edges, lat_edges])
    u_sums_g, _, _ = np.histogram2d(coords_g[:, 1], coords_g[:, 0], bins=[lon_edges, lat_edges], weights=u_g)
    v_sums_g, _, _ = np.histogram2d(coords_g[:, 1], coords_g[:, 0], bins=[lon_edges, lat_edges], weights=v_g)
    spd_sums_g, _, _ = np.histogram2d(coords_g[:, 1], coords_g[:, 0], bins=[lon_edges, lat_edges], weights=speed_g)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        u_grid_p = np.where(counts_p > 0, u_sums_p / counts_p, np.nan).T
        v_grid_p = np.where(counts_p > 0, v_sums_p / counts_p, np.nan).T
        spd_grid_p = np.where(counts_p > 0, spd_sums_p / counts_p, np.nan).T
        u_grid_g = np.where(counts_g > 0, u_sums_g / counts_g, np.nan).T
        v_grid_g = np.where(counts_g > 0, v_sums_g / counts_g, np.nan).T
        spd_grid_g = np.where(counts_g > 0, spd_sums_g / counts_g, np.nan).T

    u_diff, v_diff, spd_diff = u_grid_g - u_grid_p, v_grid_g - v_grid_p, spd_grid_g - spd_grid_p

    out_base = f"wind_vectors_{'uv' if vec_type == 'uv' else 'speed_dir'}"
    data_ratio = lat_range / lon_range
    var_label = f"Wind Vectors ({var1} & {var2})"

    spd_vmin = min(np.nanpercentile(spd_grid_p[np.isfinite(spd_grid_p)], 1) if not np.isnan(spd_grid_p).all() else 0,
                   np.nanpercentile(spd_grid_g[np.isfinite(spd_grid_g)], 1) if not np.isnan(spd_grid_g).all() else 0)
    spd_vmax = max(np.nanpercentile(spd_grid_p[np.isfinite(spd_grid_p)], 99) if not np.isnan(spd_grid_p).all() else 1,
                   np.nanpercentile(spd_grid_g[np.isfinite(spd_grid_g)], 99) if not np.isnan(spd_grid_g).all() else 1)

    diff_vmax = np.nanmax(np.abs(spd_diff)) if not np.isnan(spd_diff).all() else 1
    valid_diffs = spd_diff[np.isfinite(spd_diff)]

    is_geo = HAS_CARTOPY
    proj = ccrs.PlateCarree() if is_geo else None

    def _setup_geo_ax(ax):
        ax.set_facecolor('#f4f4f4')
        if is_geo:
            try:
                ax.add_feature(cfeature.COASTLINE, color='black', linewidth=0.5)
                ax.add_feature(cfeature.STATES, edgecolor='black', linewidth=0.5, linestyle=':')
                ax.set_extent([min_lon, max_lon, min_lat, max_lat], crs=proj)
            except Exception: pass
        else:
            ax.set_xlim(min_lon, max_lon)
            ax.set_ylim(min_lat, max_lat)
            ax.set_aspect('equal')

    def _add_cbar(im, ax, label):
        if is_geo: return plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04, label=label)
        div = make_axes_locatable(ax)
        cax = div.append_axes("right", size="5%", pad=0.05)
        return plt.colorbar(im, cax=cax, label=label)

    q_kwargs = {'transform': proj} if is_geo else {}

    ind_h = 10 * data_ratio

    fig_i = plt.figure(figsize=(10, ind_h))
    ax_i = fig_i.add_subplot(111, projection=proj) if is_geo else fig_i.add_subplot(111)
    _setup_geo_ax(ax_i)
    ax_i.set_title(f"{var_label} - PREM\n{pair_info}", fontsize=9)
    q_i = ax_i.quiver(X, Y, u_grid_p, v_grid_p, spd_grid_p, cmap='viridis', **q_kwargs)
    q_i.set_clim(spd_vmin, spd_vmax)
    _add_cbar(q_i, ax_i, 'Speed')
    fig_i.savefig(tmp_dir / f"{out_base}_PREM.png", dpi=90, bbox_inches='tight')
    plt.close(fig_i)

    fig_i = plt.figure(figsize=(10, ind_h))
    ax_i = fig_i.add_subplot(111, projection=proj) if is_geo else fig_i.add_subplot(111)
    _setup_geo_ax(ax_i)
    ax_i.set_title(f"{var_label} - GCCS\n{pair_info}", fontsize=9)
    q_i = ax_i.quiver(X, Y, u_grid_g, v_grid_g, spd_grid_g, cmap='viridis', **q_kwargs)
    q_i.set_clim(spd_vmin, spd_vmax)
    _add_cbar(q_i, ax_i, 'Speed')
    fig_i.savefig(tmp_dir / f"{out_base}_GCCS.png", dpi=90, bbox_inches='tight')
    plt.close(fig_i)

    fig_i = plt.figure(figsize=(10, ind_h))
    ax_i = fig_i.add_subplot(111, projection=proj) if is_geo else fig_i.add_subplot(111)
    _setup_geo_ax(ax_i)
    ax_i.set_title(f"{var_label} - Difference (GCCS - PREM)\n{pair_info}", fontsize=9)
    q_i = ax_i.quiver(X, Y, u_diff, v_diff, spd_diff, cmap='bwr', **q_kwargs)
    q_i.set_clim(-diff_vmax, diff_vmax)
    _add_cbar(q_i, ax_i, 'Speed Diff')
    fig_i.savefig(tmp_dir / f"{out_base}_DIFF.png", dpi=90, bbox_inches='tight')
    plt.close(fig_i)

    fig_width = 18.0
    w_ax = (fig_width - 2.0) / 2.2
    h_ax = w_ax * data_ratio
    fig_height = max(min(2 * h_ax + 3.0, 40), 8)

    fig = plt.figure(figsize=(fig_width, fig_height))
    plt.suptitle(f"Variable: {var_label}\n{pair_info} (Sparse Gridded Flow)", fontsize=10, y=0.97)

    ax1 = fig.add_subplot(221, projection=proj) if is_geo else fig.add_subplot(221)
    ax2 = fig.add_subplot(222, projection=proj) if is_geo else fig.add_subplot(222)
    ax3 = fig.add_subplot(223, projection=proj) if is_geo else fig.add_subplot(223)
    ax4 = fig.add_subplot(224)

    _setup_geo_ax(ax1)
    ax1.set_title("PREM Vectors")
    q1 = ax1.quiver(X, Y, u_grid_p, v_grid_p, spd_grid_p, cmap='viridis', **q_kwargs)
    q1.set_clim(spd_vmin, spd_vmax)
    _add_cbar(q1, ax1, 'Speed')

    _setup_geo_ax(ax2)
    ax2.set_title("GCCS Vectors")
    q2 = ax2.quiver(X, Y, u_grid_g, v_grid_g, spd_grid_g, cmap='viridis', **q_kwargs)
    q2.set_clim(spd_vmin, spd_vmax)
    _add_cbar(q2, ax2, 'Speed')

    _setup_geo_ax(ax3)
    ax3.set_title("Vector Difference (GCCS - PREM)")
    q3 = ax3.quiver(X, Y, u_diff, v_diff, spd_diff, cmap='bwr', **q_kwargs)
    q3.set_clim(-diff_vmax, diff_vmax)
    _add_cbar(q3, ax3, 'Speed Diff')

    ax4.set_title("Histogram of Speed Differences")
    if len(valid_diffs) > 0:
        ax4.hist(valid_diffs, bins=50, color='dimgray', edgecolor='black')
        ax4.axvline(0, color='red', linestyle='--', linewidth=1.5)
        ax4.set_xlabel("Difference (GCCS - PREM)")
        ax4.set_ylabel("Frequency")
    else:
        ax4.text(0.5, 0.5, "No overlapping valid data", ha='center', va='center')

    # FIX: Dummy Mappable to align Histogram properly
    if is_geo:
        sm = plt.cm.ScalarMappable(cmap='viridis')
        sm.set_array([])
        cbar = plt.colorbar(sm, ax=ax4, fraction=0.046, pad=0.04)
        cbar.ax.set_visible(False)
    else:
        div4 = make_axes_locatable(ax4)
        cax4 = div4.append_axes("right", size="5%", pad=0.05)
        cax4.axis('off')

    plt.tight_layout(rect=[0, 0.03, 1, 0.95])
    plt.savefig(tmp_dir / f"{out_base}_comparison.png", dpi=90)
    plt.close(fig)


def _compare_timeseries(ds_p, ds_g, var, tmp_dir, pair_info, instr, prod_name):
    time_var = next((v for v in ds_p.variables if 'time' in v.lower()), None)
    if not time_var:
        raise ValueError("Missing time variable for timeseries comparison.")

    df_p = pd.DataFrame({'time': ds_p[time_var].values, 'P_Val': ds_p[var].values}).dropna().sort_values('time')
    df_g = pd.DataFrame({'time': ds_g[time_var].values, 'G_Val': ds_g[var].values}).dropna().sort_values('time')

    merged = pd.merge_asof(df_g, df_p, on='time', direction='nearest', tolerance=pd.Timedelta('1s')).dropna()

    mismatch_count = len(df_g) - len(merged) + len(df_p) - len(merged)
    total_points = len(df_g) + len(df_p)
    f1_frac = mismatch_count / total_points if total_points > 0 else 0

    r_sq = 0.0
    if len(merged) > 1:
        r_val, _ = pearsonr(merged['P_Val'], merged['G_Val'])
        r_sq = r_val ** 2
        merged['diff'] = merged['G_Val'] - merged['P_Val']

        fig_i, ax_i = plt.subplots(figsize=(12, 4))
        ax_i.plot(df_p['time'], df_p['P_Val'], color='blue', label='PREM')
        ax_i.set_title(f"{var} - PREM\n{pair_info}", fontsize=9)
        ax_i.set_ylabel(var)
        ax_i.legend()
        fig_i.savefig(tmp_dir / f"{var}_PREM.png", dpi=90, bbox_inches='tight')
        plt.close(fig_i)

        fig_i, ax_i = plt.subplots(figsize=(12, 4))
        ax_i.plot(df_g['time'], df_g['G_Val'], color='green', label='GCCS')
        ax_i.set_title(f"{var} - GCCS\n{pair_info}", fontsize=9)
        ax_i.set_ylabel(var)
        ax_i.legend()
        fig_i.savefig(tmp_dir / f"{var}_GCCS.png", dpi=90, bbox_inches='tight')
        plt.close(fig_i)

        fig_c = plt.figure(figsize=(12, 4))
        plt.plot(merged['time'], merged['diff'], label='Diff (G-P)', color='red', linewidth=1)
        plt.axhline(0, color='black', linestyle='--')
        plt.title(f"Variable: {var} - Difference\n{pair_info} (Time-Series)", fontsize=8)
        plt.legend()
        plt.savefig(tmp_dir / f"{var}_DIFF.png", dpi=90, bbox_inches='tight')
        plt.savefig(tmp_dir / f"{var}_comparison.png", dpi=90, bbox_inches='tight')
        plt.close(fig_c)

    return [{'Metric': 'r-squared correlation', 'Value': r_sq}, {'Metric': 'finite_in_only_one_fraction', 'Value': f1_frac}]


# --- ENGINE ORCHESTRATOR ---

def process_file_pair(p_file, g_file, dest_root, prem_root, log):
    results = []
    pair_info = f"{p_file.name} <-> {g_file.name}"

    m = GOES_REGEX.search(p_file.name)
    if not m:
        log.warn(f"Metadata Error: {pair_info}")
        return None

    prod_name = m.group('dsn')
    meta = {'Product': prod_name, 'Sat': m.group('sat'), 'Start': m.group('start')}

    global_meta = resolve_meta(prod_name)
    comp_strategy = global_meta.get('comp_type', 'standard').lower()
    instr = global_meta.get('instr', 'ABI')

    if comp_strategy not in ['sparse', 'timeseries']:
        comp_strategy = 'standard'

    log.debug(f"[{comp_strategy.upper()}] Opened File: {p_file.name}")

    rel_dir = p_file.relative_to(prem_root).parent
    final_dir = dest_root / rel_dir / p_file.stem
    tmp_dir = dest_root / rel_dir / f"{p_file.stem}.partial"

    if final_dir.exists():
        log.debug(f"[{comp_strategy.upper()}] Skipping (Already Exists): {p_file.name}")
        return []

    if tmp_dir.exists(): shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    try:
        with xr.open_dataset(p_file, cache=False) as ds_p, \
             xr.open_dataset(g_file, cache=False) as ds_g:

            vector_tasks = []

            if comp_strategy == 'sparse':
                coord_vars = [v for v in ds_p.variables if 'lat' in v.lower() or 'lon' in v.lower()]
                variables = [v for v in ds_p.data_vars if ds_p[v].ndim == 1 and v not in coord_vars]
                if not variables: log.warn(f"[{p_file.name}] No suitable 1D data variables found.")

                for var in variables:
                    v_low = var.lower()
                    if 'wind_speed' in v_low:
                        dir_cand = var.replace('speed', 'direction').replace('Speed', 'Direction')
                        if dir_cand in variables: vector_tasks.append(('speed_dir', var, dir_cand))
                    elif 'u_component' in v_low:
                        v_cand = var.replace('u_component', 'v_component').replace('U_component', 'V_component')
                        if v_cand in variables: vector_tasks.append(('uv', var, v_cand))

            elif comp_strategy == 'timeseries':
                time_v = next((v for v in ds_p.variables if 'time' in v.lower()), None)
                variables = [v for v in ds_p.data_vars if ds_p[v].ndim == 1 and v != time_v]
                if not variables: log.warn(f"[{p_file.name}] No suitable 1D data variables found.")

            else:
                variables = [v for v in ds_p.data_vars if ds_p[v].ndim >= 2]
                if not variables: log.warn(f"[{p_file.name}] No suitable 2D+ data variables found.")

            for var in variables:
                log.debug(f"  -> Target [{var}] routed to {comp_strategy.upper()}")
                try:
                    if comp_strategy == 'sparse':
                        lat_v, lon_v = _get_coords_for_var(ds_p, var)
                        log.debug(f"    -> Mapped Coords: ({lat_v}, {lon_v})")
                        var_metrics = _compare_sparse(ds_p, ds_g, var, tmp_dir, pair_info, instr, prod_name)

                    elif comp_strategy == 'timeseries':
                        var_metrics = _compare_timeseries(ds_p, ds_g, var, tmp_dir, pair_info, instr, prod_name)

                    else:
                        var_metrics = _compare_standard(ds_p, ds_g, var, tmp_dir, pair_info, instr, prod_name)

                    for m_dict in var_metrics:
                        results.append({**meta, 'Variable': var, 'Metric': m_dict['Metric'], 'Value': m_dict['Value']})
                except MemoryError:
                    results.append({**meta, 'Variable': var, 'Metric': 'r-squared correlation', 'Value': np.nan})
                    log.warn(f"[{var}] MemoryError - Array too large to process.")
                except Exception as ve:
                    log.warn(f"[{var}] Failed processing: {str(ve)}")
                    continue
                finally:
                    gc.collect()

            for v_type, v1, v2 in vector_tasks:
                try:
                    log.debug(f"  -> Generating Dedicated Vector Flow Plots for: {v1} & {v2}")
                    _compare_sparse_vectors(ds_p, ds_g, v_type, v1, v2, tmp_dir, pair_info, instr, prod_name)
                except Exception as ve:
                    log.warn(f"Failed to generate vector plot for {v1}/{v2}: {str(ve)}")

        with open(tmp_dir / "stats.csv", 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['Product', 'Variable', 'Sat', 'Metric', 'Start', 'Value'])
            writer.writeheader()
            writer.writerows(results)

        tmp_dir.rename(final_dir)
        return results

    except Exception as e:
        if tmp_dir.exists(): shutil.rmtree(tmp_dir)
        log.warn(f"FAILED FILE: {pair_info} -> {str(e)}")
        return None
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
        for p_dir in self.dest_root.rglob("*.partial"):
            if p_dir.is_dir(): shutil.rmtree(p_dir)

    def _write_aggregated_summary(self):
        all_raw_data = []
        self.log.info("Collecting and reformatting cumulative statistics...")
        for local_csv in self.dest_root.rglob("stats.csv"):
            try:
                with open(local_csv, 'r') as f:
                    reader = csv.DictReader(f)
                    for row in reader: all_raw_data.append(row)
            except Exception: continue

        if not all_raw_data:
            self.log.warn("No statistical records found to aggregate.")
            return

        df = pd.DataFrame(all_raw_data).sort_values('Start')
        df['Value'] = pd.to_numeric(df['Value'], errors='coerce')

        header = "Product,Variable,Sat,Metric,Count,Min,Max,Mean,Median,NaN,T1,V1,T2,V2,T3,V3...\n"
        self.stats_root.mkdir(parents=True, exist_ok=True)

        with open(self.summary_csv, 'w') as f:
            f.write(header)
            groups = df.groupby(['Product', 'Variable', 'Metric', 'Sat'], sort=False)
            for (prod, var, metric, sat), group in groups:
                vals = group['Value'].dropna()
                meta = [
                    prod, var, sat, metric, str(len(group)),
                    f"{vals.min():.8f}" if not vals.empty else "NaN",
                    f"{vals.max():.8f}" if not vals.empty else "NaN",
                    f"{vals.mean():.8f}" if not vals.empty else "NaN",
                    f"{vals.median():.8f}" if not vals.empty else "NaN",
                    str(group['Value'].isna().sum())
                ]
                ts_pairs = []
                for _, row in group.iterrows():
                    ts_pairs.append(str(row['Start']))
                    ts_pairs.append(str(row['Value']))
                f.write(",".join(meta) + "," + ",".join(ts_pairs) + "\n")

    def execute(self):
        self.dest_root.mkdir(parents=True, exist_ok=True)
        self._cleanup_partial_runs()

        nc_files = list(self.prem_root.rglob("*.nc"))
        tasks, skipped = [], 0

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

        if skipped > 0: self.log.info(f"Compare-PAVE: Resuming. Skipping {skipped} existing results.")
        if tasks:
            self.log.info(f"Compare-PAVE: Processing {len(tasks)} file pairs.")
            with ProcessPoolExecutor(max_workers=self.threads) as executor:
                futures = {executor.submit(process_file_pair, p, g, self.dest_root, self.prem_root, self.log): p.name for p, g in tasks}
                for fut in as_completed(futures):
                    try: _ = fut.result()
                    except Exception as e: self.log.error(f"Critical Worker Error: {e}")

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
    log = Logger(lvl); setup_interrupt_handler(log)
    PaveComparator(args, log).execute()

if __name__ == "__main__": main()
