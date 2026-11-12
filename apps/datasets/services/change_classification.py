"""Deterministic classification and review routing for complete V2 proposals."""
from dataclasses import dataclass
import re

from apps.datasets.models import ChangeType
from apps.datasets.services.publication import materialize_proposed_metadata


@dataclass(frozen=True)
class ProposalClassification:
    minimum_change_type: str
    changed_metadata_paths: tuple[str, ...]
    added_file_ids: tuple[str, ...]
    removed_file_ids: tuple[str, ...]
    replaced_file_ids: tuple[str, ...]
    review_required: bool
    review_bypass_reason: str = ""


MAJOR_EXACT_PATHS = {
    "title", "category", "creator.name", "contributors",
    "coverage.geographic", "coverage.temporal",
    "methodology.collection_method", "methodology.instances_represent",
    "methodology.data_preprocessing", "methodology.recommended_data_splits",
    "methodology.sensitive_data_disclosure", "methodology.has_header",
    "methodology.has_missing_values", "creator.affiliation",
    "citation.collaborators_text", "citation.sponsor_or_grant",
}
MINOR_PATHS = {
    "keywords", "languages", "characteristics", "resources.related_resources",
}
# Automatic publication is deliberately narrower than PATCH classification.
# Only presentation-level changes to these fields can bypass review, and only
# when the old/new text has identical words (punctuation/spacing-only change).
FORMAT_ONLY_AUTO_PATHS = {"description", "citation.citation_notes"}

# A sentinel is required because dict.get() collapses a missing key and an
# explicitly stored JSON null into the same Python value.
_MISSING = object()


def _flatten(value, prefix=""):
    if isinstance(value, dict):
        result = {}
        for key in sorted(value):
            path = f"{prefix}.{key}" if prefix else key
            result.update(_flatten(value[key], path))
        return result
    return {prefix: value}


def _word_sequence(value):
    if not isinstance(value, str):
        return None
    return tuple(re.findall(r"[\w]+", value.casefold(), flags=re.UNICODE))


def _rank_metadata_change(path, old, new):
    if path in MAJOR_EXACT_PATHS or path.startswith("category.") or path.startswith("coverage.") or path.startswith("methodology."):
        return 2
    if path in MINOR_PATHS:
        if isinstance(old, list) and isinstance(new, list):
            old_set, new_set = set(old), set(new)
            if old_set <= new_set:
                return 1 if old_set != new_set else 0
            return 2
        if path == "resources.related_resources" and isinstance(old, str) and isinstance(new, str):
            old_items = {x.strip() for x in re.split(r"[;\n]", old) if x.strip()}
            new_items = {x.strip() for x in re.split(r"[;\n]", new) if x.strip()}
            if old_items < new_items:
                return 1
            if new_items < old_items:
                return 2
            return 2 if old_items != new_items else 0
        return 1
    if path.startswith("contributors"):
        return 2
    # Citation-specific policy is intentionally not implemented here. Until a
    # field has an explicit policy, classify it conservatively like any other
    # unknown metadata path rather than silently treating it as PATCH.
    if path == "description":
        return 0
    # Unknown or newly introduced snapshot paths must never silently become
    # no-review PATCHes. Conservative MAJOR classification forces review.
    return 2


def classify_proposal(proposal):
    """Classify every metadata and manifest change and decide review routing."""
    base = proposal.base_release
    before = _flatten(base.metadata_snapshot)
    after_snapshot = materialize_proposed_metadata(base.metadata_snapshot, proposal.proposed_metadata)
    after = _flatten(after_snapshot)
    changed_paths = tuple(sorted(
        path for path in set(before) | set(after)
        if before.get(path, _MISSING) != after.get(path, _MISSING)
    ))

    base_files = {str(x.logical_file_id): str(x.stored_object_id) for x in base.files.all()}
    proposed_files = {str(x.logical_file_id): str(x.stored_object_id) for x in proposal.files.all()}
    base_ids, proposed_ids = set(base_files), set(proposed_files)
    removed = tuple(sorted(base_ids - proposed_ids))
    added = tuple(sorted(proposed_ids - base_ids))
    replaced = tuple(sorted(k for k in base_ids & proposed_ids if base_files[k] != proposed_files[k]))

    ranks = [
        _rank_metadata_change(path, before.get(path, _MISSING), after.get(path, _MISSING))
        for path in changed_paths
    ]
    if removed or replaced:
        ranks.append(2)
    if added:
        ranks.append(1)
    rank = max(ranks, default=0)
    minimum = (ChangeType.PATCH, ChangeType.MINOR, ChangeType.MAJOR)[rank]

    if not changed_paths and not added and not removed and not replaced:
        raise ValueError("This proposal contains no changes; a no-op release cannot be published.")

    # Files and substantive/unknown metadata always require review.
    review_required = True
    bypass_reason = ""
    if not added and not removed and not replaced and changed_paths:
        if all(path in FORMAT_ONLY_AUTO_PATHS for path in changed_paths):
            format_only = True
            for path in changed_paths:
                old_words = _word_sequence(before.get(path, _MISSING))
                new_words = _word_sequence(after.get(path, _MISSING))
                if old_words is None or new_words is None or old_words != new_words:
                    format_only = False
                    break
            declared_rank = ChangeType.rank(proposal.declared_change_type) if proposal.declared_change_type else 0
            if format_only and minimum == ChangeType.PATCH and declared_rank == 0:
                review_required = False
                bypass_reason = "formatting_only_descriptive_patch"

    return ProposalClassification(
        minimum_change_type=minimum,
        changed_metadata_paths=changed_paths,
        added_file_ids=added,
        removed_file_ids=removed,
        replaced_file_ids=replaced,
        review_required=review_required,
        review_bypass_reason=bypass_reason,
    )


def classify_proposal_changes(proposal):
    """Backward-compatible API returning only the minimum SemVer severity."""
    return classify_proposal(proposal).minimum_change_type
