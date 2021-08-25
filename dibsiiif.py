# EXPECTATIONS
# settings.ini file with appropriate values (see example-settings.ini)

# NOTE: this script should be initiated by the `iiifify.sh` script that runs on cron

import json
import os
import requests
import shutil
import subprocess
import traceback
from pathlib import Path

import boto3
import botocore
import plac
from decouple import config


def main(barcode: "the barcode of an item to be processed"):
    """Process an item for Caltech Library DIBS."""

    try:
        (
            CANVAS_BASE_URL,
            IIIF_BASE_URL,
            MANIFEST_BASE_URL,
            MANIFEST_FILES_DIR,
            PROCESSED_IIIF_DIR,
            PROCESSED_SCANS_DIR,
            S3_BUCKET,
            STATUS_FILES_DIR,
            UNPROCESSED_SCANS_DIR,
            VIPS_CMD,
        ) = validate_settings()
    except Exception as e:
        # NOTE we cannot guarantee that `STATUS_FILES_DIR` is set
        # - it must exist if script is started from `initiated.sh`
        # TODO figure out how to not send a message every minute
        message = "❌ there was a problem with the settings for the `dibsiiif.py` script"
        print(message)
        # TODO move bash and notify.sh locations into settings.ini
        subprocess.run(["/bin/bash", "./notify.sh", str(e), message])
        raise

    # remove `STATUS_FILES_DIR/{barcode}-initiated` file
    # NOTE in order to allow the script to be run indpendently of a
    # wrapper, we should not insist upon the initiated file existing
    try:
        Path(STATUS_FILES_DIR).joinpath(f"{barcode}-initiated").unlink(missing_ok=True)
    except Exception as e:
        with open(Path(STATUS_FILES_DIR).joinpath(f"{barcode}-problem"), "w") as f:
            traceback.print_exc(file=f)
        raise

    # create `STATUS_FILES_DIR/{barcode}-processing` file
    try:
        Path(STATUS_FILES_DIR).joinpath(f"{barcode}-processing").touch(exist_ok=False)
    except Exception as e:
        with open(Path(STATUS_FILES_DIR).joinpath(f"{barcode}-problem"), "w") as f:
            traceback.print_exc(file=f)
        raise

    # validate the `UNPROCESSED_SCANS_DIR/{barcode}` directory
    try:
        barcode_dir = Path(UNPROCESSED_SCANS_DIR).joinpath(barcode).resolve(strict=True)
        if not len(os.listdir(barcode_dir)):
            raise ValueError(f"item directory is empty: {barcode_dir}")
    except Exception as e:
        with open(Path(STATUS_FILES_DIR).joinpath(f"{barcode}-problem"), "w") as f:
            traceback.print_exc(file=f)
        raise

    # set up lists of TIFF paths and sequence numbers
    tiff_paths = []
    sequence = []
    for i in os.scandir(barcode_dir):
        if i.is_file() and i.name.endswith((".tif", ".tiff")):
            # for the case of `35047000000000_001.tif`
            if (
                len(i.name.split(".")[0].split("_")) == 2
                and i.name.split(".")[0].split("_")[0] == barcode
                and i.name.split(".")[0].split("_")[-1].isnumeric()
            ):
                tiff_paths.append(i.path)
                sequence.append(int(i.name.split(".")[0].split("_")[-1]))
            # for the case of `35047000000000_Page_001.tif`
            elif (
                len(i.name.split(".")[0].split("_")) == 3
                and i.name.split(".")[0].split("_")[0] == barcode
                and i.name.split(".")[0].split("_")[-1].isnumeric()
                and i.name.split(".")[0].split("_")[-2] == "Page"
            ):
                tiff_paths.append(i.path)
                sequence.append(int(i.name.split(".")[0].split("_")[-1]))
            else:
                print(
                    f" ⚠️\t unexpected file name format encountered: {barcode}/{i.name}"
                )

    # verify that TIFFs exist in the `{barcode_dir}`
    try:
        if not len(tiff_paths):
            raise ValueError(f"item directory contains no TIFFs: {barcode_dir}")
    except Exception as e:
        with open(Path(STATUS_FILES_DIR).joinpath(f"{barcode}-problem"), "w") as f:
            traceback.print_exc(file=f)
        raise

    # raise exception if the sequence is missing any numbers
    try:
        if missing_numbers(sequence):
            raise ValueError(
                f"missing sequence numbers for {barcode}: {missing_numbers(sequence)}"
            )
    except Exception as e:
        with open(Path(STATUS_FILES_DIR).joinpath(f"{barcode}-problem"), "w") as f:
            traceback.print_exc(file=f)
        raise

    # set up manifest
    manifest = {
        "@context": "http://iiif.io/api/presentation/2/context.json",
        "@type": "sc:Manifest",
        "@id": f"{MANIFEST_BASE_URL}/{barcode}",
        "attribution": "Caltech Library",
        "logo": f"{IIIF_BASE_URL}/logo/full/max/0/default.png",
        "sequences": [{"@type": "sc:Sequence", "canvases": []}],
    }

    # retrieve item metadata
    # NOTE barcode validation happens in the DIBS interface
    # starting from a barcode, we must make 3 requests to get the instance record
    try:
        FOLIO_API_URL = config("FOLIO_API_URL").rstrip("/")
        okapi_headers = {'X-Okapi-Tenant':config("FOLIO_API_TENANT"),'x-okapi-token':config("FOLIO_API_TOKEN")}

        items_query = f'{FOLIO_API_URL}/inventory/items?query=barcode%3D%3D{barcode}'
        items_response = requests.get(items_query, headers=okapi_headers).json()

        if items_response.get('items'):
            items = items_response['items']
        if len(items) > 1:
            raise ValueError("❌ more than one item found for barcode")
        if items[0].get('holdingsRecordId'):
            holdingsRecordId = items[0]['holdingsRecordId']
        else:
            raise ValueError("❌ no holdingsRecordId found")

        holdings_query = f'{FOLIO_API_URL}/holdings-storage/holdings/{holdingsRecordId}'
        holdings_response = requests.get(holdings_query, headers=okapi_headers).json()

        if holdings_response.get('instanceId'):
            instanceId = holdings_response['instanceId']
        else:
            raise ValueError("❌ no instanceId found")

        # NOTE this endpoint returns a record that shows MARC fields
        instance_query = f'{FOLIO_API_URL}/records-editor/records?instanceId={instanceId}'
        instance_response = requests.get(instance_query, headers=okapi_headers).json()

        if instance_response.get('fields'):
            fields = instance_response['fields']
        else:
            raise ValueError("❌ no fields found")
        title = ""
        author = ""
        edition = ""
        year = ""
        for field in fields:
            if field['tag'] == '008':
                if field['content'].get('Date1'):
                    year = field['content']['Date1']
            if field['tag'] == '245':
                # TODO account for many more subfields
                # https://www.loc.gov/marc/bibliographic/bd245.html
                if "$a " not in field['content']:
                    raise ValueError("❌ no title found")
                if "$c " in field['content']:
                    subfield_c_position = field['content'].find('$c ')
                    author = field['content'][subfield_c_position + 3:].strip(' /:;,.')
                    title = field['content'][3:subfield_c_position].strip(' /:;,.')
                else:
                    title = field['content'][3:].strip(' /:;,.')
                if "$b " in field['content']:
                    subfield_b_position = field['content'].find('$b ')
                    title = title[:subfield_b_position - 3] + title[subfield_b_position:]
            if field['tag'] == '250':
                edition = field['content'][3:].strip(' /:;,.')
    except Exception as e:
        with open(Path(STATUS_FILES_DIR).joinpath(f"{barcode}-problem"), "w") as f:
            traceback.print_exc(file=f)
        raise

    # add metadata to manifest
    manifest["label"] = title
    manifest["metadata"] = []
    manifest["metadata"].append({"label": "Title", "value": f"{title}"})
    if author:
        manifest["metadata"].append({"label": "Author", "value": author})
    if edition:
        manifest["metadata"].append({"label": "Edition", "value": edition})
    manifest["metadata"].append({"label": "Year", "value": year})

    # make IIIF directory if needed
    try:
        os.makedirs(f"{PROCESSED_IIIF_DIR}/{barcode}", exist_ok=True)
    except Exception as e:
        with open(Path(STATUS_FILES_DIR).joinpath(f"{barcode}-problem"), "w") as f:
            traceback.print_exc(file=f)
        raise

    # loop through sorted list of TIFF paths
    tiff_paths.sort()
    for f in tiff_paths:
        f = Path(f)
        # create compressed pyramid TIFF
        if (
            # TODO use subprocess.run()
            os.system(
                f"{VIPS_CMD} tiffsave {f} {PROCESSED_IIIF_DIR}/{barcode}/{f.stem.split('_')[-1]}.tif --tile --pyramid --compression jpeg --tile-width 256 --tile-height 256"
            )
            != 0
        ):
            print("❌ an error occurred running the vips command")
            raise RuntimeError(
                f"{VIPS_CMD} tiffsave {f} {PROCESSED_IIIF_DIR}/{barcode}/{f.stem.split('_')[-1]}.tif --tile --pyramid --compression jpeg --tile-width 256 --tile-height 256"
            )
        # create canvas metadata
        # HACK the binaries for `vips` and `vipsheader` should be in the same place
        width = os.popen(f"{VIPS_CMD}header -f width {f}").read().strip()
        height = os.popen(f"{VIPS_CMD}header -f height {f}").read().strip()

        # upload TIFF to S3
        try:
            boto3.client(
                "s3",
                aws_access_key_id=config("AWS_ACCESS_KEY"),
                aws_secret_access_key=config("AWS_SECRET_KEY"),
            ).put_object(
                Bucket=S3_BUCKET,
                Key=f"{barcode}/{f.stem.split('_')[-1]}.tif",
                Body=open(
                    f"{PROCESSED_IIIF_DIR}/{barcode}/{f.stem.split('_')[-1]}.tif",
                    "rb",
                ),
            )
            print(
                f" ✅\t TIFF sent to S3: {barcode}/{f.stem.split('_')[-1]}.tif",
                flush=True,
            )
        except botocore.exceptions.ClientError as e:
            # https://boto3.amazonaws.com/v1/documentation/api/latest/guide/error-handling.html
            if e.response["Error"]["Code"] == "InternalError":
                print(f"Error Message: {e.response['Error']['Message']}")
                print(f"Request ID: {e.response['ResponseMetadata']['RequestId']}")
                print(f"HTTP Code: {e.response['ResponseMetadata']['HTTPStatusCode']}")
            else:
                raise e

        # set up canvas
        canvas = {
            "@type": "sc:Canvas",
            "@id": f"{CANVAS_BASE_URL}/{barcode}/{f.stem.split('_')[-1]}",
            "label": f"{f.stem.split('_')[-1]}",  # sequence portion of filename
            "width": width,
            "height": height,
            "images": [
                {
                    "@type": "oa:Annotation",
                    "motivation": "sc:painting",
                    "on": f"{CANVAS_BASE_URL}/{barcode}/{f.stem.split('_')[-1]}",  # same as canvas["@id"]
                    "resource": {
                        "@type": "dctypes:Image",
                        "@id": f"{IIIF_BASE_URL}/{barcode}%2F{f.stem.split('_')[-1]}/full/max/0/default.jpg",
                        "service": {
                            "@context": "http://iiif.io/api/image/2/context.json",
                            "@id": f"{IIIF_BASE_URL}/{barcode}%2F{f.stem.split('_')[-1]}",
                            "profile": "http://iiif.io/api/image/2/level2.json",
                        },
                    },
                }
            ],
        }
        # add canvas to sequences
        manifest["sequences"][0]["canvases"].append(canvas)

    # save `{barcode}-manifest.json`
    with open(
        f"{MANIFEST_FILES_DIR}/{barcode}-manifest.json",
        "w",
    ) as f:
        f.write(json.dumps(manifest, indent=4))

    # move `barcode_dir` into `PROCESSED_SCANS_DIR`
    # NOTE shutil.move() in Python < 3.9 needs strings as arguments
    try:
        shutil.move(str(barcode_dir), str(PROCESSED_SCANS_DIR))
    except Exception as e:
        with open(Path(STATUS_FILES_DIR).joinpath(f"{barcode}-problem"), "w") as f:
            traceback.print_exc(file=f)
        raise

    # remove `STATUS_FILES_DIR/{barcode}-processing` file
    try:
        Path(STATUS_FILES_DIR).joinpath(f"{barcode}-processing").unlink()
    except Exception as e:
        with open(Path(STATUS_FILES_DIR).joinpath(f"{barcode}-problem"), "w") as f:
            traceback.print_exc(file=f)
        raise


def directory_setup(directory):
    if not Path(directory).exists():
        Path(directory).mkdir()
    elif Path(directory).is_file():
        print(f" ❌\t A non-directory file exists at: {directory}")
        raise FileExistsError
    return Path(directory)


def missing_numbers(sequence):
    """return a list of missing sequence numbers"""
    sequence.sort()
    return [x for x in range(sequence[0], sequence[-1] + 1) if x not in sequence]


def validate_settings():
    CANVAS_BASE_URL = config("CANVAS_BASE_URL").rstrip("/")
    IIIF_BASE_URL = config("IIIF_BASE_URL").rstrip("/")
    MANIFEST_BASE_URL = config("MANIFEST_BASE_URL").rstrip("/")
    MANIFEST_FILES_DIR = directory_setup(
        os.path.expanduser(config("MANIFEST_FILES_DIR"))
    ).resolve(strict=True)
    PROCESSED_IIIF_DIR = directory_setup(
        os.path.expanduser(config("PROCESSED_IIIF_DIR"))
    ).resolve(strict=True)
    PROCESSED_SCANS_DIR = directory_setup(
        os.path.expanduser(config("PROCESSED_SCANS_DIR"))
    ).resolve(strict=True)
    S3_BUCKET = config("S3_BUCKET")  # TODO validate access to bucket
    STATUS_FILES_DIR = Path(os.path.expanduser(config("STATUS_FILES_DIR"))).resolve(
        strict=True
    )  # NOTE do not create missing `STATUS_FILES_DIR`
    UNPROCESSED_SCANS_DIR = directory_setup(
        os.path.expanduser(config("UNPROCESSED_SCANS_DIR"))
    ).resolve(strict=True)
    VIPS_CMD = Path(os.path.expanduser(config("VIPS_CMD"))).resolve(strict=True)
    return (
        CANVAS_BASE_URL,
        IIIF_BASE_URL,
        MANIFEST_BASE_URL,
        MANIFEST_FILES_DIR,
        PROCESSED_IIIF_DIR,
        PROCESSED_SCANS_DIR,
        S3_BUCKET,
        STATUS_FILES_DIR,
        UNPROCESSED_SCANS_DIR,
        VIPS_CMD,
    )


if __name__ == "__main__":
    plac.call(main)
