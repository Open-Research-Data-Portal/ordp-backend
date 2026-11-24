import os


def check_not_empty(file_path: str) -> dict:
    """Check 3: not_empty.

    Verify file size is > 0 and bytes can be read.
    """
    try:
        size = os.path.getsize(file_path)
        if size == 0:
            return {
                "name": "not_empty",
                "result": "fail",
                "detail": "File is empty (0 bytes)",
            }

        with open(file_path, "rb") as f:
            first_byte = f.read(1)
            if not first_byte:
                return {
                    "name": "not_empty",
                    "result": "fail",
                    "detail": "File returned 0 bytes on read",
                }

        return {
            "name": "not_empty",
            "result": "pass",
            "detail": f"File is not empty ({size} bytes)",
        }
    except Exception as exc:
        return {
            "name": "not_empty",
            "result": "fail",
            "detail": f"Failed reading file: {exc}",
        }
