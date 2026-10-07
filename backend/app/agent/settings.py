"""agent_config.json: the agent's standing decisions. Absent = defaults; unusable or
unknown keys are hard errors, like audit_config.json."""

import json
import os
from dataclasses import dataclass

from aws_resource_audit.errors import AuditError

# Read from the data directory beside audit_config.json (deps.agent_config_path()).
AGENT_CONFIG_FILENAME = "agent_config.json"


@dataclass
class AgentSettings:
    """Everything agent_config.json can say; the defaults are a fresh install's."""

    provider: str = "openai"
    model: str = "gpt-5.6-luna"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    chroma_dir: str = "agent_index"
    session_ttl_minutes: int = 240


_STR_KEYS = ("provider", "model", "embedding_model", "chroma_dir")


def load_agent_settings(path=AGENT_CONFIG_FILENAME) -> AgentSettings:
    """Read agent_config.json, or the defaults if absent. An unknown key is a hard error."""
    if not path or not os.path.exists(path):
        return AgentSettings()
    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        raise AuditError(f"{path}: could not be read ({e})")
    if not isinstance(data, dict):
        raise AuditError(f"{path}: expected a JSON object at the top level")

    known = set(AgentSettings().__dict__)
    unknown = sorted(set(data) - known)
    if unknown:
        raise AuditError(
            f"{path}: unknown setting(s) {', '.join(unknown)}. "
            f"Known settings: {', '.join(sorted(known))}")

    settings = AgentSettings()
    for key in _STR_KEYS:
        if key in data:
            if not isinstance(data[key], str) or not data[key]:
                raise AuditError(f"{path}: '{key}' must be a non-empty string, got {data[key]!r}")
            setattr(settings, key, data[key])

    if "session_ttl_minutes" in data:
        ttl = data["session_ttl_minutes"]
        if not isinstance(ttl, int) or isinstance(ttl, bool) or ttl <= 0:
            raise AuditError(f"{path}: 'session_ttl_minutes' must be a positive integer, got {ttl!r}")
        settings.session_ttl_minutes = ttl

    return settings
