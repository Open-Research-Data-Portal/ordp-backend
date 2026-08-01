from django.urls import path
from . import views

urlpatterns = [
    path("bell/", views.bell_notifications, name="notifications-bell"),
    path("history/", views.notification_history, name="notifications-history"),
    path("<uuid:notification_id>/read/", views.mark_notification_read, name="notification-read"),
]