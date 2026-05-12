#!/usr/bin/env python3
"""
PAVE: Automated Cron Scheduler (Yesterday's Data Target)
========================================================
VERSION: 1.7.0

DEPLOYMENT INSTRUCTIONS:
Add the following line to your system crontab (crontab -e).
The script automatically targets data from the PREVIOUS DAY.

CRONTAB ENTRY (Example):
0 0,3,6,9,12,15,18,21 * * * /usr/bin/python3 /absolute/path/to/pave_scheduler.py --base-dir /absolute/path/to/pave/workspace >> /absolute/path/to/scheduler_master.log 2>&1
"""

import argparse
import subprocess
import datetime
import sys
import os

# --- PRODUCT ROTATION LIST (44 Entries) ---
RAW_PRODUCTS = [
    "RadCMIP|01", "RadCMIP|02", "RadCMIP|03", "RadCMIP|04",
    "RadCMIP|05", "RadCMIP|06", "RadCMIP|07", "RadCMIP|08",
    "RadCMIP|09", "RadCMIP|10", "RadCMIP|11", "RadCMIP|12",
    "RadCMIP|13", "RadCMIP|14", "RadCMIP|15", "RadCMIP|16",
    "MCMIP",   "ACHT",    "ACM",     "ACTP",    "ADP",     "AOD",
    "COD",     "CPS",     "CTP",     "DMW|02",  "DMW|07",  "DMW|08",
    "DMW|09",  "DMW|10",  "DMWV|08", "FDC",     "FSC",     "LST",
    "LVMP",    "LVTP",    "DSI",     "TPW",     "LSP",     "RRQPE",
    "RSR",     "DSR",     "SWR",     "SST"
]

def get_slot_tasks(target_day_idx, hour_idx):
    """Rotates through the 44 entries across the 40 weekly slots."""
    scheduled = []
    for i, entry in enumerate(RAW_PRODUCTS):
        if (i + hour_idx) % 5 == target_day_idx:
            scheduled.append(entry)
    return scheduled

def run_pave(dsn, channels, hour_str, target_date, base_dir):
    """Executes pave.py from the base directory targeting a specific date."""
    timestamp = f"{target_date.year}{target_date.strftime('%j')}{hour_str}0"

    pave_script = os.path.join(base_dir, "pave.py")
    log_dir = os.path.join(base_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)

    cmd = [
        sys.executable, pave_script, dsn,
        "--times", timestamp,
        "--use-compare",
        "--verbose"
    ]

    channel_str = ""
    if channels:
        cmd.extend(["--channels"] + channels)
        channel_str = f"_CH{''.join(channels)}"

    # Construct unique log filename based on the actual run execution time
    execution_time_str = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    log_filename = f"pave_run_{dsn}{channel_str}_{timestamp}_{execution_time_str}.log"
    log_filepath = os.path.join(log_dir, log_filename)

    print(f"[{datetime.datetime.now()}] EXECUTING: {' '.join(cmd)}")
    print(f" -> Working Dir: {base_dir}")
    print(f" -> Logging to: {log_filepath}")

    with open(log_filepath, "w") as log_file:
        subprocess.run(cmd, stdout=log_file, stderr=subprocess.STDOUT, cwd=base_dir)

def main(base_dir):
    # Calculate TARGET DATE (Yesterday) to ensure data availability
    target_date = datetime.datetime.utcnow() - datetime.timedelta(days=1)
    target_day_idx = target_date.weekday()

    # Check if YESTERDAY was a weekend
    if target_day_idx > 4:
        print(f"Target day (Yesterday) was a Weekend (Day {target_day_idx}). Standby.")
        return

    # Use CURRENT system hour to trigger the slot, but pull YESTERDAY'S data
    current_hour = datetime.datetime.utcnow().hour
    slot_hour = (current_hour // 3) * 3
    hour_idx = current_hour // 3
    hour_str = f"{slot_hour:02d}"

    tasks = get_slot_tasks(target_day_idx, hour_idx)

    print(f"\n--- PAVE AUTOMATION | Executing for: {target_date.strftime('%Y-%m-%d')} (Day {target_day_idx}) | Slot {hour_str}:00Z ---")

    for entry in tasks:
        try:
            if "|" in entry:
                dsn, channel = entry.split("|")
                if dsn == "RadCMIP":
                    run_pave("Rad", [channel], hour_str, target_date, base_dir)
                    run_pave("CMIP", [channel], hour_str, target_date, base_dir)
                else:
                    run_pave(dsn, [channel], hour_str, target_date, base_dir)
            else:
                run_pave(entry, None, hour_str, target_date, base_dir)

        except Exception as e:
            print(f"ERROR: Task {entry} failed to execute: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PAVE Automated Cron Scheduler")
    parser.add_argument(
        "--base-dir",
        type=str,
        default=os.path.dirname(os.path.abspath(__file__)),
        help="The base directory containing pave.py. Logs will be stored here."
    )
    args = parser.parse_args()

    if not os.path.isfile(os.path.join(args.base_dir, "pave.py")):
        print(f"CRITICAL ERROR: 'pave.py' not found in the specified base directory: {args.base_dir}")
        sys.exit(1)

    main(args.base_dir)
