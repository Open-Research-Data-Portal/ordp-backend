import os
import mimetypes

try:
    import magic
except ImportError:
    magic = None


MIME_TO_CANONICAL = {
    "text/csv": "csv",
    "text/comma-separated-values": "csv",
    "text/tab-separated-values": "tsv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.ms-excel": "xls",
    "application/vnd.ms-excel.sheet.macroEnabled.12": "xlsm",
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.ms-word.document.macroEnabled.12": "docm",
    "application/msword": "doc",
    "application/zip": "zip",
    "application/x-zip-compressed": "zip",
    "application/x-tar": "tar",
    "application/gzip": "tar",
    "application/x-parquet": "parquet",
    "application/octet-stream": "binary",
    "application/json": "json",
    "text/plain": "txt",
    "text/markdown": "md",
    "text/x-markdown": "md",
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/tiff": "tiff",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/svg+xml": "svg",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/flac": "flac",
    "audio/ogg": "ogg",
    "video/mp4": "mp4",
    "video/x-matroska": "mkv",
    "video/x-msvideo": "avi",
    "video/quicktime": "mov",
    "application/x-netcdf": "netcdf",
    "application/x-hdf": "hdf5",
    "application/dicom": "dicom",
}


def detect_file_type_from_content(file_path: str, filename: str = "") -> tuple[str, str]:
    """Detect file type from content using python-magic.

    Returns (detected_type, mime_type).
    """
    mime = "application/octet-stream"
    if magic is not None:
        try:
            mime = magic.from_file(file_path, mime=True)
        except Exception:
            pass

    # Header-based fallback heuristics if magic gives octet-stream or text/plain
    with open(file_path, "rb") as f:
        head = f.read(4096)

    canonical = MIME_TO_CANONICAL.get(mime)

    if not canonical or canonical in ("binary", "txt"):
        if head.startswith(b"%PDF"):
            canonical = "pdf"
            mime = "application/pdf"
        elif head.startswith(b"PK\x03\x04"):
            canonical = "zip"
            mime = "application/zip"
        elif head.startswith(b"PAR1"):
            canonical = "parquet"
            mime = "application/x-parquet"
        elif head.startswith(b"\x89PNG\r\n\x1a\n"):
            canonical = "png"
            mime = "image/png"
        elif head.startswith(b"\xff\xd8\xff"):
            canonical = "jpeg"
            mime = "image/jpeg"
        elif head.startswith(b"II*\x00") or head.startswith(b"MM\x00*"):
            canonical = "tiff"
            mime = "image/tiff"
        elif head.startswith(b"\x1f\x8b"):
            canonical = "tar"
            mime = "application/gzip"
        elif head.startswith(b"RIFF") and b"WAVE" in head[:12]:
            canonical = "wav"
            mime = "audio/wav"
        elif head.startswith(b"\x89HDF\r\n\x1a\n"):
            canonical = "hdf5"
            mime = "application/x-hdf"

    if not canonical:
        # Fall back to extension guess if unrecognized
        ext = os.path.splitext(filename)[1].lstrip(".").lower()
        canonical = ext or "unknown"

    return canonical, mime


def check_extension_matches(detected_type: str, filename: str) -> bool:
    """Check if the detected type matches the filename extension."""
    if not filename:
        return True
    ext = os.path.splitext(filename)[1].lstrip(".").lower()
    if not ext:
        return True

    # Normalize aliases
    aliases = {
        "jpg": "jpeg",
        "jpeg": "jpeg",
        "tif": "tiff",
        "tiff": "tiff",
        "tsv": "tsv",
        "csv": "csv",
        "xlsx": "xlsx",
        "xlsm": "xlsx",
        "docx": "docx",
        "docm": "docx",
        "tar.gz": "tar",
        "tgz": "tar",
        "tar": "tar",
        "gz": "tar",
        "txt": "txt",
        "md": "md",
        "json": "json",
        "parquet": "parquet",
        "pdf": "pdf",
        "zip": "zip",
    }

    norm_ext = aliases.get(ext, ext)
    norm_detected = aliases.get(detected_type, detected_type)

    if norm_ext == norm_detected:
        return True

    # Text variants match txt
    if norm_detected == "txt" and norm_ext in ("csv", "tsv", "json", "md", "txt"):
        return True

    return False
