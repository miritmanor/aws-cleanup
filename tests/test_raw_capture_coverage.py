"""Every collector that builds a row also records its raw data, checked with ast so it
covers collectors this suite never runs."""

import ast
import json
import os
import tempfile
import unittest

import aws_resource_audit as audit
from aws_resource_audit.collect import raw_capture

from . import fakes

RESOURCES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "aws_resource_audit", "collect", "resources")


def _called_names(node):
    """Every function name called anywhere under `node`, as plain strings -
    "new_row" for a bare call, "raw_capture.record" for an attribute one."""
    names = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            names.add(f"{func.value.id}.{func.attr}")
    return names


def _row_building_functions():
    """(module, function, calls) per function that calls new_row()."""
    found = []
    for filename in sorted(os.listdir(RESOURCES_DIR)):
        if not filename.endswith(".py"):
            continue
        path = os.path.join(RESOURCES_DIR, filename)
        with open(path) as handle:
            tree = ast.parse(handle.read(), filename=path)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            calls = _called_names(node)
            if "new_row" in calls:
                found.append((filename, node.name, calls))
    return found


class RawCaptureCoverageTests(unittest.TestCase):

    def test_every_collector_that_builds_a_row_records_the_raw_data(self):
        missing = [f"{module}::{func}"
                   for module, func, calls in _row_building_functions()
                   if "raw_capture.record" not in calls]
        self.assertEqual(
            missing, [],
            "these collectors build rows without recording what AWS returned, "
            f"so the raw capture file would be silently incomplete: {missing}")

    def test_the_guard_would_actually_catch_something(self):
        """A glob that matched nothing, or an ast walk that found no functions,
        would make the test above pass while checking nothing at all."""
        found = _row_building_functions()
        self.assertGreater(len(found), 20,
                           "found too few row-building collectors to be reading "
                           "the real package")
        self.assertGreater(len({module for module, _f, _c in found}), 5,
                           "found row-building collectors in too few modules")


class RawCaptureReachesTheFileTests(unittest.TestCase):
    """The record() call actually runs, not just exists."""

    def test_a_collector_run_writes_the_resource_it_collected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "raw.jsonl")
            self.addCleanup(raw_capture.close)
            raw_capture.start(path)
            session = fakes.FakeSession({
                "ec2": {"describe_instances": {"Reservations": [{"Instances": [{
                    "InstanceId": "i-0abc", "ImageId": "ami-0123",
                    "InstanceType": "t3.micro", "State": {"Name": "running"},
                    "LaunchTime": fakes.recent(),
                    "UnselectedField": "never reaches a row, must reach the capture",
                }]}]}},
                "cloudwatch": fakes.metrics_at(fakes.recent()),
            })
            audit.collect_ec2_instances(session, "us-east-1")
            raw_capture.close()
            with open(path) as handle:
                lines = [json.loads(line) for line in handle if line.strip()]

            self.assertEqual(len(lines), 1, "a collector ran and captured nothing")
            self.assertEqual(lines[0]["service"], "EC2Instance")
            self.assertEqual(lines[0]["resource_id"], "i-0abc")
            # The whole point: a field no column selects is still in the file.
            self.assertEqual(lines[0]["raw"]["UnselectedField"],
                             "never reaches a row, must reach the capture")


if __name__ == "__main__":
    unittest.main()
