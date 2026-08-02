from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import UserProfile
from .models import ResearcherRequest
from .services import decide_researcher_request

User = get_user_model()

@admin.register(ResearcherRequest)
class ResearcherRequestAdmin(admin.ModelAdmin):
    list_display = ("user", "status", "submitted_at", "decided_by")
    list_filter = ("status",)
    readonly_fields = ("submitted_at",)
    actions = ["approve_requests"]

    def approve_requests(self, request, queryset):
        count = 0
        for req in queryset.filter(status=ResearcherRequest.Status.PENDING):
            decide_researcher_request(req, "approve", request.user)
            count += 1
        self.message_user(request, f"Approved {count} request(s).")
    approve_requests.short_description = "Approve selected requests"
class UserProfileInline(admin.StackedInline):
    class Media:
        js = ("accounts/profile_role_toggle.js",)
    model = UserProfile
    can_delete = False
    fk_name = "user"
    filter_horizontal = ("expertise",)
    fields = ("full_name", "role", "academia", "department", "expertise", "terms_accepted")


class CustomUserAdmin(BaseUserAdmin):
    inlines = (UserProfileInline,)


admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)