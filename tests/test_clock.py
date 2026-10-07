"""The run's clock: it does not move during a run, and a test can set it, which is
what makes rendered output byte-comparable."""

import unittest
from datetime import datetime, timedelta, timezone

from . import FROZEN_NOW
from .fakes import make_row

import aws_resource_audit as audit
from aws_resource_audit import config


class FrozenClockTests(unittest.TestCase):
    def tearDown(self):
        # Restore the suite clock however the test exited, or the next test fails instead.
        config.set_now(FROZEN_NOW)

    def test_the_suite_clock_is_pinned(self):
        """tests/__init__.py pins it, so fixtures anchor to a stated date."""
        self.assertEqual(audit.now(), FROZEN_NOW)

    def test_now_does_not_move_between_calls(self):
        """Every resource in one scan is measured against the same instant."""
        config.set_now(None)
        first = audit.now()
        self.assertEqual(audit.now(), first)
        self.assertEqual(audit.now(), first)

    def test_set_now_controls_everything_derived_from_it(self):
        config.set_now(datetime(2020, 3, 1, tzinfo=timezone.utc))
        self.assertEqual(audit.days_ago(datetime(2020, 2, 1, tzinfo=timezone.utc)), 29)
        config.set_now(datetime(2021, 3, 1, tzinfo=timezone.utc))
        self.assertEqual(audit.days_ago(datetime(2020, 2, 1, tzinfo=timezone.utc)), 394)

    def test_an_unpinned_clock_still_works(self):
        """Nothing forces set_now() - a plain library caller gets the wall
        clock, frozen on first use."""
        config.set_now(None)
        before = datetime.now(timezone.utc)
        value = audit.now()
        self.assertLessEqual(before - timedelta(seconds=5), value)
        self.assertLessEqual(value, datetime.now(timezone.utc) + timedelta(seconds=5))


class DeterministicRenderTests(unittest.TestCase):
    """The property stage 0 exists to establish: same input, same bytes."""

    def _rows(self):
        return [make_row("EC2Instance", "i-0abc", "web"),
                make_row("S3Bucket", "assets", "assets")]

    def test_the_html_render_is_byte_identical_across_calls(self):
        rows = self._rows()
        self.assertEqual(audit.render_html_report(rows),
                         audit.render_html_report(rows))

    def test_the_generated_line_follows_the_pinned_clock(self):
        """The one field that made the report non-comparable before."""
        config.set_now(datetime(2019, 7, 4, 9, 30, 0, tzinfo=timezone.utc))
        try:
            html = audit.render_html_report(self._rows())
        finally:
            config.set_now(FROZEN_NOW)
        self.assertIn("2019-07-04 09:30:00 UTC", html)


if __name__ == "__main__":
    unittest.main()
