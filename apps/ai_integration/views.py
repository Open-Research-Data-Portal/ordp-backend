import uuid
from urllib.parse import urlparse, urlunparse
from django.conf import settings
from django.db import models
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny

from apps.datasets.models import StoredObject, DatasetFile, DatasetProposalFile, DatasetReleaseFile
from apps.datasets.services.storage import presigned_download_url
from apps.datasets.v2_views import _can_read_dataset
from apps.ai_integration.models import AnalysisJob, FileAnalysis
from apps.ai_integration.permissions import IsWorkerAuthenticated
from apps.ai_integration.throttling import WorkerRateThrottle
from apps.ai_integration.serializers import AnalysisResultInputSerializer
from apps.ai_integration.services.job_service import claim_next_job, complete_job


def _adjust_worker_url(file_url: str) -> str:
    """Optionally rewrite storage host for worker container networking.

    If OBJECT_STORAGE_INTERNAL_URL is set, replace the scheme and netloc.
    """
    internal_endpoint = getattr(settings, "OBJECT_STORAGE_INTERNAL_URL", "").strip()
    if not internal_endpoint or not file_url:
        return file_url

    parsed_target = urlparse(internal_endpoint)
    parsed_source = urlparse(file_url)
    replaced = parsed_source._replace(
        scheme=parsed_target.scheme or parsed_source.scheme,
        netloc=parsed_target.netloc or parsed_source.netloc,
    )
    return urlunparse(replaced)


class NextJobView(APIView):
    """Claim the next pending analysis job for the AI worker."""
    authentication_classes = []
    permission_classes = [IsWorkerAuthenticated]
    throttle_classes = [WorkerRateThrottle]
    schema = None

    def get_authenticate_header(self, request):
        return 'Bearer realm="internal"'

    def get(self, request):
        worker_id = request.headers.get("X-Worker-Id", "ai_worker")
        job = claim_next_job(worker_id=worker_id)
        if not job:
            return Response(status=status.HTTP_204_NO_CONTENT)

        file_id = job.payload.get("file_id")
        stored_object = StoredObject.objects.filter(pk=file_id).first()
        if not stored_object:
            return Response(
                {"detail": "Associated StoredObject not found.", "code": "NOT_FOUND"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # 15-minute download link
        raw_url = presigned_download_url(stored_object.storage_key, expires_seconds=900)
        file_url = _adjust_worker_url(raw_url)

        return Response(
            {
                "id": str(job.id),
                "type": job.job_type,
                "file_id": str(stored_object.id),
                "checksum": stored_object.checksum_sha256,
                "size": stored_object.file_size,
                "filename": stored_object.original_filename,
                "file_url": file_url,
            },
            status=status.HTTP_200_OK,
        )


class JobResultView(APIView):
    """Save the result posted by the AI worker."""
    authentication_classes = []
    permission_classes = [IsWorkerAuthenticated]
    throttle_classes = [WorkerRateThrottle]
    schema = None

    def get_authenticate_header(self, request):
        return 'Bearer realm="internal"'

    def post(self, request, job_id):
        job = AnalysisJob.objects.filter(pk=job_id).first()
        if not job:
            return Response(
                {"detail": "Job not found.", "code": "NOT_FOUND"},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = AnalysisResultInputSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"detail": serializer.errors, "code": "INVALID_RESULT"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        complete_job(job.id, serializer.validated_data)
        return Response({"saved": True}, status=status.HTTP_200_OK)


class FileAnalysisDetailView(APIView):
    """Retrieve the analysis status and results for a file.

    Follows the shared dataset access rules:
    - Returns 404 when the caller cannot see the dataset or file.
    - While running or queued, returns status and level=null.
    """
    permission_classes = [AllowAny]

    def get(self, request, file_id):
        # 1. Resolve to StoredObject (supports StoredObject.id, DatasetFile.id,
        # DatasetProposalFile.id/logical_file_id, or DatasetReleaseFile.id/logical_file_id)
        try:
            val_uuid = uuid.UUID(str(file_id))
        except (ValueError, AttributeError):
            return Response(
                {"detail": "Not found.", "code": "NOT_FOUND"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # 1. Resolve to StoredObject (primary: StoredObject.id; V2: DatasetProposalFile / DatasetReleaseFile; legacy: DatasetFile)
        stored_object = StoredObject.objects.filter(pk=val_uuid).select_related("dataset").first()

        # Legacy (V1): Fallback lookup for legacy DatasetFile IDs (retained for backward compatibility with older datasets)
        if not stored_object:
            ds_file = DatasetFile.objects.filter(pk=val_uuid).select_related("stored_object__dataset").first()
            if ds_file and ds_file.stored_object:
                stored_object = ds_file.stored_object

        # V2: Lookup via DatasetProposalFile (by proposal file ID or logical_file_id)
        if not stored_object:
            proposal_file = DatasetProposalFile.objects.filter(
                models.Q(pk=val_uuid) | models.Q(logical_file_id=val_uuid)
            ).select_related("stored_object__dataset").first()
            if proposal_file and proposal_file.stored_object:
                stored_object = proposal_file.stored_object

        # V2: Lookup via DatasetReleaseFile (by release file ID or logical_file_id)
        if not stored_object:
            release_file = DatasetReleaseFile.objects.filter(
                models.Q(pk=val_uuid) | models.Q(logical_file_id=val_uuid)
            ).select_related("stored_object__dataset").first()
            if release_file and release_file.stored_object:
                stored_object = release_file.stored_object

        if not stored_object:
            return Response(
                {"detail": "Not found.", "code": "NOT_FOUND"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # 2. Check access using shared permission function
        if not _can_read_dataset(request.user, stored_object.dataset):
            return Response(
                {"detail": "Not found.", "code": "NOT_FOUND"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # 3. Fetch FileAnalysis
        analysis = FileAnalysis.objects.filter(stored_object=stored_object).first()
        if not analysis or analysis.status in [FileAnalysis.Status.QUEUED, FileAnalysis.Status.RUNNING]:
            current_status = analysis.status if analysis else "queued"
            return Response(
                {
                    "file_id": str(file_id),
                    "status": current_status,
                    "level": None,
                    "coverage": None,
                    "detected_type": None,
                    "checks": [],
                    "warnings": [],
                    "technical": {},
                },
                status=status.HTTP_200_OK,
            )

        return Response(
            {
                "file_id": str(file_id),
                "status": analysis.status,
                "level": analysis.level,
                "coverage": analysis.coverage,
                "detected_type": analysis.detected_type,
                "checks": analysis.checks,
                "warnings": analysis.warnings,
                "technical": analysis.technical,
                "error": analysis.error,
            },
            status=status.HTTP_200_OK,
        )
