from django.urls import path
from . import views

urlpatterns = [
    path("<uuid:dataset_id>/download/", views.download_dataset, name="download-dataset"),
    path("<uuid:dataset_id>/request-share/", views.request_share_access, name="request-share-access"),
    path("<uuid:dataset_id>/share-with/", views.share_with_user, name="share-with-user"),
    path("access-requests/queue/", views.access_request_queue, name="access-request-queue"),
    path("access-requests/<uuid:request_id>/vote/", views.vote_on_access_request, name="access-request-vote"),
    path("access-requests/<uuid:request_id>/owner-decision/", views.owner_decide_access_request, name="owner-decide-access-request"),
    path("<uuid:dataset_id>/invite-coauthor/", views.invite_coauthor, name="invite-coauthor"),
    path("<uuid:dataset_id>/invite-contributor/", views.invite_contributor, name="invite-contributor"),
    path("invitations/<str:token>/", views.view_invitation, name="view-invitation"),
    path("<uuid:dataset_id>/invitations/<uuid:invitation_id>/revoke/", views.revoke_invitation, name="revoke-invitation"),
]