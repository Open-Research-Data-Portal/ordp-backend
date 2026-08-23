import csv
import imghdr
import json
import zipfile

CSV_TYPES = {"csv", "tsv"}
JSON_TYPES = {"json", "jsonl"}
EXCEL_TYPES = {"xlsx", "xls", "excel"}
IMAGE_TYPES = {"jpg", "jpeg", "png", "gif", "bmp", "webp", "tiff", "tif"}
PARQUET_TYPES = {"parquet"}

# Internal file that only exists inside genuine Excel workbooks (xlsx is a ZIP container)
XLSX_MARKER = "xl/workbook.xml"

# Parquet files start and end with this 4-byte magic string
PARQUET_MAGIC = b"PAR1"


class FileTypeMismatchError(Exception):
    pass


def _is_valid_xlsx(file_path):
    """xlsx/xls are ZIP containers. A genuine Excel workbook contains
    xl/workbook.xml; other ZIP-based formats (pptx, docx) do not."""
    try:
        with zipfile.ZipFile(file_path) as z:
            return XLSX_MARKER in z.namelist()
    except zipfile.BadZipFile:
        return False


def _is_valid_parquet(file_path):
    """Parquet files have the magic bytes 'PAR1' at both the start and end
    of the file (the header and footer)."""
    try:
        with open(file_path, "rb") as f:
            if f.read(4) != PARQUET_MAGIC:
                return False
            f.seek(-4, 2)  # seek to 4 bytes before end of file
            return f.read(4) == PARQUET_MAGIC
    except (OSError, ValueError):
        return False


def validate_file_matches_declared_type(file_path, declared_file_type):
    """Sniffs the actual bytes and confirms they're plausible for the declared type.
    Not a full-format validator — just enough to catch the classic 'said CSV,
    uploaded a JPEG' mismatch your spec called out."""
    declared = declared_file_type.lower().strip()

    # --- Images ---
    if declared in IMAGE_TYPES:
        detected = imghdr.what(file_path)
        if detected is None:
            raise FileTypeMismatchError(
                f"File does not appear to be a valid image, but was declared as '{declared}'."
            )
        return

    # Catch "declared as non-image but is actually an image" for any remaining type
    if imghdr.what(file_path) is not None:
        raise FileTypeMismatchError(
            f"File appears to be an image, but was declared as '{declared}'."
        )

    # --- Excel ---
    if declared in EXCEL_TYPES:
        if not _is_valid_xlsx(file_path):
            raise FileTypeMismatchError(
                f"File does not appear to be a valid Excel workbook, but was declared as '{declared}'."
            )
        return

    # --- Parquet ---
    if declared in PARQUET_TYPES:
        if not _is_valid_parquet(file_path):
            raise FileTypeMismatchError(
                f"File does not appear to be a valid Parquet file, but was declared as '{declared}'."
            )
        return

    # --- JSON / JSONL ---
    if declared in JSON_TYPES:
        with open(file_path, "rb") as f:
            raw = f.read()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise FileTypeMismatchError(
                f"File does not appear to be valid UTF-8 text, but was declared as '{declared}'."
            )

        if declared == "json":
            try:
                json.loads(text)
            except json.JSONDecodeError:
                raise FileTypeMismatchError(
                    "File does not appear to be valid JSON, but was declared as 'json'."
                )
        else:  # jsonl — one JSON object per line
            lines = [line for line in text.splitlines() if line.strip()]
            if not lines:
                raise FileTypeMismatchError(
                    "File is empty or has no valid lines, but was declared as 'jsonl'."
                )
            try:
                for line in lines[:50]:  # sample first 50 lines, avoid huge-file slowdowns
                    json.loads(line)
            except json.JSONDecodeError:
                raise FileTypeMismatchError(
                    "File does not appear to be valid JSONL (newline-delimited JSON), "
                    "but was declared as 'jsonl'."
                )
        return

    # --- CSV / TSV ---
    if declared in CSV_TYPES:
        with open(file_path, "rb") as f:
            raw_sample = f.read(8192)
        try:
            sample = raw_sample.decode("utf-8")
        except UnicodeDecodeError:
            raise FileTypeMismatchError(
                f"File does not appear to be valid UTF-8 text, but was declared as '{declared}'."
            )
        try:
            csv.Sniffer().sniff(sample, delimiters=",\t;")
        except csv.Error:
            raise FileTypeMismatchError(
                f"File does not appear to be delimited text, but was declared as '{declared}'."
            )
        return

    # --- Unknown declared type ---
    raise FileTypeMismatchError(
        f"'{declared}' is not a supported file type on this platform."
    )