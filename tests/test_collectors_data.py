"""Collector tests for data: RDS, Aurora, DocumentDB, Neptune, DynamoDB, S3, EFS,
ElastiCache, OpenSearch, Redshift, Backup."""

import unittest

from .fakes import NO_METRICS, FakeSession, ancient, metrics_all_zero, metrics_at, audit, recent
from .collectors_base import ACCOUNT, REGION, only


class DataCollectorTests(unittest.TestCase):

    def test_an_idle_aurora_cluster_is_flagged_and_explains_storage(self):
        session = FakeSession({"rds": {"describe_db_subnet_groups": {"DBSubnetGroups": []},
            "describe_db_clusters": {"DBClusters": [{
            "DBClusterIdentifier": "reports", "Engine": "aurora-mysql", "EngineVersion": "8.0",
            "ClusterCreateTime": ancient(), "Status": "stopped", "DBClusterMembers": [],
            "ServerlessV2ScalingConfiguration": {"MinCapacity": 0.5, "MaxCapacity": 2},
            "DBClusterArn": f"arn:aws:rds:{REGION}:111122223333:cluster:reports",
        }]}}, "cloudwatch": metrics_all_zero(recent())})
        row = only(audit.collect_rds_clusters(session, REGION))
        self.assertEqual((row["service"], row["billing"]), ("RDSCluster", "cost"))
        self.assertNotIn("ACTIVE", row["flag"])
        self.assertIn("Serverless v2", row["description"])
        self.assertIn("storage keeps billing", row["notes"])

    def test_an_old_manual_rds_snapshot_is_stale(self):
        session = FakeSession({"rds": {
            "describe_db_snapshots": {"DBSnapshots": [{
                "DBSnapshotIdentifier": "pre-migration", "DBInstanceIdentifier": "legacy",
                "SnapshotCreateTime": ancient(), "Engine": "postgres", "AllocatedStorage": 100}]},
            "describe_db_cluster_snapshots": {"DBClusterSnapshots": []},
        }})
        row = only(audit.collect_rds_snapshots(session, REGION))
        self.assertEqual((row["service"], row["billing"]), ("RDSSnapshot", "cost"))
        self.assertIn("STALE", row["flag"])
        self.assertIn("never expire", row["notes"])

    def test_an_unmounted_efs_file_system_is_billed_and_idle(self):
        session = FakeSession({
            "efs": {
                "describe_file_systems": {"FileSystems": [{
                    "FileSystemId": "fs-0a", "Name": "shared", "CreationTime": ancient(),
                    "SizeInBytes": {"Value": 5 * 1024 ** 3}, "PerformanceMode": "generalPurpose",
                    "Tags": []}]},
                "describe_mount_targets": {"MountTargets": []},
            },
            "cloudwatch": metrics_all_zero(recent()),
        })
        row = only(audit.collect_efs_file_systems(session, REGION))
        self.assertEqual((row["service"], row["billing"]), ("EFSFileSystem", "cost"))
        self.assertNotIn("ACTIVE", row["flag"])
        self.assertIn("5.0GiB", row["description"])
        self.assertIn("No mount targets", row["notes"])

    def test_an_idle_elasticache_cluster_and_a_serverless_cache(self):
        session = FakeSession({
            "elasticache": {
                "describe_cache_subnet_groups": {"CacheSubnetGroups": []},
                "describe_cache_clusters": {"CacheClusters": [{
                    "CacheClusterId": "sessions-001", "ReplicationGroupId": "sessions",
                    "Engine": "redis", "CacheNodeType": "cache.t4g.micro", "NumCacheNodes": 1,
                    "CacheClusterCreateTime": ancient(), "ARN": "arn:aws:elasticache:x:1:cluster:sessions-001",
                    "SecurityGroups": [{"SecurityGroupId": "sg-1"}]}]},
                "list_tags_for_resource": {"TagList": []},
                "describe_serverless_caches": {"ServerlessCaches": [{
                    "ServerlessCacheName": "rate-limits", "Engine": "valkey", "CreateTime": ancient()}]},
            },
            "cloudwatch": metrics_all_zero(recent()),
        })
        rows = {r["resource_id"]: r for r in audit.collect_elasticache_clusters(session, REGION)}
        self.assertEqual(set(rows), {"sessions-001", "rate-limits"})
        self.assertEqual({r["service"] for r in rows.values()}, {"ElastiCacheCluster"})
        self.assertEqual(rows["sessions-001"]["billing"], "cost")
        self.assertNotIn("ACTIVE", rows["sessions-001"]["flag"])
        self.assertIn("serverless", rows["rate-limits"]["description"])

    def test_a_serverless_collection_nobody_queries_still_bills_its_minimum(self):
        calls = []

        def metrics(**kwargs):
            calls.append({d["Name"]: d["Value"] for d in kwargs["Dimensions"]})
            return {"Datapoints": [{"Timestamp": recent(), "Sum": 0.0}]}
        created_ms = int(ancient().timestamp() * 1000)
        session = FakeSession({"opensearchserverless": {
            "list_collections": {"collectionSummaries": [{"id": "abc123", "name": "docs"}]},
            "batch_get_collection": {"collectionDetails": [{
                "id": "abc123", "name": "docs", "arn": "arn:aws:aoss:us-east-1:111122223333:collection/abc123",
                "type": "VECTORSEARCH", "status": "ACTIVE", "standbyReplicas": "ENABLED",
                "createdDate": created_ms}]},
            "list_tags_for_resource": {"tags": [{"key": "Project", "value": "rag"}]},
        }, "cloudwatch": {"get_metric_statistics": metrics}})
        row = only(audit.collect_opensearch_serverless_collections(session, REGION))
        self.assertEqual((row["service"], row["resource_id"], row["billing"]), ("OpenSearchServerlessCollection", "abc123", "cost"))
        self.assertNotIn("ACTIVE", row["flag"])
        self.assertIn("standby replicas on", row["notes"])
        self.assertEqual(row["tags"], {"Project": "rag"})
        self.assertEqual(calls[0], {"ClientId": "111122223333", "CollectionId": "abc123", "CollectionName": "docs"})

    def test_an_opensearch_domain_nobody_searches(self):
        session = FakeSession({
            "opensearch": {
                "list_domain_names": {"DomainNames": [{"DomainName": "logs"}]},
                "describe_domains": {"DomainStatusList": [{
                    "DomainName": "logs", "ARN": "arn:aws:es:us-east-1:111122223333:domain/logs",
                    "EngineVersion": "OpenSearch_2.11",
                    "ClusterConfig": {"InstanceType": "t3.small.search", "InstanceCount": 1}}]},
                "list_tags": {"TagList": []},
            },
            "cloudwatch": metrics_all_zero(recent()),
        })
        row = only(audit.collect_opensearch_domains(session, REGION))
        self.assertEqual((row["service"], row["billing"]), ("OpenSearchDomain", "cost"))
        self.assertNotIn("ACTIVE", row["flag"])

    def test_documentdb_and_neptune_get_their_own_types_and_metrics(self):
        cw_calls = []

        def metrics(**kwargs):
            cw_calls.append((kwargs["Namespace"], kwargs["MetricName"]))
            return {"Datapoints": []}
        session = FakeSession({"rds": {"describe_db_subnet_groups": {"DBSubnetGroups": []},
            "describe_db_clusters": {"DBClusters": [
            {"DBClusterIdentifier": "docs", "Engine": "docdb", "ClusterCreateTime": ancient(),
             "DBClusterMembers": [{"DBInstanceIdentifier": "docs-1"}]},
            {"DBClusterIdentifier": "graph", "Engine": "neptune", "ClusterCreateTime": ancient()},
        ]}}, "cloudwatch": {"get_metric_statistics": metrics}})
        rows = {r["resource_id"]: r for r in audit.collect_rds_clusters(session, REGION)}
        self.assertEqual(rows["docs"]["service"], "DocumentDBCluster")
        self.assertEqual(rows["graph"]["service"], "NeptuneCluster")
        self.assertEqual(rows["docs"]["_edges"][0]["target_service"], "DocumentDBInstance")
        self.assertIn(("AWS/DocDB", "DatabaseConnections"), cw_calls)
        self.assertIn(("AWS/Neptune", "TotalRequestsPerSec"), cw_calls)

    def test_documentdb_and_neptune_instances_are_not_rds_instances(self):
        session = FakeSession({"rds": {
            "describe_db_instances": {"DBInstances": [
                {"DBInstanceIdentifier": "docs-1", "Engine": "docdb", "InstanceCreateTime": ancient()},
                {"DBInstanceIdentifier": "graph-1", "Engine": "neptune", "InstanceCreateTime": ancient()}]},
        }, "cloudwatch": NO_METRICS})
        rows = {r["resource_id"]: r["service"] for r in audit.collect_rds_instances(session, REGION)}
        self.assertEqual(rows, {"docs-1": "DocumentDBInstance", "graph-1": "NeptuneInstance"})

    def test_a_paused_redshift_cluster_still_bills_storage(self):
        session = FakeSession({"redshift": {"describe_clusters": {"Clusters": [{
            "ClusterIdentifier": "bi", "ClusterStatus": "paused", "NodeType": "ra3.xlplus",
            "NumberOfNodes": 2, "ClusterCreateTime": ancient()}]}}, "cloudwatch": NO_METRICS})
        row = only(audit.collect_redshift_clusters(session, REGION))
        self.assertEqual((row["service"], row["billing"]), ("RedshiftCluster", "cost"))
        self.assertIn("paused", row["flag"])

    def test_a_serverless_workgroup_with_no_compute_is_not_active(self):
        session = FakeSession({"redshift-serverless": {
            "list_workgroups": {"workgroups": [{"workgroupName": "olap", "namespaceName": "ns",
                                                "baseCapacity": 8, "creationDate": ancient()}]},
            "list_namespaces": {"namespaces": []},
            "list_tags_for_resource": {"tags": [{"key": "Project", "value": "bi"}]},
        }, "cloudwatch": metrics_all_zero(recent())})
        row = only(audit.collect_redshift_serverless_workgroups(session, REGION))
        self.assertEqual((row["service"], row["billing"]), ("RedshiftServerlessWorkgroup", "usage"))
        self.assertNotIn("ACTIVE", row["flag"])
        self.assertEqual(row["tags"], {"Project": "bi"})
        self.assertIn("8 RPU base", row["description"])

    def test_a_backup_vault_summarises_its_recovery_points(self):
        session = FakeSession({"backup": {
            "list_backup_vaults": {"BackupVaultList": [
                {"BackupVaultName": "Default", "CreationDate": ancient(),
                 "BackupVaultArn": "arn:aws:backup:us-east-1:1:backup-vault:Default"},
                {"BackupVaultName": "empty", "CreationDate": ancient()}]},
            "list_recovery_points_by_backup_vault": lambda BackupVaultName, **_: {
                "RecoveryPoints": [
                    {"CreationDate": recent(), "BackupSizeInBytes": 2 * 1024 ** 3,
                     "ResourceArn": f"arn:aws:dynamodb:{REGION}:1:table/orders"}]
                if BackupVaultName == "Default" else []},
            "list_tags": {"Tags": {}},
        }})
        rows = {r["resource_id"]: r for r in audit.collect_backup_vaults(session, REGION)}
        self.assertIn("ACTIVE", rows["Default"]["flag"])
        self.assertIn("2.00GiB", rows["Default"]["description"])
        self.assertIn("empty vault", rows["empty"]["flag"])
        self.assertEqual(rows["Default"]["billing"], "cost")

    def test_a_backup_plan_that_never_ran_is_stale(self):
        session = FakeSession({"backup": {
            "list_backup_plans": {"BackupPlansList": [{
                "BackupPlanId": "p1", "BackupPlanName": "nightly", "CreationDate": ancient()}]},
            "get_backup_plan": {"BackupPlan": {"Rules": []}},
            "list_tags": {"Tags": {}},
        }})
        row = only(audit.collect_backup_plans(session, REGION))
        self.assertEqual((row["name"], row["billing"]), ("nightly", "indirect"))
        self.assertIn("STALE", row["flag"])


class DataServiceCollectorTests(unittest.TestCase):

    def test_rds_instance(self):
        session = FakeSession({
            "rds": {
                "describe_db_instances": {"DBInstances": [{
                    "DBInstanceIdentifier": "orders-db",
                    "DBInstanceArn": f"arn:aws:rds:{REGION}:{ACCOUNT}:db:orders-db",
                    "Engine": "postgres", "EngineVersion": "15.4",
                    "DBInstanceClass": "db.t3.micro", "AllocatedStorage": 20,
                    "DBInstanceStatus": "available", "InstanceCreateTime": recent(),
                    "DBSubnetGroup": {"VpcId": "vpc-0123", "DBSubnetGroupName": "default"},
                    "VpcSecurityGroups": [{"VpcSecurityGroupId": "sg-0123"}],
                }]},
                "list_tags_for_resource": {"TagList": []},
            },
            "cloudwatch": metrics_at(recent()),
        })
        row = only(audit.collect_rds_instances(session, REGION))
        self.assertEqual(row["service"], "RDSInstance")
        self.assertEqual(row["resource_id"], "orders-db")
        self.assertEqual(row["billing"], "cost")
        self.assertIn("postgres", row["description"])

    def test_dynamodb_table(self):
        session = FakeSession({
            "dynamodb": {
                "list_tables": {"TableNames": ["orders"]},
                "describe_table": {"Table": {
                    "TableName": "orders", "TableArn": f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/orders",
                    "TableStatus": "ACTIVE", "CreationDateTime": recent(), "ItemCount": 42,
                    "BillingModeSummary": {"BillingMode": "PAY_PER_REQUEST"},
                }},
                "list_tags_of_resource": {"Tags": []},
            },
            "cloudwatch": metrics_at(recent()),
        })
        row = only(audit.collect_dynamodb_tables(session, REGION))
        self.assertEqual(row["service"], "DynamoDBTable")
        self.assertEqual(row["billing"], "usage")
        self.assertIn("PAY_PER_REQUEST", row["description"])

    def test_s3_bucket(self):
        session = FakeSession({"s3": {
            "list_buckets": {"Buckets": [{"Name": "orders-assets", "CreationDate": recent()}]},
            "get_bucket_notification_configuration": {},
            "get_bucket_tagging": {"TagSet": [{"Key": "Project", "Value": "orders"}]},
        }})
        row = only(audit.collect_s3_buckets(session))
        self.assertEqual(row["service"], "S3Bucket")
        self.assertEqual(row["region"], "global")
        self.assertEqual(row["tags"]["Project"], "orders")
        self.assertIn("no usage signal for this resource type", row["notes"])


if __name__ == "__main__":
    unittest.main()
