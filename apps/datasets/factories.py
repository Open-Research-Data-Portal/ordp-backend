from django.contrib.auth import get_user_model
from apps.accounts.models import UserProfile, College, Department

User = get_user_model()


def make_college_and_department(college_name="Test College", dept_name="Test Department"):
    college = College.objects.create(name=college_name)
    department = Department.objects.create(name=dept_name, college=college)
    return college, department


def make_user(username, email, role="researcher", department=None):
    """role='public' skips academia/department/terms — mirrors a fresh registrant.
    Any other role is treated as already-approved, with a full profile."""
    user = User.objects.create_user(username=username, email=email, password="pw12345!")
    if role == "public":
        UserProfile.objects.create(user=user, full_name=username.title())  
        return user

    if department is None:
        _, department = make_college_and_department(f"{username}-college", f"{username}-dept")
    UserProfile.objects.create(
        user=user, full_name=username.title(), role=role,
        academia="researcher", department=department, terms_accepted=True,
    )
    return user