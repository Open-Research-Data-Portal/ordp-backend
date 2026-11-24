import os
import time
import logging

from config import config
from .levels import get_level_for_type
from .scanner import get_default_scanner
from .checks.type_check import check_type_and_extension
from .checks.checksum_check import check_checksum
from .checks.not_empty_check import check_not_empty
from .checks.opens_correctly_check import check_opens_correctly
from .checks.password_check import check_password_protected
from .checks.macro_check import check_macros
from .checks.archive_check import check_archive_limits
from .checks.malware_check import check_malware

logger = logging.getLogger(__name__)


def run_checks(file_path: str, job_data: dict, scanner=None) -> dict:
    """Run all 8 file checks sequentially within the configured time limit.

    Returns the formatted result dictionary to post back to the backend.
    """
    start_time = time.time()
    filename = job_data.get("filename", "uploaded_file")
    expected_checksum = job_data.get("checksum", "")
    max_seconds = config.AI_MAX_JOB_SECONDS

    checks = []
    warnings = []
    is_partial = False
    error = None

    # Scanner setup
    active_scanner = scanner or get_default_scanner()

    # 1. Type from contents & extension matches
    type_res, ext_res, detected_type = check_type_and_extension(file_path, filename)
    checks.append(type_res)
    checks.append(ext_res)
    if ext_res["result"] == "warn":
        warnings.append(ext_res["detail"])
    extension_matches = ext_res["result"] == "pass"

    # Level is determined by detected type
    level = get_level_for_type(detected_type)

    # 2. Checksum verified
    checksum_res = check_checksum(file_path, expected_checksum)
    checks.append(checksum_res)

    # 3. Not empty
    not_empty_res = check_not_empty(file_path)
    checks.append(not_empty_res)

    # Check timeout before deep inspection
    if time.time() - start_time > max_seconds:
        is_partial = True
        warnings.append(f"Analysis interrupted: exceeded job timeout of {max_seconds} seconds")

    # 4. Opens correctly
    opens_res = check_opens_correctly(file_path, detected_type, filename)
    checks.append(opens_res)

    # 5. Password protected
    pass_res = check_password_protected(file_path, detected_type)
    checks.append(pass_res)

    # 6. Macros
    macro_res = check_macros(file_path, filename)
    checks.append(macro_res)
    if macro_res["result"] == "warn":
        warnings.append(macro_res["detail"])

    # 7. Archive limits
    archive_res = check_archive_limits(file_path)
    checks.append(archive_res)
    if archive_res["result"] == "fail":
        is_partial = True
        warnings.append(archive_res["detail"])

    # 8. Malware scan
    malware_res = check_malware(file_path, active_scanner)
    checks.append(malware_res)
    if malware_res["result"] == "fail":
        warnings.append(malware_res["detail"])

    # Final status determination:
    # If file cannot be opened: keep level from its type, set status failed, error code CANNOT_READ
    if opens_res["result"] == "fail":
        final_status = "failed"
        ext = os.path.splitext(filename)[1].lstrip(".").lower() if filename else ""
        if detected_type in ("unknown", "txt", "binary", "") and ext:
            level = get_level_for_type(ext)
        error = {
            "code": "CANNOT_READ",
            "message": "File cannot be opened or parsed. Nothing was analysed.",
            "detail": opens_res["detail"],
        }
    elif not_empty_res["result"] == "fail" or checksum_res["result"] == "fail" or pass_res["result"] == "fail" or malware_res["result"] == "fail":
        final_status = "failed"
        fail_reasons = [c["detail"] for c in checks if c["result"] == "fail"]
        error = {
            "code": "CHECK_FAILED",
            "message": "; ".join(fail_reasons),
        }
    elif is_partial or archive_res["result"] == "fail":
        final_status = "partial"
    else:
        final_status = "done"

    coverage = "partially_analysed" if is_partial else "fully_analysed"

    actual_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0

    return {
        "status": final_status,
        "level": level,
        "detected_type": detected_type,
        "extension_matches": extension_matches,
        "coverage": coverage,
        "checks": checks,
        "warnings": warnings,
        "technical": {
            "size_bytes": actual_size,
            "sha256": expected_checksum,
        },
        "error": error,
    }
