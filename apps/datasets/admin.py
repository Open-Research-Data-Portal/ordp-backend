from django.contrib import admin

from .models import DatasetProposalActivity


@admin.register(DatasetProposalActivity)
class DatasetProposalActivityAdmin(admin.ModelAdmin):
    list_display = ("created_at", "proposal", "action", "actor", "from_status", "to_status")
    list_filter = ("action", "created_at")
    search_fields = ("proposal__id", "reason", "actor__email")
    readonly_fields = tuple(field.name for field in DatasetProposalActivity._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

from .models import DatasetProposalVote, DOIRegistrationAttempt, PIDRegistrationEvent


@admin.register(DatasetProposalVote)
class DatasetProposalVoteAdmin(admin.ModelAdmin):
    list_display = ("voted_at", "proposal", "reviewer", "vote")
    list_filter = ("vote", "voted_at")
    search_fields = ("proposal__id", "reviewer__email", "reason")
    readonly_fields = tuple(field.name for field in DatasetProposalVote._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DOIRegistrationAttempt)
class DOIRegistrationAttemptAdmin(admin.ModelAdmin):
    list_display = ("attempted_at", "identifier", "attempt_number", "status")
    list_filter = ("status", "attempted_at")
    search_fields = ("identifier__identifier_value", "identifier__id", "error_information")
    readonly_fields = tuple(field.name for field in DOIRegistrationAttempt._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PIDRegistrationEvent)
class PIDRegistrationEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "event_id", "event_type", "from_status", "to_status", "retry_count")
    list_filter = ("to_status", "event_type", "created_at")
    search_fields = ("event_id", "detail")
    readonly_fields = tuple(field.name for field in PIDRegistrationEvent._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

from .models import OutboxEvent


@admin.register(OutboxEvent)
class OutboxEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "event_type", "status", "retry_count", "available_at", "failed_at")
    list_filter = ("status", "event_type", "created_at")
    search_fields = ("idempotency_key", "last_error")
    readonly_fields = tuple(field.name for field in OutboxEvent._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
