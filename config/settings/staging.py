from .base import *

DEBUG = False
ALLOWED_HOSTS = []  # fill in with staging domain once we have one
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"