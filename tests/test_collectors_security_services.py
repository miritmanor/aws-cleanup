"""GuardDuty, Security Hub, Inspector, Macie and Detective: enabled reads as ACTIVE with a
review hint, suspended as STALE, never enabled as no row (and no denial in the ledger)."""

import unittest

from aws_resource_audit import coverage as cov
from aws_resource_audit.scope import scan_account

from .collectors_base import ACCOUNT, REGION, only
from .fakes import FakeClient, FakeSession, audit, client_error, recent

ENABLED = "ACTIVE (enabled - bills while on)"
_HUB_ARN = f"arn:aws:securityhub:{REGION}:{ACCOUNT}:hub/default"


def first_page_then_denied(page):
    """A listing that returns one page with a NextToken, then is refused."""
    def call(**kwargs):
        return client_error(operation="List") if kwargs.get("NextToken") else dict(page, NextToken="t")
    return call


class GuardDutyTests(unittest.TestCase):

    def _session(self, detail):
        return FakeSession({"guardduty": {"list_detectors": {"DetectorIds": ["det1"]},
                                          "get_detector": detail}})

    def test_an_enabled_detector_lists_its_features_and_tags(self):
        row = only(audit.collect_guardduty_detectors(self._session({
            "Status": "ENABLED", "CreatedAt": "2024-03-01T10:00:00.000Z",
            "Tags": {"Project": "baseline"},
            "Features": [{"Name": "CLOUD_TRAIL", "Status": "ENABLED"},
                         {"Name": "S3_DATA_EVENTS", "Status": "DISABLED"}]}), REGION))
        self.assertEqual((row["service"], row["resource_id"]), ("GuardDutyDetector", "det1"))
        self.assertEqual(row["flag"], ENABLED)
        self.assertEqual(row["_cost_hint"], "bills_while_enabled")
        self.assertEqual(row["description"], "1 feature(s) on, CLOUD_TRAIL")
        self.assertEqual(row["tags"], {"Project": "baseline"})
        self.assertEqual(row["created"].year, 2024)

    def test_a_suspended_detector_is_unused(self):
        row = only(audit.collect_guardduty_detectors(self._session({"Status": "DISABLED"}), REGION))
        self.assertEqual(row["flag"], "STALE (suspended - not billing)")
        self.assertIsNone(row["_cost_hint"])

    def test_unreadable_details_keep_the_detector_as_unknown_and_log(self):
        session = self._session(client_error(operation="GetDetector"))
        with self.assertLogs("aws_resource_audit.collect.resources.security_services", "WARNING") as logs:
            row = only(audit.collect_guardduty_detectors(session, REGION))
        self.assertEqual(row["flag"], "UNKNOWN")
        self.assertIn("Detector details could not be read", row["notes"])
        self.assertIn("det1", logs.output[0])

    def test_a_listing_refused_part_way_keeps_the_detectors_read(self):
        client = FakeClient("guardduty", {
            "list_detectors": first_page_then_denied({"DetectorIds": ["det1"]}),
            "get_detector": {"Status": "ENABLED"}}, not_pageable={"list_detectors"})
        with cov.ledger_of() as ledger:
            row = only(audit.collect_guardduty_detectors(FakeSession({"guardduty": client}), REGION))
        self.assertEqual((row["resource_id"], row["flag"]), ("det1", ENABLED))
        self.assertIn(cov.PARTIAL, [e.status for e in ledger.entries])

    def test_an_unparsable_creation_date_still_gives_a_row(self):
        row = only(audit.collect_guardduty_detectors(
            self._session({"Status": "ENABLED", "CreatedAt": "garbage"}), REGION))
        self.assertIsNone(row["created"])
        self.assertEqual(row["flag"], ENABLED)

    def test_no_detector_means_no_row(self):
        session = FakeSession({"guardduty": {"list_detectors": {"DetectorIds": []}}})
        self.assertEqual(audit.collect_guardduty_detectors(session, REGION), [])


class SecurityHubTests(unittest.TestCase):

    def _session(self, hub, standards=None):
        return FakeSession({"securityhub": {
            "describe_hub": hub,
            "get_enabled_standards": standards if standards is not None else {"StandardsSubscriptions": [
                {"StandardsArn": f"arn:aws:securityhub:{REGION}::standards/aws-foundational-security-best-practices/v/1.0.0"},
                {"StandardsArn": "arn:aws:securityhub:::ruleset/cis-aws-foundations-benchmark/v/1.2.0"}]},
        }})

    def test_a_subscribed_hub_names_its_standards(self):
        row = only(audit.collect_security_hub(self._session(
            {"HubArn": _HUB_ARN, "SubscribedAt": "2024-01-02T03:04:05.000Z"}), REGION))
        self.assertEqual((row["service"], row["resource_id"], row["arn"]),
                         ("SecurityHubHub", _HUB_ARN, _HUB_ARN))
        self.assertEqual(row["flag"], ENABLED)
        self.assertEqual(row["created"].year, 2024)
        self.assertEqual(row["description"], "2 standard(s), aws-foundational-security-best-practices, "
                                             "cis-aws-foundations-benchmark")

    def test_an_access_error_that_is_not_about_subscribing_is_a_denial(self):
        session = self._session(client_error("InvalidAccessException", "User is not authorized", "DescribeHub"))
        with cov.ledger_of() as ledger:
            row = only(audit.collect_security_hub(session, REGION))
        self.assertEqual(row["flag"], "ERROR")
        self.assertEqual([e.status for e in ledger.entries], [cov.DENIED])

    def test_a_standard_arn_with_no_path_is_named_by_the_whole_arn(self):
        row = only(audit.collect_security_hub(self._session(
            {"HubArn": _HUB_ARN}, {"StandardsSubscriptions": [{"StandardsArn": "odd-arn"}]}), REGION))
        self.assertEqual(row["description"], "1 standard(s), odd-arn")

    def test_not_subscribed_is_no_row_and_no_denial(self):
        for code, message in (("InvalidAccessException", "Account 111122223333 is not subscribed to AWS Security Hub"),
                              ("ResourceNotFoundException", "Hub not found")):
            with self.subTest(code=code), cov.ledger_of() as ledger:
                rows = audit.collect_security_hub(self._session(client_error(code, message, "DescribeHub")), REGION)
                self.assertEqual(rows, [])
                self.assertEqual([e.status for e in ledger.entries], [cov.COMPLETE],
                                 "an off service must not ask for an IAM grant")

    def test_a_denied_hub_lookup_is_an_error_row(self):
        session = self._session(client_error("AccessDeniedException", operation="DescribeHub"))
        with cov.ledger_of() as ledger:
            row = only(audit.collect_security_hub(session, REGION))
        self.assertEqual(row["flag"], "ERROR")
        self.assertEqual([e.status for e in ledger.entries], [cov.DENIED])

    def test_unreadable_standards_keep_the_hub_and_log(self):
        session = self._session({"HubArn": _HUB_ARN}, client_error(operation="GetEnabledStandards"))
        with self.assertLogs("aws_resource_audit.collect.resources.security_services", "WARNING"):
            row = only(audit.collect_security_hub(session, REGION))
        self.assertEqual(row["flag"], ENABLED)
        self.assertIn("Enabled standards could not be read", row["notes"])

    def test_standards_refused_part_way_keep_the_ones_read(self):
        std = "arn:aws:securityhub:us-east-1::standards/pci-dss/v/3.2.1"
        client = FakeClient("securityhub", {
            "describe_hub": {"HubArn": _HUB_ARN},
            "get_enabled_standards": first_page_then_denied({"StandardsSubscriptions": [{"StandardsArn": std}]}),
        }, not_pageable={"get_enabled_standards"})
        with self.assertLogs("aws_resource_audit.collect.resources.security_services", "WARNING"):
            row = only(audit.collect_security_hub(FakeSession({"securityhub": client}), REGION))
        self.assertEqual(row["description"], "1 standard(s), pci-dss")
        self.assertIn("Enabled standards could not be read", row["notes"])


class InspectorTests(unittest.TestCase):

    def _session(self, status, **scans):
        return FakeSession({"inspector2": {"batch_get_account_status": {"accounts": [{
            "accountId": "444455556666", "state": {"status": status},
            "resourceState": {k: {"status": v} for k, v in scans.items()}}]}}})

    def test_enabled_scans_are_listed_and_the_account_comes_from_the_response(self):
        row = only(audit.collect_inspector(self._session("ENABLED", ec2="ENABLED", ecr="ENABLED",
                                                         **{"lambda": "DISABLED"}), REGION))
        self.assertEqual((row["service"], row["resource_id"]), ("InspectorAccount", "inspector"))
        self.assertEqual(row["flag"], ENABLED)
        self.assertEqual(row["description"], "ENABLED, ec2, ecr")
        self.assertEqual(row["account"], "444455556666")

    def test_never_enabled_is_no_row(self):
        self.assertEqual(audit.collect_inspector(self._session("DISABLED", ec2="DISABLED"), REGION), [])

    def test_each_account_state(self):
        stale = "STALE (suspended - not billing)"
        for status, scan, flag in (("ENABLING", "ENABLING", ENABLED), ("SUSPENDED", "SUSPENDED", stale),
                                   ("SUSPENDING", "SUSPENDED", stale), ("DISABLING", "DISABLING", stale)):
            with self.subTest(status=status):
                row = only(audit.collect_inspector(self._session(status, ec2=scan), REGION))
                self.assertEqual(row["flag"], flag)
                self.assertEqual(row["_cost_hint"], "bills_while_enabled" if flag == ENABLED else None)

    def test_an_enabled_scan_type_outranks_the_account_status(self):
        """A scan type that is on bills, whatever the account-level status says."""
        for status in ("DISABLED", "SUSPENDED"):
            with self.subTest(status=status):
                row = only(audit.collect_inspector(self._session(status, ec2="ENABLED"), REGION))
                self.assertEqual(row["flag"], ENABLED)

    def test_a_missing_account_id_falls_back_to_the_scan_account(self):
        session = FakeSession({"inspector2": {"batch_get_account_status": {"accounts": [{
            "state": {"status": "ENABLED"}, "resourceState": {}}]}}})
        row = only(audit.collect_inspector(session, REGION))
        self.assertEqual(row["account"], scan_account())


class MacieTests(unittest.TestCase):

    def _session(self, resp):
        return FakeSession({"macie2": {"get_macie_session": resp}})

    def test_enabled_and_paused(self):
        for status, flag in (("ENABLED", ENABLED), ("PAUSED", "STALE (paused - not billing)")):
            with self.subTest(status=status):
                row = only(audit.collect_macie(self._session(
                    {"status": status, "createdAt": recent(100)}), REGION))
                self.assertEqual((row["service"], row["resource_id"], row["flag"]),
                                 ("MacieAccount", "macie", flag))
                self.assertIsNotNone(row["created"])

    def test_not_enabled_is_no_row_and_no_denial(self):
        for code, message in (("AccessDeniedException", "Macie is not enabled."),
                              ("ResourceNotFoundException", "No Macie session")):
            with self.subTest(code=code), cov.ledger_of() as ledger:
                session = self._session(client_error(code, message, "GetMacieSession"))
                self.assertEqual(audit.collect_macie(session, REGION), [])
                self.assertEqual([e.status for e in ledger.entries], [cov.COMPLETE])

    def test_a_real_denial_with_the_same_code_is_an_error_row(self):
        """Macie uses AccessDeniedException for both; only the message tells them apart."""
        session = self._session(client_error(
            "AccessDeniedException", "User: x is not authorized to perform: macie2:GetMacieSession",
            "GetMacieSession"))
        with cov.ledger_of() as ledger:
            row = only(audit.collect_macie(session, REGION))
        self.assertEqual(row["flag"], "ERROR")
        self.assertEqual([e.status for e in ledger.entries], [cov.DENIED])


class DetectiveTests(unittest.TestCase):

    def test_each_graph_is_a_row_named_by_its_id(self):
        arn = f"arn:aws:detective:{REGION}:{ACCOUNT}:graph:0abc123"
        client = FakeClient("detective", {"list_graphs": {"GraphList": [
            {"Arn": arn, "CreatedTime": recent(60)}]}}, not_pageable={"list_graphs"})
        row = only(audit.collect_detective_graphs(FakeSession({"detective": client}), REGION))
        self.assertEqual((row["service"], row["resource_id"], row["name"]), ("DetectiveGraph", arn, "0abc123"))
        self.assertEqual(row["flag"], ENABLED)

    def test_a_listing_refused_part_way_keeps_the_graphs_read(self):
        arn = f"arn:aws:detective:{REGION}:{ACCOUNT}:graph:0abc123"
        client = FakeClient("detective", {"list_graphs": first_page_then_denied({"GraphList": [{"Arn": arn}]})},
                            not_pageable={"list_graphs"})
        row = only(audit.collect_detective_graphs(FakeSession({"detective": client}), REGION))
        self.assertEqual(row["resource_id"], arn)

    def test_no_graph_means_no_row(self):
        client = FakeClient("detective", {"list_graphs": {"GraphList": []}}, not_pageable={"list_graphs"})
        self.assertEqual(audit.collect_detective_graphs(FakeSession({"detective": client}), REGION), [])


if __name__ == "__main__":
    unittest.main()
