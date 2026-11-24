import os
import csv
import json
import zipfile


def check_opens_correctly(file_path: str, detected_type: str, filename: str = "") -> dict:
    """Check 4: opens_correctly.

    Try to open supported file types (csv, xlsx, pdf, docx, zip, json) using standard libraries.
    Inspects against detected type or filename extension if content type was ambiguous.
    """
    clean_detected = (detected_type or "").lower()
    ext = os.path.splitext(filename)[1].lstrip(".").lower() if filename else ""

    # Prefer detected type unless ambiguous, then check extension
    target_type = clean_detected
    if target_type in ("unknown", "txt", "binary", "") and ext in ("xlsx", "xlsm", "xls", "pdf", "docx", "docm", "zip", "csv", "tsv", "json", "parquet"):
        target_type = ext

    try:
        if target_type in ("csv", "tsv"):
            delimiter = "\t" if target_type == "tsv" else ","
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                reader = csv.reader(f, delimiter=delimiter)
                for i, row in enumerate(reader):
                    if i >= 10:
                        break
            return {
                "name": "opens_correctly",
                "result": "pass",
                "detail": f"Successfully parsed {target_type.upper()} format",
            }

        elif target_type in ("xlsx", "xlsm", "xls"):
            try:
                import openpyxl
                wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
                _ = wb.sheetnames
                wb.close()
                return {
                    "name": "opens_correctly",
                    "result": "pass",
                    "detail": "Successfully opened Excel workbook",
                }
            except Exception as exc:
                return {
                    "name": "opens_correctly",
                    "result": "fail",
                    "detail": f"Corrupt or invalid Excel file: {exc}",
                }

        elif target_type == "pdf":
            try:
                import pypdf
                reader = pypdf.PdfReader(file_path)
                _ = len(reader.pages)
                return {
                    "name": "opens_correctly",
                    "result": "pass",
                    "detail": f"Successfully opened PDF ({len(reader.pages)} pages)",
                }
            except Exception as exc:
                return {
                    "name": "opens_correctly",
                    "result": "fail",
                    "detail": f"Corrupt or invalid PDF: {exc}",
                }

        elif target_type in ("docx", "docm"):
            try:
                import docx
                doc = docx.Document(file_path)
                _ = len(doc.paragraphs)
                return {
                    "name": "opens_correctly",
                    "result": "pass",
                    "detail": "Successfully opened Word document",
                }
            except Exception as exc:
                return {
                    "name": "opens_correctly",
                    "result": "fail",
                    "detail": f"Corrupt or invalid Word document: {exc}",
                }

        elif target_type == "zip":
            try:
                with zipfile.ZipFile(file_path, "r") as zf:
                    corrupt = zf.testzip()
                    if corrupt is not None:
                        return {
                            "name": "opens_correctly",
                            "result": "fail",
                            "detail": f"Corrupt entry in ZIP: {corrupt}",
                        }
                return {
                    "name": "opens_correctly",
                    "result": "pass",
                    "detail": "Successfully validated ZIP archive",
                }
            except Exception as exc:
                return {
                    "name": "opens_correctly",
                    "result": "fail",
                    "detail": f"Corrupt or invalid ZIP archive: {exc}",
                }

        elif target_type in ("json", "json-table", "json_table"):
            try:
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    json.load(f)
                return {
                    "name": "opens_correctly",
                    "result": "pass",
                    "detail": "Successfully parsed JSON",
                }
            except Exception as exc:
                return {
                    "name": "opens_correctly",
                    "result": "fail",
                    "detail": f"Corrupt JSON format: {exc}",
                }

        # Other types without specialized parsers
        return {
            "name": "opens_correctly",
            "result": "pass",
            "detail": f"File of type '{target_type}' is accessible",
        }

    except Exception as exc:
        return {
            "name": "opens_correctly",
            "result": "fail",
            "detail": f"Failed opening file: {exc}",
        }
