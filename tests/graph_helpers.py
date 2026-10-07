"""Shared helpers for the graph tests: connection-type isolation and the real pipeline."""

import contextlib
import io

from .fakes import audit


def quiet(fn, *args, **kwargs):
    """Run something that prints a summary, discarding stdout."""
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args, **kwargs)


def only(conn_type, state=audit.CONN_ON):
    """Everything off except conn_type, so nothing else can form the link."""
    return only_these({conn_type: state})


def only_these(overrides):
    """As only(), for the few tests that need two types active at once."""
    return audit.connection_states(
        overrides,
        base={cid: audit.CONN_OFF for cid in audit.CONNECTION_TYPES_BY_ID},
    )


def graph_for(rows, **grouping_kwargs):
    """The full pipeline a real run performs, in the real order."""
    edge_links = audit.resolve_edges(rows)
    info = quiet(audit.apply_project_grouping, rows, edge_links=edge_links,
                 **grouping_kwargs)
    return audit.build_graph_data(rows, info["grouping_links"]), edge_links, info


def edge_between(graph, source, target):
    for e in graph["edges"]:
        if e["source"] == source and e["target"] == target:
            return e
    return None


def node_ids(graph):
    return {n["id"] for n in graph["nodes"]}
