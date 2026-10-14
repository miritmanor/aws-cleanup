"""deploy/ecs/ and tools/build_ecs_template.py: the read-only policy has one source
(iam/aws-audit-role.yaml), and every taggable resource carries the Project tag."""

import importlib.util
import os
import re
import unittest

from . import _PARENT
from .test_iam_role import role_actions

_spec = importlib.util.spec_from_file_location(
    "build_ecs_template", os.path.join(_PARENT, "tools", "build_ecs_template.py"))
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

BASE_PATH = os.path.join(_PARENT, "deploy", "ecs", "base.yaml")
# Actions the app template may write itself: role trust, ECS Exec, EFS mount.
OWN_ACTIONS = {
    "sts:AssumeRole",
    "ssmmessages:CreateControlChannel", "ssmmessages:CreateDataChannel",
    "ssmmessages:OpenControlChannel", "ssmmessages:OpenDataChannel",
    "elasticfilesystem:ClientMount", "elasticfilesystem:ClientWrite",
}
# Types with no Tags property; each belongs to a tagged parent.
UNTAGGABLE = {
    "AWS::EC2::VPCGatewayAttachment", "AWS::EC2::Route",
    "AWS::EC2::SubnetRouteTableAssociation", "AWS::EFS::MountTarget",
}
BACKEND = "ghcr.io/example/aws-cleanup-backend@sha256:" + "a" * 64
FRONTEND = "ghcr.io/example/aws-cleanup-frontend@sha256:" + "b" * 64


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def actions_in(text):
    return set(re.findall(r"^\s*(?:- |Action: )([a-z0-9-]+:[A-Za-z0-9*]+)\s*$", text, re.M))


def resources(text):
    """{logical id: (type, block text)} for the top-level Resources section."""
    section = text.split("\nResources:\n", 1)[1].split("\nOutputs:\n", 1)[0]
    found = {}
    for match in re.finditer(r"^  ([A-Za-z0-9]+):\n((?:(?:    .*|\s*)\n)+)", section, re.M):
        type_match = re.search(r"^    Type: (\S+)", match.group(2), re.M)
        found[match.group(1)] = (type_match.group(1), match.group(2))
    return found


class GeneratedTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app_source = _read(gen.APP_SOURCE)
        cls.generated = gen.build(cls.app_source, _read(gen.ROLE_SOURCE))

    def test_it_grants_exactly_the_role_file_plus_its_own_actions(self):
        self.assertEqual(actions_in(self.generated), role_actions() | OWN_ACTIONS)

    def test_the_source_holds_no_copy_of_the_read_only_list(self):
        self.assertEqual(sorted(actions_in(self.app_source) - OWN_ACTIONS), [])

    def test_the_marker_is_replaced(self):
        self.assertEqual(self.app_source.count(gen.MARKER), 1)
        self.assertNotIn(gen.MARKER, self.generated)
        self.assertIn(gen.POLICY_START, self.generated)

    def test_the_policy_sits_beside_the_other_task_role_policies(self):
        indents = {len(line) - len(line.lstrip()) for line in self.generated.splitlines()
                   if line.strip().startswith("- PolicyName:")}
        self.assertEqual(indents, {8})

    def test_it_says_it_is_generated(self):
        self.assertTrue(self.generated.startswith("# GENERATED"))

    def test_images_have_no_default_unless_given(self):
        self.assertNotIn("    Default: 'ghcr.io", self.generated)
        released = gen.build(self.app_source, _read(gen.ROLE_SOURCE), BACKEND, FRONTEND)
        self.assertIn(f"  BackendImage:\n    Type: String\n    Default: '{BACKEND}'\n", released)
        self.assertIn(f"  FrontendImage:\n    Type: String\n    Default: '{FRONTEND}'\n", released)

    def test_one_image_alone_is_refused(self):
        with self.assertRaises(gen.TemplateError):
            gen.build(self.app_source, _read(gen.ROLE_SOURCE), BACKEND, None)

    def test_a_missing_marker_is_refused(self):
        with self.assertRaises(gen.TemplateError):
            gen.build(self.app_source.replace(gen.MARKER, "# gone"), _read(gen.ROLE_SOURCE))


class ProjectTagTests(unittest.TestCase):
    def _check(self, text):
        found = resources(text)
        self.assertGreater(len(found), 3)
        untagged = sorted(name for name, (rtype, block) in found.items()
                          if rtype not in UNTAGGABLE
                          and not re.search(r"- Key: Project\n\s+Value: !Ref AppName", block))
        self.assertEqual(untagged, [], "add Project: !Ref AppName to these")

    def test_every_taggable_base_resource_is_tagged(self):
        self._check(_read(BASE_PATH))

    def test_every_taggable_app_resource_is_tagged(self):
        self._check(gen.build(_read(gen.APP_SOURCE), _read(gen.ROLE_SOURCE)))


if __name__ == "__main__":
    unittest.main()
