"""The architecture graph: what the Architecture tab and the .mmd draw."""

import unittest

from .fakes import audit, make_row

from aws_resource_audit.analyze.architecture import architecture_graph


def key(row):
    return audit.member_key(row)


def graph(rows, *links):
    """A scan graph over `rows` with (source row, target row, conn_type) links."""
    return {
        "nodes": [{"id": key(r), "kind": "resource", "service": r["service"],
                   "label": r["name"] or r["resource_id"]} for r in rows],
        "edges": [{"source": key(s), "target": key(t), "state": "resolved",
                   "category": "link", "links": [{"conn_type": c}]} for s, t, c in links],
    }


def edges_of(arch):
    return {(e["source"].split(":")[-1], e["target"].split(":")[-1]): (e["verb"], e["style"])
            for e in arch["edges"]}


class NodeTests(unittest.TestCase):
    def test_deployment_resources_are_not_drawn(self):
        fn = make_row("LambdaFunction", "app")
        deploy = make_row("S3Bucket", "deployment-bucket")
        deploy["tier"] = audit.TIER_DEPLOYMENT
        arch = architecture_graph(graph([fn, deploy], (fn, deploy, "lambda.any.env-var-arn-value")),
                                  [fn, deploy])
        self.assertEqual([n["id"] for n in arch["nodes"]], [key(fn)])
        self.assertEqual(arch["edges"], [])

    def test_plumbing_and_attached_disks_are_not_drawn_but_sign_in_is(self):
        rows = [make_row("EBSVolume", "vol"), make_row("SecurityGroup", "sg"),
                make_row("IAMRole", "role"), make_row("CognitoUserPool", "pool"),
                make_row("EventBridgeSchedule", "nightly")]
        drawn = {n["service"] for n in architecture_graph(graph(rows), rows)["nodes"]}
        self.assertEqual(drawn, {"CognitoUserPool", "EventBridgeSchedule"})


class LineTests(unittest.TestCase):
    def test_a_runtime_link_is_drawn_with_its_verb(self):
        api, fn = make_row("APIGatewayRestApi", "api"), make_row("LambdaFunction", "fn")
        arch = architecture_graph(graph([api, fn], (api, fn, "apigateway.lambda.integration")),
                                  [api, fn])
        self.assertEqual(edges_of(arch), {("api", "fn"): ("invokes", "solid")})

    def test_belongs_together_links_are_not_drawn(self):
        app, fn = make_row("AmplifyApp", "app"), make_row("LambdaFunction", "fn")
        arch = architecture_graph(graph([app, fn], (app, fn, "amplify.any.cfn-stack-walk"),
                                        (app, fn, "group.shared-tag")), [app, fn])
        self.assertEqual(arch["edges"], [])

    def test_an_event_source_mapping_points_from_the_queue(self):
        fn, queue = make_row("LambdaFunction", "fn"), make_row("SQSQueue", "q")
        arch = architecture_graph(graph([fn, queue], (fn, queue, "lambda.any.event-source-mapping")),
                                  [fn, queue])
        self.assertEqual(edges_of(arch), {("q", "fn"): ("triggers", "solid")})

    def test_an_api_authorizes_with_its_user_pool(self):
        api, pool = make_row("APIGatewayV2Api", "http1"), make_row("CognitoUserPool", "pool")
        arch = architecture_graph(graph([api, pool], (api, pool, "apigatewayv2.cognito.authorizer")),
                                  [api, pool])
        self.assertEqual(edges_of(arch), {("http1", "pool"): ("authorizes with", "solid")})

    def test_a_vpn_connects_through_its_transit_gateway(self):
        vpn, tgw = make_row("VPNConnection", "vpn-1"), make_row("TransitGateway", "tgw-1")
        arch = architecture_graph(graph([vpn, tgw], (vpn, tgw, "vpnconnection.transitgateway.attachment")),
                                  [vpn, tgw])
        self.assertEqual(edges_of(arch), {("vpn-1", "tgw-1"): ("connects through", "solid")})

    def test_a_schedule_triggers_its_target(self):
        sched, fn = make_row("EventBridgeSchedule", "nightly"), make_row("LambdaFunction", "fn")
        arch = architecture_graph(graph([sched, fn], (sched, fn, "eventbridgeschedule.any.target")),
                                  [sched, fn])
        self.assertEqual(edges_of(arch), {("nightly", "fn"): ("triggers", "solid")})


class PassThroughTests(unittest.TestCase):
    def test_a_custom_domain_is_drawn_as_route53_to_the_api(self):
        zone, domain = make_row("Route53HostedZone", "Z1", region="global"), make_row("APIGatewayDomainName", "api.x")
        api = make_row("APIGatewayRestApi", "rest1")
        rows = [zone, domain, api]
        arch = architecture_graph(graph(rows, (zone, domain, "route53.apigatewaydomain.alias"),
                                        (domain, api, "apidomain.apigateway.mapping")), rows)
        self.assertEqual(edges_of(arch), {("Z1", "rest1"): ("resolves to", "solid")})
        self.assertNotIn(key(domain), [n["id"] for n in arch["nodes"]])


class GrantTests(unittest.TestCase):
    def setUp(self):
        self.fn = make_row("LambdaFunction", "fn")
        self.role = make_row("IAMRole", "exec")
        self.policy = make_row("IAMPolicy", "pol")
        self.table = make_row("DynamoDBTable", "orders")
        self.rows = [self.fn, self.role, self.policy, self.table]

    def test_a_role_grant_is_a_dotted_can_access(self):
        arch = architecture_graph(graph(self.rows,
                                        (self.fn, self.role, "lambda.iamrole.execution-role"),
                                        (self.role, self.table, "iamrole.any.resource-grant")),
                                  self.rows)
        self.assertEqual(edges_of(arch), {("fn", "orders"): ("can access", "dotted")})

    def test_a_grant_through_an_attached_policy_counts(self):
        arch = architecture_graph(graph(self.rows,
                                        (self.fn, self.role, "lambda.iamrole.execution-role"),
                                        (self.policy, self.role, "iampolicy.iamrole.attachment"),
                                        (self.policy, self.table, "iampolicy.any.resource-grant")),
                                  self.rows)
        self.assertEqual(edges_of(arch), {("fn", "orders"): ("can access", "dotted")})

    def test_a_cognito_sms_role_grants_nothing_to_the_pool(self):
        pool = make_row("CognitoUserPool", "pool")
        rows = self.rows + [pool]
        arch = architecture_graph(graph(rows,
                                        (pool, self.role, "cognito.iamrole.sms-caller"),
                                        (self.role, self.table, "iamrole.any.resource-grant")),
                                  rows)
        self.assertEqual(arch["edges"], [])

    def test_a_bare_name_in_an_env_var_is_a_dotted_uses(self):
        arch = architecture_graph(graph(self.rows,
                                        (self.fn, self.table, "lambda.any.env-var-bare-name")),
                                  self.rows)
        self.assertEqual(edges_of(arch), {("fn", "orders"): ("uses", "dotted")})

    def test_a_runtime_link_outranks_a_grant_between_the_same_pair(self):
        arch = architecture_graph(graph(self.rows,
                                        (self.fn, self.role, "lambda.iamrole.execution-role"),
                                        (self.role, self.table, "iamrole.any.resource-grant"),
                                        (self.fn, self.table, "lambda.any.env-var-arn-value")),
                                  self.rows)
        self.assertEqual(edges_of(arch), {("fn", "orders"): ("uses", "solid")})


class MermaidTests(unittest.TestCase):
    def test_nodes_are_icons_and_lines_carry_their_verb(self):
        api, fn = make_row("APIGatewayRestApi", "api"), make_row("LambdaFunction", "fn")
        role, table = make_row("IAMRole", "r"), make_row("DynamoDBTable", "t")
        rows = [api, fn, role, table]
        arch = architecture_graph(graph(rows, (api, fn, "apigateway.lambda.integration"),
                                        (fn, role, "lambda.iamrole.execution-role"),
                                        (role, table, "iamrole.any.resource-grant")), rows)
        text = audit.render_mermaid(arch, rows)
        self.assertIn('icon: "aws:lambda"', text)
        self.assertIn('icon: "aws:api-gateway"', text)
        self.assertIn("-->|invokes|", text)
        self.assertIn("-.->|can access|", text)


if __name__ == "__main__":
    unittest.main()
