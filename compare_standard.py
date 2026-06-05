#!/usr/bin/env python3
"""
COMPARE-PAVE: Standard Imagery Engine
=====================================
VERSION: 1.19.0 (Fast-Mode Enabled & Spatial Dimension Validations)
"""
import numpy as np
import compare_utils as utils

try:
    import cartopy.crs as ccrs
    HAS_CARTOPY = True
except ImportError:
    HAS_CARTOPY = False

def compare_standard(ds_p, ds_g, tmp_dir, pair_info, instr, prod_name, log, m_flag, fast_mode=False):
    results = []
    variables = []

    spatial_keywords = {'y', 'x', 'lat', 'lon', 'latitude', 'longitude', 'lines', 'pixels', 'rows', 'cols'}

    for var in ds_p.data_vars:
        if ds_p[var].ndim >= 2:
            var_dims = [str(d).lower() for d in ds_p[var].dims]
            if not any(kw in var_dims for kw in spatial_keywords):
                continue
            variables.append(var)

    if not variables:
        return []

    proj, extent, base_cmap = None, None, 'viridis'
    x_key = 'x' if 'x' in ds_p.variables else 'X' if 'X' in ds_p.variables else None
    y_key = 'y' if 'y' in ds_p.variables else 'Y' if 'Y' in ds_p.variables else None

    if HAS_CARTOPY and instr == "ABI" and 'goes_imager_projection' in ds_p.variables:
        try:
            gip = ds_p['goes_imager_projection']
            h_sat = gip.attrs.get('perspective_point_height', 35786023.0)
            lon_0 = gip.attrs.get('longitude_of_projection_origin', -75.0)
            sweep = gip.attrs.get('sweep_angle_axis', 'x')
            proj = ccrs.Geostationary(central_longitude=lon_0, satellite_height=h_sat, sweep_axis=sweep)
            if x_key and y_key:
                x_vals, y_vals = ds_p[x_key].values * h_sat, ds_p[y_key].values * h_sat
                extent = [x_vals.min(), x_vals.max(), y_vals.min(), y_vals.max()]
        except Exception: pass

    if instr == 'SUVI': base_cmap = utils.get_suvi_cmap(prod_name)

    for var in variables:
        try:
            if var not in ds_g.data_vars: continue

            data_p = ds_p[var].values.astype(np.float32)
            data_g = ds_g[var].values.astype(np.float32)
            if data_p.shape != data_g.shape: continue

            fill_val = ds_p[var].attrs.get('_FillValue')
            if fill_val is not None: data_p[data_p == fill_val] = np.nan
            fill_val_g = ds_g[var].attrs.get('_FillValue')
            if fill_val_g is not None: data_g[data_g == fill_val_g] = np.nan

            var_attrs = ds_p[var].attrs
            is_bitset = any(k in var_attrs for k in ['flag_values', 'flag_masks', 'flag_meanings'])
            if not is_bitset:
                v_lower, l_name, s_name = var.lower(), var_attrs.get('long_name', '').lower(), var_attrs.get('standard_name', '').lower()
                for kw in ['dqf', 'mask', 'dif', 'pqi', 'flag', 'bit']:
                    if kw in v_lower or kw in l_name or kw in s_name:
                        is_bitset = True; break

            metrics = utils.execute_visual_comparison(
                data_p, data_g, var, tmp_dir, pair_info,
                "Standard", proj, extent, 'upper', base_cmap, is_bitset=is_bitset, fast_mode=fast_mode
            )

            for m in metrics: results.append({'var': var, 'm': m['Metric'], 'v': m['Value']})
        except Exception: continue

    return results
