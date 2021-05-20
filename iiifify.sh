#!/bin/bash

# When there are files named *-initiated in the provided directory,
# run the processing script for the targeted files.

# CONFIGURATION:
# - set values in `iiifify.ini`
# - set up a cron job to run this script every minute

# USAGE:
# /bin/bash /path/to/this/script.sh

# set the nullglob in case there are no `*-initiated` files
shopt -s nullglob

# expecting an absolute path as an argument
for FILE in "$(source "$(dirname "$0")"/iiifify.ini && echo "$STATUS_FILES_DIR")"/*-initiated; do
    barcode=$(basename "$FILE" | cut -d '-' -f 1)
    python=$(source "$(dirname "$0")"/iiifify.ini && echo "$PYTHON")
    $python "$(dirname "$0")"/dibsiiif.py "$barcode"
done
