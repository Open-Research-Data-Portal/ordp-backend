from ..detector import detect_file_type_from_content, check_extension_matches


def check_type_and_extension(file_path: str, filename: str) -> tuple[dict, dict, str]:
    """Check 1: type_from_contents and extension_matches.

    Returns (type_check_result, extension_check_result, detected_type).
    """
    detected_type, mime = detect_file_type_from_content(file_path, filename)

    type_check = {
        "name": "type_from_contents",
        "result": "pass",
        "detail": f"Detected {detected_type} ({mime})",
    }

    matches = check_extension_matches(detected_type, filename)
    if matches:
        extension_check = {
            "name": "extension_matches",
            "result": "pass",
            "detail": f"Content type '{detected_type}' matches filename '{filename}'",
        }
    else:
        extension_check = {
            "name": "extension_matches",
            "result": "warn",
            "detail": f"Content detected as '{detected_type}', does not match extension in '{filename}'",
        }

    return type_check, extension_check, detected_type
