from django.contrib.auth import get_user_model
from apps.accounts.models import UserProfile, College, Department

User = get_user_model()


def make_college_and_department(
    college_name="Test College",
    dept_name="Test Department",
):
    college = College.objects.create(name=college_name)
    department = Department.objects.create(
        name=dept_name,
        college=college,
    )
    return college, department

def make_user(username, email, role="researcher", department=None):
    user = User.objects.create_user(
        username=username,
        email=email,
        password="pw12345!"
    )

    profile = user.profile
    profile.full_name = username.title()

    if role == "public":
        profile.save()
        return user

    if department is None:
        _, department = make_college_and_department(
            f"{username}-college",
            f"{username}-dept",
        )

    profile.role = role
    profile.academia = "researcher"
    profile.department = department
    profile.terms_accepted = True
    profile.save()

    return user