import random
import re
import hashlib
from django.conf import settings
from django.core.mail import send_mail

from .models import User

INSTITUTIONAL_DOMAIN = "aastu.edu.et"


def detect_tier(email: str) -> str:
    domain = email.split("@")[-1].lower()
    return User.Tier.INSTITUTIONAL if domain == INSTITUTIONAL_DOMAIN else User.Tier.EXTERNAL


def generate_username(full_name: str) -> str:
    base = re.sub(r"[^a-z]", "", full_name.lower().replace(" ", "."))
    base = base or "user"
    username = base
    suffix = 1
    while User.objects.filter(username=username).exists():
        suffix += 1
        username = f"{base}{suffix}"
    return username


def generate_otp() -> str:
    return f"{random.randint(0, 999999):06d}"


def hash_otp(otp_code: str) -> str:
    return hashlib.sha256(otp_code.encode()).hexdigest()


def send_otp_email(user: User, otp_code: str):
    send_mail(
        subject="Your ORDP account — one-time password",
        message=(
            f"Hello {user.full_name},\n\n"
            f"Your username is: {user.username}\n"
            f"Your one-time password is: {otp_code}\n"
            f"This code expires in 15 minutes.\n\n"
            f"Enter it on the platform to verify your account and set your password."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
    )