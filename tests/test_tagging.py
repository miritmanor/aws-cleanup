"""--tag-groups: what a dry run says before anything is written to AWS."""

import contextlib
import io
import unittest

from . import FROZEN_ACCOUNT
from .fakes import make_row

from aws_resource_audit.naming.tagging import apply_group_tagging


def _named(tags):
    row = make_row("LambdaFunction", "orders-worker", "orders-worker", tags=tags)
    row.update(project_group="Shop", grouping_method="named")
    return row


class DryRunTests(unittest.TestCase):
    def _said(self, rows):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            apply_group_tagging(rows, session=None, account_id=FROZEN_ACCOUNT, confirm=False)
        return out.getvalue()

    def test_a_replaced_project_tag_is_named(self):
        """Naming a tag project then tagging it rewrites the tag."""
        self.assertIn("(replaces Project=orders)", self._said([_named({"Project": "orders"})]))

    def test_an_untagged_resource_says_nothing_about_replacing(self):
        self.assertNotIn("replaces", self._said([_named({})]))


if __name__ == "__main__":
    unittest.main()
