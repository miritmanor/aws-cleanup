"""Dumb boto3 test doubles and fixtures. An unstubbed operation raises, naming it;
timestamps are relative to the pinned clock (recent()/ancient())."""

from datetime import timedelta

from botocore.exceptions import ClientError

from . import _PARENT  # noqa: F401  (import for the sys.path fixup side effect)

import aws_resource_audit as audit


# --- Time helpers, relative to the pinned clock.

def days_before_now(days):
    return audit.now() - timedelta(days=days)


def recent(days=5):
    """A timestamp comfortably inside the ACTIVE window."""
    return days_before_now(days)


def ancient(days=None):
    """A timestamp past STALE_THRESHOLD_DAYS, so flag_stale() returns STALE."""
    return days_before_now(days if days is not None else audit.STALE_THRESHOLD_DAYS + 100)


def aws_ts(dt):
    """The string form AWS uses for the handful of fields typed as 'string'
    rather than 'timestamp' (Lambda LastModified, AMI CreationDate)."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000+0000")


# --- botocore error helpers

def client_error(code="AccessDenied", message="not authorized", operation="DescribeThings"):
    return ClientError({"Error": {"Code": code, "Message": message}}, operation)


# --- Fake boto3 client, paginator and session

class FakeClient:
    """Canned-response boto3 client. `responses` maps an operation to a dict (one page),
    a list (pages), an Exception (raised) or a callable of the call kwargs."""

    def __init__(self, service_name, responses=None, not_pageable=()):
        self.service_name = service_name
        self.responses = dict(responses or {})
        self.calls = []  # [(operation, kwargs)] - for asserting what was asked
        # Some real list_* operations have no paginator; the fake must be able to say so.
        self.not_pageable = set(not_pageable)

    def _resolve(self, operation, kwargs):
        if operation not in self.responses:
            raise AssertionError(
                f"FakeClient({self.service_name!r}) has no canned response for "
                f"{operation!r}. Provided operations: {sorted(self.responses) or '(none)'}"
            )
        value = self.responses[operation]
        if isinstance(value, BaseException):
            raise value
        if callable(value):
            value = value(**kwargs)
            if isinstance(value, BaseException):
                raise value
        return value

    def __getattr__(self, operation):
        # Only reached for names not set in __init__, i.e. AWS operations.
        if operation.startswith("_"):
            raise AttributeError(operation)

        def call(**kwargs):
            self.calls.append((operation, kwargs))
            value = self._resolve(operation, kwargs)
            return value[0] if isinstance(value, list) else value

        return call

    def can_paginate(self, operation):
        return operation not in self.not_pageable

    def get_paginator(self, operation):
        if operation in self.not_pageable:
            raise AssertionError(
                f"{operation!r} has no paginator on the real client; "
                "paged() must fall back to NextToken by hand")
        return FakePaginator(self, operation)

    def operations_called(self):
        return [op for op, _ in self.calls]


class FakePaginator:
    def __init__(self, client, operation):
        self._client = client
        self._operation = operation

    def paginate(self, **kwargs):
        self._client.calls.append((self._operation, kwargs))
        value = self._client._resolve(self._operation, kwargs)
        return list(value) if isinstance(value, list) else [value]


class FakeSession:
    """Stand-in for boto3.Session. `clients` maps a service name to either a
    FakeClient or a plain {operation: response} dict."""

    def __init__(self, clients=None, region_name="us-east-1"):
        self.region_name = region_name
        self._clients = {}
        for name, spec in (clients or {}).items():
            self._clients[name] = spec if isinstance(spec, FakeClient) else FakeClient(name, spec)

    def client(self, service_name, region_name=None, config=None, **kwargs):
        # An empty client, so the first unstubbed operation raises an informative error.
        if service_name not in self._clients:
            self._clients[service_name] = FakeClient(service_name, {})
        return self._clients[service_name]

    def get(self, service_name):
        return self._clients[service_name]


# --- CloudWatch shorthands

NO_METRICS = {"get_metric_statistics": {"Datapoints": []}}


def metrics_at(when, value=42.0, stat="Sum"):
    return {"get_metric_statistics": {"Datapoints": [{"Timestamp": when, stat: value}]}}


def metrics_all_zero(when, stat="Sum"):
    """Datapoints exist but every one is zero - "we have data and it says
    nothing happened", which the script must distinguish from "no data"."""
    return {"get_metric_statistics": {"Datapoints": [{"Timestamp": when, stat: 0.0}]}}


METRICS_DENIED = {"get_metric_statistics": client_error(operation="GetMetricStatistics")}


# --- Row helpers for tests that drive the edge/grouping layer directly

def make_row(service, resource_id, name="", region="us-east-1", **kwargs):
    """A minimal row built through new_row(). A stated last_used gets matching activity
    evidence, so the fixture is a state a collector could produce."""
    last_used = kwargs.pop("last_used", "")
    activity = kwargs.pop("activity", None)
    if activity is None and last_used:
        activity = audit.activity.from_timestamp(
            last_used, source=audit.activity.SOURCE_CLOUDWATCH,
            metric="Invocations", explanation="fixture: observed activity")
    return audit.new_row(
        service, region, resource_id, name or resource_id,
        kwargs.pop("created", ""), last_used,
        kwargs.pop("last_used_days", 1), kwargs.pop("inferred", False),
        kwargs.pop("flag", "ACTIVE"), kwargs.pop("notes", ""),
        activity=activity,
        **kwargs
    )


def edge_conn_types(row):
    return [e["conn_type"] for e in row.get("_edges", [])]


def find_edge(row, conn_type):
    for edge in row.get("_edges", []):
        if edge["conn_type"] == conn_type:
            return edge
    return None
