"""The audit role's credentials renew themselves: a second AssumeRole when the first
are about to end, and a RoleRefreshFailed (never a bare crash) when renewal cannot work."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from botocore.exceptions import ClientError

from . import _PARENT  # noqa: F401  (import for the sys.path fixup side effect)

from aws_resource_audit.collect import role_credentials
from aws_resource_audit.collect.auth_guard import RoleRefreshFailed
from aws_resource_audit.collect.session import authenticated_session
from aws_resource_audit.settings import Settings

ACCOUNT = "123456789012"
LOGGER = "aws_resource_audit.collect.role_credentials"


def _creds(key, expires_in):
    return {"Credentials": {"AccessKeyId": key, "SecretAccessKey": key + "SECRET",
                            "SessionToken": key + "TOKEN",
                            "Expiration": datetime.now(timezone.utc) + expires_in}}


def _expired_token():
    return ClientError({"Error": {"Code": "ExpiredToken", "Message": "token expired"}},
                       "AssumeRole")


class RenewalTests(unittest.TestCase):

    def assume(self, *outcomes, region="us-east-1"):
        """authenticated_session on a mocked base whose AssumeRole returns `outcomes` in
        turn; returns (the assumed role's botocore credentials, the AssumeRole mock)."""
        base = mock.MagicMock()
        base.region_name = region
        self.base = base
        sts = base.client.return_value
        sts.get_caller_identity.return_value = {"Account": ACCOUNT, "Arn": "arn:x"}
        sts.assume_role.side_effect = list(outcomes)
        assumed = mock.MagicMock()
        assumed.client.return_value.get_caller_identity.return_value = {"Account": ACCOUNT}
        with mock.patch("boto3.Session", side_effect=[base, assumed]) as session_cls:
            authenticated_session(mock.Mock(profile="p"),
                                  Settings(assume_role=True, role_name="AWS-audit-role"))
        core = session_cls.call_args_list[1].kwargs["botocore_session"]
        return core.get_credentials(), sts.assume_role

    def test_credentials_inside_the_mandatory_window_are_renewed_on_use(self):
        creds, assume_role = self.assume(_creds("FIRST", timedelta(minutes=2)),
                                         _creds("SECOND", timedelta(hours=1)))
        frozen = creds.get_frozen_credentials()
        self.assertEqual(frozen.access_key, "SECOND")
        self.assertEqual(frozen.secret_key, "SECONDSECRET")
        self.assertEqual(frozen.token, "SECONDTOKEN")
        self.assertEqual(assume_role.call_count, 2)

    def test_credentials_far_from_expiry_are_not_renewed(self):
        creds, assume_role = self.assume(_creds("FIRST", timedelta(hours=1)))
        self.assertEqual(creds.get_frozen_credentials().access_key, "FIRST")
        self.assertEqual(assume_role.call_count, 1)

    def test_a_failed_renewal_raises_role_refresh_failed_and_logs(self):
        creds, _ = self.assume(_creds("FIRST", timedelta(minutes=2)), _expired_token())
        with self.assertLogs(LOGGER, "WARNING") as caught:
            with self.assertRaises(RoleRefreshFailed) as ctx:
                creds.get_frozen_credentials()
        self.assertIn("ExpiredToken", str(ctx.exception))
        self.assertIn("AWS-audit-role", caught.output[0])

    def test_renewed_credentials_that_already_ended_are_a_refresh_failure(self):
        creds, _ = self.assume(_creds("FIRST", timedelta(minutes=2)),
                               _creds("SECOND", timedelta(minutes=-5)))
        with self.assertLogs(LOGGER, "WARNING"):
            with self.assertRaises(RoleRefreshFailed) as ctx:
                creds.get_frozen_credentials()
        self.assertIn("before this computer's clock says now", str(ctx.exception))

    def test_credentials_missing_a_field_on_renewal_are_a_refresh_failure(self):
        incomplete = _creds("SECOND", timedelta(hours=1))
        del incomplete["Credentials"]["SessionToken"]
        creds, _ = self.assume(_creds("FIRST", timedelta(minutes=2)), incomplete)
        with self.assertLogs(LOGGER, "WARNING"):
            with self.assertRaises(RoleRefreshFailed) as ctx:
                creds.get_frozen_credentials()
        self.assertIn("SessionToken", str(ctx.exception))

    def test_a_base_session_without_a_region_uses_us_east_1_for_sts(self):
        self.assume(_creds("FIRST", timedelta(hours=1)), region=None)
        self.assertEqual(self.base.client.call_args.args, ("sts",))
        self.assertEqual(self.base.client.call_args.kwargs["region_name"], "us-east-1")


class ExpiryTests(unittest.TestCase):

    def test_expiry_is_an_iso_string_whatever_assume_role_returned(self):
        moment = datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        self.assertEqual(role_credentials._expiry(moment), "2030-01-02T03:04:05+00:00")
        self.assertEqual(role_credentials._expiry("2030-01-02T03:04:05Z"),
                         "2030-01-02T03:04:05Z")

    def test_already_expired_reads_every_form_of_expiry(self):
        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        cases = [
            (past.isoformat(), True), (future.isoformat(), False),
            (past, True), (future, False),
            (past.replace(tzinfo=None), True), (future.replace(tzinfo=None), False),
        ]
        for expiry, expected in cases:
            with self.subTest(expiry=expiry):
                self.assertEqual(
                    role_credentials._already_expired({"expiry_time": expiry}), expected)


if __name__ == "__main__":
    unittest.main()
