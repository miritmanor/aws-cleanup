"""authenticated_session() with boto3.Session mocked: the base profile and the
sts:AssumeRole branch, without real credentials."""

import unittest
from types import SimpleNamespace
from unittest import mock

from botocore.exceptions import ClientError, NoCredentialsError, ProfileNotFound

from aws_resource_audit.collect.session import ROLE_SESSION_NAME, authenticated_session
from aws_resource_audit.errors import AuditError
from aws_resource_audit.settings import ROLE_NAME_ENV, Settings

ACCOUNT = "123456789012"


def _client_error(operation):
    return ClientError({"Error": {"Code": "AccessDenied", "Message": "nope"}}, operation)


def _args(profile="aws-audit"):
    return SimpleNamespace(profile=profile)


class AuthenticatedSessionTests(unittest.TestCase):

    def _mock_session(self, ident=None, ident_error=False, assume_error=False,
                      assumed_ident=None):
        """A boto3.Session double whose sts client answers get_caller_identity
        (and, if asked, assume_role) the way real botocore would."""
        session = mock.MagicMock()
        session.region_name = "us-east-1"
        sts = session.client.return_value
        if ident_error:
            sts.get_caller_identity.side_effect = _client_error("GetCallerIdentity")
        else:
            sts.get_caller_identity.return_value = ident
        if assume_error:
            sts.assume_role.side_effect = _client_error("AssumeRole")
        else:
            sts.assume_role.return_value = {
                "Credentials": {
                    "AccessKeyId": "ASSUMEDKEY",
                    "SecretAccessKey": "ASSUMEDSECRET",
                    "SessionToken": "ASSUMEDTOKEN",
                }
            }
        return session

    def test_bad_base_credentials_raise_before_any_role_logic(self):
        base = self._mock_session(ident_error=True)
        with mock.patch("boto3.Session", return_value=base):
            with self.assertRaises(AuditError) as ctx:
                authenticated_session(_args(), Settings(assume_role=True))
        self.assertIn("Could not authenticate to AWS", str(ctx.exception))
        base.client.return_value.assume_role.assert_not_called()

    def test_missing_credentials_arrive_with_the_guidance_attached(self):
        """No credentials: botocore's sentence first, then where to put credentials."""
        base = self._mock_session()
        base.client.return_value.get_caller_identity.side_effect = NoCredentialsError()
        with mock.patch("boto3.Session", return_value=base):
            with self.assertRaises(AuditError) as ctx:
                authenticated_session(_args(), Settings())
        message = str(ctx.exception)
        self.assertIn("Could not authenticate to AWS: Unable to locate credentials", message)
        self.assertIn("Checked, in the order boto3 checks them:", message)
        self.assertIn("aws-audit", message)      # the profile that was asked for

    def test_an_unknown_profile_names_it_rather_than_raising_a_traceback(self):
        """ProfileNotFound from Session() itself becomes an AuditError."""
        with mock.patch("boto3.Session", side_effect=ProfileNotFound(profile="typo")):
            with self.assertRaises(AuditError) as ctx:
                authenticated_session(_args(profile="typo"), Settings())
        message = str(ctx.exception)
        self.assertIn("Could not authenticate to AWS", message)
        self.assertIn('The profile "typo" is not defined.', message)

    def test_assume_role_off_returns_the_base_session_untouched(self):
        ident = {"Account": ACCOUNT, "Arn": f"arn:aws:iam::{ACCOUNT}:user/aws-audit"}
        base = self._mock_session(ident=ident)
        with mock.patch("boto3.Session", return_value=base):
            session, returned_ident = authenticated_session(_args(), Settings(assume_role=False))
        self.assertIs(session, base)
        self.assertEqual(returned_ident, ident)
        base.client.return_value.assume_role.assert_not_called()

    def test_assume_role_on_builds_a_session_from_the_temporary_credentials(self):
        base_ident = {"Account": ACCOUNT, "Arn": f"arn:aws:iam::{ACCOUNT}:user/aws-audit"}
        assumed_ident = {"Account": ACCOUNT,
                         "Arn": f"arn:aws:sts::{ACCOUNT}:assumed-role/AWS-audit-role/{ROLE_SESSION_NAME}"}
        base = self._mock_session(ident=base_ident)
        assumed = self._mock_session(ident=assumed_ident)

        with mock.patch("boto3.Session", side_effect=[base, assumed]) as session_cls:
            session, returned_ident = authenticated_session(
                _args(), Settings(assume_role=True, role_name="AWS-audit-role"))

        self.assertIs(session, assumed)
        self.assertEqual(returned_ident, assumed_ident)

        base.client.return_value.assume_role.assert_called_once_with(
            RoleArn=f"arn:aws:iam::{ACCOUNT}:role/AWS-audit-role",
            RoleSessionName=ROLE_SESSION_NAME,
        )
        # The second Session() call is built from exactly the credentials
        # AssumeRole returned, not the base profile's own.
        _, kwargs = session_cls.call_args_list[1]
        self.assertEqual(kwargs["aws_access_key_id"], "ASSUMEDKEY")
        self.assertEqual(kwargs["aws_secret_access_key"], "ASSUMEDSECRET")
        self.assertEqual(kwargs["aws_session_token"], "ASSUMEDTOKEN")

    def test_assume_role_failure_raises_naming_the_role_arn(self):
        base_ident = {"Account": ACCOUNT, "Arn": f"arn:aws:iam::{ACCOUNT}:user/aws-audit"}
        base = self._mock_session(ident=base_ident, assume_error=True)
        with mock.patch("boto3.Session", return_value=base):
            with self.assertRaises(AuditError) as ctx:
                authenticated_session(_args(), Settings(assume_role=True, role_name="AWS-audit-role"))
        self.assertIn(f"arn:aws:iam::{ACCOUNT}:role/AWS-audit-role", str(ctx.exception))

    def test_the_role_name_env_var_overrides_the_configured_name(self):
        base_ident = {"Account": ACCOUNT, "Arn": f"arn:aws:iam::{ACCOUNT}:user/aws-audit"}
        assumed_ident = {"Account": ACCOUNT, "Arn": "irrelevant"}
        base = self._mock_session(ident=base_ident)
        assumed = self._mock_session(ident=assumed_ident)

        with mock.patch("boto3.Session", side_effect=[base, assumed]), \
             mock.patch.dict("os.environ", {ROLE_NAME_ENV: "EnvOverrideRole"}):
            authenticated_session(_args(), Settings(assume_role=True, role_name="CustomRole"))

        base.client.return_value.assume_role.assert_called_once_with(
            RoleArn=f"arn:aws:iam::{ACCOUNT}:role/EnvOverrideRole",
            RoleSessionName=ROLE_SESSION_NAME,
        )


if __name__ == "__main__":
    unittest.main()
