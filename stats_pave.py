#!/usr/bin/env python3
"""
STATS-PAVE: Statistical Summary Engine
=======================================
VERSION: 3.1.3 (Corrected Class & Log Level)
"""

import argparse
import re
import pandas as pd
import numpy as np
import sys
from io import StringIO
from pathlib import Path
from pave_utils import Logger, setup_interrupt_handler

class StatsHarvester:
    def __init__(self, args, log):
        self.glance_dir = Path(args.glance_fld).resolve()
        dest = Path(args.dest_fld).resolve()
        # Output file logic: stays in stats/ or specified filename
        self.output_file = dest / "glance_stats_summary.csv" if dest.is_dir() else dest
        self.log = log
        self.quiet = getattr(args, 'quiet', False)

        # Capture DSN (between OR_ and _G##), Sat, and Start Timestamp
        self.goes_regex = re.compile(r"OR_(?P<dsn>.*?)_(?P<sat>G1[89]).*?s(?P<start>\d{14})")

        # Complete Algorithm Configuration Mapping
        self.alg_config = {
            "CMIP": ["CMI", "DQF"],
            "ADP": ["Cloud", "DQF", "Dust", "PQI1", "PQI2", "Smoke", "SnowIce"],
            "LST": ["DQF", "LST", "PQI"],
            "ESC": ["EMIS", "RAD"],
            "ACM": ["ACM", "BCM", "DQF", "Cloud_Probabilities"],
            "ACH": ["BETA", "COD", "COST", "EMIS", "ERROR_ESTIMATES", "INVERSION_FLAG", "LPRES", "LTEMP", "PQI", "SHADOW_FLAG"],
            "ACHA": ["DQF", "HT"],
            "ACHA2KM": ["DQF", "HT"],
            "ACHP2KM": ["DQF", "PRES"],
            "ACHT": ["DQF", "TEMP"],
            "ACMDIF": ["Cloud_Detection_Flags"],
            "ACTP": ["DQF", "Phase"],
            "ACTPPQI": ["PQI"],
            "ACTPTYPE": ["Type"],
            "AICE": ["DQF", "IceConc", "Mask", "PQI", "Temp"],
            "AITA": ["DQF", "IceAge3", "IceAge8", "IceThickness", "PQI"],
            "AOD": ["AE1", "AE2", "AE_DQF", "AOD", "DQF"],
            "BRF": ["BRF1", "BRF2", "BRF3", "BRF5", "BRF6", "DQF"],
            "BRDFF20": ["BRDF_Parameters_Band1", "BRDF_Parameters_Band2", "BRDF_Parameters_Band3", "BRDF_Parameters_Band5", "BRDF_Parameters_Band6", "BRDF_QF", "Kernels"],
            "CCL": ["CF1", "CF2", "CF3", "CF4", "CF5", "CL", "DQF", "Max_TCF", "TCF", "TCF_MEAN", "TCF_MIN", "TCF_STDDEV"],
            "CCL2KM": ["CCL", "CCP", "CF1", "CF2", "CF3", "CF4", "CF5", "CL", "DQF", "SCL", "SCP", "TCF", "TCFU"],
            "CLST": ["ClimLST"],
            "COD": ["COD", "DQF"],
            "COD2KM": ["COD", "DQF"],
            "CPS": ["CPS", "DQF"],
            "CTP": ["DQF", "PRES"],
            "DMW": ["DQF", "pressure", "temperature", "wind_direction", "wind_speed"],
            "DMWDIAG": ["u_component_of_vector1", "u_component_of_vector2", "v_component_of_vector1", "v_component_of_vector2", "vertical_temperature_gradient", "vertical_wind_shear", "weight_cloud_top_pressure"],
            "DMWPQI": ["direction_consistency_test", "forecast_consistency_test", "quality_indicator", "speed_consistency_test", "vector_consistency_test"],
            "DMWV": ["DQF", "pressure", "temperature", "wind_direction", "wind_speed"],
            "DMWVDIAG": ["tracking_correlation_of_vector1", "tracking_correlation_of_vector2", "u_component_of_vector1", "u_component_of_vector2", "v_component_of_vector1", "v_component_of_vector2", "vertical_temperature_gradient", "vertical_wind_shear"],
            "DMWVPQI": ["direction_consistency_test", "forecast_consistency_test", "local_consistency_test", "quality_indicator", "speed_consistency_test", "vector_consistency_test"],
            "DSI": ["CAPE", "DQF_Overall", "DQF_Retrieval", "DQF_SkinTemp", "KI", "LI", "SI", "TT"],
            "DSR": ["DQF", "DSR"],
            "ECBH": ["BH", "BH_DQF", "BP", "BT", "GT", "LBP", "SummaryDQF", "TH", "TP"],
            "EOCH": ["ALT", "PRES", "TEMP"],
            "ERBCLM": ["BAP_CLM", "ClearInstAlb"],
            "ESU": ["Max_Band13", "Max_Band14", "Min_Band2"],
            "ETEC13": ["EMIS"],
            "ETEC14": ["EMIS"],
            "FDC": ["Area", "DQF", "Mask", "Power", "Temp"],
            "FSC": ["DQF", "FSC"],
            "LSA": ["DQF", "LSA"],
            "LSP": ["Num_Clear", "Num_Iter", "Ocean_Flag", "PW_Low", "PW_high", "PW_mid", "RMSE", "Skin_Temp"],
            "LST2KM": ["DQF", "LST", "PQI"],
            "LVMP": ["DQF_Overall", "DQF_Retrieval", "DQF_SkinTemp", "LVM", "pressure"],
            "LVMPR": ["DQF_Overall", "DQF_Retrieval", "DQF_SkinTemp", "LVM", "pressure"],
            "LVTP": ["DQF_Overall", "DQF_Retrieval", "DQF_SkinTemp", "LVT", "pressure"],
            "LVTPR": ["DQF_Overall", "DQF_Retrieval", "DQF_SkinTemp", "LVT", "pressure"],
            "MCMIP": ["CMI_C01", "CMI_C02", "CMI_C03", "CMI_C04", "CMI_C05", "CMI_C06", "CMI_C07", "CMI_C08", "CMI_C09", "CMI_C10", "CMI_C11", "CMI_C12", "CMI_C13", "CMI_C14", "CMI_C15", "CMI_C16", "DQF_C01", "DQF_C02", "DQF_C03", "DQF_C04", "DQF_C05", "DQF_C06", "DQF_C07", "DQF_C08", "DQF_C09", "DQF_C10", "DQF_C11", "DQF_C12", "DQF_C13", "DQF_C14", "DQF_C15", "DQF_C16"],
            "NBARF20": ["BRDF_QF", "GOESR_NBAR_Band1", "GOESR_NBAR_Band2", "GOESR_NBAR_Band3", "GOESR_NBAR_Band5", "GOESR_NBAR_Band6"],
            "PAR": ["DQF", "PAR"],
            "RRQPE": ["DQF", "RRQPE"],
            "RSR": ["DQF", "RSR"],
            "SST": ["DQF", "SST"],
            "SWR": ["ClearCompAlb", "PQI1", "PQI2", "PQI3"],
            "SWRD": ["SFC_Down_Diff", "SFC_Down_Diff_IC", "SFC_Down_Diff_OS", "SFC_Down_Diff_SF", "SFC_Down_Diff_WC", "SFC_Down_IC", "SFC_Down_OS", "SFC_Down_SF", "FC_Down_WC", "TOA_Down", "TOA_Down_IC", "TOA_Down_OS", "TOA_Down_SF", "TOA_Down_WC"],
            "SWROD": ["RetAOD_OS", "RetAOD_SF", "RetCOD_IC", "RetCOD_WC"],
            "SWRU": ["SFC_Up", "SFC_Up_IC", "SFC_Up_OS", "SFC_Up_SF", "SFC_Up_WC", "TOA_Up_IC", "TOA_Up_OS", "TOA_Up_SF", "TOA_Up_WC"],
            "TPW": ["DQF_Overall", "DQF_Retrieval", "DQF_SkinTemp", "TPW"],
            "RAD": ["Rad", "DQF"],
            "GEOF": ["DQF", "IB_data", "IB_mag_ACRF", "IB_mag_BRF", "IB_mag_ECI", "IB_mag_EPN", "OB_data", "OB_mag_ACRF", "OB_mag_BRF", "OB_mag_ECI", "OB_mag_EPN", "total_mag_ACRF"],
            "FE093": ["RAD", "DQF"],
            "FE131": ["RAD", "DQF"],
            "FE171": ["RAD", "DQF"],
            "FE195": ["RAD", "DQF"],
            "FE284": ["RAD", "DQF"],
            "HE303": ["RAD", "DQF"],
            "SFXR": ["irradiance_xrsa1", "irradiance_xrsa2", "primary_xrsa", "irradiance_xrsb1", "irradiance_xrsb2", "primary_xrsb", "xrs_ratio", "corrected_current_xrsa_1", "corrected_current_xrsa_2", "corrected_current_xrsa_3", "corrected_current_xrsa_4", "corrected_current_xrsb_1", "corrected_current_xrsb_2", "corrected_current_xrsb_3", "corrected_current_xrsb_4", "sps_temperature"],
            "SFEU": ["irradianceSpectrum", "euvsa_corrected_currents_256", "euvsa_corrected_currents_284", "euvsa_corrected_currents_304", "euvsa_corrected_currents_dark", "euvsb_corrected_currents_1175 ", "euvsb_corrected_currents_1216", "euvsb_corrected_currents_1335", "euvsb_corrected_currents_1405", "euvsb_corrected_currents_dark"],
        }

    def summarize_stats(self, var_name, html_files):
        """Extracts specific correlation metrics from HTML tables."""
        results = []
        targets = ['r-squared correlation', 'finite_in_only_one_fraction']

        self.log.debug(f"  [SUMMARIZE] Processing {len(html_files)} HTML files for Variable: {var_name}")

        for f in html_files:
            try:
                with open(f, encoding='utf-8', errors='ignore') as fp:
                    page = fp.read()
                if not page.strip():
                    self.log.debug(f"    [SKIP] Empty HTML file: {f.name}")
                    continue

                # Verify file identity from the report metadata
                m = self.goes_regex.search(page[:10000])
                if not m:
                    self.log.debug(f"    [SKIP] Could not find DSN/Metadata in HTML: {f.name}")
                    continue

                # Scrape all tables from the page
                dfs = pd.read_html(StringIO(page))
                self.log.debug(f"    [TABLES] Found {len(dfs)} tables in {f.name}")

                for df in dfs:
                    if df.empty or df.shape[1] < 2: continue

                    # Normalize first column (Stat names) for matching
                    rows_norm = df.iloc[:, 0].astype(str).str.lower().str.replace(r'[^a-z0-9]', '', regex=True)

                    for tname in targets:
                        normt = re.sub(r'[^a-z0-9]', '', tname.lower())
                        if any(normt in r for r in rows_norm.values):
                            # Set standard columns for the stats table
                            df.columns = ['Stat', 'Both', 'File A', 'File B'][:df.shape[1]]
                            v_idx = df.set_index('Stat').index
                            matchk = next((k for k in v_idx if normt in re.sub(r'[^a-z0-9]', '', str(k).lower())), None)

                            if matchk:
                                val = df.set_index('Stat').loc[matchk, 'Both']
                                self.log.debug(f"      [MATCH] Found {tname}: {val}")
                                results.append({
                                    'Product': m.group('dsn'),
                                    'Variable': var_name,
                                    'Sat': m.group('sat'),
                                    'Start': m.group('start'),
                                    'Metric': tname,
                                    'Value': val
                                })
            except Exception as e:
                self.log.debug(f"    [ERROR] Failed to parse {f.name}: {e}")
                continue

        if results:
            self._write_summary(results)
        else:
            self.log.debug(f"  [SUMMARIZE] No matching metrics found for {var_name}")

    def _write_summary(self, res):
        """Appends aggregate results and timeseries to the CSV."""
        df = pd.DataFrame(res).sort_values('Start')

        with open(self.output_file, 'a') as f:
            # Group by DSN/Variable/Metric to create a single row per combination
            for (prod, var, metric, sat), group in df.groupby(['Product', 'Variable', 'Metric', 'Sat'], sort=False):
                vals = pd.to_numeric(group['Value'], errors='coerce').dropna()
                if vals.empty: continue

                # Metadata (Columns 1-10)
                meta_fields = [
                    prod, var, sat, metric, str(len(vals)),
                    f"{vals.min():.8f}", f"{vals.max():.8f}",
                    f"{vals.mean():.8f}", f"{vals.median():.8f}",
                    str(group['Value'].isna().sum())
                ]

                # Flattened Time Series: T1, V1, T2, V2...
                ts_flat = []
                for _, row in group.iterrows():
                    ts_flat.append(str(row['Start']))
                    ts_flat.append(str(row['Value']))

                csv_line = ",".join(meta_fields) + "," + ",".join(ts_flat) + "\n"
                f.write(csv_line)

                # REFINEMENT: Mean value reporting moved to debug
                if not self.quiet:
                    self.log.debug(f"{prod:<30} | {var:<15} | Mean: {vals.mean():.8f}")

    def execute(self):
        """Iterates through Glance workspace to find valid report files."""
        if not self.glance_dir.exists():
            self.log.error(f"Missing: {self.glance_dir}")
            return

        self.log.info(f"Scanning Glance workspace: {self.glance_dir}")
        header = "Product,Variable,Sat,Metric,Count,Min,Max,Mean,Median,NaN,T1,V1,T2,V2,T3,V3...\n"
        self.output_file.write_text(header)

        # Keys sorted by length (descending) to prevent short-key mismatches (e.g., ACHA vs ACHA2KM)
        allk = sorted(self.alg_config.keys(), key=len, reverse=True)

        for instr_dir in [d for d in self.glance_dir.iterdir() if d.is_dir()]:
            self.log.debug(f"Checking Instrument Dir: {instr_dir.name}")

            for prod_dir in [p for p in instr_dir.iterdir() if p.is_dir()]:
                norm = prod_dir.name.upper().replace('-', '').replace('_', '')
                match = next((k for k in allk if k in norm), None)

                if match:
                    self.log.debug(f"  [MATCHED] Product folder {prod_dir.name} matched config key: {match}")
                    for var in self.alg_config[match]:
                        # Look for index.html in variable-specific subfolders
                        htmls = list(prod_dir.rglob(f"{var}/index.html")) or \
                                [h for h in prod_dir.rglob("index.html") if h.parent.name.upper() == var.upper()]

                        if htmls:
                            self.summarize_stats(var, htmls)
                        else:
                            self.log.debug(f"    [MISSING] No HTML reports found for {var} in {prod_dir.name}")
                else:
                    self.log.debug(f"  [SKIP] No config mapping for folder: {prod_dir.name}")

def parse_args():
    parser = argparse.ArgumentParser(prog="stats_pave.py")
    parser.add_argument("glance_fld", help="Input directory containing instrument subfolders")
    parser.add_argument("dest_fld", help="Destination folder or filename for summary CSV")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-d", "--debug", action="store_true")
    parser.add_argument("-q", "--quiet", action="store_true")
    return parser.parse_args()

def main():
    args = parse_args()
    log = Logger("DEBUG" if args.debug else "VERBOSE" if args.verbose else "QUIET" if args.quiet else "INFO")
    setup_interrupt_handler(log)
    # The class must be named StatsHarvester for pave.py compatibility
    StatsHarvester(args, log).execute()

if __name__ == "__main__":
    main()
