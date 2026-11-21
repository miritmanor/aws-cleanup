"""ScanLog tees the console into a file without disturbing the sink under it, and a
file that fails never stops the scan."""

import os
import sys
import tempfile
import unittest
from unittest import mock

from . import _PARENT  # noqa: F401  (import for the sys.path fixup side effect)

from aws_resource_audit import console
from aws_resource_audit.errors import AuditError, ScanCancelled
from aws_resource_audit.scan_log import ScanLog

LOGGER = "aws_resource_audit.scan_log"


class ScanLogTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "scan.txt")
        self.seen = []
        self.sink = lambda message, stream, end, flush: self.seen.append(
            (message, stream, end, flush))
        self.addCleanup(console.set_sink, console.set_sink(self.sink))

    def _read(self):
        with open(self.path, encoding="utf-8") as handle:
            return handle.read()

    def test_every_message_reaches_the_previous_sink_unchanged_and_the_file(self):
        with ScanLog(self.path):
            console.say("  EC2 instances  ", end="", flush=True)
            console.say("    3")
            console.warn("denied")
        self.assertEqual(self.seen, [("  EC2 instances  ", sys.stdout, "", True),
                                     ("    3", sys.stdout, "\n", False),
                                     ("denied", sys.stderr, "\n", False)])
        self.assertEqual(self._read(), "  EC2 instances      3\ndenied\n")

    def test_the_previous_sink_is_restored_on_exit(self):
        with ScanLog(self.path):
            self.assertIsNot(console._SINK, self.sink)
        self.assertIs(console._SINK, self.sink)

    def test_an_audit_error_is_written_last_and_still_raised(self):
        with self.assertRaises(AuditError):
            with ScanLog(self.path):
                console.say("started")
                raise AuditError("credentials rejected")
        self.assertIs(console._SINK, self.sink)
        self.assertEqual(self._read(), "started\n\ncredentials rejected\n")

    def test_a_cancelled_scan_writes_its_message_and_no_traceback(self):
        with self.assertRaises(ScanCancelled):
            with ScanLog(self.path):
                raise ScanCancelled("The scan was stopped.")
        self.assertEqual(self._read(), "\nThe scan was stopped.\n")

    def test_an_unexpected_error_keeps_its_traceback_in_the_file(self):
        with self.assertRaises(RuntimeError):
            with ScanLog(self.path):
                raise RuntimeError("bug here")
        self.assertIs(console._SINK, self.sink)
        text = self._read()
        self.assertIn("Traceback", text)
        self.assertIn("RuntimeError: bug here", text)

    def test_a_path_that_cannot_be_opened_never_stops_the_scan(self):
        with self.assertLogs(LOGGER, "WARNING"):
            with ScanLog(self.tmp.name):           # a directory
                console.say("still running")
                ran = True
        self.assertTrue(ran)
        self.assertEqual(self.seen, [("still running", sys.stdout, "\n", False)])
        self.assertIs(console._SINK, self.sink)

    def test_an_unopenable_path_does_not_hide_the_scans_own_error(self):
        with self.assertLogs(LOGGER, "WARNING"):
            with self.assertRaises(AuditError) as caught:
                with ScanLog(self.tmp.name):
                    raise AuditError("credentials rejected")
        self.assertEqual(str(caught.exception), "credentials rejected")
        self.assertIs(console._SINK, self.sink)

    def test_a_write_that_fails_mid_scan_warns_once_and_the_console_carries_on(self):
        with ScanLog(self.path) as log:
            console.say("first")
            broken = mock.Mock()
            broken.write.side_effect = OSError("disk full")
            real = log._file
            log._file = broken
            with self.assertLogs(LOGGER, "WARNING") as caught:
                console.say("second")
                console.say("third")
            real.close()
        self.assertEqual(len(caught.records), 1)
        broken.close.assert_called_once_with()
        self.assertEqual([m for m, *_ in self.seen], ["first", "second", "third"])
        self.assertEqual(self._read(), "first\n")

    def test_a_close_that_fails_is_a_warning_not_an_error(self):
        with self.assertLogs(LOGGER, "WARNING") as caught:
            with ScanLog(self.path) as log:
                real = log._file
                broken = mock.Mock()
                broken.close.side_effect = OSError("gone")
                log._file = broken
        real.close()
        self.assertEqual(len(caught.records), 1)
        self.assertIn("closing the scan log", caught.records[0].getMessage())
        self.assertIs(console._SINK, self.sink)


if __name__ == "__main__":
    unittest.main()
