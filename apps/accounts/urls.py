from django.urls import path
from .views import LoginView, LogoutView, ProfileView, CustomTokenRefreshView

urlpatterns = [
    path("login/", LoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("profile/", ProfileView.as_view(), name="profile"),
    path("refresh/", CustomTokenRefreshView.as_view(), name="token_refresh"),
]