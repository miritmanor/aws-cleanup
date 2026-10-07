"""Picking a chat model: the only place a provider name becomes an SDK call. The API key
is read from the file AGENT_API_KEY_FILE names (optional), never from the environment."""

import os

from aws_resource_audit.errors import AuditError

from .settings import AgentSettings

_PROVIDERS = ("openai", "anthropic", "bedrock")

# Bedrock is absent on purpose: it authenticates with the AWS credentials the
# container already has, so it needs no key of its own.
_NEEDS_API_KEY = ("openai", "anthropic")

_API_KEY_FILE_ENV = "AGENT_API_KEY_FILE"


def read_api_key() -> str:
    """The key in AGENT_API_KEY_FILE's file, or "". Unset, missing and empty all mean no key."""
    path = os.environ.get(_API_KEY_FILE_ENV)
    if not path:
        return ""
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def unavailable_reason(settings: AgentSettings) -> str:
    """Why the agent cannot answer, or "" if it can. A presence check only: a wrong
    key fails at the first turn instead."""
    if settings.provider not in _PROVIDERS:
        return (f"Unknown provider {settings.provider!r} in agent_config.json. "
                f"Known providers: {', '.join(_PROVIDERS)}")
    if settings.provider in _NEEDS_API_KEY and not read_api_key():
        return (f"No API key for {settings.provider}. Put the key in a file, "
                f"set AUDIT_LLM_API_KEY_FILE to its path (an .env file beside "
                f"docker-compose.yml is the usual place), then "
                f"`docker compose up --force-recreate`.")
    return ""


def get_chat_model(settings: AgentSettings):
    """A LangChain chat model for settings.provider/model. Imports are per branch, so
    only the configured provider's package is needed."""
    reason = unavailable_reason(settings)
    if reason:
        # AuditError, not the provider's exception: the user must fix this, and the
        # message says how.
        raise AuditError(reason)

    api_key = read_api_key()
    if settings.provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=settings.model, temperature=0, api_key=api_key)
    if settings.provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=settings.model, temperature=0, api_key=api_key)
    from langchain_aws import ChatBedrock
    return ChatBedrock(model_id=settings.model)
