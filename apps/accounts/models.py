from django.conf import settings
from django.db import models
from django.utils import timezone
from datetime import timedelta


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
class UserProfile(models.Model):
    OCCUPATION_CHOICES = [
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
    DEGREE_CHOICES = [
        ("high_school", "High School Diploma"), ("diploma", "Diploma"),
        ("bachelor", "Bachelor's Degree"), ("master", "Master's Degree"),
        ("phd", "PhD"), ("postdoc", "Postdoctoral Fellowship"), ("other", "Other"),
    ]
    VISIBILITY_CHOICES = [
        ("public", "Everyone (Public)"), ("trusted", "Trusted Parties"), ("private", "Only Me (Private)"),
    ]
    # TODO: real values needed from Yodit — doc's list is incomplete (cuts off with "...")
    COLLEGE_CHOICES = [("engineering", "Engineering"), ("applied_science", "Applied Science")]
    COE_CHOICES = []  # TODO: "all 8 CoE" — none named in the doc yet

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")

    # Personal Information
    full_name = models.CharField(max_length=255)
    profile_picture = models.ImageField(upload_to="profile_pictures/", blank=True, null=True)

    # Academic & Professional Information
    affiliation = models.CharField(max_length=255, default="Addis Ababa Science and Technology University (AASTU)")
    college = models.CharField(max_length=50, choices=COLLEGE_CHOICES, blank=True)
    center_of_excellence = models.CharField(max_length=50, choices=COE_CHOICES, blank=True)
    department = models.CharField(max_length=255)  # TODO: depends on college/CoE — needs real dependent-dropdown data
    occupation = models.CharField(max_length=30, choices=OCCUPATION_CHOICES)
    academic_title = models.CharField(max_length=10, choices=ACADEMIC_TITLE_CHOICES, blank=True, default="none")
    highest_degree = models.CharField(max_length=20, choices=DEGREE_CHOICES, blank=True)
    orcid_id = models.CharField(max_length=19, blank=True)  # format: 0000-0002-1825-0097

    # Research Profile
    research_interests = models.JSONField(default=list)  # multi-select, stored as a list of strings
    bio = models.CharField(max_length=300, blank=True)
    additional_link = models.URLField(blank=True)

    # Visibility & Consent
    profile_visibility = models.CharField(max_length=10, choices=VISIBILITY_CHOICES, default="private")
    terms_accepted = models.BooleanField(default=False)
    terms_accepted_at = models.DateTimeField(null=True, blank=True)