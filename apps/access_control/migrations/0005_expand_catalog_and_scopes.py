from django.db import migrations, models
from django.db.models import Q


EXTRA_CATALOG = [
    ("dataset.publish", "Publish dataset", "platform", "publish"),
    ("access_request.create", "Create access requests", "platform", "create"),
    ("access_request.view", "View access requests", "platform", "view"),
    ("access_request.approve", "Approve access requests", "platform", "approve"),
    ("access_request.reject", "Reject access requests", "platform", "reject"),
    ("audit.export", "Export permission history", "platform", "export"),
    ("project.view", "View project", "project", "view"),
    ("project.create", "Create project", "platform", "create"),
    ("project.edit", "Edit project", "project", "edit"),
    ("project.delete", "Delete project", "project", "delete"),
    ("project.member.add", "Add project members", "project", "member.add"),
    ("project.member.remove", "Remove project members", "project", "member.remove"),
    ("collection.view", "View collection", "collection", "view"),
    ("collection.create", "Create collection", "platform", "create"),
    ("collection.edit", "Edit collection", "collection", "edit"),
    ("collection.delete", "Delete collection", "collection", "delete"),
    ("dataset_file.view", "View dataset file", "dataset_file", "view"),
    ("dataset_file.download", "Download dataset file", "dataset_file", "download"),
]

# Committee-wide approve/publish authority lives on the platform.
PLATFORM_OVERRIDES = {
    "dataset.approve": ("Approve dataset", "platform", "approve"),
}


def expand_catalog(apps, schema_editor):
    Permission = apps.get_model("access_control", "Permission")
    for code, name, resource_type, action in EXTRA_CATALOG:
        Permission.objects.update_or_create(
            code=code,
            defaults={"name": name, "resource_type": resource_type, "action": action},
        )
    for code, (name, resource_type, action) in PLATFORM_OVERRIDES.items():
        Permission.objects.update_or_create(
            code=code,
            defaults={"name": name, "resource_type": resource_type, "action": action},
        )


class Migration(migrations.Migration):
    dependencies = [
        ("access_control", "0004_accessrequest_group_accessrequest_kind"),
    ]

    operations = [
        migrations.AlterField(
            model_name="userpermissiongrant",
            name="resource_type",
            field=models.CharField(
                choices=[
                    ("platform", "Platform"),
                    ("dataset", "Dataset"),
                    ("dataset_file", "Dataset file"),
                    ("project", "Project"),
                    ("collection", "Collection"),
                ],
                max_length=24,
            ),
        ),
        migrations.AlterField(
            model_name="grouppermissiongrant",
            name="resource_type",
            field=models.CharField(
                choices=[
                    ("platform", "Platform"),
                    ("dataset", "Dataset"),
                    ("dataset_file", "Dataset file"),
                    ("project", "Project"),
                    ("collection", "Collection"),
                ],
                max_length=24,
            ),
        ),
        migrations.AlterField(
            model_name="accessrequest",
            name="resource_type",
            field=models.CharField(
                choices=[
                    ("platform", "Platform"),
                    ("dataset", "Dataset"),
                    ("dataset_file", "Dataset file"),
                    ("project", "Project"),
                    ("collection", "Collection"),
                ],
                max_length=24,
            ),
        ),
        migrations.RemoveConstraint(model_name="userpermissiongrant", name="userpermissiongrant_valid_scope"),
        migrations.RemoveConstraint(model_name="grouppermissiongrant", name="grouppermissiongrant_valid_scope"),
        migrations.AddConstraint(
            model_name="userpermissiongrant",
            constraint=models.CheckConstraint(
                condition=(
                    Q(resource_type="platform", resource_id__isnull=True)
                    | Q(resource_type__in=["dataset", "dataset_file", "project", "collection"], resource_id__isnull=False)
                ),
                name="userpermissiongrant_valid_scope",
            ),
        ),
        migrations.AddConstraint(
            model_name="grouppermissiongrant",
            constraint=models.CheckConstraint(
                condition=(
                    Q(resource_type="platform", resource_id__isnull=True)
                    | Q(resource_type__in=["dataset", "dataset_file", "project", "collection"], resource_id__isnull=False)
                ),
                name="grouppermissiongrant_valid_scope",
            ),
        ),
        migrations.RunPython(expand_catalog, migrations.RunPython.noop),
    ]
