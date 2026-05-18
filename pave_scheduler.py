#!/usr/bin/env python3
"""
PAVE: Continuous Background Scheduler
=====================================
VERSION: 2.30.0 (Automated Post-Slot Dashboard Harvesting Integration)

SCHEDULING & LOAD BALANCING ARCHITECTURE:
-----------------------------------------
1. Alternating Satellite Slots:
   - G19 executes at: 01:00Z, 09:00Z, 17:00Z
   - G18 executes at: 05:00Z, 13:00Z, 21:00Z

2. Persistent Monitor (ACM):
   - Clear Sky Mask (ACM) executes at the start of EVERY time slot to guarantee
     baseline masking verification remains continuously active.

3. True Load Balancing (The Configurable DOY Cycle):
   - Core product combinations are divided by a rolling Day-of-Year scalar.
   - Algorithm: `(product_index + daily_slot_index) % CYCLE_DAYS == (DOY % CYCLE_DAYS)`

4. Dependent Triggers (Rad, CMIP, & DMW):
   - 'RadCMIP' handles radiance and cloud pairs.
   - Cascades to run 'DMW' (Channels 2, 7, 8, 9, 10) and 'DMWV' (Channel 8).

5. Radiation Product Coupling:
   - RSR, DSR, PAR, and SWR are combined into a single logical "RadiationGroup".

6. Thermodynamic Sounding Coupling:
   - LVMP, LVTP, DSI, TPW, and LSP are combined into a single "SoundingGroup".

7. Unified Cloud Product Coupling:
   - All cloud physical parameters (macro, micro, enterprise heights, and convective structure)
     are bound tightly under a single rotating execution sequence.
   - Group items: ACH, ACT, CTP, ECBH, EOCH, COD, CPS, CCL

8. Surface Albedo & Reflectance Coupling:
   - LSA (Land Surface Albedo) and BRF (Bidirectional Reflectance Factor) are bound
     into a shared "SurfaceAlbedoGroup" to force simultaneous batch slot execution.

9. Daily Quirks Coupled to Closest Subsequent LSA Runs:
   - G19 (12Z Data) executes during the 17:00Z slot alongside the G19 LSA block.
   - G18 (14Z Data) executes during the 21:00Z slot alongside the G18 LSA block.

OPERATIONAL BACKGROUND INVOCATIONS (nohup examples):
----------------------------------------------------
1. Standard Background Operation with Auto-Dashboard Harvesting:
   nohup python3 pave_scheduler.py --workspace /path/to/work --dashboard /path/to/archive > scheduler.log 2>&1 &

2. Verbose Background Tracking Operation:
   nohup python3 pave_scheduler.py --workspace /path/to/work -v > scheduler.log 2>&1 &
"""

import argparse
import subprocess
import datetime
import time
import sys
import os

# --- PATH RESOLUTION & IMPORTS ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

try:
    from pave_utils import Logger, setup_interrupt_handler
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

# --- SCHEDULE & ROTATION CONFIGURATION ---
CYCLE_DAYS = 4              # Configurable rotation loop duration (e.g., 3, 4, 5 days)
ALLOWED_SLOTS = [1, 5, 9, 13, 17, 21]

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
    "MCMIP",
    "ADP",    "AOD",
    "FDC",    "FSC",    "LST",
    "SurfaceAlbedoGroup",       # Combined items: LSA, BRF
    "RRQPE",  "SST",    "AICE",   "AITA",
    "ESC",    "ESU",    "ETE",
    "RadiationGroup",           # Group items: RSR, DSR, PAR, SWR
    "SoundingGroup",            # Group items: LVMP, LVTP, DSI, TPW, LSP
    "CloudGroup"                # Group items: ACH, ACT, CTP, ECBH, EOCH, COD, CPS, CCL
]

def get_now_utc():
    return datetime.datetime.now(datetime.timezone.utc)

def get_slot_tasks(target_date, slot_hour):
    """
    Distributes products so EVERY product group runs at EVERY time slot exactly once
    during the configurable rolling day-of-year loop.
    """
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
    """Ensures no other pave.py instance is running before launching."""
    cmd = "ps aux | grep '[p]ave.py' | grep -v 'pave_scheduler.py'"
    first_wait = True
    while True:
        try:
            output = subprocess.check_output(cmd, shell=True).decode()
            if output.strip():
                if first_wait:
                    log.warn("Active pave.py process detected. Waiting in queue...")
                    first_wait = False
                time.sleep(60)
            else:
                break
        except subprocess.CalledProcessError:
            break

def run_pave(dsn, channels, hour_str, target_date, workspace_dir, pave_script, sat, log):
    """Executes the pave.py call and returns the targeted execution folder path."""
    timestamp = f"{target_date.year}{target_date.strftime('%j')}{hour_str}0"

    log_dir = os.path.join(workspace_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)

    cmd = [
        sys.executable, pave_script, dsn,
        "--times", timestamp,
        "--prefix", dsn,
        "--sat", sat,
        "--use-compare",
        "--verbose"
    ]

    channel_str = ""
    folder_tag = ""
    if channels:
        cmd.extend(["--channels"] + channels)
        ch_tag = f"CH{''.join(channels)}"
        cmd.extend(["--tag", ch_tag])
        channel_str = f"_{ch_tag}"
        folder_tag = f"_{ch_tag}"

    # Track directory path using prefix, date, and tag matching pave.py specifications
    target_workspace_folder = os.path.join(workspace_dir, f"{dsn}_{timestamp}{folder_tag}")

    execution_time_str = get_now_utc().strftime("%Y%m%d_%H%M%S")
    log_filename = f"pave_run_{dsn}{channel_str}_G{sat}_{timestamp}_{execution_time_str}.log"
    log_filepath = os.path.join(log_dir, log_filename)

    wait_until_pave_finishes(log)

    log.info(f"EXECUTING: {' '.join(cmd)}")

    with open(log_filepath, "w") as log_file:
        subprocess.run(cmd, stdout=log_file, stderr=subprocess.STDOUT, cwd=workspace_dir)

    return target_workspace_folder

def execute_slot(workspace_dir, pave_script, log, dashboard_path=None, time_slot=None):
    """Executes jobs for the specified slot, targeting yesterday's data."""
    target_date = get_now_utc() - datetime.timedelta(days=1)
    target_day_idx = target_date.weekday()

    if target_day_idx in [4, 5]:
        log.info("Target day (Yesterday) was a Friday/Saturday. Standby.")
        return

    if time_slot is not None:
        slot_hour = time_slot
    else:
        current_hour = get_now_utc().hour
        slot_hour = ALLOWED_SLOTS[-1]
        for h in reversed(ALLOWED_SLOTS):
            if current_hour >= h:
                slot_hour = h
                break

    hour_str = f"{slot_hour:02d}"
    target_sat = SLOT_TO_SAT[slot_hour]

    tasks = get_slot_tasks(target_date, slot_hour)

    log.info(f"--- PAVE AUTOMATION | Executing for: {target_date.strftime('%Y-%m-%d')} (DOY {target_date.strftime('%j')}) | Slot {hour_str}:00Z (G{target_sat}) ---")

    # Array list to collect folder names that are created by pave.py
    active_workspaces = []

    # ==========================================
    # --- PERSISTENT MONITOR INJECTION (ACM) ---
    # ==========================================
    try:
        folder = run_pave("ACM", None, hour_str, target_date, workspace_dir, pave_script, sat=target_sat, log=log)
        active_workspaces.append(folder)
    except Exception as e:
        log.error(f"Persistent Monitor ACM failed to execute: {e}")

    if not tasks:
        log.info("  -> No rotating products scheduled for this specific slot iteration.")

    for entry in tasks:
        try:
            if entry == "SurfaceAlbedoGroup":
                for surf_dsn in ["LSA", "BRF"]:
                    folder = run_pave(surf_dsn, None, hour_str, target_date, workspace_dir, pave_script, sat=target_sat, log=log)
                    active_workspaces.append(folder)
            elif entry == "RadiationGroup":
                for rad_dsn in ["RSR", "DSR", "PAR", "SWR"]:
                    folder = run_pave(rad_dsn, None, hour_str, target_date, workspace_dir, pave_script, sat=target_sat, log=log)
                    active_workspaces.append(folder)
            elif entry == "SoundingGroup":
                for snd_dsn in ["LVMP", "LVTP", "DSI", "TPW", "LSP"]:
                    folder = run_pave(snd_dsn, None, hour_str, target_date, workspace_dir, pave_script, sat=target_sat, log=log)
                    active_workspaces.append(folder)
            elif entry == "CloudGroup":
                for cld_dsn in ["ACH", "ACT", "CTP", "ECBH", "EOCH", "COD", "CPS", "CCL"]:
                    folder = run_pave(cld_dsn, None, hour_str, target_date, workspace_dir, pave_script, sat=target_sat, log=log)
                    active_workspaces.append(folder)
            elif "|" in entry:
                dsn, channel = entry.split("|")
                if dsn == "RadCMIP":
                    f_rad = run_pave("Rad", [channel], hour_str, target_date, workspace_dir, pave_script, sat=target_sat, log=log)
                    f_cmip = run_pave("CMIP", [channel], hour_str, target_date, workspace_dir, pave_script, sat=target_sat, log=log)
                    active_workspaces.extend([f_rad, f_cmip])

                    if channel in ["02", "07", "08", "09", "10"]:
                        f_dmw = run_pave("DMW", [channel], hour_str, target_date, workspace_dir, pave_script, sat=target_sat, log=log)
                        active_workspaces.append(f_dmw)

                    if channel == "08":
                        f_dmwv = run_pave("DMWV", [channel], hour_str, target_date, workspace_dir, pave_script, sat=target_sat, log=log)
                        active_workspaces.append(f_dmwv)
                else:
                    folder = run_pave(dsn, [channel], hour_str, target_date, workspace_dir, pave_script, sat=target_sat, log=log)
                    active_workspaces.append(folder)
            else:
                folder = run_pave(entry, None, hour_str, target_date, workspace_dir, pave_script, sat=target_sat, log=log)
                active_workspaces.append(folder)
        except Exception as e:
            log.error(f"Task {entry} failed to execute: {e}")

    # =========================================================================
    # --- HARDCODED DAILY QUIRKS (Coupled to the closest subsequent LSA run) ---
    # =========================================================================
    if "SurfaceAlbedoGroup" in tasks:
        if slot_hour == 17:
            log.info("Triggering Daily Quirks (NBAR / BRDF) for G19 at 12Z (Closest post-generation LSA slot)...")
            for special_dsn in ["NBAR", "BRDF"]:
                folder = run_pave(special_dsn, None, "12", target_date, workspace_dir, pave_script, sat="19", log=log)
                active_workspaces.append(folder)

        if slot_hour == 21:
            log.info("Triggering Daily Quirks (NBAR / BRDF) for G18 at 14Z (Closest post-generation LSA slot)...")
            for special_dsn in ["NBAR", "BRDF"]:
                folder = run_pave(special_dsn, None, "14", target_date, workspace_dir, pave_script, sat="18", log=log)
                active_workspaces.append(folder)
    else:
        if slot_hour in [17, 21]:
            log.info(f"Skipping Daily Quirks (NBAR / BRDF) for slot {hour_str}Z: SurfaceAlbedoGroup is not scheduled today in the {CYCLE_DAYS}-day DOY cycle.")

    # =========================================================================
    # --- AUTOMATED DASHBOARD HARVESTING TRIGGER ---
    # =========================================================================
    if dashboard_path and active_workspaces:
        log.info("Evaluating generated folders for dashboard report aggregation...")
        dashboard_script = os.path.join(SCRIPT_DIR, "pave_dashboard.py")

        # Filter out folder entries that do not physically exist (e.g., if a task completely failed)
        valid_paths = [f for f in active_workspaces if os.path.isdir(f)]

        if valid_paths:
            # Construct command: python3 pave_dashboard.py [dir1] [dir2] ... -o [dashboard_path]
            dash_cmd = [sys.executable, dashboard_script] + valid_paths + ["--output", dashboard_path]
            log.info(f"LAUNCHING AUTO-HARVEST SUITE: {' '.join(dash_cmd)}")
            try:
                subprocess.run(dash_cmd, cwd=workspace_dir)
            except Exception as e:
                log.error(f"Post-slot visual dashboard harvesting failed to execute: {e}")
        else:
            log.warn("No active output workspace directories were physically created during this slot cycle.")

def wait_for_next_slot(log):
    now = get_now_utc()
    current_hour = now.hour

    next_hour = None
    for h in ALLOWED_SLOTS:
        if h > current_hour:
            next_hour = h
            break

    if next_hour is None:
        next_time = (now + datetime.timedelta(days=1)).replace(hour=ALLOWED_SLOTS[0], minute=0, second=0, microsecond=0)
    else:
        next_time = now.replace(hour=next_hour, minute=0, second=0, microsecond=0)

    sleep_seconds = (next_time - now).total_seconds()
    log.info(f"Sleeping for {sleep_seconds/3600:.2f} hours until {next_time.strftime('%H:%M:%S')} UTC...")
    time.sleep(sleep_seconds)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PAVE Continuous Background Scheduler")
    parser.add_argument(
        "--workspace",
        type=str,
        default=os.getcwd(),
        help="The directory where PAVE outputs and logs will be generated."
    )
    parser.add_argument(
        "--pave-script",
        type=str,
        default=os.path.join(SCRIPT_DIR, "pave.py"),
        help="The explicit path to pave.py."
    )
    parser.add_argument(
        "--time-slot",
        type=int,
        choices=[1, 5, 9, 13, 17, 21],
        help="Run a specific time slot immediately and exit (do not enter daemon mode)."
    )
    parser.add_argument(
        "--dashboard",
        type=str,
        help="Path to the shared dashboard archive folder. Enables automated harvesting."
    )
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

    log.info("=========================================")
    log.info("  PAVE SCHEDULER INITIALIZED")
    log.info(f"  PAVE Script:   {abs_pave_script}")
    log.info(f"  Workspace Dir: {abs_workspace}")
    log.info(f"  Rotation Loop: {CYCLE_DAYS} Days (DOY-Anchored)")
    if args.dashboard:
        log.info(f"  Dashboard Out: {os.path.abspath(args.dashboard)}")
    if args.time_slot is not None:
        log.info(f"  MODE:          OVERRIDE EXECUTION (Slot {args.time_slot:02d}Z)")
    else:
        log.info("  MODE:          CONTINUOUS DAEMON (Immediate Boot Trigger Active)")
    log.info("=========================================")

    if args.time_slot is not None:
        execute_slot(abs_workspace, abs_pave_script, log, dashboard_path=args.dashboard, time_slot=args.time_slot)
        log.info("--- OVERRIDE EXECUTION COMPLETE ---")
    else:
        log.info("Boot verification check: Executing target slot for current hour profile...")
        execute_slot(abs_workspace, abs_pave_script, log, dashboard_path=args.dashboard)

        while True:
            wait_for_next_slot(log)
            execute_slot(abs_workspace, abs_pave_script, log, dashboard_path=args.dashboard)
