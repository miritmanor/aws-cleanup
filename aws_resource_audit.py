#!/usr/bin/env python3
"""Command line for the AWS scan: per-run flags only. Standing choices live in
audit_config.json; the work is in aws_resource_audit/run.py."""

import argparse
import logging

from aws_resource_audit import AuditError, load_settings, logsetup, output_paths, run
from cli_output import print_outputs

logger = logging.getLogger("aws_resource_audit.cli")


def build_parser():
    parser = argparse.ArgumentParser(
        description="Audit AWS resources for inventory + staleness signals",
        epilog="Where the output goes, and which detections run, live in "
               "audit_config.json, not here. Run aws_regenerate_report.py to "
               "redraw the report from the last scan without touching AWS.")
    parser.add_argument("--regions", nargs="*", help="Specific regions to scan (e.g. us-east-1 eu-west-1)")
    parser.add_argument("--all-regions", action="store_true", help="Scan all enabled regions")
    parser.add_argument("--profile", required=True,
                        help="AWS named profile to use. Required - there is no fallback to "
                             "whatever boto3's default chain happens to find, so a scan is "
                             "never accidentally run as an unintended identity. A profile "
                             "dedicated to this tool is recommended; see iam/README.md.")
    parser.add_argument("--no-cost", action="store_true",
                        help="Skip the Cost Explorer lookup. Cost enrichment is on by default; "
                             "use this to avoid the chargeable ce:GetCostAndUsage call "
                             "(~$0.01/request) or if you lack that permission.")
    parser.add_argument("--debug", action="store_true",
                        help="Print diagnostics: the resolved connection-type table at startup, "
                             "and every Lambda environment variable read ($LATEST plus each "
                             "alias-pinned version) with the resource candidates extracted from "
                             "it. Use it when a link you expect - typically Lambda->DynamoDB or "
                             "Lambda->S3 - is missing, to tell an absent value apart from one "
                             "the matcher didn't recognize.")
    parser.add_argument("--tag-groups", action="store_true",
                        help="Write a real 'Project=<name>' AWS tag to every resource in a NAMED "
                             "group (dry-run unless --confirm is also passed). See docstring caveats.")
    parser.add_argument("--confirm", action="store_true",
                        help="Actually perform the writes requested by --tag-groups, instead of "
                             "just printing what would be tagged.")
    return parser


if __name__ == "__main__":
    # The library raises AuditError; the command line turns it into SystemExit.
    args = build_parser().parse_args()
    # --debug selects DEBUG; AUDIT_LOG_LEVEL overrides it. Deliberately no log-level flag.
    logsetup.configure(debug=args.debug)
    try:
        settings = load_settings()
        run(args, settings=settings)
        print_outputs(output_paths(settings), settings, after_scan=True)
    except AuditError as e:
        # The message is the whole of what the user sees; the traceback exists
        # only under DEBUG, so the terminal never says the same thing twice.
        logger.debug("aborting: %s", e, exc_info=True)
        raise SystemExit(str(e))
