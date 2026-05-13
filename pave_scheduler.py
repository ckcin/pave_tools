#!/usr/bin/env python3
"""
PAVE: Continuous Background Scheduler
=====================================
VERSION: 2.12.0 (Documentation & Load Balancing Overview)

SCHEDULING & LOAD BALANCING ARCHITECTURE:
-----------------------------------------
1. Target Data & Processing Window:
   - This scheduler operates on a continuous background daemon loop.
   - To ensure data is fully available on the remote servers, the scheduler ALWAYS
     targets data from "Yesterday" (UTC - 1 Day).
   - It only processes data generated from Sunday through Thursday. Therefore,
     the cron/daemon is active Monday through Friday, and goes into standby
     over the weekend.

2. True Load Balancing (The 5-Day Cycle):
   - There are 43 standard products in the rotation.
   - The goal is to run EVERY product at EVERY 3-hour time slot (0Z, 3Z, 6Z, 9Z,
     12Z, 15Z, 18Z, 21Z) exactly once per week.
   - Instead of running all 43 products at every time slot (which would crash the
     system), the load is divided by 5 days.
   - Algorithm: `(product_index + hour_index) % 5 == cycle_day`
   - Result: During any given 3-hour slot, the script only triggers ~8 to 9 products.
     By the end of the 5-day week, all 43 products will have run at all 8 time slots.

3. Dependent Triggers (Rad, CMIP, & DMW):
   - To ensure spectral consistency, 'RadCMIP' is treated as a single rotation item.
   - When triggered, it spawns sequential runs for 'Rad' and 'CMIP' for that channel.
   - If the channel is 02, 07, 08, 09, or 10, it automatically triggers 'DMW'.
   - If the channel is 08, it automatically triggers 'DMWV'.

4. Daily Quirks (NBAR & BRDF):
   - NBAR and BRDF only produce data once per day and cannot be in the 3-hour rotation.
   - They are hardcoded to trigger ONLY during the 12:00Z execution block.
   - G19 is evaluated at 12Z; G18 is evaluated at 14Z.

5. Concurrency Protection:
   - The scheduler actively monitors the OS process list. It will not launch a
     new pave.py task until the previous one has completely finished, preventing
     OOM (Out of Memory) crashes.
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

def get_slot_tasks(target_date, hour_idx):
    """
    Distributes 43 products so EVERY product runs at EVERY time slot exactly once
    during the 5-day cycle (approx 8-9 products per slot).
    """
    cycle_day = (target_date.weekday() + 1) % 7

    scheduled = []
    for i, entry in enumerate(RAW_PRODUCTS):
        if (i + hour_idx) % 5 == cycle_day:
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

def run_pave(dsn, channels, hour_str, target_date, workspace_dir, pave_script, sat=None):
    """Executes the pave.py call, generating the timestamp based on the provided hour_str."""
    timestamp = f"{target_date.year}{target_date.strftime('%j')}{hour_str}0"

    log_dir = os.path.join(workspace_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)

    cmd = [
        sys.executable, pave_script, dsn,
        "--times", timestamp,
        "--prefix", dsn,
        "--use-compare",
        "--verbose"
    ]

    channel_str = ""
    sat_str = ""

    if channels:
        cmd.extend(["--channels"] + channels)
        ch_tag = f"CH{''.join(channels)}"
        cmd.extend(["--tag", ch_tag])
        channel_str = f"_{ch_tag}"

    if sat:
        cmd.extend(["--sat", sat])
        sat_str = f"_G{sat}"

    execution_time_str = get_now_utc().strftime("%Y%m%d_%H%M%S")
    log_filename = f"pave_run_{dsn}{channel_str}{sat_str}_{timestamp}_{execution_time_str}.log"
    log_filepath = os.path.join(log_dir, log_filename)

    wait_until_pave_finishes()

    print(f"[{get_now_utc().strftime('%Y-%m-%d %H:%M:%S')} UTC] EXECUTING: {' '.join(cmd)}")

    with open(log_filepath, "w") as log_file:
        subprocess.run(cmd, stdout=log_file, stderr=subprocess.STDOUT, cwd=workspace_dir)

def execute_slot(workspace_dir, pave_script, time_slot=None):
    """Executes jobs for the specified slot, targeting yesterday's data."""
    target_date = get_now_utc() - datetime.timedelta(days=1)
    target_day_idx = target_date.weekday()

    # Process Sun-Thu data on Mon-Fri
    if target_day_idx in [4, 5]:
        print(f"[{get_now_utc().strftime('%H:%M:%S')} UTC] Target day (Yesterday) was a Friday/Saturday. Standby.")
        return

    if time_slot is not None:
        slot_hour = time_slot
    else:
        current_hour = get_now_utc().hour
        slot_hour = (current_hour // 3) * 3

    hour_idx = slot_hour // 3
    hour_str = f"{slot_hour:02d}"

    tasks = get_slot_tasks(target_date, hour_idx)

    print(f"\n--- PAVE AUTOMATION | Executing for: {target_date.strftime('%Y-%m-%d')} (Day {target_day_idx}) | Slot {hour_str}:00Z ---")

    if not tasks:
        print("  -> No standard products scheduled for this specific slot iteration.")

    for entry in tasks:
        try:
            if "|" in entry:
                dsn, channel = entry.split("|")
                if dsn == "RadCMIP":
                    run_pave("Rad", [channel], hour_str, target_date, workspace_dir, pave_script)
                    run_pave("CMIP", [channel], hour_str, target_date, workspace_dir, pave_script)

                    if channel in ["02", "07", "08", "09", "10"]:
                        run_pave("DMW", [channel], hour_str, target_date, workspace_dir, pave_script)

                    if channel == "08":
                        run_pave("DMWV", [channel], hour_str, target_date, workspace_dir, pave_script)
                else:
                    run_pave(dsn, [channel], hour_str, target_date, workspace_dir, pave_script)
            else:
                run_pave(entry, None, hour_str, target_date, workspace_dir, pave_script)
        except Exception as e:
            print(f"ERROR: Task {entry} failed to execute: {e}")

    # ==========================================
    # --- HARDCODED DAILY QUIRKS (12Z ONLY) ---
    # ==========================================
    if slot_hour == 12:
        print("\n  -> Triggering Daily Quirks (NBAR / BRDF) for 12Z/14Z...")
        for special_dsn in ["NBAR", "BRDF"]:
            try:
                # G19 at 12Z
                run_pave(special_dsn, None, "12", target_date, workspace_dir, pave_script, sat="19")
                # G18 at 14Z
                run_pave(special_dsn, None, "14", target_date, workspace_dir, pave_script, sat="18")
            except Exception as e:
                print(f"ERROR: Daily Quirk Task {special_dsn} failed to execute: {e}")

def wait_for_next_slot():
    now = get_now_utc()
    next_hour = ((now.hour // 3) + 1) * 3

    if next_hour >= 24:
        next_time = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
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
        choices=[0, 3, 6, 9, 12, 15, 18, 21],
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
