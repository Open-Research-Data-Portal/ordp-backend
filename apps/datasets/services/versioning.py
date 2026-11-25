import re
from typing import Union, Tuple, Sequence

from apps.datasets.models import ChangeType

# Strict SemVer 2.0.0 numeric pattern: MAJOR.MINOR.PATCH
# - Exactly three non-negative integers separated by dots.
# - No leading zeros allowed unless the numeric value is exactly 0.
# - No whitespace, prefixes (e.g. 'v'), suffixes, or metadata tags allowed.
_SEMVER_REGEX = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


class SemVerError(ValueError):
    """Base exception for semantic versioning errors."""
    pass


class InvalidSemVerError(SemVerError):
    """Raised when a version string or version component fails validation."""
    pass


class InvalidChangeTypeError(SemVerError):
    """Raised when a change type is invalid or violates authority rules."""
    pass


def parse_semver(version_str: str) -> Tuple[int, int, int]:
    """Parse a strict 'MAJOR.MINOR.PATCH' string into a 3-integer tuple.

    Rules:
    - Only string types are accepted.
    - Exactly three components (e.g. '1.0.0', '0.1.0').
    - Leading zeros are rejected unless the value is exactly '0' (e.g. '01.0.0' is invalid).
    - Prefixes (e.g. 'v1.0.0'), suffixes (e.g. '-beta'), whitespace, and non-numeric values are rejected.
    """
    if not isinstance(version_str, str):
        raise InvalidSemVerError(
            f"SemVer version must be a string, got {type(version_str).__name__}: {version_str!r}"
        )

    match = _SEMVER_REGEX.match(version_str)
    if not match:
        raise InvalidSemVerError(
            f"Invalid semantic version string: '{version_str}'. "
            "Must follow strict MAJOR.MINOR.PATCH format with no prefixes, suffixes, or leading zeros."
        )

    major = int(match.group(1))
    minor = int(match.group(2))
    patch = int(match.group(3))
    return major, minor, patch


def format_semver(major: int, minor: int, patch: int) -> str:
    """Format three non-negative integer components into a 'MAJOR.MINOR.PATCH' string.

    Rules:
    - Rejects booleans, floats, strings, and non-integer types.
    - Rejects negative integers.
    """
    components = [("major", major), ("minor", minor), ("patch", patch)]

    for name, val in components:
        # Note: in Python, bool is a subclass of int (isinstance(True, int) is True).
        # We must explicitly reject booleans.
        if isinstance(val, bool) or not isinstance(val, int):
            raise InvalidSemVerError(
                f"{name} version component must be an integer, got {type(val).__name__}: {val!r}"
            )
        if val < 0:
            raise InvalidSemVerError(
                f"{name} version component cannot be negative, got {val}."
            )

    return f"{major}.{minor}.{patch}"


def normalize_change_type(change_type: Union[str, ChangeType]) -> ChangeType:
    """Validate and normalize a change type to a ChangeType enum instance."""
    if isinstance(change_type, bool):
        raise InvalidChangeTypeError(
            f"change_type cannot be a boolean, got {change_type!r}."
        )

    if isinstance(change_type, ChangeType):
        return change_type

    if isinstance(change_type, str):
        cleaned = change_type.strip().lower()
        try:
            return ChangeType(cleaned)
        except ValueError:
            pass

    valid_choices = list(ChangeType.values)
    raise InvalidChangeTypeError(
        f"Invalid change_type: '{change_type}'. Must be one of: {valid_choices}."
    )


def calculate_next_version(
    base_version: Union[Tuple[int, int, int], Sequence[int], str, None] = None,
    change_type: Union[str, ChangeType, None] = None,
) -> Tuple[int, int, int]:
    """Calculate the next semantic version tuple (major, minor, patch).

    Rules:
    1. Initial release:
       - When base_version is None and change_type is None, returns (1, 0, 0).
       - If a change_type is supplied without a base_version, raises ValueError
         (an initial release does not have a change type and defaults to 1.0.0).
    2. Subsequent releases:
       - When base_version is provided, change_type is strictly required.
       - 'patch': increments patch (major, minor, patch + 1).
       - 'minor': increments minor, resets patch to 0 (major, minor + 1, 0).
       - 'major': increments major, resets minor and patch to 0 (major + 1, 0, 0).
    3. Input validation:
       - All inputs are strictly validated before any calculation.
    """
    # Case 1: Initial release (no base_version)
    if base_version is None:
        if change_type is not None:
            # Validate the change type first to give a specific error if it's invalid,
            # otherwise explain that initial releases cannot take a change type.
            normalize_change_type(change_type)
            raise ValueError(
                f"change_type '{change_type}' cannot be specified when base_version is None. "
                "The initial verified release is always 1.0.0."
            )
        return 1, 0, 0

    # Case 2: Subsequent release (base_version provided)
    if change_type is None:
        raise ValueError("change_type is required when calculating next version from a base version.")

    # Validate and normalize base_version
    if isinstance(base_version, str):
        major, minor, patch = parse_semver(base_version)
    elif isinstance(base_version, (tuple, list)):
        if len(base_version) != 3:
            raise InvalidSemVerError(
                f"base_version sequence must have exactly 3 components (major, minor, patch), got {len(base_version)}."
            )
        major, minor, patch = base_version[0], base_version[1], base_version[2]
        # Validate each integer component
        format_semver(major, minor, patch)
    else:
        raise InvalidSemVerError(
            f"base_version must be a string or a 3-integer tuple/list, got {type(base_version).__name__}: {base_version!r}"
        )

    norm_change = normalize_change_type(change_type)

    if norm_change == ChangeType.PATCH:
        return major, minor, patch + 1
    elif norm_change == ChangeType.MINOR:
        return major, minor + 1, 0
    elif norm_change == ChangeType.MAJOR:
        return major + 1, 0, 0
    else:  # Defensive safeguard
        raise InvalidChangeTypeError(f"Unsupported change_type: '{norm_change}'.")


def validate_change_type_authority(
    declared: Union[str, ChangeType],
    computed: Union[str, ChangeType],
    reviewer: Union[str, ChangeType, None] = None,
) -> ChangeType:
    """Enforce change type authority rules between proposer, analyzer, and reviewer.

    Rules:
    - The computed minimum change type is the mandatory lower boundary.
    - Without a reviewer override:
        Returns max(declared, computed) using ChangeType.rank.
    - With a reviewer override:
        The reviewer may increase or confirm the change type, but must NOT lower it below computed.
        If rank(reviewer) < rank(computed), raises InvalidChangeTypeError.
        Otherwise returns the reviewer's choice.
    """
    norm_declared = normalize_change_type(declared)
    norm_computed = normalize_change_type(computed)

    if reviewer is None:
        if ChangeType.rank(norm_declared) >= ChangeType.rank(norm_computed):
            return norm_declared
        return norm_computed

    norm_reviewer = normalize_change_type(reviewer)

    if ChangeType.rank(norm_reviewer) < ChangeType.rank(norm_computed):
        raise InvalidChangeTypeError(
            f"Reviewer decision '{norm_reviewer.value}' cannot be lower than the computed minimum "
            f"change type '{norm_computed.value}'."
        )

    return norm_reviewer
