from django.db import migrations


def migrate_legacy_upload_permissions(apps, schema_editor):
    UserProfile = apps.get_model("accounts", "UserProfile")
    Permission = apps.get_model("access_control", "Permission")
    UserPermissionGrant = apps.get_model("access_control", "UserPermissionGrant")

    permission = Permission.objects.filter(pk="dataset.create").first()
    if not permission:
        return

    for profile in UserProfile.objects.filter(can_upload_datasets=True).only("user_id"):
        UserPermissionGrant.objects.get_or_create(
            user_id=profile.user_id,
            permission_id=permission.pk,
            resource_type="platform",
            resource_id=None,
            revoked_at=None,
            defaults={"granted_by_id": None},
        )


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0022_userprofile_upload_permission_revoked_and_more"),
        ("access_control", "0005_expand_catalog_and_scopes"),
    ]

    operations = [
        migrations.RunPython(migrate_legacy_upload_permissions, migrations.RunPython.noop),
    ]
