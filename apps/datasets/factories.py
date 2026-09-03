from django.contrib.auth import get_user_model
from apps.accounts.models import UserProfile, UserRole, College

User = get_user_model()


def make_college(college_name="Test College"):
    return College.objects.create(name=college_name)


def make_user(username, email, role="researcher", college=None):
    user = User.objects.create_user(
        username=username,
        email=email,
        password="pw12345!",
    )

    profile = user.profile
    profile.full_name = username.title()

    if role == "public":
        profile.save()
        return user

    if college is None:
        college = make_college(f"{username}-college")

    if role in UserRole.RoleChoice.values:
        UserRole.objects.get_or_create(
            profile=profile,
            role=role,
        )

    profile.academia = UserProfile.Academia.RESEARCHER
    profile.college = college
    profile.terms_accepted = True
    profile.affiliation = "AASTU"
    profile.profile_visibility = "public"
    profile.can_upload_datasets = True
    profile.save()

    return user