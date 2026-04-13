#!/usr/bin/env python3
"""
REFRESH-PAVE: GCCS Product Refresh Rate Metrics
===============================================
VERSION: 1.2.6 (Positional Output Argument)
"""

import argparse
import boto3
import pandas as pd
import re
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# Shared Infrastructure
from pave_utils import Logger, setup_interrupt_handler

# --- CONFIGURATION ---
BUCKETS = ["gccs-products", "gccs-intermediate-products"]
DEFAULT_SATS = ["18", "19"]
MAX_WORKERS = 20 
# ---------------------

class RefreshAnalyzer:
    def __init__(self, args, log):
        self.log = log
        self.year = args.year
        self.doy = args.doy
        self.level = args.level
        # Now a required positional argument
        self.output_file = Path(args.output_file).resolve()
        self.max_workers = MAX_WORKERS
        self.profile = getattr(args, 'profile', 'geocloud')
        
        # Sanitization for S3 path consistency
        raw_sats = [args.sat] if args.sat else DEFAULT_SATS
        self.sats = [str(s).upper().lstrip('G') for s in raw_sats]
        
        self.goes_regex = re.compile(
            r"^[A-Z0-9]+(?:_)?(?P<prefix>I_)?"
            r"(?P<sensor>[A-Z0-9]+)-"
            r"(?P<level>L1b|L2)-"
            r"(?P<product>[A-Z0-9]+?)"
            r"(?P<scene>F|C|M1|M2)?"
            r"(?:-(?P<mode>[A-Z0-9]+)?(?P<channel>C\d{2})?)?_"
            r"(?P<sat>G\d{2})_"
            r"s(?P<start>\d{14})_"
            r"e(?P<end>\d{14})_"
            r"c(?P<created>\d{14})",
            re.IGNORECASE
        )

    def _build_product_key(self, row):
        prefix_sensor = f"{row['prefix']}{row['sensor']}" if pd.notna(row['prefix']) else row['sensor']
        components = [
            prefix_sensor, row['level'], row['product'],
            row['scene'], row['mode'], row['channel'], row['sat']
        ]
        return "_".join([str(c) for c in components if pd.notna(c) and str(c).strip() != ""])

    def analyze_metadata(self, s3_objects):
        df = pd.DataFrame(s3_objects)
        if df.empty: return pd.DataFrame()

        df['Filename'] = df['Key'].str.split('/').str[-1]
        extracted = df['Filename'].str.extract(self.goes_regex.pattern, flags=re.IGNORECASE)
        df = pd.concat([df, extracted], axis=1).dropna(subset=['start'])
        if df.empty: return pd.DataFrame()

        df['ProductType'] = df.apply(self._build_product_key, axis=1)
        df['LastModified_dt'] = pd.to_datetime(df['LastModified'])
        df = df.sort_values(['ProductType', 'LastModified_dt'])
        df['delta'] = df.groupby('ProductType')['LastModified_dt'].diff().dt.total_seconds()

        return df.groupby('ProductType').agg(
            avg_delta_sec=('delta', 'mean'),
            min_delta_sec=('delta', 'min'),
            max_delta_sec=('delta', 'max'),
            file_count=('Filename', 'count'),
            first_start_time=('start', 'first')
        )

    def scrape_folder(self, bucket, path):
        self.log.debug(f"  [S3 READ] s3://{bucket}/{path} (Profile: {self.profile})")
        session = boto3.Session(profile_name=self.profile)
        s3 = session.client('s3')
        paginator = s3.get_paginator('list_objects_v2')
        files = []
        try:
            for page in paginator.paginate(Bucket=bucket, Prefix=path):
                if 'Contents' in page:
                    files.extend([{'Key': o['Key'], 'LastModified': o['LastModified']} for o in page['Contents']])
        except Exception as e:
            self.log.debug(f"Failed to scrape {path}: {e}")
        return files

    def execute(self):
        self.log.debug(f"Initiating Discovery using AWS Profile: {self.profile}")
        session = boto3.Session(profile_name=self.profile)
        s3 = session.client('s3')
        
        self.log.info(f"Discovering GCCS product folders for {self.year}/{self.doy}...")
        
        all_target_paths = []
        for sat in self.sats:
            root_prefix = f"GCCS/op/GOES-{sat}/"
            for bucket in BUCKETS:
                paginator = s3.get_paginator('list_objects_v2')
                
                levels = [f"{root_prefix}{self.level}/"] if self.level else []
                if not levels:
                    self.log.debug(f"  [DISCO] Checking Levels in s3://{bucket}/{root_prefix}")
                    for page in paginator.paginate(Bucket=bucket, Prefix=root_prefix, Delimiter='/'):
                        if 'CommonPrefixes' in page:
                            levels.extend([p['Prefix'] for p in page['CommonPrefixes']])

                for lvl in levels:
                    instr_pages = paginator.paginate(Bucket=bucket, Prefix=lvl, Delimiter='/')
                    for ipage in instr_pages:
                        if 'CommonPrefixes' in ipage:
                            for inst in [p['Prefix'] for p in ipage['CommonPrefixes']]:
                                prod_pages = paginator.paginate(Bucket=bucket, Prefix=inst, Delimiter='/')
                                for ppage in prod_pages:
                                    if 'CommonPrefixes' in ppage:
                                        for prod in [p['Prefix'] for p in ppage['CommonPrefixes']]:
                                            doy_path = f"{prod}{self.year}/{self.doy}/"
                                            self.log.debug(f"    [TARGET] Registered: s3://{bucket}/{doy_path}")
                                            all_target_paths.append((bucket, doy_path))

        self.log.info(f"Found {len(all_target_paths)} potential product folders.")
        all_files = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(self.scrape_folder, b, p): (b, p) for b, p in all_target_paths}
            for future in as_completed(futures):
                res = future.result()
                if res: all_files.extend(res)

        if not all_files:
            self.log.warn(f"No data found for Year {self.year} DOY {self.doy}.")
            return

        self.log.info(f"Analyzing refresh metrics for {len(all_files)} files...")
        report = self.analyze_metadata(all_files)

        if not report.empty:
            report = report.round(2)
            # Logic simplified: output_file is now required
            report.to_csv(self.output_file)
            self.log.info(f"Report saved to {self.output_file}")

            print("\n" + "="*110)
            print(f" GCCS REFRESH RATE REPORT | YEAR {self.year} | DOY {self.doy}")
            print("="*110)
            print(report.to_string())
            print("="*110 + "\n")

def parse_args():
    parser = argparse.ArgumentParser(prog="refresh_pave.py")
    # Positional required arguments
    parser.add_argument("year", help="Year (e.g., 2026)")
    parser.add_argument("doy", help="Day of Year (e.g., 058)")
    parser.add_argument("output_file", help="Path to save the output CSV")
    
    # Optional flags
    parser.add_argument("--sat", help="Satellite number (18 or 19)")
    parser.add_argument("--level", choices=['L1b', 'L2'], help="Product level")
    parser.add_argument("--profile", default="geocloud", help="AWS profile")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-d", "--debug", action="store_true")
    parser.add_argument("-q", "--quiet", action="store_true")
    return parser.parse_args()

def main():
    args = parse_args()
    log = Logger("DEBUG" if args.debug else "VERBOSE" if args.verbose else "QUIET" if args.quiet else "INFO")
    setup_interrupt_handler(log)
    RefreshAnalyzer(args, log).execute()

if __name__ == "__main__":
    main()
