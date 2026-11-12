
"""Provider-independent ORDP PID lookup, JSON resolver and relationship API."""

from django.db.models import Q
from django.shortcuts import get_object_or_404

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from apps.datasets.models import (
    Dataset,
    DatasetRelease,
    ResourceIdentifier,
    ResourceRelationship,
)
from apps.datasets.permissions import can_access_private_dataset


def _can_read(user, dataset):
    """Return whether the requester may discover this dataset and its releases."""
    if not dataset.is_active or dataset.status != Dataset.Status.PUBLISHED:
        return False

    authenticated = bool(user and user.is_authenticated)

    if dataset.is_archived:
        return authenticated and (
            dataset.is_owned_by(user)
            or getattr(
                getattr(user, "profile", None),
                "has_role",
                lambda *_: False,
            )("reviewer", "admin")
        )

    if not authenticated:
        return dataset.visibility == Dataset.Visibility.PUBLIC

    if dataset.visibility == Dataset.Visibility.PUBLIC:
        return True

    if dataset.visibility == Dataset.Visibility.PRIVATE:
        return can_access_private_dataset(user, dataset)

    from apps.sharing.services import user_can_access_dataset

    return user_can_access_dataset(user, dataset)


def _resolver_url(identifier):
    """Return the stable ORDP resolver URL without relying on DOI registration."""
    if identifier.landing_page_url:
        return identifier.landing_page_url

    from django.conf import settings

    base_url = getattr(settings, "ORDP_PUBLIC_BASE_URL", "").rstrip("/")
    if base_url and identifier.identifier_value:
        return f"{base_url}/id/{identifier.identifier_value}"

    if identifier.identifier_value:
        return f"/id/{identifier.identifier_value}"

    return None


def _identifier_data(identifier):
    return {
        "id": str(identifier.pk),
        "scheme": identifier.scheme,
        "role": identifier.role,
        "identifier": identifier.identifier_value,
        "status": identifier.lifecycle_status,
        "registration_status": identifier.registration_status,
        "resolver_url": _resolver_url(identifier),
        "doi_url": identifier.official_doi_link,
        "created_at": identifier.created_at,
    }


def _registered_doi(*, dataset=None, release=None):
    """Return a DOI only if the provider registration actually succeeded."""
    query = {
        "scheme": ResourceIdentifier.Scheme.DOI,
        "registration_status": ResourceIdentifier.RegistrationStatus.REGISTERED,
    }

    if release is not None:
        query.update(
            role=ResourceIdentifier.Role.VERSION,
            release=release,
        )
    else:
        query.update(
            role=ResourceIdentifier.Role.CONCEPT,
            dataset=dataset,
        )

    return ResourceIdentifier.objects.filter(**query).first()


def _citation_with_doi(citation_snapshot, *, dataset=None, release=None):
    """Preserve the citation snapshot and add only a genuinely registered DOI."""
    citation = dict(citation_snapshot or {})
    doi_identifier = _registered_doi(dataset=dataset, release=release)

    citation["doi"] = (
        doi_identifier.identifier_value if doi_identifier else None
    )

    return citation


def _release_file_data(release, dataset):
    """Return file metadata and API download routes, never storage URLs."""
    files = []

    for entry in release.files.select_related("stored_object").order_by(
        "display_order", "id"
    ):
        stored_object = entry.stored_object

        files.append({
            "filename": stored_object.original_filename,
            "file_size": stored_object.file_size,
            "file_type": stored_object.file_type,
            "checksum_sha256": stored_object.checksum_sha256,
            "download_url": (
                f"/api/datasets/{dataset.pk}/releases/{release.pk}/files/"
                f"{entry.logical_file_id}/download/"
            ),
        })

    return files


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def release_identifiers(request, dataset_id, release_id):
    """List identifiers belonging to one accessible release."""
    dataset = get_object_or_404(
        Dataset,
        pk=dataset_id,
        is_active=True,
    )

    if not _can_read(request.user, dataset):
        return Response({"detail": "Not found."}, status=404)

    release = get_object_or_404(
        DatasetRelease,
        pk=release_id,
        dataset=dataset,
    )

    identifiers = ResourceIdentifier.objects.filter(
        release=release,
    ).order_by("scheme", "created_at")

    return Response({
        "dataset_id": str(dataset.pk),
        "release_id": str(release.pk),
        "identifiers": [
            _identifier_data(identifier)
            for identifier in identifiers
        ],
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def resource_relationships(request, dataset_id):
    """List identifier relationships belonging to an accessible dataset."""
    dataset = get_object_or_404(
        Dataset,
        pk=dataset_id,
        is_active=True,
    )

    if not _can_read(request.user, dataset):
        return Response({"detail": "Not found."}, status=404)

    rows = ResourceRelationship.objects.filter(
        Q(subject_identifier__dataset=dataset)
        | Q(subject_identifier__release__dataset=dataset)
        | Q(related_identifier__dataset=dataset)
        | Q(related_identifier__release__dataset=dataset)
    ).select_related(
        "subject_identifier",
        "related_identifier",
        "subject_identifier__dataset",
        "subject_identifier__release",
        "related_identifier__dataset",
        "related_identifier__release",
    ).order_by("created_at", "id")

    return Response([
        {
            "id": str(row.pk),
            "relationship_type": row.relationship_type,
            "subject": _identifier_data(row.subject_identifier),
            "related": _identifier_data(row.related_identifier),
            "created_at": row.created_at,
        }
        for row in rows
    ])


@api_view(["GET"])
@permission_classes([AllowAny])
def resolve_pid(request, pid):
    """
    Resolve an ORDP PID to JSON.

    Both /id/{pid}/ and /api/datasets/id/{pid}/ use this view.
    No HTML template or frontend application is required.
    """
    identifier = get_object_or_404(
        ResourceIdentifier.objects.select_related(
            "dataset",
            "release",
            "release__dataset",
        ),
        scheme=ResourceIdentifier.Scheme.ORDP,
        identifier_value=pid,
    )

    if identifier.role == ResourceIdentifier.Role.CONCEPT:
        dataset = identifier.dataset
    elif identifier.role == ResourceIdentifier.Role.VERSION:
        if identifier.release is None:
            return Response({"detail": "Not found."}, status=404)
        dataset = identifier.release.dataset
    else:
        return Response({"detail": "Not found."}, status=404)

    if not _can_read(request.user, dataset):
        return Response({"detail": "Not found."}, status=404)

    # Concept PID: dataset metadata and published release history.
    if identifier.role == ResourceIdentifier.Role.CONCEPT:
        releases = list(
            dataset.releases.filter(
                status__in=[
                    DatasetRelease.Status.PUBLISHED,
                    DatasetRelease.Status.WITHDRAWN,
                ]
            ).order_by("-major", "-minor", "-patch")
        )

        latest = next(
            (
                release
                for release in releases
                if release.status == DatasetRelease.Status.PUBLISHED
            ),
            None,
        )

        release_history = []

        for release in releases:
            version_identifier = ResourceIdentifier.objects.filter(
                scheme=ResourceIdentifier.Scheme.ORDP,
                role=ResourceIdentifier.Role.VERSION,
                release=release,
            ).first()

            release_history.append({
                "id": str(release.pk),
                "version": release.version,
                "status": release.status,
                "published_at": release.published_at,
                "pid": (
                    version_identifier.identifier_value
                    if version_identifier
                    else None
                ),
                "resolver_url": (
                    _resolver_url(version_identifier)
                    if version_identifier
                    else None
                ),
            })

        latest_pid = None

        if latest is not None:
            latest_identifier = ResourceIdentifier.objects.filter(
                scheme=ResourceIdentifier.Scheme.ORDP,
                role=ResourceIdentifier.Role.VERSION,
                release=latest,
            ).first()

            if latest_identifier:
                latest_pid = latest_identifier.identifier_value

        data = {
            "resource_type": "dataset",
            "pid": _identifier_data(identifier),
            "dataset_id": str(dataset.pk),
            "title": dataset.title,
            "citation": _citation_with_doi(
                latest.citation_snapshot if latest else {},
                dataset=dataset,
            ),
            "metadata_snapshot": (
                latest.metadata_snapshot if latest else None
            ),
            "latest_published_release": (
                {
                    "id": str(latest.pk),
                    "version": latest.version,
                    "pid": latest_pid,
                }
                if latest
                else None
            ),
            "release_history": release_history,
            "withdrawn": False,
        }

        if latest is not None:
            data["release_files"] = _release_file_data(latest, dataset)
        else:
            data["release_files"] = []

    # Version PID: resolve the exact release, never the latest release.
    else:
        release = identifier.release

        data = {
            "resource_type": "release",
            "pid": _identifier_data(identifier),
            "dataset_id": str(dataset.pk),
            "release_id": str(release.pk),
            "title": (
                (release.metadata_snapshot or {}).get("title")
                or dataset.title
            ),
            "version": release.version,
            "status": release.status,
            "published_at": release.published_at,
            "citation": _citation_with_doi(
                release.citation_snapshot,
                release=release,
            ),
            "metadata_snapshot": release.metadata_snapshot,
            "withdrawn": (
                release.status == DatasetRelease.Status.WITHDRAWN
            ),
            "release_files": _release_file_data(release, dataset),
        }

    data["canonical_pid_url"] = _resolver_url(identifier)

    # Backend-only contract: always return JSON. The frontend is responsible
    # for displaying the dataset/release landing page.
    return Response(data)
