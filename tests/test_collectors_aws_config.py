"""AWS Config collectors: a recorder's flag follows its recording state and scope; a
rule is listed with who created it. Not set up means no row; a denied read means ERROR."""

import unittest

from aws_resource_audit import coverage as cov

from .collectors_base import ACCOUNT, REGION, only
from .fakes import FakeClient, FakeSession, audit, client_error, recent
from .test_collectors_security_services import first_page_then_denied

_ARN = f"arn:aws:config:{REGION}:{ACCOUNT}:configuration-recorder/default/abc"


def _recorder(group=None):
    return {"name": "default", "arn": _ARN, "recordingScope": "PAID",
            "recordingGroup": group if group is not None else {"allSupported": True},
            "recordingMode": {"recordingFrequency": "CONTINUOUS"}}


def _session(recorders, recording=True, status=None, channels=None):
    status_resp = status if status is not None else {"ConfigurationRecordersStatus": [
        {"name": "default", "recording": recording, "lastStartTime": recent(30)}]}
    return FakeSession({"config": {
        "describe_configuration_recorders": {"ConfigurationRecorders": recorders},
        "describe_configuration_recorder_status": status_resp,
        "describe_delivery_channels": channels if channels is not None else {
            "DeliveryChannels": [{"name": "default", "s3BucketName": "config-history"}]},
    }})


class ConfigRecorderTests(unittest.TestCase):

    def test_a_recording_recorder_is_active_and_flagged_for_review(self):
        row = only(audit.collect_config_recorders(_session([_recorder()]), REGION))
        self.assertEqual(row["service"], "ConfigRecorder")
        self.assertEqual(row["resource_id"], "default")
        self.assertEqual(row["arn"], _ARN)
        self.assertEqual(row["flag"], "ACTIVE (recording - bills per configuration item)")
        self.assertEqual(row["_cost_hint"], "bills_while_enabled")
        self.assertEqual(row["description"],
                         "all resource types, CONTINUOUS, delivers to s3://config-history")
        self.assertIn("Recording last started", row["notes"])

    def test_it_asks_for_the_customer_managed_recorder(self):
        """With no name or ARN, AWS returns the customer-managed recorder: the one that bills."""
        session = _session([_recorder()])
        audit.collect_config_recorders(session, REGION)
        self.assertEqual(session.get("config").calls[0], ("describe_configuration_recorders", {}))

    def test_a_stopped_recorder_is_unused_and_not_billing(self):
        row = only(audit.collect_config_recorders(_session([_recorder()], recording=False), REGION))
        self.assertEqual(row["flag"], "STALE (not recording - not billing)")
        self.assertIsNone(row["_cost_hint"])

    def test_scope_names_what_is_recorded(self):
        cases = [
            ({"recordingStrategy": {"useOnly": "EXCLUSION_BY_RESOURCE_TYPES"},
              "exclusionByResourceTypes": {"resourceTypes": ["AWS::EC2::Volume", "AWS::S3::Bucket"]}},
             "all but 2 resource type(s)"),
            ({"allSupported": False, "resourceTypes": ["AWS::IAM::Role"]}, "1 resource type(s)"),
            ({"recordingStrategy": {"useOnly": "ALL_SUPPORTED_RESOURCE_TYPES"}}, "all resource types"),
        ]
        for group, expected in cases:
            with self.subTest(expected=expected):
                row = only(audit.collect_config_recorders(_session([_recorder(group)]), REGION))
                self.assertTrue(row["description"].startswith(expected + ","), row["description"])

    def test_an_unreadable_status_is_unknown_and_logged(self):
        session = _session([_recorder()], status=client_error(operation="DescribeConfigurationRecorderStatus"))
        with self.assertLogs("aws_resource_audit.collect.resources.aws_config", "WARNING") as logs:
            row = only(audit.collect_config_recorders(session, REGION))
        self.assertEqual(row["flag"], "UNKNOWN")
        self.assertIn("Recording status could not be read", row["notes"])
        self.assertIn("recording status unreadable", logs.output[0])
        self.assertIn("UNKNOWN", audit.compute_risk(row["flag"], False, row["_cost_hint"]))

    def test_a_status_list_without_this_recorder_is_unknown(self):
        session = _session([_recorder()], status={"ConfigurationRecordersStatus": [
            {"name": "other", "recording": True}]})
        self.assertEqual(only(audit.collect_config_recorders(session, REGION))["flag"], "UNKNOWN")

    def test_an_unreadable_delivery_channel_keeps_the_row_and_logs(self):
        session = _session([_recorder()], channels=client_error(operation="DescribeDeliveryChannels"))
        with self.assertLogs("aws_resource_audit.collect.resources.aws_config", "WARNING"):
            row = only(audit.collect_config_recorders(session, REGION))
        self.assertNotIn("delivers to", row["description"])
        self.assertTrue(row["flag"].startswith("ACTIVE"))

    def test_no_recorder_means_no_row(self):
        session = FakeSession({"config": {"describe_configuration_recorders": {"ConfigurationRecorders": []}}})
        self.assertEqual(audit.collect_config_recorders(session, REGION), [])
        self.assertEqual(session.get("config").operations_called(), ["describe_configuration_recorders"],
                         "no recorder: there is no status or channel worth a call")

    def test_a_denied_listing_is_an_error_row_and_a_denied_ledger_entry(self):
        session = FakeSession({"config": {"describe_configuration_recorders": client_error(
            "AccessDeniedException", operation="DescribeConfigurationRecorders")}})
        with cov.ledger_of() as ledger:
            row = only(audit.collect_config_recorders(session, REGION))
        self.assertEqual(row["flag"], "ERROR")
        self.assertEqual([e.status for e in ledger.entries], [cov.DENIED])


class ConfigRuleTests(unittest.TestCase):

    def _rules(self, *rules):
        return FakeSession({"config": {"describe_config_rules": {"ConfigRules": list(rules)}}})

    def test_an_active_rule_bills_per_evaluation_and_is_flagged_for_review(self):
        row = only(audit.collect_config_rules(self._rules({
            "ConfigRuleName": "s3-versioning", "ConfigRuleState": "ACTIVE",
            "ConfigRuleArn": f"arn:aws:config:{REGION}:{ACCOUNT}:config-rule/config-rule-1",
            "Description": "Checks bucket versioning",
            "Source": {"Owner": "AWS", "SourceIdentifier": "S3_BUCKET_VERSIONING_ENABLED"},
        }), REGION))
        self.assertEqual(row["service"], "ConfigRule")
        self.assertEqual(row["resource_id"], "s3-versioning")
        self.assertEqual(row["flag"], "ACTIVE (bills per evaluation while the recorder runs)")
        self.assertEqual(row["_cost_hint"], "bills_while_enabled")
        self.assertEqual(row["description"],
                         "Checks bucket versioning, AWS, S3_BUCKET_VERSIONING_ENABLED")
        self.assertNotIn("Created by", row["notes"])

    def test_a_rule_another_service_created_says_where_to_remove_it(self):
        row = only(audit.collect_config_rules(self._rules({
            "ConfigRuleName": "securityhub-s3-1", "ConfigRuleState": "ACTIVE",
            "CreatedBy": "securityhub.amazonaws.com"}), REGION))
        self.assertIn("Created by securityhub.amazonaws.com: remove it there", row["notes"])

    def test_a_rule_being_deleted_is_unused(self):
        for state in ("DELETING", "DELETING_RESULTS"):
            with self.subTest(state=state):
                row = only(audit.collect_config_rules(self._rules(
                    {"ConfigRuleName": "old", "ConfigRuleState": state}), REGION))
                self.assertEqual(row["flag"], "STALE (being deleted)")
                self.assertIsNone(row["_cost_hint"])

    def test_an_evaluating_rule_is_active(self):
        row = only(audit.collect_config_rules(self._rules(
            {"ConfigRuleName": "new", "ConfigRuleState": "EVALUATING"}), REGION))
        self.assertEqual(row["flag"], "ACTIVE (bills per evaluation while the recorder runs)")

    def test_a_listing_refused_part_way_keeps_the_rules_read(self):
        client = FakeClient("config", {"describe_config_rules": first_page_then_denied(
            {"ConfigRules": [{"ConfigRuleName": "kept", "ConfigRuleState": "ACTIVE"}]})},
            not_pageable={"describe_config_rules"})
        with cov.ledger_of() as ledger:
            row = only(audit.collect_config_rules(FakeSession({"config": client}), REGION))
        self.assertEqual(row["resource_id"], "kept")
        self.assertEqual([e.status for e in ledger.entries], [cov.PARTIAL])

    def test_a_denied_rule_listing_is_an_error_row(self):
        session = FakeSession({"config": {"describe_config_rules": client_error(
            "AccessDeniedException", operation="DescribeConfigRules")}})
        with cov.ledger_of() as ledger:
            row = only(audit.collect_config_rules(session, REGION))
        self.assertEqual((row["service"], row["flag"]), ("ConfigRule", "ERROR"))
        self.assertEqual([e.status for e in ledger.entries], [cov.DENIED])

    def test_no_rules_means_no_rows(self):
        self.assertEqual(audit.collect_config_rules(self._rules(), REGION), [])


if __name__ == "__main__":
    unittest.main()
