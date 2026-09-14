"""Dataset-detail data preview — first release covers tabular (CSV/JSON) only.
Image/video preview can be added the same way later."""
import csv
import io
import json
import logging

from .storage import read_object_range

logger = logging.getLogger(__name__)

TABULAR_PREVIEW_ROW_LIMIT = 15
TABULAR_PREVIEW_BYTE_BUDGET = 131072

CSV_FILE_TYPES = {"csv", "tsv"}
JSON_FILE_TYPES = {"json", "jsonl"}


def _delimiter_for(file_type):
    return "\t" if file_type == "tsv" else ","


def preview_tabular_file(dataset_file, row_limit=TABULAR_PREVIEW_ROW_LIMIT):
    file_type = (dataset_file.file_type or "").lower()
    try:
        raw = read_object_range(dataset_file.file_key, max_bytes=TABULAR_PREVIEW_BYTE_BUDGET)
        text = raw.decode("utf-8", errors="replace")
    except Exception:
        logger.exception("Failed to read preview bytes for DatasetFile %s", dataset_file.id)
        return {"available": False, "reason": "Preview is temporarily unavailable."}

    if file_type in CSV_FILE_TYPES:
        return _preview_csv(text, _delimiter_for(file_type), row_limit)
    if file_type in JSON_FILE_TYPES:
        return _preview_json(text, file_type, row_limit)

    return {"available": False, "reason": f"No preview available for '{file_type}' files yet."}


def _preview_csv(text, delimiter, row_limit):
    lines = text.splitlines()
    if len(lines) > 1:
        lines = lines[:-1]  # byte-range read may cut the last line mid-row

    rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter))
    if not rows:
        return {"available": False, "reason": "File appears to be empty."}

    header, data_rows = rows[0], rows[1:row_limit + 1]
    return {
        "available": True, "format": "tabular", "columns": header,
        "rows": data_rows, "row_limit": row_limit, "truncated": True,
    }


def _preview_json(text, file_type, row_limit):
    if file_type == "jsonl":
        lines = [line for line in text.splitlines() if line.strip()]
        if len(lines) > 1:
            lines = lines[:-1]

        records = []
        for line in lines[:row_limit]:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                break

        if not records:
            return {"available": False, "reason": "File appears to be empty or unreadable."}

        columns = sorted({key for record in records if isinstance(record, dict) for key in record})
        return {
            "available": True, "format": "tabular", "columns": columns,
            "rows": records, "row_limit": row_limit, "truncated": True,
        }

    try:
        data = json.loads(text)
        if isinstance(data, list):
            rows = data[:row_limit]
        else:
            return {"available": True, "format": "object", "preview": data, "truncated": False}
    except json.JSONDecodeError:
        rows = _best_effort_json_array_prefix(text, row_limit)
        if rows is None:
            return {"available": False, "reason": "Preview is temporarily unavailable."}

    columns = sorted({key for record in rows if isinstance(record, dict) for key in record})
    return {
        "available": True, "format": "tabular", "columns": columns,
        "rows": rows, "row_limit": row_limit, "truncated": True,
    }


def _best_effort_json_array_prefix(text, row_limit):
    text = text.lstrip()
    if not text.startswith("["):
        return None

    decoder = json.JSONDecoder()
    idx, n, items = 1, len(text), []
    while idx < n and len(items) < row_limit:
        while idx < n and text[idx] in " \t\r\n,":
            idx += 1
        if idx >= n or text[idx] == "]":
            break
        try:
            obj, end = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            break
        items.append(obj)
        idx = end
    return items or None