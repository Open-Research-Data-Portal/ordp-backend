from django.urls import path
from . import views

urlpatterns = [
    path("upload/init/", views.init_upload, name="upload-init"),
    path("upload/chunk/<str:upload_session_id>/", views.upload_chunk, name="upload-chunk"),
    path("upload/complete/<str:upload_session_id>/", views.complete_upload, name="upload-complete"),
    path("<uuid:dataset_id>/submit/", views.accept_terms_and_submit, name="dataset-submit"),
    path("mine/", views.my_datasets, name="my-datasets"),
    path("<uuid:dataset_id>/", views.dataset_detail, name="dataset-detail"),
    path("<uuid:dataset_id>/update/", views.update_dataset, name="dataset-update"),
    path("<uuid:dataset_id>/propose-revision/", views.propose_revision, name="propose-revision"),
    path("revisions/<uuid:revision_id>/comparison/", views.revision_comparison, name="revision-comparison"),
    path("revisions/<uuid:revision_id>/decide/", views.decide_revision, name="revision-decide"),
    path("<uuid:dataset_id>/versions/", views.dataset_versions, name="dataset-versions"),
    # path("<uuid:dataset_id>/delete/", views.soft_delete_dataset, name="dataset-delete"),
]