from django.urls import path
from .views import NextJobView, JobResultView, FileAnalysisDetailView

urlpatterns = [
    path("internal/ai-jobs/next/", NextJobView.as_view(), name="internal-ai-job-next"),
    path("internal/ai-jobs/<uuid:job_id>/result/", JobResultView.as_view(), name="internal-ai-job-result"),
    path("api/files/<str:file_id>/analysis/", FileAnalysisDetailView.as_view(), name="file-analysis-detail"),
]
