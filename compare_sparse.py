#!/usr/bin/env python3
"""
COMPARE-PAVE: Sparse & Vector Engine
====================================
VERSION: 1.18.0 (Fast-Mode Enabled & Bitset Routing)
"""
import numpy as np
import compare_utils as utils

try:
    import cartopy.crs as ccrs
    HAS_CARTOPY = True
except ImportError:
    HAS_CARTOPY = False

def compare_sparse(ds_p, ds_g, tmp_dir, pair_info, instr, prod_name, log, m_flag, fast_mode=False):
    results = []

    coord_vars = [v for v in ds_p.variables if 'lat' in v.lower() or 'lon' in v.lower()]
    variables = [v for v in ds_p.data_vars if ds_p[v].ndim == 1 and v not in coord_vars and 'bounds' not in v.lower() and 'time' not in v.lower()]

    vector_tasks, vector_vars = [], set()
    for var in variables:
        if 'wind_speed' in var.lower():
            dir_v = var.replace('speed', 'direction').replace('Speed', 'Direction')
            if dir_v in variables:
                vector_tasks.append((var, dir_v))
                vector_vars.add(var); vector_vars.add(dir_v)

    if vector_tasks:
        for spd_v, dir_v in vector_tasks:
            try:
                metrics = utils.compare_sparse_vectors(ds_p, ds_g, 'speed_dir', spd_v, dir_v, tmp_dir, pair_info, instr, prod_name, fast_mode=fast_mode)
                if metrics:
                    for m in metrics: results.append({'var': spd_v, 'm': m['Metric'], 'v': m['Value']})
            except Exception: pass

    for var in variables:
        if var in vector_vars: continue

        lat_v, lon_v = utils.get_coords_for_var(ds_p, var)
        if not lat_v or not lon_v: continue

        try:
            lat_p, lon_p = ds_p[lat_v].values.ravel(), ds_p[lon_v].values.ravel()
            val_p, val_g = ds_p[var].values.ravel().astype(np.float32), ds_g[var].values.ravel().astype(np.float32)
            lat_g, lon_g = ds_g[lat_v].values.ravel(), ds_g[lon_v].values.ravel()

            if len(val_p) != len(lat_p) or len(val_g) != len(lat_g): continue

            fill_val = ds_p[var].attrs.get('_FillValue')
            if fill_val is not None: val_p[val_p == fill_val] = np.nan
            fill_val_g = ds_g[var].attrs.get('_FillValue')
            if fill_val_g is not None: val_g[val_g == fill_val_g] = np.nan

            mask_p, mask_g = np.isfinite(lat_p) & np.isfinite(lon_p) & np.isfinite(val_p), np.isfinite(lat_g) & np.isfinite(lon_g) & np.isfinite(val_g)
            if not np.any(mask_p) or not np.any(mask_g): continue

            var_attrs = ds_p[var].attrs
            is_bitset = any(k in var_attrs for k in ['flag_values', 'flag_masks', 'flag_meanings'])
            if not is_bitset:
                v_lower, l_name, s_name = var.lower(), var_attrs.get('long_name', '').lower(), var_attrs.get('standard_name', '').lower()
                for kw in ['dqf', 'mask', 'dif', 'pqi', 'flag', 'bit']:
                    if kw in v_lower or kw in l_name or kw in s_name:
                        is_bitset = True; break

            min_lon, max_lon = min(np.nanmin(lon_p[mask_p]), np.nanmin(lon_g[mask_g])), max(np.nanmax(lon_p[mask_p]), np.nanmax(lon_g[mask_g]))
            min_lat, max_lat = min(np.nanmin(lat_p[mask_p]), np.nanmin(lat_g[mask_g])), max(np.nanmax(lat_p[mask_p]), np.nanmax(lat_g[mask_g]))
            if min_lon == max_lon: min_lon -= 1; max_lon += 1
            if min_lat == max_lat: min_lat -= 1; max_lat += 1

            lon_edges, lat_edges = np.linspace(min_lon, max_lon, 101), np.linspace(min_lat, max_lat, 101)

            grid_p = utils.grid_sparse_component(lon_p[mask_p], lat_p[mask_p], val_p[mask_p], lon_edges, lat_edges, is_bitset=is_bitset)
            grid_g = utils.grid_sparse_component(lon_g[mask_g], lat_g[mask_g], val_g[mask_g], lon_edges, lat_edges, is_bitset=is_bitset)

            metrics = utils.execute_visual_comparison(
                grid_p, grid_g, var, tmp_dir, pair_info, "SPARSE",
                proj=ccrs.PlateCarree() if HAS_CARTOPY else None, extent=[min_lon, max_lon, min_lat, max_lat], origin='lower', cmap='viridis', is_bitset=is_bitset, fast_mode=fast_mode
            )

            for m in metrics: results.append({'var': var, 'm': m['Metric'], 'v': m['Value']})

        except Exception: continue

    return results
