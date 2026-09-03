from django.urls import path
from . import views

urlpatterns = [
    path("<uuid:dataset_id>/attach/", views.attach_metadata, name="attach-metadata"),
    path("categories/", views.list_categories, name="list-categories"),
    path("categories/interests/", views.list_interest_categories, name="list-interest-categories"),
    path("languages/", views.list_languages, name="list-languages"),
    path("<uuid:dataset_id>/languages/", views.set_dataset_languages, name="set-dataset-languages"),
    path("characteristics/", views.list_characteristics, name="list-characteristics"),
    path("<uuid:dataset_id>/characteristics/", views.set_dataset_characteristics, name="set-dataset-characteristics"),
]