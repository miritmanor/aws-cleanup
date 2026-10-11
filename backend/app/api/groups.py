"""Naming project groups without re-scanning: write the group store, then drop the
snapshot cache so the next read applies it. One seeded member names its whole project."""

import logging
import re

from fastapi import APIRouter, HTTPException, status

from aws_resource_audit.config import now
from aws_resource_audit.naming.additions import add_to_group, add_to_project, check_addable
from aws_resource_audit.naming.group_names import listed_keys
from aws_resource_audit.naming.merges import EditRefused, forget_group, merge_projects
from aws_resource_audit.present.format import fmt_dt
from aws_resource_audit.rows import member_key

from .. import stores
from ..schemas import (
    Group,
    GroupCreate,
    GroupMember,
    GroupMembersAdd,
    GroupRename,
    ManualGroupCreate,
    ProjectMembersAdd,
    ProjectMerge,
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


def _scan_rows():
    """The current scan's resolved rows, or an empty list when there is no scan."""
    try:
        return stores.snapshot_store().load().rows
    except Exception:                                            # noqa: BLE001
        # An empty list turns OFF the "in the current scan" and AWS-tag checks
        # at every call site below - it does not merely shrink the answer.
        logger.warning("could not read the snapshot; member-key validation is "
                       "disabled for this request", exc_info=True)
        return []


def _live_keys():
    """Member keys in the current scan, or an empty set when there is no scan."""
    return {member_key(row) for row in _scan_rows()}


def _refused(e):
    code = status.HTTP_409_CONFLICT if e.conflict else status.HTTP_400_BAD_REQUEST
    return HTTPException(status_code=code, detail=str(e))


def _as_group(gid, stored, live):
    members = sorted(listed_keys(stored))
    return Group(
        id=gid,
        name=stored.get("name", ""),
        updated_at=stored.get("updated_at"),
        members=[GroupMember(member_key=k, present=k in live) for k in members],
        live_members=sum(1 for k in members if k in live),
        manual=bool(stored.get("manual")),
        merged_into=stored.get("merged_into"),
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
        if request.member_key in listed_keys(stored):
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

    rows = _scan_rows()
    try:
        check_addable(groups, rows, keys)
    except EditRefused as e:
        raise _refused(e) from e

    gid = _next_gid(store)
    groups[gid] = {"name": request.name, "members": keys, "manual": True,
                   "updated_at": fmt_dt(now())}
    group_store.save(store)
    stores.snapshot_store().invalidate()

    return _as_group(gid, groups[gid], {member_key(r) for r in rows})


@router.post("/merge", response_model=Group, summary="Merge one project into another")
def merge_into(request: ProjectMerge):
    """Show the source project's resources under the target's name from now on. Forgetting
    the target's name splits them apart again."""
    try:
        rows = stores.snapshot_store().load().rows
    except Exception as e:                                      # noqa: BLE001
        logger.warning("could not read the snapshot; merge refused", exc_info=True)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="No scan to merge projects in.") from e

    group_store = stores.group_store()
    store = group_store.load()
    try:
        gid = merge_projects(store, rows, request.source_project_id,
                             request.target_project_id, lambda: _next_gid(store))
    except EditRefused as e:
        raise _refused(e) from e
    group_store.save(store)
    stores.snapshot_store().invalidate()

    return _as_group(gid, store["groups"][gid], _live_keys())


@router.post("/add-to-project", response_model=Group, summary="Add resources to a project")
def add_to_project_members(request: ProjectMembersAdd):
    """Add a multi-select to any project, saving its current name first if it only has a
    default one. The project keeps growing on its own."""
    rows = _scan_rows()
    group_store = stores.group_store()
    store = group_store.load()
    try:
        gid = add_to_project(store, rows, request.project_id, request.member_keys,
                             lambda: _next_gid(store))
    except EditRefused as e:
        raise _refused(e) from e
    group_store.save(store)
    stores.snapshot_store().invalidate()

    return _as_group(gid, store["groups"][gid], _live_keys())


@router.post("/{gid}/members", response_model=Group, summary="Add resources to a group")
def add_group_members(gid: str, request: GroupMembersAdd):
    """Add a multi-select to a saved name. A manual group lists them as members; any
    other keeps growing on its own and lists them as extra_members."""
    group_store = stores.group_store()
    store = group_store.load()
    groups = store.setdefault("groups", {})
    if gid not in groups:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"No group {gid}.")

    keys = sorted(set(request.member_keys))
    rows = _scan_rows()
    try:
        check_addable(groups, rows, keys, exclude_gid=gid)
    except EditRefused as e:
        raise _refused(e) from e

    add_to_group(groups, gid, keys)
    group_store.save(store)
    stores.snapshot_store().invalidate()

    return _as_group(gid, groups[gid], {member_key(r) for r in rows})


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
    """Drop the name; the project returns to its default name, and projects merged into
    it split back apart. The only undo, since stored membership only ever grows."""
    group_store = stores.group_store()
    store = group_store.load()
    groups = store.setdefault("groups", {})
    if gid not in groups:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"No group {gid}.")
    forget_group(store, gid)
    group_store.save(store)
    stores.snapshot_store().invalidate()
