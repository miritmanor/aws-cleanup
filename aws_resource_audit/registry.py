"""The connection-type registry: on, report-only or off for every inferable link.
Read through connection_state() and friends; importing CONNECTION_STATES freezes a copy."""

import fnmatch
from contextlib import contextmanager

from .connection_types import CONNECTION_TYPES
from .connection_types.vocabulary import (
    ANY_TARGET,
    CONF_AUTHORITATIVE,
    CONN_OFF,
    CONN_ON,
)
from .errors import AuditError

CONNECTION_TYPES_BY_ID = {ct.id: ct for ct in CONNECTION_TYPES}

# Live per-id state, replaced by set_connection_states(). Tests use the
# connection_states() context manager so state never leaks between cases.
CONNECTION_STATES = {ct.id: ct.default_state for ct in CONNECTION_TYPES}


def set_connection_states(states):
    """Install the resolved states for the rest of the run. Call this rather than
    assigning: add_edge() reads THIS module's global."""
    global CONNECTION_STATES
    CONNECTION_STATES = dict(states)


def default_connection_states():
    return {ct.id: ct.default_state for ct in CONNECTION_TYPES}


def connection_state(conn_type):
    """State for a registered id. An unknown id raises: it is a typo."""
    try:
        return CONNECTION_STATES[conn_type]
    except KeyError:
        raise KeyError(
            f"unknown connection type {conn_type!r} - add it to CONNECTION_TYPES. "
            f"Known ids: {', '.join(sorted(CONNECTION_TYPES_BY_ID))}"
        ) from None


def detection_enabled(conn_type):
    """False only for "off" - i.e. don't even build the edge."""
    return connection_state(conn_type) != CONN_OFF


def feeds_grouping(conn_type):
    """True only for "on" - "report-only" is detected and displayed but must
    never pull two resources into the same project_group."""
    return connection_state(conn_type) == CONN_ON


@contextmanager
def connection_states(overrides=None, base=None):
    """Temporarily swap the active connection-state map. Restores the previous
    map on exit even if the body raises, so tests can't leak state."""
    global CONNECTION_STATES
    previous = CONNECTION_STATES
    new_states = dict(base) if base is not None else default_connection_states()
    new_states.update(overrides or {})
    CONNECTION_STATES = new_states
    try:
        yield new_states
    finally:
        CONNECTION_STATES = previous


def expand_connection_patterns(patterns, label):
    """Resolve exact ids and fnmatch globs ("lambda.*") to ids. A pattern matching
    nothing raises, so a typo cannot silently leave a detection on."""
    resolved = set()
    for pattern in patterns:
        for value in (p.strip() for p in pattern.split(",")):
            if not value:
                continue
            matched = fnmatch.filter(CONNECTION_TYPES_BY_ID, value)
            if not matched:
                raise AuditError(
                    f"{label}: '{value}' matches no connection type. "
                    "Run with --debug to print the available ids."
                )
            resolved.update(matched)
    return resolved


def resolve_connection_states(overrides=None, authoritative_only=False,
                              label="audit_config.json: connection_types"):
    """Build the active state map from audit_config.json. authoritative_only is
    applied first, then each override in file order; a later key wins."""
    states = default_connection_states()
    if authoritative_only:
        for ct in CONNECTION_TYPES:
            if ct.confidence != CONF_AUTHORITATIVE:
                states[ct.id] = CONN_OFF
    for pattern, state in (overrides or {}).items():
        for cid in expand_connection_patterns([pattern], label):
            states[cid] = state
    return states


def format_connection_type_table(states=None):
    """Human-readable registry dump, printed by --debug."""
    states = states or CONNECTION_STATES
    lines = ["Connection types (state: on = detect + group, report-only = detect only, off = skip)", ""]
    for kind, heading in (("edge", "LINK DETECTION"), ("grouping", "SHARED-ATTRIBUTE GROUPING")):
        entries = [ct for ct in CONNECTION_TYPES if ct.kind == kind]
        if not entries:
            continue
        lines.append(heading)
        if kind == "grouping":
            lines.append("  (no connections text of their own - 'report-only' behaves as 'off')")
        width = max(len(ct.id) for ct in entries)
        for ct in entries:
            target = ct.targets[0] if len(ct.targets) == 1 else f"{len(ct.targets)} types"
            if ct.targets == (ANY_TARGET,):
                target = "-"
            lines.append(
                f"  {ct.id:<{width}}  {states.get(ct.id, ct.default_state):<11} "
                f"{ct.confidence:<17} {ct.source} -> {target}"
            )
            lines.append(f"  {'':<{width}}  {ct.mechanism}")
        lines.append("")
    return "\n".join(lines)
