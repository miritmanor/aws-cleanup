"""run() end to end with AWS faked: rows collected and analysed, debug dumps in runtime/,
snapshot and outputs in results/, rendered through the file."""

import importlib
import json
import os
import tempfile
import unittest
from collections import defaultdict
from unittest import mock

import aws_resource_audit as audit
from aws_resource_audit.settings import Settings

# By module name: `from aws_resource_audit import run` binds the function, not the module.
run_module = importlib.import_module("aws_resource_audit.run")
collect_module = importlib.import_module("aws_resource_audit.collect.collect")

from . import fakes


class _PermissiveClient(fakes.FakeClient):
    """A FakeClient that returns empty results for unstubbed operations, so every
    collector can run."""

    def _resolve(self, operation, kwargs):
        if operation not in self.responses:
            return defaultdict(list)
        return super()._resolve(operation, kwargs)


class _PermissiveSession(fakes.FakeSession):
    def client(self, service_name, region_name=None, config=None, **kwargs):
        if service_name not in self._clients:
            self._clients[service_name] = _PermissiveClient(service_name, {})
        return self._clients[service_name]


class _Args:
    """The parsed-options object run() reads. Seven attributes, no parsing."""
    debug = False
    profile = "fake-profile"
    regions = ["us-east-1"]
    all_regions = False
    no_cost = True          # never reach for the chargeable call in a test
    tag_groups = False
    confirm = False


def _session_with_one_instance_and_one_bucket():
    session = _PermissiveSession()
    session._clients["ec2"] = _PermissiveClient("ec2", {
        "describe_instances": {"Reservations": [{"Instances": [{
            "InstanceId": "i-0abc", "ImageId": "ami-0123",
            "InstanceType": "t3.micro", "State": {"Name": "running"},
            "LaunchTime": fakes.recent(),
            "Tags": [{"Key": "Name", "Value": "web-server"}],
            "OnlyInTheRawCapture": "no column selects this",
        }]}]},
    })
    session._clients["s3"] = _PermissiveClient("s3", {
        "list_buckets": {"Buckets": [{"Name": "a-bucket", "BucketRegion": "us-east-1",
                                      "CreationDate": fakes.recent()}]},
    })
    session._clients["cloudwatch"] = _PermissiveClient(
        "cloudwatch", fakes.metrics_at(fakes.recent()))
    return session


class RunPipelineTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = Settings(output_dir=self.tmp.name)
        self.paths = audit.output_paths(self.settings)

        session = _session_with_one_instance_and_one_bucket()
        patches = [
            mock.patch.object(run_module, "authenticated_session",
                              return_value=(session, {"Account": "123456789012",
                                                      "Arn": "arn:aws:iam::123456789012:user/tester"})),
            mock.patch.object(run_module, "get_regions", return_value=["us-east-1"]),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

        quiet = audit.console.set_quiet(True)
        self.addCleanup(audit.console.set_quiet, quiet)
        self.snapshot = run_module.run(_Args(), settings=self.settings)

    def test_it_collected_and_analysed_the_rows(self):
        services = {r["service"] for r in self.snapshot.rows}
        self.assertIn("EC2Instance", services)
        self.assertIn("S3Bucket", services)
        # apply_tiers stamps every row (default runtime); project_group is not asserted.
        for row in self.snapshot.rows:
            self.assertTrue(row["tier"], row["resource_id"])

    def test_the_finished_outputs_land_in_results(self):
        for kind in ("csv", "json", "html", "mermaid", "audit", "groups", "snapshot"):
            path = self.paths[kind]
            self.assertTrue(os.path.exists(path), kind)
            self.assertEqual(os.path.basename(os.path.dirname(path)),
                             audit.RESULTS_DIRNAME, kind)

    def test_the_debug_dumps_land_in_runtime(self):
        for kind in ("raw_capture", "normalized_scan_results"):
            path = self.paths[kind]
            self.assertTrue(os.path.exists(path), kind)
            self.assertEqual(os.path.basename(os.path.dirname(path)),
                             audit.RUNTIME_DIRNAME, kind)

    def test_the_raw_capture_holds_what_no_column_selected(self):
        """The raw capture holds fields the row has no column for."""
        with open(self.paths["raw_capture"]) as handle:
            captured = [json.loads(line) for line in handle if line.strip()]
        instances = [c for c in captured if c["service"] == "EC2Instance"]
        self.assertEqual(len(instances), 1)
        self.assertEqual(instances[0]["resource_id"], "i-0abc")
        self.assertEqual(instances[0]["raw"]["OnlyInTheRawCapture"],
                         "no column selects this")

    def test_the_normalized_dump_is_the_rows_before_analysis(self):
        with open(self.paths["normalized_scan_results"]) as handle:
            dump = json.load(handle)
        self.assertIn("captured_at", dump)
        ids = {r["resource_id"] for r in dump["rows"]}
        self.assertEqual(ids, {r["resource_id"] for r in self.snapshot.rows})

    def test_it_returned_the_snapshot_it_wrote(self):
        """run() returns what is on disk, not the in-memory rows."""
        on_disk = audit.read_snapshot(self.paths["snapshot"])
        self.assertEqual(len(on_disk.rows), len(self.snapshot.rows))
        self.assertEqual(on_disk.account, "123456789012")


DENIED = ("An error occurred (AccessDenied) when calling the ListUsers operation: "
          "User: arn:aws:iam::123456789012:user/auditor is not authorized to perform: "
          "iam:ListUsers on resource: arn:aws:iam::123456789012:user/ because no "
          "identity-based policy allows the iam:ListUsers action")


class _DenyingClient(_PermissiveClient):
    """Every operation refused, the way a credential with no policies behaves."""

    def _resolve(self, operation, kwargs):
        raise fakes.client_error(operation=operation)


class CollectorFailureTests(unittest.TestCase):
    """A collector that cannot look versus one that finds nothing."""

    def setUp(self):
        quiet = audit.console.set_quiet(True)
        self.addCleanup(audit.console.set_quiet, quiet)

    def test_a_raising_collector_is_counted_not_collected(self):
        """The scan survives it, and the failure does not become a resource."""
        def exploding(session, region):
            raise fakes.client_error(operation="DescribeThings")

        with mock.patch.object(collect_module, "COLLECTORS_PER_REGION",
                               [("Exploding things", exploding)]):
            result = collect_module.collect_all(_PermissiveSession(), ["us-east-1"],
                                                no_cost=True)
        self.assertEqual(result.normalized_scan_results, [])
        self.assertEqual(result.failed_lookups, 1)

    def test_a_denied_lookup_never_reaches_the_row_set(self):
        """A failed lookup is console output, not a row."""
        denied = _PermissiveSession()
        denied._clients["ec2"] = _PermissiveClient("ec2", {
            "describe_instances": fakes.client_error(operation="DescribeInstances"),
        })
        with mock.patch.object(collect_module, "COLLECTORS_PER_REGION",
                               [("EC2 instances", audit.collect_ec2_instances)]):
            result = collect_module.collect_all(denied, ["us-east-1"], no_cost=True)
        self.assertEqual(result.normalized_scan_results, [])
        self.assertEqual(result.failed_lookups, 1)

    def test_the_count_column_separates_resources_from_failed_lookups(self):
        """The regression this exists for: every denied collector printing "1"
        - the one error row - which reads as one of every resource type."""
        err = audit.error_row("IAMUser", "global", "ERROR", DENIED)
        self.assertEqual(collect_module._count_line(0, []).strip(), "-")
        self.assertEqual(collect_module._count_line(3, []).strip(), "3")

        denied = collect_module._count_line(0, [err])
        self.assertEqual(denied.split()[0], "ERR")
        self.assertIn("AccessDenied: iam:ListUsers", denied)

        mixed = collect_module._count_line(2, [err])
        self.assertEqual(mixed.split()[0], "2")
        self.assertIn("+1 ERR", mixed)

    def test_the_progress_event_counts_resources_not_error_rows(self):
        def exploding(session, region):
            raise fakes.client_error(operation="DescribeThings")

        events = []
        with mock.patch.object(collect_module, "COLLECTORS_PER_REGION",
                               [("Exploding things", exploding)]):
            collect_module.collect_all(_PermissiveSession(), ["us-east-1"],
                                       no_cost=True, report=lambda **e: events.append(e))
        done = [e for e in events if e.get("collector") == "Exploding things"
                and "count" in e]
        self.assertEqual([e["count"] for e in done], [0])
        self.assertEqual([e["errors"] for e in done], [1])

    def test_a_denied_scan_renders_an_empty_report_not_a_report_of_failures(self):
        """End to end: the account in the bug report, where every call was
        denied. The six outputs describe nothing, because nothing was found."""
        denied = fakes.FakeSession()
        denied.client = lambda service_name, **kw: _DenyingClient(service_name, {})

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        settings = Settings(output_dir=tmp.name)
        events = []
        with mock.patch.object(run_module, "authenticated_session",
                               return_value=(denied, {"Account": "123456789012"})), \
             mock.patch.object(run_module, "get_regions", return_value=["us-east-1"]):
            snapshot = run_module.run(_Args(), settings=settings,
                                      progress=events.append)

        self.assertEqual(snapshot.rows, [])
        with open(audit.output_paths(settings)["json"]) as handle:
            self.assertEqual(json.load(handle), [])
        # And the failures were real, rather than the scan having quietly
        # skipped everything - which would satisfy the assertions above too.
        done = [e for e in events if e.get("phase") == "done"][-1]
        self.assertEqual(done["rows"], 0)
        # At least one per collector call, and more where a collector makes
        # several separately-permissioned calls (IAM's four lists, X-Ray's two).
        self.assertGreaterEqual(done["errors"],
                                collect_module.total_collection_steps(["us-east-1"]))



class SavedRegionTests(unittest.TestCase):
    """A scan saves the regions it found things in; the next scan with no
    regions given covers them."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = Settings(output_dir=self.tmp.name)
        self.paths = audit.output_paths(self.settings)
        session = _session_with_one_instance_and_one_bucket()
        auth = mock.patch.object(run_module, "authenticated_session",
                                 return_value=(session, {"Account": "123456789012",
                                                         "Arn": "arn:aws:iam::123456789012:user/tester"}))
        auth.start()
        self.addCleanup(auth.stop)
        quiet = audit.console.set_quiet(True)
        self.addCleanup(audit.console.set_quiet, quiet)

    def _run(self, args):
        with mock.patch.object(run_module, "get_regions",
                               return_value=["us-east-1"]) as get_regions:
            run_module.run(args, settings=self.settings)
        return get_regions.call_args.kwargs["saved"]

    def test_a_scan_saves_where_it_found_resources_and_the_next_one_uses_it(self):
        class _NoRegions(_Args):
            regions = None

        self.assertIsNone(self._run(_NoRegions()))
        saved = audit.load_active_regions(self.paths["active_regions"], "123456789012")
        self.assertEqual(saved["regions"], ["us-east-1"])
        self.assertEqual(self._run(_NoRegions()), ["us-east-1"])

    def test_named_regions_ignore_the_saved_list(self):
        audit.record_active_regions(self.paths["active_regions"], "123456789012",
                                    ["eu-west-1"], replace=True, observed_at=audit.now())
        self.assertIsNone(self._run(_Args()))


class StoppedScanTests(unittest.TestCase):
    """should_stop= ends a scan before its next AWS step, with nothing saved."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = Settings(output_dir=self.tmp.name)
        self.paths = audit.output_paths(self.settings)
        session = _session_with_one_instance_and_one_bucket()
        for p in (
            mock.patch.object(run_module, "authenticated_session",
                              return_value=(session, {"Account": "123456789012",
                                                      "Arn": "arn:aws:iam::123456789012:user/tester"})),
            mock.patch.object(run_module, "get_regions", return_value=["us-east-1"]),
        ):
            p.start()
            self.addCleanup(p.stop)
        quiet = audit.console.set_quiet(True)
        self.addCleanup(audit.console.set_quiet, quiet)

    def test_a_stop_leaves_no_snapshot_no_report_and_no_group_file(self):
        events, asked = [], []

        def stop_after_three():
            asked.append(1)
            return len(asked) > 3

        with self.assertRaises(audit.ScanCancelled):
            run_module.run(_Args(), settings=self.settings,
                           progress=events.append, should_stop=stop_after_three)

        for kind in ("snapshot", "csv", "json", "html", "groups"):
            self.assertFalse(os.path.exists(self.paths[kind]), kind)
        # Three collectors ran and the fourth never started.
        finished = [e for e in events if e.get("phase") == "collect" and "count" in e]
        self.assertEqual(len(finished), 3)

    def test_a_stop_never_asked_for_changes_nothing(self):
        snapshot = run_module.run(_Args(), settings=self.settings,
                                  should_stop=lambda: False)
        self.assertTrue(snapshot.rows)
        self.assertTrue(os.path.exists(self.paths["snapshot"]))

    def test_a_stop_before_the_bill_skips_the_chargeable_request(self):
        """The last check sits in front of Cost Explorer, which costs money."""
        steps = collect_module.total_collection_steps(["us-east-1"])
        asked = []

        def stop_once_every_collector_ran():
            asked.append(1)
            return len(asked) > steps

        class _WithCost(_Args):
            no_cost = False

        with mock.patch.object(collect_module, "collect_billing") as billing:
            with self.assertRaises(audit.ScanCancelled):
                run_module.run(_WithCost(), settings=self.settings,
                               should_stop=stop_once_every_collector_ran)
        billing.assert_not_called()


if __name__ == "__main__":
    unittest.main()
