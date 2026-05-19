#!/usr/bin/env python3
"""
COMPARE-PAVE: Sparse & Vector Engine
====================================
VERSION: 1.8.2 (Strict Coordinate Length Validation Guard)
"""
import numpy as np
import compare_utils as utils

try:
    import cartopy.crs as ccrs
    HAS_CARTOPY = True
except ImportError:
    HAS_CARTOPY = False

def compare_sparse(ds_p, ds_g, tmp_dir, pair_info, instr, prod_name, log, m_flag):
    """Processes 1D sparse data and Wind Vector flows with strict coordinate length validation."""
    results = []

    # Candidate Variable Discovery Telemetry
    coord_vars = [v for v in ds_p.variables if 'lat' in v.lower() or 'lon' in v.lower()]
    variables = [v for v in ds_p.data_vars if ds_p[v].ndim == 1 and v not in coord_vars]
    vector_tasks = []

    log.debug(f"[SPARSE DEBUG] Discovered 1D data evaluation candidates: {variables}")

    for var in variables:
        log.verbose(f"[SPARSE VERBOSE] --- Inspecting Target Field: {var} ---")

        # 1. Resolve Coordinate Bindings
        lat_v, lon_v = utils.get_coords_for_var(ds_p, var)
        if not lat_v or not lon_v:
            log.debug(f"[SPARSE SKIP] Could not resolve coordinate bindings for '{var}' (lat_v={lat_v}, lon_v={lon_v})")
            continue

        # 2. ENFORCED GUARD: Verify data track length matches coordinate length EXACTLY across both environments
        if (ds_p[var].size != ds_p[lat_v].size or ds_p[var].size != ds_p[lon_v].size or
            ds_g[var].size != ds_g[lat_v].size or ds_g[var].size != ds_g[lon_v].size):
            log.debug(
                f"[SPARSE SKIP] Shape/Length mismatch for '{var}': "
                f"Data size (Prem: {ds_p[var].size}, GCCS: {ds_g[var].size}) "
                f"must match coordinate track size (Prem Lat: {ds_p[lat_v].size}, GCCS Lat: {ds_g[lat_v].size}) exactly."
            )
            continue

        log.debug(f"[SPARSE DEBUG] Variable '{var}' verified with coordinate track layout -> Lat: '{lat_v}', Lon: '{lon_v}'")

        try:
            # Extract underlying array views safely now that shapes are guaranteed
            lat_p_vals = ds_p[lat_v].values.ravel()
            lon_p_vals = ds_p[lon_v].values.ravel()
            var_p_vals = ds_p[var].values.ravel()

            lat_g_vals = ds_g[lat_v].values.ravel()
            lon_g_vals = ds_g[lon_v].values.ravel()
            var_g_vals = ds_g[var].values.ravel()

            log.verbose(f"[SPARSE VERBOSE] Raw entry lengths -> On-Prem: {len(var_p_vals)} points | GCCS: {len(var_g_vals)} points")

            # Escape Gate: Verify array population
            if len(var_p_vals) == 0 or len(var_g_vals) == 0:
                log.debug(f"[SPARSE SKIP] Array for '{var}' is entirely unpopulated in one or both matching file pairs.")
                continue

            c_p = np.column_stack((lat_p_vals, lon_p_vals))
            c_g = np.column_stack((lat_g_vals, lon_g_vals))
            v_p = var_p_vals.astype(np.float32)
            v_g = var_g_vals.astype(np.float32)

            # Evaluate finite mask filtering footprint
            mask_p = np.isfinite(c_p).all(axis=1) & np.isfinite(v_p)
            mask_g = np.isfinite(c_g).all(axis=1) & np.isfinite(v_g)

            c_p, v_p = c_p[mask_p], v_p[mask_p]
            c_g, v_g = c_g[mask_g], v_g[mask_g]

            log.verbose(f"[SPARSE VERBOSE] Post-Finite Mask counts -> On-Prem: {len(v_p)} valid | GCCS: {len(v_g)} valid")

            if len(c_p) == 0 or len(c_g) == 0:
                log.debug(f"[SPARSE SKIP] Zero finite operational points remaining for '{var}' after filtering out NaNs/Fill Values.")
                continue

            # Compute boundary domains
            min_lat, max_lat = min(c_p[:,0].min(), c_g[:,0].min()), max(c_p[:,0].max(), c_g[:,0].max())
            min_lon, max_lon = min(c_p[:,1].min(), c_g[:,1].min()), max(c_p[:,1].max(), c_g[:,1].max())

            log.debug(f"[SPARSE DEBUG] Calculated Domain Box -> Lat: [{min_lat:.3f}, {max_lat:.3f}], Lon: [{min_lon:.3f}, {max_lon:.3f}]")

            bins_lon = 500
            lat_r, lon_r = max_lat - min_lat, max_lon - min_lon
            bins_lat = max(min(int(bins_lon * (lat_r / lon_r)), 2000), 100) if lon_r > 0 else bins_lon

            lon_edges = np.linspace(min_lon, max_lon, bins_lon + 1)
            lat_edges = np.linspace(min_lat, max_lat, bins_lat + 1)

            def _grid_with_trace(c, v, identifier):
                cnt = np.histogram2d(c[:,1], c[:,0], bins=[lon_edges, lat_edges])[0]
                sm = np.histogram2d(c[:,1], c[:,0], bins=[lon_edges, lat_edges], weights=v)[0]
                with np.errstate(divide='ignore', invalid='ignore'):
                    grid_res = np.where(cnt > 0, sm / cnt, np.nan).T

                # Check grid cell concentration density
                populated_cells = np.count_nonzero(np.isfinite(grid_res))
                log.debug(f"[SPARSE DEBUG] Vector Bin Summary ({identifier}) -> Populated {populated_cells} of {grid_res.size} matrix grid nodes.")
                return grid_res

            grid_p = _grid_with_trace(c_p, v_p, "On-Prem")
            grid_g = _grid_with_trace(c_g, v_g, "GCCS")

            # Route to the visual dashboard renderer
            log.verbose(f"[SPARSE VERBOSE] Routing gridded fields for '{var}' to 3x2 comparison dashboard suite...")
            metrics = utils.execute_visual_comparison(
                grid_p, grid_g, var, tmp_dir, pair_info,
                "Sparse", ccrs.PlateCarree() if HAS_CARTOPY else None,
                [min_lon, max_lon, min_lat, max_lat], 'lower'
            )
            log.verbose(f"[SPARSE VERBOSE] Dashboard engine completed processing for '{var}'. Derived metrics count: {len(metrics) if metrics else 0}")

            for m in metrics:
                results.append({'var': var, 'm': m['Metric'], 'v': m['Value']})

        except Exception as ev_var:
            log.error(f"[SPARSE CRASH] Exception intercepted while building gridded comparisons for '{var}': {ev_var}")
            import traceback
            log.debug(traceback.format_exc())
            continue

        # Catch Wind Speed fields to stage structural vector tracking tasks
        if 'wind_speed' in var.lower():
            dir_v = var.replace('speed', 'direction').replace('Speed', 'Direction')
            if dir_v in variables:
                vector_tasks.append(('speed_dir', var, dir_v))

    # Trigger Wind Flow Quiver/Dashboard plotting chains
    if vector_tasks:
        log.verbose(f"[SPARSE VERBOSE] Processing isolated wind flow vector maps queue size: {len(vector_tasks)}")
        for vt, v1, v2 in vector_tasks:
            log.verbose(f"[SPARSE VERBOSE] Executing 6-cell quiver flow field rendering tool for pair: ({v1} + {v2})")
            try:
                utils.compare_sparse_vectors(ds_p, ds_g, vt, v1, v2, tmp_dir, pair_info, instr, prod_name)
            except Exception as e_vec:
                log.error(f"[SPARSE CRASH] Upstream vector flow quiver execution encountered a failure: {e_vec}")

    return results
