"""Connection types for the network: interfaces, VPC placement, load balancer
routing, CloudFront origins, Route 53 records, WAF."""

import unittest

from .fakes import NO_METRICS, FakeClient, FakeSession, make_row, audit, recent
from .connections_base import (
    ACCOUNT,
    ConnectionTypeTestCase,
    REGION,
    ec2_session,
    function_payload,
    instance_payload,
    lambda_session,
    quiet,
)


class TransitConnectionTests(ConnectionTypeTestCase):

    def test_transitgateway_vpc_attachment(self):
        def build():
            session = FakeSession({"ec2": {
                "describe_transit_gateways": {"TransitGateways": [{"TransitGatewayId": "tgw-1", "State": "available"}]},
                "describe_transit_gateway_attachments": {"TransitGatewayAttachments": [
                    {"TransitGatewayId": "tgw-1", "ResourceType": "vpc", "ResourceId": "vpc-0123",
                     "State": "available"}]},
            }, "cloudwatch": NO_METRICS})
            return audit.collect_transit_gateways(session, REGION) + [make_row("VPC", "vpc-0123")]
        self.assert_three_states("transitgateway.vpc.attachment", build, "tgw-1", "vpc-0123")

    def test_vpnconnection_transitgateway_attachment(self):
        def build():
            session = FakeSession({"ec2": {"describe_vpn_connections": {"VpnConnections": [
                {"VpnConnectionId": "vpn-1", "State": "available", "TransitGatewayId": "tgw-1"}]}},
                "cloudwatch": NO_METRICS})
            return audit.collect_vpn_connections(session, REGION) + [make_row("TransitGateway", "tgw-1")]
        self.assert_three_states("vpnconnection.transitgateway.attachment", build, "vpn-1", "tgw-1")


class ResolverEndpointConnectionTests(ConnectionTypeTestCase):

    @staticmethod
    def _endpoint(subnets=(), groups=()):
        session = FakeSession({"route53resolver": {
            "list_resolver_endpoints": {"ResolverEndpoints": [{
                "Id": "rslvr-out-1", "Direction": "OUTBOUND", "SecurityGroupIds": list(groups)}]},
            "list_resolver_endpoint_ip_addresses": {"IpAddresses": [{"SubnetId": s} for s in subnets]},
            "list_tags_for_resource": {"Tags": []},
        }, "cloudwatch": NO_METRICS})
        return audit.collect_resolver_endpoints(session, REGION)

    def test_resolverendpoint_subnet_placement(self):
        self.assert_three_states("resolverendpoint.subnet.placement",
                                 lambda: self._endpoint(subnets=["subnet-0123"]) + [make_row("Subnet", "subnet-0123")],
                                 "rslvr-out-1", "subnet-0123")

    def test_resolverendpoint_securitygroup_membership(self):
        self.assert_three_states("resolverendpoint.securitygroup.membership",
                                 lambda: self._endpoint(groups=["sg-0123"]) + [make_row("SecurityGroup", "sg-0123")],
                                 "rslvr-out-1", "sg-0123")


class NetworkInterfaceConnectionTests(ConnectionTypeTestCase):

    @staticmethod
    def _session(**eni_overrides):
        eni = {
            "NetworkInterfaceId": "eni-0abc", "Status": "in-use",
            "PrivateIpAddress": "10.0.0.5", "VpcId": "vpc-0123", "SubnetId": "subnet-0123",
        }
        eni.update(eni_overrides)
        return FakeSession({"ec2": {"describe_network_interfaces": {"NetworkInterfaces": [eni]}}})

    def test_eni_ec2_attachment(self):
        def build():
            rows = audit.collect_network_interfaces(
                self._session(Attachment={"InstanceId": "i-0abc"}), REGION)
            return rows + [make_row("EC2Instance", "i-0abc")]
        self.assert_three_states("eni.ec2.attachment", build, "eni-0abc", "i-0abc")

    def test_eni_elasticip_association(self):
        def build():
            rows = audit.collect_network_interfaces(
                self._session(Association={"AllocationId": "eipalloc-0abc",
                                           "PublicIp": "203.0.113.5"}), REGION)
            return rows + [make_row("ElasticIP", "eipalloc-0abc")]
        self.assert_three_states("eni.elasticip.association", build, "eni-0abc", "eipalloc-0abc")

    def test_eni_securitygroup_membership(self):
        def build():
            rows = audit.collect_network_interfaces(
                self._session(Groups=[{"GroupId": "sg-0123"}]), REGION)
            return rows + [make_row("SecurityGroup", "sg-0123")]
        self.assert_three_states("eni.securitygroup.membership", build, "eni-0abc", "sg-0123")


class VpcNetworkConnectionTests(ConnectionTypeTestCase):
    """Where things sit in the network. Supporting, never ownership: a shared
    default VPC must not merge the projects placed in it."""

    def test_subnet_vpc_placement(self):
        def build():
            session = FakeSession({"ec2": {"describe_subnets": {"Subnets": [
                {"SubnetId": "subnet-0a", "VpcId": "vpc-0123"}]}}})
            return audit.collect_subnets(session, REGION) + [make_row("VPC", "vpc-0123")]
        self.assert_three_states("subnet.vpc.placement", build, "subnet-0a", "vpc-0123")

    def _route_tables(self, **table):
        base = {"RouteTableId": "rtb-0a", "VpcId": "vpc-0123", "Associations": [], "Routes": []}
        base.update(table)
        session = FakeSession({"ec2": {"describe_route_tables": {"RouteTables": [base]}}})
        return audit.collect_route_tables(session, REGION)

    def test_routetable_vpc_placement(self):
        self.assert_three_states(
            "routetable.vpc.placement",
            lambda: self._route_tables() + [make_row("VPC", "vpc-0123")], "rtb-0a", "vpc-0123")

    def test_routetable_subnet_association(self):
        self.assert_three_states(
            "routetable.subnet.association",
            lambda: self._route_tables(Associations=[{"SubnetId": "subnet-0a"}])
            + [make_row("Subnet", "subnet-0a")], "rtb-0a", "subnet-0a")

    def test_routetable_internetgateway_route(self):
        self.assert_three_states(
            "routetable.internetgateway.route",
            lambda: self._route_tables(Routes=[{"GatewayId": "igw-0a"}])
            + [make_row("InternetGateway", "igw-0a")], "rtb-0a", "igw-0a")

    def test_internetgateway_vpc_attachment(self):
        def build():
            session = FakeSession({"ec2": {"describe_internet_gateways": {"InternetGateways": [
                {"InternetGatewayId": "igw-0a",
                 "Attachments": [{"VpcId": "vpc-0123", "State": "available"}]}]}}})
            return audit.collect_internet_gateways(session, REGION) + [make_row("VPC", "vpc-0123")]
        self.assert_three_states("internetgateway.vpc.attachment", build, "igw-0a", "vpc-0123")

    def test_ec2_subnet_placement(self):
        def build():
            rows = audit.collect_ec2_instances(ec2_session([instance_payload()]), REGION)
            return rows + [make_row("Subnet", "subnet-0123")]
        self.assert_three_states("ec2.subnet.placement", build, "i-0abc", "subnet-0123")

    def test_eni_subnet_placement(self):
        def build():
            rows = audit.collect_network_interfaces(
                NetworkInterfaceConnectionTests._session(), REGION)
            return rows + [make_row("Subnet", "subnet-0123")]
        self.assert_three_states("eni.subnet.placement", build, "eni-0abc", "subnet-0123")

    def test_lambda_subnet_placement(self):
        def build():
            fn = function_payload(VpcConfig={"VpcId": "vpc-0123", "SubnetIds": ["subnet-0123"]})
            rows = audit.collect_lambda_functions(lambda_session([fn]), REGION)
            return rows + [make_row("Subnet", "subnet-0123")]
        self.assert_three_states("lambda.subnet.placement", build, "orders-fn", "subnet-0123")

    def test_loadbalancer_subnet_placement(self):
        def build():
            session = FakeSession({
                "elbv2": {
                    "describe_load_balancers": {"LoadBalancers": [{
                        "LoadBalancerArn": f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:loadbalancer/app/web/abc",
                        "LoadBalancerName": "web", "Type": "application", "CreatedTime": recent(),
                        "VpcId": "vpc-0123", "AvailabilityZones": [{"SubnetId": "subnet-0123"}],
                    }]},
                    "describe_tags": {"TagDescriptions": [{"Tags": []}]},
                    "describe_target_groups": {"TargetGroups": []},
                },
                "cloudwatch": NO_METRICS,
            })
            return audit.collect_load_balancers(session, REGION) + [make_row("Subnet", "subnet-0123")]
        self.assert_three_states("loadbalancer.subnet.placement", build, "web", "subnet-0123")

    def test_rdsinstance_subnet_placement(self):
        def build():
            session = FakeSession({
                "rds": {
                    "describe_db_instances": {"DBInstances": [{
                        "DBInstanceIdentifier": "orders-db",
                        "DBInstanceArn": f"arn:aws:rds:{REGION}:{ACCOUNT}:db:orders-db",
                        "Engine": "postgres", "DBInstanceClass": "db.t3.micro",
                        "DBInstanceStatus": "available", "InstanceCreateTime": recent(),
                        "DBSubnetGroup": {"VpcId": "vpc-0123", "DBSubnetGroupName": "default",
                                          "Subnets": [{"SubnetIdentifier": "subnet-0123"}]},
                    }]},
                    "list_tags_for_resource": {"TagList": []},
                },
                "cloudwatch": NO_METRICS,
            })
            return audit.collect_rds_instances(session, REGION) + [make_row("Subnet", "subnet-0123")]
        self.assert_three_states("rdsinstance.subnet.placement", build, "orders-db", "subnet-0123")

    def _nat(self):
        session = FakeSession({"ec2": {"describe_nat_gateways": {"NatGateways": [{
            "NatGatewayId": "nat-0a", "State": "available", "SubnetId": "subnet-0123",
            "CreateTime": recent(), "NatGatewayAddresses": [{"AllocationId": "eipalloc-0a"}],
        }]}}, "cloudwatch": NO_METRICS})
        return audit.collect_nat_gateways(session, REGION)

    def test_networkfirewall_subnet_placement(self):
        def build():
            session = FakeSession({"network-firewall": {
                "list_firewalls": {"Firewalls": [{"FirewallName": "fw", "FirewallArn": "arn:fw"}]},
                "describe_firewall": {"Firewall": {"FirewallName": "fw",
                                                   "SubnetMappings": [{"SubnetId": "subnet-0123"}]},
                                      "FirewallStatus": {"SyncStates": {"us-east-1a": {}}}},
            }, "cloudwatch": NO_METRICS})
            return audit.collect_network_firewalls(session, REGION) + [make_row("Subnet", "subnet-0123")]
        self.assert_three_states("networkfirewall.subnet.placement", build, "fw", "subnet-0123")

    def test_natgateway_subnet_placement(self):
        self.assert_three_states("natgateway.subnet.placement",
                                 lambda: self._nat() + [make_row("Subnet", "subnet-0123")],
                                 "nat-0a", "subnet-0123")

    def test_natgateway_elasticip_allocation(self):
        self.assert_three_states("natgateway.elasticip.allocation",
                                 lambda: self._nat() + [make_row("ElasticIP", "eipalloc-0a")],
                                 "nat-0a", "eipalloc-0a")

    def test_routetable_natgateway_route(self):
        self.assert_three_states(
            "routetable.natgateway.route",
            lambda: self._route_tables(Routes=[{"NatGatewayId": "nat-0a"}])
            + [make_row("NatGateway", "nat-0a")], "rtb-0a", "nat-0a")

    def _endpoint(self, **endpoint):
        payload = {"VpcEndpointId": "vpce-0a", "VpcEndpointType": "Interface", "State": "available",
                   "ServiceName": "com.amazonaws.us-east-1.sqs", "VpcId": "vpc-0123",
                   "CreationTimestamp": recent()}
        payload.update(endpoint)
        session = FakeSession({"ec2": {"describe_vpc_endpoints": {"VpcEndpoints": [payload]}},
                               "cloudwatch": NO_METRICS})
        return audit.collect_vpc_endpoints(session, REGION)

    def test_vpcendpoint_vpc_placement(self):
        self.assert_three_states("vpcendpoint.vpc.placement",
                                 lambda: self._endpoint() + [make_row("VPC", "vpc-0123")],
                                 "vpce-0a", "vpc-0123")

    def test_vpcendpoint_subnet_placement(self):
        self.assert_three_states("vpcendpoint.subnet.placement",
                                 lambda: self._endpoint(SubnetIds=["subnet-0123"])
                                 + [make_row("Subnet", "subnet-0123")], "vpce-0a", "subnet-0123")

    def test_vpcendpoint_securitygroup_membership(self):
        self.assert_three_states("vpcendpoint.securitygroup.membership",
                                 lambda: self._endpoint(Groups=[{"GroupId": "sg-0123"}])
                                 + [make_row("SecurityGroup", "sg-0123")], "vpce-0a", "sg-0123")

    def test_vpcendpoint_routetable_association(self):
        self.assert_three_states("vpcendpoint.routetable.association",
                                 lambda: self._endpoint(VpcEndpointType="Gateway",
                                                        RouteTableIds=["rtb-0a"])
                                 + [make_row("RouteTable", "rtb-0a")], "vpce-0a", "rtb-0a")

    def test_a_shared_subnet_does_not_merge_two_projects(self):
        rows = [make_row("EC2Instance", "i-a", tags={"Project": "alpha"}),
                make_row("EC2Instance", "i-b", tags={"Project": "beta"}),
                make_row("Subnet", "subnet-0123")]
        for row, rid in ((rows[0], "i-a"), (rows[1], "i-b")):
            audit.add_edge(row, "subnet-0123", "runs in subnet", "instance SubnetId",
                           conn_type="ec2.subnet.placement", target_service="Subnet")
        links = audit.resolve_edges(rows)
        quiet(audit.apply_project_grouping, rows, edge_links=links)
        self.assertNotEqual(rows[0]["project_id"], rows[1]["project_id"])
        self.assertEqual(rows[2]["membership"], "shared")


class LoadBalancerRoutingTests(ConnectionTypeTestCase):
    """Where the traffic actually goes."""

    def _build(self, target_type="instance", target_id="i-0abc", state="healthy"):
        def build():
            session = FakeSession({
                "elbv2": {
                    "describe_load_balancers": {"LoadBalancers": [{
                        "LoadBalancerArn": f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:loadbalancer/app/web/abc",
                        "LoadBalancerName": "web", "Type": "application",
                        "CreatedTime": recent(), "VpcId": "vpc-1",
                    }]},
                    "describe_tags": {"TagDescriptions": [{"Tags": []}]},
                    "describe_target_groups": {"TargetGroups": [{
                        "TargetGroupArn": f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:targetgroup/tg/1",
                        "TargetGroupName": "tg", "TargetType": target_type,
                    }]},
                    "describe_target_health": {"TargetHealthDescriptions": [{
                        "Target": {"Id": target_id},
                        "TargetHealth": {"State": state},
                    }]},
                },
                "cloudwatch": NO_METRICS,
            })
            rows = audit.collect_load_balancers(session, REGION)
            return rows + [make_row("EC2Instance", "i-0abc")]
        return build

    def test_loadbalancer_any_target(self):
        self.assert_three_states("loadbalancer.any.target", self._build(),
                                 "web", "i-0abc")

    def test_an_unhealthy_target_is_still_a_route(self):
        """Target health travels as evidence, never a verdict."""
        rows = self._build(state="unhealthy")()
        audit.resolve_edges(rows)
        lb = self._row(rows, "web")

        self.assertIn("routes to", audit.render_connections(lb))
        self.assertIn("unhealthy", audit.render_connections(lb))
        self.assertNotIn("abandoned", audit.render_connections(lb).lower())

    def test_an_ip_target_names_no_resource(self):
        """A raw address matches nothing this script inventories, and must
        not be matched against anything by accident."""
        rows = self._build(target_type="ip", target_id="10.0.1.5")()
        audit.resolve_edges(rows)
        self.assertNotIn("routes to", audit.render_connections(self._row(rows, "web")))


class CloudFrontOriginTests(ConnectionTypeTestCase):

    @staticmethod
    def _dist(origin):
        session = FakeSession({
            "cloudfront": {"list_distributions": {"DistributionList": {"Items": [{
                "Id": "E1", "DomainName": "d1.cloudfront.net", "Enabled": True,
                "Origins": {"Items": [{"DomainName": origin}]}}]}}},
            "cloudwatch": NO_METRICS,
        })
        return audit.collect_cloudfront_distributions(session)

    def test_cloudfront_s3bucket_origin(self):
        self.assert_three_states(
            "cloudfront.s3bucket.origin",
            lambda: self._dist("site-assets.s3.us-east-1.amazonaws.com")
            + [make_row("S3Bucket", "site-assets", region="eu-west-1")], "E1", "site-assets")

    def test_cloudfront_loadbalancer_origin(self):
        self.assert_three_states(
            "cloudfront.loadbalancer.origin",
            lambda: self._dist("web-alb-1234567890.us-east-1.elb.amazonaws.com")
            + [make_row("LoadBalancer", "web-alb")], "E1", "web-alb")

    def test_cloudfront_apigateway_origin(self):
        self.assert_three_states(
            "cloudfront.apigateway.origin",
            lambda: self._dist("abc123defg.execute-api.us-east-1.amazonaws.com")
            + [make_row("APIGatewayRestApi", "abc123defg")], "E1", "abc123defg")

    def test_an_unknown_origin_is_not_linked(self):
        self.assertEqual(self._dist("origin.example.com")[0]["_edges"], [])


class Route53RecordTests(ConnectionTypeTestCase):

    @staticmethod
    def _zone(*records):
        session = FakeSession({"route53": {
            "list_hosted_zones": {"HostedZones": [{"Id": "/hostedzone/Z1", "Name": "shop.example.",
                                                    "ResourceRecordSetCount": 5}]},
            "list_tags_for_resource": {"ResourceTagSet": {"Tags": []}},
            "list_resource_record_sets": {"ResourceRecordSets": list(records)},
        }, "cloudwatch": NO_METRICS})
        return audit.collect_route53_hosted_zones(session)

    def test_route53_cloudfront_alias(self):
        """Matched on the distribution's alias (its row name), so the generic
        three-state helper, which looks targets up by id, is spelled out here."""
        record = {"Name": "www.shop.example.", "Type": "A",
                  "AliasTarget": {"DNSName": "d111abcdef8.cloudfront.net."}}

        def rows():
            return self._zone(record) + [
                make_row("CloudFrontDistribution", "E1", "www.shop.example", region="global")]
        for state, expect in ((audit.CONN_ON, True), (audit.CONN_REPORT_ONLY, True),
                              (audit.CONN_OFF, False)):
            with self.subTest(state=state), self._isolated("route53.cloudfront.alias", state):
                built = rows()
                audit.resolve_edges(built)
                text = audit.render_connections(built[1])    # the distribution's side
                self.assertEqual("Route53HostedZone:Z1" in text, expect, text)

    def test_route53_loadbalancer_alias(self):
        record = {"Name": "api.shop.example.", "Type": "A", "AliasTarget": {
            "DNSName": "dualstack.web-alb-1234567890.us-east-1.elb.amazonaws.com."}}
        self.assert_three_states("route53.loadbalancer.alias",
                                 lambda: self._zone(record) + [make_row("LoadBalancer", "web-alb")],
                                 "Z1", "web-alb")

    def test_route53_apigatewaydomain_alias(self):
        record = {"Name": "api.shop.example.", "Type": "A", "AliasTarget": {
            "DNSName": "d-abc123.execute-api.us-east-1.amazonaws.com."}}
        self.assert_three_states(
            "route53.apigatewaydomain.alias",
            lambda: self._zone(record) + [make_row("APIGatewayDomainName", "api.shop.example")],
            "Z1", "api.shop.example")

    def _missing(self, rows):
        audit.resolve_edges(rows)
        return [r["target_service"] for r in rows[0]["_references"] if r["kind"] == "dangling"]

    def test_an_edge_optimized_api_domain_is_not_a_missing_distribution(self):
        """Its CloudFront distribution is AWS's own and is never in the scan."""
        record = {"Name": "api.shop.example.", "Type": "A",
                  "AliasTarget": {"DNSName": "d111abcdef8.cloudfront.net."}}
        rows = self._zone(record) + [make_row("APIGatewayDomainName", "api.shop.example")]
        self.assertEqual(self._missing(rows), [])
        self.assertTrue(any(r["kind"] == "resolved" for r in rows[0]["_references"]))

    def test_a_record_for_a_deleted_distribution_is_still_reported(self):
        record = {"Name": "old.shop.example.", "Type": "A",
                  "AliasTarget": {"DNSName": "d222abcdef8.cloudfront.net."}}
        self.assertEqual(self._missing(self._zone(record)), ["CloudFrontDistribution"])

    def test_a_cloudfront_alias_with_no_api_domain_is_not_reported_missing(self):
        record = {"Name": "www.shop.example.", "Type": "A",
                  "AliasTarget": {"DNSName": "d111abcdef8.cloudfront.net."}}
        rows = self._zone(record)
        audit.resolve_edges(rows)
        self.assertNotIn("APIGatewayDomainName", audit.render_connections(rows[0]))

    def test_a_cname_to_a_load_balancer_counts_too(self):
        record = {"Name": "old.shop.example.", "Type": "CNAME", "ResourceRecords": [
            {"Value": "legacy-9876543210.us-east-1.elb.amazonaws.com"}]}
        self.assertEqual([e["target_id"] for e in self._zone(record)[0]["_edges"]], ["legacy"])

    def test_route53_s3bucket_alias(self):
        record = {"Name": "static.shop.example.", "Type": "A", "AliasTarget": {
            "DNSName": "s3-website-us-east-1.amazonaws.com."}}
        self.assert_three_states(
            "route53.s3bucket.alias",
            lambda: self._zone(record) + [make_row("S3Bucket", "static.shop.example", region="eu-west-1")],
            "Z1", "static.shop.example")


class WafConnectionTests(ConnectionTypeTestCase):

    @staticmethod
    def _acl(kind, resource_arn):
        def resources(WebACLArn, ResourceType):
            return {"ResourceArns": [resource_arn] if ResourceType == kind else []}
        session = FakeSession({"wafv2": FakeClient("wafv2", {
            "list_web_acls": {"WebACLs": [{"Name": "front-acl", "Id": "1", "ARN": "arn:acl"}]},
            "list_resources_for_web_acl": resources,
        }, not_pageable={"list_web_acls"})})
        return audit.collect_waf_web_acls(session, REGION)

    def test_wafwebacl_loadbalancer_protection(self):
        arn = f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:loadbalancer/app/web/abc"
        self.assert_three_states("wafwebacl.loadbalancer.protection",
                                 lambda: self._acl("APPLICATION_LOAD_BALANCER", arn)
                                 + [make_row("LoadBalancer", "web")], "front-acl", "web")

    def test_wafwebacl_apigateway_protection(self):
        arn = f"arn:aws:apigateway:{REGION}::/restapis/abc123defg/stages/prod"
        self.assert_three_states("wafwebacl.apigateway.protection",
                                 lambda: self._acl("API_GATEWAY", arn)
                                 + [make_row("APIGatewayRestApi", "abc123defg")], "front-acl", "abc123defg")

    def test_wafwebacl_cognito_protection(self):
        arn = f"arn:aws:cognito-idp:{REGION}:{ACCOUNT}:userpool/{REGION}_pool1"
        self.assert_three_states("wafwebacl.cognito.protection",
                                 lambda: self._acl("COGNITO_USER_POOL", arn)
                                 + [make_row("CognitoUserPool", f"{REGION}_pool1")],
                                 "front-acl", f"{REGION}_pool1")

    def test_cloudfront_wafwebacl_protected_by(self):
        acl = f"arn:aws:wafv2:us-east-1:{ACCOUNT}:global/webacl/edge-acl/0123"

        def build():
            session = FakeSession({"cloudfront": {"list_distributions": {"DistributionList": {"Items": [{
                "Id": "E1", "DomainName": "d1.cloudfront.net", "Enabled": True, "WebACLId": acl,
                "Origins": {"Items": []}}]}}}, "cloudwatch": NO_METRICS})
            return (audit.collect_cloudfront_distributions(session)
                    + [make_row("WAFWebACL", "edge-acl", region="global")])
        self.assert_three_states("cloudfront.wafwebacl.protected-by", build, "E1", "edge-acl")


if __name__ == "__main__":
    unittest.main()
