"""Collector tests for EC2: instances, volumes, Elastic IPs, launch templates, spot,
reserved instances, AMIs, snapshots, security groups, key pairs, network interfaces."""

import unittest

from .fakes import NO_METRICS, FakeSession, ancient, aws_ts, metrics_at, audit, recent
from .collectors_base import REGION, only


class Ec2CollectorTests(unittest.TestCase):

    def test_ec2_instance(self):
        session = FakeSession({
            "ec2": {"describe_instances": {"Reservations": [{"Instances": [{
                "InstanceId": "i-0abc", "ImageId": "ami-0123", "InstanceType": "t3.micro",
                "State": {"Name": "running"}, "LaunchTime": recent(),
                "VpcId": "vpc-0123", "SubnetId": "subnet-0123",
                "SecurityGroups": [{"GroupId": "sg-0123", "GroupName": "web"}],
                "Tags": [{"Key": "Name", "Value": "web-server"}],
            }]}]}},
            "cloudwatch": metrics_at(recent()),
        })
        row = only(audit.collect_ec2_instances(session, REGION))
        self.assertEqual(row["service"], "EC2Instance")
        self.assertEqual(row["resource_id"], "i-0abc")
        self.assertEqual(row["name"], "web-server")
        self.assertEqual(row["flag"], "ACTIVE")
        self.assertFalse(row["inferred"], "a real CloudWatch datapoint is not an inference")
        self.assertIn("vpc-0123", audit.render_connections(row))
        self.assertEqual(row["billing"], "usage")

    def test_an_instance_without_metrics_is_unknown_not_stale(self):
        """A stopped instance publishes no CPU metric, so an empty series is UNKNOWN,
        not STALE by age."""
        session = FakeSession({
            "ec2": {"describe_instances": {"Reservations": [{"Instances": [{
                "InstanceId": "i-old", "State": {"Name": "stopped"}, "LaunchTime": ancient(),
            }]}]}},
            "cloudwatch": NO_METRICS,
        })
        row = only(audit.collect_ec2_instances(session, REGION))
        self.assertTrue(row["inferred"])
        self.assertIn("UNKNOWN", row["flag"])
        self.assertNotIn("STALE", row["flag"])

    def test_ebs_volume_unattached(self):
        session = FakeSession({
            "ec2": {"describe_volumes": {"Volumes": [{
                "VolumeId": "vol-0abc", "State": "available", "Size": 100,
                "VolumeType": "gp3", "CreateTime": ancient(), "Attachments": [],
            }]}},
            "cloudwatch": NO_METRICS,
        })
        row = only(audit.collect_ebs_volumes(session, REGION))
        self.assertEqual(row["service"], "EBSVolume")
        self.assertEqual(row["flag"], "STALE (UNATTACHED)")
        self.assertEqual(row["billing"], "cost", "an unattached volume is still billed")
        # Risk is filled by resolve_edges, not at construction - before that
        # pass "has connections" cannot mean anything, so the column is empty.
        audit.resolve_edges([row])
        self.assertTrue(row["risk_if_removed"].startswith("LOW"))

    def test_ebs_volume_attached_to_a_stopped_instance_is_flagged_idle_not_unattached(self):
        """'in-use' only means attached. Attached to a stopped instance means
        billed while nothing reads it - a distinct, and common, finding."""
        session = FakeSession({
            "ec2": {
                "describe_volumes": {"Volumes": [{
                    "VolumeId": "vol-0abc", "State": "in-use", "Size": 100,
                    "VolumeType": "gp3", "CreateTime": recent(),
                    "Attachments": [{"InstanceId": "i-stopped", "Device": "/dev/xvda",
                                     "State": "attached"}],
                }]},
                "describe_instances": {"Reservations": [{"Instances": [{
                    "InstanceId": "i-stopped", "State": {"Name": "stopped"},
                    "LaunchTime": recent(),
                }]}]},
            },
            "cloudwatch": NO_METRICS,
        })
        row = only(audit.collect_ebs_volumes(session, REGION))
        self.assertEqual(row["flag"], "STALE (ATTACHED TO STOPPED INSTANCE)")
        audit.resolve_edges([row])
        self.assertIn("attached", row["risk_if_removed"].lower())
        self.assertNotIn("unattached", row["risk_if_removed"].lower())

    def test_elastic_ip(self):
        session = FakeSession({"ec2": {"describe_addresses": {"Addresses": [{
            "AllocationId": "eipalloc-0abc", "PublicIp": "203.0.113.5", "Domain": "vpc",
        }]}}})
        row = only(audit.collect_elastic_ips(session, REGION))
        self.assertEqual(row["service"], "ElasticIP")
        self.assertEqual(row["resource_id"], "eipalloc-0abc")
        self.assertIn("UNASSOCIATED", row["flag"])
        self.assertEqual(row["billing"], "cost")

    def test_launch_template(self):
        session = FakeSession({"ec2": {
            "describe_launch_templates": {"LaunchTemplates": [{
                "LaunchTemplateId": "lt-0abc", "LaunchTemplateName": "web-lt",
                "DefaultVersionNumber": 2, "LatestVersionNumber": 2, "CreateTime": recent(),
            }]},
            "describe_launch_template_versions": {"LaunchTemplateVersions": [
                {"LaunchTemplateData": {"ImageId": "ami-0123"}}]},
        }})
        row = only(audit.collect_launch_templates(session, REGION))
        self.assertEqual(row["service"], "LaunchTemplate")
        self.assertEqual(row["resource_id"], "lt-0abc")
        self.assertEqual(row["billing"], "free")
        self.assertIn("Auto Scaling", row["notes"])

    def test_a_launch_template_an_auto_scaling_group_uses_is_active(self):
        usage = audit.new_ec2_usage_registry()
        asg = FakeSession({"autoscaling": {"describe_auto_scaling_groups": {"AutoScalingGroups": [{
            "AutoScalingGroupName": "workers", "DesiredCapacity": 0, "MinSize": 0, "MaxSize": 4,
            "CreatedTime": ancient(), "LaunchTemplate": {"LaunchTemplateId": "lt-used"},
            "Instances": []}]}}})
        group = only(audit.collect_auto_scaling_groups(asg, REGION, usage=usage))
        self.assertIn("scaled to zero", group["flag"])
        self.assertEqual(group["billing"], "indirect")
        templates = FakeSession({"ec2": {
            "describe_launch_templates": {"LaunchTemplates": [
                {"LaunchTemplateId": "lt-used", "LaunchTemplateName": "a", "CreateTime": ancient(),
                 "DefaultVersionNumber": 1},
                {"LaunchTemplateId": "lt-idle", "LaunchTemplateName": "b", "CreateTime": ancient(),
                 "DefaultVersionNumber": 1}]},
            "describe_launch_template_versions": {"LaunchTemplateVersions": []},
        }})
        flags = {r["resource_id"]: r["flag"]
                 for r in audit.collect_launch_templates(templates, REGION, usage=usage)}
        self.assertIn("Auto Scaling group", flags["lt-used"])
        self.assertIn("STALE", flags["lt-idle"])

    def test_spot_instance_request_closed_is_history_not_stale(self):
        """AWS ages closed requests out itself, so they aren't cleanup targets."""
        session = FakeSession({"ec2": {"describe_spot_instance_requests": {
            "SpotInstanceRequests": [{
                "SpotInstanceRequestId": "sir-0abc", "State": "closed",
                "CreateTime": ancient(), "Type": "one-time",
                "Status": {"Message": "instance terminated"},
            }]}}})
        row = only(audit.collect_spot_instance_requests(session, REGION))
        self.assertEqual(row["service"], "SpotInstanceRequest")
        self.assertIn("HISTORY", row["flag"])

    def test_reserved_instance_without_a_matching_running_instance(self):
        """The most billing-critical finding here: committed money with nothing
        using it."""
        session = FakeSession({"ec2": {"describe_reserved_instances": {"ReservedInstances": [{
            "ReservedInstancesId": "ri-0abc", "InstanceType": "m5.large", "State": "active",
            "Start": recent(400), "End": recent(-100), "InstanceCount": 2,
            "FixedPrice": 500.0, "UsagePrice": 0.0, "OfferingClass": "standard",
            "OfferingType": "All Upfront", "ProductDescription": "Linux/UNIX", "Scope": "Region",
        }]}}})
        usage = audit.new_ec2_usage_registry()  # nothing running
        row = only(audit.collect_reserved_instances(session, REGION, usage=usage))
        self.assertEqual(row["service"], "ReservedInstance")
        self.assertTrue(row["flag"].startswith("STALE"), row["flag"])
        self.assertIn("no matching running instance", row["flag"])
        self.assertEqual(row["usage_state"], "unused")
        self.assertIn("Billing console", row["notes"], "must not present the hint as proof")

    def test_expired_reserved_instance_is_stale(self):
        session = FakeSession({"ec2": {"describe_reserved_instances": {"ReservedInstances": [{
            "ReservedInstancesId": "ri-0old", "InstanceType": "m5.large", "State": "retired",
            "Start": recent(800), "End": recent(35), "InstanceCount": 1,
        }]}}})
        row = only(audit.collect_reserved_instances(session, REGION,
                                                    usage=audit.new_ec2_usage_registry()))
        self.assertEqual(row["flag"], "STALE (reservation expired: retired)")
        self.assertEqual(row["usage_state"], "unused")

    def test_reserved_instance_with_a_matching_running_instance(self):
        session = FakeSession({"ec2": {"describe_reserved_instances": {"ReservedInstances": [{
            "ReservedInstancesId": "ri-0abc", "InstanceType": "m5.large", "State": "active",
            "Start": recent(400), "End": recent(-100), "InstanceCount": 1,
            "FixedPrice": 0.0, "UsagePrice": 0.5,
        }]}}})
        usage = audit.new_ec2_usage_registry()
        usage["instance_types"].add(f"{REGION}:m5.large")
        row = only(audit.collect_reserved_instances(session, REGION, usage=usage))
        self.assertEqual(row["flag"], "ACTIVE")

    def test_ami(self):
        session = FakeSession({"ec2": {"describe_images": {"Images": [{
            "ImageId": "ami-0abc", "Name": "golden-image", "State": "available",
            "CreationDate": aws_ts(recent(30)), "LastLaunchedTime": aws_ts(recent(10)),
            "Architecture": "x86_64", "PlatformDetails": "Linux/UNIX",
            "Description": "hardened base image for the bakery stack",
            "BlockDeviceMappings": [{"Ebs": {"SnapshotId": "snap-0abc"}}],
        }]}}})
        row = only(audit.collect_amis(session, REGION))
        self.assertEqual(row["service"], "AMI")
        self.assertEqual(row["flag"], "ACTIVE")
        self.assertEqual(row["billing"], "indirect", "the AMI is free; its snapshots are not")
        self.assertIn("snapshot", row["notes"].lower())
        self.assertIn("hardened base image for the bakery stack", row["description"],
                      "the AMI's own Description field should be captured, not just Architecture/PlatformDetails")

    def test_ami_and_its_snapshot_agree_on_staleness(self):
        # Through the shared usage registry: a snapshot backing a stale AMI is not ACTIVE.
        session = FakeSession({"ec2": {
            "describe_images": {"Images": [{
                "ImageId": "ami-0stale", "Name": "forgotten-image", "State": "available",
                "CreationDate": aws_ts(ancient()),
                "BlockDeviceMappings": [{"Ebs": {"SnapshotId": "snap-0stale"}}],
            }]},
            "describe_snapshots": {"Snapshots": [{
                "SnapshotId": "snap-0stale", "State": "completed",
                "StartTime": ancient(), "VolumeSize": 50,
            }]},
        }})
        usage = audit.new_ec2_usage_registry()
        ami_row = only(audit.collect_amis(session, REGION, usage=usage))
        self.assertIn("STALE", ami_row["flag"])
        snap_row = only(audit.collect_ebs_snapshots(session, REGION, usage=usage))
        self.assertIn("STALE", snap_row["flag"])
        self.assertIn("unused AMI", snap_row["flag"])

    def test_ami_shared_publicly_is_called_out(self):
        session = FakeSession({"ec2": {"describe_images": {"Images": [{
            "ImageId": "ami-0abc", "Name": "oops", "State": "available",
            "CreationDate": aws_ts(recent(30)), "Public": True,
        }]}}})
        row = only(audit.collect_amis(session, REGION))
        self.assertIn("PUBLICLY", row["notes"])

    def test_ebs_snapshot(self):
        session = FakeSession({"ec2": {"describe_snapshots": {"Snapshots": [{
            "SnapshotId": "snap-0abc", "VolumeId": "vol-0abc", "State": "completed",
            "StartTime": ancient(), "VolumeSize": 50, "Description": "nightly",
        }]}}})
        row = only(audit.collect_ebs_snapshots(session, REGION))
        self.assertEqual(row["service"], "EBSSnapshot")
        self.assertEqual(row["billing"], "cost")
        self.assertIn("incremental", row["notes"],
                      "deleting one snapshot rarely frees its full size")

    def test_ebs_snapshot_backing_an_active_ami_is_active(self):
        session = FakeSession({"ec2": {"describe_snapshots": {"Snapshots": [{
            "SnapshotId": "snap-0abc", "State": "completed",
            "StartTime": ancient(), "VolumeSize": 50,
        }]}}})
        usage = audit.new_ec2_usage_registry()
        usage["snapshot_ids"].add("snap-0abc")
        usage["active_ami_snapshot_ids"].add("snap-0abc")
        row = only(audit.collect_ebs_snapshots(session, REGION, usage=usage))
        self.assertIn("backs a registered AMI", row["flag"])
        self.assertNotIn("unused", row["flag"])
        self.assertIn("deregister", row["notes"].lower())

    def test_ebs_snapshot_backing_a_stale_ami_is_flagged_stale(self):
        # The AMI exists (not dangling) but has no recent launch; the snapshot says so.
        session = FakeSession({"ec2": {"describe_snapshots": {"Snapshots": [{
            "SnapshotId": "snap-0abc", "State": "completed",
            "StartTime": ancient(), "VolumeSize": 50,
        }]}}})
        usage = audit.new_ec2_usage_registry()
        usage["snapshot_ids"].add("snap-0abc")
        row = only(audit.collect_ebs_snapshots(session, REGION, usage=usage))
        self.assertIn("STALE", row["flag"])
        self.assertIn("unused AMI", row["flag"])
        self.assertIn("deregistering", row["notes"].lower())

    def test_security_group_unattached(self):
        session = FakeSession({"ec2": {"describe_security_groups": {"SecurityGroups": [{
            "GroupId": "sg-0abc", "GroupName": "old-web", "VpcId": "vpc-0123",
            "Description": "unused",
        }]}}})
        row = only(audit.collect_security_groups(
            session, REGION, usage=audit.new_ec2_usage_registry()))
        self.assertEqual(row["service"], "SecurityGroup")
        self.assertIn("STALE", row["flag"])
        self.assertEqual(row["billing"], "free")

    def test_security_group_referenced_by_another_group(self):
        """A group named in another group's rules can't be deleted, even with
        no ENI attached - a different state from plain "unused"."""
        session = FakeSession({"ec2": {"describe_security_groups": {"SecurityGroups": [
            {"GroupId": "sg-app", "GroupName": "app", "VpcId": "vpc-0123",
             "IpPermissions": [{"UserIdGroupPairs": [{"GroupId": "sg-db"}]}]},
            {"GroupId": "sg-db", "GroupName": "db", "VpcId": "vpc-0123"},
        ]}}})
        rows = audit.collect_security_groups(
            session, REGION, usage=audit.new_ec2_usage_registry())
        db_row = [r for r in rows if r["resource_id"] == "sg-db"][0]
        self.assertEqual(db_row["flag"], "IN USE BY ANOTHER SECURITY GROUP")

    def test_default_security_group_is_never_a_cleanup_target(self):
        session = FakeSession({"ec2": {"describe_security_groups": {"SecurityGroups": [{
            "GroupId": "sg-default", "GroupName": "default", "VpcId": "vpc-0123",
        }]}}})
        row = only(audit.collect_security_groups(
            session, REGION, usage=audit.new_ec2_usage_registry()))
        self.assertIn("cannot be deleted", row["flag"])

    def test_key_pair(self):
        session = FakeSession({"ec2": {"describe_key_pairs": {"KeyPairs": [{
            "KeyName": "deploy-key", "CreateTime": ancient(), "KeyType": "rsa",
            "KeyPairId": "key-0abc", "KeyFingerprint": "aa:bb:cc",
        }]}}})
        row = only(audit.collect_key_pairs(session, REGION, usage=audit.new_ec2_usage_registry()))
        self.assertEqual(row["service"], "KeyPair")
        self.assertIn("STALE", row["flag"])
        self.assertIn("does not lock you out", row["notes"])

    def test_network_interface_managed_by_an_aws_service(self):
        """A requester-managed ENI must never be presented as directly
        deletable - you remove the owning service instead."""
        session = FakeSession({"ec2": {"describe_network_interfaces": {"NetworkInterfaces": [{
            "NetworkInterfaceId": "eni-0abc", "Status": "available",
            "RequesterManaged": True, "RequesterId": "amazon-rds",
            "PrivateIpAddress": "10.0.0.5", "VpcId": "vpc-0123",
        }]}}})
        row = only(audit.collect_network_interfaces(session, REGION))
        self.assertEqual(row["service"], "NetworkInterface")
        self.assertIn("service-managed", row["flag"])
        self.assertIn("remove the service that owns it", row["notes"])

    def test_network_interface_with_public_ip_is_not_free(self):
        session = FakeSession({"ec2": {"describe_network_interfaces": {"NetworkInterfaces": [{
            "NetworkInterfaceId": "eni-0abc", "Status": "available",
            "Association": {"PublicIp": "203.0.113.5"}, "VpcId": "vpc-0123",
        }]}}})
        row = only(audit.collect_network_interfaces(session, REGION))
        self.assertIn("bills every public IPv4", row["notes"])

    def test_eni_tags_come_from_tagset_not_tags(self):
        """describe_network_interfaces is the one EC2 call that names the tag
        field TagSet; reading Tags would silently lose every ENI tag."""
        session = FakeSession({"ec2": {"describe_network_interfaces": {"NetworkInterfaces": [{
            "NetworkInterfaceId": "eni-0abc", "Status": "in-use", "VpcId": "vpc-0123",
            "TagSet": [{"Key": "Project", "Value": "orders"}],
        }]}}})
        row = only(audit.collect_network_interfaces(session, REGION))
        self.assertEqual(row["tags"]["Project"], "orders")


if __name__ == "__main__":
    unittest.main()
