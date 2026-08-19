from django.urls import path
from . import views
from . import dashboard_views

urlpatterns = [
    path("queue/", views.moderation_queue, name="moderation-queue"),
    path("<uuid:dataset_id>/decide/", views.moderate_dataset, name="moderation-decide"),

    path("content-updates/queue/", views.content_update_queue, name="content-update-queue"),
    path("content-updates/<uuid:update_id>/decide/", views.decide_content_update, name="content-update-decide"),
    path("researcher-requests/queue/", views.researcher_request_queue, name="researcher-request-queue"),
    path("researcher-requests/<uuid:request_id>/decide/", views.decide_researcher_request, name="researcher-request-decide"),
    path("datasets/<uuid:dataset_id>/thumbnail-suggestion/", views.suggest_thumbnail, name="suggest-thumbnail"),
    path("datasets/<uuid:dataset_id>/request-deletion/", views.request_dataset_deletion, name="request-dataset-deletion"),
    path("deletion-requests/<uuid:request_id>/vote/", views.vote_on_deletion_request, name="vote-deletion-request"),
    path("deletion-requests/queue/", views.deletion_request_queue, name="deletion-request-queue"),
    path("deletion-requests/<uuid:request_id>/execute/", views.execute_deletion, name="execute-deletion"),
    path("dashboard/reviewer/overview/", dashboard_views.reviewer_overview, name="reviewer-dashboard-overview"),
    path("dashboard/reviewer/metrics/", dashboard_views.reviewer_metrics, name="reviewer-dashboard-metrics"),
    path("dashboard/reviewer/guidelines/", dashboard_views.reviewer_guidelines, name="reviewer-dashboard-guidelines"),
    path("dashboard/admin/cards/", dashboard_views.admin_cards, name="admin-dashboard-cards"),
    path("dashboard/admin/graphs/", dashboard_views.admin_graphs, name="admin-dashboard-graphs"),
    path("dashboard/admin/audit-log/", dashboard_views.audit_log, name="admin-audit-log"),
    path("dashboard/admin/audit-log/distribution/", dashboard_views.audit_log_distribution, name="admin-audit-log-distribution"),
    path("dashboard/admin/audit-log/summary/", dashboard_views.audit_log_summary, name="admin-audit-log-summary"),
    path("dashboard/admin/audit-log/export/", dashboard_views.audit_log_export, name="admin-audit-log-export"),
    path("users/", dashboard_views.list_users, name="admin-list-users"),
    path("users/create/", dashboard_views.admin_create_user, name="admin-create-user"),
    path("users/<int:user_id>/grant-role/", dashboard_views.admin_grant_role, name="admin-grant-role"),
    path("users/<int:user_id>/revoke-role/", dashboard_views.admin_revoke_role, name="admin-revoke-role"),
    path("users/<int:user_id>/deactivate/", dashboard_views.admin_deactivate_user, name="admin-deactivate-user"),
    path("categories/pending/", dashboard_views.pending_categories, name="pending-categories"),
    path("categories/<uuid:category_id>/decide/", dashboard_views.decide_pending_category, name="decide-pending-category"),
    path("users/<int:user_id>/reactivate/", dashboard_views.admin_reactivate_user, name="admin-reactivate-user"),
    path("share-permissions/<uuid:permission_id>/revoke/", dashboard_views.admin_revoke_share_permission, name="admin-revoke-share-permission"),
    path("languages/pending/", dashboard_views.pending_languages, name="pending-languages"),
    path("languages/<uuid:language_id>/decide/", dashboard_views.decide_pending_language, name="decide-pending-language"),
    path("notifications/broadcast/", dashboard_views.admin_broadcast_notification, name="admin-broadcast-notification"),
]
   
