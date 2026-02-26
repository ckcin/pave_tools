#!/usr/bin/env bash
## simple processing script to download and determine the cadences of GCCS L2 processing

source $(dirname "$(readlink -f "$0")")/utils.sh

# create local aliases
shopt -s expand_aliases
alias s3cp='aws s3 cp '
alias s3ls='aws s3 ls --human-readable '
alias s3sync='aws s3 sync '
###

function display_usage() {
  # this function SHOULD be overwritten the callign script
  program=${BASH_SOURCE[-1]}
  echo -e $program
  echo -e "Usage: $program [options] <arguemnts>..."
  echo -e # blank line
  echo -e "Arguments:"
  echo -e "\t <analysis_date>\t date to collect cadence details for. [Format: YYYYDDD]"
  echo -e "\t <analysis_file>\t file to save analysis too [default <analysis_date>_cadence.csv]"
  echo -e # blank line
  echo -e "Options:"
  verbose_usage

  exit;
} # end display_usage()

SHORT=""
LONG=""

function parse_cli() {
  # this function SHOULD be overwritten the callign script
  parsed=$(getopt -o "${COMMON_SHORT},${SHORT}" --long "${COMMON_LONG},${LONG}" --name "$0" -- "$@") || (display_usage; exit 1)

  eval set -- "$parsed"
  while true; do
    verbose_handle_cli "$@" && { shift; continue; }

    case "$1" in
      --) shift; break ;;
      *) display_usage; error "invalid argument" ;;
    esac
  done
  
  [[ $# -lt 1 ]] && display_usage
  analysis_date=${1:-""}; shift
  [[ $# -ge 1 ]] && { analysis_file=${1:-""}; shift; } || analysis_file=${analysis_date}_cadence.csv
} # end parse_cli

summarize_goes() {
    local bucket="$1"
    local base_prefix="$2" # e.g., GCCS/op/GOES-19/L2/ABI
    local date_suffix="$3" # e.g., 2026/014
    local dest="$4"

    # Query with null-check to prevent NoneType errors on empty results
    local query="Contents[?contains(Key, '$date_suffix')].[LastModified, Key]"

    info "Streaming s3://${bucket}/${base_prefix} for suffix ${date_suffix}..."

    debug "aws s3api list-objects-v2 --bucket \"$bucket\" --prefix \"$base_prefix\" --query \"$query\" --output text"

    # s3api -> sort (chronological) -> awk (process & report on key change)
    stdbuf -oL aws s3api list-objects-v2 --bucket "$bucket" --prefix "$base_prefix" --query "$query" --output text | \
    awk '
    BEGIN { 
        OFS=","; 
        print "Key,Avg_Delta_Sec,First_Filename,First_Start_Time" 
    }
    {
        # 1. Clean ^M (Carriage Returns)
        gsub(/\r/, "", $0); 
        if ($1 == "None" || NF < 2) next

        # 2. Extract Filename and Tokens
        n = split($2, path, "/"); fname = path[n]
        split(fname, dash, "-")
        # KEY: 3rd and 5th tokens separated by UNDERSCORE
        curr_key = dash[3] "_" dash[5]

        split(fname, underscore, "_")
        start_t = underscore[7] # GOES Start Time (sYYYYJJJHHMMSS)

        # 3. Time Math (Seconds of Day from S3 LastModified)
        split($1, dt, "T"); split(dt[2], t, ":"); gsub(/[^0-9.]/, "", t[3])
        curr_secs = (t[1] * 3600) + (t[2] * 60) + t[3]

        # 4. REPORT SUMMARY WHEN NEW KEY APPEARS
        if (prev_key != "" && curr_key != prev_key) {
            # Average is sum of gaps / number of gaps (count - 1)
            avg = (count[prev_key] > 1) ? (sum_delta[prev_key] / (count[prev_key]-1)) : 0
            print prev_key, avg, f_name[prev_key], f_start[prev_key]
            fflush() 
            printf "\rCompleted Group: %-30s", prev_key > "/dev/stderr"
        }

        # 5. Initialize or Accumulate
        if (!(curr_key in f_name)) {
            f_name[curr_key] = fname
            f_start[curr_key] = start_t
            sum_delta[curr_key] = 0
            count[curr_key] = 1
        } else {
            delta = curr_secs - last_secs[curr_key]
            if (delta < 0) delta += 86400 # Handle UTC day rollover
            sum_delta[curr_key] += delta
            count[curr_key]++
        }

        last_secs[curr_key] = curr_secs
        prev_key = curr_key
    }
    END {
        # Report the very last group in the stream
        if (prev_key != "") {
            avg = (count[prev_key] > 1) ? (sum_delta[prev_key] / (count[prev_key]-1)) : 0
            print prev_key, avg, f_name[prev_key], f_start[prev_key]
        }
        print "\nProcess Complete." > "/dev/stderr"
    }' > "$dest"
}

## main
parse_cli $@
debug analysis_date=$analysis_date
debug analysis_file=$analysis_file

TMP_DIR=$(mktemp -d -t $(basename ${BASH_SOURCE[-1]})-XXXXXXXXXX)
debug TMP_DIR=$TMP_DIR

analysis_year=${analysis_date:0:4}; debug analysis_year=$analysis_year
analysis_doy=${analysis_date:4:3}; debug analysis_doy=$analysis_doy

summarize_goes gccs-products GCCS/op/GOES-19/L2/ABI $analysis_year/$analysis_doy $TMP_DIR/formatted_ep_listing.csv &
#retrieve_and_analyze gccs-intermediate-products GCCS/op/GOES-19/L2/ABI $analysis_year/$analysis_doy $TMP_DIR/formatted_ip_listing.csv &
wait

exit
## below this is never reached or called

# gather file listings
info gathering listings from gccs
collect_file_listing $gccs/op/GOES-19/L2/ABI/    formatted_ep_listing.csv &
collect_file_listing $gccs_ip/op/GOES-19/L2/ABI/ formatted_ip_listing.csv &
# wait for listing collection to complete
wait

# combine EP and IP
info combing listings
cat $TMP_DIR/formatted_ep_listing.csv $TMP_DIR/formatted_ip_listing.csv > $TMP_DIR/combined_listing.csv

info analysing listings
analyze_cadence $TMP_DIR/combined_listing.csv $analysis_file

info analysis complete


test_day=$1
test_year=${test_day:0:4}
test_mon=$(date --date="jan 1 + ${test_day:4:3} days - 1 days" +%b | tr '[:upper:]' '[:lower:]')
test_date=$(date --date="jan 1 + ${test_day:4:3} days - 1 days" +%Y%m%d)
test_doy=${test_day:4:3}

#- file listing retrievals
#-- End Prod from GOES
#aws s3 ls $goes/GOES-19/l2/ABI/2026/feb/20260210/ --recursive | while read -r date time size file; do if [[ $file =~ ".nc" ]]; then printf "%s,%s,%s\n" ${file##*/} $date $time; fi; done > formatted_ep_listing.csv
#aws s3 ls $goes/GOES-19/l2/ABI/${test_year}/${test_mon}/${test_date}/ --recursive | while read -r date time size file; do if [[ $file =~ ".nc" ]]; then printf "%s,%s,%s\n" ${file##*/} $date $time; fi; done > formatted_ep_listing.csv
aws s3 ls $gccs/op/GOES-19/L2/ABI/ --recursive | while read -r date time size file; do if [[ $file =~ "${test_year}/${test_doy}" ]]; then printf "%s,%s,%s\n" ${file##*/} $date $time; fi; done > formatted_ep_listing.csv &
#-- IP from GCCS
#aws s3 ls $gccs_ip/op/GOES-19/L2/ABI/ --recursive | while read -r date time size file; do if [[ $file =~ "2026/041" ]]; then printf "%s,%s,%s\n" ${file##*/} $date $time; fi; done > formatted_ip_listing.csv
aws s3 ls $gccs_ip/op/GOES-19/L2/ABI/ --recursive | while read -r date time size file; do if [[ $file =~ "${test_year}/${test_doy}" ]]; then printf "%s,%s,%s\n" ${file##*/} $date $time; fi; done > formatted_ip_listing.csv &

#wait for collections to complete
wait

#- combine and prune details
cat formatted_ep_listing.csv formatted_ip_listing.csv > combined_listing.csv

#- parse
GOES_FILE_PATTERN='^OR_([^-]+)-([^-]+)-([^-]+)(-M[0-9]+)?(C[0-9]+)?_G([0-9]+)_s([0-9]+)_e([0-9]+)_c([0-9]+)\.nc$'; \
  cat combined_listing.csv | while IFS="," read -r file date time; do \
  [[ $file =~ $GOES_FILE_PATTERN ]] || true; printf "%s,%s,%s,%s,%s,%s,%s,%s,%s\n" \
  $file "${BASH_REMATCH[1]}" "${BASH_REMATCH[3]}" "${BASH_REMATCH[5]}" "${BASH_REMATCH[7]}" "${BASH_REMATCH[8]}" "${BASH_REMATCH[9]}" $date $time; \
  done > cadence.csv


#######

cat cadence.csv | sort -t',' -k2,2 -k3,3 -k4,4 -k9,9 | awk -F, 'BEGIN {OFS=","} {
    current_time = $9

    # Check if the grouping keys (B, C, D) match the previous record
    # Note: Column 9 (Time) in the sheet is a decimal fraction of a day.
    if ($2 == prev_B && $3 == prev_C && $4 == prev_D) {
        # Time delta in seconds: (Current Time - Previous Time) * 86400 seconds/day
        delta = (current_time - prev_time) * 86400
    } else {
        # If the key changes, the delta for the first entry of the new group is 0
        delta = 0
    }

    # Print the original line plus the new delta (Column 11)
    print $0, delta

    # Store current values for the next iteration (for the sequential comparison)
    prev_B = $2; prev_C = $3; prev_D = $4; prev_time = current_time }' | awk -F, '
BEGIN {
    OFS=","
    # Print the final header row
    print "Inst", "Product", "Channel", "Filename", "Start Time", "Entry Count", "Avg Time Delta (Seconds)"
}

# Main processing loop
{
    # Create a unique composite key (B, C, D)
    key = $2 "," $3 "," $4

    # Store Filename (A) only for the first entry (min) of the sorted group
    if (!(key in count)) {
        filename[key] = $1
        starttime[key] = $5
    }

    # Aggregate Count (B) and Sum of the Delta (J/11)
    count[key]++
    sum_delta[key] += $11
}END {
    # Iterate over all unique keys that were found
    for (key in count) {
        # Calculate the final average
        avg = sum_delta[key] / count[key]

        # Print the final grouped result
        print key, filename[key], starttime[key], count[key], avg
    }
}' > cadence_report_${test_day}.csv