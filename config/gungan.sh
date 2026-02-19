# Paths for use on Gungan2

# Paths to PAVE scripts/tools
export pave_bin=$SCRIPT_DIR

# base path for analysis work
export analysis_path=$PWD/YYYYDDDhh

# threads
export MAXTHREADS=8

# glance paths
export glance=/data/hcarrasc/miniforge3/envs/glance_user/bin/glance
export glance_cfg=$pave_bin/glance_summarize/configuration
export glance_summarizer=$pave_bin/glance_summarize/glance_stats.py

# metadata analysis tool path
export metadata_analyzer=$pave_bin/metadata_scripts/analyze_metadata.sh
