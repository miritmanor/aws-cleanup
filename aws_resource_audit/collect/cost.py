"""The Cost Explorer lookup: one chargeable request grouped by service AND region,
fully paginated. A partial response is kept and marked incomplete."""

import logging
from datetime import timedelta

from ..billing import (
    BillingAmount, BillingResult, UsageTypeAmount, ZERO, not_queried, to_decimal,
)
from ..config import (
    BILLING_CACHE_TTL_MINUTES, BILLING_COVERAGE, COST_LOOKBACK_DAYS, RETRY_CONFIG,
    now,
)
from .calls import safe_call

logger = logging.getLogger(__name__)

METRIC = "UnblendedCost"
GROUP_BY = (("DIMENSION", "SERVICE"), ("DIMENSION", "REGION"))

# Keyed by account and query. A scan never reads it (each scan reports what it saw);
# it only saves a repeat request within one scan.
_cache = {}


def _cache_key(account, period, metric, group_by, filters):
    return (account, period, metric, group_by, filters)


def cache_clear():
    """For tests, and for a process that has changed credentials under itself."""
    _cache.clear()


def _cached(key):
    entry = _cache.get(key)
    if entry is None:
        return None
    observed, result = entry
    if (now() - observed) > timedelta(minutes=BILLING_CACHE_TTL_MINUTES):
        _cache.pop(key, None)
        return None
    return result


def collect_billing(session, *, account="", no_cost=False, refresh=True):
    """The account's spend over COST_LOOKBACK_DAYS. no_cost skips the request and
    reports "not queried", never zero. A scan always refreshes."""
    if no_cost:
        return not_queried()

    end = now().date()
    start = end - timedelta(days=COST_LOOKBACK_DAYS)
    period = (start, end)
    key = _cache_key(account, period, METRIC, GROUP_BY, ())
    hit = None if refresh else _cached(key)
    if hit is not None:
        logger.debug("billing: reusing the response observed at %s", hit.observed_at)
        return hit

    ce = session.client("ce", region_name="us-east-1", config=RETRY_CONFIG)
    amounts, currency = {}, ""
    estimated, complete, error = False, True, ""
    token = None
    while True:
        request = {
            "TimePeriod": {"Start": start.isoformat(), "End": end.isoformat()},
            "Granularity": "MONTHLY",
            "Metrics": [METRIC],
            "GroupBy": [{"Type": kind, "Key": name} for kind, name in GROUP_BY],
        }
        if token:
            request["NextPageToken"] = token
        resp = safe_call(ce.get_cost_and_usage, **request)
        if "__error__" in resp:
            error = resp["__error__"]
            complete = False
            logger.warning("Cost Explorer request failed after %d cell(s): %s",
                           len(amounts), error)
            break
        for result in resp.get("ResultsByTime", []):
            # One estimated period makes the whole figure estimated.
            estimated = estimated or bool(result.get("Estimated"))
            for group in result.get("Groups", []):
                keys = group.get("Keys") or []
                service = keys[0] if keys else ""
                # "NoRegion" -> "" so it never matches a real region.
                region = keys[1] if len(keys) > 1 else ""
                if region in ("NoRegion", "global"):
                    region = ""
                metric = (group.get("Metrics") or {}).get(METRIC) or {}
                currency = currency or metric.get("Unit") or ""
                amount = to_decimal(metric.get("Amount")) or ZERO
                # Zero and credit-offset cells are kept: the service still exists.
                slot = (service, region)
                amounts[slot] = amounts.get(slot, ZERO) + amount
        token = resp.get("NextPageToken")
        if not token:
            break

    usage_types, usage_error = _collect_usage_types(ce, start, end, amounts)
    if usage_error and not error:
        error = usage_error

    result = BillingResult(
        amounts=tuple(BillingAmount(service=svc, region=region, amount=total)
                      for (svc, region), total in sorted(amounts.items())),
        usage_types=usage_types,
        period_start=start, period_end=end, metric=METRIC,
        currency=currency or "USD", account=account,
        estimated=estimated, complete=complete, queried=True,
        observed_at=now(), error=error)
    # An incomplete response is never cached.
    if complete:
        _cache[key] = (now(), result)
    return result


def _broad_buckets(amounts):
    """Billed services this tool cannot divide that spent something."""
    spend = {}
    for (service, _region), amount in amounts.items():
        spend[service] = spend.get(service, ZERO) + amount
    return sorted(name for name, total in spend.items()
                  if total > ZERO and BILLING_COVERAGE.get(name, ((), ()))[1])


def _collect_usage_types(ce, start, end, amounts):
    """What is inside the buckets this tool refuses to divide, by usage type. A second
    chargeable request, made only when such a bucket exists; failure is non-fatal."""
    services = _broad_buckets(amounts)
    if not services:
        return (), ""
    totals, token = {}, None
    while True:
        request = {
            "TimePeriod": {"Start": start.isoformat(), "End": end.isoformat()},
            "Granularity": "MONTHLY",
            "Metrics": [METRIC],
            "Filter": {"Dimensions": {"Key": "SERVICE", "Values": services}},
            "GroupBy": [{"Type": "DIMENSION", "Key": "SERVICE"},
                        {"Type": "DIMENSION", "Key": "USAGE_TYPE"}],
        }
        if token:
            request["NextPageToken"] = token
        resp = safe_call(ce.get_cost_and_usage, **request)
        if "__error__" in resp:
            logger.warning("usage-type breakdown failed: %s", resp["__error__"])
            return (), resp["__error__"]
        for result in resp.get("ResultsByTime", []):
            for group in result.get("Groups", []):
                keys = group.get("Keys") or ["", ""]
                metric = (group.get("Metrics") or {}).get(METRIC) or {}
                amount = to_decimal(metric.get("Amount")) or ZERO
                slot = (keys[0], keys[1] if len(keys) > 1 else "")
                totals[slot] = totals.get(slot, ZERO) + amount
        token = resp.get("NextPageToken")
        if not token:
            break
    return tuple(UsageTypeAmount(service=svc, usage_type=usage, amount=total)
                 for (svc, usage), total in sorted(totals.items())
                 if total != ZERO), ""
