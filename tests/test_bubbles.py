"""The bubble pack: area proportionality, non-overlap, determinism, and values that get
no circle - properties a reader believes from the picture whether true or not."""

import math
import unittest

from . import FROZEN_ACCOUNT  # noqa: F401  (import for the clock/identity pin)

from aws_resource_audit.analyze.service_summary import ServiceSummary
from aws_resource_audit.billing import to_decimal
from aws_resource_audit.config import BUBBLE_METRIC_IDS
from aws_resource_audit.present.bubbles import (
    DEFAULT_METRIC,
    METRIC_IDS,
    METRICS,
    metric_value,
    pack,
    pack_all,
)


def summary(pid, count, *, unused=0, cost=None, unknown=0):
    """A summary with just the fields a pack reads. ServiceSummary, because
    it is the one that can carry a cost."""
    return ServiceSummary(
        id=pid, display_name=pid,
        resource_keys=tuple(f"{pid}:{i}" for i in range(count)),
        unused=unused, unknown=unknown, active=count - unused - unknown,
        cost=None if cost is None else to_decimal(str(cost)))


def overlaps(bubbles, tolerance=1e-6):
    """Every pair that intersects. Empty is the only acceptable answer."""
    bad = []
    for i, a in enumerate(bubbles):
        for b in bubbles[i + 1:]:
            if math.hypot(a.x - b.x, a.y - b.y) < a.r + b.r - tolerance:
                bad.append((a.id, b.id))
    return bad


class GeometryTests(unittest.TestCase):
    def test_area_is_proportional_to_value_not_diameter(self):
        """Four times the value is four times the AREA, so twice the radius.
        Getting this backwards is the classic bubble-chart error."""
        bubbles = pack([summary("big", 100), summary("small", 25)], "resource_count")
        big, small = bubbles[0], bubbles[1]
        self.assertAlmostEqual(big.r / small.r, 2.0, places=9)
        self.assertAlmostEqual((big.r ** 2) / (small.r ** 2), 4.0, places=8)

    def test_equal_percentages_give_equal_radii_whatever_the_project_size(self):
        """A project 50% idle out of 2 and one 50% idle out of 100 are the same
        answer to the question this view asks."""
        bubbles = pack([summary("tiny", 2, unused=1), summary("huge", 100, unused=50)],
                       "unused_pct")
        self.assertEqual(len({round(b.r, 9) for b in bubbles}), 1)

    def test_no_two_circles_overlap(self):
        sizes = [1, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 100]
        bubbles = pack([summary(f"p{i}", n) for i, n in enumerate(sizes)],
                       "resource_count")
        self.assertEqual(len(bubbles), len(sizes))
        self.assertEqual(overlaps(list(bubbles)), [])

    def test_a_very_uneven_spread_still_does_not_overlap(self):
        """The pathological case for a greedy packer: one huge circle and a
        crowd of tiny ones."""
        summaries = [summary("giant", 10000)] + [summary(f"m{i}", 1) for i in range(25)]
        bubbles = pack(summaries, "resource_count")
        self.assertEqual(overlaps(list(bubbles)), [])

    def test_every_circle_fits_inside_the_unit_box(self):
        bubbles = pack([summary(f"p{i}", i + 1) for i in range(12)], "resource_count")
        for b in bubbles:
            self.assertGreaterEqual(b.x - b.r, 0.0)
            self.assertGreaterEqual(b.y - b.r, 0.0)
            self.assertLessEqual(b.x + b.r, 1.0)
            self.assertLessEqual(b.y + b.r, 1.0)

    def test_the_pack_is_centred(self):
        bubbles = pack([summary(f"p{i}", i + 1) for i in range(7)], "resource_count")
        mid_x = (min(b.x - b.r for b in bubbles) + max(b.x + b.r for b in bubbles)) / 2
        mid_y = (min(b.y - b.r for b in bubbles) + max(b.y + b.r for b in bubbles)) / 2
        self.assertAlmostEqual(mid_x, 0.5, places=6)
        self.assertAlmostEqual(mid_y, 0.5, places=6)


class DeterminismTests(unittest.TestCase):
    def test_the_same_data_packs_identically(self):
        summaries = [summary(f"p{i}", (i * 7) % 13 + 1) for i in range(15)]
        self.assertEqual(pack(summaries, "resource_count"),
                         pack(summaries, "resource_count"))

    def test_input_order_does_not_change_the_layout(self):
        """Nothing downstream orders these - the API serves a dict and the
        report serves JSON - so a layout that depended on it would drift."""
        summaries = [summary(f"p{i}", (i * 5) % 11 + 1) for i in range(12)]
        self.assertEqual(pack(summaries, "resource_count"),
                         pack(list(reversed(summaries)), "resource_count"))

    def test_equal_values_break_their_tie_on_the_id(self):
        forward = pack([summary("a", 5), summary("b", 5), summary("c", 5)], "resource_count")
        shuffled = pack([summary("c", 5), summary("a", 5), summary("b", 5)], "resource_count")
        self.assertEqual([b.id for b in forward],
                         [b.id for b in shuffled])


class NoCircleTests(unittest.TestCase):
    """A value that does not exist gets no circle. A minimum-size bubble would
    be a quantitative claim about a quantity there isn't one of."""

    def test_a_zero_resource_project_gets_no_circle(self):
        bubbles = pack([summary("live", 4), summary("gone", 0)], "resource_count")
        self.assertEqual([b.id for b in bubbles], ["live"])

    def test_a_project_with_nothing_unused_gets_no_circle_in_the_unused_views(self):
        summaries = [summary("clean", 10, unused=0), summary("idle", 10, unused=3)]
        for metric in ("unused_pct", "unused_count"):
            self.assertEqual([b.id for b in pack(summaries, metric)], ["idle"])

    def test_an_empty_project_has_no_percentage_and_no_circle(self):
        self.assertIsNone(metric_value(summary("gone", 0), "unused_pct"))
        self.assertEqual(pack([summary("gone", 0)], "unused_pct"), ())

    def test_cost_not_queried_draws_nothing_at_all(self):
        """Not a set of zero-cost bubbles, which would be a claim the scan never
        made - the panel explains instead."""
        self.assertEqual(pack([summary("p", 5, cost=None)], "cost"), ())

    def test_a_zero_cost_project_gets_no_circle(self):
        self.assertEqual(pack([summary("free", 5, cost="0")], "cost"), ())

    def test_a_negative_cost_gets_no_circle(self):
        """Area cannot represent a credit. The summary carries it as text."""
        self.assertIsNone(metric_value(summary("credited", 5, cost="-20"), "cost"))

    def test_a_metric_with_nothing_to_draw_returns_an_empty_pack(self):
        self.assertEqual(pack([summary("clean", 3, unused=0)], "unused_count"), ())


class ContractTests(unittest.TestCase):
    def test_the_metric_list_matches_the_one_settings_validates_against(self):
        """config.BUBBLE_METRIC_IDS (used by settings.py) matches the pack's metric ids."""
        self.assertEqual(METRIC_IDS, BUBBLE_METRIC_IDS)
        self.assertIn(DEFAULT_METRIC, METRIC_IDS)

    def test_every_metric_has_a_label_and_a_tooltip(self):
        for entry in METRICS:
            self.assertEqual(len(entry), 3)
            self.assertTrue(all(isinstance(part, str) and part for part in entry))

    def test_an_unknown_metric_is_refused_rather_than_guessed(self):
        with self.assertRaises(ValueError):
            pack([summary("p", 1)], "no_such_metric")
        with self.assertRaises(ValueError):
            metric_value(summary("p", 1), "no_such_metric")

    def test_pack_all_covers_every_metric(self):
        packs = pack_all([summary("p", 5, unused=2, cost="12")])
        self.assertEqual(set(packs), set(METRIC_IDS))

    def test_a_serialised_bubble_rounds_its_coordinates(self):
        """Stable bytes: the report embeds this JSON and the golden files
        compare it as text."""
        data = pack([summary("p", 5)], "resource_count")[0].as_dict()
        self.assertEqual(set(data), {"id", "x", "y", "r", "value"})
        for key in ("x", "y", "r", "value"):
            self.assertEqual(data[key], round(data[key], 6))

    def test_an_empty_input_packs_to_nothing(self):
        self.assertEqual(pack([], "resource_count"), ())
        self.assertEqual(pack_all([]), {m: [] for m in METRIC_IDS})


if __name__ == "__main__":
    unittest.main()
