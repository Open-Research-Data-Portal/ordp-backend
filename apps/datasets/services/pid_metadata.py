"""Provider-independent DataCite metadata preparation for ORDP PID records."""
from django.conf import settings
from django.core.exceptions import ValidationError

from apps.datasets.models import DatasetRelease, ResourceIdentifier


_DATACITE_RELATION_TYPES = {
    "hasVersion": "HasVersion",
    "isVersionOf": "IsVersionOf",
    "isNewVersionOf": "IsNewVersionOf",
    "isPreviousVersionOf": "IsPreviousVersionOf",
    "isPartOf": "IsPartOf",
    "isDerivedFrom": "IsDerivedFrom",
    "isSupplementTo": "IsSupplementTo",
    "isSupplementedBy": "IsSupplementedBy",
    "isIdenticalTo": "IsIdenticalTo",
}


def _release_for_identifier(identifier):
    if identifier.role == ResourceIdentifier.Role.VERSION:
        release = identifier.release
        if release.status != DatasetRelease.Status.PUBLISHED:
            raise ValidationError("A version DOI can only describe a published release.")
        return release

    release = DatasetRelease.objects.filter(
        dataset_id=identifier.dataset_id,
        status=DatasetRelease.Status.PUBLISHED,
    ).order_by("-major", "-minor", "-patch").first()
    if release is None:
        raise ValidationError("The dataset has no published release to describe.")
    return release


def build_datacite_metadata(identifier):
    """Build DataCite-compatible attributes without contacting a DOI provider.

    DOI minting is deliberately separate: the provider assigns the DOI value.
    License and citation fields are intentionally omitted from this metadata
    preparation step until those features are implemented in the project.
    """
    release = _release_for_identifier(identifier)
    snapshot = release.metadata_snapshot or {}
    creator = snapshot.get("creator") or {}
    creator_name = creator.get("name") if isinstance(creator, dict) else None
    title = snapshot.get("title")
    publisher = getattr(settings, "ORDP_DATACITE_PUBLISHER", "")

    issues = []
    if not isinstance(title, str) or not title.strip():
        issues.append("A release title is required.")
    if not isinstance(creator_name, str) or not creator_name.strip():
        issues.append("At least one creator name is required.")
    if not isinstance(publisher, str) or not publisher.strip():
        issues.append("ORDP_DATACITE_PUBLISHER must be configured.")
    if not identifier.landing_page_url:
        issues.append("A public landing-page URL must be configured on the identifier.")
    if issues:
        raise ValidationError(issues)

    creator_item = {"name": creator_name.strip()}
    affiliation = creator.get("affiliation") if isinstance(creator, dict) else None
    if isinstance(affiliation, str) and affiliation.strip():
        creator_item["affiliation"] = [{"name": affiliation.strip()}]

    attributes = {
        "url": identifier.landing_page_url,
        "creators": [creator_item],
        "titles": [{"title": title.strip()}],
        "publisher": publisher.strip(),
        "publicationYear": release.published_at.year,
        "types": {"resourceTypeGeneral": "Dataset"},
        "version": release.version,
    }
    description = snapshot.get("description")
    if isinstance(description, str) and description.strip():
        attributes["descriptions"] = [{
            "description": description.strip(),
            "descriptionType": "Abstract",
        }]
    keywords = snapshot.get("keywords")
    if isinstance(keywords, list):
        subjects = [
            {"subject": value.strip()}
            for value in keywords
            if isinstance(value, str) and value.strip()
        ]
        if subjects:
            attributes["subjects"] = subjects

    related = []
    for relation in identifier.outgoing_relationships.select_related("related_identifier"):
        related_identifier = relation.related_identifier
        if (
            related_identifier.scheme == ResourceIdentifier.Scheme.DOI
            and related_identifier.registration_status == ResourceIdentifier.RegistrationStatus.REGISTERED
            and related_identifier.identifier_value
        ):
            related.append({
                "relatedIdentifier": related_identifier.identifier_value,
                "relatedIdentifierType": "DOI",
                "relationType": _DATACITE_RELATION_TYPES[relation.relationship_type],
            })
    if related:
        attributes["relatedIdentifiers"] = related
    return attributes
