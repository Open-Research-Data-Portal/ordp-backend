import json
import uuid
from unittest.mock import MagicMock, PropertyMock
from django.core.exceptions import ObjectDoesNotExist
from django.test import SimpleTestCase

from apps.datasets.services.snapshots import (
    generate_metadata_snapshot,
    generate_citation_snapshot,
    generate_license_snapshot,
    CURRENT_SNAPSHOT_SCHEMA_VERSION,
)


class MetadataSnapshotTests(SimpleTestCase):
    databases = set()

    def _build_full_mock_dataset(self):
        dataset = MagicMock()
        dataset.id = uuid.uuid4()
        dataset.title = "Agricultural Soil Quality Survey"

        # Operational attributes that MUST BE EXCLUDED
        dataset.visibility = "public"
        dataset.status = "published"
        dataset.is_active = True
        dataset.version = 5
        dataset.view_count = 120
        dataset.download_count = 45
        dataset.assigned_reviewer = MagicMock(id=99)
        dataset.is_archived = False
        dataset.archived_at = None
        dataset.terms_accepted = True
        dataset.terms_accepted_at = "2026-01-01T00:00:00Z"
        dataset.terms_version = "v1.0"
        dataset.thumbnail_key = "thumbnails/soil.png"
        dataset.thumbnail_source = "uploaded"
        dataset.edit_in_progress_notice = False

        # Owner / Creator (Internal account info that MUST NOT appear in snapshot)
        owner = MagicMock()
        owner.id = 10
        owner.username = "abebe_b"
        owner.email = "abebe@aastu.edu.et"
        owner.profile.full_name = "Dr. Abebe Bekele"
        owner.profile.affiliation = "AASTU Department of Biotechnology"
        dataset.owner = owner

        # Contributors
        c1 = MagicMock()
        c1.id = uuid.uuid4()
        c1.name = "Chala Tadesse"
        c1.contributor_type = "co_author"
        c1.order = 1
        c1.permission = "edit"  # Operational - MUST BE EXCLUDED
        c1.invited_email = "chala@example.com"  # Internal - MUST BE EXCLUDED
        c1.user = MagicMock()
        c1.user.profile.full_name = "Chala Tadesse"
        c1.user.profile.affiliation = "AASTU"

        c2 = MagicMock()
        c2.id = uuid.uuid4()
        c2.name = "Desta Haile"
        c2.contributor_type = "contributor"
        c2.order = 2
        c2.permission = "view"  # Operational - MUST BE EXCLUDED
        c2.invited_email = "desta@example.com"  # Internal - MUST BE EXCLUDED
        c2.user = None  # Contributor without user account

        dataset.contributors.all.return_value.select_related.return_value.order_by.return_value = [c1, c2]

        # Metadata
        metadata = MagicMock()
        metadata.description = "Longitudinal survey of soil pH and mineral content across regional farms."

        category = MagicMock()
        category.id = uuid.uuid4()
        category.name = "Agriculture & Environmental Sciences"
        category.description = "Datasets related to farming and environment"
        metadata.category = category

        # Keywords in non-alphabetical order to test deterministic sorting
        metadata.keywords.values_list.return_value = ["soil", "nitrogen", "ph-level", "agriculture"]
        metadata.languages.values_list.return_value = ["English", "Amharic"]
        metadata.characteristics.values_list.return_value = ["Tabular", "Multivariate"]

        metadata.geographic_coverage = "Oromia & Amhara Regions, Ethiopia"
        metadata.temporal_coverage = "2022-2025"
        metadata.collection_method = "Field core sampling with laboratory spectrometry"
        metadata.data_preprocessing = "Outlier filtering and standard normal deviation scaling"
        metadata.instances_represent = "One physical soil core from 15cm depth"
        metadata.recommended_data_splits = "80% training, 10% validation, 10% test"
        metadata.sensitive_data_disclosure = "Farm GPS coordinates anonymized to nearest township"
        metadata.has_header = True
        metadata.has_missing_values = False

        metadata.doi_citation = "10.1234/aastu.soil.2025"
        metadata.citation_notes = "Please cite as ORDP Soil Dataset Release"
        metadata.collaborators_text = "Supported by Ethiopian Agricultural Research Institute"
        metadata.sponsor_or_grant = "MoSHE Grant #ET-2024-AGR-01"
        metadata.related_resources = "https://github.com/aastu/soil-analysis-toolkit"

        dataset.metadata = metadata
        return dataset

    def test_full_metadata_snapshot_serialization(self):
        dataset = self._build_full_mock_dataset()
        snapshot = generate_metadata_snapshot(dataset)

        self.assertEqual(snapshot["schema_version"], CURRENT_SNAPSHOT_SCHEMA_VERSION)
        self.assertEqual(snapshot["title"], "Agricultural Soil Quality Survey")
        self.assertEqual(snapshot["description"], "Longitudinal survey of soil pH and mineral content across regional farms.")

        # Category
        self.assertIsNotNone(snapshot["category"])
        self.assertEqual(snapshot["category"]["name"], "Agriculture & Environmental Sciences")
        self.assertEqual(snapshot["category"]["description"], "Datasets related to farming and environment")

        # Deterministic M2M sorting
        self.assertEqual(snapshot["keywords"], ["agriculture", "nitrogen", "ph-level", "soil"])
        self.assertEqual(snapshot["languages"], ["Amharic", "English"])
        self.assertEqual(snapshot["characteristics"], ["Multivariate", "Tabular"])

        # Creator (Privacy-preserving: only name and affiliation)
        self.assertEqual(snapshot["creator"]["name"], "Dr. Abebe Bekele")
        self.assertEqual(snapshot["creator"]["affiliation"], "AASTU Department of Biotechnology")
        self.assertNotIn("id", snapshot["creator"])
        self.assertNotIn("username", snapshot["creator"])
        self.assertNotIn("email", snapshot["creator"])

        # Contributors (Privacy-preserving: no internal IDs or permissions)
        self.assertEqual(len(snapshot["contributors"]), 2)
        self.assertEqual(snapshot["contributors"][0]["name"], "Chala Tadesse")
        self.assertEqual(snapshot["contributors"][0]["contributor_type"], "co_author")
        self.assertEqual(snapshot["contributors"][0]["order"], 1)
        self.assertEqual(snapshot["contributors"][0]["affiliation"], "AASTU")
        self.assertNotIn("id", snapshot["contributors"][0])
        self.assertNotIn("permission", snapshot["contributors"][0])
        self.assertNotIn("invited_email", snapshot["contributors"][0])

        self.assertEqual(snapshot["contributors"][1]["name"], "Desta Haile")
        self.assertEqual(snapshot["contributors"][1]["contributor_type"], "contributor")
        self.assertEqual(snapshot["contributors"][1]["order"], 2)
        self.assertEqual(snapshot["contributors"][1]["affiliation"], "")
        self.assertNotIn("id", snapshot["contributors"][1])

        # Coverage
        self.assertEqual(snapshot["coverage"]["geographic"], "Oromia & Amhara Regions, Ethiopia")
        self.assertEqual(snapshot["coverage"]["temporal"], "2022-2025")

        # Methodology
        self.assertEqual(snapshot["methodology"]["collection_method"], "Field core sampling with laboratory spectrometry")
        self.assertEqual(snapshot["methodology"]["data_preprocessing"], "Outlier filtering and standard normal deviation scaling")
        self.assertEqual(snapshot["methodology"]["instances_represent"], "One physical soil core from 15cm depth")
        self.assertEqual(snapshot["methodology"]["recommended_data_splits"], "80% training, 10% validation, 10% test")
        self.assertEqual(snapshot["methodology"]["sensitive_data_disclosure"], "Farm GPS coordinates anonymized to nearest township")
        self.assertIs(snapshot["methodology"]["has_header"], True)
        self.assertIs(snapshot["methodology"]["has_missing_values"], False)

        # Citation
        self.assertEqual(snapshot["citation"]["doi_citation"], "10.1234/aastu.soil.2025")
        self.assertEqual(snapshot["citation"]["citation_notes"], "Please cite as ORDP Soil Dataset Release")
        self.assertEqual(snapshot["citation"]["collaborators_text"], "Supported by Ethiopian Agricultural Research Institute")
        self.assertEqual(snapshot["citation"]["sponsor_or_grant"], "MoSHE Grant #ET-2024-AGR-01")

        # Resources
        self.assertEqual(snapshot["resources"]["related_resources"], "https://github.com/aastu/soil-analysis-toolkit")

    def test_creator_privacy_excludes_user_id_email_username(self):
        dataset = self._build_full_mock_dataset()
        snapshot = generate_metadata_snapshot(dataset)

        creator = snapshot["creator"]
        self.assertNotIn("id", creator)
        self.assertNotIn("username", creator)
        self.assertNotIn("email", creator)
        self.assertEqual(set(creator.keys()), {"name", "affiliation"})

    def test_creator_without_profile_full_name_uses_empty_string(self):
        # Must NOT expose username as a fallback when profile full_name is missing
        dataset = MagicMock()
        dataset.title = "Anonymous Creator Dataset"
        type(dataset).metadata = PropertyMock(side_effect=ObjectDoesNotExist)
        dataset.contributors.all.return_value.select_related.return_value.order_by.return_value = []

        owner = MagicMock()
        owner.username = "secret_username"
        owner.profile.full_name = ""
        owner.profile.affiliation = "Addis Ababa University"
        dataset.owner = owner

        snapshot = generate_metadata_snapshot(dataset)
        self.assertEqual(snapshot["creator"]["name"], "")
        self.assertNotIn("secret_username", str(snapshot["creator"]))

    def test_contributor_privacy_excludes_internal_ids_and_permissions(self):
        dataset = self._build_full_mock_dataset()
        snapshot = generate_metadata_snapshot(dataset)

        for contrib in snapshot["contributors"]:
            self.assertNotIn("id", contrib)
            self.assertNotIn("permission", contrib)
            self.assertNotIn("invited_email", contrib)
            self.assertEqual(set(contrib.keys()), {"name", "contributor_type", "order", "affiliation"})

    def test_contributor_queryset_uses_order_by_order_and_id(self):
        dataset = self._build_full_mock_dataset()
        generate_metadata_snapshot(dataset)

        # Confirm the query ordering is enforced at the database level
        dataset.contributors.all.return_value.select_related.return_value.order_by.assert_called_with("order", "id")

    def test_operational_fields_are_strictly_excluded(self):
        dataset = self._build_full_mock_dataset()
        snapshot = generate_metadata_snapshot(dataset)

        def collect_keys(obj):
            keys = set()
            if isinstance(obj, dict):
                for k, v in obj.items():
                    keys.add(k)
                    keys.update(collect_keys(v))
            elif isinstance(obj, list):
                for item in obj:
                    keys.update(collect_keys(item))
            return keys

        all_snapshot_keys = collect_keys(snapshot)

        banned_operational_keys = {
            "visibility",
            "status",
            "is_active",
            "version",
            "view_count",
            "download_count",
            "assigned_reviewer",
            "is_archived",
            "archived_at",
            "terms_accepted",
            "terms_accepted_at",
            "terms_version",
            "thumbnail_key",
            "thumbnail_source",
            "edit_in_progress_notice",
            "permission",
            "invited_email",
        }

        for banned in banned_operational_keys:
            self.assertNotIn(banned, all_snapshot_keys, f"Operational key '{banned}' must not appear in snapshot!")

    def test_sparse_or_missing_metadata_handled_safely(self):
        dataset = MagicMock()
        dataset.title = "Minimal Dataset"
        dataset.owner = None
        dataset.contributors.all.return_value.select_related.return_value.order_by.return_value = []
        type(dataset).metadata = PropertyMock(side_effect=ObjectDoesNotExist)

        snapshot = generate_metadata_snapshot(dataset)

        self.assertEqual(snapshot["schema_version"], CURRENT_SNAPSHOT_SCHEMA_VERSION)
        self.assertEqual(snapshot["title"], "Minimal Dataset")
        self.assertEqual(snapshot["description"], "")
        self.assertIsNone(snapshot["category"])
        self.assertEqual(snapshot["keywords"], [])
        self.assertEqual(snapshot["languages"], [])
        self.assertEqual(snapshot["characteristics"], [])
        self.assertIsNone(snapshot["creator"])
        self.assertEqual(snapshot["contributors"], [])
        self.assertEqual(snapshot["coverage"]["geographic"], "")
        self.assertEqual(snapshot["methodology"]["collection_method"], "")
        self.assertIsNone(snapshot["methodology"]["has_header"])
        self.assertEqual(snapshot["citation"]["doi_citation"], "")
        self.assertEqual(snapshot["resources"]["related_resources"], "")

    def test_contributor_ordering_is_strictly_preserved(self):
        dataset = MagicMock()
        dataset.title = "Ordered Authors Dataset"
        dataset.owner = None
        type(dataset).metadata = PropertyMock(side_effect=ObjectDoesNotExist)

        c_third = MagicMock()
        c_third.id = uuid.uuid4()
        c_third.name = "Author 3"
        c_third.contributor_type = "contributor"
        c_third.order = 3
        c_third.user = None

        c_first = MagicMock()
        c_first.id = uuid.uuid4()
        c_first.name = "Author 1"
        c_first.contributor_type = "owner"
        c_first.order = 1
        c_first.user = None

        c_second = MagicMock()
        c_second.id = uuid.uuid4()
        c_second.name = "Author 2"
        c_second.contributor_type = "co_author"
        c_second.order = 2
        c_second.user = None

        dataset.contributors.all.return_value.select_related.return_value.order_by.return_value = [
            c_first, c_second, c_third
        ]

        snapshot = generate_metadata_snapshot(dataset)

        contributors = snapshot["contributors"]
        self.assertEqual(len(contributors), 3)
        self.assertEqual([c["order"] for c in contributors], [1, 2, 3])
        self.assertEqual([c["name"] for c in contributors], ["Author 1", "Author 2", "Author 3"])

    def test_deterministic_output(self):
        dataset = self._build_full_mock_dataset()

        snapshot_1 = generate_metadata_snapshot(dataset)
        snapshot_2 = generate_metadata_snapshot(dataset)

        self.assertEqual(snapshot_1, snapshot_2)
        self.assertEqual(json.dumps(snapshot_1, sort_keys=True), json.dumps(snapshot_2, sort_keys=True))

    def test_json_serializability(self):
        dataset = self._build_full_mock_dataset()
        snapshot = generate_metadata_snapshot(dataset)

        serialized = json.dumps(snapshot)
        deserialized = json.loads(serialized)
        self.assertEqual(snapshot, deserialized)

    def test_citation_snapshot_generation(self):
        dataset = self._build_full_mock_dataset()
        citation = generate_citation_snapshot(dataset)

        self.assertEqual(citation["title"], "Agricultural Soil Quality Survey")
        self.assertEqual(citation["creator_name"], "Dr. Abebe Bekele")
        self.assertEqual(citation["doi_citation"], "10.1234/aastu.soil.2025")
        self.assertEqual(citation["citation_notes"], "Please cite as ORDP Soil Dataset Release")
        self.assertEqual(citation["collaborators_text"], "Supported by Ethiopian Agricultural Research Institute")
        self.assertEqual(citation["sponsor_or_grant"], "MoSHE Grant #ET-2024-AGR-01")

        self.assertEqual(json.loads(json.dumps(citation)), citation)

    def test_license_snapshot_generation(self):
        self.assertEqual(generate_license_snapshot(None), {})
        self.assertEqual(generate_license_snapshot({}), {})

        data = {
            "name": "Creative Commons Attribution 4.0 International",
            "identifier": "CC-BY-4.0",
            "url": "https://creativecommons.org/licenses/by/4.0/",
            "custom_terms": "",
        }
        license_snapshot = generate_license_snapshot(data)
        self.assertEqual(license_snapshot["name"], "Creative Commons Attribution 4.0 International")
        self.assertEqual(license_snapshot["identifier"], "CC-BY-4.0")
        self.assertEqual(license_snapshot["url"], "https://creativecommons.org/licenses/by/4.0/")
        self.assertEqual(json.loads(json.dumps(license_snapshot)), license_snapshot)

    def test_license_snapshot_strict_validation(self):
        with self.assertRaises(ValueError):
            generate_license_snapshot("not a dict")

        with self.assertRaises(ValueError):
            generate_license_snapshot({"name": "MIT", "unknown_field": "val"})

        with self.assertRaises(ValueError):
            generate_license_snapshot({"name": "MIT", "url": "ftp://example.com"})
