from django.urls import path
from . import views
from . import dashboard_views

urlpatterns = [
    path("upload/init/", views.init_upload, name="upload-init"),
    path("upload/chunk/<str:upload_session_id>/", views.upload_chunk, name="upload-chunk"),
    path("upload/complete/<str:upload_session_id>/", views.complete_upload, name="upload-complete"),
    path("<uuid:dataset_id>/submit/", views.accept_terms_and_submit, name="dataset-submit"),
    path("mine/", views.my_datasets, name="my-datasets"),
    path("<uuid:dataset_id>/", views.dataset_detail, name="dataset-detail"),
    path("<uuid:dataset_id>/update/", views.update_dataset, name="dataset-update"),
    path("<uuid:dataset_id>/propose-revision/", views.propose_revision, name="propose-revision"),
    path("revisions/<uuid:revision_id>/decide/", views.decide_revision, name="revision-decide"),
    path("<uuid:dataset_id>/versions/", views.dataset_versions, name="dataset-versions"),
    path("<uuid:dataset_id>/thumbnail/", views.upload_thumbnail, name="upload-thumbnail"),
    path("<uuid:dataset_id>/bookmark/", views.toggle_bookmark, name="toggle-bookmark"),
    path("bookmarks/", views.my_bookmarks, name="my-bookmarks"),
    path("<uuid:dataset_id>/contributors/<uuid:contributor_id>/", views.update_contributor_type, name="update-contributor-type"),
    path("<uuid:dataset_id>/delete/", views.soft_delete_dataset, name="dataset-delete"),
    path("dashboard/stats/", dashboard_views.dashboard_stats, name="dashboard-stats"),
    path("dashboard/recent-activity/", dashboard_views.recent_activity, name="dashboard-recent-activity"),
    path("dashboard/feed/", dashboard_views.feed, name="dashboard-feed"),
    path("dashboard/my-contributions/", dashboard_views.my_contributions, name="dashboard-my-contributions"),
    path("<uuid:dataset_id>/contributors/<uuid:contributor_id>/remove/", views.remove_contributor, name="remove-contributor"),
    path("<uuid:dataset_id>/request-revision-permission/", views.request_revision_permission, name="request-revision-permission"),
    path("<uuid:dataset_id>/watch/", views.toggle_watch, name="toggle-watch"),
    path("content-updates/<uuid:update_id>/comparison/", views.content_update_comparison, name="content-update-comparison"),
]
    
