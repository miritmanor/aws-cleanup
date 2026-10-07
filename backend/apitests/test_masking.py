"""backend/app/masking.py: pure functions, no app, no snapshot needed."""

import unittest

from app.masking import mask_row, mask_value


class MaskValueTests(unittest.TestCase):
    def test_masks_an_account_id_inside_an_arn(self):
        arn = "arn:aws:iam::111122223333:policy/BudgetGuard"
        masked = mask_value(arn)
        self.assertNotIn("111122223333", masked)
        self.assertTrue(masked.startswith("arn:aws:iam::ACCOUNT-"))
        self.assertTrue(masked.endswith(":policy/BudgetGuard"))

    def test_masks_a_bare_account_id_with_no_arn_around_it(self):
        masked = mask_value("owned by account 111122223333, contact ops")
        self.assertNotIn("111122223333", masked)
        self.assertIn("ACCOUNT-", masked)

    def test_masks_every_occurrence_in_one_string(self):
        masked = mask_value("111122223333 and again 111122223333")
        self.assertNotIn("111122223333", masked)
        self.assertEqual(masked.count("ACCOUNT-"), 2)

    def test_same_account_id_always_produces_the_same_placeholder(self):
        bare = mask_value("111122223333")
        embedded = mask_value("prefix-111122223333-suffix")
        self.assertIn(bare, embedded)

    def test_different_account_ids_get_different_placeholders(self):
        a = mask_value("111122223333")
        b = mask_value("123456789012")
        self.assertNotEqual(a, b)

    def test_leaves_a_shorter_or_longer_digit_run_alone(self):
        # Not a 12-digit account id - an 11-digit or 13-digit run must survive,
        # since \b\d{12}\b would otherwise be too eager.
        self.assertEqual(mask_value("12345678901"), "12345678901")
        self.assertEqual(mask_value("1234567890123"), "1234567890123")

    def test_non_string_values_pass_through_unchanged(self):
        self.assertIsNone(mask_value(None))
        self.assertEqual(mask_value(42), 42)
        self.assertEqual(mask_value(True), True)

    def test_deterministic_across_repeated_calls(self):
        """Not a counter keyed to call order - a pure function of the id."""
        results = {mask_value("111122223333") for _ in range(5)}
        self.assertEqual(len(results), 1)


class MaskRowTests(unittest.TestCase):
    def test_masks_every_string_field_recursively(self):
        row = {
            "resource_id": "arn:aws:iam::111122223333:role/app",
            "notes": "granted by policy referencing 111122223333",
            "tags": {"Owner": "team-111122223333"},
            "connections": ["reads arn:aws:s3:::x", "111122223333 elsewhere"],
            "last_used_days": 4,
            "inferred": False,
        }
        masked = mask_row(row)
        blob = str(masked)
        self.assertNotIn("111122223333", blob)
        # Non-string fields are untouched.
        self.assertEqual(masked["last_used_days"], 4)
        self.assertEqual(masked["inferred"], False)

    def test_does_not_mutate_the_input(self):
        row = {"resource_id": "111122223333"}
        mask_row(row)
        self.assertEqual(row["resource_id"], "111122223333")


if __name__ == "__main__":
    unittest.main()
