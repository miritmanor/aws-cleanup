"""Connection types: Shared-attribute signals: no connection text, they only union resources into a project."""

from .vocabulary import (
    ANY_TARGET,
    CONF_AUTHORITATIVE,
    ConnectionType,
    OWNERSHIP_OWNS,
    SEM_OWNS,
)

TYPES = (
    # --- Shared-attribute grouping signals: no connections text, only unions,
    # so "report-only" and "off" behave identically.
    ConnectionType(
        "group.shared-tag", ANY_TARGET, (ANY_TARGET,),
        "shared Project/App/Service/Environment tag value", CONF_AUTHORITATIVE,
        "Groups resources carrying the same grouping-tag value. The only grouping "
        "signal that reflects a deliberate human decision rather than an inference.",
        kind="grouping",
        semantics=SEM_OWNS, ownership=OWNERSHIP_OWNS,
),
)
