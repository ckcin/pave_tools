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
#   Feb 19, 2026 nickc : added basic threading to aws calls
#######################################################################################################################
# TODO:
# [-] [YYYY-MM-DD] To-do template
# [-] [YYYY-MM-DD] update config of paths to scriptsa and tools to be in config file
# [-] [YYYY-MM-DD] Consider feature to add all retrieved GCCS files to an array so that on future pulls only new files
#                  are pulled from on-prem
# [-] [YYYY-MM-DD] update to use common shared utility script
# [-] [YYYY-MM-DD] update to loop over all channels for ABI L1, will need for CMIP too - may be OBE
# [-] [YYYY-MM-DD] feature to pull previously stored IP mounted archive bucket (/buckets/geotowr-proghost/IP_Data/)
#
# [-] [2026-02-19] update to use threads when retrieving s3 data
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

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)

# AWS S3 buckets:
GCCS="gccs-products"; GCCS_IP="gccs-intermediate-products"; GCCS_BASE_PREFIX="GCCS/op"
PREM="geoproducts-ops"; PREM_BASE_PREFIX="op"
NODD="noaa-goes##" # ## to be replaced with sat number inline

# AWS configuration
aws configure set default.s3.max_concurrent_requests 20
aws configure set default.s3.max_queue_size 10000
aws configure set default.s3.multipart_threshold 64MB
S3_PROGRESS="--no-progress"

# assitive globals
SCENE_LIST="f c m1 m2"

####### ----- HELP ----- #######
function print_help {
  printf "Usage: %s [options] <date_hour> <product(s)>\n" ${BASH_SOURCE[-1]}
  printf "%s validates products produced by GCCS against on-prem products\n" $PROGRAM

  printf "\nArguments:\n"
  printf "  %-20s %s\n" "<date_hour>" "date for data collection, format YYYYDDDHH[M], add M for single timeline"
  printf "  %-20s %s\n" "<product(s)>" "list of products to validate"

  printf "\nOptions:\n"
  printf "  %-20s %s\n" "--collect_only" "only retrieve data, no reporting"
  printf "  %-20s %s\n" "--skip_gccs" "skip collection of gccs produced data"
  printf "  %-20s %s\n" "--skip_prem" "skip collection of on-prem produced data"
  printf "  %-20s %s\n" "--force_nodd" "force usage of NODD for on-prem produced data"
  printf "\n"
  printf "  %-20s %s\n" "--report_only" "analyze previously collected data"
  printf "  %-20s %s\n" "--skip_glance" "skip production of glance reports"
  printf "  %-20s %s\n" "--skip_metadata" "skip production of metadata reports"
  printf "  %-20s %s\n" "--glance_flags" "added/use additional flags in glance run [eg:--glance_flags \"-e 0,5\"]"
  printf "\n"
  printf "  %-20s %s\n" "--scene_list [f c m1 m2]" "specific list of scenes to validate a product for (quote list)"
  printf "\n"
  printf "  %-20s %s\n" "--prefix [PREFIX]" "<date_hour> is used as folder name, prefix used to help identify run"
  printf "  %-20s %s\n" "--tag [TAG]" "used to add additional details to folder name, is appended"
  printf "\n"
  printf "  %-20s %s\n" "-c|--config" "use specified config file"
  printf "\n"
  printf "  %-20s %s\n" "--test" "runs simple test routine with hardwired paramets"
  printf "  %-20s %s\n" "-v|--verbose" "verbose messaging"
  printf "  %-20s %s\n" "-d|--debug" "debug messaging"
  printf "  %-20s %s\n" "-D|--tool_debug" "debug tools called by script"
  printf "  %-20s %s\n" "-h|--help" "prints this message"

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
INSTRUMENT['LCFA']='GLM'
INSTRUMENT['FED']='GLM'

function getInstrument() {
  echo ${INSTRUMENT[${1^^}]:-"ABI"}
}

function getLevel() {
  local prod=${1^^}
  if [[ -v INSTRUMENT[$prod] && ${INSTRUMENT[$prod]} != "GLM" ]]; then echo "L1b"; else echo "L2"; fi
}

function isABI() {
  [[ $(getInstrument $1) == "ABI" ]] && return 0 || return 1;
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

function deprecated_get_gccs() {
  verbose "starting collection of gccs produced products"
  local timestamp=$1
  local product=$2
  local instrument=${3:-"ABI"}
  local level=${4:-"L2"}
  debug "${FUNCNAME[0]}: instrument=$instrument, level=$level"

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

    local thread_count=0
    aws s3api list-objects-v2 --profile geocloud --bucket $GCCS --prefix ${prefix/sat/$sat} --query "${query/sat/$sat}" --output json | jq -r '.[]?' |
    while read file; do
      (
      debug "retrieving $(basename $file) from s3 bucket"
      [[ $(basename $file) =~ $GOES_FILE_PATTERN ]] || true; debug "parsed file: ${BASH_REMATCH[@]}"
      local channel="${BASH_REMATCH[5]:-}"; debug channel=$channel

      local doy=$(basename $(dirname $file)); debug doy=$doy
      local year=$(basename $(dirname $(dirname $file))); debug year=$year
      local dated_dest=$dest${channel:+_$channel}/$year/$doy; debug dated_dest=$dated_dest
      mkdir -p $dated_dest
      aws s3 cp --profile geocloud s3://$GCCS/$file $dated_dest/ $S3_PROGRESS
      ) &
      ((thread_count++))
      if [[ $thread_count -ge $MAXTHREADS ]]; then wait -n; ((thread_count--)); fi
    done
    if [[ $level = "L2" ]]; then #retrieve L2 IP
      verbose "retrieving L2 IP data for GOES-$sat"
      aws s3api list-objects-v2 --profile geocloud --bucket $GCCS_IP --prefix ${prefix/sat/$sat} --query "${query/sat/$sat}" --output json | jq -r '.[]?' |
      while read file; do
        (
        debug "retrieving $file from s3 bucket"
        local doy=$(basename $(dirname $file)); debug doy=$doy
        local year=$(basename $(dirname $(dirname $file))); debug year=$year
        aws s3 cp --profile geocloud s3://$GCCS_IP/$file $dest/$year/$doy/ $S3_PROGRESS
        ) &
        ((thread_count++))
        if [[ $thread_count -ge $MAXTHREADS ]]; then wait -n; ((thread_count--)); fi
      done
    fi
  done

  # wait for any remaining threads
  wait
  verbose "all gccs retrieval threads compmlete"
}

function deprecated_get_gccs_products() {
  info "starting gccs data collection"
  local timestamp=$1; debug timestamp=$timestamp
  local -n products=$2

  debug analysis_path=$analysis_path
  debug gccs_path=$gccs_path

  mkdir -p $gccs_path

  for product in "${products[@]}"; do
    local instrument=$(getInstrument $product)
    local level=$(getLevel $product)
    verbose "collecting gccs data for $product"

    debug "retrieving $product for instrument $instrument $level"
    if ! isABI $product; then SCENE_LIST=""; fi
    for scene in ${SCENE_LIST:-}; do get_gccs $timestamp $product$scene $instrument $level; done

    if [ -z "$(find $gccs_path/**/$product*/ -type f)" ]; then
      echo "WARNING: no products retrieved for product: $product"
    fi
  done
}

function get_gccs_products() {
  info "Starting GCCS Retrieval (Lowercase Folders / Original Filenames)"
  local timestamp=$1       # YYYYdddhhm
  local -n products_ref=$2  # Array of base products
  local thread_count=0

  local year=${timestamp:0:4}
  local doy=${timestamp:4:3}

  for sat_num in 18 19; do
    local sat_id="GOES-${sat_num}"

    for prod in "${products_ref[@]}"; do
      local inst=$(getInstrument "$prod")
      local lvl=$(getLevel "$prod")

      local current_scenes=""
      if isABI "$prod"; then current_scenes=${SCENE_LIST:-}; fi

      for scene in ${current_scenes:-""}; do
        # Prefix Discovery: e.g., ops/GOES-18/L2/ABI/dmwf
        local s_prefix="${GCCS_BASE_PREFIX}/${sat_id}/${lvl}/${inst}/${prod,,}${scene,,}"

        # 1. NATIVE DISCOVERY
        local matching_keys=$(aws s3api list-objects-v2 --profile geocloud \
          --bucket "$GCCS" --prefix "$s_prefix" \
          --query "Contents[?contains(Key, '_s${timestamp}')].Key" \
          --output text)

        if [[ -z "$matching_keys" || "$matching_keys" == "None" ]]; then continue; fi

        # 2. DYNAMIC PATH MAPPING
        declare -A processed_dirs
        read -ra keys_array <<< "$matching_keys"

        for s3_key in "${keys_array[@]}"; do
          local s3_dir=$(dirname "$s3_key")

          if [[ -z ${processed_dirs["$s3_dir"]} ]]; then
            # Extract folder segment from S3 path
            local after_inst="${s3_key#*/${inst}/}"
            local s3_folder="${after_inst%%/*}"

            # --- FOLDER NAMING LOGIC ---
            # Everything in the path is forced to lowercase
            local folder_name="${s3_folder,,}"

            # Convert _C02 to -c02 in the folder name
            if [[ "$s3_folder" == *"_"* ]]; then
                local base_p="${s3_folder%_*}"
                local chan_p="${s3_folder#*_}"
                folder_name="${base_p,,}-c${chan_p#C}"
            # Catch channel in filename for flat L1b structures
            elif [[ "$s3_key" =~ C([0-9]{2}) ]]; then
                local chan_num="${BASH_REMATCH[1]}"
                [[ ! "$folder_name" == *"-c${chan_num}"* ]] && folder_name="${folder_name}-c${chan_num}"
            fi

            # Final Local Destination: .../gccs/ABI/dmwf-c02/2026/014
            local dest="${gccs_path}/${inst}/${folder_name}/${year}/${doy}"



            (
              mkdir -p "$dest"

              # SURGICAL SYNC
              # Note: Filenames are NOT changed by sync; they remain Uppercase as on S3.
              aws s3 sync "s3://${GCCS}/${s3_dir}/" "$dest/" \
                --profile geocloud --exclude "*" --include "*_s${timestamp}*" \
                --no-progress $S3_PROGRESS

              # IP SYNC (Mapping Ops directory structure to IP bucket)
              local ip_dir="${s3_dir/${GCCS_BASE_PREFIX}/${GCCS_IP_PREFIX}}"
              aws s3 sync "s3://${GCCS_IP}/${ip_dir}/" "$dest/" \
                --profile geocloud --exclude "*" --include "*_s${timestamp}*" \
                --no-progress $S3_PROGRESS 2>/dev/null
            ) &

            processed_dirs["$s3_dir"]=1
            ((thread_count++))
            if [[ $thread_count -ge $MAXTHREADS ]]; then wait -n; ((thread_count--)); fi
          fi
        done
        unset processed_dirs
      done
    done
  done
  wait
  find "$gccs_path" -type d -empty -delete
}

function deprecated_get_on_prem_products() {
  # retrieves on-prem products based on available products from gccs
  info "starting collections of matching products from on-prem"
  local start_path=$PWD; debug "start_path=$start_path"
  local timestamp=$1; debug "timestamp=$timestamp"

  debug "gccs_path=$gccs_path"
  debug "prem_path=$prem_path"

  local thread_count=0

  # retrieve matching files for non-IP data
  for gccs_file in $(find $gccs_path -type f ! -name "*I_ABI*"); do
    (
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
    ) &

    ((thread_count++))
    if [[ $thread_count -ge $MAXTHREADS ]]; then wait -n; ((thread_count--)); fi

  done # end retrieval of matching ops products (non-IP)

  # wait for any remaining threads
  wait
  verbose "all gccs retrieval threads compmlete"

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
      aws s3 cp --profile geocloud $g18ip/$g18ip_file $prem_path $S3_PROGRESS &
      aws s3 cp --profile geocloud $g19ip/$g19ip_file $prem_path $S3_PROGRESS &
      wait
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

function deprecated_v2_get_on_prem_products() {
  info "Starting collections of matching products from On-Prem"
  local timestamp=$1
  local thread_count=0

  # Create a unique temporary directory for this specific process instance
  TMP_DIR=$(mktemp -d -t $(basename ${BASH_SOURCE[-1]})-XXXXXXXXXX)
  debug "TMP_DIR=$TMP_DIR"
  local manifest_dir="$TMP_DIR/manifests"
  mkdir -p "$manifest_dir"

  # --- STEP 1: BATCH MANIFEST GENERATION ---
  info "Building local S3 manifests for matching..."
  declare -A seen_prefixes

  for gccs_file in $(find "$gccs_path" -type f); do
      local filename=$(basename "$gccs_file")

      if [[ $filename =~ $GOES_FILE_PATTERN ]]; then
          local instr="${BASH_REMATCH[1]}"
          local lvl="${BASH_REMATCH[2]}"
          local sat_num="${BASH_REMATCH[6]}"
          local sat_id="GOES-$sat_num"
          local s_time="${BASH_REMATCH[7]}"

          [[ $filename == *"I_"* ]] && continue

          local year=${s_time:0:4}
          local doy=${s_time:4:3}
          local date_str=$(date --date="jan 1 + $doy days - 1 days" +"%b/%Y%m%d" | tr '[:upper:]' '[:lower:]')

          local prefix="op/$sat_id/${lvl,,}/${instr}/$year/$date_str"

          if [[ -z ${seen_prefixes[$prefix]} ]]; then
              local m_file="$manifest_dir/${sat_id}_${instr}_${lvl}_${year}${doy}.txt"
              seen_prefixes[$prefix]="$m_file"

              (
                debug "Scanning On-Prem: s3://$PREM/$prefix"
                timeout 120s aws s3 ls "s3://$PREM/$prefix" --recursive --profile geocloud | awk '{print $NF}' > "$m_file"

                if [[ ! -s "$m_file" ]]; then
                    debug "Prefix empty, attempting broader search for $instr"
                    local broad_prefix="op/$sat_id/${lvl,,}/"
                    timeout 120s aws s3 ls "s3://$PREM/$broad_prefix" --recursive --profile geocloud | grep "$instr" | grep "$year" | grep "$date_str" | awk '{print $NF}' > "$m_file"
                fi
              ) &

              ((thread_count++))
              if [[ $thread_count -ge 5 ]]; then wait -n; ((thread_count--)); fi
          fi
      fi
  done
  wait
  thread_count=0

  # --- STEP 2: PARALLEL RETRIEVAL (Standard Products) ---
  info "Retrieving matching standard products..."
  for gccs_file in $(find "$gccs_path" -type f ! -name "*I_*"); do
    (
      local filename=$(basename "$gccs_file")
      local search_pattern=$(echo "$filename" | grep -oP "^.*?_s\d{14}")
      local dest=$(dirname "${gccs_file/gccs/prem}")
      mkdir -p "$dest"

      [[ $filename =~ $GOES_FILE_PATTERN ]]
      local sat_id="GOES-${BASH_REMATCH[6]}"
      local instr="${BASH_REMATCH[1]}"
      local lvl="${BASH_REMATCH[2]}"
      local s_time="${BASH_REMATCH[7]}"
      local m_file="$manifest_dir/${sat_id}_${instr}_${lvl}_${s_time:0:4}${s_time:4:3}.txt"

      local s3file=$(grep "$search_pattern" "$m_file" 2>/dev/null | head -n 1)

      if [[ -n $s3file ]]; then
        debug "Retrieving $(basename "$s3file") from GPAS"
        aws s3 cp --profile geocloud "s3://$PREM/$s3file" "$dest/" $S3_PROGRESS
      else
        local bucket=$([[ "${BASH_REMATCH[6]}" == "19" ]] && echo "noaa-goes19" || echo "noaa-goes18")
        local nodd_prefix="${instr}-${lvl}-${BASH_REMATCH[3]^^}/${s_time:0:4}/${s_time:4:3}/${s_time:7:2}"
        local nodd_file=$(aws s3api list-objects-v2 --no-sign-request --bucket "$bucket" --prefix "$nodd_prefix" \
                          --query "Contents[?contains(Key, '$search_pattern')].Key" --output text | awk '{print $1}')

        [[ -n $nodd_file ]] && aws s3 cp --no-sign-request "s3://$bucket/$nodd_file" "$dest/" $S3_PROGRESS
      fi
    ) &

    ((thread_count++))
    if [[ $thread_count -ge $MAXTHREADS ]]; then wait -n; ((thread_count--)); fi
  done
  wait

  # --- STEP 3: INTERMEDIATE PRODUCT (IP) TARBALLS ---
  info "Processing Intermediate Product tarballs..."
  local -A tarballs_downloaded
  for gccs_file in $(find "$gccs_path" -type f -name "*I_*"); do
    local filename=$(basename "$gccs_file")
    [[ $filename =~ _s([0-9]{14}) ]] && local s_time=${BASH_REMATCH[1]}
    [[ $filename =~ _G([0-9]{2}) ]] && local sat_id="GOES-${BASH_REMATCH[1]}"
    [[ $filename =~ I_([A-Z0-9]+)- ]] && local instr="${BASH_REMATCH[1]}"

    local tar_name="${sat_id}_${instr}_L2_IntermediateProducts_day${s_time:4:3}_hour${s_time:7:2}.tar"

    if [[ -z ${tarballs_downloaded[$tar_name]} ]]; then
      local egress_prefix="s3://geoegress/egresout/DOE1L2IP/$sat_id"
      aws s3 cp --profile geocloud "$egress_prefix/$tar_name" "$prem_path/" $S3_PROGRESS &
      tarballs_downloaded[$tar_name]=1
    fi
  done
  wait

  # --- STEP 4: TARGETED TAR EXTRACTION ---
  local ip_temp="$TMP_DIR/ip_extract"
  mkdir -p "$ip_temp"
  for gccs_file in $(find "$gccs_path" -type f -name "*I_*"); do
    local dest=$(dirname "${gccs_file/gccs/prem}")
    local filename=$(basename "$gccs_file")
    local search_pattern=$(echo "$filename" | grep -oP "^.*?_s\d{14}")

    for tarball in $(find "$prem_path" -name "*.tar"); do
       local match_in_tar=$(tar -tf "$tarball" | grep "$search_pattern" | head -n 1)
       if [[ -n $match_in_tar ]]; then
          tar -xf "$tarball" -C "$ip_temp" "$match_in_tar" 2>/dev/null
          mkdir -p $dest
          mv "$ip_temp/$match_in_tar" "$dest/"
          break
       fi
    done
  done


  # Final Cleanup
  rm -rf "$TMP_DIR"
  info "... On-Prem matching complete"
}

function get_on_prem_products() {
  info "Starting On-Prem Retrieval"
  local timestamp=$1
  local -n products_ref=$2
  local thread_count=0

  # 1. Group products by Instrument/Level
  declare -A groups
  for prod in "${products_ref[@]}"; do
    local inst=$(getInstrument "$prod")
    local lvl=$(getLevel "$prod")
    local key="${inst}|${lvl}"
    groups["$key"]+="${prod} "
  done

  # 2. Process groups
  for key in "${!groups[@]}"; do
    local inst=${key%|*}
    local lvl=${key#*|}
    local -a current_prods=(${groups[$key]})

    for sat_num in 18 19; do
      local sat_id="GOES-${sat_num}"
      local year=${timestamp:0:4}
      local doy=${timestamp:4:3}
      # Structure: YYYY/mon/YYYYMMDD (e.g. 2026/mar/2026058)
      local date_str=$(date --date="jan 1 + $doy days - 1 days" +"%b/%Y%m%d" | tr '[:upper:]' '[:lower:]')

      # 3. Parallelize at the Product Level
      for p in "${current_prods[@]}"; do
        local current_scenes=""
        [[ "$inst" == "ABI" ]] && current_scenes=${SCENE_LIST:-}

        for scene in ${current_scenes:-""}; do
          (
            local full_prod="${p}${scene}"
            local folder_name="${full_prod,,}"

            # --- RESTORED PATH LOGIC ---
            # Explicitly append year and doy to the destination path
            local sat_dest="${prem_path}/${inst}/${folder_name}/${year}/${doy}"
            mkdir -p "$sat_dest"

            # GPAS Search Pattern
            local gpas_prefix="op/${sat_id}/${lvl,,}/${inst/SEIS/SEISS}/${year}/${date_str}/"
            local include_pattern="*${full_prod^^}*G${sat_num}_s${timestamp}*"

            verbose "Parallel Sync [Started]: GOES-${sat_num} ${folder_name} -> ${year}/${doy}"



            # GPAS Primary Sync
            # We sync "to" the specific year/doy folder to ensure files land there exactly
            aws s3 sync "s3://${PREM}/${gpas_prefix}" "$sat_dest" \
              --profile geocloud \
              --exclude "*" \
              --include "$include_pattern" \
              --no-progress $S3_PROGRESS

            # NODD Fallback
            if [[ -z "$(find "$sat_dest" -name "$include_pattern" -type f 2>/dev/null)" ]] || $force_nodd; then
              debug "GPAS match failed for ${folder_name}, checking NODD..."
              local nodd_bucket="noaa-goes${sat_num}"
              # NODD structure: <Product>-<Level>-<ShortName>/<Year>/<DOY>/<Hour>/
              local nodd_prefix="${inst}-${lvl}-${p^^}/${year}/${doy}/"

              aws s3 sync "s3://${nodd_bucket}/${nodd_prefix}" "$sat_dest" \
                --no-sign-request \
                --exclude "*" \
                --include "$include_pattern" \
                --no-progress $S3_PROGRESS
            fi
          ) &

          # --- THREAD MANAGEMENT ---
          ((thread_count++))
          if [[ $thread_count -ge $MAXTHREADS ]]; then
            wait -n
            ((thread_count--))
          fi
        done
      done
    done
  done

  wait
  find "$prem_path" -type d -empty -delete
  info "On-Prem retrieval complete with full directory hierarchy."
}

####### ----- ANALYZE ---- #######
function run_metadata_analysis() {
  info "Performing Metadata Analysis"

  local analyzer=$pave_bin/metadata_scripts/analyze_metadata.sh
  [[ -x $analyzer ]] || { ERROR "$analyzer not found or executable" >&2; }

  mkdir -p $metadata_path

  debug prem_path=$prem_path
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
  $TOOL_DEBUG && local debug_flag="--verbose"

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
    debug $glance collocate $debug_flag -c $glance_cfg/dmw_collocate.py -p $tmp_dir $gccs_file $prem_file
    local traceback=$( { $glance collocate $debug_flag -c $glance_cfg/dmw_collocate.py -p $tmp_dir $gccs_file $prem_file; } 3>&1 1>&2 2>&3 3>&- )
    if (( $? == 1 )); then WARN "Glance reported error:\nCaptured Traceback:\n$traceback"; continue; fi

    ## mv to collocated folders
    verbose "moving collocated files"
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
    debug $glance report $debug_flag -c $glance_cfg/dmw_report.py -p $glance_report --stripfromname e.* $collocated ${collocated/prem/gccs}
    traceback=$( { $glance report $debug_flag -c $glance_cfg/dmw_report.py -p $glance_report --stripfromname e.* $collocated ${collocated/prem/gccs}; } 3>&1 1>&2 2>&3 3>&- )
    if (( $? == 1 )); then WARN "Glance reported error:\nCaptured Traceback:\n$traceback"; fi
  done

  rm -rf $tmp_dir
}

function run_glance_analysis() {
  info "Generating Glance Reports"
  local thread_count=0

  for product in $(find $gccs_path -type d -links 2 ! -empty); do
    product=$(dirname $(dirname $product)) #remove yyyy/ddd

    glance_report=${product/gccs/glance_reports}; debug glance_report=$glance_report
    # skip if already run
#    [[ -d "$glance_report" && "$(ls -A "$glance_report")" ]] && continue

    (
    # execute specialized glance procedures for dmw
    if [[ "$(basename ${product,,})" =~ "dmw" ]]; then run_glance_collocation_analysis $product; continue; fi

    mkdir -p $glance_report
    $TOOL_DEBUG && local debug_flag="--verbose"
    debug $glance report $debug_flag --nolonlat $glance_flags -p $glance_report ${product/gccs/prem} $product --stripfromname e.*
    $glance report $flag --nolonlat $glance_flags -p $glance_report ${product/gccs/prem} $product --stripfromname e.*
    ) &

    ((thread_count++))
    if [[ $thread_count -ge $MAXTHREADS ]]; then
      wait -n
      ((thread_count--))
    fi
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
  PREFIX=test
  TAG=internal
  date_hour=2026059100
  product_names=("acm" "ach" "dmw" "dmwvpqi" "mpsh" "geof") # TODO: add CMI
  S3_PROGRESS=""
}

####### ----- MAIN ----- #######
PROGRAM=$(basename "$0")

####### ----- CLI  ----- #######
#default values
run_gccs=true
run_prem=true
run_glance=true
run_metadata=true
force_nodd=false
glance_flags=""
config=""

SHORT_OPTS="c:hvdD"
LONG_OPTS="collect_only,skip_gccs,skip_prem,force_nodd,\
           report_only,skip_glance,skip_metadata,glance_flags:,\
           scene_list:,prefix:,tag:,config:\
           help,verbose,debug,test,tool_debug"
ARGS=$(getopt -o "${SHORT_OPTS}" --long "${LONG_OPTS}" -- "$@")
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

    -c | --config )        config=$2; shift 2 ;;

    -h | --help    ) print_help $prog ;;
    -v | --verbose ) VERBOSE=true; shift ;;
    -d | --debug   ) DEBUG=true; VERBOSE=true; shift ;;

    --test    ) TEST=true; DEBUG=true; VERBOSE=true; shift ;;

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

info "Starting Product Validation"
if [[ -n $config && -f $config ]]; then
  debug "sourcing config file: $config"
  source $config
elif [ -f  $SCRIPT_DIR/config.sh ]; then
  debug "sourcing config file: $config"
  source $SCRIPT_DIR/config.sh
else
  # Paths to PAVE scripts/tools
  pave_bin=$SCRIPT_DIR
  glance_cfg=$pave_bin/glance_summarize/configuration
  analysis_path=$PWD/YYYYDDDhh
  MAXTHREADS=4
fi

####### ----- TEST ----- #######
if $TEST; then set_test; fi
####### ----- TEST ----- #######

if [[ -z $date_hour ]]; then info "date_hour required\n"; print_help $PROGRAM; exit 1; fi
if [ ${#product_names[@]} -eq 0 ]; then info "include prod names to best knowledge\n"; print_help; exit 1; fi

IFS=',' read -ra dates <<< "$date_hour"; date_hour="${dates[0]}"
set_paths $date_hour

for timestamp in ${dates[@]}; do
  if $run_gccs; then get_gccs_products $timestamp "product_names"; fi
done
if $run_prem; then get_on_prem_products $date_hour "product_names"; fi

if $run_metadata; then run_metadata_analysis; fi
if $run_glance; then run_glance_analysis; fi

info "... processing complete"
# happy dance you are at the end :-P
