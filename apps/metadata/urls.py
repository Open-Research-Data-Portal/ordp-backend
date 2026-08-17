from django.urls import path
from . import views

urlpatterns = [
    path("<uuid:dataset_id>/attach/", views.attach_metadata, name="attach-metadata"),
    path("categories/", views.list_categories, name="list-categories"),
    path("subjects/", views.list_subjects, name="list-subjects"),
]