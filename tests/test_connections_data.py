"""Connection types for data: EBS, EFS, Backup, Redshift, ElastiCache, OpenSearch,
RDS clusters, S3 notifications. Each in all three states (connections_base)."""

import unittest

from .fakes import NO_METRICS, FakeSession, aws_ts, client_error, make_row, audit, recent
from .connections_base import ACCOUNT, ConnectionTypeTestCase, REGION, instance_payload


class StorageConnectionTests(ConnectionTypeTestCase):

    def test_ebsvolume_ec2_attachment(self):
        def build():
            session = FakeSession({
                "ec2": {
                    "describe_volumes": {"Volumes": [{
                        "VolumeId": "vol-0abc", "State": "in-use", "Size": 20,
                        "VolumeType": "gp3", "CreateTime": recent(),
                        "Attachments": [{"InstanceId": "i-0abc", "Device": "/dev/xvda",
                                         "State": "attached"}],
                    }]},
                    "describe_instances": {"Reservations": [{"Instances": [instance_payload()]}]},
                },
                "cloudwatch": NO_METRICS,
            })
            rows = audit.collect_ebs_volumes(session, REGION)
            return rows + [make_row("EC2Instance", "i-0abc")]
        self.assert_three_states("ebsvolume.ec2.attachment", build, "vol-0abc", "i-0abc")

    def test_ami_ebssnapshot_block_device(self):
        def build():
            session = FakeSession({"ec2": {"describe_images": {"Images": [{
                "ImageId": "ami-0123", "Name": "base", "State": "available",
                "CreationDate": aws_ts(recent()), "Architecture": "x86_64",
                "BlockDeviceMappings": [{"Ebs": {"SnapshotId": "snap-0abc"}}],
            }]}}})
            rows = audit.collect_amis(session, REGION)
            return rows + [make_row("EBSSnapshot", "snap-0abc")]
        self.assert_three_states("ami.ebssnapshot.block-device", build, "ami-0123", "snap-0abc")

    def test_ebssnapshot_ebsvolume_volume_id(self):
        def build():
            session = FakeSession({"ec2": {"describe_snapshots": {"Snapshots": [{
                "SnapshotId": "snap-0abc", "VolumeId": "vol-0abc", "State": "completed",
                "StartTime": recent(), "VolumeSize": 20,
            }]}}})
            rows = audit.collect_ebs_snapshots(session, REGION)
            return rows + [make_row("EBSVolume", "vol-0abc")]
        self.assert_three_states("ebssnapshot.ebsvolume.volume-id", build, "snap-0abc", "vol-0abc")

    def test_snapshot_with_deleted_source_volume_is_not_reported_as_dangling(self):
        """A deleted source volume is the normal end state for a backup, so it
        must not be surfaced as a dangling reference."""
        session = FakeSession({"ec2": {"describe_snapshots": {"Snapshots": [{
            "SnapshotId": "snap-0abc", "VolumeId": "vol-gone", "State": "completed",
            "StartTime": recent(), "VolumeSize": 20,
        }]}}})
        rows = audit.collect_ebs_snapshots(session, REGION)
        audit.resolve_edges(rows)
        self.assertNotIn("DANGLING", audit.render_connections(rows[0]))


class EfsConnectionTests(ConnectionTypeTestCase):

    @staticmethod
    def _efs():
        session = FakeSession({
            "efs": {
                "describe_file_systems": {"FileSystems": [{
                    "FileSystemId": "fs-0a", "CreationTime": recent(), "Tags": []}]},
                "describe_mount_targets": {"MountTargets": [{
                    "MountTargetId": "fsmt-1", "SubnetId": "subnet-0123",
                    "NetworkInterfaceId": "eni-0mt", "VpcId": "vpc-0123"}]},
            },
            "cloudwatch": NO_METRICS,
        })
        return audit.collect_efs_file_systems(session, REGION)

    def test_efs_subnet_placement(self):
        self.assert_three_states("efs.subnet.placement",
                                 lambda: self._efs() + [make_row("Subnet", "subnet-0123")],
                                 "fs-0a", "subnet-0123")

    def test_efs_networkinterface_mount_target(self):
        self.assert_three_states("efs.networkinterface.mount-target",
                                 lambda: self._efs() + [make_row("NetworkInterface", "eni-0mt")],
                                 "fs-0a", "eni-0mt")


class BackupConnectionTests(ConnectionTypeTestCase):

    @staticmethod
    def _vault(**vault):
        session = FakeSession({"backup": {
            "list_backup_vaults": {"BackupVaultList": [dict({"BackupVaultName": "Default"}, **vault)]},
            "list_recovery_points_by_backup_vault": {"RecoveryPoints": [{
                "CreationDate": recent(),
                "ResourceArn": f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/orders"}]},
        }})
        return audit.collect_backup_vaults(session, REGION)

    def test_backupvault_kmskey_encryption(self):
        key_arn = f"arn:aws:kms:{REGION}:{ACCOUNT}:key/1234abcd"
        self.assert_three_states("backupvault.kmskey.encryption",
                                 lambda: self._vault(EncryptionKeyArn=key_arn)
                                 + [make_row("KMSKey", "1234abcd")], "Default", "1234abcd")

    def test_backupvault_any_recovery_point(self):
        self.assert_three_states("backupvault.any.recovery-point",
                                 lambda: self._vault() + [make_row("DynamoDBTable", "orders")],
                                 "Default", "orders")

    def test_backupplan_backupvault_target(self):
        def build():
            session = FakeSession({"backup": {
                "list_backup_plans": {"BackupPlansList": [{"BackupPlanId": "p1", "BackupPlanName": "nightly"}]},
                "get_backup_plan": {"BackupPlan": {"Rules": [{"TargetBackupVaultName": "Default"}]}},
            }})
            return audit.collect_backup_plans(session, REGION) + [make_row("BackupVault", "Default")]
        self.assert_three_states("backupplan.backupvault.target", build, "p1", "Default")


class RedshiftConnectionTests(ConnectionTypeTestCase):

    @staticmethod
    def _cluster(**cluster):
        session = FakeSession({"redshift": {"describe_clusters": {"Clusters": [
            dict({"ClusterIdentifier": "bi", "ClusterCreateTime": recent()}, **cluster)]}},
            "cloudwatch": NO_METRICS})
        return audit.collect_redshift_clusters(session, REGION)

    def test_redshift_securitygroup_membership(self):
        self.assert_three_states(
            "redshift.securitygroup.membership",
            lambda: self._cluster(VpcSecurityGroups=[{"VpcSecurityGroupId": "sg-0123"}])
            + [make_row("SecurityGroup", "sg-0123")], "bi", "sg-0123")

    def test_redshift_iamrole_attached_role(self):
        self.assert_three_states(
            "redshift.iamrole.attached-role",
            lambda: self._cluster(IamRoles=[{"IamRoleArn": f"arn:aws:iam::{ACCOUNT}:role/copy-role"}])
            + [make_row("IAMRole", "copy-role", region="global")], "bi", "copy-role")


class RedshiftServerlessConnectionTests(ConnectionTypeTestCase):

    @staticmethod
    def _workgroup(roles=(), **group):
        session = FakeSession({"redshift-serverless": {
            "list_workgroups": {"workgroups": [dict({"workgroupName": "olap", "namespaceName": "ns",
                                                     "creationDate": recent()}, **group)]},
            "list_namespaces": {"namespaces": [{"namespaceName": "ns", "iamRoles": list(roles)}]},
            "list_tags_for_resource": {"tags": []},
        }, "cloudwatch": NO_METRICS})
        return audit.collect_redshift_serverless_workgroups(session, REGION)

    def test_redshiftserverless_subnet_placement(self):
        self.assert_three_states(
            "redshiftserverless.subnet.placement",
            lambda: self._workgroup(subnetIds=["subnet-0123"]) + [make_row("Subnet", "subnet-0123")],
            "olap", "subnet-0123")

    def test_redshiftserverless_securitygroup_membership(self):
        self.assert_three_states(
            "redshiftserverless.securitygroup.membership",
            lambda: self._workgroup(securityGroupIds=["sg-0123"]) + [make_row("SecurityGroup", "sg-0123")],
            "olap", "sg-0123")

    def test_redshiftserverless_iamrole_attached_role(self):
        """The namespace returns each role as text wrapping its ARN, not as the ARN."""
        role = f"IamRole(applyStatus=in-sync, iamRoleArn=arn:aws:iam::{ACCOUNT}:role/copy-role)"
        self.assert_three_states(
            "redshiftserverless.iamrole.attached-role",
            lambda: self._workgroup(roles=[role]) + [make_row("IAMRole", "copy-role", region="global")],
            "olap", "copy-role")


class ServerlessCollectionConnectionTests(ConnectionTypeTestCase):

    def test_opensearchserverless_kmskey_encryption(self):
        def build():
            session = FakeSession({"opensearchserverless": {
                "list_collections": {"collectionSummaries": [{"id": "abc123", "name": "docs"}]},
                "batch_get_collection": {"collectionDetails": [{
                    "id": "abc123", "name": "docs", "arn": "arn:coll",
                    "kmsKeyArn": f"arn:aws:kms:{REGION}:{ACCOUNT}:key/key-0123"}]},
                "list_tags_for_resource": {"tags": []},
            }, "cloudwatch": NO_METRICS})
            return audit.collect_opensearch_serverless_collections(session, REGION) + [make_row("KMSKey", "key-0123")]
        self.assert_three_states("opensearchserverless.kmskey.encryption", build, "abc123", "key-0123")


class DatastoreConnectionTests(ConnectionTypeTestCase):

    def test_elasticache_securitygroup_membership(self):
        def build():
            session = FakeSession({"elasticache": {
                "describe_cache_subnet_groups": {"CacheSubnetGroups": []},
                "describe_cache_clusters": {"CacheClusters": [{
                    "CacheClusterId": "c-001", "CacheClusterCreateTime": recent(),
                    "SecurityGroups": [{"SecurityGroupId": "sg-0123"}]}]},
                "describe_serverless_caches": {"ServerlessCaches": []},
            }, "cloudwatch": NO_METRICS})
            return (audit.collect_elasticache_clusters(session, REGION)
                    + [make_row("SecurityGroup", "sg-0123")])
        self.assert_three_states("elasticache.securitygroup.membership", build, "c-001", "sg-0123")

    def test_elasticache_subnet_placement(self):
        def build():
            session = FakeSession({"elasticache": {
                "describe_cache_subnet_groups": {"CacheSubnetGroups": [{
                    "CacheSubnetGroupName": "cache-subnets",
                    "Subnets": [{"SubnetIdentifier": "subnet-0123"}]}]},
                "describe_cache_clusters": {"CacheClusters": [{
                    "CacheClusterId": "c-001", "CacheClusterCreateTime": recent(),
                    "CacheSubnetGroupName": "cache-subnets"}]},
                "describe_serverless_caches": {"ServerlessCaches": []},
            }, "cloudwatch": NO_METRICS})
            return (audit.collect_elasticache_clusters(session, REGION)
                    + [make_row("Subnet", "subnet-0123")])
        self.assert_three_states("elasticache.subnet.placement", build, "c-001", "subnet-0123")

    @staticmethod
    def _domain():
        session = FakeSession({"opensearch": {
            "list_domain_names": {"DomainNames": [{"DomainName": "search"}]},
            "describe_domains": {"DomainStatusList": [{
                "DomainName": "search", "ARN": "", "VPCOptions": {
                    "VPCId": "vpc-0123", "SubnetIds": ["subnet-0123"],
                    "SecurityGroupIds": ["sg-0123"]}}]},
        }, "cloudwatch": NO_METRICS})
        return audit.collect_opensearch_domains(session, REGION)

    def test_opensearch_subnet_placement(self):
        self.assert_three_states("opensearch.subnet.placement",
                                 lambda: self._domain() + [make_row("Subnet", "subnet-0123")],
                                 "search", "subnet-0123")

    def test_opensearch_securitygroup_membership(self):
        self.assert_three_states("opensearch.securitygroup.membership",
                                 lambda: self._domain() + [make_row("SecurityGroup", "sg-0123")],
                                 "search", "sg-0123")


class RdsClusterConnectionTests(ConnectionTypeTestCase):

    def test_rdscluster_rdsinstance_member(self):
        def build():
            session = FakeSession({"rds": {"describe_db_subnet_groups": {"DBSubnetGroups": []},
            "describe_db_clusters": {"DBClusters": [{
                "DBClusterIdentifier": "orders-aurora", "Engine": "aurora-postgresql",
                "ClusterCreateTime": recent(),
                "DBClusterMembers": [{"DBInstanceIdentifier": "orders-aurora-1"}],
            }]}}, "cloudwatch": NO_METRICS})
            return (audit.collect_rds_clusters(session, REGION)
                    + [make_row("RDSInstance", "orders-aurora-1")])
        self.assert_three_states("rdscluster.rdsinstance.member", build,
                                 "orders-aurora", "orders-aurora-1")

    def test_rdscluster_subnet_placement(self):
        def build():
            session = FakeSession({"rds": {
                "describe_db_subnet_groups": {"DBSubnetGroups": [{
                    "DBSubnetGroupName": "db-subnets", "Subnets": [{"SubnetIdentifier": "subnet-0123"}]}]},
                "describe_db_clusters": {"DBClusters": [{
                    "DBClusterIdentifier": "orders-aurora", "ClusterCreateTime": recent(),
                    "DBSubnetGroup": "db-subnets"}]},
            }, "cloudwatch": NO_METRICS})
            return audit.collect_rds_clusters(session, REGION) + [make_row("Subnet", "subnet-0123")]
        self.assert_three_states("rdscluster.subnet.placement", build, "orders-aurora", "subnet-0123")

    def _snapshots(self, instance=(), cluster=()):
        return FakeSession({"rds": {
            "describe_db_snapshots": {"DBSnapshots": list(instance)},
            "describe_db_cluster_snapshots": {"DBClusterSnapshots": list(cluster)},
        }})

    def test_rdssnapshot_rdsinstance_source(self):
        def build():
            session = self._snapshots(instance=[{
                "DBSnapshotIdentifier": "pre-upgrade", "DBInstanceIdentifier": "orders-db",
                "SnapshotCreateTime": recent()}])
            return (audit.collect_rds_snapshots(session, REGION)
                    + [make_row("RDSInstance", "orders-db")])
        self.assert_three_states("rdssnapshot.rdsinstance.source", build,
                                 "pre-upgrade", "orders-db")

    def test_rdssnapshot_rdscluster_source(self):
        def build():
            session = self._snapshots(cluster=[{
                "DBClusterSnapshotIdentifier": "final", "DBClusterIdentifier": "orders-aurora",
                "SnapshotCreateTime": recent()}])
            return (audit.collect_rds_snapshots(session, REGION)
                    + [make_row("RDSCluster", "orders-aurora")])
        self.assert_three_states("rdssnapshot.rdscluster.source", build,
                                 "final", "orders-aurora")

    def test_a_snapshot_of_a_deleted_database_is_not_dangling(self):
        session = self._snapshots(instance=[{
            "DBSnapshotIdentifier": "old", "DBInstanceIdentifier": "gone-db",
            "SnapshotCreateTime": recent()}])
        rows = audit.collect_rds_snapshots(session, REGION)
        audit.resolve_edges(rows)
        self.assertNotIn("DANGLING", audit.render_connections(rows[0]).upper())


class BucketNotificationTests(ConnectionTypeTestCase):
    """A bucket that triggers something is wired into an application."""

    def _build(self, config=None):
        def build():
            session = FakeSession({"s3": {
                "list_buckets": {"Buckets": [{"Name": "uploads", "BucketRegion": "us-east-1",
                                              "CreationDate": recent()}]},
                "get_bucket_tagging": client_error(code="NoSuchTagSet"),
                "get_bucket_notification_configuration": config if config is not None else {
                    "LambdaFunctionConfigurations": [{
                        "LambdaFunctionArn": f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:on-upload",
                        "Events": ["s3:ObjectCreated:*"],
                    }]},
            }})
            rows = audit.collect_s3_buckets(session)
            return rows + [make_row("LambdaFunction", "on-upload")]
        return build

    def test_s3bucket_any_notification(self):
        self.assert_three_states("s3bucket.any.notification", self._build(),
                                 "uploads", "on-upload")

    def test_the_event_types_travel_with_the_link(self):
        rows = self._build()()
        audit.resolve_edges(rows)
        self.assertIn("ObjectCreated",
                      audit.render_connections(self._row(rows, "uploads")))

    def test_eventbridge_delivery_invents_no_destination(self):
        """EventBridge delivery names no target on the bucket; none is invented."""
        rows = self._build(config={"EventBridgeConfiguration": {}})()
        audit.resolve_edges(rows)
        bucket = self._row(rows, "uploads")

        self.assertIn("EventBridge", audit.render_connections(bucket))
        self.assertEqual([e for e in bucket.get("_edges", [])], [])

    def test_no_bucket_objects_are_read(self):
        """The one thing this feature must never start doing."""
        session = FakeSession({"s3": {
            "list_buckets": {"Buckets": [{"Name": "uploads", "BucketRegion": "us-east-1", "CreationDate": recent()}]},
            "get_bucket_tagging": client_error(code="NoSuchTagSet"),
            "get_bucket_notification_configuration": {},
        }})
        audit.collect_s3_buckets(session)
        called = session.get("s3").operations_called()

        self.assertNotIn("list_objects_v2", called)
        self.assertNotIn("list_objects", called)
        self.assertNotIn("get_object", called)


if __name__ == "__main__":
    unittest.main()
