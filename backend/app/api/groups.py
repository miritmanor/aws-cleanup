"""Naming project groups without re-scanning: write the group store, then drop the
snapshot cache so the next read applies it. One seeded member names its whole project."""

import logging
import re

from fastapi import APIRouter, HTTPException, status

from aws_resource_audit.config import now
from aws_resource_audit.present.format import fmt_dt
from aws_resource_audit.rows import member_key

from .. import deps, stores
from ..schemas import (
    Group,
    GroupCreate,
    GroupMember,
    GroupMembersAdd,
    GroupRename,
    ManualGroupCreate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/groups", tags=["groups"])

_GID = re.compile(r"^grp-(\d+)$")


def _next_gid(store):
    """grp-0001, grp-0002, ... never reusing a number, via a persisted "next_gid"
    (derived from existing ids when absent), so stale clients cannot rebind an id."""
    groups = store.get("groups", {})
    highest = 0
    for gid in groups:
        match = _GID.match(gid)
        if match:
            highest = max(highest, int(match.group(1)))
    counter = max(int(store.get("next_gid") or 0), highest + 1)
    store["next_gid"] = counter + 1
    return f"grp-{counter:04d}"


def _live_keys():
    """Member keys in the current scan, or an empty set when there is no scan."""
    try:
        snapshot = stores.snapshot_store().load()
    except Exception:                                            # noqa: BLE001
        # An empty set does not merely shrink the answer - it turns OFF the
        # "is this key in the current scan" check at every call site below.
        logger.warning("could not read the snapshot; member-key validation is "
                       "disabled for this request", exc_info=True)
        return set()
    return {member_key(row) for row in snapshot.rows}


def _tag_derived_keys():
    """Member keys the scan put in a project by their own AWS tag; hand-picking must not
    move them. Read from the DETECTED method. Empty if there is no scan."""
    try:
        snapshot = stores.snapshot_store().load()
    except Exception:                                            # noqa: BLE001
        # Same consequence as _live_keys: real AWS tags stop outranking a
        # manual grouping, because nothing knows which keys are tag-derived.
        logger.warning("could not read the snapshot; tag-derived keys cannot "
                       "be protected for this request", exc_info=True)
        return set()
    return {member_key(row) for row in snapshot.rows
            if (row.get("_detected_grouping_method") or row.get("grouping_method")) == "tag"}


def _member_conflicts(member_keys, groups, exclude_gid=None):
    """(key, gid, name) for each of `member_keys` already in a DIFFERENT stored group:
    a resource is never silently moved between groups."""
    conflicts = []
    for key in member_keys:
        for gid, stored in groups.items():
            if gid == exclude_gid:
                continue
            if key in stored.get("members", []):
                conflicts.append((key, gid, stored.get("name")))
                break
    return conflicts


def _conflict_detail(conflicts):
    parts = (f"{k} is already in group {gid} (\"{name}\")" for k, gid, name in conflicts)
    return ("; ".join(parts) + ". Rename or remove it from that group first, "
            "or drop it from this selection.")


def _as_group(gid, stored, live):
    members = sorted(stored.get("members", []))
    return Group(
        id=gid,
        name=stored.get("name", ""),
        updated_at=stored.get("updated_at"),
        members=[GroupMember(member_key=k, present=k in live) for k in members],
        live_members=sum(1 for k in members if k in live),
        manual=bool(stored.get("manual")),
    )


@router.get("", response_model=list[Group], summary="Named project groups")
def list_groups():
    """Every stored name with how much of it is in the current scan, so a group whose
    members are all gone is visible as such."""
    store = stores.group_store().load()
    live = _live_keys()
    return [_as_group(gid, stored, live)
            for gid, stored in sorted(store.get("groups", {}).items())]


def _claimed(gid, seeded):
    """The group after the next snapshot read has claimed its seed's project (callers
    force that read first); the seed alone when there is no scan."""
    return stores.group_store().load().get("groups", {}).get(gid) or seeded


@router.post("", response_model=Group, status_code=status.HTTP_201_CREATED,
             summary="Name a project")
def create_group(request: GroupCreate):
    """Seed a new group with one member key; its project inherits the name. 409 if the
    member is already in a named group."""
    group_store = stores.group_store()
    store = group_store.load()
    groups = store.setdefault("groups", {})

    for gid, stored in groups.items():
        if request.member_key in stored.get("members", []):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"{request.member_key} is already in group "
                       f"{gid} (\"{stored.get('name')}\"). Rename that group, or "
                       "pick a member that is not in one.")

    live = _live_keys()
    if live and request.member_key not in live:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{request.member_key} is not in the current scan. A member "
                   "key looks like '<service>:<region>:<resource_id>' - copy one "
                   "from the table.")

    gid = _next_gid(store)
    seeded = {"name": request.name, "members": [request.member_key]}
    groups[gid] = seeded
    group_store.save(store)
    stores.snapshot_store().invalidate()

    # Re-read, because one key is not yet the group - see _claimed.
    live = _live_keys()
    return _as_group(gid, _claimed(gid, seeded), live)


@router.post("/manual", response_model=Group, status_code=status.HTTP_201_CREATED,
             summary="Group specific resources together")
def create_manual_group(request: ManualGroupCreate):
    """Force a multi-select into one new manual group, whatever detection grouped. Its
    stored membership IS the group from now on."""
    group_store = stores.group_store()
    store = group_store.load()
    groups = store.setdefault("groups", {})

    keys = sorted(set(request.member_keys))
    if len(keys) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="a manual group needs at least two distinct member keys - "
                   "a single one has a project to claim, via POST /api/groups.")

    conflicts = _member_conflicts(keys, groups)
    if conflicts:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=_conflict_detail(conflicts))

    live = _live_keys()
    missing = [k for k in keys if live and k not in live]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"not in the current scan: {', '.join(missing)}. A member "
                   "key looks like '<service>:<region>:<resource_id>' - copy "
                   "one from the table.")

    tag_derived = sorted(_tag_derived_keys() & set(keys))
    if tag_derived:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{', '.join(tag_derived)} already carr{'y' if len(tag_derived) > 1 else 'ies'} "
                   "a real AWS Project tag - edit the tag in AWS to change its "
                   "group, manual grouping can't override it.")

    gid = _next_gid(store)
    groups[gid] = {"name": request.name, "members": keys, "manual": True,
                   "updated_at": fmt_dt(now())}
    group_store.save(store)
    stores.snapshot_store().invalidate()

    return _as_group(gid, groups[gid], live)


@router.post("/{gid}/members", response_model=Group, summary="Add resources to a group")
def add_group_members(gid: str, request: GroupMembersAdd):
    """Fold a multi-select into an existing named group, which becomes manual: its
    membership is exactly its stored list from now on."""
    group_store = stores.group_store()
    store = group_store.load()
    groups = store.setdefault("groups", {})
    if gid not in groups:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"No group {gid}.")

    keys = sorted(set(request.member_keys))
    conflicts = _member_conflicts(keys, groups, exclude_gid=gid)
    if conflicts:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=_conflict_detail(conflicts))

    live = _live_keys()
    missing = [k for k in keys if live and k not in live]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"not in the current scan: {', '.join(missing)}. A member "
                   "key looks like '<service>:<region>:<resource_id>' - copy "
                   "one from the table.")

    tag_derived = sorted(_tag_derived_keys() & set(keys))
    if tag_derived:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{', '.join(tag_derived)} already carr{'y' if len(tag_derived) > 1 else 'ies'} "
                   "a real AWS Project tag - edit the tag in AWS to change its "
                   "group, manual grouping can't override it.")

    groups[gid]["members"] = sorted(set(groups[gid].get("members", [])) | set(keys))
    groups[gid]["manual"] = True
    groups[gid]["updated_at"] = fmt_dt(now())
    group_store.save(store)
    stores.snapshot_store().invalidate()

    return _as_group(gid, groups[gid], live)


@router.put("/{gid}", response_model=Group, summary="Rename a group")
def rename_group(gid: str, request: GroupRename):
    group_store = stores.group_store()
    store = group_store.load()
    groups = store.setdefault("groups", {})
    if gid not in groups:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"No group {gid}.")

    groups[gid]["name"] = request.name
    group_store.save(store)
    # Visible on the next snapshot read, which resets scan-applied names first.
    stores.snapshot_store().invalidate()

    return _as_group(gid, groups[gid], _live_keys())


@router.delete("/{gid}", status_code=status.HTTP_204_NO_CONTENT,
               summary="Forget a name")
def delete_group(gid: str):
    """Drop the name; the project returns to its default name. The only undo, since
    stored membership only ever grows."""
    group_store = stores.group_store()
    store = group_store.load()
    groups = store.setdefault("groups", {})
    if gid not in groups:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"No group {gid}.")
    del groups[gid]
    group_store.save(store)
    stores.snapshot_store().invalidate()
