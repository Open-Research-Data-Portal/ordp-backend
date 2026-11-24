import zipfile


def check_password_protected(file_path: str, detected_type: str) -> dict:
    """Check 5: password_protected.

    Check if PDF, ZIP, or Office files are password protected or encrypted.
    """
    clean_type = (detected_type or "").lower()

    if clean_type == "pdf":
        try:
            import pypdf
            reader = pypdf.PdfReader(file_path)
            if reader.is_encrypted:
                return {
                    "name": "password_protected",
                    "result": "fail",
                    "detail": "PDF file is encrypted or password-protected",
                }
            return {
                "name": "password_protected",
                "result": "pass",
                "detail": "PDF is not password protected",
            }
        except Exception as exc:
            # If reading failed because encrypted
            if "encrypted" in str(exc).lower() or "password" in str(exc).lower():
                return {
                    "name": "password_protected",
                    "result": "fail",
                    "detail": f"PDF is encrypted: {exc}",
                }
            return {
                "name": "password_protected",
                "result": "skipped",
                "detail": f"Could not inspect PDF encryption: {exc}",
            }

    if clean_type == "zip" or zipfile.is_zipfile(file_path):
        try:
            with zipfile.ZipFile(file_path, "r") as zf:
                for info in zf.infolist():
                    # Flag 0x1 indicates an encrypted entry
                    if info.flag_bits & 0x1:
                        return {
                            "name": "password_protected",
                            "result": "fail",
                            "detail": f"ZIP archive contains encrypted entry: {info.filename}",
                        }
            return {
                "name": "password_protected",
                "result": "pass",
                "detail": "ZIP archive is not password-protected",
            }
        except Exception as exc:
            if "encrypted" in str(exc).lower() or "password" in str(exc).lower():
                return {
                    "name": "password_protected",
                    "result": "fail",
                    "detail": f"ZIP is password-protected: {exc}",
                }
            return {
                "name": "password_protected",
                "result": "skipped",
                "detail": f"Could not check ZIP encryption: {exc}",
            }

    # Office files (docx/xlsx) are zip files under the hood or OLE compound files
    with open(file_path, "rb") as f:
        head = f.read(512)
        # OLE compound file header (EncryptedPackage)
        if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1") and b"EncryptedPackage" in head:
            return {
                "name": "password_protected",
                "result": "fail",
                "detail": "Office document is password encrypted (EncryptedPackage)",
            }

    return {
        "name": "password_protected",
        "result": "pass",
        "detail": "No password protection detected",
    }
