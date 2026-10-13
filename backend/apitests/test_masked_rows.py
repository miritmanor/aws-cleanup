"""The whole synthetic corpus as the agent receives it: no identifier the scan recorded
appears anywhere in it. No langchain, so this runs in every image."""

import json
import re
import unittest

from . import _REPO  # noqa: F401  (sys.path fixup)
from .api_base import ApiTestCase
from app.privacy.patterns import GENERIC_WORDS, MIN_TEXT_MATCH

_IDENTIFIERS = ("account", "resource_id", "arn", "resource_key", "name",
                "project_group", "project_id")


class MaskedCorpusTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.rows = self.write_snapshot()
        from app import stores
        self.store = stores.snapshot_store()
        self.blob = json.dumps(self.store.get_masked_rows())

    def test_no_recorded_identifier_reaches_the_agent(self):
        leaked = set()
        for row in self.rows:
            values = [row.get(f) for f in _IDENTIFIERS] + list((row.get("tags") or {}).values())
            for value in values:
                if (isinstance(value, str) and len(value) >= MIN_TEXT_MATCH
                        and value.lower() not in GENERIC_WORDS
                        and re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(value), self.blob)):
                    leaked.add(value)
        self.assertEqual(sorted(leaked), [])

    def test_no_account_id_reaches_the_agent(self):
        self.assertNotIn("123456789012", self.blob)
        self.assertNotIn("111122223333", self.blob)

    def test_every_token_reveals_to_a_real_value(self):
        pseudonymizer, masked = self.store.get_masked_view()
        for row, shown in zip(self.rows, masked):
            self.assertEqual(pseudonymizer.reveal(shown["resource_id"]), row["resource_id"])


if __name__ == "__main__":
    unittest.main()
