"""AuthGuard on a real boto3 client, with only the HTTP send replaced: the retry delay is
really honoured by botocore, and a second failure really reaches the caller."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import boto3
from botocore.awsrequest import AWSResponse
from botocore.exceptions import ClientError

from . import _PARENT  # noqa: F401  (import for the sys.path fixup side effect)

from aws_resource_audit.config import RETRY_CONFIG
from aws_resource_audit.collect.auth_guard import (
    CLOCK_RETRY_SECONDS, AuthGuard, RoleRefreshFailed)
from aws_resource_audit.collect.role_credentials import assumed_role_session

LOGGER = "aws_resource_audit.collect.auth_guard"
NS = "http://elasticloadbalancing.amazonaws.com/doc/2015-12-01/"


def _xml(code, message):
    return (f'<ErrorResponse xmlns="{NS}"><Error><Type>Sender</Type><Code>{code}</Code>'
            f'<Message>{message}</Message></Error><RequestId>r-1</RequestId>'
            '</ErrorResponse>').encode()


OK_BODY = (f'<DescribeLoadBalancersResponse xmlns="{NS}"><DescribeLoadBalancersResult>'
           '<LoadBalancers/></DescribeLoadBalancersResult>'
           '<ResponseMetadata><RequestId>r-2</RequestId></ResponseMetadata>'
           '</DescribeLoadBalancersResponse>').encode()


class _Raw:
    def __init__(self, body):
        self.body = body

    def stream(self, **_kwargs):
        yield self.body


def _response(status, body):
    return AWSResponse("https://elasticloadbalancing.eu-west-1.amazonaws.com/",
                       status, {}, _Raw(body))


class RealClientTests(unittest.TestCase):

    def setUp(self):
        self.guard = AuthGuard()
        session = boto3.Session(aws_access_key_id="AKIDEXAMPLE", aws_secret_access_key="x",
                                region_name="eu-west-1")
        self.guard.install(session)
        # The scan's retry mode, whose own retry handler runs before the guard's.
        self.client = session.client("elbv2", config=RETRY_CONFIG)
        self.sends = []
        self.client.meta.events.register("before-send", self._send)
        patch = mock.patch("botocore.endpoint.time.sleep")
        self.sleep = patch.start()
        self.addCleanup(patch.stop)

    def _send(self, **_kwargs):
        index = len(self.sends)
        self.sends.append(index)
        return self.script[index]

    def expired(self):
        return _response(400, _xml("SignatureDoesNotMatch",
                                   "Signature expired: 20260101T000000Z is now earlier"))

    def test_a_clock_error_then_success_returns_the_result_after_one_wait(self):
        self.script = [self.expired(), _response(200, OK_BODY)]
        result = self.client.describe_load_balancers()
        self.assertEqual(result["LoadBalancers"], [])
        self.assertEqual(len(self.sends), 2)
        self.sleep.assert_called_once_with(CLOCK_RETRY_SECONDS)
        self.assertIsNone(self.guard.rejection)

    def test_a_clock_error_twice_raises_and_is_recorded(self):
        self.script = [self.expired(), self.expired()]
        with self.assertLogs(LOGGER, "WARNING"):
            with self.assertRaises(ClientError):
                self.client.describe_load_balancers()
        self.assertEqual(len(self.sends), 2)
        self.sleep.assert_called_once_with(CLOCK_RETRY_SECONDS)
        self.assertEqual(self.guard.rejection["region"], "eu-west-1")
        self.assertEqual(self.guard.rejection["operation"], "DescribeLoadBalancers")
        self.assertTrue(self.guard.rejection["clock"])

    def test_an_access_denied_is_not_ours_to_retry(self):
        self.script = [_response(403, _xml("AccessDenied", "not authorized"))]
        with self.assertRaises(ClientError):
            self.client.describe_load_balancers()
        self.assertEqual(len(self.sends), 1)
        self.assertIsNone(self.guard.rejection)
        self.assertNotIn(mock.call(CLOCK_RETRY_SECONDS), self.sleep.call_args_list)


def _assumed(key, expires_in):
    return {"Credentials": {"AccessKeyId": key, "SecretAccessKey": "secret",
                            "SessionToken": "token",
                            "Expiration": datetime.now(timezone.utc) + expires_in}}


class AssumedRoleClientTests(unittest.TestCase):
    """The audit role's self-renewing session, signing real requests."""

    def _client(self, *assume_outcomes):
        base = boto3.Session(aws_access_key_id="AKIDBASE", aws_secret_access_key="x",
                             region_name="eu-west-1")
        sts = mock.Mock()
        sts.assume_role.side_effect = list(assume_outcomes)
        with mock.patch.object(base, "client", return_value=sts):
            session = assumed_role_session(base, "arn:aws:iam::123456789012:role/Audit")
        self.guard = AuthGuard().install(session)
        client = session.client("elbv2", config=RETRY_CONFIG)
        self.headers = []

        def send(request, **_kwargs):
            self.headers.append(request.headers.get("Authorization", b""))
            return _response(200, OK_BODY)

        client.meta.events.register("before-send", send)
        return client

    def test_a_renewal_that_fails_mid_scan_is_recorded_and_raised(self):
        expired = ClientError({"Error": {"Code": "ExpiredToken", "Message": "expired"}},
                              "AssumeRole")
        client = self._client(_assumed("AKIDFIRST", timedelta(minutes=2)), expired)
        with self.assertLogs("aws_resource_audit.collect", "WARNING"):
            with self.assertRaises(RoleRefreshFailed):
                client.describe_load_balancers()
        self.assertEqual(self.guard.rejection["code"], "RoleRefreshFailed")
        self.assertEqual(self.guard.rejection["operation"], "DescribeLoadBalancers")
        self.assertEqual(self.headers, [])

    def test_a_request_is_signed_with_the_assumed_roles_key(self):
        client = self._client(_assumed("AKIDASSUMED", timedelta(hours=1)))
        client.describe_load_balancers()
        header = self.headers[0]
        header = header.decode() if isinstance(header, bytes) else header
        self.assertIn("Credential=AKIDASSUMED/", header)
        self.assertNotIn("AKIDBASE", header)
        self.assertIsNone(self.guard.rejection)


if __name__ == "__main__":
    unittest.main()
