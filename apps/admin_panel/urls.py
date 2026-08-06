from django.urls import path
from . import views

urlpatterns = [
    path("queue/", views.moderation_queue, name="moderation-queue"),
    path("<uuid:dataset_id>/decide/", views.moderate_dataset, name="moderation-decide"),

    path("content-updates/queue/", views.content_update_queue, name="content-update-queue"),
    path("content-updates/<uuid:update_id>/decide/", views.decide_content_update, name="content-update-decide"),
    path("researcher-requests/queue/", views.researcher_request_queue, name="researcher-request-queue"),
    path("researcher-requests/<uuid:request_id>/decide/", views.decide_researcher_request, name="researcher-request-decide"),
    path("<uuid:dataset_id>/hard-delete/", views.hard_delete_dataset, name="dataset-hard-delete"),

]
