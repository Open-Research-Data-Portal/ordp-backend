from django.urls import path
from . import views

urlpatterns = [
    path("datasets/", views.list_datasets, name="list-datasets"),
    path("discover/", views.discover, name="discover-feed"),
]