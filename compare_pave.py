#!/usr/bin/env python3
"""
COMPARE-PAVE Orchestrator
VERSION: 1.8.1 (Interrupt Integration & Multi-Engine Routing)
"""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['MPLBACKEND'] = 'Agg'

import argparse, csv, shutil, gc, sys, traceback
import matplotlib.pyplot as plt
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
import xarray as xr

from pave_utils import Logger, setup_interrupt_handler, resolve_meta
# Sub-module engines
from compare_standard import compare_standard
from compare_sparse import compare_sparse
from compare_timeseries import compare_timeseries
import compare_utils as utils

def process_file_pair(p_file, g_file, dest_root, prem_root, log, soft_match=False):
    """Orchestrates the comparison for a single file pair."""
    results = []
    match_flag = "*" if soft_match else ""
    pair_info = f"{p_file.name}{match_flag} <-> {g_file.name}"

    m = utils.GOES_REGEX.search(p_file.name)
    if not m: return None

    prod_name = m.group('dsn')
    meta = {'Product': prod_name, 'Sat': m.group('sat'), 'Start': m.group('start')}
    g_meta = resolve_meta(prod_name)
    strategy = g_meta.get('comp_type', 'standard').lower()
    instr = g_meta.get('instr', 'ABI')

    rel_path = p_file.relative_to(prem_root).parent
    final_dir = dest_root / rel_path / p_file.stem
    tmp_dir = final_dir.with_suffix('.partial')

    if final_dir.exists(): return []
    if tmp_dir.exists(): shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    try:
        log.debug(f"Opening Dataset: {p_file.name}")
        with xr.open_dataset(p_file, cache=False) as ds_p, \
             xr.open_dataset(g_file, cache=False) as ds_g:

            # Engine routing based on product strategy
            if strategy == 'sparse':
                metrics = compare_sparse(ds_p, ds_g, tmp_dir, pair_info, instr, prod_name, log, match_flag)
            elif strategy == 'timeseries':
                metrics = compare_timeseries(ds_p, ds_g, tmp_dir, pair_info, instr, prod_name, log, match_flag)
            else:
                metrics = compare_standard(ds_p, ds_g, tmp_dir, pair_info, instr, prod_name, log, match_flag)

            for m_dict in metrics:
                results.append({**meta, 'Variable': m_dict['var'], 'Metric': m_dict['m'], 'Value': m_dict['v']})

        if results:
            with open(tmp_dir / "stats.csv", 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=['Product', 'Variable', 'Sat', 'Metric', 'Start', 'Value'])
                writer.writeheader()
                writer.writerows(results)

        tmp_dir.rename(final_dir)
        return results

    except Exception as e:
        log.warn(f"CRITICAL FILE FAILURE: {p_file.name}")
        log.warn(f"  Error Type: {type(e).__name__} | Message: {str(e)}")

        exc_type, exc_value, exc_traceback = sys.exc_info()
        tb_details = traceback.extract_tb(exc_traceback)
        last_call = tb_details[-1]
        log.warn(f"  Location: {last_call.filename} at line {last_call.lineno} in {last_call.name}")

        if log.level <= 10:
            log.debug(traceback.format_exc())

        if tmp_dir.exists(): shutil.rmtree(tmp_dir)
        return None

    finally:
        # CRITICAL: Force clear figures and collect garbage after each pair
        plt.close('all')
        gc.collect()

class PaveComparator:
    def __init__(self, args, log):
        self.prem_root = Path(args.prem_fld).resolve()
        self.gccs_root = Path(args.gccs_fld).resolve()
        self.dest_root = Path(args.dest_fld).resolve()
        self.stats_root = Path(getattr(args, 'stats_fld', args.dest_fld)).resolve()
        self.threads, self.log = getattr(args, 'threads', 4), log

    def execute(self):
        # 1. Initialize Interrupt Handler from pave_utils
        setup_interrupt_handler()
        self.log.info("Interrupt handler active.")

        self.dest_root.mkdir(parents=True, exist_ok=True)
        p_files = list(self.prem_root.rglob("*.nc"))
        g_files = list(self.gccs_root.rglob("*.nc"))

        # 2. Map file pairs
        g_strict_map = {f.stem: f for f in g_files}
        g_identity_map = {utils.get_identity_start_key(f.name): f for f in g_files}

        tasks, matched_p = [], set()

        # PASS 1: Strict Match
        for pf in p_files:
            if pf.stem in g_strict_map:
                tasks.append((pf, g_strict_map[pf.stem], False))
                matched_p.add(pf)

        # PASS 2: Soft Fallback
        soft_count = 0
        for pf in p_files:
            if pf in matched_p: continue
            p_ident_key = utils.get_identity_start_key(pf.name)
            if p_ident_key in g_identity_map:
                tasks.append((pf, g_identity_map[p_ident_key], True))
                soft_count += 1
                matched_p.add(pf)

        self.log.info(f"Starting comparison for {len(tasks)} pairs...")

        # 3. Multiprocessing Execution
        with ProcessPoolExecutor(max_workers=self.threads) as ex:
            futures = {ex.submit(process_file_pair, p, g, self.dest_root, self.prem_root, self.log, s): p.name for p, g, s in tasks}
            for f in as_completed(futures):
                f.result()

        utils.write_aggregated_summary(self.dest_root, self.stats_root, self.log)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pave Compare Orchestrator")
    parser.add_argument("--prem_fld", required=True, help="On-Prem dataset root")
    parser.add_argument("--gccs_fld", required=True, help="GCCS dataset root")
    parser.add_argument("--dest_fld", required=True, help="Output folder for plots/stats")
    parser.add_argument("--stats_fld", help="Optional separate folder for summary CSV")
    parser.add_argument("--threads", type=int, default=4, help="Parallel worker threads")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")

    args = parser.parse_args()
    log = Logger("PaveCompare", level="DEBUG" if args.verbose else "INFO")

    comparator = PaveComparator(args, log)
    comparator.execute()
