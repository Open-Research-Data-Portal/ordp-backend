from django.urls import path
from . import views

urlpatterns = [
    path("<uuid:dataset_id>/view/", views.view_dataset, name="view-dataset"),
    path("<uuid:dataset_id>/download/", views.download_dataset, name="download-dataset"),
    path("<uuid:dataset_id>/request-share/", views.request_share_access, name="request-share-access"),
    path("access-requests/queue/", views.access_request_queue, name="access-request-queue"),
    path("access-requests/<uuid:request_id>/vote/", views.vote_on_access_request, name="access-request-vote"),
    path("<uuid:dataset_id>/invite-coauthor/", views.invite_coauthor, name="invite-coauthor"),
    path("<uuid:dataset_id>/invite-contributor/", views.invite_contributor, name="invite-contributor"),
    path("invitations/<str:token>/", views.view_invitation, name="view-invitation"),
    path("<uuid:dataset_id>/invitations/<uuid:invitation_id>/revoke/", views.revoke_invitation, name="revoke-invitation")
    
]