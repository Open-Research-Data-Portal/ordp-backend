import json
from typing import Any, Dict, List, Optional
from django.core.exceptions import ObjectDoesNotExist

from apps.datasets.models import Dataset, Contributor


CURRENT_SNAPSHOT_SCHEMA_VERSION = 1


def _extract_creator(dataset: Dataset, profiles=None) -> Optional[Dict[str, Any]]:
    """Extract primary creator details from the dataset owner.

    Excludes internal account information (user database ID, username, email)
    to protect researcher privacy in public metadata snapshots.
    Preserves creator full name and affiliation. If full name is unavailable,
    uses an empty string rather than exposing the username as a fallback.
    """
    owner = dataset.owner
    if not owner:
        return None

    profile = (
        profiles.get(owner.id)
        if profiles is not None
        else getattr(owner, "profile", None)
    )
    full_name = getattr(profile, "full_name", "") if profile else ""
    affiliation = getattr(profile, "affiliation", "") if profile else ""

    return {
        "name": full_name or "",
        "affiliation": affiliation or "",
    }


def _extract_contributors(dataset: Dataset, profiles=None) -> List[Dict[str, Any]]:
    """Extract contributors ordered strictly by 'order', then 'id'.

    Preserves academic attribution ordering while strictly omitting
    internal database identifiers (id), portal permissions (edit/view),
    and invited emails.
    """
    contributors_qs = dataset.contributors.all().select_related("user").order_by("order", "id")
    result = []

    for contrib in contributors_qs:
        user = contrib.user
        profile = (
            profiles.get(user.id)
            if user and profiles is not None
            else getattr(user, "profile", None) if user else None
        )
        user_affiliation = getattr(profile, "affiliation", "") if profile else ""
        user_full_name = getattr(profile, "full_name", "") if profile else ""

        display_name = contrib.name.strip() if contrib.name else user_full_name

        result.append({
            "name": display_name or "",
            "contributor_type": contrib.contributor_type,
            "order": contrib.order,
            "affiliation": user_affiliation or "",
        })

    return result


def _extract_category(metadata) -> Optional[Dict[str, Any]]:
    """Extract category descriptive details."""
    category = getattr(metadata, "category", None)
    if not category:
        return None

    return {
        "id": str(category.id),
        "name": category.name,
        "description": category.description or "",
    }


def lock_metadata_snapshot_sources(dataset: Dataset):
    """Lock rows that supply snapshot content while the Dataset row is locked."""
    from apps.accounts.models import UserProfile
    from apps.metadata.models import (
        Category,
        DatasetCharacteristic,
        Keyword,
        Language,
        Metadata,
    )

    metadata = (
        Metadata.objects.select_for_update()
        .filter(dataset_id=dataset.pk)
        .first()
    )
    if metadata:
        Category.objects.select_for_update().filter(
            pk=metadata.category_id
        ).exists()
        for related_terms in (
            metadata.keywords,
            metadata.languages,
            metadata.characteristics,
        ):
            list(
                related_terms.select_for_update(of=("self",)).order_by("pk")
            )
    contributors = list(
        Contributor.objects.select_for_update()
        .filter(dataset_id=dataset.pk)
        .select_related("user")
        .select_for_update(of=("self",))
        .order_by("pk")
    )
    profile_user_ids = {dataset.owner_id}
    profile_user_ids.update(
        contributor.user_id
        for contributor in contributors
        if contributor.user_id is not None
    )
    profiles = {
        profile.user_id: profile
        for profile in UserProfile.objects.select_for_update()
        .filter(user_id__in=profile_user_ids)
        .order_by("pk")
    }
    return metadata, profiles


def generate_metadata_snapshot(dataset: Dataset, *, metadata_override=None, profiles=None) -> Dict[str, Any]:
    """Generate a deterministic, JSON-serializable dictionary of descriptive metadata.

    Excludes all operational fields (visibility, review status, view/download counts,
    active/archived state, assigned reviewers, internal permissions, terms acceptance).

    Ensures determinism by sorting M2M collections (keywords, languages, characteristics)
    and strictly preserving authorial contributor ordering.
    """
    if metadata_override is not None:
        metadata = metadata_override
    else:
        try:
            metadata = dataset.metadata
        except (ObjectDoesNotExist, AttributeError):
            metadata = None

    # M2M collections: strictly sorted for determinism
    keywords: List[str] = []
    languages: List[str] = []
    characteristics: List[str] = []

    if metadata:
        keywords = sorted(list(metadata.keywords.values_list("word", flat=True)))
        languages = sorted(list(metadata.languages.values_list("name", flat=True)))
        characteristics = sorted(list(metadata.characteristics.values_list("name", flat=True)))

    snapshot: Dict[str, Any] = {
        "schema_version": CURRENT_SNAPSHOT_SCHEMA_VERSION,
        "title": dataset.title or "",
        "description": (metadata.description if metadata else "") or "",
        "category": _extract_category(metadata),
        "keywords": keywords,
        "languages": languages,
        "characteristics": characteristics,
        "creator": _extract_creator(dataset, profiles),
        "contributors": _extract_contributors(dataset, profiles),
        "coverage": {
            "geographic": (metadata.geographic_coverage if metadata else "") or "",
            "temporal": (metadata.temporal_coverage if metadata else "") or "",
        },
        "methodology": {
            "collection_method": (metadata.collection_method if metadata else "") or "",
            "data_preprocessing": (metadata.data_preprocessing if metadata else "") or "",
            "instances_represent": (metadata.instances_represent if metadata else "") or "",
            "recommended_data_splits": (metadata.recommended_data_splits if metadata else "") or "",
            "sensitive_data_disclosure": (metadata.sensitive_data_disclosure if metadata else "") or "",
            "has_header": metadata.has_header if metadata else None,
            "has_missing_values": metadata.has_missing_values if metadata else None,
        },
        "citation": {
            # NOTE: 'doi_citation' is legacy citation text (e.g. citing a related paper).
            # This is NOT a verified or minted DOI assignment for this dataset or release.
            "doi_citation": (metadata.doi_citation if metadata else "") or "",
            "citation_notes": (metadata.citation_notes if metadata else "") or "",
            "collaborators_text": (metadata.collaborators_text if metadata else "") or "",
            "sponsor_or_grant": (metadata.sponsor_or_grant if metadata else "") or "",
        },
        "resources": {
            "related_resources": (metadata.related_resources if metadata else "") or "",
        },
    }

    # Verify JSON serializability before returning
    json.dumps(snapshot)
    return snapshot


def citation_from_metadata_snapshot(metadata_snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Generate citation snapshot directly from a metadata snapshot dictionary.
    Guarantees that citation snapshot is identical to and derived from the same snapshot.
    """
    citation = metadata_snapshot.get("citation", {})
    creator = metadata_snapshot.get("creator") or {}
    snapshot = {
        "title": metadata_snapshot.get("title", "") or "",
        "creator_name": creator.get("name", "") or "",
        # Legacy citation text only; not an official DOI assignment.
        "doi_citation": citation.get("doi_citation", "") or "",
        "citation_notes": citation.get("citation_notes", "") or "",
        "collaborators_text": citation.get("collaborators_text", "") or "",
        "sponsor_or_grant": citation.get("sponsor_or_grant", "") or "",
    }
    json.dumps(snapshot)
    return snapshot


def generate_citation_snapshot(dataset_or_snapshot: Any) -> Dict[str, Any]:
    """Generate the citation snapshot dictionary for DatasetRelease.citation_snapshot.

    NOTE: The 'doi_citation' field preserved here is legacy free-text citation
    information provided by researchers (such as citing a related journal publication).
    It is NOT an official or verified DOI assignment for this dataset or release.

    Accepts either a Dataset instance or a pre-generated metadata snapshot dict.
    Derives citation strictly from the metadata snapshot ensuring consistency.
    """
    if isinstance(dataset_or_snapshot, dict):
        return citation_from_metadata_snapshot(dataset_or_snapshot)
    meta = generate_metadata_snapshot(dataset_or_snapshot)
    return citation_from_metadata_snapshot(meta)


ALLOWED_LICENSE_KEYS = {"name", "identifier", "url", "custom_terms"}


def generate_license_snapshot(license_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Generate the license snapshot dictionary for DatasetRelease.license_snapshot.

    Enforces strict input validation:
    - If None or empty dict, returns {}
    - Must be a dictionary
    - Rejects unknown keys outside ALLOWED_LICENSE_KEYS
    - Values must be strings or None
    - If URL is non-empty, must start with http:// or https://
    """
    if license_data is None:
        return {}
    if not isinstance(license_data, dict):
        raise ValueError(
            f"license_data must be a dictionary, got {type(license_data).__name__}: {license_data!r}"
        )
    if not license_data:
        return {}

    unknown = set(license_data.keys()) - ALLOWED_LICENSE_KEYS
    if unknown:
        raise ValueError(
            f"Unknown license fields: {sorted(unknown)}. Allowed: {sorted(ALLOWED_LICENSE_KEYS)}"
        )

    snapshot = {}
    for key in ("name", "identifier", "url", "custom_terms"):
        val = license_data.get(key, "")
        if val is None:
            val = ""
        elif not isinstance(val, str):
            raise ValueError(f"License field '{key}' must be a string, got {type(val).__name__}.")
        clean = val.strip()
        if key == "url" and clean:
            if not (clean.startswith("http://") or clean.startswith("https://")):
                raise ValueError(f"License URL must start with 'http://' or 'https://', got '{clean}'.")
        snapshot[key] = clean

    json.dumps(snapshot)
    return snapshot


def current_published_metadata_snapshot(dataset):
    """Return the effective current descriptive state without mutating a release.

    The most recent audited PATCH correction may be displayed as current metadata
    until a later release is published. Once a newer release exists, its immutable
    snapshot becomes authoritative again.
    """
    release = getattr(dataset, "published_release", None)
    if release and release.status == "published":
        latest_audit = dataset.metadata_edit_audits.order_by("-created_at", "-id").first()
        if (
            latest_audit
            and latest_audit.created_at > release.published_at
            and isinstance(latest_audit.after_snapshot, dict)
            and latest_audit.after_snapshot.get("schema_version") == release.snapshot_schema_version
        ):
            return latest_audit.after_snapshot
        return release.metadata_snapshot
    return generate_metadata_snapshot(dataset)
