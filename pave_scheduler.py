#!/usr/bin/env python3
"""
PAVE: Automated Cron Scheduler
==============================
VERSION: 1.4.1 (Simplified DSN + Channel Pairing + Cron Docs)

DEPLOYMENT INSTRUCTIONS:
Add the following line to your system crontab (crontab -e) to run this 
script at 00:00, 03:00, 06:00, 09:00, 12:00, 15:00, 18:00, and 21:00 UTC.
Be sure to replace the paths with your actual absolute paths.

CRONTAB ENTRY:
0 0,3,6,9,12,15,18,21 * * * /usr/bin/python3 /absolute/path/to/pave_scheduler.py >> /absolute/path/to/pave_automation.log 2>&1
"""

import subprocess
import datetime
import sys

# --- PRODUCT ROTATION LIST (44 Entries) ---
# Each "RadCMIP|XX" counts as one rotation entry but triggers two pave calls.
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

def get_slot_tasks(day_idx, hour_idx):
    """Rotates through the 44 entries across the 40 weekly slots."""
    scheduled = []
    for i, entry in enumerate(RAW_PRODUCTS):
        # Logic: Distributes products so every product hits every hour over time
        if (i + hour_idx) % 5 == day_idx:
            scheduled.append(entry)
    return scheduled

def run_pave(dsn, channels, hour_str):
    """Executes the standard pave.py call."""
    now = datetime.datetime.utcnow()
    timestamp = f"{now.year}{now.strftime('%j')}{hour_str}0"
    
    cmd = [
        sys.executable, "pave.py", dsn,
        "--times", timestamp,
        "--use-compare"
    ]
    if channels:
        cmd.extend(["--channels"] + channels)

    print(f"[{datetime.datetime.now()}] EXECUTING: {' '.join(cmd)}")
    subprocess.run(cmd)

def main():
    now = datetime.datetime.utcnow()
    day_idx = now.weekday()
    
    if day_idx > 4:
        print("Weekend: No automation tasks scheduled.")
        return

    # Calculate 3-hour slot (0, 3, 6, 9, 12, 15, 18, 21)
    slot_hour = (now.hour // 3) * 3
    hour_idx = now.hour // 3
    hour_str = f"{slot_hour:02d}"

    tasks = get_slot_tasks(day_idx, hour_idx)

    print(f"--- PAVE AUTOMATION | Day {day_idx} | Slot {hour_str}:00Z ---")
    
    for entry in tasks:
        try:
            if "|" in entry:
                dsn, channel = entry.split("|")
                
                # SPECIAL CASE: Pair Rad and CMIP for the same channel
                if dsn == "RadCMIP":
                    run_pave("Rad", [channel], hour_str)
                    run_pave("CMIP", [channel], hour_str)
                else:
                    # Standard product with specific channel (e.g. DMW)
                    run_pave(dsn, [channel], hour_str)
            else:
                # Standard product (e.g. ACHT, LST)
                run_pave(entry, None, hour_str)
                
        except Exception as e:
            print(f"ERROR: Task {entry} failed: {e}")

if __name__ == "__main__":
    main()
