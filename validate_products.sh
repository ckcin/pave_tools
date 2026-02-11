#! /usr/bin/env bash
#######################################################################################################################
# FILE:           validate_products_v2.sh
# DESCRIPTION:    Version 2 of PAVE collectoin and validation routines for GCCS
# LIMITATIONS:    None
# AUTHOR:         Nick Carrasco <hector.n.carrasco@nasa.gov
# SOFTWARE HISTORY:
#   Nov 20, 2025 nickc : initial implemenation consisting of initial cli, retrieval of GCCS, and on-prem products
#                      : for on-prem, if retrieval from GPAS fails, fail-over attempt from NODD
#   Nov 21, 2025 nickc : cleaned up on-prem retrieval and added retrieval of IP from egress files
#   Nov 24, 2025 nickc : ported glance and metadata functions
#   Dec 01, 2025 nickc : fixed folder structure for glance reports, and updated glob in stat tool.
#   Dec 03, 2025 nickc : verified IP retrieval, updated glance_report to include timeseries
#   Dec 05, 2025 nickc : updated to loop over all scenes for ABI L2
#   Dec 07, 2025 nickc : update to tar/gz ncdump and glance report folders and removal to save space
#   Dec 08, 2025 nickc : disabled removal of glance reports
#   Dec 12, 2025 nickc : updated glance to only use the "start time" to match files
#   Dec 22, 2025 nickc : added tagging structure for folder names
#   Jan 16, 2026 nickc : replaced metadata script with new "simpler" analyzer tool
#   Jan 20, 2026 nickc : added routine to collocated DMW... will need to expand for GLM
#   Feb 05, 2026 nickc : added basic ability to collected data for multiple timeframes separated with a ','
#######################################################################################################################
# TODO:
# [-] [YYYY-MM-DD] To-do template
# [-] [YYYY-MM-DD] Consider feature to add all retrieved GCCS files to an array so that on future pulls only new files are pulled from on-prem
# [-] [YYYY-MM-DD] update to use common shared utility script
# [-] [YYYY-MM-DD] update to loop over all channels for ABI L1, will need for CMIP too - may be OBE
# [-] [YYYY-MM-DD] feature to pull previously stored IP data mounted archive bucket (/buckets/geotowr-proghost/IP_Data/)
#
# [-] [2026-02-05] add feature to use multiple timestamps
# [-] [2026-01-20] implement specialized glance reporting for DWM (may be usable for GLM)
# [x] [2026-01-16] integrate new metadata analyzer
# [x] [2025-12-22] add cli to apply tag and prefix to folder names for better tracking
# [x] [2025-12-03] test and verify IP retrieval
# [x] [2025-12-01] fix folder structure for glance reports to be per product, may involve fixes to glance_stats.py
# [x] [2025-11-24] port metadata analysis
# [x] [2025-11-24] port glance report generation
# [x] [2025-11-21] port functionality for IP data
# [x] [2025-11-20] port 'generic' GCCS pull function
# [x] [2025-11-21] port pull on-prem from NODD
# [x] [2025-11-21] port pull on-prem from geocloud
# [x] [2025-11-20] add cli
#######################################################################################################################

# Paths to PAVE scripts/tools
pave_bin=/data/to05/scripts
glance_cfg=$pave_bin/glance_summarize/configuration
analysis_path=$PWD/YYYYDDDhh

# AWS S3 buckets:
GCCS="gccs-products"; GCCS_IP="gccs-intermediate-products"; GCCS_BASE_PREFIX="GCCS/op"
PREM="geoproducts-ops"; PREM_BASE_PREFIX="op"
NODD="noaa-goes##" # ## to be replaced with sat number inline
ONE_TIMELINE=false
S3_PROGRESS="--no-progress"
SCENE_LIST="f c m1 m2"

####### ----- HELP ----- #######
function print_help {

  echo -e "Usage:"
  echo -e "$PROGRAM [options] <date_hour> <product(s)>"
  echo
  echo -e "Arguments:"
  echo -e "\t <date_hour> \t date for data collection, format YYYYDDDHH[M], add M for single timeline"
  echo -e "\t <product(s)> \t list of products to validate"
  echo
  echo -e "Options:"
  echo -e "\t --collect_only \t only retrieve data, no reporting"
  echo -e "\t --skip_gccs \t skip collection of gccs produced data"
  echo -e "\t --skip_prem \t skip collection of on-prem produced data"
  echo -e "\t --force_nodd \t force usage of NODD for on-prem produced data"
  echo
  echo -e "\t --report_only \t analyze previously collected data"
  echo -e "\t --skip_glance \t skip production of glance reports"
  echo -e "\t --skip_metadata \t skip production of metadata reports"
  echo -e "\t --glance_flags \t added/use additional flags in glance run [eg:--glance_flags \"-e 0,5\""
  echo
  echo -e "\t --scene_list [f c m1 m2] \n\t\t specific list of scenes to validate a product for (quote list)"
  echo
  echo -e "\t --prefix [PREFIX] \t <date_hour> is used as folder name, prefix used to help identify run"
  echo -e "\t --tag [TAG] \t used to add additional details to folder name, is appended"
  echo
  echo -e "\t -v|--verbose \t verbose messaging"
  echo -e "\t -d|--debug \t debug messaging"
  echo -e "\t -D|--tool_debug \t debug tools called by scritp"
  echo -e "\t -h|--help \t prints this message"

  echo -e "\t -t|--test \t runs simple test routine with hardwired paramets"

  exit 0
}

####### ----- UTILS ---- #######
VERBOSE=false; DEBUG=false; TEST=false
TOOL_DEBUG=false

function msgdate() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')]"
}

function info() {
  echo -e "$(msgdate) [info  ] ${FUNCNAME[1]}: $@"
}

function verbose() {
  if "$VERBOSE"; then
    echo -e "$(msgdate) [verbose] ${FUNCNAME[1]}: $@"
  fi
}

function debug() {
  if "$DEBUG"; then
    echo -e "$(msgdate) [debug  ] ${FUNCNAME[1]}: $@"
  fi
}

function WARN() {
  echo -e "$(msgdate) [WARN   ] ${FUNCNAME[1]}: $@"
}

function ERROR() {
  echo -e "$(msgdate) [ERROR  ] ${FUNCNAME[1]}: $@"
  exit 1
}

error_log=""
function catch() {
  local line=$1
  local cmd=$2
  # Append traceback details to the variable
  error_log+="[$(date)] Error on line $line: '$cmd' failed\n"
}

####### ----- script functions ----- ######
# analysis directory setup
function set_paths {
  # prepend directory name with tag
  analysis_path=${analysis_path/YYYYDDDhh/${PREFIX:+${PREFIX}_}YYYYDDDhh${TAG:+_$TAG}}
  debug "analysis_path=$analysis_path"
  local timestamp=$1

  analysis_path=${analysis_path/YYYYDDDhh/$timestamp}; debug "analysis path=$analysis_path"
  mkdir -p $analysis_path

  gccs_path=$analysis_path/gccs; debug "gccs_path=$gccs_path"
  prem_path=$analysis_path/prem; debug "prem_path=$prem_path"
  glance_path=$analysis_path/glance_reports; debug "glance_path=$glance_path"
  ncdump_path=$analysis_path/ncdump; debug "ncdump_path=$ncdump_path"
  metadata_path=$analysis_path/metadata; debug "metadata_path=$metadata_path"
}

####### vvvvv PRODUCT MAP vvvvv ######
#Instrument Map, matching products to instrument
declare -A INSTRUMENT
INSTRUMENT['RAD']='ABI'
INSTRUMENT['GEOF']='MAG'
INSTRUMENT['SFEU']='EXIS'
INSTRUMENT['SFXR']='EXIS'
INSTRUMENT['EHIS']='SEIS'
INSTRUMENT['MPSL']='SEIS'
INSTRUMENT['MPSH']='SEIS'
INSTRUMENT['SGPS']='SEIS'
INSTRUMENT['FE093']='SUVI'
INSTRUMENT['FE131']='SUVI'
INSTRUMENT['FE171']='SUVI'
INSTRUMENT['FE195']='SUVI'
INSTRUMENT['FE284']='SUVI'
INSTRUMENT['HE303']='SUVI'

function isL1() {
  if [ "${INSTRUMENT[${1^^}]+_}" ]; then return 0; else return 1; fi
}
###### ^^^^^ PRODUCT MAP ^^^^^ ######

####### ----- RETREIVAL ---- #######
# Regex Breakdown:
# ([^-]+)        1. Instrument (ABI)
# -([^-]+)       2. Level (L2)
# -([^-]+)       3. Product (DMWF)
# (-M[0-9]+)?    4. Optional Mode (-M6)
# (C[0-9]+)?     5. Optional Channel (C07)
# _G([0-9]+)     6. Satellite (18)
# _s([0-9]+)     7. Start
# _e([0-9]+)     8. End
# _c([0-9]+)     9. Created
GOES_FILE_PATTERN='^OR_([^-]+)-([^-]+)-([^-]+)(-M[0-9]+)?(C[0-9]+)?_G([0-9]+)_s([0-9]+)_e([0-9]+)_c([0-9]+)\.nc$'

function get_gccs() {
  verbose "starting collection of gccs produced products"
  local start_path=$PWD; debug "start_path=$start_path"
  local timestamp=$1
  local product=$2
  local instrument=${3:-"ABI"}
  local level=${4:-"L2"}
  debug "${FUNCNAME[0]}: instrument=$instrument, level=$level"

  # if only one timeline wanted for hour append 0 to get only data for first 10 minutes at top of hour, only for ABI and SUVI
#  if [[ $ONE_TIMELINE && ($instrument == "ABI" || $instrument == "SUVI") ]]; then timestamp=${timestamp}0; fi

  local dest=${gccs_path}/${instrument}/${product}; #mkdir -p $dest

  verbose "retrieving $product from instrument: $instrument for time:$timestamp from s3 bucket: $GCCS storing at: $dest"
  local prefix="${GCCS_BASE_PREFIX}/GOES-sat/$level/$instrument/$product"; #debug "prefix=$prefix"
  local query="Contents[?contains(Key,'${instrument}-${level}-${product^^}') && contains(Key, 'Gsat_s${timestamp}')].Key"; #debug "query=$query"

  #temp fix for L1 - remove when prefixes updated
  if [[ $level = "L1b" ]]; then prefix=${prefix/$product/}; fi

  for sat in {18..19}; do
    verbose "retrieving ops data for GOES-$sat"
    # replace placeholder for satellite id
    debug "prefix=${prefix/sat/$sat}"
    debug "query=${query/sat/$sat}"
    aws s3api list-objects-v2 --profile geocloud --bucket $GCCS --prefix ${prefix/sat/$sat} --query "${query/sat/$sat}" --output json | jq -r '.[]?' |
    while read file; do
      debug "retrieving $(basename $file) from s3 bucket"
      [[ $(basename $file) =~ $GOES_FILE_PATTERN ]] || true; debug "parsed file: ${BASH_REMATCH[@]}"
      local channel="${BASH_REMATCH[5]:-}"; debug channel=$channel

      local doy=$(basename $(dirname $file)); debug doy=$doy
      local year=$(basename $(dirname $(dirname $file))); debug year=$year
      local dated_dest=$dest${channel:+_$channel}/$year/$doy; debug dated_dest=$dated_dest
      mkdir -p $dated_dest
      aws s3 cp --profile geocloud s3://$GCCS/$file $dated_dest/ $S3_PROGRESS
    done
    if [[ $level = "L2" ]]; then #retrieve L2 IP
      verbose "retrieving L2 IP data for GOES-$sat"
      aws s3api list-objects-v2 --profile geocloud --bucket $GCCS_IP --prefix ${prefix/sat/$sat} --query "${query/sat/$sat}" --output json | jq -r '.[]?' |
      while read file; do
        debug "retrieving $file from s3 bucket"
        local doy=$(basename $(dirname $file)); debug doy=$doy
        local year=$(basename $(dirname $(dirname $file))); debug year=$year
        aws s3 cp --profile geocloud s3://$GCCS_IP/$file $dest/$year/$doy/ $S3_PROGRESS
      done
    fi
  done

}

function get_gccs_products() {
  info "starting collection of gccs produced products"
  local start_path=$PWD; debug "start_path=$start_path"
  local timestamp=$1; debug "timestamp=$timestamp"
  local -n products=$2

  debug "gccs_path=$gccs_path"
  mkdir -p $gccs_path

  for product in "${products[@]}"; do
    verbose "collecting gccs products for $product"
    if isL1 $product; then
      debug "retrieving L1b products"
      get_gccs $timestamp $product ${INSTRUMENT[${product^^}]} "L1b"
    else #L2
      debug "retrieving L2 products"
      for scene in $SCENE_LIST; do
        get_gccs $timestamp $product$scene
      done
    fi

    if [ -z "$(find $gccs_path/**/$product*/ -type f)" ]; then
      echo "WARNING: no products retrieved for product: $product"
    fi
  done
}

function get_on_prem_products() {
  # retrieves on-prem products based on available products from gccs
  info "starting collections of matching products from on-prem"
  local start_path=$PWD; debug "start_path=$start_path"
  local timestamp=$1; debug "timestamp=$timestamp"

  debug "gccs_path=$gccs_path"
  debug "prem_path=$prem_path"

  # retrieve matching files for non-IP data
  for gccs_file in $(find $gccs_path -type f ! -name "*I_ABI*"); do
    verbose "${FUNCNAME[0]}: current gccs_file for matching: $gccs_file"

    local dest=$(dirname ${gccs_file/gccs/prem}); debug "dest=$dest"
    mkdir -p $dest

    local delim="_"
    local pattern=$(basename $gccs_file); pattern="${pattern%${delim}*}"; debug "pattern=$pattern"

    local sat; if [[ $pattern == *"G19"* ]]; then sat="GOES-19"; else sat="GOES-18"; fi; debug "current sat: $sat"
    local instr=$(echo $pattern | awk -F'[-_]' '{print $2}'); debug "current instr: $instr"
    local level=$(echo $pattern | awk -F'[-_]' '{print $3}'); debug "current level: $level"
    local prod=$(echo $pattern | awk -F'[-_]' '{print $4}'); debug "current product: $prod"

    # pull exact starting time from file pattern
    local prod_time=$(echo $pattern | awk -F'[s_]' '{print $5}'); debug "prod_time: $prod_time"
    local year=${prod_time:0:4}; debug "year=$year"
    local month=$(date --date="jan 1 + ${prod_time:4:3} days - 1 days" +%b | tr '[:upper:]' '[:lower:]'); debug "month=$month"
    local date=$(date --date="jan 1 + ${prod_time:4:3} days - 1 days" +%Y%m%d); debug "date=$date"
    local doy=${prod_time:4:3}; debug "doy=$doy"
    local hour=${prod_time:7:2}; debug "hour=$hour"

    # first attempt to pull from GPAS
    local sign_request=""
    verbose "${FUNCNAME[0]}: searching and retrieving from GPAS"
    local prefix="op/$sat/${level,,}/${instr/SEIS/SEISS}/$year/$month/$date"; debug "prefix=$prefix"
    local query="Contents[?contains(Key, '$pattern')].Key"; debug "query=$query"
    local bucket=$PREM
    local profile="--profile geocloud"
    if $force_nodd; then prefix="force/failure/of/gpas"; fi
    local s3file=$(aws s3api list-objects-v2 --profile geocloud --bucket $bucket --prefix $prefix --query "$query" --output json | jq -r '.[]')
    if [[ -z $s3file ]]; then
      verbose "WARNING: no file found on GPAS checking NODD"
      sign_request="--no-sign-request"
      bucket=$([ $sat = "GOES-19" ] && echo "noaa-goes19" || echo "noaa-goes18"); debug "nodd bucket=$bucket"
      prefix="$instr-$level-${prod^^}/$year/$doy/$hour"; debug "nodd prefix=$prefix"

      s3file=$(aws s3api list-objects-v2 --bucket $bucket $sign_request --prefix $prefix --query "$query" --output json | jq -r '.[]')

      profile=""
    fi

    verbose "attempting retrieval of s3file: $s3file"
    debug "basename is: $(basename $s3file)"
    if [[ -n $s3file && ! -f $dest/$(basename $s3file) ]]; then 
      aws s3 cp $profile s3://$bucket/$s3file $dest $sign_request $S3_PROGRESS
    elif [[ -f $dest/$(basename $s3file) ]]; then
      debug "WARNING: $(basename $s3file) previously retrieved"
    else
      echo "ERROR: no matching file found for pattern: $pattern"
    fi

  done # end retrieval of matching ops products (non-IP)

  # retrieve matching ABI IP products
  local ip_tarballs_not_retrieved=true
  verbose "collecting IP data if needed"
  for gccs_file in $(find $gccs_path -type f -name "*I_ABI*"); do
    local dest=$(dirname ${gccs_file/gccs/prem}); debug "dest=$dest"
    mkdir -p $dest

    local delim="_"
    local pattern=$(basename $gccs_file); pattern="${pattern%${delim}*}"; debug "pattern=$pattern"

    local sat; if [[ $pattern == *"G19"* ]]; then sat="GOES-19"; else sat="GOES-18"; fi; debug "sat=$sat"

    local ip_time=$(echo $pattern | awk -F'[s_]' '{print $6}'); debug "${FUNCNAME[0]}: ip_time=$ip_time"
    local ip_doy=${ip_time:4:3}; debug "ip_doy=$ip_doy"
    local ip_hour=${ip_time:7:2}; debug "ip_hour=$ip_hour"

    # only need to download once
    local g18ip_file=GOES-18_ABI_L2_IntermediateProducts_day${ip_doy}_hour${ip_hour}.tar
    local g19ip_file=GOES-19_ABI_L2_IntermediateProducts_day${ip_doy}_hour${ip_hour}.tar
    if [[ -r $prem_path/$g18ip_file && -r $prem_path/$g19ip_file ]]; then ip_tarballs_not_retrieved=false; fi

    if $ip_tarballs_not_retrieved; then
      verbose "retrieving IP tarballs"
      local g18ip="s3://geoegress/egresout/DOE1L2IP/GOES-18"
      local g19ip="s3://geoegress/egresout/DOE1L2IP/GOES-19"
      aws s3 cp --profile geocloud $g18ip/$g18ip_file $prem_path $S3_PROGRESS
      aws s3 cp --profile geocloud $g19ip/$g19ip_file $prem_path $S3_PROGRESS
      ip_tarballs_not_retrieved=false
    fi

    # retrieve individual IP files from tarballs
    local tarball; if [[ $sat == "GOES-19" ]]; then tarball=$prem_path/$g19ip_file; else tarball=$prem_path/$g18ip_file; fi
    local ip_file=$(tar tf $tarball | grep $pattern); debug "ip_file: $ip_file"

    if [[ -n "$ip_file" ]]; then
      mkdir -p ip_temp
      tar xf $tarball -C ip_temp $ip_file
      mv ip_temp/$ip_file $dest
      rm -rf ip_temp
    fi

  done # end retrieval of matching ABI IP products
}

####### ----- ANALYZE ---- #######
function run_metadata_analysis() {
  info "Performing Metadata Analysis"

  local analyzer=$pave_bin/metadata_scripts/analyze_metadata.sh
  [[ -x $analyzer ]] || { ERROR "$analyzer not found or executable" >&2; }

  mkdir -p $metadata_path

  for product in $(find $prem_path -type d -links 2 ! -empty); do
    product_dir=$(dirname $(dirname $product)); debug product_dir=$product_dir #remove yyyy/ddd
    product_rpt=$metadata_path/$(basename $product_dir).csv; debug product_rpt=$product_rpt

    $TOOL_DEBUG && local flag="--verbose"
    debug $analyzer $flag --overwrite $product_dir ${product_dir/prem/gccs} $product_rpt
    $analyzer $flag --overwrite $product_dir ${product_dir/prem/gccs} $product_rpt
  done

  # collect and cleanup
  cat $(find $metadata_path -name "*.csv") > $analysis_path/metadata_summary.csv
  (cd $analysis_path; tar cfz metadata.tar.gz metadata; rm -rf $metadata_path)
}

function run_glance_collocation_analysis() {
  product=$1
  debug dmw processing $product

  local prod=${product##*/}; debug prod=$prod
  # prep data using glance collocate
  local tmp_dir=${gccs_path/gccs/tmp}; mkdir -p $tmp_dir
  local gccs_file=""
  verbose "Collocating product: $product"
  local collocated=false
  for gccs_file in $(find $product -type f); do
    debug gccs_file=$gccs_file
    local target=$(basename ${gccs_file%"_e"*}); debug target=$target
    local doy=$(basename $(dirname $gccs_file)); debug doy=$doy
    local year=$(basename $(dirname $(dirname $gccs_file))); debug year=$year

#    [[ $(basename $gccs_file) =~ $GOES_FILE_PATTERN ]] || true; debug "parsed file: ${BASH_REMATCH[@]}"
#    local channel="${BASH_REMATCH[5]:-}"; debug channel=$channel

    local prem_file=$(find $(dirname ${gccs_file/gccs/prem}) -name $target*); debug prem_file=$prem_file

    ## inspect and verify winds contained in file by checking size of lat/lon
    local NO_LON="\{0/Inf\}"
    local prem_lon=$(h5ls $prem_file/lon); debug prem_lon=$prem_lon
    local gccs_lon=$(h5ls $gccs_file/lon); debug gccs_lon=$gccs_lon
    if [[ $prem_lon =~ $NO_LON || $gccs_lon =~ $NO_LON ]]; then WARN "no winds reported in either $prem_file or $gccs_file"; continue; fi

    ## collocate
    verbose "Collocating $gccs_file with $prem_file"
    debug $glance collocate -c $glance_cfg/dmw_collocate.py -p $tmp_dir $gccs_file $prem_file
    local traceback=$( { $glance collocate -c $glance_cfg/dmw_collocate.py -p $tmp_dir $gccs_file $prem_file; } 3>&1 1>&2 2>&3 3>&- )
    if (( $? == 1 )); then WARN "Glance reported error:\nCaptured Traceback:\n$traceback"; continue; fi

    ## mv to collocated folders
    verbose "moving collocated files"
    #local coll_path_gccs=$(dirname ${gccs_file/gccs/coll_gccs}); coll_path_gccs=${coll_path_gccs/\/$year/-$channel\/$year}; debug coll_path_gccs=$coll_path_gccs
    local coll_path_gccs=$(dirname ${gccs_file/gccs/coll_gccs}); debug coll_path_gccs=$coll_path_gccs
    mkdir -p $coll_path_gccs
    local coll_file_gccs=$(find $tmp_dir -name $(basename ${gccs_file/.nc/})*); debug coll_file_gccs=$coll_file_gccs
    mv $coll_file_gccs $coll_path_gccs/$(basename ${coll_file_gccs/-collocated/})

    local coll_path_prem=${coll_path_gccs/gccs/prem}; debug coll_path_prem=$coll_path_prem
    mkdir -p $coll_path_prem
    local coll_file_prem=$(find $tmp_dir -name $(basename ${prem_file/.nc/})*); debug coll_file_prem=$coll_file_prem
    mv $coll_file_prem $coll_path_prem/$(basename ${coll_file_prem/-collocated/})
    collocated=true
  done
  verbose "Collocation complete for $product"

  if [[ $collocated == false ]]; then WARN "no winds collocated for product: $product"; return 1; fi

  # run glance report providing collocated folders, nolatlon flag and specified variables
  for collocated in $(find $(dirname ${coll_path_prem})* -type d -links 2); do
    collocated=$(dirname $(dirname $collocated)) #remove yyyy/ddd
    debug collocated=$collocated
    glance_report=${collocated/coll_prem/glance_reports}; debug glance_report=$glance_report; mkdir -p $glance_report
    verbose "glance comparison: $coll_path_prem vs $coll_path_gccs to: $glance_report"
    debug $glance report -c $glance_cfg/dmw_report.py -p $glance_report --stripfromname e.* $collocated ${collocated/prem/gccs}
    traceback=$( { $glance report -c $glance_cfg/dmw_report.py -p $glance_report --stripfromname e.* $collocated ${collocated/prem/gccs}; } 3>&1 1>&2 2>&3 3>&- )
    if (( $? == 1 )); then WARN "Glance reported error:\nCaptured Traceback:\n$traceback"; fi
  done

  rm -rf $tmp_dir
}

function run_glance_analysis() {
  info "Generating Glance Reports"
  glance=/data/glance/miniforge3/envs/glance_user/bin/glance

  for product in $(find $gccs_path -type d -links 2 ! -empty); do
    product=$(dirname $(dirname $product)) #remove yyyy/ddd

    glance_report=${product/gccs/glance_reports}; debug glance_report=$glance_report

    # execute specialized glance procedures for dmw
    if [[ "$(basename ${product,,})" =~ "dmw" ]]; then run_glance_collocation_analysis $product; continue; fi

    mkdir -p $glance_report
    $TOOL_DEBUG && local flag="--verbose"
    debug $glance report $flag --fork --nolonlat $glance_flags -p $glance_report ${product/gccs/prem} $product --stripfromname e.*
    $glance report $flag --fork --nolonlat $glance_flags -p $glance_report ${product/gccs/prem} $product --stripfromname e.*
  done

  verbose "Summarizing Glance Reports"
  local summarizer=$pave_bin/glance_summarize/glance_stats.py

  debug $summarizer -t $analysis_path $analysis_path/glance_summary.csv
  $summarizer -t $analysis_path $analysis_path/glance_summary.csv
  (cd $analysis_path; tar cfz glance_reports.tar.gz glance_reports)
}

####### ----- TEST ----- #######
function set_test() {
  DEBUG=true; VERBOSE=true
  date_hour=202532111
  product_names=("acm" "ach" "mpsh" "geof")
  ONE_TIMELINE=true
}

####### ----- MAIN ----- #######
PROGRAM=$(basename "$0")

info "Starting Product Validation"

####### ----- CLI  ----- #######
#default values
run_gccs=true
run_prem=true
run_glance=true
run_metadata=true
force_nodd=false
glance_flags=""

ARGS=$(getopt -o hvdtD --long collect_only,skip_gccs,skip_prem,force_nodd,report_only,skip_glance,skip_metadata,glance_flags:,scene_list:,prefix:,tag:,help,verbose,debug,test,tool_debug -- "$@")
eval set -- ${ARGS}
while :
do
  case $1 in
    --collect_only  )      run_glance=false; run_metadata=false; shift ;;
    --skip_gccs     )      run_gccs=false; shift ;;
    --skip_prem     )      run_prem=false; shift ;;
    --force_nodd    )      force_nodd=true; shift ;;

    --report_only   )      run_gccs=false; run_prem=false; shift ;;
    --skip_glance   )      run_glance=false; shift ;;
    --skip_metadata )      run_metadata=false; shift ;;
    --glance_flags  )      glance_flags=$2; shift 2 ;;

    --scene_list )         SCENE_LIST=$2; shift 2 ;;

    --prefix )             PREFIX=$2; shift 2 ;;
    --tag    )             TAG=$2; shift 2 ;;

    -h | --help    ) print_help $prog ;;
    -v | --verbose ) VERBOSE=true; shift ;;
    -d | --debug   ) DEBUG=true; VERBOSE=true; shift ;;
    -t | --test    ) TEST=true; DEBUG=true; VERBOSE=true; shift ;;

    -D | --tool_debug ) TOOL_DEBUG=true; shift ;;

    -- ) shift; break ;;
    *  ) echo "invalid options: -$1"; print_help $prog ;;
  esac
done
# store positional arguments
date_hour=${1:-""}; shift
product_names=("$@"); debug "product list: ${product_names[@]}"

# reset s3 progress if debug
if $DEBUG; then S3_PROGRESS=""; fi

####### ----- TEST ----- #######
if $TEST; then set_test; fi
####### ----- TEST ----- #######

if [[ -z $date_hour ]]; then echo -e "date_hour required\n"; print_help $PROGRAM; exit 1; fi
if [ ${#product_names[@]} -eq 0 ]; then echo -e "include prod names to best knowledge\n"; print_help $PROGRAM; exit 1; fi

IFS=',' read -ra dates <<< "$date_hour"; date_hour="${dates[0]}"
set_paths $date_hour

for timestamp in ${dates[@]}; do
  if $run_gccs; then get_gccs_products $timestamp "product_names"; fi
done
if $run_prem; then get_on_prem_products $date_hour; fi

if $run_metadata; then run_metadata_analysis $date_hour; fi
if $run_glance; then run_glance_analysis $date_hour; fi

info "... processing complete"
# happy dance you are at the end :-P
