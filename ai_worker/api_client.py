import logging
import requests
from config import config

logger = logging.getLogger(__name__)


class APIClient:
    def __init__(self, base_url: str = None, token: str = None, worker_id: str = None):
        self.base_url = (base_url or config.AI_BACKEND_URL).rstrip("/")
        self.token = token or config.ORDP_WORKER_TOKEN
        self.worker_id = worker_id or config.AI_WORKER_ID
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "X-Worker-Id": self.worker_id,
        })

    def get_next_job(self):
        """Fetch the next queued job from the backend. Returns dict or None."""
        url = f"{self.base_url}/internal/ai-jobs/next/"
        try:
            resp = self.session.get(url, timeout=10)
            if resp.status_code == 204:
                return None
            if resp.status_code == 200:
                return resp.json()
            logger.warning("Unexpected status from next job endpoint: %d %s", resp.status_code, resp.text)
            return None
        except requests.RequestException as exc:
            logger.error("Failed to connect to backend for next job: %s", exc)
            return None

    def post_job_result(self, job_id: str, result_data: dict) -> bool:
        """Post the analysis result to the backend."""
        url = f"{self.base_url}/internal/ai-jobs/{job_id}/result/"
        try:
            resp = self.session.post(url, json=result_data, timeout=30)
            if resp.status_code == 200:
                return True
            logger.error("Failed to post result for job %s: %d %s", job_id, resp.status_code, resp.text)
            return False
        except requests.RequestException as exc:
            logger.error("Network error posting result for job %s: %s", job_id, exc)
            return False

    def download_file(self, file_url: str, dest_path: str) -> bool:
        """Stream download file directly to local destination on disk."""
        try:
            with requests.get(file_url, stream=True, timeout=120) as resp:
                resp.raise_for_status()
                with open(dest_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=65536):
                        if chunk:
                            f.write(chunk)
            return True
        except requests.RequestException as exc:
            logger.error("Failed to download file from %s: %s", file_url, exc)
            raise
