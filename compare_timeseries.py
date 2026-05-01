#!/usr/bin/env python3
"""
COMPARE-PAVE: Time-Series Engine
================================
VERSION: 1.7.0
"""
import pandas as pd
import matplotlib.pyplot as plt
from scipy.stats import pearsonr
import compare_utils as utils

def compare_timeseries(ds_p, ds_g, tmp_dir, pair_info, instr, prod_name, log, m_flag):
    """Processes 1D time-sequenced data."""
    results = []
    time_v = next((v for v in ds_p.variables if 'time' in v.lower()), None)
    if not time_v: return []

    variables = [v for v in ds_p.data_vars if ds_p[v].ndim == 1 and v != time_v]

    for var in variables:
        log.debug(f"  -> [{var}] routed to TIMESERIES{m_flag}")
        df_p = pd.DataFrame({'t': ds_p[time_v].values, 'v_p': ds_p[var].values}).dropna().sort_values('t')
        df_g = pd.DataFrame({'t': ds_g[time_v].values, 'v_g': ds_g[var].values}).dropna().sort_values('t')
        
        merged = pd.merge_asof(df_g, df_p, on='t', direction='nearest', tolerance=pd.Timedelta('1s')).dropna()

        if len(merged) < 2: continue

        r_sq = pearsonr(merged['v_g'], merged['v_p'])[0]**2
        merged['diff'] = merged['v_g'] - merged['v_p']

        # Save Standalone Comparison Plot
        fig, ax = plt.subplots(figsize=(12, 4))
        ax.plot(merged['t'], merged['v_g'], label='GCCS', color='green', alpha=0.7)
        ax.plot(merged['t'], merged['v_p'], label='PREM', color='blue', alpha=0.7)
        ax.set_title(f"{var} - Time Series Comparison\n{pair_info}")
        ax.legend(); fig.savefig(tmp_dir / f"{var}_PREM_GCCS.png", dpi=90)
        
        # Save Difference Plot
        fig2, ax2 = plt.subplots(figsize=(12, 4))
        ax2.plot(merged['t'], merged['diff'], color='red')
        ax2.axhline(0, color='black', linestyle='--')
        ax2.set_title(f"{var} Difference (G-P)\n{pair_info}")
        fig2.savefig(tmp_dir / f"{var}_DIFF.png", dpi=90)
        
        # Copy to suite standard name
        fig2.savefig(tmp_dir / f"{var}_comparison.png", dpi=90)
        plt.close('all')

        results.append({'var': var, 'm': 'r-squared correlation', 'v': r_sq})
        results.append({'var': var, 'm': 'finite_in_only_one_fraction', 'v': 0})

    return results
