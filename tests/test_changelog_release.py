"""tools/changelog_release.py: the notes a release ships with, and moving them out of
Unreleased into the version's own section."""

import importlib.util
import os
import unittest

from . import _PARENT

_spec = importlib.util.spec_from_file_location(
    "changelog_release", os.path.join(_PARENT, "tools", "changelog_release.py"))
cl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cl)

TAGGED = """# Changelog

## Unreleased

- New thing.
- Fixed thing.

## 0.1.0: initial public release

- First.
"""


class ReleaseNotesTests(unittest.TestCase):
    def test_notes_come_from_unreleased(self):
        self.assertEqual(cl.release_notes(TAGGED, "0.2.0"), "- New thing.\n- Fixed thing.")

    def test_a_version_section_wins_over_unreleased(self):
        self.assertEqual(cl.release_notes(TAGGED, "0.1.0"), "- First.")

    def test_an_empty_unreleased_gives_no_notes(self):
        self.assertEqual(cl.release_notes("# Changelog\n\n## Unreleased\n\n## 0.1.0\n", "0.2.0"), "")

    def test_a_longer_version_is_not_mistaken_for_this_one(self):
        text = "## Unreleased\n\n## 0.2.0.1\n\n- Other.\n"
        self.assertEqual(cl.release_notes(text, "0.2.0"), "")


class FinalizeTests(unittest.TestCase):
    def notes(self):
        return cl.release_notes(TAGGED, "0.2.0")

    def test_entries_move_under_the_new_version(self):
        out = cl.finalize(TAGGED, "0.2.0", self.notes())
        self.assertEqual(out, """# Changelog

## Unreleased

## 0.2.0

- New thing.
- Fixed thing.

## 0.1.0: initial public release

- First.
""")

    def test_running_twice_changes_nothing_more(self):
        once = cl.finalize(TAGGED, "0.2.0", self.notes())
        self.assertEqual(cl.finalize(once, "0.2.0", self.notes()), once)

    def test_an_entry_merged_after_the_tag_stays_unreleased(self):
        main = TAGGED.replace("- Fixed thing.\n", "- Fixed thing.\n- Merged later.\n")
        out = cl.finalize(main, "0.2.0", self.notes())
        unreleased = out.split("## 0.2.0")[0]
        self.assertIn("- Merged later.", unreleased)
        self.assertNotIn("- New thing.", unreleased)
        self.assertIn("- New thing.", out.split("## 0.2.0")[1])

    def test_no_entries_changes_nothing(self):
        self.assertEqual(cl.finalize(TAGGED, "0.2.0", ""), TAGGED)

    def test_a_changelog_without_unreleased_is_an_error(self):
        with self.assertRaises(ValueError):
            cl.finalize("# Changelog\n", "0.2.0", "- x")


if __name__ == "__main__":
    unittest.main()
