from django.urls import path
from . import views

urlpatterns = [
    path("queue/", views.moderation_queue, name="moderation-queue"),
    path("<uuid:dataset_id>/decide/", views.moderate_dataset, name="moderation-decide"),
]