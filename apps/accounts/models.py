import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone
from datetime import timedelta
from django.core.exceptions import ValidationError
class LoginSecurity(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="login_security",
    )
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    def is_locked(self):
        if self.locked_until and self.locked_until <= timezone.now():
            self.failed_attempts = 0
            self.locked_until = None
            self.save()
            return False
        return self.locked_until is not None

    def register_failed_attempt(self):
        self.failed_attempts += 1
        if self.failed_attempts >= 3:
            self.locked_until = timezone.now() + timedelta(minutes=15)
        self.save()

    def reset(self):
        self.failed_attempts = 0
        self.locked_until = None
        self.save()


class College(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=128, unique=True)

    def __str__(self):
        return self.name


class CenterOfExcellence(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=128, unique=True)

    def __str__(self):
        return self.name


class Department(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=128)
    college = models.ForeignKey(College, null=True, blank=True, on_delete=models.CASCADE, related_name="departments")
    center_of_excellence = models.ForeignKey(
        CenterOfExcellence, null=True, blank=True, on_delete=models.CASCADE, related_name="departments"
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(  
                    models.Q(college__isnull=False, center_of_excellence__isnull=True)
                    | models.Q(college__isnull=True, center_of_excellence__isnull=False)
                ),
                name="department_belongs_to_college_xor_coe",
            )
        ]

    def __str__(self):
        return self.name
class ResearchCategory(models.Model):
    class Status(models.TextChoices):
        APPROVED = "approved"
        PENDING = "pending"
        REJECTED = "rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=128, unique=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.APPROVED)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

class UserProfile(models.Model):
    ACADEMIA_CHOICES = [
        ("student", "Student"), ("researcher", "Researcher"), ("lecturer", "Lecturer"),
        ("professor", "Professor"), ("assistant_lecturer", "Assistant Lecturer"),
        ("data_scientist", "Data Scientist"), ("software_engineer", "Software Engineer"),
        ("government_officer", "Government Officer"), ("industry_professional", "Industry Professional"),
        ("other", "Other"),
    ]
    ACADEMIC_TITLE_CHOICES = [
        ("none", "None"), ("mr", "Mr."), ("ms", "Ms."), ("mrs", "Mrs."),
        ("eng", "Eng."), ("dr", "Dr."), ("prof", "Prof."),
    ]
    ACADEMIC_RANK_CHOICES = [
        ("none", "None"),
        ("graduate_assistant", "Graduate Assistant"),
        ("assistant_lecturer", "Assistant Lecturer"),
        ("lecturer", "Lecturer"),
        ("assistant_professor", "Assistant Professor"),
        ("associate_professor", "Associate Professor"),
        ("professor", "Professor"),
    ]
    DEGREE_CHOICES = [
        ("high_school", "High School Diploma"), ("diploma", "Diploma"),
        ("bachelor", "Bachelor's Degree"), ("master", "Master's Degree"),
        ("phd", "PhD"), ("postdoc", "Postdoctoral Fellowship"), ("other", "Other"),
    ]
    VISIBILITY_CHOICES = [
        ("public", "Everyone (Public)"), ("trusted", "Trusted Parties"), ("private", "Only Me (Private)"),
    ]
    def has_role(self, *roles):
        return self.roles.filter(role__in=roles).exists()
    class Role(models.TextChoices):
        PUBLIC = "public", "Public"
        RESEARCHER = "researcher", "Researcher"
        CHECKER = "checker", "Checker/Reviewer"
        ADMIN = "admin", "Admin"

    ROLE_CHOICES = Role.choices

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")

    # Personal Information
    full_name = models.CharField(max_length=255)
    profile_picture = models.ImageField(upload_to="profile_pictures/", blank=True, null=True)

    # Academic & Professional Information
    affiliation = models.CharField(max_length=255, default="Addis Ababa Science and Technology University (AASTU)")
    college = models.ForeignKey(College, null=True, blank=True, on_delete=models.SET_NULL)                      # was CharField
    center_of_excellence = models.ForeignKey(CenterOfExcellence, null=True, blank=True, on_delete=models.SET_NULL)  # was CharField
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.SET_NULL)                # was CharField
    academia = models.CharField(max_length=30, choices=ACADEMIA_CHOICES)
    academic_title = models.CharField(max_length=10, choices=ACADEMIC_TITLE_CHOICES, blank=True, default="none")
    academic_rank = models.CharField(max_length=32, choices=ACADEMIC_RANK_CHOICES, blank=True, default="none")  # re-added
    highest_degree = models.CharField(max_length=20, choices=DEGREE_CHOICES, blank=True)
    orcid_id = models.CharField(max_length=19, blank=True)
    profession = models.CharField(max_length=255, blank=True)
    profile_completed = models.BooleanField(default=False)
    is_external = models.BooleanField(default=False)
    sponsored_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="sponsored_users",
    )
    # Research Profile
    research_interests = models.ManyToManyField(ResearchCategory, blank=True, related_name="interested_users")
    bio = models.CharField(max_length=300, blank=True)
    additional_link = models.URLField(blank=True)

    # Visibility & Consent
    profile_visibility = models.CharField(max_length=10, choices=VISIBILITY_CHOICES, default="private")
    terms_accepted = models.BooleanField(default=False)
    terms_accepted_at = models.DateTimeField(null=True, blank=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.PUBLIC)

    expertise = models.ManyToManyField("metadata.Category", blank=True, related_name="reviewers")
  
    def clean(self):

        if self.college_id and self.center_of_excellence_id:
            raise ValidationError("Select either a College or a Center of Excellence, not both.")
        if self.department_id:
            if self.college_id and self.department.college_id != self.college_id:
                raise ValidationError({"department": "Department does not belong to the selected College."})
            if self.center_of_excellence_id and self.department.center_of_excellence_id != self.center_of_excellence_id:
                raise ValidationError({"department": "Department does not belong to the selected Center of Excellence."})
    email_verified = models.BooleanField(default=False)
    research_interests_completed = models.BooleanField(default=False)


    def is_profile_complete(self):
        required = ["academia", "department"]
        return self.terms_accepted and all(getattr(self, f, None) for f in required)

class UserRole(models.Model):
    class RoleChoice(models.TextChoices):
        PUBLIC = "public"
        RESEARCHER = "researcher"
        CHECKER = "checker"
        ADMIN = "admin"

    profile = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name="roles")
    role = models.CharField(max_length=20, choices=RoleChoice.choices)
    granted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ["profile", "role"]


class ResearcherRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        REJECTED = "rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="researcher_request")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    reason = models.TextField(blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)


class ActivityLog(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="activity_logs",
    )
    action = models.CharField(max_length=100)
    target_object = models.CharField(max_length=255)
    ip_address = models.GenericIPAddressField()
    extra = models.JSONField(default=dict, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]
    @classmethod
    def log(cls, user, action, target_object, ip_address="0.0.0.0", extra=None):
        return cls.objects.create(
            user=user, action=action, target_object=target_object,
            ip_address=ip_address, extra=extra or {},
        )