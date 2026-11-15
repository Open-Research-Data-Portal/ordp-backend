from django.db import migrations


CATALOG = [
    ("dataset.view", "View dataset", "dataset", "view"),
    ("dataset.edit", "Edit dataset", "dataset", "edit"),
    ("dataset.delete", "Delete dataset", "dataset", "delete"),
    ("dataset.download", "Download dataset", "dataset", "download"),
    ("dataset.upload", "Upload dataset file", "dataset", "upload"),
    ("dataset.metadata.edit", "Edit metadata", "dataset", "metadata.edit"),
    ("dataset.version.view", "View versions", "dataset", "version.view"),
    ("dataset.version.create", "Create version", "dataset", "version.create"),
    ("dataset.version.restore", "Restore version", "dataset", "version.restore"),
    ("dataset.approve", "Approve dataset", "dataset", "approve"),
    ("dataset.create", "Create dataset", "platform", "create"),
    ("user.view", "View users", "platform", "view"),
    ("user.manage", "Manage users", "platform", "manage"),
    ("user.permission.grant", "Grant permissions", "platform", "permission.grant"),
    ("user.permission.revoke", "Revoke permissions", "platform", "permission.revoke"),
    ("group.create", "Create groups", "platform", "create"),
    ("group.edit", "Edit groups", "platform", "edit"),
    ("group.delete", "Archive groups", "platform", "delete"),
    ("group.member.manage", "Manage group members", "platform", "member.manage"),
    ("group.permission.manage", "Manage group permissions", "platform", "permission.manage"),
    ("access_request.review", "Review access requests", "platform", "review"),
    ("audit.view", "View permission history", "platform", "view"),
]


def add_catalog(apps, schema_editor):
    Permission = apps.get_model("access_control", "Permission")
    for code, name, resource_type, action in CATALOG:
        Permission.objects.update_or_create(code=code, defaults={
            "name": name, "resource_type": resource_type, "action": action,
        })


class Migration(migrations.Migration):
    dependencies = [("access_control", "0001_initial")]
    operations = [migrations.RunPython(add_catalog, migrations.RunPython.noop)]
