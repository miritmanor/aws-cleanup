"""backend/app/agent/ must not import boto3 or the core's collect/ layer: the agent has
no direct AWS access by construction."""

import ast
import os
import unittest

_AGENT_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "app", "agent")

FORBIDDEN_MODULES = {"boto3", "botocore"}
FORBIDDEN_PREFIXES = ("aws_resource_audit.collect",)


class AgentHasNoAwsAccessTests(unittest.TestCase):
    def _agent_files(self):
        for root, _dirs, names in os.walk(_AGENT_ROOT):
            for name in sorted(names):
                if name.endswith(".py"):
                    yield os.path.join(root, name)

    def _offenders(self):
        found = []
        for path in sorted(self._agent_files()):
            with open(path) as handle:
                tree = ast.parse(handle.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module] if node.module and node.level == 0 else []
                else:
                    continue
                for name in names:
                    top = name.split(".")[0]
                    if top in FORBIDDEN_MODULES or name.startswith(FORBIDDEN_PREFIXES):
                        found.append(f"{os.path.basename(path)}:{node.lineno} imports {name}")
        return found

    def test_no_module_in_agent_imports_boto3_or_collect(self):
        offenders = self._offenders()
        self.assertEqual(
            offenders, [],
            "the agent must never reach AWS directly - it may only read the "
            "already-collected snapshot via SnapshotStore:\n  "
            + "\n  ".join(offenders))

    def test_the_guard_would_actually_catch_something(self):
        """Guard against a glob that matches nothing and so checks nothing."""
        self.assertGreater(len(list(self._agent_files())), 5)
