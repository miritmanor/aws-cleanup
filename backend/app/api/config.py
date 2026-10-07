"""Reading and writing audit_config.json, validated by the core's own load_settings(),
so the API and CLI accept exactly the same configs."""

from fastapi import APIRouter, HTTPException, status

from aws_resource_audit.connection_types import CONNECTION_TYPES
from aws_resource_audit.registry import resolve_connection_states

from .. import deps, stores
from ..schemas import ConfigResponse, ConfigUpdate, ConnectionTypeInfo

router = APIRouter(prefix="/api", tags=["config"])


def _connection_table(settings):
    """Every registered connection type with its resolved state (400 on an unknown key)."""
    states = resolve_connection_states(
        overrides=settings.connection_types,
        authoritative_only=settings.authoritative_only,
    )
    return [
        ConnectionTypeInfo(
            id=ct.id,
            kind=ct.kind,
            default_state=ct.default_state,
            state=states.get(ct.id, ct.default_state),
            source=ct.source,
            targets=list(ct.targets),
            mechanism=ct.mechanism,
            confidence=ct.confidence,
            description=ct.description,
        )
        for ct in CONNECTION_TYPES
    ]


@router.get("/config", response_model=ConfigResponse, summary="Current settings")
def get_config():
    store = stores.config_store()
    settings = deps.get_settings()
    return ConfigResponse(
        raw=store.read_raw(),
        path=store.path,
        exists=store.exists(),
        authoritative_only=settings.authoritative_only,
        graph_in_html=settings.graph_in_html,
        graph_all_nodes=settings.graph_all_nodes,
        bubbles_in_html=settings.bubbles_in_html,
        bubbles_default_metric=settings.bubbles_default_metric,
        snapshot_file=settings.snapshot_file,
        output_dir=settings.output_dir,
        assume_role=settings.assume_role,
        role_name=settings.role_name,
        connection_types=_connection_table(settings),
    )


@router.put("/config", response_model=ConfigResponse, summary="Replace settings")
def put_config(update: ConfigUpdate):
    """Replace audit_config.json whole (connection_types apply in file order), or reject
    it unchanged. output_dir cannot be set: the mounted volume decides it."""
    data = update.model_dump()

    # Resolve the connection-type keys BEFORE writing: load_settings checks values, not keys,
    # and a saved bad key would make every later read fail.
    resolve_connection_states(
        overrides=data.get("connection_types") or {},
        authoritative_only=data.get("authoritative_only", False),
    )
    stores.config_store().write(data)
    stores.reset()
    return get_config()


@router.get("/connection-types", response_model=list[ConnectionTypeInfo],
            summary="The connection-type registry")
def get_connection_types():
    """Every way the scan can infer a link, with its current state - including types
    the config file does not mention."""
    return _connection_table(deps.get_settings())
