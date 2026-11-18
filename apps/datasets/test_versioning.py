from django.test import SimpleTestCase

from apps.datasets.models import ChangeType
from apps.datasets.services.versioning import (
    parse_semver,
    format_semver,
    calculate_next_version,
    validate_change_type_authority,
    normalize_change_type,
    InvalidSemVerError,
    InvalidChangeTypeError,
)


class SemVerParsingTests(SimpleTestCase):
    databases = set()

    def test_valid_semver_strings(self):
        self.assertEqual(parse_semver("0.0.0"), (0, 0, 0))
        self.assertEqual(parse_semver("1.0.0"), (1, 0, 0))
        self.assertEqual(parse_semver("0.1.0"), (0, 1, 0))
        self.assertEqual(parse_semver("1.10.2"), (1, 10, 2))
        self.assertEqual(parse_semver("12.345.678"), (12, 345, 678))

    def test_reject_leading_zeros_on_non_zero_values(self):
        invalid_cases = [
            "01.0.0",
            "1.01.0",
            "1.0.01",
            "00.0.0",
            "0.00.0",
            "0.0.00",
            "01.02.03",
        ]
        for case in invalid_cases:
            with self.subTest(case=case):
                with self.assertRaises(InvalidSemVerError):
                    parse_semver(case)

    def test_reject_prefixes_and_suffixes(self):
        invalid_cases = [
            "v1.0.0",
            "V1.0.0",
            "1.0.0-alpha",
            "1.0.0-beta.1",
            "1.0.0+20130313144700",
            "1.0.0-SNAPSHOT",
            "ver1.0.0",
        ]
        for case in invalid_cases:
            with self.subTest(case=case):
                with self.assertRaises(InvalidSemVerError):
                    parse_semver(case)

    def test_reject_whitespace(self):
        invalid_cases = [
            " 1.0.0",
            "1.0.0 ",
            "1. 0.0",
            "1.0 .0",
            "\t1.0.0",
            "\n1.0.0",
        ]
        for case in invalid_cases:
            with self.subTest(case=case):
                with self.assertRaises(InvalidSemVerError):
                    parse_semver(case)

    def test_reject_incomplete_or_extra_components(self):
        invalid_cases = [
            "",
            "1",
            "1.0",
            "1.0.",
            ".1.0.0",
            "1.0.0.0",
            "1.0.0.1",
        ]
        for case in invalid_cases:
            with self.subTest(case=case):
                with self.assertRaises(InvalidSemVerError):
                    parse_semver(case)

    def test_reject_non_numeric_and_negative_components(self):
        invalid_cases = [
            "-1.0.0",
            "1.-2.0",
            "1.0.-3",
            "1.a.0",
            "x.y.z",
            "1.0.NaN",
        ]
        for case in invalid_cases:
            with self.subTest(case=case):
                with self.assertRaises(InvalidSemVerError):
                    parse_semver(case)

    def test_reject_non_string_types(self):
        invalid_types = [
            100,
            1.0,
            None,
            True,
            False,
            (1, 0, 0),
            ["1.0.0"],
            {"version": "1.0.0"},
        ]
        for case in invalid_types:
            with self.subTest(case=case):
                with self.assertRaises(InvalidSemVerError):
                    parse_semver(case)


class SemVerFormattingTests(SimpleTestCase):
    databases = set()

    def test_valid_formatting(self):
        self.assertEqual(format_semver(0, 0, 0), "0.0.0")
        self.assertEqual(format_semver(1, 0, 0), "1.0.0")
        self.assertEqual(format_semver(1, 2, 3), "1.2.3")
        self.assertEqual(format_semver(10, 200, 3000), "10.200.3000")

    def test_reject_negative_integers(self):
        with self.assertRaises(InvalidSemVerError):
            format_semver(-1, 0, 0)
        with self.assertRaises(InvalidSemVerError):
            format_semver(1, -1, 0)
        with self.assertRaises(InvalidSemVerError):
            format_semver(1, 0, -1)

    def test_reject_booleans(self):
        # In Python, bool is a subclass of int. We must ensure booleans are rejected.
        with self.assertRaises(InvalidSemVerError):
            format_semver(True, 0, 0)
        with self.assertRaises(InvalidSemVerError):
            format_semver(1, False, 0)
        with self.assertRaises(InvalidSemVerError):
            format_semver(1, 0, True)

    def test_reject_non_integer_types(self):
        invalid_inputs = [
            (1.0, 0, 0),
            ("1", 0, 0),
            (None, 0, 0),
            ([], 0, 0),
        ]
        for major, minor, patch in invalid_inputs:
            with self.subTest(inputs=(major, minor, patch)):
                with self.assertRaises(InvalidSemVerError):
                    format_semver(major, minor, patch)


class NextVersionCalculationTests(SimpleTestCase):
    databases = set()

    def test_initial_release_no_base_no_change_type_returns_one_zero_zero(self):
        self.assertEqual(calculate_next_version(), (1, 0, 0))
        self.assertEqual(calculate_next_version(base_version=None, change_type=None), (1, 0, 0))

    def test_initial_release_with_change_type_is_explicitly_rejected(self):
        # A valid change type supplied without a base version must not be silently ignored.
        with self.assertRaises(ValueError) as ctx:
            calculate_next_version(base_version=None, change_type="patch")
        self.assertIn("cannot be specified when base_version is None", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            calculate_next_version(base_version=None, change_type="minor")
        self.assertIn("cannot be specified when base_version is None", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            calculate_next_version(base_version=None, change_type="major")
        self.assertIn("cannot be specified when base_version is None", str(ctx.exception))

    def test_initial_release_with_invalid_change_type_raises_change_type_error(self):
        with self.assertRaises(InvalidChangeTypeError):
            calculate_next_version(base_version=None, change_type="invalid_type")

    def test_base_version_requires_change_type(self):
        with self.assertRaises(ValueError) as ctx:
            calculate_next_version(base_version=(1, 0, 0), change_type=None)
        self.assertIn("change_type is required", str(ctx.exception))

    def test_patch_bump_increments_patch_only(self):
        self.assertEqual(calculate_next_version((1, 0, 0), "patch"), (1, 0, 1))
        self.assertEqual(calculate_next_version((1, 0, 1), ChangeType.PATCH), (1, 0, 2))
        self.assertEqual(calculate_next_version("1.2.3", "patch"), (1, 2, 4))
        self.assertEqual(calculate_next_version((0, 1, 0), "patch"), (0, 1, 1))

    def test_minor_bump_increments_minor_and_resets_patch(self):
        self.assertEqual(calculate_next_version((1, 0, 0), "minor"), (1, 1, 0))
        self.assertEqual(calculate_next_version((1, 0, 9), ChangeType.MINOR), (1, 1, 0))
        self.assertEqual(calculate_next_version("1.2.3", "minor"), (1, 3, 0))
        self.assertEqual(calculate_next_version((0, 1, 5), "minor"), (0, 2, 0))

    def test_major_bump_increments_major_and_resets_minor_and_patch(self):
        self.assertEqual(calculate_next_version((1, 0, 0), "major"), (2, 0, 0))
        self.assertEqual(calculate_next_version((1, 5, 9), ChangeType.MAJOR), (2, 0, 0))
        self.assertEqual(calculate_next_version("1.2.3", "major"), (2, 0, 0))
        self.assertEqual(calculate_next_version((0, 9, 9), "major"), (1, 0, 0))

    def test_accepts_list_and_tuple_base_versions(self):
        self.assertEqual(calculate_next_version([1, 0, 0], "patch"), (1, 0, 1))
        self.assertEqual(calculate_next_version((1, 0, 0), "patch"), (1, 0, 1))

    def test_rejects_malformed_sequence_base_versions(self):
        with self.assertRaises(InvalidSemVerError):
            calculate_next_version((1, 0), "patch")
        with self.assertRaises(InvalidSemVerError):
            calculate_next_version((1, 0, 0, 0), "patch")
        with self.assertRaises(InvalidSemVerError):
            calculate_next_version((1, "0", 0), "patch")
        with self.assertRaises(InvalidSemVerError):
            calculate_next_version((True, 0, 0), "patch")
        with self.assertRaises(InvalidSemVerError):
            calculate_next_version((-1, 0, 0), "patch")

    def test_rejects_unsupported_change_types(self):
        invalid_types = ["hotfix", "revision", "PATCH_BUMP", "", 123, True]
        for ct in invalid_types:
            with self.subTest(change_type=ct):
                with self.assertRaises(InvalidChangeTypeError):
                    calculate_next_version((1, 0, 0), ct)


class ChangeTypeAuthorityTests(SimpleTestCase):
    databases = set()

    def test_normalize_change_type_accepts_valid_values(self):
        self.assertEqual(normalize_change_type("patch"), ChangeType.PATCH)
        self.assertEqual(normalize_change_type("PATCH"), ChangeType.PATCH)
        self.assertEqual(normalize_change_type(ChangeType.PATCH), ChangeType.PATCH)
        self.assertEqual(normalize_change_type("minor"), ChangeType.MINOR)
        self.assertEqual(normalize_change_type("major"), ChangeType.MAJOR)

    def test_normalize_change_type_rejects_invalid(self):
        with self.assertRaises(InvalidChangeTypeError):
            normalize_change_type("invalid")
        with self.assertRaises(InvalidChangeTypeError):
            normalize_change_type(True)
        with self.assertRaises(InvalidChangeTypeError):
            normalize_change_type(1)
        with self.assertRaises(InvalidChangeTypeError):
            normalize_change_type(None)

    def test_without_reviewer_returns_max_of_declared_and_computed(self):
        # Case 1: declared equals computed
        self.assertEqual(
            validate_change_type_authority("patch", "patch"),
            ChangeType.PATCH,
        )
        self.assertEqual(
            validate_change_type_authority("minor", "minor"),
            ChangeType.MINOR,
        )
        # Case 2: declared is higher than computed -> accept declared
        self.assertEqual(
            validate_change_type_authority("minor", "patch"),
            ChangeType.MINOR,
        )
        self.assertEqual(
            validate_change_type_authority("major", "patch"),
            ChangeType.MAJOR,
        )
        self.assertEqual(
            validate_change_type_authority("major", "minor"),
            ChangeType.MAJOR,
        )
        # Case 3: declared is lower than computed -> enforce computed as minimum
        self.assertEqual(
            validate_change_type_authority("patch", "minor"),
            ChangeType.MINOR,
        )
        self.assertEqual(
            validate_change_type_authority("patch", "major"),
            ChangeType.MAJOR,
        )
        self.assertEqual(
            validate_change_type_authority("minor", "major"),
            ChangeType.MAJOR,
        )

    def test_with_reviewer_override_elevates_or_confirms(self):
        # Reviewer elevates above declared and computed
        self.assertEqual(
            validate_change_type_authority(
                declared="patch", computed="minor", reviewer="major"
            ),
            ChangeType.MAJOR,
        )
        # Reviewer matches computed (even if declared was lower)
        self.assertEqual(
            validate_change_type_authority(
                declared="patch", computed="minor", reviewer="minor"
            ),
            ChangeType.MINOR,
        )
        # Reviewer matches declared and computed
        self.assertEqual(
            validate_change_type_authority(
                declared="patch", computed="patch", reviewer="patch"
            ),
            ChangeType.PATCH,
        )

    def test_with_reviewer_override_cannot_demote_below_computed_minimum(self):
        # Reviewer attempts to demote to 'patch' when computed minimum is 'minor'
        with self.assertRaises(InvalidChangeTypeError) as ctx:
            validate_change_type_authority(
                declared="major", computed="minor", reviewer="patch"
            )
        self.assertIn("cannot be lower than the computed minimum", str(ctx.exception))

        # Reviewer attempts to demote to 'minor' when computed minimum is 'major'
        with self.assertRaises(InvalidChangeTypeError) as ctx:
            validate_change_type_authority(
                declared="major", computed="major", reviewer="minor"
            )
        self.assertIn("cannot be lower than the computed minimum", str(ctx.exception))

    def test_authority_rejects_invalid_inputs(self):
        with self.assertRaises(InvalidChangeTypeError):
            validate_change_type_authority("invalid", "minor")
        with self.assertRaises(InvalidChangeTypeError):
            validate_change_type_authority("patch", "invalid")
        with self.assertRaises(InvalidChangeTypeError):
            validate_change_type_authority("patch", "minor", reviewer="invalid")
