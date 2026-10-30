# EXPECTATIONS
# settings.ini file with appropriate values (see example-settings.ini)

# NOTE: this script should be initiated by the `iiifify.sh` script that runs on cron

import json
import logging
import logging.config
import logging.handlers
import os
import shutil
import subprocess
import traceback
from pathlib import Path

import boto3
import botocore
from decouple import config

from slack_handler import SlackHandler

# add SlackHandler to the logging.handlers namespace
logging.handlers.SlackHandler = SlackHandler

logging.config.fileConfig(
    # set the logging configuration in the settings.ini file
    Path(__file__).parent.absolute().joinpath("settings.ini"),
    disable_existing_loggers=False,
)
logger = logging.getLogger("dibsiiif")


def main(barcode: "the barcode of an item to be processed"):  # type: ignore
    """Process an item for Caltech Library DIBS."""

    try:
        (
            AWS_REGION,
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
        # - it must exist if script is started from `iiifify.sh`
        message = "❌ there was a problem with the settings for the `dibsiiif.py` script"
        logger.exception(message)
        raise

    # create `STATUS_FILES_DIR/{barcode}-processing` file
    try:
        Path(STATUS_FILES_DIR).joinpath(f"{barcode}-processing").touch(exist_ok=False)
    except Exception as e:
        with open(Path(STATUS_FILES_DIR).joinpath(f"{barcode}-problem"), "w") as f:
            traceback.print_exc(file=f)
        logger.exception("‼️")
        raise

    # read and remove `STATUS_FILES_DIR/{barcode}-initiated` file
    # NOTE DIBS writes the item's metadata into this file; it is removed
    # before the metadata is checked so that `iiifify.sh` does not run the
    # script again for a file with bad metadata
    try:
        initiated_file = Path(STATUS_FILES_DIR).joinpath(f"{barcode}-initiated")
        metadata_text = initiated_file.read_text(encoding="utf-8")
        initiated_file.unlink()
    except Exception as e:
        with open(Path(STATUS_FILES_DIR).joinpath(f"{barcode}-problem"), "w") as f:
            traceback.print_exc(file=f)
        logger.exception("‼️")
        raise

    # check the item metadata from DIBS
    # NOTE using the metadata DIBS has makes the manifest match what DIBS shows
    try:
        title, author, edition, year = parsed_metadata(metadata_text, barcode)
    except Exception as e:
        with open(Path(STATUS_FILES_DIR).joinpath(f"{barcode}-problem"), "w") as f:
            traceback.print_exc(file=f)
        logger.exception("‼️")
        raise

    # validate the `UNPROCESSED_SCANS_DIR/{barcode}` directory
    try:
        barcode_dir = Path(UNPROCESSED_SCANS_DIR).joinpath(barcode).resolve(strict=True)
        if not len(os.listdir(barcode_dir)):
            raise ValueError(f"item directory is empty: {barcode_dir}")
    except Exception as e:
        with open(Path(STATUS_FILES_DIR).joinpath(f"{barcode}-problem"), "w") as f:
            traceback.print_exc(file=f)
        logger.exception("‼️")
        raise

    # set up lists of TIFF paths and sequence numbers
    tiff_paths = []
    sequence = []
    for i in os.scandir(barcode_dir):
        if i.is_file() and i.name.endswith((".tif", ".tiff")):
            # split by underscores and remove empty strings from the list
            parts = list(filter(None, i.name.split(".")[0].split("_")))
            if not parts[0] == barcode:
                print(
                    f" ⚠️\t unexpected file name format encountered: {barcode}/{i.name}"
                )
                continue
            if not parts[-1].isnumeric():
                print(f" ⚠️\t unexpected file name encountered: {barcode}/{i.name}")
                continue
            tiff_paths.append(i.path)
            sequence.append(int(parts[-1]))

    # verify that TIFFs exist in the `{barcode_dir}`
    try:
        if not len(tiff_paths):
            raise ValueError(
                f"item directory contains no TIFFs with expected file name formatting: {barcode_dir}"
            )
    except Exception as e:
        with open(Path(STATUS_FILES_DIR).joinpath(f"{barcode}-problem"), "w") as f:
            traceback.print_exc(file=f)
        logger.exception("‼️")
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
        logger.exception("‼️")
        raise

    # set up manifest
    manifest = {
        "@context": "http://iiif.io/api/presentation/2/context.json",
        "@type": "sc:Manifest",
        "@id": f"{MANIFEST_BASE_URL}/{barcode}",
        "logo": f"{IIIF_BASE_URL}/logo/full/max/0/default.png",
        "sequences": [{"@type": "sc:Sequence", "canvases": []}],
    }

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
        logger.exception("‼️")
        raise

    # loop through sorted list of TIFF paths
    tiff_paths.sort()
    for f in tiff_paths:
        f = Path(f)
        page_num = f.stem.split("_")[-1]

        # create compressed pyramid TIFF
        if (
            # TODO use subprocess.run()
            os.system(
                f"{VIPS_CMD} tiffsave {f} {PROCESSED_IIIF_DIR}/{barcode}/{page_num}.tif --tile --pyramid --compression jpeg --tile-width 256 --tile-height 256"
            )
            != 0
        ):
            print("❌ an error occurred running the vips command")
            raise RuntimeError(
                f"{VIPS_CMD} tiffsave {f} {PROCESSED_IIIF_DIR}/{barcode}/{page_num}.tif --tile --pyramid --compression jpeg --tile-width 256 --tile-height 256"
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
                region_name=AWS_REGION,
            ).put_object(
                Bucket=S3_BUCKET,
                Key=f"{barcode}/{page_num}.tif",
                Body=open(
                    f"{PROCESSED_IIIF_DIR}/{barcode}/{page_num}.tif",
                    "rb",
                ),
            )
            print(
                f" ✅\t TIFF sent to S3: {barcode}/{page_num}.tif",
                flush=True,
            )
        except botocore.exceptions.ClientError as e:
            # https://boto3.amazonaws.com/v1/documentation/api/latest/guide/error-handling.html
            if e.response["Error"]["Code"] == "InternalError":
                print(f"Error Message: {e.response['Error']['Message']}")
                print(f"Request ID: {e.response['ResponseMetadata']['RequestId']}")
                print(f"HTTP Code: {e.response['ResponseMetadata']['HTTPStatusCode']}")
            else:
                logger.exception("‼️")
                raise e

        # set up canvas
        canvas = {
            "@type": "sc:Canvas",
            "@id": f"{CANVAS_BASE_URL}/{barcode}/{page_num}",
            "label": f"{page_num}",  # sequence portion of filename
            "width": width,
            "height": height,
            "images": [
                {
                    "@type": "oa:Annotation",
                    "motivation": "sc:painting",
                    "on": f"{CANVAS_BASE_URL}/{barcode}/{page_num}",  # same as canvas["@id"]
                    "resource": {
                        "@type": "dctypes:Image",
                        "@id": f"{IIIF_BASE_URL}/{barcode}%2F{page_num}/full/max/0/default.jpg",
                        "service": {
                            "@context": "http://iiif.io/api/image/2/context.json",
                            "@id": f"{IIIF_BASE_URL}/{barcode}%2F{page_num}",
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
        logger.exception("‼️")
        raise

    # remove `STATUS_FILES_DIR/{barcode}-processing` file
    try:
        Path(STATUS_FILES_DIR).joinpath(f"{barcode}-processing").unlink()
    except Exception as e:
        with open(Path(STATUS_FILES_DIR).joinpath(f"{barcode}-problem"), "w") as f:
            traceback.print_exc(file=f)
        logger.exception("‼️")
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


def parsed_metadata(text, barcode):
    """Return title, author, edition, and year from the JSON DIBS wrote."""
    if not text.strip():
        raise ValueError(
            "❌ the initiated file is empty; expected item metadata from DIBS"
        )
    try:
        metadata = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"❌ the initiated file is not valid JSON: {e}") from e
    if not isinstance(metadata, dict):
        raise ValueError("❌ the initiated file does not contain a JSON object")
    if metadata.get("barcode") != barcode:
        raise ValueError(
            f"❌ the initiated file is for barcode {metadata.get('barcode')}"
        )
    if not metadata.get("title"):
        raise ValueError("❌ the initiated file has no title")
    return (
        metadata["title"],
        metadata.get("author") or "",
        metadata.get("edition") or "",
        metadata.get("year") or "",
    )


def validate_settings():
    AWS_REGION = config("AWS_REGION")
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
        AWS_REGION,
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
    # fmt: off
    import plac; plac.call(main)
