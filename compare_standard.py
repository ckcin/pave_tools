#!/usr/bin/env python3
"""
COMPARE-PAVE: Standard Imagery Engine
=====================================
VERSION: 1.8.0 (Integrated Mismatch & Density)
"""
import numpy as np
import compare_utils as utils

try:
    import cartopy.crs as ccrs
    HAS_CARTOPY = True
except ImportError:
    HAS_CARTOPY = False

def compare_standard(ds_p, ds_g, tmp_dir, pair_info, instr, prod_name, log, m_flag):
    results = []
    variables = [v for v in ds_p.data_vars if ds_p[v].ndim >= 2]

    if not variables:
        log.warn(f"No 2D variables found in {pair_info}")
        return []

    proj, extent, cmap = None, None, 'viridis'
    if HAS_CARTOPY and instr == "ABI" and 'goes_imager_projection' in ds_p.variables:
        try:
            gip = ds_p['goes_imager_projection']
            h_sat = gip.attrs.get('perspective_point_height', 35786023.0)
            lon_0 = gip.attrs.get('longitude_of_projection_origin', -75.0)
            sweep = gip.attrs.get('sweep_angle_axis', 'x')
            proj = ccrs.Geostationary(central_longitude=lon_0, satellite_height=h_sat, sweep_axis=sweep)
            x, y = ds_p['x'].values * h_sat, ds_p['y'].values * h_sat
            extent = [x.min(), x.max(), y.min(), y.max()]
        except Exception as e:
            log.debug(f"Cartopy projection failed: {e}")

    if instr == 'SUVI':
        cmap = utils.get_suvi_cmap(prod_name)

    for var in variables:
        try:
            if var not in ds_g.data_vars:
                log.warn(f"Variable {var} missing in GCCS. Skipping.")
                continue

            data_p = ds_p[var].values.astype(np.float32)
            data_g = ds_g[var].values.astype(np.float32)

            if data_p.shape != data_g.shape:
                log.warn(f"DIMENSION MISMATCH for {var}. Skipping.")
                continue

            # Routed to the updated utility engine
            metrics = utils.execute_visual_comparison(
                data_p, data_g, var, tmp_dir, pair_info,
                "Standard", proj, extent, 'upper', cmap
            )

            for m in metrics:
                results.append({'var': var, 'm': m['Metric'], 'v': m['Value']})
        except Exception as e:
            log.warn(f"Error processing {var}: {e}")
            continue

    return results
