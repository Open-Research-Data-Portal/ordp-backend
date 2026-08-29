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


class UserProfile(models.Model):
    class Academia(models.TextChoices):
        STUDENT = "student", "Student"
        RESEARCHER = "researcher", "Researcher"
        LECTURER = "lecturer", "Lecturer"
        PROFESSOR = "professor", "Professor"
        ASSISTANT_LECTURER = "assistant_lecturer", "Assistant Lecturer"
        DATA_SCIENTIST = "data_scientist", "Data Scientist"
        SOFTWARE_ENGINEER = "software_engineer", "Software Engineer"
        GOVERNMENT_OFFICER = "government_officer", "Government Officer"
        INDUSTRY_PROFESSIONAL = "industry_professional", "Industry Professional"
        OTHER = "other", "Other"


    class AcademicTitle(models.TextChoices):
        NONE = "none", "None"
        MR = "mr", "Mr."
        MS = "ms", "Ms."
        MRS = "mrs", "Mrs."
        ENG = "eng", "Eng."
        DR = "dr", "Dr."
        PROF = "prof", "Prof."


    class AcademicRank(models.TextChoices):
        NONE = "none", "None"
        GRADUATE_ASSISTANT = "graduate_assistant", "Graduate Assistant"
        ASSISTANT_LECTURER = "assistant_lecturer", "Assistant Lecturer"
        LECTURER = "lecturer", "Lecturer"
        ASSISTANT_PROFESSOR = "assistant_professor", "Assistant Professor"
        ASSOCIATE_PROFESSOR = "associate_professor", "Associate Professor"
        PROFESSOR = "professor", "Professor"


    class HighestDegree(models.TextChoices):
        HIGH_SCHOOL = "high_school", "High School Diploma"
        DIPLOMA = "diploma", "Diploma"
        BACHELOR = "bachelor", "Bachelor's Degree"
        MASTER = "master", "Master's Degree"
        PHD = "phd", "PhD"
        POSTDOC = "postdoc", "Postdoctoral Fellowship"
        OTHER = "other", "Other"
    VISIBILITY_CHOICES = [
        ("public", "Everyone (Public)"),
        ("private", "Only Me (Private)"),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )

    # Personal Information
    full_name = models.CharField(max_length=255)

    profile_picture = models.ImageField(
        upload_to="profile_pictures/",
        blank=True,
        null=True,
    )

    

    # Academic & Professional Information
# Academic & Professional Information

    affiliation = models.CharField(
        max_length=255,
        default="Addis Ababa Science and Technology University (AASTU)",
    )

    college = models.ForeignKey(
        College,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    center_of_excellence = models.ForeignKey(
        CenterOfExcellence,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    department = models.ForeignKey(
        Department,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    academia = models.CharField(
        max_length=30,
        choices=Academia.choices,
        blank=True,
    )
    academia_other = models.CharField(
    max_length=100,
    blank=True,
)

    academic_title = models.CharField(
        max_length=10,
        choices=AcademicTitle.choices,
        blank=True,
        default="none",
    )

    academic_rank = models.CharField(
        max_length=32,
        choices=AcademicRank.choices,
        blank=True,
        default="none",
    )

    highest_degree = models.CharField(
        max_length=20,
        choices=HighestDegree.choices,
        blank=True,
    )
    highest_degree_other = models.CharField(
    max_length=100,
    blank=True,
)
    orcid_id = models.CharField(
        max_length=19,
        blank=True,
    )

    profession = models.CharField(
        max_length=255,
        blank=True,
    )
    can_upload_datasets = models.BooleanField(default=False)

    upload_permission_revoked = models.BooleanField(default=False)
    

    is_external = models.BooleanField(
        default=False,
    )

    sponsored_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sponsored_users",
    )
    

    bio = models.CharField(
            max_length=300,
            blank=True,
        )

    additional_link = models.URLField(
            blank=True,
        )

        # Visibility & Consent
    profile_visibility = models.CharField(
            max_length=10,
            choices=VISIBILITY_CHOICES,
            default="private",
        )

    terms_accepted = models.BooleanField(default=False)

    terms_accepted_at = models.DateTimeField(
            null=True,
            blank=True,
        )

    interests = models.ManyToManyField(
    "metadata.Category",
    blank=True,
    related_name="users_with_interests",
)

    email_verified = models.BooleanField(default=False)

    interests_completed = models.BooleanField(default=False)

        # --------------------------------------------------
        # ROLE CHECKING
        # --------------------------------------------------

    def has_role(self, *roles):
        return self.roles.filter(role__in=roles).exists()

    # --------------------------------------------------
    # VALIDATION
    # --------------------------------------------------

    def clean(self):
        if self.college_id and self.center_of_excellence_id:
            raise ValidationError(
                "Select either a College or a Center of Excellence, not both."
            )

        if self.department_id:
            if (
                self.college_id
                and self.department.college_id != self.college_id
            ):
                raise ValidationError({
                    "department":
                    "Department does not belong to the selected College."
                })

            if (
                self.center_of_excellence_id
                and self.department.center_of_excellence_id
                != self.center_of_excellence_id
            ):
                raise ValidationError({
                    "department":
                    "Department does not belong to the selected Center of Excellence."
                })

    # --------------------------------------------------
    # PROFILE COMPLETION
    # --------------------------------------------------

    def is_profile_complete(self):
        """
        The profile is complete when all required ORDP profile
        fields have been filled.
        """

        return (
            bool(self.full_name)
            and bool(self.affiliation)
            and bool(self.department_id)
            and bool(self.academia)
            and bool(self.profile_visibility)
            and self.terms_accepted
        )

class UserRole(models.Model):
    class RoleChoice(models.TextChoices):
        PUBLIC = "public"
        REVIEWER = "reviewer"
        ADMIN = "admin"

    profile = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name="roles")
    role = models.CharField(max_length=20, choices=RoleChoice.choices)
    is_primary = models.BooleanField(default=False)
    granted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ["profile", "role"]




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
class EmailVerificationToken(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="email_verification_tokens",
    )
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)

    def is_valid(self):
        from django.utils import timezone
        return not self.is_used and self.expires_at > timezone.now()

    class Meta:
        ordering = ["-created_at"]


class PasswordResetToken(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="password_reset_tokens",
    )
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)

    def is_valid(self):
        from django.utils import timezone
        return not self.is_used and self.expires_at > timezone.now()

    class Meta:
        ordering = ["-created_at"]
