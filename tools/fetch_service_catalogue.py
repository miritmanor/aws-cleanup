#!/usr/bin/env python3
"""Fetch AWS's public product directory and write the service-coverage CSV (no credentials).
Usage: python3 tools/fetch_service_catalogue.py [--check]"""

import argparse
import csv
import http.client
import json
import os
import re
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from service_map import SERVICE_SLUGS, types_by_slug  # noqa: E402


DIRECTORY_URL = "https://aws.amazon.com/api/dirs/items/search"
DIRECTORY_ID = "aws-products"
# One response, no working offset: ask for more than the catalogue holds, then check totals.
INITIAL_SIZE = 1000
# Keep service pages only; the directory also lists features.
TYPE_TAG_NAMESPACE = "aws-products#type"
SERVICE_TAG = "Service"
# Fewer than this means a truncated fetch: refuse to overwrite a good CSV.
MIN_PLAUSIBLE_SERVICES = 200

COLUMNS = [
    "category", "service_name", "service_slug", "covered",
    "covered_type_count", "covered_types", "free_tier", "launch_date",
    "product_url",
]

DEFAULT_OUTPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "aws_service_coverage.csv")


def fetch_directory(size, timeout):
    """Raw (items, totalHits) for one request against the product directory."""
    params = (
        f"?item.directoryId={DIRECTORY_ID}"
        f"&sort_by=item.additionalFields.productNameLowercase"
        f"&sort_order=asc"
        f"&size={size}"
        f"&item.locale=en_US"
    )
    req = urllib.request.Request(
        DIRECTORY_URL + params,
        headers={
            "Accept": "application/json",
            # Uncompressed, so a truncated read shows as truncated JSON.
            "Accept-Encoding": "identity",
            "User-Agent": "aws-resource-audit-catalogue/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.load(resp)
    return payload.get("items", []), payload.get("metadata", {}).get("totalHits", 0)


def fetch_all_products(timeout):
    """Every item in the directory: one request, retried larger if AWS reports more hits."""
    items, total = fetch_directory(INITIAL_SIZE, timeout)
    if total > len(items):
        items, total = fetch_directory(total + 100, timeout)
    if total > len(items):
        raise ValueError(
            f"AWS reports {total} products but returned {len(items)}, and this "
            f"endpoint has no offset parameter to fetch the rest with")
    return items


def unpack(element):
    """(additionalFields, tags) for one element; tags are a sibling of "item", not inside it."""
    return element.get("item", {}).get("additionalFields", {}), element.get("tags", [])


def is_service(tags):
    """True if AWS tagged this directory entry as a service rather than a
    feature. Absent tags mean absent evidence, so it is excluded."""
    for tag in tags:
        if tag.get("tagNamespaceId") == TYPE_TAG_NAMESPACE:
            return tag.get("name") == SERVICE_TAG
    return False


def clean_text(value):
    """Strip HTML markup and padding from a rich-text field, keeping AWS's wording."""
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", " ", value)
    text = text.replace("&nbsp;", " ").replace("\xa0", " ")
    return " ".join(text.split())


def slug_from_url(url):
    """Lowercased, unpadded path of a marketing URL, or "" when there is none."""
    if not url:
        return ""
    path = url.split("://", 1)[-1]
    path = path.split("/", 1)[1] if "/" in path else ""
    path = path.split("?", 1)[0].split("#", 1)[0]
    return path.strip("/").lower()


def build_rows(items):
    """Join products to our coverage as CSV rows, sorted by category then name."""
    covered = types_by_slug()
    rows = []
    for element in items:
        fields, tags = unpack(element)
        if not is_service(tags):
            continue
        url = fields.get("productUrl", "")
        slug = slug_from_url(url)
        types = covered.get(slug, [])
        rows.append({
            "category": clean_text(fields.get("productCategory", "")) or _tech_category(tags),
            "service_name": clean_text(fields.get("productName", "")),
            "service_slug": slug,
            "covered": "yes" if types else "no",
            "covered_type_count": len(types),
            "covered_types": ";".join(types),
            "free_tier": clean_text(fields.get("freeTierAvailability", "")),
            "launch_date": clean_text(fields.get("launchDate", "")),
            # Stored without the ?did=/&trk= tracking parameters AWS appends,
            # which are campaign junk that would churn the diff on every run.
            "product_url": url.split("?", 1)[0],
        })
    rows.sort(key=lambda r: (r["category"], r["service_name"].lower()))
    return credit_each_slug_once(rows)


def credit_each_slug_once(rows):
    """AWS sometimes lists one product twice under one URL ("Amazon SageMaker" and
    "Amazon SageMaker AI"); only the first row keeps the coverage, so no type counts twice."""
    credited = set()
    for row in rows:
        slug = row["service_slug"]
        if slug and slug in credited and row["covered"] == "yes":
            row.update({"covered": "no", "covered_type_count": 0, "covered_types": ""})
        elif slug and row["covered"] == "yes":
            credited.add(slug)
    return rows


def _tech_category(tags):
    """Fallback category from the GLOBAL#tech-category tag, for the handful of
    products that carry the tag but leave productCategory empty."""
    for tag in tags:
        if tag.get("tagNamespaceId") == "GLOBAL#tech-category":
            return clean_text(tag.get("name", ""))
    return ""


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def diff_rows(old, new):
    """(added, removed, recategorised), keyed on slug, or name when there is no slug."""
    def key(row):
        return row["service_slug"] or "name:" + row["service_name"]

    old_by_key = {key(r): r for r in old}
    new_by_key = {key(r): r for r in new}
    added = [new_by_key[k] for k in sorted(new_by_key.keys() - old_by_key.keys())]
    removed = [old_by_key[k] for k in sorted(old_by_key.keys() - new_by_key.keys())]
    moved = [
        (old_by_key[k], new_by_key[k])
        for k in sorted(new_by_key.keys() & old_by_key.keys())
        if old_by_key[k]["category"] != new_by_key[k]["category"]
    ]
    return added, removed, moved


def report(added, removed, moved, unmatched):
    """Print the drift. Returns True if anything changed."""
    for row in added:
        print(f"  ADDED        [{row['category']}] {row['service_name']}")
    for row in removed:
        print(f"  REMOVED      [{row['category']}] {row['service_name']}")
    for was, now in moved:
        print(f"  RECATEGORISED {now['service_name']}: "
              f"{was['category']} -> {now['category']}")
    if unmatched:
        # An unmatched slug means AWS renamed a product URL: warn loudly.
        print(f"\n  WARNING: {len(unmatched)} slug(s) in service_map.py matched no "
              f"product - those resource types are now reported as uncovered:")
        for slug in sorted(unmatched):
            print(f"    {slug}  ({', '.join(types_by_slug()[slug])})")
    return bool(added or removed or moved)


def main():
    parser = argparse.ArgumentParser(
        description="Fetch the AWS product catalogue and write the service-coverage CSV.")
    parser.add_argument("--check", action="store_true",
                        help="report drift against the committed CSV, write nothing, "
                             "and exit non-zero if anything changed")
    parser.add_argument("--output", default=DEFAULT_OUTPUT,
                        help=f"CSV path (default: {os.path.relpath(DEFAULT_OUTPUT)})")
    args = parser.parse_args()

    try:
        items = fetch_all_products(timeout=60)
    except (http.client.IncompleteRead, json.JSONDecodeError) as exc:
        # A truncated response usually means a proxy capping the transfer.
        print(f"The AWS product directory response was truncated ({exc}).\n"
              f"This usually means a sandbox or proxy is capping the transfer "
              f"rather than blocking it outright - the request returns HTTP 200 "
              f"and then stops partway. Retry with unrestricted network access "
              f"to aws.amazon.com.", file=sys.stderr)
        return 2
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"Could not fetch the AWS product directory: {exc}", file=sys.stderr)
        return 2

    rows = build_rows(items)
    if len(rows) < MIN_PLAUSIBLE_SERVICES:
        print(f"Refusing to continue: only {len(rows)} services parsed out of "
              f"{len(items)} directory items, which looks like a truncated fetch "
              f"rather than a real catalogue. The CSV was left untouched.",
              file=sys.stderr)
        return 2

    matched = {r["service_slug"] for r in rows}
    unmatched = set(SERVICE_SLUGS.values()) - matched

    old = read_csv(args.output)
    added, removed, moved = diff_rows(old, rows)

    covered = sum(1 for r in rows if r["covered"] == "yes")
    print(f"{len(rows)} AWS services in {len({r['category'] for r in rows})} categories; "
          f"{covered} covered by this tool ({len(SERVICE_SLUGS)} resource types).")

    if not old:
        print("No existing CSV to compare against - this is the first run.")
    changed = report(added, removed, moved, unmatched)
    if old and not changed:
        print("  no changes since the committed CSV")

    if args.check:
        return 1 if (changed or unmatched) else 0

    write_csv(args.output, rows)
    print(f"Wrote {os.path.relpath(args.output)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
