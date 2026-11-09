from django.urls import path

from . import views

urlpatterns = [
    path("me/", views.my_permissions),
    path("my-resources/", views.my_resources),
    path("catalog/", views.catalog),
    path("overview/", views.overview),
    path("users/", views.users),
    path("users/<int:user_id>/", views.user_detail),
    path("users/<int:user_id>/grants/", views.user_grants),
    path("users/<int:user_id>/grants/bulk/", views.user_bulk_grants),
    path("user-grants/<uuid:grant_id>/", views.user_grant_detail),
    path("groups/", views.groups),
    path("group-directory/", views.group_directory),
    path("groups/<uuid:group_id>/", views.group_detail),
    path("groups/<uuid:group_id>/activity/", views.group_activity),
    path("groups/<uuid:group_id>/members/<int:user_id>/", views.group_member),
    path("groups/<uuid:group_id>/grants/", views.group_grants),
    path("group-grants/<uuid:grant_id>/", views.group_grant_detail),
    path("requests/", views.access_requests),
    path("requests/<uuid:request_id>/", views.access_request_detail),
    path("requests/<uuid:request_id>/review/", views.access_request_review),
    path("resources/", views.discover_resources),
    path("resources/<str:resource_type>/<uuid:resource_id>/", views.resource_permissions),
    path("resources/<str:resource_type>/<uuid:resource_id>/matrix/", views.update_resource_matrix),
    path("audit/", views.audit_history),
]
