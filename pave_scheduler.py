#!/usr/bin/env python3
"""
PAVE: Continuous Background Scheduler
=====================================
VERSION: 2.14.0 (Quirks Shifted to Post-Generation Slots)

SCHEDULING & LOAD BALANCING ARCHITECTURE:
-----------------------------------------
1. Alternating Satellite Slots:
   - G19 executes at: 01:00Z, 09:00Z, 17:00Z
   - G18 executes at: 05:00Z, 13:00Z, 21:00Z

2. True Load Balancing (The 5-Day Cycle):
   - To run EVERY product at EVERY time slot exactly once per week per satellite,
     the 43 products are divided by 5 days (~9 products per slot).
   - Algorithm: `(product_index + daily_slot_index) % 5 == cycle_day`

3. Daily Quirks (NBAR & BRDF):
   - NBAR/BRDF only produce data once per day and are hardcoded.
   - G19 (12Z Data) executes during its first available post-generation slot: 17:00Z.
   - G18 (14Z Data) executes during its first available post-generation slot: 21:00Z.
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
    from pave_utils import setup_interrupt_handler
except ImportError:
    print(f"WARNING: 'pave_utils.py' not found in {SCRIPT_DIR}. Using default Ctrl-C behavior.")
    def setup_interrupt_handler(): pass

# --- SCHEDULE DEFINITIONS ---
ALLOWED_SLOTS = [1, 5, 9, 13, 17, 21]

SLOT_TO_SAT = {
    1: "19",
    5: "18",
    9: "19",
    13: "18",
    17: "19",
    21: "18"
}

# --- PRODUCT ROTATION LIST (43 Entries) ---
RAW_PRODUCTS = [
    "RadCMIP|01", "RadCMIP|02", "RadCMIP|03", "RadCMIP|04",
    "RadCMIP|05", "RadCMIP|06", "RadCMIP|07", "RadCMIP|08",
    "RadCMIP|09", "RadCMIP|10", "RadCMIP|11", "RadCMIP|12",
    "RadCMIP|13", "RadCMIP|14", "RadCMIP|15", "RadCMIP|16",
    "MCMIP",  "ACM",
    "ACH",    "ACT",    "CTP",    "COD",     "CPS",
    "ADP",    "AOD",
    "FDC",    "FSC",    "LST",
    "LSA",    "BRF",
    "LVMP",   "LVTP",   "DSI",    "TPW",    "LSP",
    "RRQPE",  "RSR",    "DSR",    "PAR",    "SWR",
    "SST",    "AICE",   "AITA"
]

def get_now_utc():
    return datetime.datetime.now(datetime.timezone.utc)

def get_slot_tasks(target_date, slot_hour):
    """
    Distributes 43 products so EVERY product runs at EVERY time slot exactly once
    during the 5-day cycle (approx 8-9 products per slot).
    """
    cycle_day = (target_date.weekday() + 1) % 7

    # Map the hour to a 0, 1, or 2 index for staggering the load per satellite
    if slot_hour in [1, 5]: daily_slot_idx = 0
    elif slot_hour in [9, 13]: daily_slot_idx = 1
    else: daily_slot_idx = 2

    scheduled = []
    for i, entry in enumerate(RAW_PRODUCTS):
        if (i + daily_slot_idx) % 5 == cycle_day:
            scheduled.append(entry)

    return scheduled

def wait_until_pave_finishes():
    """Ensures no other pave.py instance is running before launching."""
    cmd = "ps aux | grep '[p]ave.py' | grep -v 'pave_scheduler.py'"
    first_wait = True
    while True:
        try:
            output = subprocess.check_output(cmd, shell=True).decode()
            if output.strip():
                if first_wait:
                    print(f"[{get_now_utc().strftime('%H:%M:%S')} UTC] ⚠️  Active pave.py process detected. Waiting in queue...")
                    first_wait = False
                time.sleep(60)
            else:
                break
        except subprocess.CalledProcessError:
            break

def run_pave(dsn, channels, hour_str, target_date, workspace_dir, pave_script, sat):
    """Executes the pave.py call, passing the specific satellite argument."""
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
    if channels:
        cmd.extend(["--channels"] + channels)
        ch_tag = f"CH{''.join(channels)}"
        cmd.extend(["--tag", ch_tag])
        channel_str = f"_{ch_tag}"

    execution_time_str = get_now_utc().strftime("%Y%m%d_%H%M%S")
    log_filename = f"pave_run_{dsn}{channel_str}_G{sat}_{timestamp}_{execution_time_str}.log"
    log_filepath = os.path.join(log_dir, log_filename)

    wait_until_pave_finishes()

    print(f"[{get_now_utc().strftime('%Y-%m-%d %H:%M:%S')} UTC] EXECUTING: {' '.join(cmd)}")

    with open(log_filepath, "w") as log_file:
        subprocess.run(cmd, stdout=log_file, stderr=subprocess.STDOUT, cwd=workspace_dir)

def execute_slot(workspace_dir, pave_script, time_slot=None):
    """Executes jobs for the specified slot, targeting yesterday's data."""
    target_date = get_now_utc() - datetime.timedelta(days=1)
    target_day_idx = target_date.weekday()

    if target_day_idx in [4, 5]:
        print(f"[{get_now_utc().strftime('%H:%M:%S')} UTC] Target day (Yesterday) was a Friday/Saturday. Standby.")
        return

    # Determine the slot hour
    if time_slot is not None:
        slot_hour = time_slot
    else:
        # Find the most recently passed valid slot
        current_hour = get_now_utc().hour
        slot_hour = ALLOWED_SLOTS[-1] # Default to 21Z (yesterday) if we are before 01Z
        for h in reversed(ALLOWED_SLOTS):
            if current_hour >= h:
                slot_hour = h
                break

    hour_str = f"{slot_hour:02d}"
    target_sat = SLOT_TO_SAT[slot_hour]

    tasks = get_slot_tasks(target_date, slot_hour)

    print(f"\n--- PAVE AUTOMATION | Executing for: {target_date.strftime('%Y-%m-%d')} (Day {target_day_idx}) | Slot {hour_str}:00Z (G{target_sat}) ---")

    if not tasks:
        print("  -> No standard products scheduled for this specific slot iteration.")

    for entry in tasks:
        try:
            if "|" in entry:
                dsn, channel = entry.split("|")
                if dsn == "RadCMIP":
                    run_pave("Rad", [channel], hour_str, target_date, workspace_dir, pave_script, sat=target_sat)
                    run_pave("CMIP", [channel], hour_str, target_date, workspace_dir, pave_script, sat=target_sat)

                    if channel in ["02", "07", "08", "09", "10"]:
                        run_pave("DMW", [channel], hour_str, target_date, workspace_dir, pave_script, sat=target_sat)

                    if channel == "08":
                        run_pave("DMWV", [channel], hour_str, target_date, workspace_dir, pave_script, sat=target_sat)
                else:
                    run_pave(dsn, [channel], hour_str, target_date, workspace_dir, pave_script, sat=target_sat)
            else:
                run_pave(entry, None, hour_str, target_date, workspace_dir, pave_script, sat=target_sat)
        except Exception as e:
            print(f"ERROR: Task {entry} failed to execute: {e}")

    # ==========================================
    # --- HARDCODED DAILY QUIRKS (NBAR/BRDF) ---
    # ==========================================
    if slot_hour == 17:
        print("\n  -> Triggering Daily Quirks (NBAR / BRDF) for G19 at 12Z...")
        for special_dsn in ["NBAR", "BRDF"]:
            run_pave(special_dsn, None, "12", target_date, workspace_dir, pave_script, sat="19")

    if slot_hour == 21:
        print("\n  -> Triggering Daily Quirks (NBAR / BRDF) for G18 at 14Z...")
        for special_dsn in ["NBAR", "BRDF"]:
            run_pave(special_dsn, None, "14", target_date, workspace_dir, pave_script, sat="18")

def wait_for_next_slot():
    now = get_now_utc()
    current_hour = now.hour

    # Find the next upcoming slot in the array
    next_hour = None
    for h in ALLOWED_SLOTS:
        if h > current_hour:
            next_hour = h
            break

    if next_hour is None:
        # Rollover to tomorrow's first slot (01:00Z)
        next_time = (now + datetime.timedelta(days=1)).replace(hour=ALLOWED_SLOTS[0], minute=0, second=0, microsecond=0)
    else:
        next_time = now.replace(hour=next_hour, minute=0, second=0, microsecond=0)

    sleep_seconds = (next_time - now).total_seconds()
    print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')} UTC] Sleeping for {sleep_seconds/3600:.2f} hours until {next_time.strftime('%H:%M:%S')} UTC...")
    time.sleep(sleep_seconds)

if __name__ == "__main__":
    setup_interrupt_handler()

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
    args = parser.parse_args()

    abs_workspace = os.path.abspath(args.workspace)
    abs_pave_script = os.path.abspath(args.pave_script)

    if not os.path.isfile(abs_pave_script):
        print(f"CRITICAL ERROR: 'pave.py' not found at: {abs_pave_script}")
        sys.exit(1)

    print("=========================================")
    print("  PAVE SCHEDULER INITIALIZED")
    print(f"  PAVE Script:   {abs_pave_script}")
    print(f"  Workspace Dir: {abs_workspace}")
    if args.time_slot is not None:
        print(f"  MODE:          OVERRIDE EXECUTION (Slot {args.time_slot:02d}Z)")
    else:
        print(f"  MODE:          CONTINUOUS DAEMON")
    print("=========================================")

    if args.time_slot is not None:
        execute_slot(abs_workspace, abs_pave_script, time_slot=args.time_slot)
        print("\n--- OVERRIDE EXECUTION COMPLETE ---")
    else:
        while True:
            wait_for_next_slot()
            execute_slot(abs_workspace, abs_pave_script)
