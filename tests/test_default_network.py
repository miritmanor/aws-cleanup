"""Hiding a default VPC nothing uses and nobody changed. Every test but the first is a
reason NOT to hide one."""

import unittest

from .fakes import FakeSession, make_row

import aws_resource_audit as audit
from aws_resource_audit.analyze.analyze import run_analysis
from aws_resource_audit.analyze.default_network import hide_untouched_default_networks

VPC = "vpc-0default"
REGION = "eu-west-1"


def default_network(region=REGION, vpc=VPC):
    """The rows AWS's own default VPC produces: VPC, subnets, table, gateway, group."""
    def piece(service, rid, default=True):
        return make_row(service, rid, region=region, vpc_id=vpc, aws_default=default)
    return [
        piece("VPC", vpc),
        piece("Subnet", f"subnet-a-{region}"),
        piece("Subnet", f"subnet-b-{region}"),
        piece("RouteTable", f"rtb-{region}"),
        # The API has no "default" marker for a gateway.
        piece("InternetGateway", f"igw-{region}", default=False),
        piece("SecurityGroup", f"sg-{region}"),
    ]


def hide(rows, coverage=None, known=True, vpcs=(VPC,)):
    return hide_untouched_default_networks(rows, set(vpcs), coverage, known=known)


class UntouchedDefaultNetworkTests(unittest.TestCase):
    def test_an_untouched_default_network_is_left_out(self):
        other = make_row("LambdaFunction", "fn")
        rows = default_network() + [other]
        self.assertEqual(hide(rows), [REGION])
        self.assertEqual(rows, [other])

    def test_each_region_is_decided_on_its_own(self):
        used = default_network("us-east-1", "vpc-used")
        eni = make_row("NetworkInterface", "eni-1", region="us-east-1", vpc_id="vpc-used")
        rows = default_network() + used + [eni]
        self.assertEqual(hide(rows, vpcs=(VPC, "vpc-used")), [REGION])
        self.assertEqual(len(rows), len(used) + 1)

    def test_a_network_interface_in_it_keeps_all_of_it(self):
        rows = default_network() + [
            make_row("NetworkInterface", "eni-1", region=REGION, vpc_id=VPC)]
        self.assertEqual(hide(rows), [])
        self.assertEqual(len(rows), 7)

    def test_a_subnet_someone_added_keeps_all_of_it(self):
        rows = default_network() + [
            make_row("Subnet", "subnet-mine", region=REGION, vpc_id=VPC)]
        self.assertEqual(hide(rows), [])

    def test_a_security_group_someone_added_keeps_all_of_it(self):
        rows = default_network() + [
            make_row("SecurityGroup", "sg-mine", region=REGION, vpc_id=VPC)]
        self.assertEqual(hide(rows), [])

    def test_something_pointing_into_it_keeps_all_of_it(self):
        """A function set to run in a default subnet holds no interface while
        idle, and still depends on the subnet."""
        fn = make_row("LambdaFunction", "fn", region=REGION)
        audit.add_edge(fn, f"subnet-a-{REGION}", "runs in subnet", "VpcConfig",
                       conn_type="eni.subnet.placement", target_service="Subnet")
        rows = default_network() + [fn]
        self.assertEqual(hide(rows), [])

    def test_a_failed_lookup_in_the_region_keeps_all_of_it(self):
        """"No interface was found" proves nothing if the lookup was denied."""
        denied = [{"service": "NetworkInterface", "capability": "inventory",
                   "status": "denied", "scope": REGION}]
        rows = default_network()
        self.assertEqual(hide(rows, coverage=denied), [])
        self.assertEqual(len(rows), 6)

    def test_a_failed_metric_lookup_is_not_a_reason_to_keep_it(self):
        metric = [{"service": "LambdaFunction", "capability": "metrics",
                   "status": "denied", "scope": REGION}]
        self.assertEqual(hide(default_network(), coverage=metric), [REGION])

    def test_nothing_is_hidden_when_the_default_vpcs_are_not_known(self):
        self.assertEqual(hide(default_network(), known=False), [])

    def test_a_vpc_someone_created_is_never_hidden(self):
        rows = default_network()
        self.assertEqual(hide(rows, vpcs=()), [])
        self.assertEqual(len(rows), 6)


class WiringTests(unittest.TestCase):
    def test_the_analysis_phase_drops_the_rows_and_reports_the_region(self):
        rows = default_network() + [make_row("LambdaFunction", "fn")]
        result = run_analysis(rows, default_vpc_ids={VPC})
        self.assertEqual(result.hidden_default_networks, [REGION])
        self.assertEqual([r["service"] for r in rows], ["LambdaFunction"])

    def test_the_collectors_mark_what_aws_created(self):
        session = FakeSession({"ec2": {
            "describe_vpcs": {"Vpcs": [
                {"VpcId": "vpc-d", "IsDefault": True},
                {"VpcId": "vpc-m", "IsDefault": False}]},
            "describe_subnets": {"Subnets": [
                {"SubnetId": "subnet-d", "VpcId": "vpc-d", "DefaultForAz": True},
                {"SubnetId": "subnet-m", "VpcId": "vpc-d", "DefaultForAz": False}]},
            "describe_route_tables": {"RouteTables": [
                {"RouteTableId": "rtb-d", "VpcId": "vpc-d",
                 "Associations": [{"Main": True}]},
                {"RouteTableId": "rtb-m", "VpcId": "vpc-d", "Associations": []}]},
            "describe_security_groups": {"SecurityGroups": [
                {"GroupId": "sg-d", "GroupName": "default", "VpcId": "vpc-d"},
                {"GroupId": "sg-m", "GroupName": "web", "VpcId": "vpc-d"}]},
        }})
        rows = (audit.collect_vpcs(session, REGION)
                + audit.collect_subnets(session, REGION)
                + audit.collect_route_tables(session, REGION)
                + audit.collect_security_groups(session, REGION))
        marked = {r["resource_id"] for r in rows if r["_aws_default"]}
        self.assertEqual(marked, {"vpc-d", "subnet-d", "rtb-d", "sg-d"})

    def test_the_marker_never_reaches_an_output_or_the_snapshot(self):
        self.assertNotIn("_aws_default", audit.ROW_FIELDS)
        self.assertNotIn("_aws_default", audit.SNAPSHOT_ROW_FIELDS)


if __name__ == "__main__":
    unittest.main()
