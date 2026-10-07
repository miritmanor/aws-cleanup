"""Shared fixtures for the cost tests: a queried bill, a complete ledger, attribution."""

from datetime import date, timedelta
from decimal import Decimal

from .fakes import make_row

import aws_resource_audit as audit
from aws_resource_audit.analyze.cost import attribute_costs
from aws_resource_audit.billing import BillingAmount, BillingResult
from aws_resource_audit.present.cost import cost_columns

PERIOD_END = date(2026, 1, 31)
PERIOD_START = PERIOD_END - timedelta(days=30)


def bill(*amounts, **kwargs):
    """A BillingResult that was queried and complete unless a test says not."""
    fields = dict(period_start=PERIOD_START, period_end=PERIOD_END,
                  currency="USD", account="123456789012", queried=True,
                  complete=True)
    fields.update(kwargs)
    return BillingResult(
        amounts=tuple(BillingAmount(s, r, Decimal(a)) for s, r, a in amounts),
        **fields)


def complete_inventory(*services, scope="us-east-1"):
    return [{"service": s, "capability": "inventory", "status": "complete",
             "scope": scope} for s in services]


EC2_TYPES = ("EC2Instance", "ReservedInstance", "SpotInstanceRequest")
EC2_COMPUTE = "Amazon Elastic Compute Cloud - Compute"


def attributed(rows, billing, coverage=None):
    attribute_costs(rows, billing, coverage_entries=coverage)
    return [cost_columns(row) for row in rows]

ROUTE53 = "Amazon Route 53"
GLOBAL = complete_inventory("Route53HostedZone", "CloudFrontDistribution",
                            "S3Bucket", scope="global")


def with_usage(billing, *lines):
    return BillingResult(**{**billing.__dict__, "usage_types": tuple(
        audit.UsageTypeAmount(s, u, Decimal(a)) for s, u, a in lines)})


def zone(zid, **kwargs):
    return make_row("Route53HostedZone", zid, region="global", **kwargs)
