from . import views
from django.urls import path
from .views import CompleteProfileView 
from .views import (
    LoginView, LogoutView, ProfileView, CustomTokenRefreshView,
    RegisterView, VerifyEmailView, PasswordResetRequestView, PasswordResetConfirmView,
)

urlpatterns = [
    path("register/", RegisterView.as_view(), name="register"),
    path("verify-email/", VerifyEmailView.as_view(), name="verify-email"),
    path("login/", LoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("profile/", ProfileView.as_view(), name="profile"),
    path("profile/complete/", CompleteProfileView.as_view(), name="complete-profile"),
    path("refresh/", CustomTokenRefreshView.as_view(), name="token_refresh"),
    path("password-reset/", PasswordResetRequestView.as_view(), name="password_reset_request"),
    path("password-reset/confirm/", PasswordResetConfirmView.as_view(), name="password_reset_confirm"),
    path("profile/interests/other/", views.add_other_interest, name="add-other-interest")
]