from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import UserProfile, UserRole


User = get_user_model()


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    fk_name = "user"
    fields = (
        "full_name",
        "affiliation",
        "academia",
        "can_upload_datasets",
        "email_verified",
        "terms_accepted",
        "interests_completed",
    )


class CustomUserAdmin(BaseUserAdmin):
    def get_inlines(self, request, obj=None):
        if obj is None:
            return ()
        return (UserProfileInline,)


admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "full_name", "affiliation", "email_verified", "can_upload_datasets")
    search_fields = ("user__username", "user__email", "full_name")


@admin.register(UserRole)
class UserRoleAdmin(admin.ModelAdmin):
    list_display = ("profile", "role", "is_primary", "granted_at")
    list_filter = ("role", "is_primary")
