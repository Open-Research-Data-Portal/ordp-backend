import os
import signal
import sys
import tempfile
import time
import logging

from config import config
from api_client import APIClient
from file_checks.runner import run_checks

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [ai-worker] %(message)s",
)
logger = logging.getLogger("ai_worker")

running = True


def handle_shutdown(signum, frame):
    global running
    logger.info("Received termination signal %s. Shutting down gracefully...", signum)
    running = False


signal.signal(signal.SIGINT, handle_shutdown)
signal.signal(signal.SIGTERM, handle_shutdown)


def main():
    logger.info("Starting ORDP AI Worker (id: %s)...", config.AI_WORKER_ID)
    client = APIClient()

    consecutive_errors = 0

    while running:
        try:
            job = client.get_next_job()
            if not job:
                consecutive_errors = 0
                time.sleep(config.AI_POLL_INTERVAL_SECONDS)
                continue

            consecutive_errors = 0
            job_id = job.get("id")
            filename = job.get("filename", "unknown")
            file_url = job.get("file_url")
            logger.info("Claimed job %s for file '%s'", job_id, filename)

            # Stream download to isolated temporary file on disk
            fd, temp_path = tempfile.mkstemp(prefix=f"ordp_ai_{job_id[:8]}_")
            os.close(fd)

            try:
                logger.info("Downloading file from storage to %s...", temp_path)
                client.download_file(file_url, temp_path)

                logger.info("Running inspection checks on '%s'...", filename)
                result = run_checks(temp_path, job)

                logger.info(
                    "Inspection complete for job %s: status=%s, level=%s, coverage=%s. Posting result...",
                    job_id,
                    result["status"],
                    result["level"],
                    result["coverage"],
                )
                success = client.post_job_result(job_id, result)
                if success:
                    logger.info("Successfully posted result for job %s", job_id)
                else:
                    logger.error("Failed to post result for job %s", job_id)

            finally:
                # Always remove temporary file
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except OSError as exc:
                        logger.warning("Could not delete temp file %s: %s", temp_path, exc)

        except Exception as exc:
            consecutive_errors += 1
            backoff = min(2 ** min(consecutive_errors, 5), 30)
            logger.error("Error in worker loop: %s. Backing off %d seconds...", exc, backoff)
            time.sleep(backoff)

    logger.info("AI Worker terminated cleanly.")


if __name__ == "__main__":
    main()
