import csv
import json
import os
import sqlite3
import zipfile

from PIL import Image as PILImage, UnidentifiedImageError


# =========================================================
# Supported file types
# =========================================================

CSV_TYPES = {"csv", "tsv"}

JSON_TYPES = {"json", "jsonl"}

EXCEL_TYPES = {"xlsx", "xls", "excel"}

PARQUET_TYPES = {"parquet"}

HDF5_TYPES = {"hdf5", "h5"}

NETCDF_TYPES = {"netcdf", "nc"}

XML_TYPES = {"xml"}

DATABASE_TYPES = {"sqlite", "sqlite3", "db"}

MATLAB_TYPES = {"mat"}

R_TYPES = {"rdata", "rds"}

GEOJSON_TYPES = {"geojson"}

SHAPEFILE_TYPES = {"shp"}

IMAGE_TYPES = {
    "jpg",
    "jpeg",
    "png",
    "gif",
    "bmp",
    "webp",
    "tiff",
    "tif",
    "heic",
    "heif",
    "avif",
}

AUDIO_TYPES = {
    "wav",
    "mp3",
    "flac",
    "ogg",
    "aac",
    "m4a",
}

VIDEO_TYPES = {
    "mp4",
    "avi",
    "mov",
    "mkv",
    "webm",
    "mpeg",
    "mpg",
}

DOCUMENT_TYPES = {
    "pdf",
}

TEXT_TYPES = {
    "txt",
    "text",
    "md",
    "markdown",
}

ARCHIVE_TYPES = {
    "zip",
}


SUPPORTED_TYPES = (
    CSV_TYPES
    | JSON_TYPES
    | EXCEL_TYPES
    | PARQUET_TYPES
    | HDF5_TYPES
    | NETCDF_TYPES
    | XML_TYPES
    | DATABASE_TYPES
    | MATLAB_TYPES
    | R_TYPES
    | GEOJSON_TYPES
    | SHAPEFILE_TYPES
    | IMAGE_TYPES
    | AUDIO_TYPES
    | VIDEO_TYPES
    | DOCUMENT_TYPES
    | TEXT_TYPES
    | ARCHIVE_TYPES
)


# =========================================================
# File signatures
# =========================================================

XLSX_MARKER = "xl/workbook.xml"

PARQUET_MAGIC = b"PAR1"

PDF_MAGIC = b"%PDF"

HDF5_MAGIC = b"\x89HDF\r\n\x1a\n"

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

JPEG_MAGIC = b"\xff\xd8\xff"

GIF87_MAGIC = b"GIF87a"

GIF89_MAGIC = b"GIF89a"

BMP_MAGIC = b"BM"

FLAC_MAGIC = b"fLaC"

OGG_MAGIC = b"OggS"

MP3_MAGIC = b"\xff\xfb"


class FileTypeMismatchError(Exception):
    pass


# =========================================================
# Excel
# =========================================================

def _is_valid_xlsx(file_path):
    """
    XLSX is a ZIP container containing xl/workbook.xml.
    DOCX/PPTX are also ZIP containers, so the workbook marker
    is used to distinguish XLSX from them.
    """
    try:
        with zipfile.ZipFile(file_path) as z:
            return XLSX_MARKER in z.namelist()

    except (zipfile.BadZipFile, OSError):
        return False


def _is_valid_xls(file_path):
    """
    Old .xls files use the OLE Compound File format.
    They normally begin with this 8-byte signature.
    """
    try:
        with open(file_path, "rb") as f:
            header = f.read(8)

        return header == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

    except OSError:
        return False


# =========================================================
# Parquet
# =========================================================

def _is_valid_parquet(file_path):
    """
    Parquet files have PAR1 magic bytes at both
    the beginning and end of the file.
    """
    try:
        with open(file_path, "rb") as f:
            if f.read(4) != PARQUET_MAGIC:
                return False

            f.seek(-4, os.SEEK_END)

            return f.read(4) == PARQUET_MAGIC

    except (OSError, ValueError):
        return False


# =========================================================
# Images
# =========================================================

def _is_valid_image(file_path):
    """
    Pillow verifies whether the file is actually
    a recognizable image.
    """
    try:
        with PILImage.open(file_path) as img:
            img.verify()

        return True

    except (UnidentifiedImageError, OSError):
        return _has_image_signature(file_path)


def _has_image_signature(file_path):
    try:
        with open(file_path, "rb") as f:
            header = f.read(16)

    except OSError:
        return False

    return (
        header.startswith(PNG_MAGIC)
        or header.startswith(JPEG_MAGIC)
        or header.startswith(GIF87_MAGIC)
        or header.startswith(GIF89_MAGIC)
        or header.startswith(BMP_MAGIC)
        or (
            header.startswith(b"RIFF")
            and header[8:12] == b"WEBP"
        )
        or header.startswith(b"II*\x00")
        or header.startswith(b"MM\x00*")
    )


# =========================================================
# HDF5
# =========================================================

def _is_valid_hdf5(file_path):
    try:
        with open(file_path, "rb") as f:
            return f.read(8) == HDF5_MAGIC

    except OSError:
        return False


# =========================================================
# NetCDF
# =========================================================

def _is_valid_netcdf(file_path):
    """
    Classic NetCDF files use either:
        CDF\x01
        CDF\x02

    NetCDF-4 files are HDF5 containers and therefore use
    the HDF5 signature.
    """
    try:
        with open(file_path, "rb") as f:
            header = f.read(8)

        return (
            header.startswith(b"CDF\x01")
            or header.startswith(b"CDF\x02")
            or header == HDF5_MAGIC
        )

    except OSError:
        return False


# =========================================================
# XML
# =========================================================

def _is_valid_xml(file_path):
    try:
        import xml.etree.ElementTree as ET

        ET.parse(file_path)

        return True

    except (
        ET.ParseError,
        OSError,
        UnicodeDecodeError,
    ):
        return False


# =========================================================
# SQLite
# =========================================================

def _is_valid_sqlite(file_path):
    """
    SQLite databases begin with:
        SQLite format 3
    """
    try:
        with open(file_path, "rb") as f:
            header = f.read(16)

        if header != b"SQLite format 3\x00":
            return False

        # Open read-only so validation never modifies
        # the uploaded database.
        connection = sqlite3.connect(
            f"file:{os.path.abspath(file_path)}?mode=ro",
            uri=True,
        )

        try:
            connection.execute("PRAGMA schema_version;")

        finally:
            connection.close()

        return True

    except (OSError, sqlite3.Error):
        return False


# =========================================================
# MATLAB
# =========================================================

def _is_valid_matlab(file_path):
    """
    MATLAB .mat files have recognizable MAT-file headers.

    This performs a lightweight signature check rather than
    attempting to fully parse the file.
    """
    try:
        with open(file_path, "rb") as f:
            header = f.read(128)

        # Most traditional MAT files contain "MATLAB"
        # in the descriptive header.
        if b"MATLAB" in header:
            return True

        # MATLAB v5/v7 files may use the OLE container
        # signature.
        if header.startswith(
            b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
        ):
            return True

        # MATLAB v7.3 uses HDF5.
        if header.startswith(HDF5_MAGIC):
            return True

        return False

    except OSError:
        return False


# =========================================================
# RData / RDS
# =========================================================

def _is_valid_r_file(file_path):
    """
    RData/RDS are commonly serialized R objects.

    This is intentionally a lightweight validation. R's
    serialization format can be compressed, so we check
    for common serialization and compression signatures.
    """
    try:
        with open(file_path, "rb") as f:
            header = f.read(16)

        # Common gzip-compressed RData/RDS
        if header.startswith(b"\x1f\x8b"):
            return True

        # Common bzip2
        if header.startswith(b"BZh"):
            return True

        # Common xz
        if header.startswith(b"\xfd7zXZ\x00"):
            return True

        # Uncompressed R serialization commonly begins
        # with ASCII serialization markers.
        if (
            header.startswith(b"RDX")
            or header.startswith(b"RDA")
            or header.startswith(b"X\n")
        ):
            return True

        return False

    except OSError:
        return False


# =========================================================
# GeoJSON
# =========================================================

def _is_valid_geojson(file_path):
    try:
        with open(
            file_path,
            "r",
            encoding="utf-8",
        ) as f:
            data = json.load(f)

        if not isinstance(data, dict):
            return False

        geojson_type = data.get("type")

        valid_types = {
            "Feature",
            "FeatureCollection",
            "Point",
            "MultiPoint",
            "LineString",
            "MultiLineString",
            "Polygon",
            "MultiPolygon",
            "GeometryCollection",
        }

        return geojson_type in valid_types

    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        return False


# =========================================================
# Shapefile
# =========================================================

def _is_valid_shapefile(file_path):
    """
    A .shp file has a fixed 100-byte header.

    Bytes 0-3 contain the big-endian file code 9994.
    Bytes 28-31 contain the little-endian version 1000.
    """
    try:
        with open(file_path, "rb") as f:
            header = f.read(100)

        if len(header) < 100:
            return False

        file_code = int.from_bytes(
            header[0:4],
            byteorder="big",
        )

        version = int.from_bytes(
            header[28:32],
            byteorder="little",
        )

        return (
            file_code == 9994
            and version == 1000
        )

    except OSError:
        return False


# =========================================================
# PDF
# =========================================================

def _is_valid_pdf(file_path):
    try:
        with open(file_path, "rb") as f:
            return f.read(4) == PDF_MAGIC

    except OSError:
        return False


# =========================================================
# ZIP
# =========================================================

def _is_valid_zip(file_path):
    try:
        with zipfile.ZipFile(file_path) as z:
            return z.testzip() is None

    except (
        zipfile.BadZipFile,
        OSError,
    ):
        return False


# =========================================================
# Audio
# =========================================================

def _is_valid_audio(file_path, declared):
    try:
        with open(file_path, "rb") as f:
            header = f.read(16)

    except OSError:
        return False

    if declared == "wav":
        return (
            header.startswith(b"RIFF")
            and header[8:12] == b"WAVE"
        )

    if declared == "flac":
        return header.startswith(FLAC_MAGIC)

    if declared == "ogg":
        return header.startswith(OGG_MAGIC)

    if declared == "mp3":
        return (
            header.startswith(b"ID3")
            or header.startswith(MP3_MAGIC)
            or (
                len(header) >= 2
                and header[0] == 0xFF
                and (header[1] & 0xE0) == 0xE0
            )
        )

    # AAC/M4A can have multiple container signatures.
    if declared in {"aac", "m4a"}:
        return (
            header.startswith(b"ADIF")
            or b"ftyp" in header[4:12]
        )

    return False


# =========================================================
# Video
# =========================================================

def _is_valid_video(file_path, declared):
    try:
        with open(file_path, "rb") as f:
            header = f.read(32)

    except OSError:
        return False

    if declared == "mp4":
        return (
            len(header) >= 12
            and header[4:8] == b"ftyp"
        )

    if declared == "mov":
        return (
            len(header) >= 12
            and header[4:8] == b"ftyp"
        )

    if declared == "webm":
        return header.startswith(b"\x1a\x45\xdf\xa3")

    if declared == "mkv":
        return header.startswith(b"\x1a\x45\xdf\xa3")

    if declared == "avi":
        return (
            header.startswith(b"RIFF")
            and header[8:12] == b"AVI "
        )

    if declared in {"mpeg", "mpg"}:
        return (
            header.startswith(b"\x00\x00\x01\xba")
            or header.startswith(b"\x00\x00\x01\xb3")
        )

    return False


# =========================================================
# Main validator
# =========================================================

def validate_file_matches_declared_type(
    file_path,
    declared_file_type,
):
    """
    Verify that the uploaded file's actual contents are
    compatible with the declared file type.

    This is a format/signature validator, not a complete
    semantic validation of every possible file format.
    """

    declared = (
        declared_file_type
        .lower()
        .strip()
        .lstrip(".")
    )

    # -----------------------------------------------------
    # Supported type check
    # -----------------------------------------------------

    if declared not in SUPPORTED_TYPES:
        raise FileTypeMismatchError(
            f"'{declared}' is not a supported file type."
        )

    # -----------------------------------------------------
    # Images
    # -----------------------------------------------------

    if declared in IMAGE_TYPES:
        if not _is_valid_image(file_path):
            raise FileTypeMismatchError(
                f"File does not appear to be a valid image, "
                f"but was declared as '{declared}'."
            )

        return

    # -----------------------------------------------------
    # Cross-type protection
    # -----------------------------------------------------
    # Prevent an actual image from being uploaded while
    # claiming to be another supported file type.
    #
    # Example:
    #
    #   JPEG declared as CSV  -> rejected
    #   PNG declared as JSON  -> rejected
    #   GIF declared as PDF   -> rejected
    #
    # This preserves the original protection.

    if declared not in IMAGE_TYPES:
        if _is_valid_image(file_path):
            raise FileTypeMismatchError(
                f"File appears to be an image, but was declared "
                f"as '{declared}'."
            )

    # -----------------------------------------------------
    # Excel
    # -----------------------------------------------------

    if declared in EXCEL_TYPES:

        if declared in {"xlsx", "excel"}:
            valid = _is_valid_xlsx(file_path)
        else:
            valid = _is_valid_xls(file_path)

        if not valid:
            raise FileTypeMismatchError(
                f"File does not appear to be a valid Excel "
                f"workbook, but was declared as '{declared}'."
            )

        return

    # -----------------------------------------------------
    # Parquet
    # -----------------------------------------------------

    if declared in PARQUET_TYPES:

        if not _is_valid_parquet(file_path):
            raise FileTypeMismatchError(
                f"File does not appear to be a valid Parquet "
                f"file, but was declared as '{declared}'."
            )

        return

    # -----------------------------------------------------
    # HDF5
    # -----------------------------------------------------

    if declared in HDF5_TYPES:

        if not _is_valid_hdf5(file_path):
            raise FileTypeMismatchError(
                f"File does not appear to be a valid HDF5 file, "
                f"but was declared as '{declared}'."
            )

        return

    # -----------------------------------------------------
    # NetCDF
    # -----------------------------------------------------

    if declared in NETCDF_TYPES:

        if not _is_valid_netcdf(file_path):
            raise FileTypeMismatchError(
                f"File does not appear to be a valid NetCDF file, "
                f"but was declared as '{declared}'."
            )

        return

    # -----------------------------------------------------
    # JSON / JSONL
    # -----------------------------------------------------

    if declared in JSON_TYPES:

        try:
            with open(
                file_path,
                "r",
                encoding="utf-8",
            ) as f:
                text = f.read()

        except (
            OSError,
            UnicodeDecodeError,
        ):
            raise FileTypeMismatchError(
                f"File does not appear to be valid UTF-8 text, "
                f"but was declared as '{declared}'."
            )

        if declared == "json":

            try:
                json.loads(text)

            except json.JSONDecodeError:
                raise FileTypeMismatchError(
                    "File does not appear to be valid JSON, "
                    "but was declared as 'json'."
                )

        else:

            lines = [
                line
                for line in text.splitlines()
                if line.strip()
            ]

            if not lines:
                raise FileTypeMismatchError(
                    "File is empty or has no valid lines, "
                    "but was declared as 'jsonl'."
                )

            try:
                for line in lines[:50]:
                    json.loads(line)

            except json.JSONDecodeError:
                raise FileTypeMismatchError(
                    "File does not appear to be valid JSONL "
                    "(newline-delimited JSON), "
                    "but was declared as 'jsonl'."
                )

        return

    # -----------------------------------------------------
    # CSV / TSV
    # -----------------------------------------------------

    if declared in CSV_TYPES:

        try:
            with open(file_path, "rb") as f:
                raw_sample = f.read(8192)

            sample = raw_sample.decode("utf-8")

        except (
            OSError,
            UnicodeDecodeError,
        ):
            raise FileTypeMismatchError(
                f"File does not appear to be valid UTF-8 text, "
                f"but was declared as '{declared}'."
            )

        try:
            dialect = csv.Sniffer().sniff(
                sample,
                delimiters=",\t;",
            )

        except csv.Error:
            raise FileTypeMismatchError(
                f"File does not appear to be delimited text, "
                f"but was declared as '{declared}'."
            )

        if (
            declared == "tsv"
            and dialect.delimiter != "\t"
        ):
            raise FileTypeMismatchError(
                "File was declared as TSV but does not appear "
                "to use tab separators."
            )

        if (
            declared == "csv"
            and dialect.delimiter == "\t"
        ):
            raise FileTypeMismatchError(
                "File was declared as CSV but appears to use "
                "tab separators."
            )

        return

    # -----------------------------------------------------
    # XML
    # -----------------------------------------------------

    if declared in XML_TYPES:

        if not _is_valid_xml(file_path):
            raise FileTypeMismatchError(
                "File does not appear to be valid XML."
            )

        return

    # -----------------------------------------------------
    # SQLite
    # -----------------------------------------------------

    if declared in DATABASE_TYPES:

        if not _is_valid_sqlite(file_path):
            raise FileTypeMismatchError(
                "File does not appear to be a valid SQLite database."
            )

        return

    # -----------------------------------------------------
    # MATLAB
    # -----------------------------------------------------

    if declared in MATLAB_TYPES:

        if not _is_valid_matlab(file_path):
            raise FileTypeMismatchError(
                "File does not appear to be a valid MATLAB file."
            )

        return

    # -----------------------------------------------------
    # RData / RDS
    # -----------------------------------------------------

    if declared in R_TYPES:

        if not _is_valid_r_file(file_path):
            raise FileTypeMismatchError(
                "File does not appear to be a valid RData/RDS file."
            )

        return

    # -----------------------------------------------------
    # GeoJSON
    # -----------------------------------------------------

    if declared in GEOJSON_TYPES:

        if not _is_valid_geojson(file_path):
            raise FileTypeMismatchError(
                "File does not appear to be valid GeoJSON."
            )

        return

    # -----------------------------------------------------
    # Shapefile
    # -----------------------------------------------------

    if declared in SHAPEFILE_TYPES:

        if not _is_valid_shapefile(file_path):
            raise FileTypeMismatchError(
                "File does not appear to be a valid Shapefile."
            )

        return

    # -----------------------------------------------------
    # PDF
    # -----------------------------------------------------

    if declared in DOCUMENT_TYPES:

        if not _is_valid_pdf(file_path):
            raise FileTypeMismatchError(
                "File does not appear to be a valid PDF."
            )

        return

    # -----------------------------------------------------
    # Plain text / Markdown
    # -----------------------------------------------------

    if declared in TEXT_TYPES:

        try:
            with open(
                file_path,
                "r",
                encoding="utf-8",
            ) as f:
                f.read(8192)

        except (
            OSError,
            UnicodeDecodeError,
        ):
            raise FileTypeMismatchError(
                f"File does not appear to be valid UTF-8 text, "
                f"but was declared as '{declared}'."
            )

        return

    # -----------------------------------------------------
    # ZIP
    # -----------------------------------------------------

    if declared in ARCHIVE_TYPES:

        if not _is_valid_zip(file_path):
            raise FileTypeMismatchError(
                "File does not appear to be a valid ZIP archive."
            )

        return

    # -----------------------------------------------------
    # Audio
    # -----------------------------------------------------

    if declared in AUDIO_TYPES:

        if not _is_valid_audio(
            file_path,
            declared,
        ):
            raise FileTypeMismatchError(
                f"File does not appear to be a valid "
                f"'{declared}' audio file."
            )

        return

    # -----------------------------------------------------
    # Video
    # -----------------------------------------------------

    if declared in VIDEO_TYPES:

        if not _is_valid_video(
            file_path,
            declared,
        ):
            raise FileTypeMismatchError(
                f"File does not appear to be a valid "
                f"'{declared}' video file."
            )

        return