#!/usr/bin/env python3
"""
COMPARE-PAVE: Sparse & Vector Engine
====================================
VERSION: 1.7.0
"""
import numpy as np
import compare_utils as utils

try:
    import cartopy.crs as ccrs
    HAS_CARTOPY = True
except ImportError:
    HAS_CARTOPY = False

def compare_sparse(ds_p, ds_g, tmp_dir, pair_info, instr, prod_name, log, m_flag):
    """Processes 1D sparse data and Wind Vector flows."""
    results = []
    coord_vars = [v for v in ds_p.variables if 'lat' in v.lower() or 'lon' in v.lower()]
    variables = [v for v in ds_p.data_vars if ds_p[v].ndim == 1 and v not in coord_vars]
    vector_tasks = []

    for var in variables:
        log.debug(f"  -> [{var}] routed to SPARSE{m_flag}")
        lat_v, lon_v = utils.get_coords_for_var(ds_p, var)
        if not lat_v or not lon_v: continue

        # Extract and validate points
        c_p = np.column_stack((ds_p[lat_v].values.ravel(), ds_p[lon_v].values.ravel()))
        c_g = np.column_stack((ds_g[lat_v].values.ravel(), ds_g[lon_v].values.ravel()))
        v_p = ds_p[var].values.astype(np.float32).ravel()
        v_g = ds_g[var].values.astype(np.float32).ravel()

        mask_p = np.isfinite(c_p).all(axis=1) & np.isfinite(v_p)
        mask_g = np.isfinite(c_g).all(axis=1) & np.isfinite(v_g)
        
        c_p, v_p = c_p[mask_p], v_p[mask_p]
        c_g, v_g = c_g[mask_g], v_g[mask_g]

        if len(c_p) == 0: continue

        # Grid the sparse data
        min_lat, max_lat = min(c_p[:,0].min(), c_g[:,0].min()), max(c_p[:,0].max(), c_g[:,0].max())
        min_lon, max_lon = min(c_p[:,1].min(), c_g[:,1].min()), max(c_p[:,1].max(), c_g[:,1].max())
        
        bins_lon = 500
        lat_r, lon_r = max_lat - min_lat, max_lon - min_lon
        bins_lat = max(min(int(bins_lon * (lat_r / lon_r)), 2000), 100) if lon_r > 0 else bins_lon

        lon_edges = np.linspace(min_lon, max_lon, bins_lon + 1)
        lat_edges = np.linspace(min_lat, max_lat, bins_lat + 1)

        def _grid(c, v):
            cnt = np.histogram2d(c[:,1], c[:,0], bins=[lon_edges, lat_edges])[0]
            sm = np.histogram2d(c[:,1], c[:,0], bins=[lon_edges, lat_edges], weights=v)[0]
            return np.where(cnt > 0, sm / cnt, np.nan).T

        grid_p, grid_g = _grid(c_p, v_p), _grid(c_g, v_g)

        metrics = utils.execute_visual_comparison(
            grid_p, grid_g, var, tmp_dir, pair_info, 
            "Sparse", ccrs.PlateCarree() if HAS_CARTOPY else None, 
            [min_lon, max_lon, min_lat, max_lat], 'lower'
        )
        for m in metrics: results.append({'var': var, 'm': m['Metric'], 'v': m['Value']})

        # Identify Wind Vector pairs
        if 'wind_speed' in var.lower():
            dir_v = var.replace('speed', 'direction').replace('Speed', 'Direction')
            if dir_v in variables: vector_tasks.append(('speed_dir', var, dir_v))

    # Trigger Wind Vector Quiver plots if pairs found
    for vt, v1, v2 in vector_tasks:
        utils.compare_sparse_vectors(ds_p, ds_g, vt, v1, v2, tmp_dir, pair_info, instr, prod_name)
    
    return results
