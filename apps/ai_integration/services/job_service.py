from datetime import timedelta
import logging
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import ActivityLog
from apps.ai_integration.models import AnalysisJob, FileAnalysis

logger = logging.getLogger(__name__)


def release_stale_locks(timeout_minutes: int = 10) -> int:
    """Release locks for jobs that have been running longer than timeout_minutes."""
    stale_threshold = timezone.now() - timedelta(minutes=timeout_minutes)
    stale_jobs = AnalysisJob.objects.filter(
        status=AnalysisJob.Status.RUNNING,
        locked_at__lt=stale_threshold,
    )
    released_count = 0
    for job in stale_jobs:
        job.status = AnalysisJob.Status.QUEUED
        job.locked_by = None
        job.locked_at = None
        job.save(update_fields=["status", "locked_by", "locked_at", "updated_at"])
        released_count += 1
    return released_count


def enqueue_analysis_job(stored_object):
    """Enqueue an analysis job for a StoredObject.

    - Reuses finished FileAnalysis for identical checksum (no job created).
    - Otherwise creates or gets queued FileAnalysis and AnalysisJob.
    """
    checksum = stored_object.checksum_sha256.strip().lower()

    # 1. Check if this exact StoredObject already has a completed analysis
    existing_self_analysis = FileAnalysis.objects.filter(
        stored_object=stored_object,
        status__in=[FileAnalysis.Status.DONE, FileAnalysis.Status.PARTIAL],
    ).first()
    if existing_self_analysis:
        return None

    # 2. Check if a finished analysis exists for another StoredObject with the same checksum
    finished_analysis = (
        FileAnalysis.objects.filter(
            checksum_sha256=checksum,
            status__in=[FileAnalysis.Status.DONE, FileAnalysis.Status.PARTIAL],
        )
        .exclude(stored_object=stored_object)
        .first()
    )

    if finished_analysis:
        # Re-use analysis and skip job creation
        analysis, _ = FileAnalysis.objects.update_or_create(
            stored_object=stored_object,
            defaults={
                "checksum_sha256": checksum,
                "level": finished_analysis.level,
                "detected_type": finished_analysis.detected_type,
                "status": finished_analysis.status,
                "coverage": finished_analysis.coverage,
                "checks": finished_analysis.checks,
                "technical": finished_analysis.technical,
                "warnings": finished_analysis.warnings,
                "error": finished_analysis.error,
                "finished_at": timezone.now(),
            },
        )
        ActivityLog.log(
            user=stored_object.uploaded_by,
            action="analysis_completed",
            target_object=f"StoredObject:{stored_object.id}",
            ip_address="127.0.0.1",
            extra={"reused_from_checksum": checksum, "level": analysis.level},
        )
        return None

    # 3. Create or get queued FileAnalysis for this stored_object
    FileAnalysis.objects.get_or_create(
        stored_object=stored_object,
        defaults={
            "checksum_sha256": checksum,
            "status": FileAnalysis.Status.QUEUED,
        },
    )

    # 4. Enqueue AnalysisJob (idempotent by checksum key)
    job_key = f"analyse_file:{checksum}"
    payload = {
        "file_id": str(stored_object.id),
        "checksum": checksum,
        "size": stored_object.file_size,
        "filename": stored_object.original_filename,
    }

    job, created = AnalysisJob.objects.get_or_create(
        key=job_key,
        defaults={
            "job_type": "analyse_file",
            "payload": payload,
            "status": AnalysisJob.Status.QUEUED,
        },
    )

    if created:
        ActivityLog.log(
            user=stored_object.uploaded_by,
            action="analysis_queued",
            target_object=f"StoredObject:{stored_object.id}",
            ip_address="127.0.0.1",
            extra={"job_id": str(job.id), "checksum": checksum},
        )

    return job


def claim_next_job(worker_id: str = "worker"):
    """Claim the next available queued job using select_for_update(skip_locked=True)."""
    release_stale_locks(timeout_minutes=10)

    now = timezone.now()
    with transaction.atomic():
        job = (
            AnalysisJob.objects.select_for_update(skip_locked=True)
            .filter(
                job_type="analyse_file",
                status=AnalysisJob.Status.QUEUED,
                run_after__lte=now,
            )
            .order_by("created_at")
            .first()
        )

        if not job:
            return None

        job.status = AnalysisJob.Status.RUNNING
        job.locked_by = worker_id
        job.locked_at = now
        job.attempts += 1
        job.save(update_fields=["status", "locked_by", "locked_at", "attempts", "updated_at"])

        checksum = job.payload.get("checksum")
        if checksum:
            FileAnalysis.objects.filter(
                checksum_sha256=checksum,
                status=FileAnalysis.Status.QUEUED,
            ).update(status=FileAnalysis.Status.RUNNING)

        return job


def complete_job(job_id, result_data):
    """Mark a job done and save the result across all matching StoredObjects."""
    with transaction.atomic():
        job = AnalysisJob.objects.select_for_update().get(pk=job_id)
        job.status = AnalysisJob.Status.DONE
        job.locked_by = None
        job.locked_at = None
        job.save(update_fields=["status", "locked_by", "locked_at", "updated_at"])

        checksum = job.payload.get("checksum")
        from .analysis_service import save_analysis_result
        save_analysis_result(checksum, result_data)


def fail_and_retry(job_id, error_message: str):
    """Fail or retry a job with 1, 5, 15 minute back-off schedule."""
    with transaction.atomic():
        job = AnalysisJob.objects.select_for_update().get(pk=job_id)
        checksum = job.payload.get("checksum")

        if job.attempts >= job.max_attempts:
            job.status = AnalysisJob.Status.FAILED
            job.error = error_message
            job.locked_by = None
            job.locked_at = None
            job.save(update_fields=["status", "error", "locked_by", "locked_at", "updated_at"])

            if checksum:
                FileAnalysis.objects.filter(
                    checksum_sha256=checksum,
                    status__in=[FileAnalysis.Status.QUEUED, FileAnalysis.Status.RUNNING],
                ).update(
                    status=FileAnalysis.Status.FAILED,
                    error={"code": "MAX_ATTEMPTS_EXCEEDED", "detail": error_message},
                    finished_at=timezone.now(),
                )

            ActivityLog.log(
                user=None,
                action="analysis_failed",
                target_object=f"AnalysisJob:{job.id}",
                ip_address="127.0.0.1",
                extra={"error": error_message, "attempts": job.attempts},
            )
        else:
            backoff_schedule = [1, 5, 15]
            index = min(max(job.attempts - 1, 0), len(backoff_schedule) - 1)
            backoff_minutes = backoff_schedule[index]

            job.status = AnalysisJob.Status.QUEUED
            job.run_after = timezone.now() + timedelta(minutes=backoff_minutes)
            job.error = error_message
            job.locked_by = None
            job.locked_at = None
            job.save(update_fields=["status", "run_after", "error", "locked_by", "locked_at", "updated_at"])
