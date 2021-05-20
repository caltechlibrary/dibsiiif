# EXPECTATIONS
# settings.ini file with appropriate values (see example-settings.ini)

# NOTE: this script should be initiated by the `iiifify.sh` script that runs on cron

import json
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

import boto3
import botocore
import plac
from bs4 import BeautifulSoup
from commonpy.network_utils import net
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
    try:
        (net_response, net_exception) = net(
            "get",
            f"https://caltech.tind.io/search?p=barcode%3A{barcode}&of=xm",
        )
        if net_exception:
            raise net_exception
        else:
            soup = BeautifulSoup(net_response.text, "xml")
            tag245a = soup.select("[tag='245'] > [code='a']")
            if tag245a:
                title = tag245a[0].get_text().strip(" /:;,.")
            else:
                raise ValueError(f"❌ title tag was empty for {barcode}; notify Laurel")
            subtitle = ""
            tag245b = soup.select("[tag='245'] > [code='b']")
            if tag245b:
                subtitle = f": {tag245b[0].get_text().strip(' /:;,.')}"
            author = ""
            tag245c = soup.select("[tag='245'] > [code='c']")
            if tag245c:
                author = tag245c[0].get_text().strip(" /:;,.")
            edition = ""
            tag250a = soup.select("[tag='250'] > [code='a']")
            if tag250a:
                edition = tag250a[0].get_text()
            tag008 = soup.select("[tag='008']")
            year = tag008[0].get_text()[7:11]
    except Exception as e:
        with open(Path(STATUS_FILES_DIR).joinpath(f"{barcode}-problem"), "w") as f:
            traceback.print_exc(file=f)
        raise

    # add metadata to manifest
    manifest["label"] = title
    manifest["metadata"] = []
    manifest["metadata"].append({"label": "Title", "value": f"{title}{subtitle}"})
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
            raise RuntimeError(f"{VIPS_CMD} tiffsave {f} {PROCESSED_IIIF_DIR}/{barcode}/{f.stem.split('_')[-1]}.tif --tile --pyramid --compression jpeg --tile-width 256 --tile-height 256")
        # create canvas metadata
        # HACK the binaries for `vips` and `vipsheader` should be in the same place
        width = os.popen(f"{VIPS_CMD}header -f width {f}").read().strip()
        height = os.popen(f"{VIPS_CMD}header -f height {f}").read().strip()

        # upload TIFF to S3
        try:
            boto3.client("s3").put_object(
                Bucket=S3_BUCKET,
                Key=f"{barcode}/{f.stem.split('_')[-1]}.tif",
                Body=open(
                    f"{PROCESSED_IIIF_DIR}/{barcode}/{f.stem.split('_')[-1]}.tif",
                    "rb",
                ),
            )
            print(f" ✅\t TIFF sent to S3: {barcode}/{f.stem.split('_')[-1]}.tif")
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
    VIPS_CMD = Path(os.path.expanduser(config("VIPS_CMD"))).resolve(
        strict=True
    )
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
