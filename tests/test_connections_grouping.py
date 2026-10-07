"""Shared-attribute grouping signals, connection-state behaviour, and the guard
that every declared connection type has a test in a test_connections_* module."""

import pathlib
import unittest

from .fakes import FakeSession, make_row, audit
from .connections_base import (
    ConnectionTypeTestCase,
    REGION,
    ec2_session,
    function_payload,
    instance_payload,
    lambda_session,
    quiet,
)


class SharedInfrastructureTests(unittest.TestCase):
    """The shared-attribute guarantees that survive: a security group can adopt an
    unassigned resource but never merge two projects."""

    def _two_projects_using(self, service, shared_id, conn_type, region=REGION):
        a = make_row("LambdaFunction", "orders-fn", tags={"Project": "orders"})
        b = make_row("LambdaFunction", "billing-fn", tags={"Project": "billing"})
        shared = make_row(service, shared_id, region=region)
        for row in (a, b):
            audit.add_edge(row, shared_id, "uses", "test", conn_type=conn_type,
                           target_service=service)
        rows = [a, b, shared]
        links = audit.resolve_edges(rows)
        quiet(audit.apply_project_grouping, rows, edge_links=links)
        return a, b, shared

    def test_a_shared_security_group_does_not_merge_two_projects(self):
        a, b, sg = self._two_projects_using(
            "SecurityGroup", "sg-shared", "ec2.securitygroup.membership")
        self.assertNotEqual(a["project_id"], b["project_id"])
        self.assertEqual(sg["membership"], "shared")

    def test_a_shared_key_pair_does_not_merge_two_projects(self):
        a, b, kp = self._two_projects_using(
            "KeyPair", "deploy-key", "ec2.keypair.key-name")
        self.assertNotEqual(a["project_id"], b["project_id"])
        self.assertEqual(kp["membership"], "shared")

    def test_plumbing_used_by_one_project_joins_it(self):
        """The useful half. Held back under the old hub rule only because
        the rule could not tell one consumer from two."""
        a = make_row("LambdaFunction", "orders-fn", tags={"Project": "orders"})
        sg = make_row("SecurityGroup", "sg-orders")
        audit.add_edge(a, "sg-orders", "uses", "test",
                       conn_type="ec2.securitygroup.membership",
                       target_service="SecurityGroup")
        rows = [a, sg]
        quiet(audit.apply_project_grouping, rows,
              edge_links=audit.resolve_edges(rows))
        self.assertEqual(sg["project_id"], a["project_id"])

    def test_an_ami_owns_its_snapshots(self):
        """AMI was deliberately never a "hub": its edges point at the
        snapshots that back it, which is containment."""
        ami = make_row("AMI", "ami-1", tags={"Project": "imaging"})
        snap = make_row("EBSSnapshot", "snap-1")
        audit.add_edge(ami, "snap-1", "backed by", "block device",
                       conn_type="ami.ebssnapshot.block-device",
                       target_service="EBSSnapshot")
        rows = [ami, snap]
        quiet(audit.apply_project_grouping, rows,
              edge_links=audit.resolve_edges(rows))
        self.assertEqual(snap["project_id"], ami["project_id"])

    def test_group_shared_tag(self):
        """"group.shared-tag": a shared Project tag is the one shared-attribute signal that groups."""
        rows = [make_row("LambdaFunction", "a", tags={"Project": "orders"}),
                make_row("DynamoDBTable", "b", tags={"Project": "orders"})]
        quiet(audit.apply_project_grouping, rows)
        self.assertEqual(rows[0]["project_id"], rows[1]["project_id"])


class ConfigurationBehaviourTests(ConnectionTypeTestCase):

    def test_every_edge_type_has_a_test_in_a_connections_module(self):
        """Coverage guard: adding a connection type without a test fails here
        rather than silently shipping untested detection."""
        here = pathlib.Path(__file__).parent
        source = "".join(p.read_text() for p in here.glob("test_connections_*.py"))
        missing = [ct.id for ct in audit.CONNECTION_TYPES if ct.id not in source]
        self.assertEqual(missing, [], f"connection types with no test: {missing}")

    def test_disabling_a_type_removes_its_cost_entirely(self):
        """'off' must skip edge creation, not merely filter it out later."""
        with self._isolated("ec2.ami.image-id", audit.CONN_OFF):
            rows = audit.collect_ec2_instances(ec2_session([instance_payload()]), REGION)
            self.assertEqual(rows[0]["_edges"], [])

    def test_report_only_edges_still_count_as_connections_for_risk(self):
        """A report-only link is real - the resource IS referenced - so it must
        not make a live resource look like an unreferenced cleanup candidate."""
        with self._isolated("lambda.any.env-var-bare-name", audit.CONN_REPORT_ONLY):
            fn = function_payload(Environment={"Variables": {"TABLE_NAME": "orders"}})
            rows = audit.collect_lambda_functions(lambda_session([fn]), REGION)
            rows.append(make_row("DynamoDBTable", "orders", flag="ACTIVE"))
            audit.resolve_edges(rows)
            table = self._row(rows, "orders")
            self.assertTrue(table["risk_if_removed"].startswith("HIGH"))

    def test_confidence_display_matches_the_registry(self):
        with self._isolated("amplify.apigateway.name-match", audit.CONN_ON):
            rows = [
                make_row("AmplifyApp", "d1", name="ordersweb"),
                make_row("APIGatewayRestApi", "rest1", name="ordersweb-api"),
            ]
            audit.apply_amplify_api_links(rows, FakeSession({
                "amplify": {
                    "list_backend_environments": {"backendEnvironments": []},
                    # No environment variables, so the name match is still the
                    # only thing that can reach this API - which is the point.
                    "get_app": {"app": {"environmentVariables": {}}},
                    "list_branches": {"branches": []},
                },
                "cloudformation": {},
            }))
            audit.resolve_edges(rows)
            self.assertIn("LOW CONFIDENCE", audit.render_connections(rows[0]))


if __name__ == "__main__":
    unittest.main()
