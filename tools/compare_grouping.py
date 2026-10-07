#!/usr/bin/env python3
"""What the new grouping would do to a scan you already have.

Phase 3 changes which resources end up in which project, and the only useful
way to judge that is against a real account rather than a fixture. This reads
an existing snapshot, re-runs the current grouping over it, and prints what
moved - with no AWS calls and without touching the snapshot or any report.

    python3 tools/compare_grouping.py [path/to/aws_scan.json]

The edges are rebuilt from each row's stored `_references`, which is what
makes this possible offline: `_edges` are deliberately not in the snapshot,
but a resolved reference already carries its target and its connection type.
"""

import argparse
import collections
import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aws_resource_audit.analyze.grouping import apply_project_grouping  # noqa: E402
from aws_resource_audit.naming.group_names import load_name_rules  # noqa: E402
from aws_resource_audit.settings import load_settings, output_paths  # noqa: E402
from aws_resource_audit.snapshot import read_snapshot  # noqa: E402


def edge_links_from_references(rows):
    """(source, target, reason, conn_type) for every resolved reference, from `_references`
    (the snapshot does not carry `_edges`)."""
    links = []
    for idx, row in enumerate(rows):
        for ref in row.get("_references", ()):
            if ref.get("kind") != "resolved":
                continue
            target = ref.get("target_idx")
            if target is None:
                continue
            links.append((idx, target,
                          f"{row['service']}:{row['resource_id']} {ref['rel']} "
                          f"{ref.get('target_service', '')}:{ref['target_id']}",
                          ref["conn_type"]))
    return links


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("snapshot", nargs="?",
                        help="defaults to the snapshot audit_config.json points at")
    args = parser.parse_args()

    settings = load_settings()
    path = args.snapshot or output_paths(settings)["snapshot"]
    snapshot = read_snapshot(path)
    rows = snapshot.rows

    before = {(r["service"], r["region"], r["resource_id"]): r.get("project_group", "")
              for r in rows}

    with contextlib.redirect_stdout(io.StringIO()):
        notes = apply_project_grouping(
            rows,
            edge_links=edge_links_from_references(rows),
            name_rules=load_name_rules(output_paths(settings)["groups"]))

    print(f"{len(rows)} resources from {os.path.abspath(path)}\n")

    moved = []
    for row in rows:
        key = (row["service"], row["region"], row["resource_id"])
        was, now = before[key], row.get("project_group", "")
        if was != now:
            moved.append((key, was, now, row["membership"]))

    counts = collections.Counter(r["membership"] for r in rows)
    print("AFTER, by membership:")
    for state, n in counts.most_common():
        print(f"  {n:4}  {state}")

    old_groups = collections.Counter(v for v in before.values() if v)
    new_groups = collections.Counter(r["project_group"] for r in rows if r["project_group"])
    print(f"\nGROUPS: {len(old_groups)} before -> {len(new_groups)} after")
    if old_groups:
        name, size = old_groups.most_common(1)[0]
        print(f"  largest before: {name!r} with {size} resources "
              f"({size / len(rows):.0%} of the account)")
    if new_groups:
        name, size = new_groups.most_common(1)[0]
        print(f"  largest after:  {name!r} with {size} resources "
              f"({size / len(rows):.0%} of the account)")

    print(f"\nMOVED: {len(moved)} resource(s) changed group")
    for key, was, now, membership in moved[:40]:
        print(f"  {key[0]}:{key[2][:28]:30} {was[:22]!r:24} -> {now[:22]!r} [{membership}]")
    if len(moved) > 40:
        print(f"  ... and {len(moved) - 40} more")

    if notes["shared"]:
        print(f"\nSHARED between projects: {len(notes['shared'])}")
        for line in notes["shared"][:10]:
            print(f"  {line}")

    if notes["conflicts"]:
        print(f"\nREFUSED merges (links crossing a project boundary): "
              f"{len(notes['conflicts'])}")
        for line in notes["conflicts"][:10]:
            print(f"  {line}")

    if notes["candidate_words"]:
        print(f"\nNAME WORDS you could write a rule for: "
              f"{len(notes['candidate_words'])}")
        for entry in notes["candidate_words"][:15]:
            print(f"  {entry['word']:24} {len(entry['resources'])} resources")


if __name__ == "__main__":
    main()
