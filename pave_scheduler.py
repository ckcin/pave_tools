#!/usr/bin/env python3
"""
PAVE: Continuous Background Scheduler
=====================================
VERSION: 2.70.0 (Unified CLI Flag Propagation Downstream)

SCHEDULING & LOAD BALANCING ARCHITECTURE:
-----------------------------------------
1. Alternating Satellite Slots (Data Target Hours):
   - G19 evaluates data for: 01:00Z, 09:00Z, 17:00Z
   - G18 evaluates data for: 05:00Z, 13:00Z, 21:00Z

2. Execution Delay (+2 Hours):
   - To accommodate upstream data preparation, the scheduler sleeps and
     executes exactly 2 hours AFTER the target data slot (e.g., the 01Z
     data slot physically executes at 03Z).

3. Persistent Monitor (ACM):
   - Clear Sky Mask (ACM) executes at the start of EVERY time slot to guarantee
     baseline masking verification remains continuously active.

4. True Load Balancing (The Configurable DOY Cycle):
   - Core product combinations are divided by a rolling Day-of-Year scalar.
   - Algorithm: `(product_index + daily_slot_index) % CYCLE_DAYS == (DOY % CYCLE_DAYS)`

5. Dependent Triggers (Rad, CMIP, & DMW):
   - 'RadCMIP' handles radiance and cloud pairs.
   - Cascades to run 'DMW' (Channels 2, 7, 8, 9, 10) and 'DMWV' (Channel 8).

6. Daily Quirks Coupled to Closest Subsequent LSA Runs:
   - G19 (12Z Data) executes during the 17:00Z slot alongside the G19 LSA block.
   - G18 (14Z Data) executes during the 21:00Z slot alongside the G18 LSA block.

7. 3-Hour Cryosphere Matching:
   - AICE and AITA are dynamically adjusted to target the most recently completed
     3-hourly file timeline (00, 03, 09, 12, 15, 21) via floor-division
     to prevent fetching future, ungenerated data.

8. Automated Lifecycle & Executive Summary Records:
   - Valid workspaces are automatically passed to pave_archiver.py.
   - The archiver autonomously discovers 'stats_summary.csv' within each workspace
     to dynamically generate a color-coded executive summary matrix in the final PDF record.

9. Shared IP Tarball Cache & Local Purge:
   - Dynamically intercepts preserved intermediate product tarballs from the initial run.
   - Hot-loads cached tarballs into downstream workspaces before execution to eliminate redundant S3 retrievals.
   - IMMEDIATELY purges the local tarball copies from the workspace post-execution to prevent massive runtime disk bloating.

10. Catch-Up Mode:
   - Triggered via `--catch-up` combined with explicit `--doy` and `--year` overrides.
   - Loops rapidly through historical time metrics step-by-step before handing off to live operational tracking loops.

EXAMPLE PRODUCTION DEPLOYMENT:
------------------------------
To run the scheduler continuously in the background, immune to terminal disconnects,
use the following generic `nohup` construct:

nohup /path/to/pave_scheduler.py \
    --workspace /path/to/workspace/ \
    --pave-script /path/to/pave.py \
    --dashboard /path/to/dashboard \
    --record /path/to/records \
    --relax-match \
    --fast-compare \
    --verbose \
    >> /path/to/logs/master_scheduler.log 2>&1 &
"""

import argparse
import subprocess
import datetime
import time
import sys
import os
import re
import shutil

# --- PATH RESOLUTION & IMPORTS ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

try:
    from pave_utils import Logger, setup_interrupt_handler, get_products_in_family, PRODUCT_FAMILIES
except ImportError:
    class DummyLogger:
        def __init__(self, *args, **kwargs): pass
        def debug(self, m): print(f"[DEBUG] {m}")
        def info(self, m): print(f"[INFO] {m}")
        def verbose(self, m): print(f"[VERBOSE] {m}")
        def warn(self, m): print(f"[WARN] {m}")
        def error(self, m): print(f"[ERROR] {m}")
    Logger = DummyLogger
    print(f"WARNING: 'pave_utils.py' not found in {SCRIPT_DIR}. Falling back to terminal log emulation.")
    def setup_interrupt_handler(log=None): pass
    def get_products_in_family(fam): return []
    PRODUCT_FAMILIES = {}

# --- SCHEDULE & ROTATION CONFIGURATION ---
CYCLE_DAYS = 3
ALLOWED_SLOTS = [1, 5, 9, 13, 17, 21]
EXEC_DELAY_HOURS = 2

SLOT_TO_SAT = {
    1: "19",
    5: "18",
    9: "19",
    13: "18",
    17: "19",
    21: "18"
}

# --- PRODUCT ROTATION LIST ---
RAW_PRODUCTS = [
    "RadCMIP|01", "RadCMIP|02", "RadCMIP|03", "RadCMIP|04",
    "RadCMIP|05", "RadCMIP|06", "RadCMIP|07", "RadCMIP|08",
    "RadCMIP|09", "RadCMIP|10", "RadCMIP|11", "RadCMIP|12",
    "RadCMIP|13", "RadCMIP|14", "RadCMIP|15", "RadCMIP|16",

    "Sounding", "CloudHeight", "COMP", "Cloud_ACT", "Cloud_ECBH",
    "Cloud_EOCH", "Cloud_CCL", "Radiation", "SurfaceAlbedo",
    "Aerosol_ADP", "Aerosol_AOD", "Cryo_AICE", "Cryo_AITA",

    "SST", "RRQPE", "FDC", "FSC", "LST", "ESC", "ESU", "ETE"
]

def get_now_utc():
    return datetime.datetime.now(datetime.timezone.utc)

def get_slot_tasks(target_date, slot_hour):
    julian_day = int(target_date.strftime('%j'))
    cycle_day = julian_day % CYCLE_DAYS

    if slot_hour in [1, 5]: daily_slot_idx = 0
    elif slot_hour in [9, 13]: daily_slot_idx = 1
    else: daily_slot_idx = 2

    scheduled = []
    for i, entry in enumerate(RAW_PRODUCTS):
        if (i + daily_slot_idx) % CYCLE_DAYS == cycle_day:
            scheduled.append(entry)

    return scheduled

def wait_until_pave_finishes(log):
    cmd = "ps aux | grep '[p]ave.py' | grep -v 'pave_scheduler.py'"
    first_wait = True
    while True:
        try:
            output = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT).decode()
            if output.strip():
                if first_wait:
                    log.warn("Active pave.py process detected. Waiting in queue...")
                    first_wait = False
                time.sleep(10)
            else:
                break
        except subprocess.CalledProcessError as exc:
            if exc.returncode == 1:
                break
            log.error(f"Error checking active pave.py processes: {exc.output.decode().strip()}")
            time.sleep(10)

def run_pave(dsn, channels, hour_str, target_date, workspace_dir, pave_script, sat, log, cache_dir, relax_match=False, fast_compare=False, verbose=False, debug=False):
    timestamp = f"{target_date.year}{target_date.strftime('%j')}{hour_str}0"

    log_dir = os.path.join(workspace_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)

    channel_str = ""
    folder_tag = ""
    if channels:
        ch_tag = f"CH{''.join(channels)}"
        channel_str = f"_{ch_tag}"
        folder_tag = f"_{ch_tag}"

    target_workspace_folder = os.path.join(workspace_dir, f"{dsn}_{timestamp}{folder_tag}")
    target_prem_dir = os.path.join(target_workspace_folder, "prem")

    # --- INJECT CACHED IP TARBALLS BEFORE RUNNING ---
    if os.path.isdir(cache_dir) and any(os.scandir(cache_dir)):
        os.makedirs(target_prem_dir, exist_ok=True)
        for cached_item in os.listdir(cache_dir):
            if cached_item.endswith(".tar"):
                src_tar = os.path.join(cache_dir, cached_item)
                dst_tar = os.path.join(target_prem_dir, cached_item)
                log.info(f"Hot-loading cached IP tarball into runtime workspace: {cached_item}")
                shutil.copy2(src_tar, dst_tar)

    cmd = [
        sys.executable, pave_script, dsn,
        "--times", timestamp,
        "--prefix", dsn,
        "--sat", sat,
        "--preserve-ip"  # Keep the tarball in workspace/ip_data so the scheduler can harvest it
    ]

    if relax_match: cmd.append("--relax-match")
    if fast_compare: cmd.append("--fast-compare")
    if verbose: cmd.append("--verbose")
    if debug: cmd.append("--debug")

    if channels:
        cmd.extend(["--channels"] + channels)
        cmd.extend(["--tag", ch_tag])

    execution_time_str = get_now_utc().strftime("%Y%m%d_%H%M%S")
    log_filename = f"pave_run_{dsn}{channel_str}_G{sat}_{timestamp}_{execution_time_str}.log"
    log_filepath = os.path.join(log_dir, log_filename)

    wait_until_pave_finishes(log)

    log.info(f"EXECUTING: {' '.join(cmd)}")

    with open(log_filepath, "w") as log_file:
        subprocess.run(cmd, stdout=log_file, stderr=subprocess.STDOUT, cwd=workspace_dir)

    # --- HARVEST PRESERVED IP TARBALL INTO CENTRAL SLOT CACHE ---
    workspace_ip_data = os.path.join(target_workspace_folder, "ip_data")
    if os.path.isdir(workspace_ip_data):
        for item in os.listdir(workspace_ip_data):
            if item.endswith(".tar"):
                os.makedirs(cache_dir, exist_ok=True)
                src_preserved = os.path.join(workspace_ip_data, item)
                dst_cached = os.path.join(cache_dir, item)
                if not os.path.exists(dst_cached):
                    log.info(f"Preserving freshly extracted IP tarball to slot-level cache: {item}")
                    shutil.copy2(src_preserved, dst_cached)

        # --- IMMEDIATE RUNTIME CLEANUP ---
        # Annihilate the local ip_data folder to prevent the disk from bloating
        log.verbose("Purging preserved IP tarballs from local workspace to reclaim active disk space.")
        shutil.rmtree(workspace_ip_data, ignore_errors=True)

    # Extra safety sweep: Nuke any lingering .tar files in the local prem directory
    if os.path.isdir(target_prem_dir):
        for item in os.listdir(target_prem_dir):
            if item.endswith(".tar"):
                os.remove(os.path.join(target_prem_dir, item))

    return target_workspace_folder

def execute_slot(workspace_dir, pave_script, log, dashboard_path=None, record_path=None, relax_match=False, fast_compare=False, time_slot=None, override_doy=None, override_year=None, verbose=False, debug=False):
    now = get_now_utc()

    if time_slot is not None:
        slot_hour = time_slot
        if override_doy is not None:
            target_year = override_year if override_year is not None else now.year
            target_date = datetime.datetime.strptime(f"{target_year}{override_doy:03d}", "%Y%j").replace(tzinfo=datetime.timezone.utc)
        else:
            target_date = now
    else:
        candidate_triggers = []
        for d in [now - datetime.timedelta(days=1), now]:
            for h in ALLOWED_SLOTS:
                exec_h = h + EXEC_DELAY_HOURS
                trigger_time = d.replace(hour=exec_h, minute=0, second=0, microsecond=0)
                candidate_triggers.append((d, h, trigger_time))

        valid_triggers = [item for item in candidate_triggers if item[2] <= now]
        latest_date, latest_data_hour, latest_trigger_time = valid_triggers[-1]

        target_date = latest_date
        slot_hour = latest_data_hour

    target_date_idx = target_date.weekday()

    if target_date_idx in [5, 6]:
        if time_slot is None:
            log.info("Target day is a Weekend (Saturday/Sunday). Standby.")
            return
        else:
            log.info("Target day is a Weekend, but proceeding anyway due to execution override context.")

    hour_str = f"{slot_hour:02d}"
    target_sat = SLOT_TO_SAT[slot_hour]

    tasks = get_slot_tasks(target_date, slot_hour)

    log.info(f"--- PAVE SCHEDULER ENGINE | Target: {target_date.strftime('%Y-%m-%d')} (DOY {target_date.strftime('%j')}) | Data Slot {hour_str}:00Z (G{target_sat}) ---")

    # Initialize a temporary slot-level IP cache directory
    slot_ip_cache = os.path.join(workspace_dir, f".ip_cache_{target_date.year}{target_date.strftime('%j')}{hour_str}")
    active_workspaces = []

    try:
        folder = run_pave("ACM", None, hour_str, target_date, workspace_dir, pave_script, sat=target_sat, log=log, cache_dir=slot_ip_cache, relax_match=relax_match, fast_compare=fast_compare, verbose=verbose, debug=debug)
        active_workspaces.append(folder)
    except Exception as e:
        log.error(f"Persistent Monitor ACM failed to execute: {e}")

    if not tasks:
        log.info("  -> No rotating products scheduled for this specific slot iteration.")

    for entry in tasks:
        try:
            if entry in PRODUCT_FAMILIES:
                family_members = get_products_in_family(entry)
                for member_dsn in family_members:
                    if member_dsn in ["AICE", "AITA"]:
                        floor_3hr = (slot_hour // 3) * 3
                        ice_hour_str = f"{floor_3hr:02d}"
                        log.info(f"Syncing cryosphere product '{member_dsn}' timeline to preceding 3-hour match: {ice_hour_str}0")
                        folder = run_pave(member_dsn, None, ice_hour_str, target_date, workspace_dir, pave_script, sat=target_sat, log=log, cache_dir=slot_ip_cache, relax_match=relax_match, fast_compare=fast_compare, verbose=verbose, debug=debug)
                    else:
                        folder = run_pave(member_dsn, None, hour_str, target_date, workspace_dir, pave_script, sat=target_sat, log=log, cache_dir=slot_ip_cache, relax_match=relax_match, fast_compare=fast_compare, verbose=verbose, debug=debug)
                    active_workspaces.append(folder)

            elif "|" in entry:
                dsn, channel = entry.split("|")
                if dsn == "RadCMIP":
                    f_rad = run_pave("Rad", [channel], hour_str, target_date, workspace_dir, pave_script, sat=target_sat, log=log, cache_dir=slot_ip_cache, relax_match=relax_match, fast_compare=fast_compare, verbose=verbose, debug=debug)
                    f_cmip = run_pave("CMIP", [channel], hour_str, target_date, workspace_dir, pave_script, sat=target_sat, log=log, cache_dir=slot_ip_cache, relax_match=relax_match, fast_compare=fast_compare, verbose=verbose, debug=debug)
                    active_workspaces.extend([f_rad, f_cmip])

                    if channel in ["02", "07", "08", "09", "10"]:
                        f_dmw = run_pave("DMW", [channel], hour_str, target_date, workspace_dir, pave_script, sat=target_sat, log=log, cache_dir=slot_ip_cache, relax_match=relax_match, fast_compare=fast_compare, verbose=verbose, debug=debug)
                        active_workspaces.append(f_dmw)
                    if channel == "08":
                        f_dmwv = run_pave("DMWV", [channel], hour_str, target_date, workspace_dir, pave_script, sat=target_sat, log=log, cache_dir=slot_ip_cache, relax_match=relax_match, fast_compare=fast_compare, verbose=verbose, debug=debug)
                        active_workspaces.append(f_dmwv)
                else:
                    folder = run_pave(dsn, [channel], hour_str, target_date, workspace_dir, pave_script, sat=target_sat, log=log, cache_dir=slot_ip_cache, relax_match=relax_match, fast_compare=fast_compare, verbose=verbose, debug=debug)
                    active_workspaces.append(folder)
            else:
                folder = run_pave(entry, None, hour_str, target_date, workspace_dir, pave_script, sat=target_sat, log=log, cache_dir=slot_ip_cache, relax_match=relax_match, fast_compare=fast_compare, verbose=verbose, debug=debug)
                active_workspaces.append(folder)
        except Exception as e:
            log.error(f"Task {entry} failed to execute: {e}")

    if "SurfaceAlbedo" in tasks:
        if slot_hour == 17:
            log.info("Triggering Daily Quirks (NBAR / BRDF) for G19 at 12Z...")
            for special_dsn in ["NBAR", "BRDF"]:
                folder = run_pave(special_dsn, None, "12", target_date, workspace_dir, pave_script, sat="19", log=log, cache_dir=slot_ip_cache, relax_match=relax_match, fast_compare=fast_compare, verbose=verbose, debug=debug)
                active_workspaces.append(folder)
        if slot_hour == 21:
            log.info("Triggering Daily Quirks (NBAR / BRDF) for G18 at 14Z...")
            for special_dsn in ["NBAR", "BRDF"]:
                folder = run_pave(special_dsn, None, "14", target_date, workspace_dir, pave_script, sat="18", log=log, cache_dir=slot_ip_cache, relax_match=relax_match, fast_compare=fast_compare, verbose=verbose, debug=debug)
                active_workspaces.append(folder)

    valid_paths = [f for f in active_workspaces if os.path.isdir(f)]

    # --- CLEANUP AT THE END OF THE SLOT EXECUTION ---
    if os.path.isdir(slot_ip_cache):
        log.verbose(f"Clearing temporary time slot intermediate cache: {slot_ip_cache}")
        shutil.rmtree(slot_ip_cache)

    if valid_paths:
        log.info("Evaluating processed folders for lifecycle sweep (Harvesting, Archiving, Recording)...")
        archiver_script = os.path.join(SCRIPT_DIR, "pave_archiver.py")
        arch_cmd = [sys.executable, archiver_script] + valid_paths + ["--clean-validation"]

        if dashboard_path: arch_cmd.extend(["--dashboard", dashboard_path])
        if record_path: arch_cmd.extend(["--record", record_path])

        # Safely pass CLI flags down the archiver pipe
        if verbose: arch_cmd.append("--verbose")
        if debug:
            arch_cmd.append("--debug")
            arch_cmd.append("--debug-stats")

        log.info(f"LAUNCHING UNIFIED LIFECYCLE SWEEP: {' '.join(arch_cmd)}")
        try:
            subprocess.run(arch_cmd, cwd=workspace_dir)
        except Exception as e:
            log.error(f"Post-slot automated lifecycle sweep failed: {e}")
    else:
        log.warn("No active output workspace directories were physically created during this slot cycle. Archiver skipped.")

def wait_for_next_slot(log):
    now = get_now_utc()
    candidate_triggers = []

    for d in [now, now + datetime.timedelta(days=1)]:
        for h in ALLOWED_SLOTS:
            exec_h = h + EXEC_DELAY_HOURS
            candidate_triggers.append(d.replace(hour=exec_h, minute=0, second=0, microsecond=0))

    next_trigger = next(t for t in candidate_triggers if t > now)
    sleep_seconds = (next_trigger - now).total_seconds()
    log.info(f"Sleeping for {sleep_seconds/3600:.2f} hours until the next execution window at {next_trigger.strftime('%H:%M:%S')} UTC...")
    time.sleep(sleep_seconds)

def handle_catch_up_loop(abs_workspace, abs_pave_script, args, log):
    """Executes a sequential matrix catch-up loop step-by-step using enforced DOY and Year configurations."""
    cursor_hour = args.time_slot
    cursor_date = datetime.datetime.strptime(f"{args.year}{args.doy:03d}", "%Y%j").replace(tzinfo=datetime.timezone.utc)

    log.info(f"[CATCH-UP PROCESSING SYSTEM ACTIVATED]")
    log.info(f"  Walking forward from anchor: {cursor_date.strftime('%Y-%m-%d')} (DOY {cursor_date.strftime('%j')}) slot {cursor_hour:02d}Z")

    while True:
        now_utc = get_now_utc()
        exec_window = cursor_date.replace(hour=(cursor_hour + EXEC_DELAY_HOURS), minute=0, second=0, microsecond=0)

        if exec_window > now_utc:
            log.info(f"[SYNC COMPLETE] Catch-up processing horizon has aligned with current tracking time. Switching to background daemon.")
            break

        execute_slot(
            abs_workspace, abs_pave_script, log,
            dashboard_path=args.dashboard, record_path=args.record,
            relax_match=args.relax_match, fast_compare=args.fast_compare,
            time_slot=cursor_hour, override_doy=int(cursor_date.strftime('%j')), override_year=cursor_date.year,
            verbose=args.verbose, debug=args.debug
        )

        current_idx = ALLOWED_SLOTS.index(cursor_hour)
        if current_idx == len(ALLOWED_SLOTS) - 1:
            cursor_hour = ALLOWED_SLOTS[0]
            cursor_date += datetime.timedelta(days=1)
        else:
            cursor_hour = ALLOWED_SLOTS[current_idx + 1]

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PAVE Continuous Background Scheduler")
    parser.add_argument("--workspace", type=str, default=os.getcwd(), help="The directory where PAVE outputs and logs will be generated.")
    parser.add_argument("--pave-script", type=str, default=os.path.join(SCRIPT_DIR, "pave.py"), help="The explicit path to pave.py.")

    # SYSTEM INTERACTION AND BACKFILL CONTROLS
    parser.add_argument("--time-slot", type=int, choices=[1, 5, 9, 13, 17, 21], help="Target database execution hour slot.")
    parser.add_argument("--doy", type=int, help="Override Day of Year for standalone override or baseline catch-up loops.")
    parser.add_argument("--year", type=int, help="Override year configuration details.")
    parser.add_argument("--catch-up", action="store_true", help="Automate cyclical matrix loops sequentially from baseline parameters up to live runtime window.")

    parser.add_argument("--dashboard", type=str, help="Path to the shared dashboard extraction folder.")
    parser.add_argument("--record", type=str, help="Path to the long-term artifact PDF output folder.")

    parser.add_argument("--relax-match", action="store_true", help="Relax file matching loops to evaluate pairing based exclusively on start time.")
    parser.add_argument("--fast-compare", action="store_true", help="Passes fast mode configuration down to PAVE engines.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose operational details visibility")
    parser.add_argument("-d", "--debug", action="store_true", help="Deep structural metrics tracing details")
    parser.add_argument("-q", "--quiet", action="store_true", help="Restrict engine output print updates")
    args = parser.parse_args()

    lvl = "DEBUG" if args.debug else "VERBOSE" if args.verbose else "QUIET" if args.quiet else "INFO"
    log = Logger(lvl)
    setup_interrupt_handler(log)

    abs_workspace = os.path.abspath(args.workspace)
    abs_pave_script = os.path.abspath(args.pave_script)

    if not os.path.isfile(abs_pave_script):
        log.error(f"CRITICAL ERROR: 'pave.py' not found at: {abs_pave_script}")
        sys.exit(1)

    # VALIDATION GATE: Enforce strict positional configurations
    if args.catch_up:
        if args.time_slot is None or args.doy is None or args.year is None:
            log.error("CRITICAL ARGUMENT ERROR: The --catch-up option strictly requires that --time-slot, --doy, and --year are all specified to anchor the baseline tracking timeline.")
            sys.exit(1)
    else:
        if args.doy is not None and args.time_slot is None:
            log.error("CRITICAL ERROR: --doy override can only be used in conjunction with a specific --time-slot.")
            sys.exit(1)

    log.info("=========================================")
    log.info("  PAVE SCHEDULER INITIALIZED")
    log.info(f"  PAVE Script:   {abs_pave_script}")
    log.info(f"  Workspace Dir: {abs_workspace}")
    log.info(f"  Rotation Loop: {CYCLE_DAYS} Days (DOY-Anchored)")
    log.info(f"  Execution:     Same-Day Target (+{EXEC_DELAY_HOURS}hr Delay)")
    if args.relax_match: log.info("  Matching Mode: RELAXED (Start-Time '_s' Anchor Only)")
    if args.fast_compare: log.info("  Engine Mode:   FAST-COMPARE (Standalone plots disabled)")
    if args.dashboard: log.info(f"  Dashboard Out: {os.path.abspath(args.dashboard)}")
    if args.record: log.info(f"  Records Out:   {os.path.abspath(args.record)}")
    log.info("=========================================")

    # Branch execution parameters based on validation metrics
    if args.catch_up:
        handle_catch_up_loop(abs_workspace, abs_pave_script, args, log)
        log.info("Catch-up phase cleared successfully. Transitioning system into runtime loops...")

    if args.time_slot is not None and not args.catch_up:
        execute_slot(abs_workspace, abs_pave_script, log, dashboard_path=args.dashboard, record_path=args.record, relax_match=args.relax_match, fast_compare=args.fast_compare, time_slot=args.time_slot, override_doy=args.doy, override_year=args.year, verbose=args.verbose, debug=args.debug)
        log.info("--- OVERRIDE EXECUTION COMPLETE ---")
    elif not args.catch_up:
        log.info("Boot verification check: Executing target slot for current hour profile...")
        execute_slot(abs_workspace, abs_pave_script, log, dashboard_path=args.dashboard, record_path=args.record, relax_match=args.relax_match, fast_compare=args.fast_compare, verbose=args.verbose, debug=args.debug)

        while True:
            wait_for_next_slot(log)
            execute_slot(abs_workspace, abs_pave_script, log, dashboard_path=args.dashboard, record_path=args.record, relax_match=args.relax_match, fast_compare=args.fast_compare, verbose=args.verbose, debug=args.debug)
