import os
import zipfile


def check_macros(file_path: str, filename: str) -> dict:
    """Check 6: macros.

    Detect docm, xlsm, or vbaProject.bin in Office files.
    Flags as 'warn' when macros are present.
    """
    ext = os.path.splitext(filename)[1].lstrip(".").lower()

    if ext in ("docm", "xlsm"):
        return {
            "name": "macros",
            "result": "warn",
            "detail": f"Macro-enabled office document detected ({ext})",
        }

    # If it's a zip archive (such as docx/xlsx or plain zip), inspect member names
    if zipfile.is_zipfile(file_path):
        try:
            with zipfile.ZipFile(file_path, "r") as zf:
                for name in zf.namelist():
                    lower_name = name.lower()
                    if "vbaproject.bin" in lower_name or lower_name.endswith(".vba"):
                        return {
                            "name": "macros",
                            "result": "warn",
                            "detail": f"VBA macro project detected in archive: {name}",
                        }
        except Exception:
            pass

    return {
        "name": "macros",
        "result": "pass",
        "detail": "No macros detected",
    }
