"""Guards on the offline AWS service-coverage catalogue (tools/): every resource type is
mapped, and every mapped slug still exists in the committed CSV."""

import csv
import os
import sys
import unittest

import aws_resource_audit as audit
from aws_resource_audit import config

TOOLS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tools",
)
CATALOGUE_CSV = os.path.join(TOOLS_DIR, "aws_service_coverage.csv")

sys.path.insert(0, TOOLS_DIR)

from fetch_service_catalogue import COLUMNS, slug_from_url  # noqa: E402
from service_map import SERVICE_SLUGS, types_by_slug  # noqa: E402


# A floor far below AWS's count, to catch a truncated fetch, not to pin a number.
MIN_SERVICES = 200


def _catalogue():
    with open(CATALOGUE_CSV, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class ServiceMapTests(unittest.TestCase):
    """The hand-authored half: our resource types -> an AWS product."""

    def test_every_resource_type_is_mapped_to_a_service(self):
        # A type missing here silently stops counting towards coverage.
        missing = sorted(set(config.SERVICE_BILLING) - set(SERVICE_SLUGS))
        self.assertEqual(missing, [], f"resource types with no service mapping: {missing}")

    def test_no_mapping_for_a_type_no_collector_emits(self):
        extra = sorted(set(SERVICE_SLUGS) - set(config.SERVICE_BILLING))
        self.assertEqual(extra, [], f"mapped types that no collector emits: {extra}")

    def test_every_slug_has_a_bubble_label(self):
        # The overview's "By service" chart names each circle from this table,
        # so a new slug without a label would draw its raw slug.
        from aws_resource_audit.aws_services import SERVICE_LABELS
        missing = sorted(set(SERVICE_SLUGS.values()) - set(SERVICE_LABELS))
        self.assertEqual(missing, [], f"slugs with no SERVICE_LABELS entry: {missing}")

    def test_every_slug_matches_a_real_aws_product(self):
        # A renamed AWS product URL would silently flip a service to uncovered.
        slugs_in_csv = {row["service_slug"] for row in _catalogue()}
        unmatched = sorted(set(SERVICE_SLUGS.values()) - slugs_in_csv)
        self.assertEqual(
            unmatched, [],
            f"slugs in service_map.py matching no product in the catalogue - AWS has "
            f"probably renamed a product URL: {unmatched}")


class ReportCategoryTests(unittest.TestCase):
    """SERVICE_META's categories match AWS's own catalogue, apart from listed overrides."""

    # Types deliberately filed differently from AWS (which puts all of ec2: under Compute).
    # May shrink; grows only with a reason recorded here and above SERVICE_META.
    CATEGORY_OVERRIDES = {
        "SecurityGroup":    "Networking & Content Delivery",
        "NetworkInterface": "Networking & Content Delivery",
        "ElasticIP":        "Networking & Content Delivery",
        "KeyPair":          "Security, Identity, & Compliance",
    }

    def _category_by_slug(self):
        return {row["service_slug"]: row["category"] for row in _catalogue()}

    def test_categories_come_from_the_aws_catalogue(self):
        by_slug = self._category_by_slug()
        for resource_type, slug in sorted(SERVICE_SLUGS.items()):
            with self.subTest(resource_type=resource_type):
                expected = self.CATEGORY_OVERRIDES.get(resource_type, by_slug[slug])
                self.assertEqual(
                    audit.SERVICE_META[resource_type]["category"], expected,
                    f"{resource_type} is filed under a category AWS does not use for "
                    f"{slug}. Either fix SERVICE_META or, if the difference is "
                    f"deliberate, add it to CATEGORY_OVERRIDES with the reason.")

    def test_every_override_is_a_real_disagreement(self):
        # An override that matches AWS is dead weight that reads as a live
        # exception - the next person keeps the divergence that is not there.
        by_slug = self._category_by_slug()
        for resource_type, category in sorted(self.CATEGORY_OVERRIDES.items()):
            with self.subTest(resource_type=resource_type):
                self.assertNotEqual(
                    category, by_slug[SERVICE_SLUGS[resource_type]],
                    f"{resource_type} no longer disagrees with AWS; drop the override")

    def test_one_colour_per_category(self):
        # One colour per category, and one category per colour, or the legend lies.
        by_category = {}
        for resource_type, meta in audit.SERVICE_META.items():
            by_category.setdefault(meta["category"], set()).add(meta["color"])
        for category, colors in sorted(by_category.items()):
            with self.subTest(category=category):
                self.assertEqual(len(colors), 1, f"{category} has colours {sorted(colors)}")
        colors = [next(iter(c)) for c in by_category.values()]
        self.assertEqual(len(colors), len(set(colors)), "two categories share one colour")


class StructuralCategoryTests(unittest.TestCase):
    """The graph draws by AWS category: STRUCTURAL_SERVICES is held to that rule here,
    since config.py cannot import SERVICE_META."""

    # The application's categories; not Security/Identity, Management & Governance or
    # Developer Tools, which describe how the account is run.
    GRAPH_CATEGORIES = frozenset({
        "Compute",
        "Storage",
        "Databases",
        "Application Integration",
        "Front-End Web & Mobile",
    })

    # Drawn despite their category: front doors (ALB, CloudFront), databases (OpenSearch),
    # what runs (ECS, EKS), what apps call (SageMaker), event carriers (Kinesis, Firehose, MSK).
    STRUCTURAL_OVERRIDES = frozenset({"LoadBalancer", "OpenSearchDomain", "OpenSearchServerlessCollection", "CloudFrontDistribution",
                                      "ECSService", "SageMakerEndpoint", "EKSCluster",
                                      "KinesisStream", "FirehoseStream", "MSKCluster"})

    # Hidden despite their category: how an instance came to exist (images, snapshots,
    # templates, reservations), and the Beanstalk application behind its environment.
    SUPPORTING_OVERRIDES = frozenset({
        "AMI", "EBSSnapshot", "RDSSnapshot", "LaunchTemplate", "BackupVault", "BackupPlan",
        "SpotInstanceRequest", "ReservedInstance",
        "ElasticBeanstalkApplication",
    })

    def test_the_graph_draws_exactly_the_application_categories(self):
        for resource_type in sorted(config.SERVICE_BILLING):
            with self.subTest(resource_type=resource_type):
                category = audit.SERVICE_META[resource_type]["category"]
                expected = (
                    (category in self.GRAPH_CATEGORIES
                     or resource_type in self.STRUCTURAL_OVERRIDES)
                    and resource_type not in self.SUPPORTING_OVERRIDES)
                self.assertEqual(
                    resource_type in audit.STRUCTURAL_SERVICES, expected,
                    f"{resource_type} is filed under {category!r}, so the graph "
                    f"{'draws' if expected else 'hides'} it. Either move it between "
                    f"STRUCTURAL_SERVICES and SUPPORTING_SERVICES or, if the "
                    f"difference is deliberate, add it to STRUCTURAL_OVERRIDES "
                    f"with the reason.")

    def test_every_override_is_a_real_exception(self):
        # An override that agrees with the category rule is dead weight.
        for resource_type in sorted(self.STRUCTURAL_OVERRIDES):
            with self.subTest(resource_type=resource_type):
                category = audit.SERVICE_META[resource_type]["category"]
                self.assertNotIn(
                    category, self.GRAPH_CATEGORIES,
                    f"{resource_type} is already drawn by the category rule; "
                    f"drop the override")
        for resource_type in sorted(self.SUPPORTING_OVERRIDES):
            with self.subTest(resource_type=resource_type):
                category = audit.SERVICE_META[resource_type]["category"]
                self.assertIn(
                    category, self.GRAPH_CATEGORIES,
                    f"{resource_type} is already hidden by the category rule; "
                    f"drop the override")

    def test_the_two_override_sets_do_not_overlap(self):
        # A type may not be both drawn and hidden.
        both = sorted(self.STRUCTURAL_OVERRIDES & self.SUPPORTING_OVERRIDES)
        self.assertEqual(both, [], f"listed as both drawn and hidden: {both}")

    def test_every_drawn_category_actually_has_a_resource_type(self):
        # Every graph category matches something, so a typo cannot select nothing.
        drawn = {audit.SERVICE_META[t]["category"] for t in audit.STRUCTURAL_SERVICES}
        unused = sorted(self.GRAPH_CATEGORIES - drawn)
        self.assertEqual(
            unused, [],
            f"categories in the rule that no drawn resource type has - check the "
            f"spelling against SERVICE_META: {unused}")


class ArchitectureIconTests(unittest.TestCase):
    """Every type the architecture diagram draws has its own AWS icon, shipped."""

    def test_every_drawn_type_has_an_icon(self):
        from aws_resource_audit.present.graph.icons import SERVICE_ICONS
        missing = sorted(audit.ARCHITECTURE_SERVICES - set(SERVICE_ICONS))
        self.assertEqual(missing, [], f"add these to SERVICE_ICONS in icons.py: {missing}")

    def test_every_named_icon_is_in_the_pack(self):
        from aws_resource_audit.present.graph.icons import EXTRA_ICONS, ICON_PACK, SERVICE_ICONS
        missing = sorted((set(SERVICE_ICONS.values()) | set(EXTRA_ICONS))
                         - set(ICON_PACK["icons"]))
        self.assertEqual(missing, [], f"re-run tools/build_aws_icons.py: {missing}")


class CatalogueFileTests(unittest.TestCase):
    """The fetched half: the committed CSV is intact and internally consistent."""

    def test_header_matches_the_writer(self):
        with open(CATALOGUE_CSV, newline="", encoding="utf-8") as handle:
            header = next(csv.reader(handle))
        self.assertEqual(header, COLUMNS)

    def test_catalogue_is_not_truncated(self):
        self.assertGreaterEqual(
            len(_catalogue()), MIN_SERVICES,
            "the committed catalogue is far smaller than AWS's real service list, "
            "which means a truncated fetch was written over a good file")

    def test_every_service_has_a_category(self):
        # A blank category makes a row invisible when the file is read by
        # category, which is the only way anyone reads it.
        uncategorised = [r["service_name"] for r in _catalogue() if not r["category"]]
        self.assertEqual(uncategorised, [], f"services with no category: {uncategorised}")

    def test_no_markup_or_newlines_survive_into_the_csv(self):
        # Rich-text fields arrive with markup and newlines; none may split a CSV record.
        for row in _catalogue():
            for column, value in row.items():
                with self.subTest(service=row["service_name"], column=column):
                    self.assertNotIn("<", value, "HTML markup leaked into the CSV")
                    self.assertNotIn("\n", value)
                    self.assertEqual(value, value.strip())

    def test_free_tier_uses_the_expected_vocabulary(self):
        allowed = {"", "Free Trial", "12 Months Free", "Always Free"}
        found = {r["free_tier"] for r in _catalogue()}
        self.assertEqual(
            found - allowed, set(),
            "AWS has introduced a new free-tier wording; confirm clean_text still "
            "strips it correctly rather than widening this set reflexively")

    def test_covered_columns_agree_with_the_service_map(self):
        # covered, covered_type_count and covered_types are one fact and must agree.
        # A slug AWS lists twice is credited to its first row only.
        by_slug = types_by_slug()
        credited = set()
        for row in _catalogue():
            slug = row["service_slug"]
            expected = [] if slug in credited else by_slug.get(slug, [])
            if expected:
                credited.add(slug)
            self.assertEqual(row["covered"], "yes" if expected else "no", row["service_name"])
            self.assertEqual(int(row["covered_type_count"]), len(expected), row["service_name"])
            self.assertEqual(
                [t for t in row["covered_types"].split(";") if t], expected,
                row["service_name"])

    def test_every_resource_type_appears_exactly_once_in_the_catalogue(self):
        seen = []
        for row in _catalogue():
            seen.extend(t for t in row["covered_types"].split(";") if t)
        self.assertEqual(sorted(seen), sorted(config.SERVICE_BILLING))


class SlugParsingTests(unittest.TestCase):
    """slug_from_url is the join, and every case here is a real URL shape the
    AWS directory or SSM actually returns."""

    def test_real_url_shapes(self):
        cases = [
            ("https://aws.amazon.com/ec2/?did=ap_card&trk=ap_card", "ec2"),  # tracking params
            ("https://aws.amazon.com/iam", "iam"),                           # no trailing slash
            ("https://aws.amazon.com/api-gateway/", "api-gateway"),          # hyphenated
            ("https://aws.amazon.com/EC2/", "ec2"),                          # case
            ("https://aws.amazon.com/vpc/#anchor", "vpc"),                   # fragment
            ("https://aws.amazon.com/", ""),                                 # bare host
            ("", ""),                                                        # absent URL
        ]
        for url, expected in cases:
            with self.subTest(url=url):
                self.assertEqual(slug_from_url(url), expected)


if __name__ == "__main__":
    unittest.main()
