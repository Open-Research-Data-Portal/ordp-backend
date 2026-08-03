from django.db import migrations


def clear_empty_strings(apps, schema_editor):
    UserProfile = apps.get_model("accounts", "UserProfile")
    UserProfile.objects.filter(college="").update(college=None)
    UserProfile.objects.filter(center_of_excellence="").update(center_of_excellence=None)
    UserProfile.objects.filter(department="").update(department=None)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0009_userprofile_role"),   
    ]
    operations = [
        migrations.RunPython(clear_empty_strings, noop_reverse),
    ]