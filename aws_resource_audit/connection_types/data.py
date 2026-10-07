"""Connection types: EBS, EFS, Backup, Redshift, Glue, ElastiCache, OpenSearch,
RDS clusters and snapshots."""

from .vocabulary import (
    ARN_RESOLVABLE_TARGETS,
    CONF_AUTHORITATIVE,
    ConnectionType,
    OWNERSHIP_OWNS,
    OWNERSHIP_SUPPORTING,
    SEM_OWNS,
    SEM_READS_OR_WRITES,
    SEM_REFERENCES,
    SEM_RUNS_AS,
    SEM_SHARES_NETWORK,
)

TYPES = (
    # --- EBS / snapshots ---------------------------------------------------
    ConnectionType(
        "ebsvolume.ec2.attachment", "EBSVolume", ("EC2Instance",),
        "volume Attachments", CONF_AUTHORITATIVE,
        "The instance a volume is attached to, from the volume's own attachment record.",
        semantics=SEM_OWNS, ownership=OWNERSHIP_OWNS,
),
    ConnectionType(
        "ami.ebssnapshot.block-device", "AMI", ("EBSSnapshot",),
        "AMI BlockDeviceMappings", CONF_AUTHORITATIVE,
        "The snapshots backing an AMI - the reason a 'free' AMI still costs money.",
        semantics=SEM_OWNS, ownership=OWNERSHIP_OWNS,
),
    ConnectionType(
        "ebssnapshot.ebsvolume.volume-id", "EBSSnapshot", ("EBSVolume",),
        "snapshot VolumeId", CONF_AUTHORITATIVE,
        "The source volume a snapshot was taken from. Often already deleted, "
        "which is normal for a backup, so this never reports DANGLING.",
        semantics=SEM_OWNS, ownership=OWNERSHIP_OWNS,
),
    # --- EFS ---------------------------------------------------------------
    ConnectionType(
        "efs.subnet.placement", "EFSFileSystem", ("Subnet",),
        "MountTargets.SubnetId", CONF_AUTHORITATIVE,
        "A subnet a file system has a mount target in - where it can be mounted from.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "efs.networkinterface.mount-target", "EFSFileSystem", ("NetworkInterface",),
        "MountTargets.NetworkInterfaceId", CONF_AUTHORITATIVE,
        "The network interface EFS created for a mount target. Containment: remove "
        "the file system or mount target, never the interface.",
        semantics=SEM_OWNS, ownership=OWNERSHIP_OWNS,
),
    # --- AWS Backup --------------------------------------------------------
    ConnectionType(
        "backupvault.kmskey.encryption", "BackupVault", ("KMSKey",),
        "vault EncryptionKeyArn", CONF_AUTHORITATIVE,
        "The customer-managed key a vault encrypts recovery points with.",
        semantics=SEM_REFERENCES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "backupvault.any.recovery-point", "BackupVault", ARN_RESOLVABLE_TARGETS,
        "RecoveryPoints.ResourceArn", CONF_AUTHORITATIVE,
        "A resource the vault holds backups of. The resource may be deleted since, "
        "which is normal for a backup, so this never reports DANGLING.",
        semantics=SEM_REFERENCES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "backupplan.backupvault.target", "BackupPlan", ("BackupVault",),
        "plan Rules.TargetBackupVaultName", CONF_AUTHORITATIVE,
        "The vault a plan's rules write recovery points to.",
        semantics=SEM_REFERENCES, ownership=OWNERSHIP_SUPPORTING,
),
    # --- Glue ---------------------------------------------------------------
    ConnectionType(
        "gluejob.iamrole.role", "GlueJob", ("IAMRole",),
        "job Role", CONF_AUTHORITATIVE,
        "The role a Glue job runs as.",
        semantics=SEM_RUNS_AS, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "gluejob.s3bucket.script", "GlueJob", ("S3Bucket",),
        "job Command.ScriptLocation", CONF_AUTHORITATIVE,
        "The bucket a Glue job loads its script from.",
        semantics=SEM_REFERENCES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "gluecrawler.iamrole.role", "GlueCrawler", ("IAMRole",),
        "crawler Role", CONF_AUTHORITATIVE,
        "The role a crawler runs as.",
        semantics=SEM_RUNS_AS, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "gluecrawler.s3bucket.target", "GlueCrawler", ("S3Bucket",),
        "crawler Targets.S3Targets", CONF_AUTHORITATIVE,
        "A bucket a crawler reads to infer table schemas.",
        semantics=SEM_READS_OR_WRITES, ownership=OWNERSHIP_SUPPORTING,
),
    # --- Redshift ----------------------------------------------------------
    ConnectionType(
        "redshiftserverless.subnet.placement", "RedshiftServerlessWorkgroup", ("Subnet",),
        "workgroup subnetIds", CONF_AUTHORITATIVE,
        "A subnet a Serverless workgroup's endpoint runs in.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "redshiftserverless.securitygroup.membership", "RedshiftServerlessWorkgroup", ("SecurityGroup",),
        "workgroup securityGroupIds", CONF_AUTHORITATIVE,
        "A security group on a Serverless workgroup.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "redshiftserverless.iamrole.attached-role", "RedshiftServerlessWorkgroup", ("IAMRole",),
        "namespace iamRoles", CONF_AUTHORITATIVE,
        "A role the workgroup's namespace may assume, for COPY, UNLOAD and Spectrum.",
        semantics=SEM_RUNS_AS, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "redshift.securitygroup.membership", "RedshiftCluster", ("SecurityGroup",),
        "VpcSecurityGroups", CONF_AUTHORITATIVE,
        "A security group on a Redshift cluster.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "redshift.iamrole.attached-role", "RedshiftCluster", ("IAMRole",),
        "cluster IamRoles", CONF_AUTHORITATIVE,
        "A role the cluster can assume, for COPY/UNLOAD and similar.",
        semantics=SEM_RUNS_AS, ownership=OWNERSHIP_SUPPORTING,
),
    # --- ElastiCache and OpenSearch -----------------------------------------
    ConnectionType(
        "opensearchserverless.kmskey.encryption", "OpenSearchServerlessCollection", ("KMSKey",),
        "collection kmsKeyArn", CONF_AUTHORITATIVE,
        "The customer-managed KMS key a serverless collection is encrypted with.",
        semantics=SEM_REFERENCES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "elasticache.subnet.placement", "ElastiCacheCluster", ("Subnet",),
        "cache subnet group", CONF_AUTHORITATIVE,
        "A subnet in the cache's subnet group, or a serverless cache's own subnets.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "elasticache.securitygroup.membership", "ElastiCacheCluster", ("SecurityGroup",),
        "SecurityGroups", CONF_AUTHORITATIVE,
        "A security group on a cache cluster or serverless cache.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "opensearch.subnet.placement", "OpenSearchDomain", ("Subnet",),
        "VPCOptions.SubnetIds", CONF_AUTHORITATIVE,
        "A subnet a VPC-attached OpenSearch domain runs in.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "opensearch.securitygroup.membership", "OpenSearchDomain", ("SecurityGroup",),
        "VPCOptions.SecurityGroupIds", CONF_AUTHORITATIVE,
        "A security group on a VPC-attached OpenSearch domain.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    # --- RDS clusters and snapshots -----------------------------------------
    ConnectionType(
        # DocumentDB and Neptune clusters use the same link to their own instance types.
        "rdscluster.rdsinstance.member", "RDSCluster",
        ("RDSInstance", "DocumentDBInstance", "NeptuneInstance"),
        "DBClusterMembers", CONF_AUTHORITATIVE,
        "An instance that belongs to a cluster. Containment, so the two share a project.",
        semantics=SEM_OWNS, ownership=OWNERSHIP_OWNS,
),
    ConnectionType(
        "rdscluster.subnet.placement", "RDSCluster", ("Subnet",),
        "DBSubnetGroup subnets", CONF_AUTHORITATIVE,
        "A subnet in the cluster's subnet group - where its instances can run.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "rdssnapshot.rdsinstance.source", "RDSSnapshot", ("RDSInstance",),
        "snapshot DBInstanceIdentifier", CONF_AUTHORITATIVE,
        "The database a manual snapshot was taken from. Often already deleted, "
        "which is normal for a backup, so this never reports DANGLING.",
        semantics=SEM_OWNS, ownership=OWNERSHIP_OWNS,
),
    ConnectionType(
        "rdssnapshot.rdscluster.source", "RDSSnapshot",
        ("RDSCluster", "DocumentDBCluster", "NeptuneCluster"),
        "cluster snapshot DBClusterIdentifier", CONF_AUTHORITATIVE,
        "The cluster a manual cluster snapshot was taken from; may already be deleted.",
        semantics=SEM_OWNS, ownership=OWNERSHIP_OWNS,
),
)
