from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import UserProfile


User = get_user_model()


class UserProfileInline(admin.StackedInline):
    class Media:
        js = ("accounts/profile_role_toggle.js",)
    model = UserProfile
    can_delete = False
    fk_name = "user"
    filter_horizontal = ("interests",)
    fields = (
    "full_name",
    "role",
    "academia",
    "interests",
    "terms_accepted",
)


class CustomUserAdmin(BaseUserAdmin):
    inlines = (UserProfileInline,)


admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)