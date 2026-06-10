#!/usr/bin/env python3
"""
COMPARE-PAVE: 3D Profile Slicing Engine
=======================================
VERSION: 1.5.1 (Hexbin Extent Fix)
"""

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec
from mpl_toolkits.mplot3d import Axes3D
from scipy.stats import pearsonr
import compare_utils as utils
import warnings

try:
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
    HAS_CARTOPY = True
except ImportError:
    HAS_CARTOPY = False

def compare_profiles(ds_p, ds_g, tmp_dir, pair_info, instr, prod_name, log, m_flag, fast_mode=False):
    """Processes 3D geospatial profile variables, generating vertically stacked 3D spatial visualizations."""
    results = []
    variables = []

    spatial_keywords = {'y', 'x', 'lat', 'lon', 'lines', 'pixels'}

    # 1. Discover 3D Variables mapped to spatial horizontal grids
    for var in ds_p.data_vars:
        if ds_p[var].ndim == 3:
            var_dims = [str(d).lower() for d in ds_p[var].dims]
            if any(kw in var_dims for kw in spatial_keywords):
                variables.append(var)

    if not variables:
        log.warn(f"No valid 3D profile variables found in {pair_info}")
        return []

    for var in variables:
        log.debug(f"[PROFILE DEBUG] --- Inspecting 3D Profile Field: {var} ---")
        try:
            if var not in ds_g.data_vars:
                continue

            data_p = ds_p[var].values.astype(np.float32)
            data_g = ds_g[var].values.astype(np.float32)

            if data_p.shape != data_g.shape:
                log.warn(f"[PROFILE SKIP] Dimension mismatch for {var}.")
                continue

            # Handle Fill Values
            fill_val = ds_p[var].attrs.get('_FillValue')
            if fill_val is not None: data_p[data_p == fill_val] = np.nan
            fill_val_g = ds_g[var].attrs.get('_FillValue')
            if fill_val_g is not None: data_g[data_g == fill_val_g] = np.nan

            # --- SMART Z-AXIS DETECTION ---
            var_dims = [str(d).lower() for d in ds_p[var].dims]
            exact_dims = ds_p[var].dims
            z_axis = -1

            # Find the dimension that is NOT a spatial coordinate (e.g., 'pressure', 'level')
            for i, dim_name in enumerate(var_dims):
                if dim_name not in spatial_keywords:
                    z_axis = i
                    break

            # Fallback: The vertical profile is almost always the smallest dimension
            if z_axis == -1:
                z_axis = int(np.argmin(data_p.shape))

            # --- FETCH Z-AXIS PHYSICAL COORDINATES ---
            z_dim_name = exact_dims[z_axis]
            if z_dim_name in ds_p.variables:
                z_coords_full = ds_p[z_dim_name].values.astype(np.float32)

                # TWEAK: Shorten the Z-axis label to prevent overlap
                base_name = z_dim_name.replace('_', ' ').title()
                if 'pressure' in base_name.lower() or 'pres' in base_name.lower():
                    z_label_str = "Pressure"
                elif 'level' in base_name.lower():
                    z_label_str = "Level"
                else:
                    z_label_str = base_name

                z_units = ds_p[z_dim_name].attrs.get('units', '')
                if z_units:
                    z_label_str += f" ({z_units})"
            else:
                z_coords_full = np.arange(data_p.shape[z_axis], dtype=np.float32)
                z_label_str = 'Level Index'

            # Surface maps generally start at the highest pressure, so we track the bounding extrema
            bottom_z = np.nanmax(z_coords_full)
            top_z = np.nanmin(z_coords_full)

            # Reorient array to standard (Z, Y, X) layout
            if z_axis != 0:
                data_p = np.moveaxis(data_p, z_axis, 0)
                data_g = np.moveaxis(data_g, z_axis, 0)

            num_levels, h, w = data_p.shape

            # Volumetric Math Execution
            mask_p, mask_g = np.isfinite(data_p), np.isfinite(data_g)
            common = np.logical_and(mask_p, mask_g)
            num_common = np.count_nonzero(common)

            diff_array = data_g - data_p
            valid_diffs = diff_array[np.isfinite(diff_array)]

            r_sq, r_sq_is_na, samp_p, samp_g = 0.0, True, np.array([]), np.array([])
            if num_common > 1:
                s_size = min(num_common, 500_000)
                sample_idx = np.random.choice(np.flatnonzero(common), size=s_size, replace=False)
                samp_p = data_p.ravel()[sample_idx]
                samp_g = data_g.ravel()[sample_idx]
                if np.any(samp_p != samp_p[0]) and np.any(samp_g != samp_g[0]):
                    r_sq = float(pearsonr(samp_p, samp_g)[0] ** 2)
                    r_sq_is_na = False

            if len(samp_p) > 0 and len(samp_g) > 0:
                vmin = min(np.nanpercentile(samp_p, 1), np.nanpercentile(samp_g, 1))
                vmax = max(np.nanpercentile(samp_p, 99), np.nanpercentile(samp_g, 99))
            else:
                vmin, vmax = 0, 1
            if vmin == vmax: vmin -= 0.1; vmax += 0.1

            d_min = np.nanmin(valid_diffs) if len(valid_diffs) > 0 else -1
            d_max = np.nanmax(valid_diffs) if len(valid_diffs) > 0 else 1
            if d_min == d_max: d_min -= 0.1; d_max += 0.1

            # --- PERFORMANCE DOWNSAMPLING ---
            xy_step = max(1, max(h, w) // 100)
            z_step = 10

            plot_p = data_p[::z_step, ::xy_step, ::xy_step]
            plot_g = data_g[::z_step, ::xy_step, ::xy_step]
            plot_d = diff_array[::z_step, ::xy_step, ::xy_step]

            h_sub, w_sub = plot_p.shape[1], plot_p.shape[2]

            # Fetch physical coordinate values matching the Z-slices
            z_levels = z_coords_full[::z_step]

            # --- SPATIAL COORDINATE RESOLUTION (For Cartopy Borders) ---
            active_proj = None
            x_key = 'x' if 'x' in ds_p.variables else 'X' if 'X' in ds_p.variables else None
            y_key = 'y' if 'y' in ds_p.variables else 'Y' if 'Y' in ds_p.variables else None

            if HAS_CARTOPY and instr == "ABI" and 'goes_imager_projection' in ds_p.variables and x_key and y_key:
                try:
                    gip = ds_p['goes_imager_projection']
                    h_sat = gip.attrs.get('perspective_point_height', 35786023.0)
                    lon_0 = gip.attrs.get('longitude_of_projection_origin', -75.0)
                    sweep = gip.attrs.get('sweep_angle_axis', 'x')
                    active_proj = ccrs.Geostationary(central_longitude=lon_0, satellite_height=h_sat, sweep_axis=sweep)

                    x_vals = ds_p[x_key].values * h_sat
                    y_vals = ds_p[y_key].values * h_sat
                    # STRICT TRUNCATION: Forces meshgrid to exactly match the plot array slice
                    X, Y = np.meshgrid(x_vals[::xy_step][:w_sub], y_vals[::xy_step][:h_sub])
                except Exception as e:
                    log.debug(f"Cartopy projection failed: {e}")
                    X, Y = np.meshgrid(np.arange(w_sub), np.arange(h_sub))
            else:
                lat_v, lon_v = utils.get_coords_for_var(ds_p, var)
                if lat_v and lon_v and HAS_CARTOPY:
                    try:
                        x_vals = ds_p[lon_v].values
                        y_vals = ds_p[lat_v].values
                        if x_vals.ndim == 1:
                            X, Y = np.meshgrid(x_vals[::xy_step][:w_sub], y_vals[::xy_step][:h_sub])
                        else:
                            X = x_vals[::xy_step, ::xy_step][:h_sub, :w_sub]
                            Y = y_vals[::xy_step, ::xy_step][:h_sub, :w_sub]
                        active_proj = ccrs.PlateCarree()
                    except:
                        X, Y = np.meshgrid(np.arange(w_sub), np.arange(h_sub))
                else:
                    X, Y = np.meshgrid(np.arange(w_sub), np.arange(h_sub))

            # Pre-calculate data bounding box to explicitly lock viewport and cull distant geometries
            x_min, x_max = np.nanmin(X), np.nanmax(X)
            y_min, y_max = np.nanmin(Y), np.nanmax(Y)

            # --- RENDER 3D DASHBOARD ---
            fig = plt.figure(figsize=(24, 12))
            prem_name, gccs_name = pair_info.split(" <-> ", 1) if " <-> " in pair_info else (pair_info, "Unknown")
            plt.suptitle(f"{prod_name} | {var.upper()} (3D Profile Dashboard)\nPrem: {prem_name}\nGCCS: {gccs_name}", fontsize=14, weight='bold', y=0.97)

            gs = GridSpec(2, 6, figure=fig)
            ax1 = fig.add_subplot(gs[0, 0:2], projection='3d')
            ax2 = fig.add_subplot(gs[0, 2:4], projection='3d')
            ax4 = fig.add_subplot(gs[0, 4:6])

            ax3 = fig.add_subplot(gs[1, 0:2], projection='3d')
            ax6 = fig.add_subplot(gs[1, 2:4])
            ax_table = fig.add_subplot(gs[1, 4:6])

            ax1.set_title("On-Prem (Z-Slices)", weight='bold')
            ax2.set_title("GCCS (Z-Slices)", weight='bold')
            ax3.set_title("Difference (Z-Slices)", weight='bold')

            def _draw_3d_slices(ax, plot_data, cmap, c_min, c_max):
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    for i, z_val in enumerate(z_levels):
                        if i < plot_data.shape[0]:
                            slice_2d = plot_data[i]
                            if not np.all(np.isnan(slice_2d)):
                                ax.contourf(X, Y, slice_2d, zdir='z', offset=z_val, cmap=cmap, vmin=c_min, vmax=c_max, alpha=0.7, levels=15)

                if HAS_CARTOPY and active_proj is not None:
                    pc = ccrs.PlateCarree()
                    try:
                        for feat in [cfeature.COASTLINE, cfeature.BORDERS, cfeature.STATES]:
                            for geom in feat.geometries():
                                lines = [geom] if geom.geom_type == 'LineString' else geom.geoms if geom.geom_type == 'MultiLineString' else []
                                for line in lines:
                                    lon, lat = line.xy
                                    pts = active_proj.transform_points(pc, np.array(lon), np.array(lat))
                                    vx, vy = pts[:, 0], pts[:, 1]

                                    # Geometry Culling: Skip lines that contain NaNs (off Earth-disk)
                                    if np.all(np.isnan(vx)) or np.all(np.isnan(vy)):
                                        continue

                                    # Geometry Culling: Skip lines entirely outside the CONUS/MESO bounding box
                                    if np.nanmax(vx) < x_min or np.nanmin(vx) > x_max or \
                                       np.nanmax(vy) < y_min or np.nanmin(vy) > y_max:
                                        continue

                                    # Anchor geographic map to the highest pressure plane (surface level)
                                    ax.plot(vx, vy, zs=bottom_z, zdir='z', color='black', linewidth=0.8, alpha=0.5)
                    except Exception as e:
                        log.debug(f"Failed to manually render geographic 3D borders: {e}")

                # Viewport Locking: Explicitly prevent Matplotlib from zooming out to fit cropped map lines
                ax.set_xlim(x_min, x_max)
                ax.set_ylim(y_min, y_max)

                # Automatically invert the Z-Axis range so the surface (highest pressure) is on the bottom
                ax.set_zlim(bottom_z, top_z)

                ax.set_xticks([]); ax.set_yticks([])
                ax.set_zlabel(z_label_str, labelpad=15)
                ax.view_init(elev=20, azim=-45)

            _draw_3d_slices(ax1, plot_p, 'viridis', vmin, vmax)
            _draw_3d_slices(ax2, plot_g, 'viridis', vmin, vmax)
            _draw_3d_slices(ax3, plot_d, 'coolwarm', d_min, d_max)

            # Scatter Subplot
            ax4.set_box_aspect(1)
            ax4.set_xlabel("On-Prem Volumetric Value", fontsize=11); ax4.set_ylabel("GCCS Volumetric Value", fontsize=11)
            if len(samp_p) > 0 and not r_sq_is_na:
                im4 = ax4.hexbin(samp_p, samp_g, gridsize=60, cmap='viridis', mincnt=1, bins='log', extent=[vmin, vmax, vmin, vmax])
                ax4.set_title(f"3D Volume Correlation ($R^2$: {r_sq:.4f})", weight='bold', fontsize=12)
                plt.colorbar(im4, ax=ax4, label='log10(count)')
                ax4.plot([vmin, vmax], [vmin, vmax], color='red', linestyle='--', linewidth=1.5, alpha=0.6, zorder=5)
                ax4.set_xlim(vmin, vmax); ax4.set_ylim(vmin, vmax)
            else:
                ax4.set_title("Correlation ($R^2$: N/A)", weight='bold', fontsize=12)
                ax4.text(0.5, 0.5, "Data Constant or N/A", ha='center', va='center', color='gray')

            # Histogram Subplot
            ax6.set_box_aspect(1)
            ax6.set_title("Volumetric Distribution of Delta", weight='bold', fontsize=12)
            if len(valid_diffs) > 0:
                ax6.hist(valid_diffs, bins=100, color='gray', edgecolor='black')
                ax6.axvline(0, color='red', linestyle='--')

            # Summary Table
            ax_table.axis('off')
            ax_table.set_title("Volumetric Statistical Summary", weight='bold', pad=10, fontsize=14)

            table_content = [
                ["Metric Description", "Observed Value"],
                ["3D Volume Dimensions", f"{data_p.shape}"],
                ["Total Volumetric Pixels", f"{data_p.size:,}"],
                ["Valid Common Intersects", f"{num_common:,}"],
                ["R-Squared ($R^2$)", "N/A" if r_sq_is_na else f"{r_sq:.4f}"],
                ["Max Value", f"P:{np.nanmax(data_p) if np.any(mask_p) else np.nan:.4f} / G:{np.nanmax(data_g) if np.any(mask_g) else np.nan:.4f}"],
                ["Mean Value", f"P:{np.nanmean(data_p) if np.any(mask_p) else np.nan:.4f} / G:{np.nanmean(data_g) if np.any(mask_g) else np.nan:.4f}"],
                ["Max Delta (G-P)", f"{d_max:.4f}" if len(valid_diffs) > 0 else "N/A"],
                ["Min Delta (G-P)", f"{d_min:.4f}" if len(valid_diffs) > 0 else "N/A"],
                ["Mean Abs Error", f"{np.mean(np.abs(valid_diffs)):.4f}" if len(valid_diffs) > 0 else "N/A"]
            ]

            metric_table = ax_table.table(cellText=table_content, loc='center', cellLoc='center', colWidths=[0.55, 0.45], bbox=[0.0, 0.0, 1.0, 0.95])
            metric_table.auto_set_font_size(False); metric_table.set_fontsize(12)

            plt.tight_layout(rect=[0, 0.05, 1, 0.95])
            fig.text(0.5, 0.03, f"3D R-Squared Correlation: {'N/A' if r_sq_is_na else f'{r_sq:.4f}'}", ha='center', va='center', fontsize=22, weight='bold', bbox=dict(facecolor='palegreen' if r_sq >= 0.98 else 'lightcoral', boxstyle='round,pad=0.5'))

            plt.savefig(tmp_dir / f"{var}_comparison.png", dpi=100)
            plt.close(fig)

            results.append({'var': var, 'm': 'r-squared correlation', 'v': r_sq})

        except Exception as e:
            log.warn(f"Error processing 3D Profile {var}: {e}")
            continue

    return results
