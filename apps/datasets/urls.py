from django.urls import path
from . import views
from . import dashboard_views

urlpatterns = [
    path("upload/init/", views.init_upload, name="upload-init"),
    path(
        "<uuid:dataset_id>/upload/init/",
        views.init_existing_draft_upload,
        name="existing-draft-upload-init",
    ),
    path("upload/chunk/<str:upload_session_id>/", views.upload_chunk, name="upload-chunk"),
    path("upload/complete/<str:upload_session_id>/", views.complete_upload, name="upload-complete"),
    path("<uuid:dataset_id>/submit/", views.accept_terms_and_submit, name="dataset-submit"),
    path("mine/", views.my_datasets, name="my-datasets"),
    path("archived/", views.archived_datasets, name="archived-datasets"),
    path("<uuid:dataset_id>/archive/", views.request_archive_dataset, name="request-archive-dataset"),
    path("<uuid:dataset_id>/unarchive/", views.request_unarchive_dataset, name="request-unarchive-dataset"),
    path("<uuid:dataset_id>/reviewers/", views.dataset_reviewers, name="dataset-reviewers"),
    path("<uuid:dataset_id>/identifiers/", views.dataset_identifiers, name="dataset-identifiers"),
    path("<uuid:dataset_id>/", views.dataset_detail, name="dataset-detail"),
    path("<uuid:dataset_id>/update/", views.update_dataset, name="dataset-update"),
    path("<uuid:dataset_id>/propose-revision/", views.propose_revision, name="propose-revision"),
    path("revision-requests/<uuid:request_id>/decide/", views.decide_revision_request, name="revision-request-decide"),
    path("<uuid:dataset_id>/versions/", views.dataset_versions, name="dataset-versions"),
    path("<uuid:dataset_id>/thumbnail/", views.upload_thumbnail, name="upload-thumbnail"), 
    path( 
    "<uuid:dataset_id>/thumbnail/url/", 
    views.get_thumbnail_url, 
    name="thumbnail-url", 
),   
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
    path(
    "upload/prepare/<str:upload_session_id>/",
    views.prepare_upload,
    name="upload-prepare",
),
    
   
]
    

# ORDP V2 proposal and immutable-release API
from . import v2_views

urlpatterns += [
    path("<uuid:dataset_id>/identifiers/<uuid:identifier_id>/datacite-metadata/", v2_views.pid_metadata_view, name="v2-pid-datacite-metadata"),
    path("<uuid:dataset_id>/proposals/", v2_views.create_proposal_view, name="v2-proposal-create"),
    path("<uuid:dataset_id>/proposals/list/", v2_views.dataset_proposals_view, name="v2-proposal-list"),
    path("proposals/<uuid:proposal_id>/", v2_views.proposal_detail_view, name="v2-proposal-detail"),
    path("proposals/<uuid:proposal_id>/cancel/", v2_views.cancel_proposal_view, name="v2-proposal-cancel"),
    path("proposals/<uuid:proposal_id>/submit/", v2_views.submit_proposal_view, name="v2-proposal-submit"),
    path("proposals/<uuid:proposal_id>/revise/", v2_views.revise_requested_proposal_view, name="v2-proposal-revise"),
    path("proposals/<uuid:proposal_id>/edit/", v2_views.edit_proposal_view, name="v2-proposal-edit"),
    path("proposals/<uuid:proposal_id>/request-changes/", v2_views.request_changes_proposal_view, name="v2-proposal-request-changes"),
    path("proposals/<uuid:proposal_id>/vote/", v2_views.vote_proposal_view, name="v2-proposal-vote"),
    path("proposals/<uuid:proposal_id>/publish/", v2_views.publish_proposal_view, name="v2-proposal-publish"),
    path("<uuid:dataset_id>/proposals/<uuid:proposal_id>/files/", v2_views.add_proposal_file_view, name="v2-proposal-file-add"),
    path("<uuid:dataset_id>/proposals/<uuid:proposal_id>/upload/init/", v2_views.init_proposal_upload_view, name="v2-proposal-upload-init"),
    path("<uuid:dataset_id>/proposals/<uuid:proposal_id>/upload/complete/<str:upload_session_id>/", v2_views.complete_proposal_upload_view, name="v2-proposal-upload-complete"),
    path("<uuid:dataset_id>/proposals/<uuid:proposal_id>/files/<uuid:logical_file_id>/", v2_views.replace_proposal_file_view, name="v2-proposal-file-replace"),
    path("<uuid:dataset_id>/proposals/<uuid:proposal_id>/files/<uuid:logical_file_id>/remove/", v2_views.remove_proposal_file_view, name="v2-proposal-file-remove"),
    path("<uuid:dataset_id>/metadata-audit/", v2_views.metadata_audit_history_view, name="v2-metadata-audit-history"),
    path("<uuid:dataset_id>/releases/", v2_views.release_history_view, name="v2-release-history"),
    path("<uuid:dataset_id>/releases/<uuid:release_id>/", v2_views.release_detail_view, name="v2-release-detail"),
    path("<uuid:dataset_id>/releases/<uuid:release_id>/files/<uuid:logical_file_id>/download/", v2_views.release_file_download_view, name="v2-release-file-download"),
]

# ORDP-owned persistent identifier API and resolver
from . import pid_views

urlpatterns += [
    path("<uuid:dataset_id>/releases/<uuid:release_id>/identifiers/", pid_views.release_identifiers, name="release-identifiers"),
    path("<uuid:dataset_id>/relationships/", pid_views.resource_relationships, name="dataset-resource-relationships"),
    path("id/<str:pid>/", pid_views.resolve_pid, name="ordp-pid-resolver"),
]
