#!/bin/bash

# When there are files named *-initiated in the provided directory,
# run the processing script for the targeted files.

# NOTE: this file is intended to be run via cron every minute

# USAGE:
# /bin/bash /path/to/this/script.sh /path/to/status/files/directory

# set the nullglob in case there are no `*-initiated` files
shopt -s nullglob

# expecting an absolute path as an argument
for FILE in "$1"/*-initiated; do
    barcode=$(basename "$FILE" | cut -d '-' -f 1)
    python=$(source "$(dirname "$0")"/iiifify.ini && echo "$PYTHON")
    $python "$(dirname "$0")"/dibsiiif.py "$barcode"
done
