import hashlib


def check_checksum(file_path: str, expected_checksum: str) -> dict:
    """Check 2: checksum_verified.

    Recompute SHA-256 and compare with the StoredObject checksum.
    """
    if not expected_checksum:
        return {
            "name": "checksum_verified",
            "result": "fail",
            "detail": "Expected checksum not provided",
        }

    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            sha256.update(chunk)

    actual = sha256.hexdigest().lower()
    expected = expected_checksum.strip().lower()

    if actual == expected:
        return {
            "name": "checksum_verified",
            "result": "pass",
            "detail": f"SHA-256 verified ({actual})",
        }
    return {
        "name": "checksum_verified",
        "result": "fail",
        "detail": f"SHA-256 mismatch: expected {expected}, got {actual}",
    }
