"""AWS Backup vaults and plans. Recovery points bill per GB-month until they
expire or are deleted; a vault summarises them rather than listing each one."""

from ... import coverage
from ...config import RETRY_CONFIG
from ...collect import raw_capture
from ...collect.arns import probable_resource_id_from_arn
from ...collect.calls import paged, safe_call
from ...rows import add_edge, error_row, new_row
from ...staleness import days_ago, flag_stale
from ...text import join_nonempty


def _tags(client, arn):
    resp = safe_call(client.list_tags, capability=coverage.TAGS, ResourceArn=arn) if arn else {}
    return {} if "__error__" in resp else dict(resp.get("Tags") or {})


def collect_backup_vaults(session, region):
    client = session.client("backup", region_name=region, config=RETRY_CONFIG)
    vaults, page_error = paged(client, "list_backup_vaults", "BackupVaultList", service="BackupVault")
    if page_error and not vaults:
        return [error_row("BackupVault", region, "ERROR", page_error)]
    rows = []
    for vault in vaults:
        name = vault["BackupVaultName"]
        points, _error = paged(client, "list_recovery_points_by_backup_vault", "RecoveryPoints",
                               service="BackupVault", BackupVaultName=name)
        newest = max((p["CreationDate"] for p in points if p.get("CreationDate")), default=None)
        size = sum(p.get("BackupSizeInBytes") or 0 for p in points) / 1024 ** 3
        created = vault.get("CreationDate")
        row = new_row(
            "BackupVault", region, name, name, created, newest,
            days_ago(newest) if newest else days_ago(created), newest is None,
            "STALE (empty vault)" if not points
            else flag_stale(days_ago(newest), days_ago(created), False),
            "Recovery points bill per GB-month until they expire or are deleted; an empty "
            "vault is free. Usage is the newest recovery point. ",
            tags=_tags(client, vault.get("BackupVaultArn")),
            description=join_nonempty([f"{len(points)} recovery point(s)", f"{size:.2f}GiB"], ", "),
            arn=vault.get("BackupVaultArn", ""))
        raw_capture.record("BackupVault", region, name, vault)
        key_id, _svc = probable_resource_id_from_arn(vault.get("EncryptionKeyArn") or "")
        add_edge(row, key_id, "is encrypted with key", "vault EncryptionKeyArn",
                 conn_type="backupvault.kmskey.encryption", target_service="KMSKey")
        protected = {probable_resource_id_from_arn(p.get("ResourceArn") or "") for p in points}
        for rid, service in sorted(p for p in protected if p[0]):
            add_edge(row, rid, "holds backups of", "RecoveryPoints.ResourceArn",
                     conn_type="backupvault.any.recovery-point", target_service=service,
                     assert_exists=False)
        rows.append(row)
    return rows


def collect_backup_plans(session, region):
    client = session.client("backup", region_name=region, config=RETRY_CONFIG)
    plans, page_error = paged(client, "list_backup_plans", "BackupPlansList", service="BackupPlan")
    if page_error and not plans:
        return [error_row("BackupPlan", region, "ERROR", page_error)]
    rows = []
    for plan in plans:
        pid = plan["BackupPlanId"]
        last = plan.get("LastExecutionDate")
        created = plan.get("CreationDate")
        detail = safe_call(client.get_backup_plan, BackupPlanId=pid)
        rules = [] if "__error__" in detail else (detail.get("BackupPlan") or {}).get("Rules", [])
        row = new_row(
            "BackupPlan", region, pid, plan.get("BackupPlanName", pid), created, last,
            days_ago(last) if last else days_ago(created), last is None,
            flag_stale(days_ago(last) if last else None, days_ago(created), last is None),
            "A plan is free; the recovery points it creates are what bill. ",
            tags=_tags(client, plan.get("BackupPlanArn")), description=f"{len(rules)} rule(s)",
            arn=plan.get("BackupPlanArn", ""))
        raw_capture.record("BackupPlan", region, pid, plan)
        for vault in sorted({r.get("TargetBackupVaultName") for r in rules} - {None}):
            add_edge(row, vault, "writes to vault", "plan Rules.TargetBackupVaultName",
                     conn_type="backupplan.backupvault.target", target_service="BackupVault")
        rows.append(row)
    return rows
