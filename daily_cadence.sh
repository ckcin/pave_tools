#!/bin/env bash
## simple processing script to download and determine the cadences of GCCS L2 processing

shopt -s expand_aliases

alias s3cp='aws s3 cp '
alias s3ls='aws s3 ls --human-readable '
alias s3sync='aws s3 sync '

test_day=$1
test_year=${test_day:0:4}
test_mon=$(date --date="jan 1 + ${test_day:4:3} days - 1 days" +%b | tr '[:upper:]' '[:lower:]')
test_date=$(date --date="jan 1 + ${test_day:4:3} days - 1 days" +%Y%m%d)
test_doy=${test_day:4:3}

#- file listing retrievals
#-- End Prod from GOES
#aws s3 ls $goes/GOES-19/l2/ABI/2026/feb/20260210/ --recursive | while read -r date time size file; do if [[ $file =~ ".nc" ]]; then printf "%s,%s,%s\n" ${file##*/} $date $time; fi; done > formatted_ep_listing.csv
#aws s3 ls $goes/GOES-19/l2/ABI/${test_year}/${test_mon}/${test_date}/ --recursive | while read -r date time size file; do if [[ $file =~ ".nc" ]]; then printf "%s,%s,%s\n" ${file##*/} $date $time; fi; done > formatted_ep_listing.csv
aws s3 ls $gccs/op/GOES-19/L2/ABI/ --recursive | while read -r date time size file; do if [[ $file =~ "${test_year}/${test_doy}" ]]; then printf "%s,%s,%s\n" ${file##*/} $date $time; fi; done > formatted_ep_listing.csv
#-- IP from GCCS
#aws s3 ls $gccs_ip/op/GOES-19/L2/ABI/ --recursive | while read -r date time size file; do if [[ $file =~ "2026/041" ]]; then printf "%s,%s,%s\n" ${file##*/} $date $time; fi; done > formatted_ip_listing.csv
aws s3 ls $gccs_ip/op/GOES-19/L2/ABI/ --recursive | while read -r date time size file; do if [[ $file =~ "${test_year}/${test_doy}" ]]; then printf "%s,%s,%s\n" ${file##*/} $date $time; fi; done > formatted_ip_listing.csv


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
