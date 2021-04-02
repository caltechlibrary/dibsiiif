# EXPECTATIONS
# settings.ini file with appropriate values (see example-settings.ini)

# EXAMPLES
"""
    MANIFEST: https://purl.stanford.edu/qm670kv1873/iiif/manifest
    CANVAS: https://purl.stanford.edu/qm670kv1873/iiif/canvas/image_1
    RESOURCE: https://stacks.stanford.edu/image/iiif/qm670kv1873%2FW168_000001_300/full/full/0/default.jpg
    CANVAS: https://purl.stanford.edu/qm670kv1873/iiif/canvas/image_2
    RESOURCE: https://stacks.stanford.edu/image/iiif/qm670kv1873%2FW168_000002_300/full/full/0/default.jpg
"""

# TEST CASES
# - no results from barcode
# - vips program not found
# - supplied path to scans not valid
# - no access to S3 bucket
# - both .tif and .tiff extensions

import json
import os
import shutil
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
            STATUS_FILES_DIR,
            UNPROCESSED_SCANS_DIR,
            PROCESSED_SCANS_DIR,
            PROCESSED_IIIF_DIR,
            MANIFEST_BASE_URL,
            CANVAS_BASE_URL,
            IIIF_BASE_URL,
            S3_BUCKET,
        ) = validate_settings()
    except Exception as e:
        # NOTE we cannot guarantee that `STATUS_FILES_DIR` exists
        # TODO send a message to devs including `str(e)`
        print(" ❌\t A problem occurred when validating the settings.")
        raise

    # remove `STATUS_FILES_DIR/{barcode}-initiated` file
    try:
        Path(STATUS_FILES_DIR).joinpath(f"{barcode}-initiated").unlink()
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
            # TODO confirm proper way to raise this
            raise net_exception
        else:
            soup = BeautifulSoup(net_response.text, "xml")
            tag245a = soup.select("[tag='245'] > [code='a']")
            if tag245a:
                title = tag245a[0].get_text().strip(" /:;,.")
            else:
                # TODO raise an exception
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

    manifest["label"] = title
    manifest["metadata"] = []
    manifest["metadata"].append({"label": "Title", "value": f"{title}{subtitle}"})
    if author:
        manifest["metadata"].append({"label": "Author", "value": author})
    if edition:
        manifest["metadata"].append({"label": "Edition", "value": edition})
    manifest["metadata"].append({"label": "Year", "value": year})

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
                f"vips tiffsave {f} {PROCESSED_IIIF_DIR}/{barcode}/{f.stem.split('_')[-1]}.tif --tile --pyramid --compression jpeg --tile-width 256 --tile-height 256"
            )
            != 0
        ):
            print(" ❌\t An error occurred running the following vips command:")
            print(
                f" \t vips tiffsave {f} {PROCESSED_IIIF_DIR}/{barcode}/{f.stem.split('_')[-1]}.tif --tile --pyramid --compression jpeg --tile-width 256 --tile-height 256"
            )
            sys.exit()
        # create canvas metadata
        width = os.popen(f"vipsheader -f width {f}").read().strip()
        height = os.popen(f"vipsheader -f height {f}").read().strip()

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
        f"{PROCESSED_IIIF_DIR}/{barcode}/{barcode}-manifest.json",
        "w",
    ) as f:
        f.write(json.dumps(manifest, indent=4))

    # move original item directory to PROCESSED location
    shutil.move(i, f"{PROCESSED_SCANS_DIR}")


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
    STATUS_FILES_DIR = Path(os.path.expanduser(config("STATUS_FILES_DIR"))).resolve(
        strict=True
    )  # NOTE do not create missing `STATUS_FILES_DIR`
    UNPROCESSED_SCANS_DIR = directory_setup(
        os.path.expanduser(config("UNPROCESSED_SCANS_DIR"))
    ).resolve(strict=True)
    PROCESSED_SCANS_DIR = directory_setup(
        os.path.expanduser(config("PROCESSED_SCANS_DIR"))
    ).resolve(strict=True)
    PROCESSED_IIIF_DIR = directory_setup(
        os.path.expanduser(config("PROCESSED_IIIF_DIR"))
    ).resolve(strict=True)
    MANIFEST_BASE_URL = config("MANIFEST_BASE_URL").rstrip("/")
    CANVAS_BASE_URL = config("CANVAS_BASE_URL").rstrip("/")
    IIIF_BASE_URL = config("IIIF_BASE_URL").rstrip("/")
    S3_BUCKET = config("S3_BUCKET")  # TODO validate access to bucket
    return (
        STATUS_FILES_DIR,
        UNPROCESSED_SCANS_DIR,
        PROCESSED_SCANS_DIR,
        PROCESSED_IIIF_DIR,
        MANIFEST_BASE_URL,
        CANVAS_BASE_URL,
        IIIF_BASE_URL,
        S3_BUCKET,
    )


if __name__ == "__main__":
    plac.call(main)

# minimal manifest
"""
    {
      "@context": "http://iiif.io/api/presentation/2/context.json",
      "@type": "sc:Manifest",
      "@id": "http://localhost:3000/manifest.json",
      "label": "Papillons",
      "description": "Four patterns inspired by butterflies.",
      "attribution": "Special Collections Research Center at NCSU Libraries",
      "logo": "http://localhost:3000/logo.jpg",
      "sequences": [
        {
          "@type": "sc:Sequence",
          "canvases": [
            {
              "@type": "sc:Canvas",
              "@id": "http://localhost:3000/segPap_022/canvas/1",
              "label": "22",
              "width": 6099,
              "height": 8599,
              "images": [
                {
                  "@type": "oa:Annotation",
                  "motivation": "sc:painting",
                  "on": "http://localhost:3000/segPap_022/canvas/1",
                  "resource": {
                    "@type": "dctypes:Image",
                    "@id": "https://iiif.lib.ncsu.edu/iiif/segPap_022/full/500,/0/default.jpg",
                    "service": {
                      "@context":  "http://iiif.io/api/image/2/context.json",
                      "@id": "https://iiif.lib.ncsu.edu/iiif/segPap_022",
                      "profile": "http://iiif.io/api/image/2/level2.json"
                    }
                  }
                }
              ]
            },
            {
              "@type": "sc:Canvas",
              "@id": "http://localhost:3000/segPap_023/canvas/2",
              "label": "23",
              "width": 6099,
              "height": 8599,
              "images": [
                {
                  "@type": "oa:Annotation",
                  "motivation": "sc:painting",
                  "on": "http://localhost:3000/segPap_023/canvas/2",
                  "resource": {
                    "@type": "dctypes:Image",
                    "@id": "https://iiif.lib.ncsu.edu/iiif/segPap_023/full/500,/0/default.jpg",
                    "service": {
                      "@context":  "http://iiif.io/api/image/2/context.json",
                      "@id": "https://iiif.lib.ncsu.edu/iiif/segPap_023",
                      "profile": "http://iiif.io/api/image/2/level2.json"
                    }
                  }
                }
              ]
            }
          ]
        }
      ]
    }
"""

# book metadata in MARCXML
"""
    <?xml version="1.0" encoding="UTF-8"?>
    <collection xmlns="http://www.loc.gov/MARC21/slim">
    <record>
      <controlfield tag="000">01059cam\a2200361Ia\4500</controlfield>
      <controlfield tag="001">735973</controlfield>
      <controlfield tag="005">20201028221548.0</controlfield>
      <controlfield tag="008">120118s2012\\\\nyua\\\\\b\\\\001\0\eng\d</controlfield>
      <datafield tag="010" ind1=" " ind2=" ">
        <subfield code="a">2011931725</subfield>
      </datafield>
      <datafield tag="015" ind1=" " ind2=" ">
        <subfield code="a">GBB1D1820</subfield>
        <subfield code="2">bnb</subfield>
      </datafield>
      <datafield tag="016" ind1="7" ind2=" ">
        <subfield code="a">015969759</subfield>
        <subfield code="2">Uk</subfield>
      </datafield>
      <datafield tag="019" ind1=" " ind2=" ">
        <subfield code="a">761380918</subfield>
      </datafield>
      <datafield tag="020" ind1=" " ind2=" ">
        <subfield code="a">1429215089</subfield>
      </datafield>
      <datafield tag="020" ind1=" " ind2=" ">
        <subfield code="a">1429224045 (hbk.)</subfield>
      </datafield>
      <datafield tag="020" ind1=" " ind2=" ">
        <subfield code="a">9781429215084</subfield>
      </datafield>
      <datafield tag="020" ind1=" " ind2=" ">
        <subfield code="a">9781429224048 (hbk.)</subfield>
      </datafield>
      <datafield tag="035" ind1=" " ind2=" ">
        <subfield code="a">(OCoLC)773193687</subfield>
        <subfield code="z">(OCoLC)761380918</subfield>
      </datafield>
      <datafield tag="040" ind1=" " ind2=" ">
        <subfield code="a">IPL</subfield>
        <subfield code="c">IPL</subfield>
        <subfield code="d">YDXCP</subfield>
        <subfield code="d">UKMGB</subfield>
        <subfield code="d">BWX</subfield>
        <subfield code="d">CIT</subfield>
      </datafield>
      <datafield tag="049" ind1=" " ind2=" ">
        <subfield code="a">CIT5</subfield>
      </datafield>
      <datafield tag="050" ind1=" " ind2="4">
        <subfield code="a">QA303</subfield>
        <subfield code="b">.M338 2012</subfield>
      </datafield>
      <datafield tag="100" ind1="1" ind2=" ">
        <subfield code="a">Marsden, Jerrold E</subfield>
      </datafield>
      <datafield tag="245" ind1="1" ind2="0">
        <subfield code="a">Vector calculus /</subfield>
        <subfield code="c">Jerrold E. Marsden, Anthony Tromba</subfield>
      </datafield>
      <datafield tag="250" ind1=" " ind2=" ">
        <subfield code="a">6th ed</subfield>
      </datafield>
      <datafield tag="260" ind1=" " ind2=" ">
        <subfield code="a">New York :</subfield>
        <subfield code="b">W.H. Freeman,</subfield>
        <subfield code="c">c2012</subfield>
      </datafield>
      <datafield tag="300" ind1=" " ind2=" ">
        <subfield code="a">xxv, 545 p. :</subfield>
        <subfield code="b">ill. (some col.) ;</subfield>
        <subfield code="c">26 cm</subfield>
      </datafield>
      <datafield tag="504" ind1=" " ind2=" ">
        <subfield code="a">Includes bibliographical references and index</subfield>
      </datafield>
      <datafield tag="650" ind1=" " ind2="0">
        <subfield code="a">Calculus</subfield>
      </datafield>
      <datafield tag="650" ind1=" " ind2="0">
        <subfield code="a">Vector analysis</subfield>
      </datafield>
      <datafield tag="690" ind1=" " ind2=" ">
        <subfield code="a">Caltech authors</subfield>
      </datafield>
      <datafield tag="700" ind1="1" ind2=" ">
        <subfield code="a">Tromba, Anthony</subfield>
      </datafield>
      <datafield tag="907" ind1=" " ind2=" ">
        <subfield code="a">.b14946786</subfield>
        <subfield code="b">150825</subfield>
        <subfield code="c">120214</subfield>
      </datafield>
      <datafield tag="909" ind1="C" ind2="O">
        <subfield code="o">oai:caltech.tind.io:735973</subfield>
        <subfield code="p">caltech:bibliographic</subfield>
      </datafield>
      <datafield tag="948" ind1=" " ind2=" ">
        <subfield code="a">PP</subfield>
      </datafield>
      <datafield tag="980" ind1=" " ind2=" ">
        <subfield code="a">BIB</subfield>
      </datafield>
      <datafield tag="998" ind1=" " ind2=" ">
        <subfield code="a">sfl</subfield>
        <subfield code="b">120313</subfield>
        <subfield code="c">a</subfield>
        <subfield code="d">m</subfield>
        <subfield code="e">-</subfield>
        <subfield code="f">eng</subfield>
        <subfield code="g">nyu</subfield>
        <subfield code="h">0</subfield>
        <subfield code="i">1</subfield>
      </datafield>
    </record>
    </collection>
"""
