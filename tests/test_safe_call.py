"""safe_call's failure value: what kind of failure it was, read from AWS's error
code, and the guard that keeps every call site checking for it."""

import ast
import pathlib
import unittest

from botocore.exceptions import EndpointConnectionError

from aws_resource_audit import coverage as cov
from aws_resource_audit.collect.calls import CallFailed, paged, safe_call

from .fakes import FakeClient, client_error

PACKAGE = pathlib.Path(__file__).resolve().parent.parent / "aws_resource_audit"


def _raises(err):
    def call(**_kwargs):
        raise err
    return call


class FailureKindTests(unittest.TestCase):
    def _fail(self, err):
        with cov.ledger_of() as ledger:
            result = safe_call(_raises(err))
        return result, ledger

    def test_a_refusal_is_denied_and_still_reads_as_an_error(self):
        result, ledger = self._fail(client_error("AccessDeniedException"))
        self.assertIsInstance(result, CallFailed)
        self.assertIn("__error__", result)
        self.assertEqual((result.code, result.kind), ("AccessDeniedException", cov.DENIED))
        self.assertEqual([e.status for e in ledger.entries], [cov.DENIED])

    def test_a_rate_limit_is_throttled_not_denied(self):
        """The message says nothing about permissions; only the code says what happened."""
        result, ledger = self._fail(client_error("ThrottlingException", message="Rate exceeded"))
        self.assertEqual(result.kind, cov.THROTTLED)
        self.assertEqual([e.status for e in ledger.entries], [cov.THROTTLED])

    def test_an_absent_configuration_is_an_answer(self):
        result, _ledger = self._fail(client_error("NoSuchTagSet", message="The TagSet does not exist"))
        self.assertEqual(result.kind, cov.COMPLETE)

    def test_an_error_that_never_reached_aws_is_failed_with_its_class_name(self):
        result, _ledger = self._fail(EndpointConnectionError(endpoint_url="https://x"))
        self.assertEqual((result.code, result.kind), ("EndpointConnectionError", cov.FAILED))

    def test_a_throttled_listing_is_recorded_as_throttled(self):
        client = FakeClient("sqs", {"list_queues": client_error("Throttling", message="Rate exceeded")})
        with cov.ledger_of() as ledger:
            items, error = paged(client, "list_queues", "QueueUrls", service="SQSQueue")
        self.assertEqual((items, bool(error)), ([], True))
        self.assertEqual([e.status for e in ledger.entries], [cov.THROTTLED])

    def test_gaps_put_a_throttle_before_a_denial(self):
        with cov.ledger_of() as ledger:
            ledger.record(cov.INVENTORY, cov.DENIED, service="A")
            ledger.record(cov.INVENTORY, cov.THROTTLED, service="B")
            self.assertEqual([e.status for e in ledger.gaps()], [cov.THROTTLED, cov.DENIED])
            self.assertEqual(ledger.status("B"), cov.THROTTLED)


class EveryCallSiteChecksTests(unittest.TestCase):
    """The failure value is a dict, so resp.get("Items", []) on it is an empty
    list: a site that forgets to check reads "could not look" as "found none"."""

    def test_every_safe_call_result_is_checked_for_an_error(self):
        unchecked = []
        for path in sorted(PACKAGE.rglob("*.py")):
            source = path.read_text()
            tree = ast.parse(source)
            for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
                body = ast.get_source_segment(source, fn) or ""
                for node in ast.walk(fn):
                    if isinstance(node, ast.Attribute) and _is_safe_call(node.value):
                        unchecked.append(f"{path.name}:{node.lineno} uses .{node.attr} on the result directly")
                    if isinstance(node, ast.Assign) and _is_safe_call(node.value):
                        for name in (t.id for t in node.targets if isinstance(t, ast.Name)):
                            checks = (f'"__error__" in {name}', f'"__error__" not in {name}',
                                      f'{name}.get("__error__")')
                            if not any(check in body for check in checks):
                                unchecked.append(f"{path.name}:{node.lineno} never checks {name}")
        self.assertEqual(unchecked, [], "check the result with '\"__error__\" in resp' before reading it")


def _is_safe_call(node):
    return isinstance(node, ast.Call) and getattr(node.func, "id", None) == "safe_call"


if __name__ == "__main__":
    unittest.main()
