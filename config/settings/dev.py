from .base import *

DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

# For local development without an external SMTP server, set in .env:
# EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
EMAIL_BACKEND = config("EMAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend")
