from django.db import migrations


def backfill_missing_profiles(apps, schema_editor):
    User = apps.get_model("auth", "User")
    UserProfile = apps.get_model("accounts", "UserProfile")
    UserRole = apps.get_model("accounts", "UserRole")

    for user in User.objects.all().iterator():
        name_parts = [getattr(user, "first_name", ""), getattr(user, "last_name", "")]
        full_name = " ".join(part for part in name_parts if part).strip()
        full_name = full_name or user.username or user.email or f"User {user.pk}"
        profile, _ = UserProfile.objects.get_or_create(
            user=user,
            defaults={"full_name": full_name},
        )

        if not profile.full_name:
            profile.full_name = full_name
            profile.save(update_fields=["full_name"])

        if not UserRole.objects.filter(profile=profile).exists():
            role = "admin" if user.is_superuser or user.is_staff else "public"
            UserRole.objects.create(profile=profile, role=role)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0025_blockedcredential"),
    ]

    operations = [
        migrations.RunPython(backfill_missing_profiles, migrations.RunPython.noop),
    ]
