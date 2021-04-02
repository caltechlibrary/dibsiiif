#!/bin/bash

# expecting an absolute path as an argument
for FILE in "$1"/*-initiated; do
    barcode=$(basename "$FILE" | cut -d '-' -f 1)
    python=$(which python3)
    $python "$(dirname "$0")"/dibsprep.py "$barcode"
done
