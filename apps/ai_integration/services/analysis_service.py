import logging
from django.utils import timezone
from apps.accounts.models import ActivityLog
from apps.datasets.models import StoredObject
from apps.ai_integration.models import FileAnalysis

logger = logging.getLogger(__name__)


def save_analysis_result(checksum: str, result_data: dict):
    """Save analysis results to all StoredObjects that share the checksum.

    If multiple StoredObjects have this checksum and no final result yet,
    every one of them receives the result.
    """
    normalized_checksum = checksum.strip().lower()
    matching_objects = StoredObject.objects.filter(checksum_sha256=normalized_checksum)

    status_val = result_data.get("status", FileAnalysis.Status.DONE)
    level_val = result_data.get("level")
    detected_type = result_data.get("detected_type", "")
    coverage = result_data.get("coverage", FileAnalysis.Coverage.FULLY_ANALYSED)
    checks = result_data.get("checks", [])
    technical = result_data.get("technical", {})
    warnings = result_data.get("warnings", [])
    error = result_data.get("error")

    saved_analyses = []
    for stored_obj in matching_objects:
        analysis, _ = FileAnalysis.objects.update_or_create(
            stored_object=stored_obj,
            defaults={
                "checksum_sha256": normalized_checksum,
                "level": level_val,
                "detected_type": detected_type,
                "status": status_val,
                "coverage": coverage,
                "checks": checks,
                "technical": technical,
                "warnings": warnings,
                "error": error,
                "finished_at": timezone.now(),
            },
        )
        saved_analyses.append(analysis)

        ActivityLog.log(
            user=None,
            action="analysis_completed" if status_val != FileAnalysis.Status.FAILED else "analysis_failed",
            target_object=f"StoredObject:{stored_obj.id}",
            ip_address="127.0.0.1",
            extra={"status": status_val, "level": level_val, "coverage": coverage},
        )

    return saved_analyses
