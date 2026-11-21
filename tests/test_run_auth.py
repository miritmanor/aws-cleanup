"""run() and collect_all() when AWS stops accepting the scan's credentials: nothing saved,
the log ends with the reason, and the auth check comes before a stop request."""

import os
import tempfile
import unittest
from unittest import mock

from botocore.hooks import HierarchicalEmitter

from . import _PARENT  # noqa: F401  (import for the sys.path fixup side effect)

import aws_resource_audit as audit
from aws_resource_audit.settings import Settings

from .test_run_pipeline import (
    _Args, _PermissiveSession, _session_with_one_instance_and_one_bucket,
    collect_module, run_module)

IDENT = {"Account": "123456789012", "Arn": "arn:aws:iam::123456789012:user/tester"}
OUTPUTS = ("snapshot", "csv", "json", "html", "mermaid", "audit", "groups")
EXPIRED = "Could not authenticate to AWS: credentials expired"


class _StubGuard:
    """An AuthGuard whose check() raises AuditError on its Nth call."""

    def __init__(self, fail_on):
        self.fail_on = fail_on
        self.calls = 0

    def install(self, session):
        return self

    def check(self, profile=None):
        self.calls += 1
        if self.calls == self.fail_on:
            raise audit.AuditError(EXPIRED)


class _ScanCase(unittest.TestCase):
    """A scan against a faked session, writing into a temporary directory."""

    session = None

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = Settings(output_dir=self.tmp.name)
        self.paths = audit.output_paths(self.settings)
        session = self.session or _session_with_one_instance_and_one_bucket()
        for p in (
            mock.patch.object(run_module, "authenticated_session",
                              return_value=(session, IDENT)),
            mock.patch.object(run_module, "get_regions", return_value=["us-east-1"]),
        ):
            p.start()
            self.addCleanup(p.stop)
        quiet = audit.console.set_quiet(True)
        self.addCleanup(audit.console.set_quiet, quiet)

    def assert_nothing_saved(self):
        for kind in OUTPUTS:
            self.assertFalse(os.path.exists(self.paths[kind]), kind)


class RejectedCredentialsTests(_ScanCase):
    """A credential AWS stops accepting ends the scan with nothing saved and a log."""

    def _run_rejected_on(self, call):
        guard = _StubGuard(fail_on=call)
        with mock.patch.object(run_module, "AuthGuard", return_value=guard):
            with self.assertRaises(audit.AuditError):
                run_module.run(_Args(), settings=self.settings)
        return guard

    def test_a_rejection_at_any_early_check_saves_nothing_and_keeps_the_region_list(self):
        known = b'{"accounts": {"123456789012": {"regions": ["eu-west-1"]}}}'
        for call in (1, 2, 3):
            with self.subTest(call=call):
                with open(self.paths["active_regions"], "wb") as handle:
                    handle.write(known)
                guard = self._run_rejected_on(call)
                self.assertEqual(guard.calls, call)
                self.assert_nothing_saved()
                with open(self.paths["active_regions"], "rb") as handle:
                    self.assertEqual(handle.read(), known)

    def test_the_scan_log_ends_with_the_error_text(self):
        self._run_rejected_on(3)
        with open(self.paths["scan_log"], encoding="utf-8") as handle:
            self.assertTrue(handle.read().rstrip().endswith(EXPIRED))

    def test_a_rejection_noticed_only_after_collection_still_saves_nothing(self):
        guard = _StubGuard(fail_on=None)
        real = run_module.collect_all

        def collect_then_reject(*args, **kwargs):
            result = real(*args, **kwargs)
            guard.fail_on = guard.calls + 1
            return result

        with mock.patch.object(run_module, "AuthGuard", return_value=guard), \
             mock.patch.object(run_module, "collect_all", collect_then_reject):
            with self.assertRaises(audit.AuditError):
                run_module.run(_Args(), settings=self.settings)
        self.assert_nothing_saved()


class _EventSession(_PermissiveSession):
    """A fake session with a real botocore event emitter, which AuthGuard registers on."""

    def __init__(self):
        super().__init__()
        self.events = HierarchicalEmitter()


class RealGuardTests(_ScanCase):
    """The real AuthGuard, fed through the events a botocore client would emit."""

    def setUp(self):
        self.session = _EventSession()
        super().setUp()

    def _expired_twice(self, session, region):
        request = {"context": {"client_region": region}}
        response = (None, {"Error": {"Code": "ExpiredToken",
                                     "Message": "The security token included in the "
                                                "request is expired"}})
        operation = mock.Mock()
        operation.name = "DescribeInstances"
        for _ in range(2):
            session.events.emit("needs-retry.ec2.DescribeInstances", response=response,
                                operation=operation, request_dict=request)
        return []

    def test_a_credential_rejected_twice_in_one_request_stops_the_scan_with_help(self):
        collector = lambda session, region: self._expired_twice(session, region)  # noqa: E731
        with mock.patch.object(collect_module, "COLLECTORS_PER_REGION",
                               [("Fake things", collector)]):
            with self.assertLogs("aws_resource_audit.collect.auth_guard", "WARNING"):
                with self.assertRaises(audit.AuditError) as caught:
                    run_module.run(_Args(), settings=self.settings)
        text = str(caught.exception)
        self.assertIn("ExpiredToken on DescribeInstances in us-east-1", text)
        self.assertIn("no results were saved", text)
        self.assertIn('profile "fake-profile"', text)
        self.assert_nothing_saved()


class ScanLogTests(_ScanCase):
    """A scan that finishes writes what it printed, and puts the console back."""

    def test_a_scan_writes_what_it_printed_to_the_log_and_restores_the_console(self):
        audit.console.set_quiet(False)
        lines = []
        previous = audit.console.set_sink(
            lambda message, stream, end, flush: lines.append(f"{message}{end}"))
        self.addCleanup(audit.console.set_sink, previous)
        sink_before = audit.console._SINK
        run_module.run(_Args(), settings=self.settings)
        self.assertIs(audit.console._SINK, sink_before)
        with open(self.paths["scan_log"], encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("Regions: us-east-1", text)
        self.assertEqual(text, "".join(lines))


class CollectAuthCheckTests(unittest.TestCase):

    def setUp(self):
        quiet = audit.console.set_quiet(True)
        self.addCleanup(audit.console.set_quiet, quiet)

    def test_a_failing_check_propagates_out_of_collect_all(self):
        calls = []

        def check():
            calls.append(1)
            if len(calls) == 2:
                raise audit.AuditError("rejected")

        with self.assertRaises(audit.AuditError) as caught:
            collect_module.collect_all(_PermissiveSession(), ["us-east-1"],
                                       no_cost=True, check_auth=check)
        self.assertEqual(str(caught.exception), "rejected")
        self.assertEqual(len(calls), 2)

    def test_the_auth_check_wins_over_a_stop_request(self):
        def check():
            raise audit.AuditError("rejected")

        with self.assertRaises(audit.AuditError) as caught:
            collect_module.collect_all(_PermissiveSession(), ["us-east-1"], no_cost=True,
                                       check_auth=check, should_stop=lambda: True)
        self.assertNotIsInstance(caught.exception, audit.ScanCancelled)
        self.assertEqual(str(caught.exception), "rejected")

    def test_a_stop_request_still_cancels_when_the_credentials_are_fine(self):
        with self.assertRaises(audit.ScanCancelled):
            collect_module.collect_all(_PermissiveSession(), ["us-east-1"], no_cost=True,
                                       check_auth=lambda: None, should_stop=lambda: True)


class DefaultVpcLookupPerRegionTests(unittest.TestCase):

    def test_a_failed_default_vpc_lookup_marks_only_its_own_region(self):
        """The bug: one region's failure made default VPCs unknown in every region."""
        def lookup(session, region):
            return (set(), False) if region == "eu-west-1" else ({f"vpc-{region}"}, True)

        quiet = audit.console.set_quiet(True)
        self.addCleanup(audit.console.set_quiet, quiet)
        with mock.patch.object(collect_module, "collect_default_vpc_ids", lookup), \
             mock.patch.object(collect_module, "COLLECTORS_PER_REGION", []):
            result = collect_module.collect_all(
                _PermissiveSession(), ["us-east-1", "eu-west-1", "ap-south-1"],
                no_cost=True)
        self.assertEqual(result.default_vpcs_unknown, {"eu-west-1"})
        self.assertEqual(result.default_vpc_ids, {"vpc-us-east-1", "vpc-ap-south-1"})


if __name__ == "__main__":
    unittest.main()
