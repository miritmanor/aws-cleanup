"""AuthGuard's event handlers: a clock error retried once, a rejected credential
recorded, every other error left to botocore, and check() turning it into an AuditError."""

import unittest
from types import SimpleNamespace
from unittest import mock

from . import _PARENT  # noqa: F401  (import for the sys.path fixup side effect)

from aws_resource_audit.collect.auth_guard import (
    CLOCK_RETRY_SECONDS, AuthGuard, RoleRefreshFailed, _error, rejection_message)
from aws_resource_audit.collect.credentials import _expired_help
from aws_resource_audit.errors import AuditError

LOGGER = "aws_resource_audit.collect.auth_guard"
OPERATION = SimpleNamespace(name="DescribeInstances")


def failure(code, message="boom"):
    return (SimpleNamespace(status_code=400), {"Error": {"Code": code, "Message": message}})


def request(region="eu-west-1"):
    return {"context": {"client_region": region}}


class RetryOnceTests(unittest.TestCase):

    def setUp(self):
        self.guard = AuthGuard()

    def call(self, response, request_dict=None):
        return self.guard.on_needs_retry(
            response=response, operation=OPERATION,
            request_dict=request() if request_dict is None else request_dict)

    def test_a_clock_error_is_retried_once_then_recorded(self):
        req = request("eu-west-1")
        with self.assertLogs(LOGGER, "WARNING") as first:
            self.assertEqual(self.call(failure("RequestExpired", "too old"), req),
                             CLOCK_RETRY_SECONDS)
        self.assertIsNone(self.guard.rejection)
        self.assertIn("retrying once", first.output[0])
        with self.assertLogs(LOGGER, "WARNING") as second:
            self.assertIsNone(self.call(failure("RequestExpired", "too old"), req))
        self.assertEqual(self.guard.rejection, {
            "region": "eu-west-1", "operation": "DescribeInstances",
            "code": "RequestExpired", "message": "too old", "clock": True})
        self.assertIn("rejected the scan's credentials", second.output[0])

    def test_a_signature_expired_message_counts_as_a_clock_error(self):
        for code in ("InvalidSignatureException", "SignatureDoesNotMatch"):
            with self.subTest(code=code):
                guard = AuthGuard()
                req = request()
                response = failure(code, "Signature expired: 20260101 is now earlier")
                with self.assertLogs(LOGGER, "WARNING"):
                    self.assertEqual(guard.on_needs_retry(
                        response=response, operation=OPERATION, request_dict=req),
                        CLOCK_RETRY_SECONDS)
                    self.assertIsNone(guard.on_needs_retry(
                        response=response, operation=OPERATION, request_dict=req))
                self.assertTrue(guard.rejection["clock"])

    def test_an_expired_token_is_retried_once_and_is_not_a_clock_error(self):
        for code in ("ExpiredToken", "ExpiredTokenException"):
            with self.subTest(code=code):
                guard = AuthGuard()
                req = request()
                response = failure(code, "The security token is expired")
                with self.assertLogs(LOGGER, "WARNING"):
                    self.assertEqual(guard.on_needs_retry(
                        response=response, operation=OPERATION, request_dict=req),
                        CLOCK_RETRY_SECONDS)
                    self.assertIsNone(guard.on_needs_retry(
                        response=response, operation=OPERATION, request_dict=req))
                self.assertEqual(guard.rejection["code"], code)
                self.assertFalse(guard.rejection["clock"])

    def test_other_errors_and_successes_are_left_to_botocore(self):
        cases = [failure("AuthFailure"), failure("InvalidClientTokenId"),
                 failure("ThrottlingException", "Rate exceeded"), failure("AccessDenied"),
                 (SimpleNamespace(status_code=200), {"ResponseMetadata": {}}), None]
        for response in cases:
            with self.subTest(response=response):
                self.assertIsNone(self.call(response))
                self.assertIsNone(self.guard.rejection)

    def test_after_a_rejection_a_fresh_request_fails_fast(self):
        with self.assertLogs(LOGGER, "WARNING"):
            req = request()
            self.call(failure("RequestExpired"), req)
            self.call(failure("RequestExpired"), req)
        self.assertIsNotNone(self.guard.rejection)
        self.assertIsNone(self.call(failure("RequestExpired"), request()))

    def test_a_missing_region_is_recorded_as_global(self):
        req = {"context": {}}
        with self.assertLogs(LOGGER, "WARNING"):
            self.call(failure("ExpiredToken"), req)
            self.call(failure("ExpiredToken"), req)
        self.assertEqual(self.guard.rejection["region"], "global")


class ErrorShapeTests(unittest.TestCase):

    def test_what_is_not_a_failed_response_has_no_error(self):
        self.assertIsNone(_error((SimpleNamespace(),)))
        self.assertIsNone(_error((SimpleNamespace(), {"Error": {"Message": "no code"}})))
        self.assertIsNone(_error((SimpleNamespace(), {"Error": None})))
        self.assertIsNone(_error((SimpleNamespace(), None)))

    def test_a_failed_response_gives_code_and_message(self):
        self.assertEqual(_error(failure("ExpiredToken", "old")), ("ExpiredToken", "old"))
        self.assertEqual(_error((SimpleNamespace(), {"Error": {"Code": "X"}})), ("X", ""))


class RefreshFailureTests(unittest.TestCase):

    def test_a_failed_renewal_is_recorded_with_the_operation_from_the_event(self):
        guard = AuthGuard()
        with self.assertLogs(LOGGER, "WARNING"):
            guard.on_call_error(exception=RoleRefreshFailed(error="denied"),
                                context={"client_region": "us-east-1"},
                                event_name="after-call-error.ec2.DescribeInstances")
        self.assertEqual(guard.rejection["operation"], "DescribeInstances")
        self.assertEqual(guard.rejection["region"], "us-east-1")
        self.assertEqual(guard.rejection["code"], "RoleRefreshFailed")
        self.assertFalse(guard.rejection["clock"])
        self.assertIn("denied", guard.rejection["message"])

    def test_an_empty_event_name_and_no_context_still_record(self):
        guard = AuthGuard()
        with self.assertLogs(LOGGER, "WARNING"):
            guard.on_call_error(exception=RoleRefreshFailed(error="x"), context=None,
                                event_name="")
        self.assertEqual(guard.rejection["operation"], "")
        self.assertEqual(guard.rejection["region"], "global")

    def test_other_call_errors_are_not_recorded(self):
        from botocore.exceptions import EndpointConnectionError
        guard = AuthGuard()
        guard.on_call_error(exception=EndpointConnectionError(endpoint_url="https://x"),
                            context={}, event_name="after-call-error.ec2.DescribeInstances")
        self.assertIsNone(guard.rejection)


class CheckTests(unittest.TestCase):

    def reject(self, code, message):
        guard = AuthGuard()
        req = request()
        with self.assertLogs(LOGGER, "WARNING"):
            for _ in range(2):
                guard.on_needs_retry(response=failure(code, message),
                                     operation=OPERATION, request_dict=req)
        return guard

    def test_without_a_rejection_check_returns_nothing(self):
        self.assertIsNone(AuthGuard().check("some-profile"))

    def test_a_clock_rejection_says_the_clock_and_that_nothing_was_saved(self):
        guard = self.reject("RequestExpired", "too old")
        with self.assertRaises(AuditError) as caught:
            guard.check()
        text = str(caught.exception)
        self.assertIn("clock is behind", text)
        self.assertIn("no results were saved", text)
        self.assertIn("RequestExpired on DescribeInstances in eu-west-1", text)

    def test_an_expired_rejection_carries_the_credentials_help(self):
        guard = self.reject("ExpiredToken", "The security token included is expired")
        with self.assertRaises(AuditError) as caught:
            guard.check("my-profile")
        text = str(caught.exception)
        self.assertTrue(text.startswith("Could not authenticate to AWS"))
        self.assertIn("no results were saved", text)
        self.assertIn(_expired_help("my-profile").splitlines()[0], text)
        self.assertNotIn("clock is behind", text)

    def test_the_first_rejection_is_the_one_kept(self):
        guard = AuthGuard()
        with self.assertLogs(LOGGER, "WARNING"):
            guard._record("us-east-1", "First", "ExpiredToken", "one", False)
            guard._record("eu-west-1", "Second", "RequestExpired", "two", True)
        self.assertEqual(guard.rejection["operation"], "First")
        with self.assertRaises(AuditError) as caught:
            guard.check()
        self.assertIn("First", str(caught.exception))
        self.assertNotIn("Second", str(caught.exception))

    def test_without_credentials_help_the_message_is_just_the_rejection(self):
        rejection = {"region": "us-east-1", "operation": "Op", "code": "ExpiredToken",
                     "message": "old", "clock": False}
        with mock.patch("aws_resource_audit.collect.auth_guard.credentials_help",
                        return_value=""):
            text = rejection_message(rejection)
        self.assertTrue(text.startswith("Could not authenticate to AWS"))
        self.assertTrue(text.endswith("the previous results are unchanged."))


if __name__ == "__main__":
    unittest.main()
