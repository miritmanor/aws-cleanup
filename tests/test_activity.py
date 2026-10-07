"""Activity evidence: used, measured idle and no telemetry are three answers, and only
the first may produce a last-used date (zero samples are not use; age is not idleness)."""

import unittest

from .fakes import FakeSession, audit, aws_ts, ancient, client_error, make_row, \
    metrics_at, metrics_all_zero, recent, NO_METRICS, METRICS_DENIED

from aws_resource_audit import activity as act
from aws_resource_audit.staleness import (
    USAGE_ACTIVE,
    USAGE_UNKNOWN,
    USAGE_UNUSED,
    usage_state,
)

REGION = "us-east-1"


def one_row(session, collector, **kwargs):
    rows = collector(session, REGION, **kwargs)
    return [r for r in rows if r["flag"] != "ERROR"][0]


class MetricEvidenceTests(unittest.TestCase):
    """cw_activity, on its own terms."""

    def _evidence(self, metric_response):
        cw = FakeSession({"cloudwatch": metric_response}).client("cloudwatch")
        return audit.cw_activity(cw, "AWS/Lambda", "Invocations",
                                 [{"Name": "FunctionName", "Value": "fn"}])

    def test_positive_datapoints_are_observed_activity(self):
        when = recent()
        evidence = self._evidence(metrics_at(when))

        self.assertEqual(evidence.telemetry, act.OBSERVED_ACTIVITY)
        self.assertEqual(evidence.last_activity, when)

    def test_zero_only_datapoints_are_observed_silence(self):
        """The headline case. Data exists and it says nothing happened."""
        evidence = self._evidence(metrics_all_zero(recent()))

        self.assertEqual(evidence.telemetry, act.OBSERVED_ZERO)
        self.assertIsNone(evidence.last_activity,
                          "a zero sample is not a use, whatever its timestamp")
        self.assertIsNotNone(evidence.latest_sample,
                             "but the fact that telemetry exists is kept")

    def test_no_datapoints_is_its_own_answer(self):
        evidence = self._evidence(NO_METRICS)
        self.assertEqual(evidence.telemetry, act.NO_DATAPOINTS)
        self.assertIsNone(evidence.last_activity)

    def test_a_denied_metric_is_not_an_idle_resource(self):
        evidence = self._evidence(METRICS_DENIED)
        self.assertEqual(evidence.telemetry, act.LOOKUP_FAILED)
        self.assertTrue(evidence.error)

    def test_the_three_silences_stay_distinguishable_after_serialization(self):
        """A report renders from the snapshot, so the distinction has to
        survive the round trip or it may as well not exist."""
        seen = set()
        for response in (NO_METRICS, metrics_all_zero(recent()), METRICS_DENIED):
            row = audit.new_row("LambdaFunction", REGION, "fn", "fn", "", "", None,
                                True, "UNKNOWN", "", activity=self._evidence(response))
            restored = audit.restore_row(audit.snapshot_row(row))
            seen.add(restored["activity"]["telemetry"])
        self.assertEqual(len(seen), 3, seen)


class FabricatedActivityTests(unittest.TestCase):
    """No collector may report a use that was never observed."""

    def _lambda(self, metric_response, last_modified=None):
        session = FakeSession({
            "lambda": {
                "list_event_source_mappings": {"EventSourceMappings": []},
                "list_functions": {"Functions": [{
                    "FunctionName": "orders-fn", "Runtime": "python3.12",
                    "LastModified": aws_ts(last_modified or recent()),
                    "FunctionArn": f"arn:aws:lambda:{REGION}:111122223333:function:orders-fn",
                }]},
                "list_aliases": {"Aliases": []},
                "list_tags": {"Tags": {}},
            },
            "cloudwatch": metric_response,
        })
        return one_row(session, audit.collect_lambda_functions)

    def test_recent_zeros_do_not_produce_active_or_a_last_used_date(self):
        row = self._lambda(metrics_all_zero(recent()))

        self.assertFalse(row["flag"].startswith("ACTIVE"), row["flag"])
        self.assertIn(row["last_used"], (None, ""),
                      "a zero-valued sample must not become a last-used date")

    def test_recent_invocations_do_produce_activity(self):
        when = recent()
        row = self._lambda(metrics_at(when))

        self.assertTrue(row["flag"].startswith("ACTIVE"), row["flag"])
        self.assertEqual(row["last_used"], when)

    def test_a_recently_modified_uninvoked_lambda_has_no_runtime_activity(self):
        """LastModified is a configuration change. Editing a function does
        not mean anyone called it."""
        row = self._lambda(NO_METRICS, last_modified=recent())

        self.assertIn(row["last_used"], (None, ""))
        self.assertFalse(row["flag"].startswith("ACTIVE"), row["flag"])
        self.assertNotEqual(row["activity"]["telemetry"], act.OBSERVED_ACTIVITY)
        self.assertIn("EDITED", row["notes"],
                      "the notes must say LastModified is a configuration change")


class TelemetryAbsenceTests(unittest.TestCase):
    """Age is not inactivity."""

    def test_an_old_s3_bucket_without_telemetry_is_unknown_not_stale(self):
        session = FakeSession({"s3": {
            "list_buckets": {"Buckets": [{"Name": "archive", "BucketRegion": "us-east-1", "CreationDate": ancient()}]},
            "get_bucket_notification_configuration": {},
            "get_bucket_tagging": client_error(code="NoSuchTagSet"),
        }})
        row = [r for r in audit.collect_s3_buckets(session) if r["flag"] != "ERROR"][0]

        self.assertIn("UNKNOWN", row["flag"], row["flag"])
        self.assertNotIn("STALE", row["flag"],
                         "S3 exposes no access telemetry; old is not idle")

    def test_the_creation_date_is_still_reported_as_its_own_evidence(self):
        session = FakeSession({"s3": {
            "list_buckets": {"Buckets": [{"Name": "archive", "BucketRegion": "us-east-1", "CreationDate": ancient()}]},
            "get_bucket_notification_configuration": {},
            "get_bucket_tagging": client_error(code="NoSuchTagSet"),
        }})
        row = [r for r in audit.collect_s3_buckets(session) if r["flag"] != "ERROR"][0]

        self.assertTrue(row["created"], "age is kept, it is just not used as use")


class DynamoDbWriteTests(unittest.TestCase):
    """Reads are half the story."""

    def _table(self, metric_fn):
        session = FakeSession({
            "dynamodb": {
                "list_tables": {"TableNames": ["events"]},
                "describe_table": {"Table": {
                    "TableName": "events", "CreationDateTime": ancient(),
                    "TableArn": f"arn:aws:dynamodb:{REGION}:111122223333:table/events",
                }},
                "list_tags_of_resource": {"Tags": []},
            },
            "cloudwatch": {"get_metric_statistics": metric_fn},
        })
        return one_row(session, audit.collect_dynamodb_tables)

    def test_a_write_only_table_is_active(self):
        """An append-only table - logs, events, audit records - reads zero
        forever and is in constant use."""
        when = recent()

        def by_metric(**kwargs):
            if kwargs.get("MetricName") == "ConsumedWriteCapacityUnits":
                return {"Datapoints": [{"Timestamp": when, "Sum": 128.0}]}
            return {"Datapoints": [{"Timestamp": when, "Sum": 0.0}]}

        row = self._table(by_metric)

        self.assertTrue(row["flag"].startswith("ACTIVE"), row["flag"])
        self.assertEqual(row["last_used"], when)

    def test_a_table_idle_on_both_metrics_is_not_active(self):
        def by_metric(**kwargs):
            return {"Datapoints": [{"Timestamp": recent(), "Sum": 0.0}]}

        row = self._table(by_metric)
        self.assertFalse(row["flag"].startswith("ACTIVE"), row["flag"])


class QuietIsNotUnusedTests(unittest.TestCase):
    """Things that are supposed to be quiet must not read as deletable."""

    def test_a_volume_attached_to_a_stopped_instance_is_not_low_risk(self):
        """Billed while nothing reads it, but the instance may be stopped on purpose."""
        session = FakeSession({
            "ec2": {
                "describe_volumes": {"Volumes": [{
                    "VolumeId": "vol-1", "Size": 100, "State": "in-use",
                    "CreateTime": ancient(),
                    "Attachments": [{"InstanceId": "i-1", "Device": "/dev/sda1"}],
                }]},
                "describe_instances": {"Reservations": [{"Instances": [{
                    "InstanceId": "i-1", "State": {"Name": "stopped"}, "Tags": [],
                }]}]},
            },
            "cloudwatch": NO_METRICS,
        })
        row = one_row(session, audit.collect_ebs_volumes)
        audit.resolve_edges([row])

        self.assertFalse(row["risk_if_removed"].startswith("LOW"),
                         row["risk_if_removed"])
        self.assertIn("billed", row["risk_if_removed"].lower())

    def test_a_snapshot_backing_nothing_is_still_not_judged_on_quiet_alone(self):
        """Snapshots emit no telemetry at all. Whatever the report says about
        one, it cannot be 'idle, therefore safe'."""
        session = FakeSession({"ec2": {"describe_snapshots": {"Snapshots": [{
            "SnapshotId": "snap-1", "VolumeSize": 8, "StartTime": ancient(),
            "Description": "nightly backup",
        }]}}})
        usage = audit.new_ec2_usage_registry()
        row = one_row(session, audit.collect_ebs_snapshots, usage=usage)

        self.assertNotIn("ACTIVE", row["flag"])
        self.assertEqual(row["activity"]["telemetry"], act.UNAVAILABLE,
                         "no metric exists for a snapshot, and that is the fact")


class UsageStateTests(unittest.TestCase):
    """usage_state is the flag's verdict as a countable value, derived from the flag so
    collector-specific judgements survive."""

    def test_every_flag_a_collector_can_produce_starts_with_a_known_word(self):
        """What makes the derivation total rather than a guess. A new flag
        wording that broke this would silently classify rows as unknown."""
        flags = [
            audit.flag_stale(900, None, False),
            audit.flag_stale(900, None, True),
            audit.flag_stale(1, None, False),
            audit.flag_stale(1, None, True),
            audit.flag_stale(None, None, False),
            audit.flag_from_activity(None, None),
            audit.flag_from_activity(act.unavailable("none"), None),
            audit.flag_from_activity(
                act.ActivityEvidence(telemetry=act.LOOKUP_FAILED,
                                     metric="Invocations", error="denied"), None),
            audit.flag_from_activity(
                act.ActivityEvidence(telemetry=act.NO_DATAPOINTS,
                                     metric="Invocations", window_days=455), None),
            audit.flag_from_activity(
                act.ActivityEvidence(telemetry=act.OBSERVED_ZERO,
                                     metric="Invocations", window_days=900), None),
            audit.flag_from_activity(
                act.ActivityEvidence(telemetry=act.OBSERVED_ZERO,
                                     metric="Invocations", window_days=30), None),
            audit.flag_from_activity(
                act.from_timestamp(recent(), source=act.SOURCE_CLOUDWATCH,
                                   metric="Invocations"), None),
        ]
        for flag in flags:
            self.assertRegex(flag, r"^(STALE|ACTIVE|UNKNOWN)\b", flag)

    def test_a_stale_flag_reads_as_potentially_unused(self):
        self.assertEqual(usage_state("STALE"), USAGE_UNUSED)
        self.assertEqual(usage_state("STALE (INFERRED)"), USAGE_UNUSED)
        self.assertEqual(usage_state("STALE (no activity in 455d)"), USAGE_UNUSED)

    def test_an_active_flag_reads_as_active(self):
        self.assertEqual(usage_state("ACTIVE"), USAGE_ACTIVE)
        self.assertEqual(usage_state("ACTIVE (INFERRED)"), USAGE_ACTIVE)

    def test_every_shape_of_unknown_reads_as_unknown(self):
        for flag in ("UNKNOWN",
                     "UNKNOWN (activity lookup failed - see notes)",
                     "UNKNOWN (metric published no datapoints; it may not be enabled)",
                     "UNKNOWN (no usage telemetry exists for this resource type)"):
            self.assertEqual(usage_state(flag), USAGE_UNKNOWN, flag)

    def test_a_missing_or_malformed_flag_reads_as_unknown_rather_than_raising(self):
        """new_row() calls this on whatever a collector passed. A row with a
        broken flag is a row to classify, not a scan to abort."""
        for flag in ("", None, "  ", "WEIRD", 7, True, [], "stale"):
            self.assertEqual(usage_state(flag), USAGE_UNKNOWN, repr(flag))

    def test_a_short_measured_window_is_unknown_not_unused(self):
        """"No activity in 30 days" over a 730-day staleness threshold has not
        measured enough to conclude anything, and must not be counted idle."""
        evidence = act.ActivityEvidence(telemetry=act.OBSERVED_ZERO,
                                        metric="Invocations", window_days=30)
        flag = audit.flag_from_activity(evidence, None)
        self.assertEqual(usage_state(flag), USAGE_UNKNOWN)

    def test_a_measured_empty_window_past_the_threshold_is_unused(self):
        evidence = act.ActivityEvidence(telemetry=act.OBSERVED_ZERO,
                                        metric="Invocations", window_days=900)
        flag = audit.flag_from_activity(evidence, None)
        self.assertEqual(usage_state(flag), USAGE_UNUSED)

    def test_a_row_carries_the_state_matching_its_own_flag(self):
        """new_row() derives one from the other, at the single place every row
        is built, so the column and the count cannot disagree."""
        for flag, expected in (("STALE", USAGE_UNUSED),
                               ("ACTIVE", USAGE_ACTIVE),
                               ("UNKNOWN (no telemetry)", USAGE_UNKNOWN)):
            row = make_row("LambdaFunction", "fn", flag=flag)
            self.assertEqual(row["usage_state"], expected)
            self.assertEqual(row["usage_state"], usage_state(row["flag"]))

    def test_an_unattached_volume_keeps_its_collector_s_verdict(self):
        """A detached volume's collector verdict survives into the count."""
        row = make_row("EBSVolume", "vol-1", flag=audit.flag_stale(900, None, True))
        self.assertEqual(row["usage_state"], USAGE_UNUSED)


if __name__ == "__main__":
    unittest.main()
