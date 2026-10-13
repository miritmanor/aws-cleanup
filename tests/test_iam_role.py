"""iam/aws-audit-role.yaml against the code, statically: every AWS call in collect/ is
granted, nothing else is, and every grant is an exact, read-only action."""

import ast
import os
import re
import unittest

import boto3

from . import _PARENT

ROLE_PATH = os.path.join(_PARENT, "iam", "aws-audit-role.yaml")
COLLECT_ROOT = os.path.join(_PARENT, "aws_resource_audit", "collect")

# boto3 service name -> IAM prefix, where they differ.
_IAM_PREFIX = {
    "accessanalyzer": "access-analyzer", "efs": "elasticfilesystem",
    "elb": "elasticloadbalancing", "elbv2": "elasticloadbalancing",
    "opensearch": "es", "opensearchserverless": "aoss", "stepfunctions": "states",
}
# API operations whose IAM action has another name.
_IAM_ACTION = {
    ("s3", "ListBuckets"): "s3:ListAllMyBuckets",
    ("s3", "ListObjectsV2"): "s3:ListBucket",
    ("s3", "GetBucketNotificationConfiguration"): "s3:GetBucketNotification",
    ("accessanalyzer", "ListFindingsV2"): "access-analyzer:ListFindings",
}
# Names that match an operation but are not one here: re.search, and the base
# identity's own AssumeRole (granted to it, not to this role).
_NOT_ROLE_CALLS = {"search", "assume_role"}
_READ_VERBS = ("Describe", "List", "Get", "BatchGet")


def role_actions():
    """The permission policy's actions, read as text: the CLI has no YAML parser."""
    with open(ROLE_PATH, encoding="utf-8") as f:
        text = f.read().split("Policies:", 1)[1]
    found = re.findall(r"^\s*(?:- |Action: )([a-z0-9-]+:[A-Za-z0-9*]+)\s*$", text, re.M)
    return set(found)


def _iam_action(service, api):
    if service in ("apigateway", "apigatewayv2"):
        return "apigateway:GET"
    return _IAM_ACTION.get((service, api), f"{_IAM_PREFIX.get(service, service)}:{api}")


def _file_names(tree):
    """The services a file makes clients for, and every name or string it mentions."""
    services, names = set(), set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "client" and node.args
                and isinstance(node.args[0], ast.Constant)):
            services.add(node.args[0].value)
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            names.add(node.value)
    return services, names


def code_calls():
    """{(file, operation name): candidate IAM actions}. A file's own clients decide
    the service; a file handed its client as an argument gets every match."""
    parsed = []
    for folder, _dirs, files in os.walk(COLLECT_ROOT):
        for name in sorted(files):
            if name.endswith(".py"):
                with open(os.path.join(folder, name), encoding="utf-8") as f:
                    parsed.append((name, *_file_names(ast.parse(f.read()))))
    all_services = set().union(*(services for _n, services, _m in parsed))
    session = boto3.Session(region_name="us-east-1", aws_access_key_id="x",
                            aws_secret_access_key="x")
    ops = {svc: session.client(svc).meta.method_to_api_mapping for svc in all_services}

    calls = {}
    for file_name, services, names in parsed:
        for name in names - _NOT_ROLE_CALLS:
            matches = {svc for svc in all_services if name in ops[svc]}
            local = matches & services
            chosen = local or matches
            if chosen:
                calls[(file_name, name)] = {_iam_action(svc, ops[svc][name]) for svc in chosen}
    return calls


class RoleMatchesTheCodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.actions = role_actions()
        cls.calls = code_calls()

    def test_the_role_file_was_read(self):
        self.assertGreater(len(self.actions), 100)

    def test_every_call_in_collect_is_granted(self):
        missing = sorted(f"{file}: {name} needs one of {sorted(candidates)}"
                         for (file, name), candidates in self.calls.items()
                         if not candidates & self.actions)
        self.assertEqual(missing, [], "add these to iam/aws-audit-role.yaml")

    def test_every_grant_is_used_by_some_call(self):
        used = set().union(*self.calls.values())
        self.assertEqual(sorted(self.actions - used), [],
                         "nothing in collect/ calls these; remove them from the role")

    def test_no_action_is_a_wildcard(self):
        self.assertEqual(sorted(a for a in self.actions if "*" in a), [])

    def test_every_action_is_a_read(self):
        writes = sorted(a for a in self.actions if a != "apigateway:GET"
                        and not a.split(":", 1)[1].startswith(_READ_VERBS))
        self.assertEqual(writes, [])


if __name__ == "__main__":
    unittest.main()
