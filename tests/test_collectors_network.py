"""Collector tests for the network and edge: VPCs, subnets, route tables, gateways,
endpoints, Network Firewall, load balancers, target groups, Route 53, CloudFront, WAF."""

import unittest

from .fakes import (
    METRICS_DENIED,
    NO_METRICS,
    FakeClient,
    FakeSession,
    ancient,
    metrics_all_zero,
    metrics_at,
    audit,
    recent,
)
from aws_resource_audit import config
from .collectors_base import ACCOUNT, REGION, only


class NetworkCollectorTests(unittest.TestCase):

    def test_vpc_in_use_only_when_a_network_interface_is_in_it(self):
        session = FakeSession({"ec2": {"describe_vpcs": {"Vpcs": [
            {"VpcId": "vpc-live", "CidrBlock": "10.0.0.0/16", "IsDefault": False},
            {"VpcId": "vpc-empty", "CidrBlock": "10.1.0.0/16", "IsDefault": False},
            {"VpcId": "vpc-default", "CidrBlock": "172.31.0.0/16", "IsDefault": True},
        ]}}})
        usage = audit.new_ec2_usage_registry()
        usage["vpc_ids"].add("vpc-live")
        flags = {r["resource_id"]: r["flag"] for r in audit.collect_vpcs(session, REGION, usage=usage)}
        self.assertIn("ACTIVE", flags["vpc-live"])
        self.assertIn("STALE", flags["vpc-empty"])
        self.assertIn("default VPC", flags["vpc-default"])

    def test_vpc_state_is_unknown_when_the_network_interfaces_were_not_read(self):
        session = FakeSession({"ec2": {"describe_vpcs": {"Vpcs": [{"VpcId": "vpc-1"}]}}})
        usage = audit.new_ec2_usage_registry()
        usage["complete"] = False
        self.assertEqual(only(audit.collect_vpcs(session, REGION, usage=usage))["flag"], "UNKNOWN")

    def test_subnet_is_placed_in_its_vpc(self):
        session = FakeSession({"ec2": {"describe_subnets": {"Subnets": [{
            "SubnetId": "subnet-a", "VpcId": "vpc-1", "CidrBlock": "10.0.1.0/24",
            "AvailabilityZone": "us-east-1a", "Tags": [{"Key": "Name", "Value": "private-a"}],
        }]}}})
        row = only(audit.collect_subnets(session, REGION, usage=audit.new_ec2_usage_registry()))
        self.assertEqual((row["service"], row["name"], row["billing"]), ("Subnet", "private-a", "free"))
        self.assertIn("STALE", row["flag"])
        self.assertIn("us-east-1a", row["description"])
        self.assertEqual([e["target_id"] for e in row["_edges"]], ["vpc-1"])

    def test_route_table_links_its_subnets_and_internet_gateway(self):
        session = FakeSession({"ec2": {"describe_route_tables": {"RouteTables": [
            {"RouteTableId": "rtb-public", "VpcId": "vpc-1",
             "Associations": [{"SubnetId": "subnet-a"}],
             "Routes": [{"GatewayId": "igw-1"}, {"GatewayId": "local"}]},
            {"RouteTableId": "rtb-orphan", "VpcId": "vpc-1", "Associations": [], "Routes": []},
        ]}}})
        rows = {r["resource_id"]: r for r in audit.collect_route_tables(session, REGION)}
        self.assertIn("ACTIVE", rows["rtb-public"]["flag"])
        self.assertIn("STALE", rows["rtb-orphan"]["flag"])
        self.assertEqual(sorted(e["target_id"] for e in rows["rtb-public"]["_edges"]),
                         ["igw-1", "subnet-a", "vpc-1"])

    def test_detached_internet_gateway_is_stale(self):
        session = FakeSession({"ec2": {"describe_internet_gateways": {"InternetGateways": [
            {"InternetGatewayId": "igw-on", "Attachments": [{"VpcId": "vpc-1", "State": "available"}]},
            {"InternetGatewayId": "igw-off", "Attachments": []},
        ]}}})
        flags = {r["resource_id"]: r["flag"] for r in audit.collect_internet_gateways(session, REGION)}
        self.assertIn("ACTIVE", flags["igw-on"])
        self.assertIn("STALE", flags["igw-off"])

    def _nat_session(self, cw, **nat):
        payload = {"NatGatewayId": "nat-0a", "State": "available", "VpcId": "vpc-1",
                   "SubnetId": "subnet-a", "CreateTime": ancient(), "ConnectivityType": "public",
                   "NatGatewayAddresses": [{"AllocationId": "eipalloc-1"}]}
        payload.update(nat)
        return FakeSession({"ec2": {"describe_nat_gateways": {"NatGateways": [payload]}},
                            "cloudwatch": cw})

    def test_nat_gateway_that_moved_no_traffic_is_billed_and_idle(self):
        row = only(audit.collect_nat_gateways(
            self._nat_session(metrics_all_zero(recent())), REGION))
        self.assertEqual((row["service"], row["billing"]), ("NatGateway", "cost"))
        self.assertNotIn("ACTIVE", row["flag"])
        self.assertIn("$32/month", row["notes"])

    def test_nat_gateway_with_traffic_is_active(self):
        row = only(audit.collect_nat_gateways(self._nat_session(metrics_at(recent())), REGION))
        self.assertIn("ACTIVE", row["flag"])

    def test_a_transit_gateway_counts_only_live_attachments(self):
        session = FakeSession({"ec2": {
            "describe_transit_gateways": {"TransitGateways": [
                {"TransitGatewayId": "tgw-1", "State": "available", "CreationTime": ancient(),
                 "Tags": [{"Key": "Name", "Value": "hub"}]},
                {"TransitGatewayId": "tgw-old", "State": "deleted"}]},
            "describe_transit_gateway_attachments": {"TransitGatewayAttachments": [
                {"TransitGatewayId": "tgw-1", "ResourceType": "vpc", "ResourceId": "vpc-a", "State": "available"},
                {"TransitGatewayId": "tgw-1", "ResourceType": "vpn", "ResourceId": "vpn-1", "State": "available"},
                {"TransitGatewayId": "tgw-1", "ResourceType": "vpc", "ResourceId": "vpc-b", "State": "deleted"}]},
        }, "cloudwatch": metrics_all_zero(recent())})
        row = only(audit.collect_transit_gateways(session, REGION))
        self.assertEqual((row["service"], row["name"], row["billing"]), ("TransitGateway", "hub", "cost"))
        self.assertIn("2 attachment(s)", row["description"])
        self.assertNotIn("ACTIVE", row["flag"])

    def test_a_vpn_connection_reports_its_tunnels(self):
        session = FakeSession({"ec2": {"describe_vpn_connections": {"VpnConnections": [
            {"VpnConnectionId": "vpn-1", "State": "available", "CustomerGatewayId": "cgw-1",
             "VgwTelemetry": [{"Status": "UP"}, {"Status": "DOWN"}]},
            {"VpnConnectionId": "vpn-gone", "State": "deleted"}]}},
            "cloudwatch": metrics_at(recent())})
        row = only(audit.collect_vpn_connections(session, REGION))
        self.assertEqual((row["service"], row["billing"]), ("VPNConnection", "cost"))
        self.assertIn("1 tunnel(s) up", row["description"])
        self.assertIn("ACTIVE", row["flag"])

    def test_a_resolver_endpoint_reads_the_query_volume_for_its_direction(self):
        session = FakeSession({"route53resolver": {
            "list_resolver_endpoints": {"ResolverEndpoints": [{
                "Id": "rslvr-in-1", "Name": "onprem-in", "Direction": "INBOUND", "IpAddressCount": 2,
                "HostVPCId": "vpc-1", "Status": "OPERATIONAL", "CreationTime": "2024-01-02T03:04:05.678Z",
                "Arn": "arn:aws:route53resolver:us-east-1:111122223333:resolver-endpoint/rslvr-in-1"}]},
            "list_resolver_endpoint_ip_addresses": {"IpAddresses": [{"SubnetId": "subnet-a"}, {"SubnetId": "subnet-b"}]},
            "list_tags_for_resource": {"Tags": []},
        }, "cloudwatch": metrics_all_zero(recent())})
        row = only(audit.collect_resolver_endpoints(session, REGION))
        self.assertEqual((row["service"], row["name"], row["billing"], row["_vpc"]),
                         ("Route53ResolverEndpoint", "onprem-in", "cost", "vpc-1"))
        self.assertIn("2 here", row["notes"])
        self.assertNotIn("ACTIVE", row["flag"])
        (_op, kwargs), = session.get("cloudwatch").calls
        self.assertEqual(kwargs["MetricName"], "InboundQueryVolume")

    def test_a_deleted_nat_gateway_is_not_reported(self):
        self.assertEqual(audit.collect_nat_gateways(
            self._nat_session(NO_METRICS, State="deleted"), REGION), [])

    @staticmethod
    def _firewall_session(cw):
        return FakeSession({"network-firewall": {
            "list_firewalls": {"Firewalls": [{"FirewallName": "fw", "FirewallArn": "arn:fw"}]},
            "describe_firewall": {
                "Firewall": {"FirewallName": "fw", "VpcId": "vpc-1",
                             "SubnetMappings": [{"SubnetId": "subnet-a"}, {"SubnetId": "subnet-b"}]},
                "FirewallStatus": {"Status": "READY",
                                   "SyncStates": {"us-east-1a": {}, "us-east-1b": {}}}},
        }, "cloudwatch": cw})

    def test_network_firewall_with_traffic_is_active(self):
        row = only(audit.collect_network_firewalls(self._firewall_session(metrics_at(recent())), REGION))
        self.assertEqual((row["service"], row["billing"], row["_vpc"]), ("NetworkFirewall", "cost", "vpc-1"))
        self.assertIn("ACTIVE", row["flag"])
        self.assertIn("2 endpoint(s)", row["description"])

    def test_network_firewall_that_passed_nothing_is_not_active(self):
        row = only(audit.collect_network_firewalls(
            self._firewall_session(metrics_all_zero(recent())), REGION))
        self.assertNotIn("ACTIVE", row["flag"])
        self.assertIn("$290/month", row["notes"])

    def _endpoint_session(self, cw, **endpoint):
        payload = {"VpcEndpointId": "vpce-0a", "VpcEndpointType": "Interface", "State": "available",
                   "ServiceName": "com.amazonaws.us-east-1.secretsmanager", "VpcId": "vpc-1",
                   "SubnetIds": ["subnet-a", "subnet-b"], "CreationTimestamp": ancient()}
        payload.update(endpoint)
        return FakeSession({"ec2": {"describe_vpc_endpoints": {"VpcEndpoints": [payload]}},
                            "cloudwatch": cw})

    def test_idle_interface_endpoint_is_billed_per_zone(self):
        row = only(audit.collect_vpc_endpoints(
            self._endpoint_session(metrics_all_zero(recent())), REGION))
        self.assertEqual((row["service"], row["billing"]), ("VpcEndpoint", "cost"))
        self.assertNotIn("ACTIVE", row["flag"])
        self.assertIn("(2 here)", row["notes"])

    def test_gateway_endpoint_is_free_and_unmeasured(self):
        row = only(audit.collect_vpc_endpoints(self._endpoint_session(
            NO_METRICS, VpcEndpointType="Gateway", SubnetIds=[],
            ServiceName="com.amazonaws.us-east-1.s3", RouteTableIds=["rtb-1"]), REGION))
        self.assertIn("UNKNOWN", row["flag"])
        self.assertIn("free", row["notes"])

    def test_classic_load_balancer_is_scanned_and_routes_to_instances(self):
        session = FakeSession({
            "elb": {
                "describe_load_balancers": {"LoadBalancerDescriptions": [{
                    "LoadBalancerName": "legacy-web", "CreatedTime": ancient(),
                    "DNSName": "legacy-web-1.elb.amazonaws.com", "VPCId": "vpc-1",
                    "Instances": [{"InstanceId": "i-0abc"}], "Subnets": ["subnet-a"]}]},
                "describe_tags": {"TagDescriptions": [{"Tags": []}]},
            },
            "cloudwatch": metrics_all_zero(recent()),
        })
        row = only(audit.collect_classic_load_balancers(session, REGION))
        self.assertEqual((row["service"], row["name"], row["billing"]),
                         ("LoadBalancer", "classic", "cost"))
        self.assertNotIn("ACTIVE", row["flag"])
        self.assertEqual(sorted(e["target_id"] for e in row["_edges"]), ["i-0abc", "subnet-a"])

    def test_a_target_group_with_no_load_balancer_is_stale(self):
        session = FakeSession({"elbv2": {"describe_target_groups": {"TargetGroups": [
            {"TargetGroupName": "live", "TargetType": "instance",
             "LoadBalancerArns": [f"arn:aws:elasticloadbalancing:{REGION}:1:loadbalancer/app/web/abc"]},
            {"TargetGroupName": "orphan", "TargetType": "ip", "LoadBalancerArns": []},
        ]}}})
        rows = {r["resource_id"]: r for r in audit.collect_target_groups(session, REGION)}
        self.assertIn("ACTIVE", rows["live"]["flag"])
        self.assertIn("STALE", rows["orphan"]["flag"])
        self.assertEqual([e["target_id"] for e in rows["live"]["_edges"]], ["web"])

    def test_an_empty_hosted_zone_is_stale(self):
        session = FakeSession({"route53": {
            "list_hosted_zones": {"HostedZones": [
                {"Id": "/hostedzone/ZEMPTY", "Name": "old.example.", "ResourceRecordSetCount": 2},
                {"Id": "/hostedzone/ZLIVE", "Name": "live.example.", "ResourceRecordSetCount": 9},
            ]},
            "list_tags_for_resource": {"ResourceTagSet": {"Tags": []}},
            "list_resource_record_sets": {"ResourceRecordSets": []},
        }, "cloudwatch": metrics_at(recent())})
        rows = {r["resource_id"]: r for r in audit.collect_route53_hosted_zones(session)}
        self.assertEqual(rows["ZEMPTY"]["region"], "global")
        self.assertIn("no records", rows["ZEMPTY"]["flag"])
        self.assertIn("ACTIVE", rows["ZLIVE"]["flag"])

    def _zone(self, cloudwatch, **config):
        session = FakeSession({"route53": {
            "list_hosted_zones": {"HostedZones": [
                {"Id": "/hostedzone/Z1", "Name": "shop.example.",
                 "ResourceRecordSetCount": 9, "Config": config}]},
            "list_tags_for_resource": {"ResourceTagSet": {"Tags": []}},
            "list_resource_record_sets": {"ResourceRecordSets": []},
        }, "cloudwatch": cloudwatch})
        return only(audit.collect_route53_hosted_zones(session)), session

    def test_a_queried_hosted_zone_is_active_and_dated(self):
        row, session = self._zone(metrics_at(recent()))
        self.assertIn("ACTIVE", row["flag"])
        self.assertTrue(row["last_used"])
        call = session.get("cloudwatch").calls[0][1]
        self.assertEqual((call["Namespace"], call["MetricName"]),
                         ("AWS/Route53", "DNSQueries"))
        self.assertEqual(call["Dimensions"], [{"Name": "HostedZoneId", "Value": "Z1"}])

    def test_a_hosted_zone_nobody_queries_is_stale(self):
        """A zone with records whose domain expired: Route 53 answers no
        queries for it, so it publishes no DNSQueries datapoints at all."""
        row, _session = self._zone(NO_METRICS)
        self.assertTrue(row["flag"].startswith("STALE"), row["flag"])
        self.assertEqual(row["usage_state"], "unused")
        self.assertFalse(row["last_used"])

    def test_a_zero_query_count_is_never_read_as_use(self):
        row, _session = self._zone(metrics_all_zero(recent()))
        self.assertNotIn("ACTIVE", row["flag"])
        self.assertFalse(row["last_used"])

    def test_a_denied_metric_leaves_the_zone_unknown_not_stale(self):
        row, _session = self._zone(METRICS_DENIED)
        self.assertIn("UNKNOWN", row["flag"])

    def test_a_private_zone_has_no_query_count_and_none_is_asked_for(self):
        row, session = self._zone(NO_METRICS, PrivateZone=True)
        self.assertIn("UNKNOWN", row["flag"])
        self.assertIn("private zone", row["flag"])
        self.assertEqual(session.get("cloudwatch").calls, [])

    def test_a_disabled_cloudfront_distribution_is_stale(self):
        session = FakeSession({
            "cloudfront": {"list_distributions": {"DistributionList": {"Items": [{
                "Id": "E1", "ARN": "arn:aws:cloudfront::111122223333:distribution/E1",
                "DomainName": "d1.cloudfront.net", "Enabled": False, "Status": "Deployed",
                "LastModifiedTime": ancient(), "Aliases": {"Items": ["old.example.org"]},
                "Origins": {"Items": []}}]}},
                "list_tags_for_resource": {"Tags": {"Items": []}}},
            "cloudwatch": NO_METRICS,
        })
        row = only(audit.collect_cloudfront_distributions(session))
        self.assertEqual((row["region"], row["name"]), ("global", "old.example.org"))
        self.assertIn("disabled", row["flag"])

    def test_a_web_acl_protecting_nothing_is_stale(self):
        session = FakeSession({"wafv2": FakeClient("wafv2", {
            "list_web_acls": {"WebACLs": [{"Name": "old-acl", "Id": "1", "ARN": "arn:1"}]},
            "list_resources_for_web_acl": {"ResourceArns": []},
        }, not_pageable={"list_web_acls"})})
        row = only(audit.collect_waf_web_acls(session, REGION))
        self.assertEqual((row["service"], row["billing"]), ("WAFWebACL", "cost"))
        self.assertIn("protects nothing", row["flag"])

    def test_web_acls_page_with_next_marker(self):
        pages = [{"WebACLs": [{"Name": "a", "Id": "1", "ARN": "arn:a"}], "NextMarker": "m1"},
                 {"WebACLs": [{"Name": "b", "Id": "2", "ARN": "arn:b"}]}]
        session = FakeSession({"wafv2": FakeClient("wafv2", {
            "list_web_acls": lambda **kw: pages[1] if kw.get("NextMarker") == "m1" else pages[0],
            "list_resources_for_web_acl": {"ResourceArns": []},
        }, not_pageable={"list_web_acls"})})
        self.assertEqual([r["resource_id"] for r in audit.collect_waf_web_acls(session, REGION)],
                         ["a", "b"])


class LoadBalancerCollectorTests(unittest.TestCase):

    def test_load_balancer(self):
        arn = f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:loadbalancer/app/web-alb/abc123"
        session = FakeSession({
            "elbv2": {
                "describe_target_groups": {"TargetGroups": []},
                "describe_load_balancers": {"LoadBalancers": [{
                    "LoadBalancerArn": arn, "LoadBalancerName": "web-alb",
                    "Type": "application", "CreatedTime": recent(),
                    "State": {"Code": "active"}, "VpcId": "vpc-0123",
                    "SecurityGroups": ["sg-0123"], "DNSName": "web-alb.elb.amazonaws.com",
                }]},
                "describe_tags": {"TagDescriptions": [{"Tags": []}]},
            },
            "cloudwatch": metrics_at(recent()),
        })
        row = only(audit.collect_load_balancers(session, REGION))
        self.assertEqual(row["service"], "LoadBalancer")
        self.assertEqual(row["billing"], "cost", "an ALB bills hourly with no traffic")


if __name__ == "__main__":
    unittest.main()
