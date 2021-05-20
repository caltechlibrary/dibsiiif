# dibsiiif

## TL;DR

Pass a barcode to the script and convert all TIFFs in the barcode-named
directory into JPEG-compressed, IIIF-compatible Pyramid TIFFs for use with the
[Caltech Library DIBS](https://caltechlibrary.github.io/dibs/).

## Components

### `dibsiiif.py`

The main script. Includes status file manipulation for DIBS, image conversion,
metadata retrieval, IIIF manifest generation, and file upload to S3.

### `iiifify.sh`

A simple Bash script to check for initialized status files and begin processing
any TIFFs in folders named with the barcode embedded in the status file name.

### `notify.sh`

A small notification utility to send messages to a Slack channel or email
addresses.

#### Slack Requirements

1. Install and follow configuration instructions for [slack-cli](https://github.com/rockymadden/slack-cli).
2. Be sure to have a legacy API token in a `~/.slack` file.
3. Enable Slack and supply a channel in the `notify.ini` file.
