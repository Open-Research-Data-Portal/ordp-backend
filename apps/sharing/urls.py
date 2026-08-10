from django.urls import path
from . import views

urlpatterns = [
    path("<uuid:dataset_id>/view/", views.view_dataset, name="view-dataset"),
    path("<uuid:dataset_id>/download/", views.download_dataset, name="download-dataset"),
    path("<uuid:dataset_id>/request-share/", views.request_share_access, name="request-share-access"),
    path("<uuid:dataset_id>/invite-contributor/", views.invite_contributor, name="invite-contributor"),
    path("access-requests/queue/", views.access_request_queue, name="access-request-queue"),
    path("access-requests/<uuid:request_id>/vote/", views.vote_on_access_request, name="access-request-vote"),
]