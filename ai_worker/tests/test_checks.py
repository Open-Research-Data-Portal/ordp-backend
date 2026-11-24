import hashlib
import io
import os
import sys
import tempfile
import unittest
import zipfile

# Ensure parent directory is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from file_checks.runner import run_checks
from file_checks.scanner import DisabledScanner
from file_checks.levels import get_level_for_type


class WorkerFileChecksTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_temp_file(self, filename: str, content: bytes) -> str:
        path = os.path.join(self.temp_dir.name, filename)
        with open(path, "wb") as f:
            f.write(content)
        return path

    def test_level_mapping(self):
        """Verify level mapping matches specification."""
        self.assertEqual(get_level_for_type("csv"), "A")
        self.assertEqual(get_level_for_type("xlsx"), "A")
        self.assertEqual(get_level_for_type("parquet"), "A")
        self.assertEqual(get_level_for_type("pdf"), "B1")
        self.assertEqual(get_level_for_type("docx"), "B1")
        self.assertEqual(get_level_for_type("txt"), "B1")
        self.assertEqual(get_level_for_type("png"), "B2")
        self.assertEqual(get_level_for_type("mp4"), "B2")
        self.assertEqual(get_level_for_type("unknown_bin"), "C")

    def test_empty_file_fails(self):
        """Empty file fails not_empty check and marks status failed."""
        path = self._create_temp_file("empty.csv", b"")
        checksum = hashlib.sha256(b"").hexdigest()
        job = {"filename": "empty.csv", "checksum": checksum}

        res = run_checks(path, job, scanner=DisabledScanner())
        self.assertEqual(res["status"], "failed")
        not_empty_check = next(c for c in res["checks"] if c["name"] == "not_empty")
        self.assertEqual(not_empty_check["result"], "fail")

    def test_extension_mismatch_warns(self):
        """Content/extension mismatch flags warning without failing."""
        # Content is PDF (%PDF...) but filename is .csv
        content = b"%PDF-1.4 sample pdf content"
        path = self._create_temp_file("data.csv", content)
        checksum = hashlib.sha256(content).hexdigest()
        job = {"filename": "data.csv", "checksum": checksum}

        res = run_checks(path, job, scanner=DisabledScanner())
        ext_check = next(c for c in res["checks"] if c["name"] == "extension_matches")
        self.assertEqual(ext_check["result"], "warn")
        self.assertFalse(res["extension_matches"])
        self.assertTrue(len(res["warnings"]) > 0)

    def test_corrupt_xlsx_fails_with_cannot_read(self):
        """Corrupt xlsx keeps level A, sets status failed, error code CANNOT_READ."""
        # Writing garbage into an xlsx file
        path = self._create_temp_file("corrupt.xlsx", b"not a valid zip or excel structure")
        checksum = hashlib.sha256(b"not a valid zip or excel structure").hexdigest()
        job = {"filename": "corrupt.xlsx", "checksum": checksum}

        res = run_checks(path, job, scanner=DisabledScanner())
        self.assertEqual(res["status"], "failed")
        self.assertEqual(res["level"], "A")
        self.assertIsNotNone(res["error"])
        self.assertEqual(res["error"]["code"], "CANNOT_READ")

    def test_password_protected_zip_fails(self):
        """Password protected ZIP is detected and check fails."""
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zf:
            zf.writestr("secret.txt", b"classified data")

        # Set bit 0 of flag_bits (encryption bit) in local and central directory headers
        raw = bytearray(zip_buffer.getvalue())
        raw[6] |= 0x1
        central_idx = raw.find(b"PK\x01\x02")
        if central_idx != -1:
            raw[central_idx + 8] |= 0x1

        path = self._create_temp_file("locked.zip", bytes(raw))
        checksum = hashlib.sha256(bytes(raw)).hexdigest()
        job = {"filename": "locked.zip", "checksum": checksum}

        res = run_checks(path, job, scanner=DisabledScanner())
        pass_check = next(c for c in res["checks"] if c["name"] == "password_protected")
        self.assertEqual(pass_check["result"], "fail")
        self.assertEqual(res["status"], "failed")

    def test_password_protected_pdf_fails(self):
        """Password protected PDF is detected as failed."""
        try:
            import pypdf
            writer = pypdf.PdfWriter()
            writer.add_blank_page(width=100, height=100)
            writer.encrypt("user_pass", "owner_pass")
            pdf_bytes = io.BytesIO()
            writer.write(pdf_bytes)
            content = pdf_bytes.getvalue()
        except ImportError:
            content = b"%PDF-1.4 /Encrypt << /V 2 /R 3 >>"

        path = self._create_temp_file("protected.pdf", content)
        checksum = hashlib.sha256(content).hexdigest()
        job = {"filename": "protected.pdf", "checksum": checksum}

        res = run_checks(path, job, scanner=DisabledScanner())
        pass_check = next(c for c in res["checks"] if c["name"] == "password_protected")
        self.assertEqual(pass_check["result"], "fail")
        self.assertEqual(res["status"], "failed")

    def test_high_ratio_archive_triggers_limits_and_partial(self):
        """Zip bomb / high compression ratio triggers archive limit fail and partial status."""
        zip_buffer = io.BytesIO()
        # 1MB of null bytes compresses to ~1KB (ratio ~1000x > 100 limit)
        large_zeros = b"\x00" * (1024 * 1024)
        with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("huge.txt", large_zeros)

        data = zip_buffer.getvalue()
        path = self._create_temp_file("bomb.zip", data)
        checksum = hashlib.sha256(data).hexdigest()
        job = {"filename": "bomb.zip", "checksum": checksum}

        res = run_checks(path, job, scanner=DisabledScanner())
        archive_check = next(c for c in res["checks"] if c["name"] == "archive_limits")
        self.assertEqual(archive_check["result"], "fail")
        self.assertEqual(res["coverage"], "partially_analysed")

    def test_macro_enabled_xlsm_warns(self):
        """Macro-enabled file (xlsm or vbaProject.bin) triggers macro warning."""
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zf:
            zf.writestr("xl/vbaProject.bin", b"VBA macro fake binary")
            zf.writestr("[Content_Types].xml", b"<Types></Types>")

        data = zip_buffer.getvalue()
        path = self._create_temp_file("workbook.xlsm", data)
        checksum = hashlib.sha256(data).hexdigest()
        job = {"filename": "workbook.xlsm", "checksum": checksum}

        res = run_checks(path, job, scanner=DisabledScanner())
        macro_check = next(c for c in res["checks"] if c["name"] == "macros")
        self.assertEqual(macro_check["result"], "warn")
        self.assertTrue(any("macro" in w.lower() for w in res["warnings"]))

    def test_scanner_disabled_returns_skipped(self):
        """Disabled scanner reports skipped and does not block pass status."""
        csv_content = b"col1,col2\nval1,val2\n"
        path = self._create_temp_file("valid.csv", csv_content)
        checksum = hashlib.sha256(csv_content).hexdigest()
        job = {"filename": "valid.csv", "checksum": checksum}

        res = run_checks(path, job, scanner=DisabledScanner())
        malware_check = next(c for c in res["checks"] if c["name"] == "malware_scan")
        self.assertEqual(malware_check["result"], "skipped")
        self.assertEqual(res["status"], "done")
        self.assertEqual(res["coverage"], "fully_analysed")


if __name__ == "__main__":
    unittest.main()
