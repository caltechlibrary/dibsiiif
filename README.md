# dibsiiif

## TL;DR

Pass a barcode to the script and convert all TIFFs in the barcode-named
directory into JPEG-compressed, IIIF-compatible Pyramid TIFFs for use with the
[Caltech Library DIBS](https://caltechlibrary.github.io/dibs/).

## Components

### `dibsiiif.py`

The main script. Includes status file manipulation for DIBS, image conversion,
IIIF manifest generation, and file upload to S3.

#### Item metadata from DIBS

When staff start processing an item, DIBS writes a JSON object to the
`{barcode}-initiated` status file with the item's `barcode`, `title`,
`author`, `edition`, and `year`. The script uses these for the manifest, so
the viewer shows the same details as DIBS. If the file is missing, empty, or
not valid JSON, or lacks a title, the script writes a `{barcode}-problem`
file that says so.

#### AWS Requirements

AWS credentials and the S3 bucket's region (`AWS_REGION`) are required and
may be added in the `settings.ini` file.

### `iiifify.sh`

A simple Bash script to check for initialized status files and begin processing
any TIFFs in folders named with the barcode embedded in the status file name.

### `slack_handler.py`

A logging handler to send messages to a Slack channel. Logging configuration is
saved in the `settings.ini` file. See `example-settings.ini` for details.

TODO: develop a general caltechlibrarylogger package to be used instead.

#### Slack Requirements

1. A Slack API OAuth Token.
2. A Slack channel where messages will be sent.
