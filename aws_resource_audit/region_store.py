"""The saved list of regions each account has resources in. Only a scan of every
enabled region replaces it; any other scan only adds, so nothing gets hidden."""

import json
import logging
import os

STORE_VERSION = 1

logger = logging.getLogger(__name__)


def _read(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        # Not fatal: the file only supplies a default, and a scan still runs.
        logger.warning("ignoring unreadable %s (%s); no saved regions", path, e)
        return {}
    return data if isinstance(data, dict) else {}


def load_active_regions(path, account):
    """The saved entry for one account, or None. An empty list counts as none."""
    entry = (_read(path).get("accounts") or {}).get(account or "")
    if not isinstance(entry, dict) or not entry.get("regions"):
        return None
    return {"regions": sorted(str(r) for r in entry["regions"]),
            "updated_at": entry.get("updated_at") or "",
            "all_regions_scan_at": entry.get("all_regions_scan_at") or ""}


def record_active_regions(path, account, regions, *, replace, observed_at):
    """Save `regions` for `account`; returns the stored list. replace=True only
    for a scan that listed every enabled region; otherwise regions are added."""
    if not account:
        return sorted(regions)
    data = _read(path)
    accounts = data.setdefault("accounts", {})
    entry = accounts.get(account) if isinstance(accounts.get(account), dict) else {}
    stamp = observed_at.isoformat()
    kept = set() if replace else set(entry.get("regions") or ())
    stored = sorted(kept | set(regions))
    entry.update({"regions": stored, "updated_at": stamp})
    if replace:
        entry["all_regions_scan_at"] = stamp
    accounts[account] = entry
    data["schema_version"] = STORE_VERSION
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    return stored
