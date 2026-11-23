from abc import ABC, abstractmethod
import logging
from config import config

logger = logging.getLogger(__name__)


class MalwareScanner(ABC):
    @abstractmethod
    def scan(self, file_path: str) -> dict:
        """Scan file for malware.

        Returns { "name": "malware_scan", "result": "pass"|"warn"|"fail"|"skipped", "detail": str }
        """
        pass


class DisabledScanner(MalwareScanner):
    def scan(self, file_path: str) -> dict:
        return {
            "name": "malware_scan",
            "result": "skipped",
            "detail": "Malware scan is disabled",
        }


class ClamAVScanner(MalwareScanner):
    def __init__(self, host: str = None, port: int = None):
        self.host = host or config.CLAMAV_HOST
        self.port = port or config.CLAMAV_PORT

    def scan(self, file_path: str) -> dict:
        if not self.host:
            return {
                "name": "malware_scan",
                "result": "skipped",
                "detail": "ClamAV host not configured",
            }

        try:
            import pyclamd
            cd = pyclamd.ClamdNetworkSocket(self.host, self.port, timeout=15)
            if not cd.ping():
                return {
                    "name": "malware_scan",
                    "result": "skipped",
                    "detail": f"ClamAV clamd daemon at {self.host}:{self.port} not responding",
                }
            scan_res = cd.scan_file(file_path)
            if scan_res is None:
                return {
                    "name": "malware_scan",
                    "result": "pass",
                    "detail": "No malware detected by ClamAV",
                }
            virus_name = scan_res.get(file_path, ["Infection detected"])[1]
            return {
                "name": "malware_scan",
                "result": "fail",
                "detail": f"Malware detected: {virus_name}",
            }
        except Exception as exc:
            logger.warning("ClamAV scan error: %s", exc)
            return {
                "name": "malware_scan",
                "result": "skipped",
                "detail": f"ClamAV scan skipped due to connection error: {exc}",
            }


def get_default_scanner() -> MalwareScanner:
    if config.CLAMAV_ENABLED and config.CLAMAV_HOST:
        return ClamAVScanner()
    return DisabledScanner()
