#!/usr/bin/env python3
"""
STATS-PAVE: Statistical Summary Engine
=======================================
Refactored harvester for Glance HTML reports.

VERSION: 2.9.0 (StringIO Fix & FutureWarning Silence)
"""

import argparse
import re
import pandas as pd
from io import StringIO  # Added for Pandas 2.x compatibility
from pathlib import Path
from bs4 import BeautifulSoup as soup
from pave_utils import Logger, setup_interrupt_handler

# =============================================================================
# CLI ARGUMENT DEFINITION
# =============================================================================

def parse_args():
    parser = argparse.ArgumentParser(prog="stats_pave.py")
    parser.add_argument("basepath", type=str, help="Workspace root")
    parser.add_argument("output_file", type=str, nargs="?", help="CSV output path")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-d", "--debug", action="store_true")
    parser.add_argument("-q", "--quiet", action="store_true")
    return parser.parse_args()

# =============================================================================
# CORE ANALYSIS ENGINE
# =============================================================================

class StatsAnalyzer:
    def __init__(self, args, log):
        self.basepath = Path(args.basepath)
        self.output_file = Path(args.output_file) if args.output_file else None
        self.log = log
        self.quiet = getattr(args, 'quiet', False)

        # Identity Regex: Looking for Satellite (G18/19) and Timestamp
        self.goes_regex = re.compile(r"(?P<sat>G1[89]).*?s(?P<start>\d{14})")

        # FULL PRODUCT CONFIGURATION DICTIONARY
        self.alg_config = {
            "CMIP": [ "CMI", "DQF" ],
            "ADP": [ "Cloud", "DQF", "Dust", "PQI1", "PQI2", "Smoke", "SnowIce" ],
            "LST": [ "DQF", "LST", "PQI" ],
            "ESC": [ "EMIS", "RAD" ],
            "ACM": [ "ACM", "BCM", "DQF", "Cloud_Probabilities" ],
            "ACH": ["BETA","COD","COST","EMIS","ERROR_ESTIMATES","INVERSION_FLAG","LPRES","LTEMP","PQI","SHADOW_FLAG"],
            "ACHA": ["DQF","HT"],
            "ACHA2KM": ["DQF","HT"],
            "ACHP2KM": ["DQF","PRES"],
            "ACHT": ["DQF","TEMP"],
            "ACMDIF": ["Cloud_Detection_Flags"],
            "ACTP": ["DQF","Phase"],
            "ACTPPQI": ["PQI"],
            "ACTPTYPE": ["Type"],
            "AICE": ["DQF","IceConc","Mask","PQI","Temp"],
            "AITA": ["DQF","IceAge3","IceAge8","IceThickness","PQI"],
            "AOD": ["AE1","AE2","AE_DQF","AOD","DQF"],
            "BRF": ["BRF1","BRF2","BRF3","BRF5","BRF6","DQF"],
            "BRDFF20": ["BRDF_Parameters_Band1","BRDF_Parameters_Band2","BRDF_Parameters_Band3","BRDF_Parameters_Band5","BRDF_Parameters_Band6","BRDF_QF","Kernels"],
            "CCL": ["CF1","CF2","CF3","CF4","CF5","CL","DQF","Max_TCF","TCF","TCF_MEAN","TCF_MIN","TCF_STDDEV"],
            "CCL2KM": ["CCL","CCP","CF1","CF2","CF3","CF4","CF5","CL","DQF","SCL","SCP","TCF","TCFU"],
            "CLST": ["ClimLST"],
            "COD": ["COD","DQF"],
            "COD2KM": ["COD","DQF"],
            "CPS": ["CPS","DQF"],
            "CTP": ["DQF","PRES"],
            "DMW": ["DQF","pressure","temperature","wind_direction","wind_speed"],
            "DMWDIAG": ["u_component_of_vector1","u_component_of_vector2","v_component_of_vector1","v_component_of_vector2","vertical_temperature_gradient","vertical_wind_shear","weight_cloud_top_pressure"],
            "DMWPQI": ["direction_consistency_test","forecast_consistency_test","quality_indicator","speed_consistency_test","vector_consistency_test"],
            "DMWV": ["DQF","pressure","temperature","wind_direction","wind_speed"],
            "DMWVDIAG": ["tracking_correlation_of_vector1","tracking_correlation_of_vector2","u_component_of_vector1","u_component_of_vector2","v_component_of_vector1","v_component_of_vector2","vertical_temperature_gradient","vertical_wind_shear"],
            "DMWVPQI": ["direction_consistency_test","forecast_consistency_test","local_consistency_test","quality_indicator","speed_consistency_test","vector_consistency_test"],
            "DSI": ["CAPE","DQF_Overall","DQF_Retrieval","DQF_SkinTemp","KI","LI","SI","TT"],
            "DSR": ["DQF","DSR"],
            "ECBH": ["BH","BH_DQF","BP","BT","GT","LBP","SummaryDQF","TH","TP"],
            "EOCH": ["ALT","PRES","TEMP"],
            "ERBCLM": ["BAP_CLM","ClearInstAlb"],
            "ESU": ["Max_Band13","Max_Band14","Min_Band2"],
            "ETEC13": ["EMIS"],
            "ETEC14": ["EMIS"],
            "FDC": ["Area","DQF","Mask","Power","Temp"],
            "FSC": ["DQF","FSC"],
            "LSA": ["DQF","LSA"],
            "LSP": ["Num_Clear","Num_Iter","Ocean_Flag","PW_Low","PW_high","PW_mid","RMSE","Skin_Temp"],
            "LST2KM": ["DQF","LST","PQI"],
            "LVMP": ["DQF_Overall","DQF_Retrieval","DQF_SkinTemp","LVM","pressure"],
            "LVMPR": ["DQF_Overall","DQF_Retrieval","DQF_SkinTemp","LVM","pressure"],
            "LVTP": ["DQF_Overall","DQF_Retrieval","DQF_SkinTemp","LVT","pressure"],
            "LVTPR": ["DQF_Overall","DQF_Retrieval","DQF_SkinTemp","LVT","pressure"],
            "MCMIP": ["CMI_C01","CMI_C02","CMI_C03","CMI_C04","CMI_C05","CMI_C06","CMI_C07","CMI_C08","CMI_C09","CMI_C10","CMI_C11","CMI_C12","CMI_C13","CMI_C14","CMI_C15","CMI_C16","DQF_C01","DQF_C02","DQF_C03","DQF_C04","DQF_C05","DQF_C06","DQF_C07","DQF_C08","DQF_C09","DQF_C10","DQF_C11","DQF_C12","DQF_C13","DQF_C14","DQF_C15","DQF_C16"],
            "NBARF20": ["BRDF_QF","GOESR_NBAR_Band1","GOESR_NBAR_Band2","GOESR_NBAR_Band3","GOESR_NBAR_Band5","GOESR_NBAR_Band6"],
            "PAR": ["DQF","PAR"],
            "RRQPE": ["DQF","RRQPE"],
            "RSR": ["DQF","RSR"],
            "SST": ["DQF","SST"],
            "SWR": ["ClearCompAlb","PQI1","PQI2","PQI3"],
            "SWRD": ["SFC_Down_Diff","SFC_Down_Diff_IC","SFC_Down_Diff_OS","SFC_Down_Diff_SF","SFC_Down_Diff_WC","SFC_Down_IC","SFC_Down_OS","SFC_Down_SF","FC_Down_WC","TOA_Down","TOA_Down_IC","TOA_Down_OS","TOA_Down_SF","TOA_Down_WC"],
            "SWROD": ["RetAOD_OS","RetAOD_SF","RetCOD_IC","RetCOD_WC"],
            "SWRU": ["SFC_Up","SFC_Up_IC","SFC_Up_OS","SFC_Up_SF","SFC_Up_WC","TOA_Up_IC","TOA_Up_OS","TOA_Up_SF","TOA_Up_WC"],
            "TPW": ["DQF_Overall","DQF_Retrieval","DQF_SkinTemp","TPW"],
            "RAD": ["Rad", "DQF"],
            "GEOF": ["DQF","IB_data","IB_mag_ACRF","IB_mag_BRF","IB_mag_ECI","IB_mag_EPN","OB_data","OB_mag_ACRF","OB_mag_BRF","OB_mag_ECI","OB_mag_EPN","total_mag_ACRF"],
        }

    def summarize_stats(self, label, html_files):
        """Parses Glance HTML tables using fuzzy row matching."""
        results = []
        targets = ['r-squared correlation', 'finite in only one fraction']

        for f_path in html_files:
            try:
                with open(f_path, encoding='utf-8', errors='ignore') as fp:
                    page_content = fp.read()

                # Identify satellite and timestamp
                match = self.goes_regex.search(page_content[:10000])
                if not match: continue
                sat, start = match.group('sat'), match.group('start')

                # FIXED: Wrap literal HTML in StringIO to silence FutureWarning
                df_list = pd.read_html(StringIO(page_content))

                found_table = None
                for i, df in enumerate(df_list):
                    if df.empty or df.shape[1] < 2: continue

                    rows = df.iloc[:, 0].astype(str).str.lower()
                    rows = rows.str.replace(r'[^a-z0-9]', '', regex=True)

                    for t in targets:
                        norm_t = re.sub(r'[^a-z0-9]', '', t.lower())
                        if any(norm_t in r for r in rows.values):
                            found_table = df
                            break
                    if found_table is not None: break

                if found_table is None:
                    self.log.debug(f"      [SKIP] No stats table found in {f_path.parent.name}")
                    continue

                found_table.columns = ['Stat', 'Both', 'File A', 'File B'][:found_table.shape[1]]
                found_table.set_index('Stat', inplace=True)

                idx_norm = {str(r): re.sub(r'[^a-z0-9]', '', str(r).lower()) for r in found_table.index}

                for t_name in targets:
                    norm_t = re.sub(r'[^a-z0-9]', '', t_name.lower())
                    match_key = next((k for k, v in idx_norm.items() if norm_t in v), None)

                    if match_key:
                        val = found_table.loc[match_key, 'Both']
                        results.append({
                            'Label': label, 'Sat': sat, 'Start': start,
                            'Metric': t_name, 'Value': val
                        })

            except Exception as e:
                self.log.debug(f"      [ERROR] {f_path.parent.name}: {e}")
                continue

        if results:
            self._write_summary(results)

    def _write_summary(self, results):
        df_res = pd.DataFrame(results)
        df_res['Start_DT'] = pd.to_datetime(df_res['Start'], format="%Y%j%H%M%S%f")

        for (label, metric, sat), group in df_res.groupby(['Label', 'Metric', 'Sat']):
            sorted_group = group.sort_values('Start_DT')
            vals = sorted_group['Value'].astype(float)

            ts_segments = []
            for _, row in sorted_group.iterrows():
                ts_segments.append(f",{row['Start']},{row['Value']}")
            time_series_str = "".join(ts_segments)

            summary_line = (f"{label:35}, {sat:3}, {metric:12}, {len(vals):3}, "
                           f"{vals.min():10.8f}, {vals.max():10.8f}, {vals.mean():10.8f}, "
                           f"{vals.median():10.8f}, {vals.isna().sum():3}")

            if not self.quiet: self.log.info(summary_line)
            if self.output_file:
                with open(self.output_file, 'a') as f:
                    f.write(summary_line + time_series_str + "\n")

    def execute(self):
        """Discovery phase: Instrument -> Product -> Variable."""
        glance_dir = self.basepath / "glance"
        if not glance_dir.exists():
            self.log.error(f"Glance directory missing: {glance_dir}")
            return

        if self.output_file:
            self.output_file.write_text("Product/Var, Sat, Metric, Count, Min, Max, Mean, Median, NaN, Time Series\n")

        all_keys = sorted(self.alg_config.keys(), key=len, reverse=True)

        for instr_dir in [d for d in glance_dir.iterdir() if d.is_dir()]:
            for prod_dir in [p for p in instr_dir.iterdir() if p.is_dir()]:
                norm_name = prod_dir.name.upper().replace('-', '').replace('_', '')
                matched_key = next((k for k in all_keys if k in norm_name), None)

                if not matched_key: continue

                self.log.debug(f"[MATCH] Folder: {prod_dir.name} -> Key: {matched_key}")

                for var in self.alg_config[matched_key]:
                    html_files = list(prod_dir.rglob(f"{var}/index.html"))

                    if not html_files:
                        all_htmls = list(prod_dir.rglob("index.html"))
                        html_files = [h for h in all_htmls if h.parent.name.upper() == var.upper()]

                    if html_files:
                        self.log.debug(f"  [FOUND] {len(html_files)} reports for {var}")
                        self.summarize_stats(f"{prod_dir.name}/{var}", html_files)

if __name__ == "__main__":
    args = parse_args()
    log = Logger("DEBUG" if args.debug else "INFO")
    setup_interrupt_handler(log)
    StatsAnalyzer(args, log).execute()
