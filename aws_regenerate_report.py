#!/usr/bin/env python3
"""Command line for the report renderer: redraw the last scan without calling AWS.
No flags: the output location comes from audit_config.json in the current directory."""

import argparse
import logging

from aws_resource_audit import AuditError, load_settings, logsetup, output_paths, render
from cli_output import print_outputs

logger = logging.getLogger("aws_resource_audit.cli")


def build_parser():
    return argparse.ArgumentParser(
        description="Render the AWS inventory report from the last scan's snapshot",
        epilog="No flags, and no AWS calls. Output directory and snapshot name "
               "are settings in audit_config.json; run aws_resource_audit.py "
               "to produce a snapshot in the first place.")


if __name__ == "__main__":
    # The library raises; the CLI exits with the message verbatim.
    args = build_parser().parse_args()
    # AUDIT_LOG_LEVEL only. This parser has no flags and a test enforces that.
    logsetup.configure()
    try:
        settings = load_settings()
        render(args, settings=settings)
        print_outputs(output_paths(settings), settings, after_scan=False)
    except AuditError as e:
        logger.debug("aborting: %s", e, exc_info=True)
        raise SystemExit(str(e))
