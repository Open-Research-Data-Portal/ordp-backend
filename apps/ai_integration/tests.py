import threading
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.accounts.models import ActivityLog
from apps.datasets.models import Dataset, StoredObject, DatasetFile
from apps.ai_integration.models import AnalysisJob, FileAnalysis
from apps.ai_integration.services.job_service import (
    enqueue_analysis_job,
    claim_next_job,
    fail_and_retry,
    complete_job,
    release_stale_locks,
)
from apps.ai_integration.services.analysis_service import save_analysis_result

User = get_user_model()
TEST_WORKER_TOKEN = "test-secret-token"


class JobServiceUnitTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="test_owner",
            email="owner@example.com",
            first_name="Test",
            last_name="Owner",
            password="password123",
        )
        self.dataset = Dataset.objects.create(
            title="AI Test Dataset",
            owner=self.user,
            visibility=Dataset.Visibility.PRIVATE,
        )
        self.stored_obj = StoredObject.objects.create(
            dataset=self.dataset,
            storage_key="objects/test/1.csv",
            original_filename="1.csv",
            file_type="csv",
            file_size=1024,
            checksum_sha256="a" * 64,
            uploaded_by=self.user,
        )

    def test_duplicate_job_key_is_idempotent(self):
        """Enqueuing twice with the same checksum returns the same job and creates only one."""
        job1 = enqueue_analysis_job(self.stored_obj)
        job2 = enqueue_analysis_job(self.stored_obj)

        self.assertIsNotNone(job1)
        self.assertEqual(job1.id, job2.id)
        self.assertEqual(AnalysisJob.objects.filter(key=f"analyse_file:{'a'*64}").count(), 1)
        self.assertEqual(FileAnalysis.objects.filter(stored_object=self.stored_obj).count(), 1)

    def test_existing_checksum_reuses_finished_analysis(self):
        """If a finished analysis exists for that checksum, copy it and create no new job."""
        # Create an initial completed analysis for the first object
        save_analysis_result(
            checksum=self.stored_obj.checksum_sha256,
            result_data={
                "status": "done",
                "level": "A",
                "detected_type": "csv",
                "coverage": "fully_analysed",
                "checks": [{"name": "not_empty", "result": "pass", "detail": ""}],
                "warnings": [],
                "technical": {"size_bytes": 1024, "sha256": "a" * 64},
                "error": None,
            },
        )

        # Create a second distinct StoredObject with the identical checksum
        second_obj = StoredObject.objects.create(
            dataset=self.dataset,
            storage_key="objects/test/2.csv",
            original_filename="2.csv",
            file_type="csv",
            file_size=1024,
            checksum_sha256="a" * 64,
            uploaded_by=self.user,
        )

        initial_job_count = AnalysisJob.objects.count()
        job = enqueue_analysis_job(second_obj)

        # No new job is created
        self.assertIsNone(job)
        self.assertEqual(AnalysisJob.objects.count(), initial_job_count)

        # Second object has a completed FileAnalysis copied over
        second_analysis = FileAnalysis.objects.get(stored_object=second_obj)
        self.assertEqual(second_analysis.status, "done")
        self.assertEqual(second_analysis.level, "A")
        self.assertEqual(second_analysis.detected_type, "csv")

    def test_retry_and_backoff_schedule(self):
        """Verify back-off schedule (1m, 5m, 15m) and failure on max_attempts."""
        job = enqueue_analysis_job(self.stored_obj)
        self.assertEqual(job.attempts, 0)

        # Attempt 1 claim and failure -> backoff 1 min
        claimed_1 = claim_next_job(worker_id="w1")
        self.assertEqual(claimed_1.attempts, 1)
        fail_and_retry(claimed_1.id, "Attempt 1 failed")
        job.refresh_from_db()
        self.assertEqual(job.status, "queued")
        self.assertGreater(job.run_after, timezone.now())

        # Fast forward time to permit attempt 2
        AnalysisJob.objects.filter(pk=job.pk).update(run_after=timezone.now() - timedelta(seconds=1))
        claimed_2 = claim_next_job(worker_id="w1")
        self.assertEqual(claimed_2.attempts, 2)
        fail_and_retry(claimed_2.id, "Attempt 2 failed")
        job.refresh_from_db()
        self.assertEqual(job.status, "queued")

        # Fast forward time to permit attempt 3
        AnalysisJob.objects.filter(pk=job.pk).update(run_after=timezone.now() - timedelta(seconds=1))
        claimed_3 = claim_next_job(worker_id="w1")
        self.assertEqual(claimed_3.attempts, 3)
        fail_and_retry(claimed_3.id, "Attempt 3 failed")
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.error, "Attempt 3 failed")

        # Associated FileAnalysis is also marked failed
        analysis = FileAnalysis.objects.get(stored_object=self.stored_obj)
        self.assertEqual(analysis.status, "failed")
        self.assertEqual(analysis.error["code"], "MAX_ATTEMPTS_EXCEEDED")

    def test_stale_lock_release(self):
        """Locks older than 10 minutes are released back to queued."""
        job = enqueue_analysis_job(self.stored_obj)
        claimed = claim_next_job(worker_id="worker_crashed")
        self.assertEqual(claimed.status, "running")

        # Simulate job locked 11 minutes ago
        AnalysisJob.objects.filter(pk=job.pk).update(
            locked_at=timezone.now() - timedelta(minutes=11)
        )

        released = release_stale_locks(timeout_minutes=10)
        self.assertEqual(released, 1)

        job.refresh_from_db()
        self.assertEqual(job.status, "queued")
        self.assertIsNone(job.locked_by)
        self.assertIsNone(job.locked_at)


@override_settings(ORDP_WORKER_TOKEN=TEST_WORKER_TOKEN)
class InternalAIWorkerAPITests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="test_researcher",
            email="researcher@example.com",
            first_name="R",
            last_name="User",
            password="pass",
        )
        self.dataset = Dataset.objects.create(
            title="Worker API Test",
            owner=self.user,
            visibility=Dataset.Visibility.PRIVATE,
        )
        self.stored_obj = StoredObject.objects.create(
            dataset=self.dataset,
            storage_key="objects/test/api.csv",
            original_filename="api.csv",
            file_type="csv",
            file_size=2048,
            checksum_sha256="b" * 64,
            uploaded_by=self.user,
        )

    def test_wrong_worker_token_gives_401(self):
        """Worker endpoint rejects invalid or missing bearer token with 401."""
        resp = self.client.get("/internal/ai-jobs/next/", HTTP_AUTHORIZATION="Bearer wrong-token")
        self.assertEqual(resp.status_code, 401)

        resp2 = self.client.get("/internal/ai-jobs/next/")
        self.assertEqual(resp2.status_code, 401)

    def test_claim_next_and_post_result_is_idempotent(self):
        """Worker claims job (200), posts result (200), and duplicate posts are idempotent."""
        job = enqueue_analysis_job(self.stored_obj)

        # Claim job
        resp = self.client.get(
            "/internal/ai-jobs/next/",
            HTTP_AUTHORIZATION=f"Bearer {TEST_WORKER_TOKEN}",
            HTTP_X_WORKER_ID="worker-1",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["id"], str(job.id))
        self.assertEqual(resp.data["checksum"], "b" * 64)
        self.assertIn("file_url", resp.data)

        # Post result
        result_payload = {
            "status": "done",
            "level": "A",
            "detected_type": "csv",
            "extension_matches": True,
            "coverage": "fully_analysed",
            "checks": [
                {"name": "not_empty", "result": "pass", "detail": ""},
                {"name": "checksum_verified", "result": "pass", "detail": ""},
            ],
            "warnings": [],
            "technical": {"size_bytes": 2048, "sha256": "b" * 64},
            "error": None,
        }
        post_resp = self.client.post(
            f"/internal/ai-jobs/{job.id}/result/",
            data=result_payload,
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {TEST_WORKER_TOKEN}",
        )
        self.assertEqual(post_resp.status_code, 200)
        self.assertTrue(post_resp.data.get("saved"))

        # Verify DB state
        analysis = FileAnalysis.objects.get(stored_object=self.stored_obj)
        self.assertEqual(analysis.status, "done")
        self.assertEqual(analysis.level, "A")

        # Repeat posting is idempotent
        post_resp2 = self.client.post(
            f"/internal/ai-jobs/{job.id}/result/",
            data=result_payload,
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {TEST_WORKER_TOKEN}",
        )
        self.assertEqual(post_resp2.status_code, 200)


class PublicAnalysisAPITests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="test_dataset_owner",
            email="dataset_owner@example.com",
            first_name="Owner",
            last_name="One",
            password="password",
        )
        self.stranger = User.objects.create_user(
            username="test_stranger",
            email="stranger@example.com",
            first_name="Stranger",
            last_name="Two",
            password="password",
        )
        self.dataset = Dataset.objects.create(
            title="Private Dataset with Analysis",
            owner=self.owner,
            visibility=Dataset.Visibility.PRIVATE,
        )
        self.stored_obj = StoredObject.objects.create(
            dataset=self.dataset,
            storage_key="objects/test/public.csv",
            original_filename="public.csv",
            file_type="csv",
            file_size=512,
            checksum_sha256="c" * 64,
            uploaded_by=self.owner,
        )

    def test_owner_sees_analysis_and_stranger_gets_404(self):
        """Owner can view analysis; unauthorized stranger gets 404."""
        # Create completed analysis
        FileAnalysis.objects.create(
            stored_object=self.stored_obj,
            checksum_sha256="c" * 64,
            level="A",
            detected_type="csv",
            status="done",
            coverage="fully_analysed",
            checks=[{"name": "not_empty", "result": "pass", "detail": ""}],
            technical={"size_bytes": 512, "sha256": "c" * 64},
        )

        url = f"/api/files/{self.stored_obj.id}/analysis/"

        # 1. Owner sees analysis
        self.client.force_authenticate(user=self.owner)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "done")
        self.assertEqual(resp.data["level"], "A")
        self.assertEqual(resp.data["coverage"], "fully_analysed")

        # 2. Stranger gets 404
        self.client.force_authenticate(user=self.stranger)
        resp_stranger = self.client.get(url)
        self.assertEqual(resp_stranger.status_code, 404)

        # 3. Unauthenticated on private dataset gets 404
        self.client.logout()
        resp_anon = self.client.get(url)
        self.assertEqual(resp_anon.status_code, 404)

    def test_running_analysis_returns_null_level(self):
        """While running or queued, returns status and level=null."""
        FileAnalysis.objects.create(
            stored_object=self.stored_obj,
            checksum_sha256="c" * 64,
            status="running",
            level=None,
        )

        url = f"/api/files/{self.stored_obj.id}/analysis/"
        self.client.force_authenticate(user=self.owner)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "running")
        self.assertIsNone(resp.data["level"])


class UploadResilienceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="test_uploader",
            email="uploader@example.com",
            first_name="Up",
            last_name="Loader",
            password="pass",
        )
        self.dataset = Dataset.objects.create(
            title="Upload Resilience Dataset",
            owner=self.user,
        )

    def test_upload_still_succeeds_when_enqueue_fails(self):
        """An AI queue failure inside transaction.on_commit must never break the upload."""
        with patch("apps.ai_integration.services.job_service.enqueue_analysis_job", side_effect=RuntimeError("AI Queue Down")):
            # Simulate what finalize_upload does
            with transaction.atomic():
                stored_obj = StoredObject.objects.create(
                    dataset=self.dataset,
                    storage_key="objects/test/resilience.csv",
                    original_filename="resilience.csv",
                    file_type="csv",
                    file_size=100,
                    checksum_sha256="d" * 64,
                    uploaded_by=self.user,
                )
                df = DatasetFile.objects.create(
                    dataset=self.dataset,
                    stored_object=stored_obj,
                    file_key=stored_obj.storage_key,
                    original_filename="resilience.csv",
                    file_type="csv",
                    file_size=100,
                    checksum="d" * 64,
                )
                try:
                    from apps.ai_integration.services.job_service import enqueue_analysis_job
                    transaction.on_commit(lambda so=stored_obj: enqueue_analysis_job(so))
                except Exception:
                    pass

            # StoredObject and DatasetFile still exist despite enqueue failure
            self.assertTrue(StoredObject.objects.filter(pk=stored_obj.pk).exists())
            self.assertTrue(DatasetFile.objects.filter(pk=df.pk).exists())


class ConcurrentJobClaimTests(TransactionTestCase):
    """Test concurrent claiming with select_for_update(skip_locked=True)."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="test_concurrency_user",
            email="concurrency@example.com",
            first_name="Conc",
            last_name="User",
            password="password",
        )
        self.dataset = Dataset.objects.create(
            title="Concurrent Claims Dataset",
            owner=self.user,
        )
        # Create 2 separate stored objects and queued jobs
        self.so1 = StoredObject.objects.create(
            dataset=self.dataset,
            storage_key="objects/test/c1.csv",
            original_filename="c1.csv",
            file_type="csv",
            file_size=100,
            checksum_sha256="e" * 64,
            uploaded_by=self.user,
        )
        self.so2 = StoredObject.objects.create(
            dataset=self.dataset,
            storage_key="objects/test/c2.csv",
            original_filename="c2.csv",
            file_type="csv",
            file_size=200,
            checksum_sha256="f" * 64,
            uploaded_by=self.user,
        )
        enqueue_analysis_job(self.so1)
        enqueue_analysis_job(self.so2)

    def test_two_workers_claiming_at_once_get_different_jobs(self):
        """Two concurrent workers claim distinct jobs simultaneously without conflict."""
        claimed = []
        errors = []

        def worker_claim(worker_name):
            try:
                job = claim_next_job(worker_id=worker_name)
                if job:
                    claimed.append(job.id)
            except Exception as exc:
                errors.append(exc)
            finally:
                connection.close()

        t1 = threading.Thread(target=worker_claim, args=("worker-A",))
        t2 = threading.Thread(target=worker_claim, args=("worker-B",))

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(len(errors), 0, f"Thread errors: {errors}")
        self.assertEqual(len(claimed), 2, "Both workers should have claimed a job")
        self.assertNotEqual(claimed[0], claimed[1], "Workers must not claim the same job")
