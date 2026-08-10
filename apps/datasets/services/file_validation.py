import csv
import imghdr
import json

CSV_TYPES = {"csv", "tsv"}
JSON_TYPES = {"json"}
IMAGE_TYPES = {"jpg", "jpeg", "png", "gif", "bmp", "webp"}


class FileTypeMismatchError(Exception):
    pass


def validate_file_matches_declared_type(file_path, declared_file_type):
    """Sniffs the actual bytes and confirms they're plausible for the declared type.
    Not a full-format validator — just enough to catch the classic 'said CSV,
    uploaded a JPEG' mismatch your spec called out."""
    declared = declared_file_type.lower().strip()

    if declared in IMAGE_TYPES:
        detected = imghdr.what(file_path)
        if detected is None:
            raise FileTypeMismatchError(f"File does not appear to be a valid image, but was declared as '{declared}'.")
        return

    if imghdr.what(file_path) is not None:
        raise FileTypeMismatchError(f"File appears to be an image, but was declared as '{declared}'.")

    if declared in JSON_TYPES:
        with open(file_path, "r", encoding="utf-8", errors="strict") as f:
            try:
                json.load(f)
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise FileTypeMismatchError("File does not appear to be valid JSON, but was declared as 'json'.")
        return

    if declared in CSV_TYPES:
        with open(file_path, "r", encoding="utf-8", errors="strict") as f:
            sample = f.read(8192)
        try:
            csv.Sniffer().sniff(sample, delimiters=",\t;")
        except csv.Error:
            raise FileTypeMismatchError(f"File does not appear to be delimited text, but was declared as '{declared}'.")
        return

