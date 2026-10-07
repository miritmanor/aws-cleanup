"""The seams non-terminal callers use (set_sink, settings=, progress=, should_stop=):
omitted, behaviour is unchanged; used, they must actually work."""

import inspect
import os
import sys
import tempfile
import unittest

from . import _PARENT  # noqa: F401  (import for the sys.path fixup side effect)
from . import corpus

import aws_resource_audit as audit
from aws_resource_audit import console, render, run
from aws_resource_audit.settings import Settings


class ConsoleSinkTests(unittest.TestCase):
    """Redirecting the one channel every layer reports through."""

    def setUp(self):
        self.lines = []
        # (message, stream) - the stream is the only distinction say/detail/warn
        # make, so a sink that loses it cannot tell a warning from progress.
        self.previous = console.set_sink(
            lambda message, stream, end, flush: self.lines.append((message, stream)))
        self.addCleanup(console.set_sink, self.previous)

    def test_say_reaches_the_sink_instead_of_stdout(self):
        console.say("collecting")
        self.assertEqual(self.lines, [("collecting", sys.stdout)])

    def test_warn_stays_distinguishable_from_progress(self):
        """A sink has to be able to keep these apart: one is a running
        commentary, the other is something the user has to act on."""
        console.say("collecting")
        console.warn("could not read metric")
        self.assertEqual([stream for _, stream in self.lines],
                         [sys.stdout, sys.stderr])

    def test_detail_is_still_gated_on_verbose(self):
        """--debug output must not start appearing merely because someone
        installed a sink."""
        console.detail("env var dump")
        self.assertEqual(self.lines, [])

        previous_verbose = console.VERBOSE
        console.VERBOSE = True
        try:
            console.detail("env var dump")
        finally:
            console.VERBOSE = previous_verbose
        self.assertEqual(self.lines, [("env var dump", sys.stdout)])

    def test_quiet_still_wins(self):
        """set_quiet is what the rest of the suite silences output with. A sink
        must not become a way to hear what quiet turned off."""
        previous = console.set_quiet(True)
        try:
            console.say("collecting")
            console.warn("problem")
        finally:
            console.set_quiet(previous)
        self.assertEqual(self.lines, [])

    def test_set_sink_returns_the_previous_one(self):
        """So a caller restores what was there rather than asserting the
        default - the same contract set_quiet has."""
        marker = lambda *a: None                                    # noqa: E731
        previous = console.set_sink(marker)
        restored = console.set_sink(previous)
        self.assertIs(restored, marker)

    def test_passing_none_restores_printing(self):
        console.set_sink(None)
        console.say("")          # would raise if _SINK were left as None
        console.set_sink(lambda message, stream, end, flush: self.lines.append((message, stream)))


class ExplicitSettingsTests(unittest.TestCase):
    """Settings handed in, never read from the process's current directory."""

    def _snapshot_in(self, directory):
        """Written through output_paths(), not a hand-built path."""
        rows, _contracted, full, notes = corpus.build()
        paths = audit.output_paths(Settings(output_dir=directory))
        audit.write_snapshot(
            paths["snapshot"], rows, full, notes,
            account="123456789012", regions=["us-east-1"], scanned_at=audit.now())

    def test_render_uses_the_settings_it_is_given(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._snapshot_in(tmp)
            quiet = console.set_quiet(True)
            try:
                render(object(), settings=Settings(output_dir=tmp))
            finally:
                console.set_quiet(quiet)
            results = os.path.join(tmp, audit.RESULTS_DIRNAME)
            for name in ("aws_inventory.csv", "aws_inventory.html",
                         "aws_inventory.json",
                         "aws_resource_graph.mmd", "grouping_audit.md"):
                self.assertTrue(os.path.exists(os.path.join(results, name)), name)

    def test_an_audit_config_in_the_cwd_is_not_consulted(self):
        """An INVALID config in the cwd must not reach a render given explicit settings."""
        with tempfile.TemporaryDirectory() as scan_dir, \
                tempfile.TemporaryDirectory() as cwd:
            self._snapshot_in(scan_dir)
            with open(os.path.join(cwd, "audit_config.json"), "w") as f:
                f.write('{"no_such_setting": true}')

            previous_cwd = os.getcwd()
            os.chdir(cwd)
            quiet = console.set_quiet(True)
            try:
                render(object(), settings=Settings(output_dir=scan_dir))
            finally:
                console.set_quiet(quiet)
                os.chdir(previous_cwd)

            self.assertTrue(os.path.exists(os.path.join(
                scan_dir, audit.RESULTS_DIRNAME, "aws_inventory.html")))
            # And the decoy really would have stopped it, so the test above is
            # not passing because a broken config is harmless.
            with self.assertRaises(audit.AuditError):
                audit.load_settings(os.path.join(cwd, "audit_config.json"))


class RunSignatureTests(unittest.TestCase):
    """run()'s seam keywords exist, checked by signature."""

    def test_run_accepts_settings_and_progress(self):
        params = inspect.signature(run).parameters
        self.assertIn("settings", params)
        self.assertIn("progress", params)
        self.assertIn("should_stop", params)
        for name in ("settings", "progress", "should_stop"):
            self.assertIs(params[name].default, None,
                          f"{name} must default to None - omitting it is what keeps "
                          "the CLI's behaviour unchanged")
            self.assertEqual(params[name].kind, inspect.Parameter.KEYWORD_ONLY,
                             f"{name} must be keyword-only so positional callers "
                             "cannot pass one by accident")

    def test_render_accepts_settings(self):
        params = inspect.signature(render).parameters
        self.assertIn("settings", params)
        self.assertIs(params["settings"].default, None)
        self.assertEqual(params["settings"].kind, inspect.Parameter.KEYWORD_ONLY)


class ConsoleMirrorTests(unittest.TestCase):
    """Console lines also arrive as log records (not via the sink)."""

    LOGGER = "aws_resource_audit.console"

    def setUp(self):
        # A discarding sink: the printed half is the sink tests' subject, and
        # letting it through would put this suite's fixtures on the terminal.
        self.addCleanup(console.set_sink,
                        console.set_sink(lambda message, stream, end, flush: None))
        # The mirror must not become a way to hear what these turned off, so
        # every test here sets them back the way the sink tests do.
        self.addCleanup(console.set_quiet, console.set_quiet(False))
        previous = console.VERBOSE
        self.addCleanup(setattr, console, "VERBOSE", previous)

    def test_a_two_fragment_line_is_one_record(self):
        """The terminal idiom - a label, then the count that fills it in - is
        one line, and must not arrive as two records."""
        with self.assertLogs(self.LOGGER, level="INFO") as caught:
            console.say("  EC2 instances  ", end="")
            console.say("    3")
        self.assertEqual(len(caught.records), 1)
        self.assertEqual(caught.records[0].getMessage(), "  EC2 instances      3")

    def test_a_warning_mid_line_does_not_absorb_the_progress_label(self):
        """A warning mid-line is its own record, not glued to the label."""
        with self.assertLogs(self.LOGGER, level="INFO") as caught:
            console.say("  EC2 instances  ", end="")
            console.warn("permission denied")
            console.say("    0")
        by_level = [(r.levelno, r.getMessage()) for r in caught.records]
        self.assertEqual(by_level, [
            (20, "  EC2 instances"),
            (30, "permission denied"),
            (20, "    0"),
        ])

    def test_warn_is_mirrored_at_warning(self):
        with self.assertLogs(self.LOGGER, level="WARNING") as caught:
            console.warn("could not read metric")
        self.assertEqual(caught.records[0].levelno, 30)

    def test_detail_is_mirrored_at_debug(self):
        console.VERBOSE = True
        with self.assertLogs(self.LOGGER, level="DEBUG") as caught:
            console.detail("env var X=y")
        self.assertEqual(caught.records[0].levelno, 10)

    def test_quiet_silences_the_mirror_too(self):
        console.set_quiet(True)
        logger = __import__("logging").getLogger(self.LOGGER)
        with self.assertLogs(self.LOGGER, level="DEBUG") as caught:
            console.say("collecting")
            console.warn("a problem")
            logger.info("only this one")
        self.assertEqual([r.getMessage() for r in caught.records], ["only this one"])


if __name__ == "__main__":
    unittest.main()
