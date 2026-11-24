from django.contrib import admin
from .models import AnalysisJob, FileAnalysis


@admin.register(AnalysisJob)
class AnalysisJobAdmin(admin.ModelAdmin):
    list_display = ("id", "job_type", "key", "status", "attempts", "run_after", "locked_by", "created_at")
    list_filter = ("status", "job_type")
    search_fields = ("key", "locked_by")
    readonly_fields = ("created_at", "updated_at")


@admin.register(FileAnalysis)
class FileAnalysisAdmin(admin.ModelAdmin):
    list_display = ("id", "stored_object", "level", "detected_type", "status", "coverage", "created_at")
    list_filter = ("status", "level", "coverage")
    search_fields = ("checksum_sha256", "stored_object__original_filename")
    readonly_fields = ("created_at", "finished_at")
