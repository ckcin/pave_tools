#! /opt/isatss/noaapy/anaconda/bin/python
#######################################################################################################################
# Script to pull statistics from the Glance results held in html files and summarize them.
# 
# Author Nick Carrasco (hector.n.carrasco@[noaa|nasa].gov
#
# This script extends the expanded version for the "summarize_glance_stats" python tool written by Paul Van Rompay.
# Updates the utility to accept commandline parameters as well as fully populates the dictionary or product variables
# for all products. Additionally accepts output file name to store stats
#######################################################################################################################

import argparse
import re, glob, os
import numpy as np
import pandas as pd
import xarray as xr
from bs4 import BeautifulSoup as soup
from pandas.api.types import is_numeric_dtype

def option_parser():
    parser = argparse.ArgumentParser()

    #positional args
    parser.add_argument("basepath", type=str, help="basepath to process products")
    parser.add_argument("output_file", type=str, help="text file to write results to [optional]", nargs="?", metavar="output")

    parser.add_argument("-v", "--verbose", action="store_true", help="set verbose")
    parser.add_argument("-V", "--verbose_count", action="store_const", default=20, help="depth of verbosity")
    parser.add_argument("-d", "--debug", action="store_true", help="set debug")
    parser.add_argument("-q", "--quiet", action="store_true", help="disable screen output of results")
    parser.add_argument("--test", action="store_true", help="enable testing")
    parser.add_argument("-t", "--table", action="store_true", help="enable table output")

    parser.add_argument("-p", "--product", action="extend", nargs="+",
                        help="products to investigate")
    parser.add_argument("--list_products", action="store_true", default=False,
                        help="list products and variables")
    parser.add_argument("-s", "--append_scene", action="store_true", default=False,
                        help="expand product for all scenes")

    return parser

#-----
# product list and varibles
#-----
scene_extensions = ["F", "C", "M1", "M2", ""]
base_alg_products = {
    "CMIP": [ "CMI", "DQF" ],
    "ADP": [ "Cloud", "DQF", "Dust", "PQI1", "PQI2", "Smoke", "SnowIce" ],
    "LST": [ "DQF", "LST", "PQI" ],  # , "FPT_mitigation_flag" ],
    "ESC": [ "EMIS", "RAD" ],
    "ACM": [ "ACM", "BCM", "DQF", "Cloud_Probabilities" ],
    "ACH": ["BETA","COD","COST","EMIS","ERROR_ESTIMATES","INVERSION_FLAG","LPRES","LTEMP","PQI","SHADOW_FLAG"],

    "ACHA": ["DQF","HT"],
    "ACHA2KM": ["DQF","HT"],
    "ACHP2KM": ["DQF","PRES"],
    "ACHT": ["DQF","TEMP"],
    "ACHT": ["DQF","TEMP"],
    "ACM": ["ACM","BCM","Cloud_Probabilities","DQF"],
    "ACMDIF": ["Cloud_Detection_Flags"],
    "ACTP": ["DQF","Phase"],
    "ACTPPQI": ["PQI"],
    "ACTPTYPE": ["Type"],
    "ADP": ["Cloud","DQF","Dust","PQI1","PQI2","Smoke","SnowIce"],
    "AICE": ["DQF","IceConc","Mask","PQI","Temp"],
    "AITA": ["DQF","IceAge3","IceAge8","IceThickness","PQI"],
    "AOD": ["AE1","AE2","AE_DQF","AOD","DQF"],
    "BRF": ["BRF1","BRF2","BRF3","BRF5","BRF6","DQF"],
    "BRDFF20": ["BRDF_Parameters_Band1","BRDF_Parameters_Band2","BRDF_Parameters_Band3","BRDF_Parameters_Band5","BRDF_Parameters_Band6","BRDF_QF","Kernels"],
    "CCL": ["CF1","CF2","CF3","CF4","CF5","CL","DQF","Max_TCF","TCF","TCF_MEAN","TCF_MIN","TCF_STDDEV"],
    "CCL2KM": ["CCL","CCP","CF1","CF2","CF3","CF4","CF5","CL","DQF","SCL","SCP","TCF","TCFU"],
    "CLST": ["ClimLST"],
    "CMIP": ["CMI","DQF"],
    "COD": ["COD","DQF"], # the following are not found: "IWP","LWP","PQI","VisTransSolar"],
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
    "ESC": ["EMIS","RAD"],
    "ESU": ["Max_Band13","Max_Band14","Min_Band2"],
    "ETEC13": ["EMIS"],
    "ETEC14": ["EMIS"],
    "FDC": ["Area","DQF","Mask","Power","Temp"],
    "FSC": ["DQF","FSC"],
    "LSA": ["DQF","LSA"],
    "LSP": ["Num_Clear","Num_Iter","Ocean_Flag","PW_Low","PW_high","PW_mid","RMSE","Skin_Temp"],
    "LST": ["DQF","LST","PQI"],
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

#    "RAD": [],
    "GEOF": ["DQF","IB_data","IB_mag_ACRF","IB_mag_BRF","IB_mag_ECI","IB_mag_EPN","OB_data","OB_mag_ACRF","OB_mag_BRF","OB_mag_ECI","OB_mag_EPN","total_mag_ACRF"],
#    "SFEU": [],
#    "SFXR": [].
#    "EHIS": [],
#    "MPSL": [],
#    "MPSH": [],
#    "SGPS": [],
#    "FE093": [],
#    "FE131": [],
#    "FE171": [],
#    "FE195": [],
#    "FE284": [],
#    "HE303": [],
    }

ALG_PRODUCTS = { f"{prod}{scene}": stat for prod,stat in base_alg_products.items() for scene in scene_extensions }

# ---------------------------------------------------------------------------------------------------------------------
# Full set of glance statistics to copy & paste above for this summary script
# ---------------------------------------------------------------------------------------------------------------------
COLBOTH, COLEACH, COLFILEA, COLFILEB = 'Both', 'Each', 'File A', 'File B'

# Set statistics to summarize here; Copy & paste from full set below.
STATLIST = {
    'r-squared correlation'             : { 'col': COLBOTH, 'shortname': 'R^2Corr',  'type': 'float64' },
    'finite_in_only_one_fraction'       : { 'col': COLBOTH, 'shortname': 'FinOneFr', 'type': 'float64' },
    }

ALLSTATS = {
    # Finite Data Statistics
             'common_finite_count'               : { 'col': COLBOTH, 'shortname': 'ComFinCt', 'type': 'int32'   },
             'common_finite_fraction  ?'         : { 'col': COLBOTH, 'shortname': 'ComFinFr', 'type': 'float64' },
             'finite_count'                      : { 'col': COLEACH, 'shortname': 'FinCount', 'type': 'int32'   },
             'finite_fraction'                   : { 'col': COLEACH, 'shortname': 'FinFract', 'type': 'float64' },
             'finite_in_only_one_count'          : { 'col': COLBOTH, 'shortname': 'FinOneCt', 'type': 'int32'   },
             'finite_in_only_one_fraction'       : { 'col': COLBOTH, 'shortname': 'FinOneFr', 'type': 'float64' },
    # General Statistics
             'epsilon'                           : { 'col': COLBOTH, 'shortname': 'Epsilon',  'type': 'float64' },
             'epsilon_percent'                   : { 'col': COLBOTH, 'shortname': 'EpsPerct', 'type': 'float64' },
             'max'                               : { 'col': COLEACH, 'shortname': 'MaxVal',   'type': 'float64' },
             'mean'                              : { 'col': COLEACH, 'shortname': 'MeanVal',  'type': 'float64' },
             'median'                            : { 'col': COLEACH, 'shortname': 'MednVal',  'type': 'float64' },
             'min'                               : { 'col': COLEACH, 'shortname': 'MinVal',   'type': 'float64' },
             'missing_value'                     : { 'col': COLEACH, 'shortname': 'MissVal',  'type': 'int32'   },
             'num_data_points'                   : { 'col': COLBOTH, 'shortname': 'NumPoint', 'type': 'int32'   },
             'shape'                             : { 'col': COLBOTH, 'shortname': 'Shape',    'type': 'string'  },
             'spatially_invalid_pts_ignored'     : { 'col': COLEACH, 'shortname': 'IgnorPts', 'type': 'int32'   },
             'std_val'                           : { 'col': COLEACH, 'shortname': 'ValStdDv', 'type': 'float64' },  # Warning: this name is duplicated below, so may not work
    # Missing Value Statistics
             'common_missing_count'              : { 'col': COLBOTH, 'shortname': 'ComMisCt', 'type': 'int32'   },
             'common_missing_fraction'           : { 'col': COLBOTH, 'shortname': 'ComMisFr', 'type': 'float64' },
             'missing_count'                     : { 'col': COLEACH, 'shortname': 'MisCount', 'type': 'int32'   },
             'missing_fraction'                  : { 'col': COLEACH, 'shortname': 'MisFrac',  'type': 'float64' },
    # NaN Statistics
             'common_nan_count'                  : { 'col': COLBOTH, 'shortname': 'ComNaNCt', 'type': 'int32'   },
             'common_nan_fraction'               : { 'col': COLBOTH, 'shortname': 'ComNaNFr', 'type': 'float64' },
             'nan_count'                         : { 'col': COLEACH, 'shortname': 'NaNCount', 'type': 'int32'   },
             'nan_fraction'                      : { 'col': COLEACH, 'shortname': 'NaNFrac',  'type': 'float64' },
    # Numerical Comparison Statistics
             'correlation'                       : { 'col': COLBOTH, 'shortname': 'PearCorr', 'type': 'float64' },
             'diff_outside_epsilon_count'        : { 'col': COLBOTH, 'shortname': 'OutEpCt',  'type': 'int32'   },
             'diff_outside_epsilon_fraction'     : { 'col': COLBOTH, 'shortname': 'OutEpFrc', 'type': 'float64' },
             'max_delta'                         : { 'col': COLBOTH, 'shortname': 'MaxDelta', 'type': 'float64' },
             'max_diff'                          : { 'col': COLBOTH, 'shortname': 'MaxDiff',  'type': 'float64' },
             'mean_delta'                        : { 'col': COLBOTH, 'shortname': 'MeanDelt', 'type': 'float64' },
             'mean_diff'                         : { 'col': COLBOTH, 'shortname': 'MeanDiff', 'type': 'float64' },
             'median_delta'                      : { 'col': COLBOTH, 'shortname': 'MedDelta', 'type': 'float64' },
             'median_diff'                       : { 'col': COLBOTH, 'shortname': 'MedDiff',  'type': 'float64' },
             'min_delta'                         : { 'col': COLBOTH, 'shortname': 'MinDelta', 'type': 'float64' },
             'mismatch_points_count'             : { 'col': COLBOTH, 'shortname': 'MisCount', 'type': 'int32'   },
             'mismatch_points_fraction'          : { 'col': COLBOTH, 'shortname': 'MisFrac',  'type': 'float64' },
             'perfect_match_count'               : { 'col': COLBOTH, 'shortname': 'MatchCt',  'type': 'int32'   },
             'perfect_match_fraction'            : { 'col': COLBOTH, 'shortname': 'MatchFrc', 'type': 'float64' },
             'r-squared correlation'             : { 'col': COLBOTH, 'shortname': 'R^2Corr',  'type': 'float64' },
             'rms_val'                           : { 'col': COLBOTH, 'shortname': 'DiffRMS',  'type': 'float64' },
             'std_val'                           : { 'col': COLBOTH, 'shortname': 'DiffStdD', 'type': 'float64' },  # Warning: this name is duplicated above, so may not work
           }
# ---------------------------------------------------------------------------------------------------------------------

# Regex to parse filename
goes_regex = (
    r"path: .*?/"                     # find path entry
    r"[A-Z0-9]+(?:_[A-Z0-9])?_"       # Environment OR_ or OR_I_
    r"(?P<sensor>[A-Z0-9]+)-"         # Instrument/Sensor (ABI,MAG,etc)
    r"(?P<level>L1b|L2)-"             # Product Level
    r"(?P<product>[A-Z0-9]+?)"        # Product name (Non-greedy)
    r"(?P<scene>F|C|M1|M2)?"          # Scene [optional] (lazy match)
    # Look for the channel inside the suffix, or just match the suffix
    r"(?:-(?P<mode>[A-Z0-9]+)?(?P<channel>C\d{2})?)?_"
    r"(?P<sat>G\d{2})_"               # Satellite ID ]G19|G18]
    r"s(?P<start>\d{14})_"            # Start time
    r"e(?P<end>\d{14})_"              # End time
    r"c(?P<created>\d{14})"           # Creation time
)

def summarize_stats(label, file_glob, ofile):
    # useful constants
    STATS_NAME = 'Finite Data Statistics'
    DASH_LINE  = "-" * 100
    DASH2_LINE = "=" * 100
    SCENES_MAP = { "F": "FD", "C": "CONUS", "M1": "MESO1", "M2": "MESO2" }  # map from filename to full scene name
    G18 = "G18"
    G19 = "G19"

    # Generate list of files that match the provided glob (with wildcards)
    list_of_files = glob.glob(file_glob, recursive=True)
    filecount     = len(list_of_files)

    # Initialize overall data structures; for simplicity, use separate DataFrames for G18, G19
    df_g18 = {}
    df_g19 = {}

    if VERBOSE:
        print(DASH2_LINE)
        print(f"{filecount} files to summarize: {file_glob}")

    # List of statistics fields to pull into dictionary of DataFrames
    statstring = ''
    for stat in STATLIST:
        df_g18[stat] = pd.DataFrame()
        df_g19[stat] = pd.DataFrame()
        statstring = statstring + (', ' if statstring != '' else '') + stat
    if VERBOSE:
        print(f"Collating {statstring}.")
        print(DASH_LINE)

    # iterate through set of files
    iFile = 0
    for file in list_of_files:
        # Initialize file-level data structures to populate from file's statistics
        aStart = bStart = 0
        aEnd   = bEnd   = 0
        aCreat = bCreat = 0
        aCkSum = bCkSum = ''
        aFound = bFound = False
        iFile += 1
        filenameDisplay = file[0:30] + "..." + file[-30:]
        # print(f"Processing file {iFile} of {filecount}: {filenameDisplay:.65}. . .")  # version w/o line overwrite
        if DEBUG: print(f"Processing file {iFile} of {filecount}: {filenameDisplay:.65}. . .", end='\r', flush=True)
        # Extract file information
        with open(file) as fp:
            sp_file = soup(fp, 'html.parser')
        variable_name = re.search(r'variable name: ([^\s]*)',
                                  sp_file.find(string=re.compile('variable name:')).text).group(1)
        for block in sp_file.find_all('blockquote'):
            if DEBUG: print(block)
            #filetimes = re.search(r'path: .*ABI-L2-(\w+)([CFM][12]*)-M\d([C0-9]*)_(G\d+)_s(\d+)_e(\d+)_c(\d+)\.nc',
            fileinfo = re.search(goes_regex, block.text)
            if DEBUG: print(fileinfo.groupdict())
            fileletter = re.search(r'md5sum for File ([AB]): ([^\s]*)', block.text)
            if fileletter.group(1) == 'A' and fileinfo:
                aFound = True
                #aAlg, aScn, aChn, aSat, aStart, aEnd, aCreat = filetimes.groups()
                aAlg, aScn, aChn, aSat = fileinfo['sensor'],fileinfo['scene'],fileinfo['channel'],fileinfo['sat']
                aStart, aEnd, aCreat = fileinfo['start'],fileinfo['end'],fileinfo['created']
                aCkSum = fileletter.group(2)
            elif fileletter.group(1) == 'B' and fileinfo:
                bFound = True
                #bAlg, bScn, bChn, bSat, bStart, bEnd, bCreat = filetimes.groups()
                bAlg, bScn, bChn, bSat = fileinfo['sensor'],fileinfo['scene'],fileinfo['channel'],fileinfo['sat']
                bStart, bEnd, bCreat = fileinfo['start'],fileinfo['end'],fileinfo['created']
                bCkSum = fileletter.group(2)
            elif not fileinfo:
                print("\nFatal Error: Could not parse pathname for File A or File B.")
                print(block.text)
                exit(1)
            else:
                print("\nFatal Error: Found unexpected blockquote that was neither File A nor B.")
                print(block.text)
                exit(1)

        # Validate File A and File B attributes
        if not aFound or not bFound:
            print(f"\nFatal Error: Did not find blockquotes for both File A and B, start times {aStart},{bStart}.")
            exit(1)
        if (aStart != bStart):
            print(f"\nFatal Error: Mismatch in File A and File B start times {aStart} != {bStart}.")
            exit(1)
        if (aEnd != bEnd):
            print(f"\nFatal Error: Mismatch in File A and File B end times {aEnd} != {bEnd}.")
            exit(1)
        if (aSat != bSat):
            print(f"\nFatal Error: Mismatch in File A and File B satellites {aSat} != {bSat}.")
            exit(1)
        #if (aCreat == bCreat) and flag_warning_equal_creation_times:
        #if (aCreat == bCreat):
        #    print(f"\nWarning: File A and File B creation times are equal, which is unexpected but not impossible,",
        #          f"{aCreat} == {bCreat}.")

        pdAlg, pdScn, pdChn, pdSat = aAlg, SCENES_MAP[aScn], aChn, aSat  # assumes a = b values, some validated above
        if not pdChn: pdChn = 'n/a'  # for non-CMI algorithms
        pdStart  = pd.to_datetime(str(aStart), format="%Y%j%H%M%S%f")
        pdEnd    = pd.to_datetime(str(aEnd),   format="%Y%j%H%M%S%f")
        pdCreatA = pd.to_datetime(str(aCreat), format="%Y%j%H%M%S%f")
        pdCreatB = pd.to_datetime(str(bCreat), format="%Y%j%H%M%S%f")

        # Create a list of dataframes from the tables in the file
        df_file = pd.read_html(file, match=STATS_NAME, index_col=STATS_NAME)   # print(df_file[0])
        df_table = pd.DataFrame(df_file[0], columns=[STATS_NAME, COLBOTH, COLFILEA, COLFILEB])
        # print(df_table[STATS_NAME].astype('str'))  # to see all statistics in the HTML table

        # Rename row names (index) to remove HTML tooltip remainder.  Use .columns for column names instead.
        df_table.index = [row.replace('  ? :','') for row in df_table.index]

        # TODO: detect NaN for R^2 when DQF is all 0 and matches exactly, consider replacing with float('inf') and see how this affects the max, mean

        df_dict = df_g18 if (pdSat == G18) else df_g19
        for stat in STATLIST:
            stattype = STATLIST[stat]['type']
            statcol  = STATLIST[stat]['col']
            if statcol != COLBOTH:
                print(f"Fatal error: this script only supports statistics in the {COLBOTH} column; remove: {stat}"+" "*16)
                exit(1)
            df_stat = df_table.loc[stat]  # to list, use df_table.columns or df_table.index
            newstat = pd.DataFrame({ stat: df_stat[statcol], 'Alg': pdAlg, 'Chn': pdChn,
                                     'Scene': pdScn, 'Sat': pdSat }, index=[pdStart])
            if df_dict[stat].empty:
                df_dict[stat] = pd.DataFrame(data=newstat)
            else:
                newstatconv = newstat
                df_dict[stat] = pd.concat([df_dict[stat], newstatconv]).sort_index()
        # end for stat in statlist:
    # end for file in list_of_files:

    if VERBOSE:
        print(f"\n{DASH2_LINE}")
        print("Summary")

    # Cycle through statistics to summarize each
    g18count = g19count = 0
    for sat in [G18, G19]:
        df_dict = df_g18 if (sat == G18) else df_g19
        for stat in STATLIST:
            if stat not in df_dict[stat]:
                print(f"missing {stat} for {label}")
                continue #skip to next stat
            stattype = STATLIST[stat]['type']
            statshort = STATLIST[stat]['shortname']
            statset   = df_dict[stat][stat].astype(stattype)
            statsize  = len(statset)
            if sat == G18:
                g18count = statsize
            else:
                g19count = statsize
            if VERBOSE: print(f"{sat} {stat}: {statsize} glance reports")
            if statsize < VERBOSE_COUNT:
                if VERBOSE: print(statset.to_string(dtype=False))
            else:
                if VERBOSE:
                    halfcount = int(VERBOSE_COUNT / 2)
                    print(statset.head(halfcount).to_string(dtype=False))
                    print(". . . {} more glance reports . . .".format(statsize-VERBOSE_COUNT))
                    print(statset.tail(halfcount).to_string(dtype=False))
            if statsize > 0 and is_numeric_dtype(statset):
                nancount   = statset.isna().sum()
                statmean   = statset.mean()
                statmedian = float('nan') if statsize==nancount else statset.median()
                statmin    = statset.min()
                statmax    = statset.max()
            else:
                nancount = 0
                statmean = statmedian = statmin = statmax = 0.0
            if TABLE:
                timeseries=f"".join([',{0},{1}'.format(k, v) for k,v in statset.items()])
                if not QUIET: print(f"{label:21}, {sat:3}, {statshort:8}, {statsize:3}, {statmin:10.8f}, {statmax:10.8f},",
                                    f"{statmean:10.8f}, {statmedian:10.8f}, {nancount:3}," + " "*16)  # extra space to clear line
                if ofile is not None : ofile.write(f"{label:21}, {sat:3}, {statshort:8}, {statsize:3}, {statmin:10.8f}, {statmax:10.8f}, "+
                                                   f"{statmean:10.8f}, {statmedian:10.8f}, {nancount:3}, {timeseries}"+"\n")

            else:
                if not QUIET: print(f"{label:21} {sat:3} {statshort:8} count: {statsize:3}, min: {statmin:10.8f},",
                                    f"max: {statmax:10.8f}, mean: {statmean:10.8f}, median: {statmedian:10.8f},",
                                    f"NaN count: {nancount:3}")
                if ofile is not None: ofile.write(f"{label:21} {sat:3} {statshort:8} count: {statsize:3}, min: {statmin:10.8f},"+
                                                  f"max: {statmax:10.8f}, mean: {statmean:10.8f}, median: {statmedian:10.8f},"+
                                                  f"NaN count: {nancount:3}"+"\n")
            if VERBOSE: print("-" * 100)
        # end for stat in statlist:
    # end for sat loop

    if TESTING: print(df_g18['r-squared correlation'])
    if TESTING: print(df_g19['r-squared correlation'])

    if VERBOSE:
        print(f"{filecount} files to summarize: {file_glob}")
        print(f"{g18count} G18 files, {g19count} G19 files")
        print(DASH2_LINE)

# end function summarize_stats

def getProductList(basepath):
    glance_path=basepath+"/glance_reports/*/*" #added extra folder depth to account for instr (hnc)
    if VERBOSE: print("getting product list from glance report folder: "+glance_path)
    prods=[os.path.basename(path).replace("-m6","") for path in glob.glob(glance_path)]
    return prods
# end function getProductList

# ---------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
# ---------------------------------------------------------------------------------------------------------------------
    parser = option_parser()
    args = parser.parse_args()
    DEBUG,VERBOSE,VERBOSE_COUNT,TESTING,TABLE,QUIET = args.debug, args.verbose, args.verbose_count, args.test, args.table, args.quiet
    if DEBUG: VERBOSE=DEBUG

    if DEBUG : print(args)

    if args.list_products :
        print("\n".join(f"{k}: {','.join(v)}" for k,v in base_alg_products.items()))
        exit(0)

    if VERBOSE: print("collecting summarized glance stats")

    if DEBUG: print("output will go to: "+args.output_file)
    ofile = open(args.output_file, "w")
    if TABLE and ofile is not None:
        ofile.write(f"Alg/Prod, Sat, Stat, Num, Min, Max, Mean, Median, NaN, Time Series"+"\n")

    products = args.product if args.product else getProductList(args.basepath)

    if args.append_scene :
        products = [f"{prod}/{scene}" for prod in products for scene in ["conus", "fd", "meso1", "meso2"] ]

    for product in products:
        if not QUIET: print(f"processing: {product}")
#        for prodvar in ALG_PRODUCTS.get(product.split("/")[0].split("_")[0].upper(), []):
        for prodvar in ALG_PRODUCTS.get(re.split(r'[/_-]', product, maxsplit=1)[0].upper(), []):
            if DEBUG : print(f"prodvar is : {prodvar} for {product}")
            prodglob=f"{args.basepath}/glance_reports/**/{product}*/**/*report*/{prodvar}/index.html"
            if DEBUG : print(f"prodglob: {prodglob}")
            prodglobresults=glob.glob(prodglob, recursive=True)
            if DEBUG : print(f"glob results: {prodglobresults}")
            if not prodglobresults: continue
            # get detailed product name from first file glob
            #prodname=next(token for token in prodglobresults[0].split("/") if product.split("/")[0] in token)
            #summarize_stats(f"{prodname} - {prodvar}", prodglob, ofile)
            summarize_stats(f"{product} - {prodvar}", prodglob, ofile)

