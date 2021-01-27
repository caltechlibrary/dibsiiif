# EXPECTATIONS
# .env file containing the following variables and values:
"""
    PATH_TO_MARTIAN="" # location of martian application on filesystem
    MANIFEST_BASE_URL="" # URL path before "/{identifier}/manifest.json"
    S3_BUCKET="" # name of S3 bucket
    CANVAS_BASE_URL="" # URL path before item identifier and image identifier
    IIIF_SERVER_BASE_URL="" # URL path including "/{stage}/iiif/2" and before "/35047019492099_001"
    PATH_TO_PROCESSED_SCANS="" # location on filesystem to move processed item folders into
    PATH_TO_PROCESSED_IIIF="" # location on filesystem to move final IIIF items into
"""
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
# - martian program not found
# - vips program not found
# - supplied path to scans not valid
# - no access to S3 bucket
# - both .tif and .tiff extensions

# given: a source directory
# look for subdirectories
# create a list of TIFFs
# report on sequence anomalies
# loop through list of TIFFs
# create compressed pyramid TIFF
# retrieve book metadata
# create manifest.json file
# upload TIFF to S3
# upload manifest.json to ?

import boto3
import botocore
import json
import os
import plac
import shutil
import sys
from bs4 import BeautifulSoup
from decouple import config
from pathlib import Path


def main(
    path_to_scans: (
        "parent directory containing folders of scanned items",
        "positional",
        None,
        Path,
    )
):
    """Process digitized material for Caltech Library DIBS."""

    try:
        (
            PATH_TO_READY_SCANS,
            PATH_TO_MARTIAN,
            MANIFEST_BASE_URL,
            S3_BUCKET,
            CANVAS_BASE_URL,
            IIIF_SERVER_BASE_URL,
            PATH_TO_PROCESSED_SCANS,
            PATH_TO_PROCESSED_IIIF,
        ) = validate_config(path_to_scans)
    except FileNotFoundError as e:
        print(" ❌\t A problem occurred when validating the configuration.")
        raise e

    # look for subdirectories
    directory_paths = [e.path for e in os.scandir(PATH_TO_READY_SCANS) if e.is_dir()]

    for i in directory_paths:
        # create a list of TIFFs
        tiff_paths = []
        sequence = []
        for e in os.scandir(i):
            if e.is_file() and e.name.endswith((".tif", ".tiff")):
                # report on sequence anomalies
                if not e.name.split(".")[0].split("_", 1)[-1].isnumeric():
                    # TODO parse and transform sequence strings as output by scanning software
                    sys.exit(
                        f" ⚠️\t Non-numeric sequence identifier encountered: {e.path}"
                    )
                else:
                    tiff_paths.append(e.path)
                    sequence.append(int(e.name.split(".")[0].split("_")[-1]))

        # report on sequence anomalies
        missing = find_missing(sequence)
        if missing:
            print(missing)
            sys.exit(f" ⚠️\t Missing sequence number(s) in {i}")

        # set up manifest
        manifest = {
            "@context": "http://iiif.io/api/presentation/2/context.json",
            "@type": "sc:Manifest",
            "@id": f"{MANIFEST_BASE_URL}/{os.path.basename(i)}/manifest.json",  # TODO add domain
            "attribution": "Caltech Library",
            "logo": "https://www.library.caltech.edu/sites/default/files/caltechlibrary-logo.png",  # TODO add logo
            "sequences": [{"@type": "sc:Sequence", "canvases": []}],
        }

        # retrieve book metadata
        # NOTE assuming directory name is a barcode number
        # NOTE assuming martian is installed
        if (
            os.system(
                f"{PATH_TO_MARTIAN} --no-gui --output '/tmp/output.xml' 'barcode:{os.path.basename(i)}'"
            )
            != 0
        ):
            sys.exit(" ❌\t An error occurred running martian.")
        try:
            with open("/tmp/output.xml") as f:
                soup = BeautifulSoup(f, "xml")
                tag245a = soup.select("[tag='245'] > [code='a']")
                if tag245a:
                    title = tag245a[0].get_text().strip(" /:;,.")
                else:
                    sys.exit(
                        f" ❌\t title tag was empty for {os.path.basename(i)}; notify Laurel"
                    )
                subtitle = None
                tag245b = soup.select("[tag='245'] > [code='b']")
                if tag245b:
                    subtitle = f": {tag245b[0].get_text().strip(' /:;,.')}"
                author = None
                tag245c = soup.select("[tag='245'] > [code='c']")
                if tag245c:
                    author = tag245c[0].get_text().strip(" /:;,.")
                edition = None
                tag250a = soup.select("[tag='250'] > [code='a']")
                if tag250a:
                    edition = tag250a[0].get_text()
                tag008 = soup.select("[tag='008']")
                year = tag008.get_text()[7:11]
        except FileNotFoundError as e:
            print(
                f" ⚠️\t No output received from martian for item {os.path.basename(i)}."
            )
            continue

        manifest["label"] = title
        manifest["metadata"] = []
        manifest["metadata"].append({"label": "Title", "value": f"{title}{subtitle}"})
        if author:
            manifest["metadata"].append({"label": "Author", "value": author})
        if edition:
            manifest["metadata"].append({"label": "Edition", "value": edition})
        manifest["metadata"].append({"label": "Year", "value": year})

        os.makedirs(f"{PATH_TO_PROCESSED_IIIF}/{os.path.basename(i)}", exist_ok=True)
        # loop through list of TIFFs
        tiff_paths.sort()
        for f in tiff_paths:
            f = Path(f)
            # create compressed pyramid TIFF
            # vips tiffsave in.tiff out.tif --tile --pyramid --compression jpeg --tile-width 256 --tile-height 256
            if (
                os.system(
                    f"vips tiffsave {f} {PATH_TO_PROCESSED_IIIF}/{os.path.basename(i)}/{f.stem}.tif --tile --pyramid --compression jpeg --tile-width 256 --tile-height 256"
                )
                != 0
            ):
                sys.exit(" ❌\t An error occurred running vips.")
            # create canvas metadata
            # vipsheader -f width file.tiff
            # vipsheader -f height file.tiff
            width = os.popen(f"vipsheader -f width {f}").read().strip()
            height = os.popen(f"vipsheader -f height {f}").read().strip()

            # upload TIFF to S3
            try:
                boto3.client("s3").put_object(
                    Bucket=S3_BUCKET,
                    Key=f"{f.stem}.tif",
                    Body=open(
                        f"{PATH_TO_PROCESSED_IIIF}/{os.path.basename(i)}/{f.stem}.tif",
                        "rb",
                    ),
                )
                print(f" ✅\t TIFF sent to S3: {f.stem}.tif")
            except botocore.exceptions.ClientError as e:
                # https://boto3.amazonaws.com/v1/documentation/api/latest/guide/error-handling.html
                if e.response["Error"]["Code"] == "InternalError":
                    print(f"Error Message: {e.response['Error']['Message']}")
                    print(f"Request ID: {e.response['ResponseMetadata']['RequestId']}")
                    print(
                        f"HTTP Code: {e.response['ResponseMetadata']['HTTPStatusCode']}"
                    )
                else:
                    raise e

            # set up canvas
            canvas = {
                "@type": "sc:Canvas",
                "@id": f"{CANVAS_BASE_URL}/{f.stem}",  # TODO
                "label": f"{f.stem.split('_')[-1]}",  # sequence portion of filename
                "width": width,
                "height": height,
                "images": [
                    {
                        "@type": "oa:Annotation",
                        "motivation": "sc:painting",
                        "on": f"{CANVAS_BASE_URL}/{f.stem}",  # TODO same as canvas["@id"]
                        "resource": {
                            "@type": "dctypes:Image",
                            "@id": f"{IIIF_SERVER_BASE_URL}/{f.stem}/full/max/0/default.jpg",  # TODO
                            "service": {
                                "@context": "http://iiif.io/api/image/2/context.json",
                                "@id": f"{IIIF_SERVER_BASE_URL}/{f.stem}",  # TODO
                                "profile": "http://iiif.io/api/image/2/level2.json",
                            },
                        },
                    }
                ],
            }
            # add canvas to sequences
            manifest["sequences"][0]["canvases"].append(canvas)

        # save manifest.json
        with open(
            f"{PATH_TO_PROCESSED_IIIF}/{os.path.basename(i)}/manifest.json", "w"
        ) as f:
            f.write(json.dumps(manifest, indent=4))

        # TODO upload manifest.json to ?

        os.makedirs(f"{PATH_TO_PROCESSED_SCANS}/{os.path.basename(i)}", exist_ok=True)
        # move original item directory to PROCESSED location
        shutil.move(i, f"{PATH_TO_PROCESSED_SCANS}/{os.path.basename(i)}")


def directory_setup(directory):
    if not Path(directory).exists():
        Path(directory).mkdir()
    elif Path(directory).is_file():
        print(f" ❌\t A non-directory file exists at: {directory}")
        raise FileExistsError
    return Path(directory)


def find_missing(sequence):
    sequence.sort()
    return [x for x in range(sequence[0], sequence[-1] + 1) if x not in sequence]


def validate_config(path_to_scans):
    PATH_TO_READY_SCANS = Path(path_to_scans).resolve(strict=True)
    PATH_TO_MARTIAN = config("PATH_TO_MARTIAN", cast=Path).resolve(strict=True)
    MANIFEST_BASE_URL = config("MANIFEST_BASE_URL").rstrip("/")
    S3_BUCKET = config("S3_BUCKET")  # TODO validate access to bucket
    CANVAS_BASE_URL = config("CANVAS_BASE_URL").rstrip("/")
    IIIF_SERVER_BASE_URL = config("IIIF_SERVER_BASE_URL").rstrip("/")
    PATH_TO_PROCESSED_SCANS = directory_setup(
        config(
            "PATH_TO_PROCESSED_SCANS",
            default=f"{PATH_TO_READY_SCANS.parent}/DIBS_PROCESSED",
            cast=Path,
        )
    ).resolve(strict=True)
    PATH_TO_PROCESSED_IIIF = directory_setup(
        config(
            "PATH_TO_PROCESSED_IIIF",
            default=f"{PATH_TO_READY_SCANS.parent}/DIBS_IIIF",
            cast=Path,
        )
    ).resolve(strict=True)
    return (
        PATH_TO_READY_SCANS,
        PATH_TO_MARTIAN,
        MANIFEST_BASE_URL,
        S3_BUCKET,
        CANVAS_BASE_URL,
        IIIF_SERVER_BASE_URL,
        PATH_TO_PROCESSED_SCANS,
        PATH_TO_PROCESSED_IIIF,
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

# retrieve book metadata
# ~/Applications/martian/bin/martian -G "https://caltech.tind.io/search?p=barcode%3A35047019492099"
# ~/Desktop/output.xml
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
