#!/usr/bin/env python3
"""
COMPARE-PAVE: Sparse & Vector Engine
====================================
VERSION: 1.12.0 (Logging Verbosity Optimization)
"""
import numpy as np
import compare_utils as utils

try:
    import cartopy.crs as ccrs
    HAS_CARTOPY = True
except ImportError:
    HAS_CARTOPY = False

def compare_sparse(ds_p, ds_g, tmp_dir, pair_info, instr, prod_name, log, m_flag):
    """Processes 1D sparse data tracks. Vectors get quiver maps, scalars get binned geographic maps."""
    results = []

    # 1. Candidate Variable Discovery (Filtering out coordinate and temporal metadata boundaries)
    coord_vars = [v for v in ds_p.variables if 'lat' in v.lower() or 'lon' in v.lower()]
    variables = [
        v for v in ds_p.data_vars
        if ds_p[v].ndim == 1
        and v not in coord_vars
        and 'bounds' not in v.lower()
        and 'time' not in v.lower()
    ]

    log.debug(f"[SPARSE DEBUG] Discovered 1D data evaluation candidates: {variables}")

    # 2. Vector Routing Pass (Identify and queue wind vectors first)
    vector_tasks = []
    vector_vars = set()

    for var in variables:
        if 'wind_speed' in var.lower():
            dir_v = var.replace('speed', 'direction').replace('Speed', 'Direction')
            if dir_v in variables:
                vector_tasks.append((var, dir_v))
                vector_vars.add(var)
                vector_vars.add(dir_v)

    # Trigger Wind Flow Quiver/Dashboard plotting chains
    if vector_tasks:
        log.debug(f"[SPARSE DEBUG] Processing isolated wind flow vector maps queue size: {len(vector_tasks)}")
        for spd_v, dir_v in vector_tasks:
            log.debug(f"[SPARSE DEBUG] Executing flow field rendering tool for pair: ({spd_v} + {dir_v})")
            try:
                metrics = utils.compare_sparse_vectors(ds_p, ds_g, 'speed_dir', spd_v, dir_v, tmp_dir, pair_info, instr, prod_name)
                if metrics:
                    for m in metrics:
                        results.append({'var': spd_v, 'm': m['Metric'], 'v': m['Value']})
            except Exception as e_vec:
                log.error(f"[SPARSE CRASH] Upstream vector flow quiver execution encountered a failure: {e_vec}")

    # 3. Standard Scalar Geographic Binning Pass (Temperatures, Heights, PQI)
    for var in variables:
        if var in vector_vars:
            continue

        log.debug(f"[SPARSE DEBUG] --- Inspecting Collocated 1D Track Field: {var} ---")

        lat_v, lon_v = utils.get_coords_for_var(ds_p, var)
        if not lat_v or not lon_v:
            continue

        try:
            lat_p, lon_p = ds_p[lat_v].values.ravel(), ds_p[lon_v].values.ravel()
            val_p = ds_p[var].values.ravel().astype(np.float32)

            lat_g, lon_g = ds_g[lat_v].values.ravel(), ds_g[lon_v].values.ravel()
            val_g = ds_g[var].values.ravel().astype(np.float32)

            # Strict Array Broadcasting Guard
            if len(val_p) != len(lat_p) or len(val_g) != len(lat_g):
                log.debug(f"[SPARSE SKIP] Coordinate broadcasting mismatch for '{var}': Array length ({len(val_p)}) does not match Coordinate points ({len(lat_p)}).")
                continue

            mask_p = np.isfinite(lat_p) & np.isfinite(lon_p) & np.isfinite(val_p)
            mask_g = np.isfinite(lat_g) & np.isfinite(lon_g) & np.isfinite(val_g)

            if not np.any(mask_p) or not np.any(mask_g):
                log.debug(f"[SPARSE SKIP] Array for '{var}' is entirely unpopulated in one or both file pairs.")
                continue

            # Determine Shared Spatial Bounds
            min_lon = min(np.nanmin(lon_p[mask_p]), np.nanmin(lon_g[mask_g]))
            max_lon = max(np.nanmax(lon_p[mask_p]), np.nanmax(lon_g[mask_g]))
            min_lat = min(np.nanmin(lat_p[mask_p]), np.nanmin(lat_g[mask_g]))
            max_lat = max(np.nanmax(lat_p[mask_p]), np.nanmax(lat_g[mask_g]))

            if min_lon == max_lon: min_lon -= 1; max_lon += 1
            if min_lat == max_lat: min_lat -= 1; max_lat += 1

            lon_edges = np.linspace(min_lon, max_lon, 101)
            lat_edges = np.linspace(min_lat, max_lat, 101)

            # Bin Point Clouds to 2D Geographic Grids
            grid_p = utils.grid_sparse_component(lon_p[mask_p], lat_p[mask_p], val_p[mask_p], lon_edges, lat_edges)
            grid_g = utils.grid_sparse_component(lon_g[mask_g], lat_g[mask_g], val_g[mask_g], lon_edges, lat_edges)

            proj = ccrs.PlateCarree() if HAS_CARTOPY else None
            extent = [min_lon, max_lon, min_lat, max_lat]

            # Route newly gridded 2D matrices into the standard visual map engine
            metrics = utils.execute_visual_comparison(
                grid_p, grid_g, var, tmp_dir, pair_info, "SPARSE",
                proj=proj, extent=extent, origin='lower'
            )

            if metrics:
                for m in metrics:
                    results.append({'var': var, 'm': m['Metric'], 'v': m['Value']})

        except Exception as ev_var:
            log.error(f"[SPARSE CRASH] Exception intercepted while building gridded spatial comparisons for '{var}': {ev_var}")
            continue

    return results
