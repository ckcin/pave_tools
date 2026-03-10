#! /bin/env bash
#######################################################################################################################
# FILE:           analyze_metadata.sh
# DESCRIPTION:    script to compare metadata between two product folders for GCCS
# LIMITATIONS:    None
# AUTHOR:         Nick Carrasco <hector.n.carrasco@nasa.gov
# SOFTWARE HISTORY:
#   Jan 14, 2026 nickc : initial implementation consisting of initial cli a basic diff dump
#   Jan 16, 2026 nickc : initial implementation complete with initial set of report filters
#   Jan 26, 2026 nickc : updated to use NCO's ncks command to get sorted metadata
#   Jan 30, 2026 nickc : updated comparison of variables, now pull variable by variable to enable attribute sorting
#######################################################################################################################
# TODO:
# - [YYYY-MM-DD] To-do template
# - [YYYY-MM-DD] update writer to wrap fields that include commas in quotes
# - [2026-01-16] add start time value to rows and/or gccs filename
#######################################################################################################################

## ncdump sections headers
dimensions="dimensions:"
variables="variables:"
attributes="attributes:" 

####### ----- HELP ----- #######
function print_help {

  echo -e "Usage:"
  echo -e "$PROGRAM [options] <prem product fld> <gccs product fld>"
  echo
  echo -e "Arguments:"
  echo -e "\t <prem_product_fld> \t folder containing data produced on-prem"
  echo -e "\t <gccs_product_fld> \t folder containing data produced by gccs"
  echo -e "\t <output_file> \t\t filename for collected results"
  echo
  echo -e "Options:"
  echo -e "\t -O|--overwrite \t overwrite existing output file if exists"
  echo 
  echo -e "\t -v|--verbose \t verbose messaging"
  echo -e "\t -d|--debug \t debug messaging"
  echo -e "\t -S|--silent \t no messaging"
  echo -e "\t -h|--help \t prints this message"

  exit 0
}

####### ----- UTILS ---- #######
VERBOSE=false; DEBUG=false; SILENT=false; TEST=false

function info() {
  if [[ ! $SILENT ]]; then
    echo -e "${FUNCNAME[1]}: $@"
  fi
}

function verbose() {
  if "$VERBOSE"; then
    echo -e "${FUNCNAME[1]}: $@"
  fi
}

function debug() {
  if "$DEBUG"; then
    echo -e "${FUNCNAME[1]}: $@"
  fi
}

####### ----- comparison utils ----- #######
function collect_group() {
#  ncdump -h $3 | sed -n "/$1/,/$2/{/$1/!{/$2/!p}}" | sort
#  ncks -mM $3 | sed -n "/$1/,/$2/{/$1/!{/$2/!p}}"
  ncdump -h "$3" | sed -n "/$1/,/$2/{/$1/!{/$2/!p}}" | sed 's/^[[:space:]]*//' | grep -v '^$' | sort
}

function compare_group() {
  local group_name=$1
  local -n prem_group=$2
  local -n gccs_group=$3
  local -n group_results=$4

  local differ=$(diff -q -w <(echo -e "$prem_group" | sort -t'=' -k1) <(echo -e "$gccs_group" | sort -t'=' -k1))
  if [[ -n $differ ]]; then
    debug members of $group_name differ

    local length=$(printf "%s\n" "${prem_group[@]} ${gccs_group[@]}" | wc -L); debug length=$length
    local width=$(($length * 2 + 3)); debug width=$width

    #group_results=$(
    readarray -t group_results < <(
    diff --side-by-side --suppress-common-lines --expand-tabs --width=$width <(echo "$prem_group" | sed 's/.*/"&"/') <(echo "$gccs_group" | sed 's/.*/"&"/') |
      awk -v len="$length" -v grp="$group_name" '{
        prem = substr($0, 1, len); #print "[prem]"prem;
        mark = substr($0, len+1, 3); #print "[mark]"mark
        gccs = substr($0, len+4); #print "[gccs]"gccs

        gsub(/^[[:space:]]+|[[:space:]]+$/, "", prem);
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", gccs);
        gsub(/[[:space:]]+/, "", mark);

        if (mark == "|") {
          print "ERROR,"grp",MISMATCH,"prem","gccs",";
        } else if (mark == "<") {
          print "ERROR,"grp",PREM ONLY,"prem",,";
        } else if (mark == ">") {
          print "ERROR,"grp",GCCS ONLY,,"gccs",";
        }
      }')

    debug "results=\n${group_results[@]}"
  else
    debug "all entries matching for $group_name"
  fi
}

function filter_report() {
  local filename=$1; local -n reports=$2

  local warning_strings=(":data_name" ":dataset_name")
  local ignore_strings=(":date_created" ":id" ":production_site")
  local known_strings=("algorithm_dynamic_input_data_container:")

  local warn_pattern=$(IFS="|"; echo "${warning_strings[*]}"); debug warn_pattern=$warn_pattern
  local ignr_pattern=$(IFS="|"; echo "${ignore_strings[*]}"); debug ignr_pattern=$ignr_pattern
  local knwn_pattern=$(IFS="|"; echo "${known_strings[*]}"); debug knwn_pattern=$knwn_pattern

  for report in "${reports[@]}"; do
    debug "filering: $report"
    if   [[ "$report" =~ $warn_pattern ]]; then echo "${report/ERROR/WARNING}" >> $filename;
    elif [[ "$report" =~ $ignr_pattern ]]; then echo "${report/ERROR/IGNORE}" >> $filename;
    elif [[ "$report" =~ $knwn_pattern ]]; then echo "${report/ERROR/KNOWN}" >> $filename;
    else echo "${report}" >> $filename;
    fi
  done
}

function compare_dimensions() {
  local prod=$1; local prem=$2; local gccs=$3; local outfile=$4

  verbose "comparing dimenions for: $prem vs $gccs"

  local prem_dims=$(collect_group $dimensions $variables $prem); # debug prem: "\n$prem_dims"
  local gccs_dims=$(collect_group $dimensions $variables $gccs); # debug gccs: "\n$gccs_dims"

  local -a results
  compare_group "$prod,${dimensions%?}" "prem_dims" "gccs_dims" "results"
  debug "${results[@]}"
  filter_report $outfile "results"
}

function get_variable_list() {
  ncks -qm $1 | sed -n '/variables:/,$p' | sed 's/([^(].*$//' | sed 's/:[^:].*$//' | awk '{print $2}' | sed -e '/^$/d' | grep -v "//"
}

function collect_variable() {
#  ncks -Cmv $1 $2 | grep $1: | sort
  ncdump -h "$2" | grep "^[[:space:]]*$1:" | sed 's/^[[:space:]]*//' | sort
}

function compare_variables() {
  local prod=$1; local prem=$2; local gccs=$3; local outfile=$4

  verbose "comparing variables for: $prem vs $gccs"

  local prem_vars=$(get_variable_list $prem); #debug prem_vars: "\n${prem_vars[@]}"
  local gccs_vars=$(get_variable_list $prem); #debug prem_vars: "\n${prem_vars[@]}"

  for var in ${prem_vars[@]}; do
    debug checking $var for metadata differences

    local prem_var=$(collect_variable $var $prem); debug prem_var=${prem_var[@]}
    local gccs_var=$(collect_variable $var $gccs); debug gccs_var=${gccs_var[@]}

    local -a results=()
    compare_group "$prod,$var" "prem_var" "gccs_var" "results"; debug "${results[@]}"
    filter_report $outfile "results"
  done
}


function compare_attributes() {
  local prod=$1; local prem=$2; local gccs=$3; local outfile=$4

  verbose "comparing global attributes for: $prem vs $gccs"

  local prem_attrs=$(collect_group $attributes "bogus" $prem)
  local gccs_attrs=$(collect_group $attributes "bogus" $gccs)

  local -a results
  compare_group "$prod,${attributes%?}" "prem_attrs" "gccs_attrs" "results"
  debug "${results[@]}"
  filter_report $outfile "results"
}

####### ----- collect and analyze ----- #######
function collect_metadata() {
  info "collecting metdata"

  local prem_fld=$1; debug prem_fld=$prem_fld
  local gccs_fld=$2; debug gccs_fld=$gccs_fld
  local out_file=$3; debug out_file=$out_file

  # first find list of uniq products per type and satellite based on prem versions
  for product in $(find $prem_fld -type d -links 2 ! -empty); do
    verbose procesing folder: $product

    for satellite in "G19" "G18"; do
      # locate first on-prem product file for current product folder
      local prem_prods=($(shopt -s nullglob dotglob; echo $product/*$satellite*.nc))
      if [[ ${#prem_prods[@]} -gt 0 ]]; then
        local prem_prod=${prem_prods[0]}
      else 
        echo "WARNING: no files found from on-prem for $product"
        continue
      fi

      # generate pattern removing _e and _c tokens, and get product name
      local pattern=$(basename $prem_prod); pattern="${pattern%_*}"; pattern="${pattern%_*}"; debug "pattern=$pattern"
      local product_name="${pattern%_*}"; debug "product_name=$product_name"
      local product_time="${pattern##*_}"; debug "product_time=$product_time"

      # get gccs product file using pattern from on-prem
      local gccs_prod=$(find $gccs_fld -name $pattern*); debug gccs_prod=$gccs_prod

      # report if no file found from gccs (note this should never exist since on-prem collection based on gccs)
      if [[ ! $gccs_prod ]]; then echo "WARNING: no matching gccs file for: $prem_prod"; continue; fi

      # compare metadata sections
      compare_dimensions "$product_name,$product_time" $prem_prod $gccs_prod $out_file
      compare_variables "$product_name,$product_time" $prem_prod $gccs_prod $out_file
      compare_attributes "$product_name,$product_time" $prem_prod $gccs_prod $out_file

    done
  done
}

####### ----- MAIN ----- #######
PROGRAM=$(basename "$0")
OVERWRITE=false

ARGS=$(getopt -o OhvdSt --long overwrite,help,verbose,debug,silent,test -- "$@")
eval set -- ${ARGS}
while :
do
  case $1 in
    -O | --overwrite ) OVERWRITE=true; shift ;;

    -h | --help   ) print_help $prog ;;
    -v | --verbose ) VERBOSE=true; shift ;;
    -d | --debug  ) DEBUG=true; VERBOSE=true; shift ;;
    -S | --silent ) SILENT=true; VERBOSE=false; DEBUG=false; shift ;;
    -t | --test   ) TEST=true; DEBUG=true; VERBOSE=true; shift ;;

    -- ) shift; break ;;
    *  ) echo "invalid options: -$1"; print_help ;;
  esac
done
# store positional arguments
debug "arg count:$#"
if [ $# -lt 2 ]; then print_help; fi
prem_folder=${1}; shift; debug prem_folder=$prem_folder
gccs_folder=${1}; shift; debug gccs_folder=$gccs_folder
if [ $# -ne 1 ]; then output_file="/dev/tty"; else output_file=${1}; shift; fi; debug output_file=$output_file

if [[ -e "$output_file" && "$OVERWRITE" == false && "$output_file" != *"tty"* ]]; then
  read -p "Output file '$output_file' exists. Overwrite? (y/N): " confirm
  if [[ "$confirm" != [yY] ]]; then 
    echo "Aborting"
    exit 1
  fi
fi
echo "Level,Product,StartTime,Group,Difference,From_On_Prem,From_GCCS,Notes"> $output_file

collect_metadata $prem_folder $gccs_folder $output_file

# happy dance you are at the end :-P
