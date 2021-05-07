#!/bin/bash

# NOTE: this file is intended to be run via cron every minute

# USAGE:
# /bin/bash /path/to/this/script.sh /path/to/status/files/directory

# set the nullglob in case there are no `*-initiated` files
shopt -s nullglob

# expecting an absolute path as an argument
for FILE in "$1"/*-initiated; do
    barcode=$(basename "$FILE" | cut -d '-' -f 1)
    python=$(which python3)
    $python "$(dirname "$0")"/dibsprep.py "$barcode"
done
