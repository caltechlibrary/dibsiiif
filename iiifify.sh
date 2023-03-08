#!/bin/bash

# Process the oldest -initiated file in the STATUS_FILES_DIR directory
# Additional files will be processed next time cron calls this script

# CONFIGURATION:
# - set values in `iiifify.ini`
# - set up a cron job to run this script every minute

# USAGE:
# /bin/bash /path/to/this/script.sh

status_files_dir=$(source "$(dirname "$0")"/iiifify.ini && echo "$STATUS_FILES_DIR")
file="$(ls -tr $status_files_dir/*-initiated 2> /dev/null | head -1)"
if [ -n "$file" ]; then
    barcode=$(basename "$file" | cut -d '-' -f 1)
    python=$(source "$(dirname "$0")"/iiifify.ini && echo "$PYTHON")
    $python "$(dirname "$0")"/dibsiiif.py "$barcode"
fi
