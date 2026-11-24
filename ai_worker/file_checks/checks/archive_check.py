import os
import tarfile
import zipfile
from config import config


def check_archive_limits(file_path: str) -> dict:
    """Check 7: archive_limits.

    Inspect zip or tar files WITHOUT extracting:
    - Member count <= AI_MAX_ARCHIVE_FILES
    - Total uncompressed size <= AI_MAX_ARCHIVE_UNCOMPRESSED_GB
    - Compression ratio <= AI_MAX_COMPRESSION_RATIO
    - Nesting depth <= AI_MAX_NESTING
    """
    max_files = config.AI_MAX_ARCHIVE_FILES
    max_uncompressed_bytes = int(config.AI_MAX_ARCHIVE_UNCOMPRESSED_GB * (1024 ** 3))
    max_ratio = config.AI_MAX_COMPRESSION_RATIO
    max_nesting = config.AI_MAX_NESTING

    compressed_size = os.path.getsize(file_path)

    if zipfile.is_zipfile(file_path):
        try:
            with zipfile.ZipFile(file_path, "r") as zf:
                infos = zf.infolist()
                file_count = len(infos)
                if file_count > max_files:
                    return {
                        "name": "archive_limits",
                        "result": "fail",
                        "detail": f"Archive contains {file_count} files, exceeding limit of {max_files}",
                    }

                total_uncompressed = 0
                max_depth = 0

                for info in infos:
                    total_uncompressed += info.file_size
                    # Normalize separators and compute depth
                    norm_name = info.filename.replace("\\", "/").strip("/")
                    parts = [p for p in norm_name.split("/") if p]
                    depth = max(0, len(parts) - 1)
                    if depth > max_depth:
                        max_depth = depth

                if total_uncompressed > max_uncompressed_bytes:
                    return {
                        "name": "archive_limits",
                        "result": "fail",
                        "detail": f"Archive uncompressed size ({total_uncompressed} bytes) exceeds limit ({max_uncompressed_bytes} bytes)",
                    }

                ratio = total_uncompressed / max(compressed_size, 1)
                if ratio > max_ratio:
                    return {
                        "name": "archive_limits",
                        "result": "fail",
                        "detail": f"Suspicious compression ratio ({ratio:.1f}x) exceeds limit ({max_ratio}x)",
                    }

                if max_depth > max_nesting:
                    return {
                        "name": "archive_limits",
                        "result": "fail",
                        "detail": f"Archive folder nesting depth ({max_depth}) exceeds limit ({max_nesting})",
                    }

            return {
                "name": "archive_limits",
                "result": "pass",
                "detail": f"Archive limits respected ({file_count} files, ratio {ratio:.1f}x, depth {max_depth})",
            }
        except Exception as exc:
            return {
                "name": "archive_limits",
                "result": "fail",
                "detail": f"Error inspecting ZIP archive limits: {exc}",
            }

    if tarfile.is_tarfile(file_path):
        try:
            with tarfile.open(file_path, "r") as tf:
                members = tf.getmembers()
                file_count = len(members)
                if file_count > max_files:
                    return {
                        "name": "archive_limits",
                        "result": "fail",
                        "detail": f"Archive contains {file_count} files, exceeding limit of {max_files}",
                    }

                total_uncompressed = sum(m.size for m in members)
                if total_uncompressed > max_uncompressed_bytes:
                    return {
                        "name": "archive_limits",
                        "result": "fail",
                        "detail": f"Tar uncompressed size exceeds limit",
                    }

                ratio = total_uncompressed / max(compressed_size, 1)
                if ratio > max_ratio:
                    return {
                        "name": "archive_limits",
                        "result": "fail",
                        "detail": f"Suspicious compression ratio ({ratio:.1f}x) exceeds limit ({max_ratio}x)",
                    }

                max_depth = 0
                for m in members:
                    parts = [p for p in m.name.replace("\\", "/").strip("/").split("/") if p]
                    depth = max(0, len(parts) - 1)
                    if depth > max_depth:
                        max_depth = depth

                if max_depth > max_nesting:
                    return {
                        "name": "archive_limits",
                        "result": "fail",
                        "detail": f"Archive folder nesting depth ({max_depth}) exceeds limit ({max_nesting})",
                    }

            return {
                "name": "archive_limits",
                "result": "pass",
                "detail": f"Archive limits respected ({file_count} files, ratio {ratio:.1f}x)",
            }
        except Exception as exc:
            return {
                "name": "archive_limits",
                "result": "fail",
                "detail": f"Error inspecting tar archive limits: {exc}",
            }

    return {
        "name": "archive_limits",
        "result": "skipped",
        "detail": "Not a zip or tar archive",
    }
