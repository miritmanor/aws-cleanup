"""backend/app/privacy: what may reach the LLM. Pure functions and classes; no app, no
langchain, so these run in every image, including one built without the agent."""

import json
import os
import stat
import tempfile
import unittest

from . import _REPO  # noqa: F401  (sys.path fixup)
from app.privacy.key import KEY_FILENAME, load_or_create_key
from app.privacy.pseudonymizer import Pseudonymizer

KEY = b"k" * 32
ACCOUNT = "111122223333"
ROW = {
    "service": "Lambda", "region": "us-east-1", "account": ACCOUNT,
    "resource_id": "payments-api-handler",
    "arn": f"arn:aws:lambda:us-east-1:{ACCOUNT}:function:payments-api-handler",
    "resource_key": "Lambda:us-east-1:payments-api-handler",
    "name": "payments-api-handler", "project_group": "acme-payments",
    "project_id": "tag:Project=acme-payments",
    "tags": {"Owner": "alice@acme.example", "Environment": "production"},
    "notes": "reads bucket acme-invoices-2024 in account 999988887777",
    "flag": "STALE", "tier": "runtime", "last_used_days": 400,
    "_references": [{"target_id": "acme-invoices-2024"}],
}
SECRETS = ("111122223333", "999988887777", "payments-api-handler", "acme-payments",
           "alice@acme.example", "acme-invoices-2024", "arn:aws")


def display(row):
    """What display_row would hand the agent: tags as text, no private fields."""
    out = {k: v for k, v in row.items() if not k.startswith("_")}
    out["tags"] = "; ".join(f"{k}={v}" for k, v in row["tags"].items())
    return out


class MaskRowTests(unittest.TestCase):
    def setUp(self):
        self.p = Pseudonymizer(KEY, [ROW])
        self.masked = self.p.mask_row(display(ROW))
        self.blob = json.dumps(self.masked)

    def test_no_identifier_survives_anywhere_in_the_row(self):
        for secret in SECRETS:
            self.assertNotIn(secret, self.blob)

    def test_identity_fields_become_tokens_of_their_kind(self):
        self.assertTrue(self.masked["account"].startswith("ACCT-"))
        self.assertTrue(self.masked["resource_id"].startswith("RES-"))
        self.assertTrue(self.masked["project_group"].startswith("PROJ-"))

    def test_schema_values_and_numbers_are_sent_as_they_are(self):
        for field in ("service", "region", "flag", "tier", "last_used_days"):
            self.assertEqual(self.masked[field], ROW[field])

    def test_a_category_tag_value_stays_readable(self):
        self.assertIn("Environment=production", self.masked["tags"])

    def test_the_same_value_gets_the_same_token_wherever_it_appears(self):
        self.assertEqual(self.masked["name"], self.masked["resource_id"])
        self.assertEqual(self.p.mask_text("payments-api-handler"), self.masked["resource_id"])

    def test_an_identifier_the_scan_never_recorded_is_still_caught(self):
        self.assertNotIn("999988887777", self.masked["notes"])
        self.assertIn("ID-", self.masked["notes"])

    def test_does_not_mutate_the_input(self):
        self.assertEqual(ROW["resource_id"], "payments-api-handler")


class MaskTextTests(unittest.TestCase):
    def setUp(self):
        self.p = Pseudonymizer(KEY, [ROW])

    def mask(self, text):
        return self.p.mask_text(text)

    def test_a_question_naming_a_resource_is_masked(self):
        masked = self.mask("why is payments-api-handler stale?")
        self.assertNotIn("payments-api-handler", masked)
        self.assertTrue(masked.endswith(" stale?"))

    def test_a_known_value_inside_a_longer_word_is_left_alone(self):
        self.assertEqual(self.mask("acme-payments-v2"), "acme-payments-v2")

    def test_shapes(self):
        for text in ("arn:aws:s3:::other-bucket", "10.0.12.7", "10.0.0.0/16",
                     "bob@example.org", "i-0abc1234def567890", "sg-0123abcd",
                     "AKIAABCDEFGHIJKLMNOP", "db.cluster-x.us-east-1.rds.amazonaws.com",
                     "123e4567-e89b-12d3-a456-426614174000", "123456789012"):
            with self.subTest(text=text):
                self.assertNotIn(text, self.mask(f"see {text} here"))

    def test_ordinary_words_and_regions_pass(self):
        text = "The Lambda in us-east-1 has had no invocations for 400 days."
        self.assertEqual(self.mask(text), text)

    def test_a_generic_name_is_not_hunted_for_in_prose(self):
        p = Pseudonymizer(KEY, [{"resource_id": "sg-1", "name": "default"}])
        self.assertEqual(p.mask_text("uses the default VPC"), "uses the default VPC")

    def test_masking_twice_changes_nothing(self):
        once = self.mask("payments-api-handler in 999988887777")
        self.assertEqual(self.mask(once), once)


class RevealTests(unittest.TestCase):
    def setUp(self):
        self.p = Pseudonymizer(KEY, [ROW])

    def test_reveal_undoes_mask_text(self):
        text = "payments-api-handler reads acme-invoices-2024 from 10.1.2.3"
        self.assertEqual(self.p.reveal(self.p.mask_text(text)), text)

    def test_an_unknown_token_is_left_as_written(self):
        self.assertEqual(self.p.reveal("RES-0000000000"), "RES-0000000000")

    def test_tokens_are_stable_across_instances_with_one_key(self):
        other = Pseudonymizer(KEY, [ROW])
        self.assertEqual(self.p.mask_text("acme-payments"), other.mask_text("acme-payments"))

    def test_a_different_key_gives_different_tokens(self):
        other = Pseudonymizer(b"z" * 32, [ROW])
        self.assertNotEqual(self.p.mask_text("acme-payments"), other.mask_text("acme-payments"))


class KeyFileTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def test_created_once_then_reused(self):
        first = load_or_create_key(self.dir)
        self.assertEqual(len(first), 32)
        self.assertEqual(load_or_create_key(self.dir), first)

    def test_only_the_owner_can_read_it(self):
        load_or_create_key(self.dir)
        mode = stat.S_IMODE(os.stat(os.path.join(self.dir, KEY_FILENAME)).st_mode)
        self.assertEqual(mode, 0o600)


if __name__ == "__main__":
    unittest.main()
