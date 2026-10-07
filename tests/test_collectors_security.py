"""Collector tests for identity and secrets: IAM, Cognito, KMS, Secrets Manager."""

import unittest

from .fakes import FakeSession, ancient, client_error, audit, recent
from .collectors_base import ACCOUNT, REGION, by_service, only


class KeyCollectorTests(unittest.TestCase):

    def test_only_customer_managed_kms_keys_are_listed(self):
        session = FakeSession({"kms": {
            "list_keys": {"Keys": [{"KeyId": "k-mine"}, {"KeyId": "k-aws"}]},
            "list_aliases": {"Aliases": [{"AliasName": "alias/payroll", "TargetKeyId": "k-mine"}]},
            "describe_key": lambda KeyId: {
                "k-mine": {"KeyMetadata": {"KeyId": "k-mine", "KeyManager": "CUSTOMER",
                                           "KeyState": "Disabled", "CreationDate": ancient()}},
                "k-aws": {"KeyMetadata": {"KeyId": "k-aws", "KeyManager": "AWS",
                                          "KeyState": "Enabled"}},
            }[KeyId],
            "list_resource_tags": {"Tags": []},
        }})
        row = only(audit.collect_kms_keys(session, REGION))
        self.assertEqual((row["resource_id"], row["name"]), ("k-mine", "alias/payroll"))
        self.assertIn("disabled", row["flag"])

    def test_a_secret_never_read_is_stale_and_one_read_recently_is_not(self):
        session = FakeSession({"secretsmanager": {"list_secrets": {"SecretList": [
            {"Name": "old-token", "CreatedDate": ancient()},
            {"Name": "db-password", "CreatedDate": ancient(), "LastAccessedDate": recent()},
        ]}}})
        flags = {r["resource_id"]: r["flag"] for r in audit.collect_secrets(session, REGION)}
        self.assertIn("STALE", flags["old-token"])
        self.assertIn("ACTIVE", flags["db-password"])


class CertificateCollectorTests(unittest.TestCase):
    @staticmethod
    def _cert(**cert):
        detail = dict({"DomainName": "shop.example", "Status": "ISSUED", "Type": "AMAZON_ISSUED",
                       "CreatedAt": ancient(), "NotAfter": recent(-200), "InUseBy": []}, **cert)
        return FakeSession({"acm": {
            "list_certificates": {"CertificateSummaryList": [
                {"CertificateArn": f"arn:aws:acm:{REGION}:{ACCOUNT}:certificate/abc-123"}]},
            "describe_certificate": {"Certificate": detail},
            "list_tags_for_certificate": {"Tags": []},
        }})

    def test_a_certificate_nothing_uses_is_stale(self):
        row = only(audit.collect_acm_certificates(self._cert(), REGION))
        self.assertEqual((row["service"], row["resource_id"], row["billing"]), ("ACMCertificate", "abc-123", "free"))
        self.assertIn("not in use", row["flag"])

    def test_an_expired_certificate_is_stale(self):
        row = only(audit.collect_acm_certificates(self._cert(Status="EXPIRED"), REGION))
        self.assertEqual(row["flag"], "STALE (expired)")

    def test_an_imported_certificate_about_to_expire_is_called_out(self):
        session = self._cert(Type="IMPORTED", NotAfter=recent(-10),
                             InUseBy=[f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:loadbalancer/app/web/1"])
        self.assertIn("will not renew", only(audit.collect_acm_certificates(session, REGION))["flag"])

    def test_every_key_type_is_asked_for(self):
        session = self._cert()
        audit.collect_acm_certificates(session, REGION)
        (_op, kwargs), = [c for c in session.get("acm").calls if c[0] == "list_certificates"]
        self.assertIn("EC_prime256v1", kwargs["Includes"]["keyTypes"])


class ParameterCollectorTests(unittest.TestCase):
    def test_only_advanced_parameters_are_asked_for_and_none_is_called_stale(self):
        session = FakeSession({"ssm": {
            "describe_parameters": {"Parameters": [{"Name": "/app/config", "Type": "SecureString",
                                                    "Version": 3, "LastModifiedDate": ancient()}]},
            "list_tags_for_resource": {"TagList": [{"Key": "Project", "Value": "web"}]},
        }})
        row = only(audit.collect_ssm_parameters(session, REGION))
        self.assertEqual((row["service"], row["billing"]), ("SSMParameter", "cost"))
        self.assertTrue(row["flag"].startswith("UNKNOWN"))
        self.assertEqual(row["tags"], {"Project": "web"})
        (_op, kwargs), = [c for c in session.get("ssm").calls if c[0] == "describe_parameters"]
        self.assertEqual(kwargs["ParameterFilters"], [{"Key": "Tier", "Values": ["Advanced"]}])

    def test_parameter_arns_name_their_rows(self):
        base = f"arn:aws:ssm:{REGION}:{ACCOUNT}:parameter"
        self.assertEqual(audit.probable_resource_id_from_arn(f"{base}/app/config"), ("/app/config", "SSMParameter"))
        self.assertEqual(audit.probable_resource_id_from_arn(f"{base}/flat"), ("flat", "SSMParameter"))


class CognitoCollectorTests(unittest.TestCase):

    def test_cognito_user_pool(self):
        session = FakeSession({"cognito-idp": {
            "list_user_pools": {"UserPools": [{
                "Id": "us-east-1_abc123", "Name": "orders-users", "Status": "ENABLED",
                "CreationDate": recent(300), "LastModifiedDate": recent(20),
            }]},
            "describe_user_pool": {"UserPool": {}},
        }})
        row = only(audit.collect_cognito_user_pools(session, REGION))
        self.assertEqual(row["service"], "CognitoUserPool")
        self.assertEqual(row["resource_id"], "us-east-1_abc123")
        self.assertIn("not actual sign-ins", row["notes"])

    def test_cognito_user_pool_detail_denied_is_reported_not_silent(self):
        """describe_user_pool failing must not look like "pool has no triggers"."""
        session = FakeSession({"cognito-idp": {
            "list_user_pools": {"UserPools": [{
                "Id": "us-east-1_abc123", "Name": "orders-users", "Status": "ENABLED",
                "CreationDate": recent(300), "LastModifiedDate": recent(20),
            }]},
            "describe_user_pool": client_error("AccessDeniedException", "denied"),
        }})
        row = only(audit.collect_cognito_user_pools(session, REGION))
        self.assertIn("Could not read pool detail", row["notes"])
        self.assertEqual(row["_edges"], [])


class IamCollectorTests(unittest.TestCase):

    @staticmethod
    def _session(**overrides):
        ops = {
            "list_users": {"Users": []}, "list_groups": {"Groups": []},
            "list_policies": {"Policies": []}, "list_roles": {"Roles": []},
            "list_role_policies": {"PolicyNames": []},
            "list_attached_role_policies": {"AttachedPolicies": []},
            # Default GetRole to "no data" so tests not about that fallback need not say so.
            "get_role": {"Role": {}},
        }
        ops.update(overrides)
        return FakeSession({"iam": ops})

    def test_iam_user(self):
        session = self._session(
            list_users={"Users": [{"UserName": "alice", "CreateDate": recent(500),
                                   "PasswordLastUsed": recent(3)}]},
            list_access_keys={"AccessKeyMetadata": []},
        )
        row = only(by_service(audit.collect_iam(session), "IAMUser"))
        self.assertEqual(row["resource_id"], "alice")
        self.assertEqual(row["flag"], "ACTIVE")
        self.assertFalse(row["inferred"], "IAM last-used is native data, not inferred")

    def test_iam_user_last_used_takes_the_most_recent_of_password_and_keys(self):
        session = self._session(
            list_users={"Users": [{"UserName": "alice", "CreateDate": recent(500),
                                   "PasswordLastUsed": ancient()}]},
            list_access_keys={"AccessKeyMetadata": [{"AccessKeyId": "AKIA1"}]},
            get_access_key_last_used={"AccessKeyLastUsed": {"LastUsedDate": recent(2)}},
        )
        row = only(by_service(audit.collect_iam(session), "IAMUser"))
        self.assertEqual(row["flag"], "ACTIVE", "a recently used key must win over an old password")

    def test_iam_group_empty(self):
        session = self._session(
            list_groups={"Groups": [{"GroupName": "devs", "CreateDate": recent(500)}]},
            get_group={"Users": []},
        )
        row = only(by_service(audit.collect_iam(session), "IAMGroup"))
        self.assertEqual(row["flag"], "STALE (EMPTY GROUP)")

    def test_iam_policy_unattached(self):
        session = self._session(list_policies={"Policies": [{
            "PolicyName": "Unused", "Arn": f"arn:aws:iam::{ACCOUNT}:policy/Unused",
            "AttachmentCount": 0, "CreateDate": recent(500), "UpdateDate": recent(500),
        }]})
        row = only(by_service(audit.collect_iam(session), "IAMPolicy"))
        self.assertEqual(row["flag"], "STALE (UNATTACHED)")
        self.assertEqual(row["billing"], "free")

    def test_iam_role_with_no_scanned_consumer(self):
        session = self._session(list_roles={"Roles": [{
            "RoleName": "orphan-role", "Path": "/", "CreateDate": ancient(),
            "AssumeRolePolicyDocument": {"Statement": []},
        }]})
        row = only(by_service(audit.collect_iam(session, used_role_names=set()), "IAMRole"))
        self.assertIn("NO SCANNED CONSUMER", row["flag"])
        self.assertIn("NOT conclusive", row["notes"], "the hint must not read as proof")

    def test_iam_role_trusted_by_an_unscanned_service_is_not_called_an_orphan(self):
        """A role scoped to ECS is evidence it belongs to something this script
        doesn't scan - not evidence either way about whether it's used."""
        session = self._session(list_roles={"Roles": [{
            "RoleName": "ecs-task-role", "Path": "/", "CreateDate": ancient(),
            "AssumeRolePolicyDocument": {"Statement": [{
                "Effect": "Allow", "Principal": {"Service": "ecs-tasks.amazonaws.com"},
            }]},
        }]})
        row = only(by_service(audit.collect_iam(session, used_role_names=set()), "IAMRole"))
        self.assertNotIn("NO SCANNED CONSUMER", row["flag"])
        self.assertIn("ecs-tasks.amazonaws.com", row["notes"])

    def test_iam_role_confirmed_unused_by_access_analyzer(self):
        session = self._session(list_roles={"Roles": [{
            "RoleName": "old-role", "Path": "/", "CreateDate": ancient(),
            "AssumeRolePolicyDocument": {"Statement": []},
        }]})
        row = only(by_service(
            audit.collect_iam(session, used_role_names=set(),
                              unused_access_role_names={"old-role"}),
            "IAMRole"))
        self.assertIn("ACCESS ANALYZER: CONFIRMED UNUSED", row["flag"])

    def test_iam_role_falls_back_to_get_role_when_list_roles_omits_last_used(self):
        """ListRoles can omit RoleLastUsed that GetRole reports; fall back to GetRole."""
        session = self._session(
            list_roles={"Roles": [{
                "RoleName": "UploadFunctionRole", "Path": "/", "CreateDate": ancient(),
                "AssumeRolePolicyDocument": {"Statement": []},
            }]},
            get_role={"Role": {"RoleLastUsed": {"LastUsedDate": recent(5)}}},
        )
        row = only(by_service(audit.collect_iam(session, used_role_names=set()), "IAMRole"))
        self.assertFalse(row["inferred"], "GetRole's RoleLastUsed must count as native data, not a guess")
        self.assertEqual(row["flag"], "ACTIVE")

    def test_iam_role_stays_inferred_when_get_role_also_has_no_last_used(self):
        session = self._session(
            list_roles={"Roles": [{
                "RoleName": "never-used", "Path": "/", "CreateDate": ancient(),
                "AssumeRolePolicyDocument": {"Statement": []},
            }]},
            get_role={"Role": {}},
        )
        row = only(by_service(audit.collect_iam(session, used_role_names=set()), "IAMRole"))
        self.assertTrue(row["inferred"])

    def test_service_linked_roles_are_not_cross_checked(self):
        session = self._session(list_roles={"Roles": [{
            "RoleName": "AWSServiceRoleForRDS", "Path": "/aws-service-role/",
            "CreateDate": ancient(), "AssumeRolePolicyDocument": {"Statement": []},
        }]})
        row = only(by_service(audit.collect_iam(session, used_role_names=set()), "IAMRole"))
        self.assertNotIn("NO SCANNED CONSUMER", row["flag"])
        self.assertIn("service-linked", row["notes"])

    def test_access_analyzer_unused_role_finding(self):
        session = FakeSession({"accessanalyzer": {
            "list_analyzers": {"analyzers": [{
                "arn": f"arn:aws:access-analyzer:{REGION}:{ACCOUNT}:analyzer/unused",
                "name": "unused", "type": "ACCOUNT_UNUSED_ACCESS", "status": "ACTIVE",
            }]},
            "list_findings_v2": {"findings": [{
                "findingType": "UnusedIAMRole",
                "resource": f"arn:aws:iam::{ACCOUNT}:role/old-role",
                "status": "ACTIVE", "createdAt": recent(30), "analyzedAt": recent(1),
            }]},
        }})
        seen = set()
        row = only(audit.collect_access_analyzer_unused_roles(
            session, REGION, unused_access_role_names=seen))
        self.assertEqual(row["service"], "IAMRoleUnusedAccessFinding")
        self.assertEqual(row["name"], "old-role")
        self.assertEqual(seen, {"old-role"})

    def test_access_analyzer_without_an_unused_access_analyzer_is_a_silent_no_op(self):
        """Most accounts have no such analyzer; that's expected, not an error."""
        session = FakeSession({"accessanalyzer": {"list_analyzers": {"analyzers": [
            {"arn": "arn:x", "name": "ext", "type": "ACCOUNT", "status": "ACTIVE"}]}}})
        self.assertEqual(audit.collect_access_analyzer_unused_roles(session, REGION), [])


if __name__ == "__main__":
    unittest.main()
