"""Which agent this image was built with: AUDIT_AGENT_PROVIDER, a build argument the
Dockerfile also sets as ENV. "none" builds the image without the agent's packages."""

import importlib.util
import os

PROVIDER_ENV = "AUDIT_AGENT_PROVIDER"
NONE = "none"
DEFAULT_PROVIDER = "openai"

# Each provider's LangChain package: the only one the image installs for it.
PROVIDER_PACKAGES = {
    "openai": "langchain_openai",
    "anthropic": "langchain_anthropic",
    "bedrock": "langchain_aws",
}

# Every provider needs these: the agent loop and the document index.
_COMMON_PACKAGES = ("langchain", "chromadb")


def provider() -> str:
    """The provider named at build time; unset (outside Docker) means the default."""
    return os.environ.get(PROVIDER_ENV, DEFAULT_PROVIDER).strip().lower()


def _is_installed(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def not_installed_reason() -> str:
    """Why this image cannot run the agent at all, or "" if it can. Finds the
    packages without importing them, so it is cheap enough for every status call."""
    name = provider()
    if name == NONE:
        return (f"The agent is not installed: this image was built with "
                f"{PROVIDER_ENV}=none. To add it, set {PROVIDER_ENV} to openai, "
                f"anthropic or bedrock in .env, then `docker compose up --build`.")
    if name not in PROVIDER_PACKAGES:
        return (f"Unknown provider {name!r} in {PROVIDER_ENV}. Known providers: "
                f"{', '.join(PROVIDER_PACKAGES)}, {NONE}.")
    missing = [m for m in (*_COMMON_PACKAGES, PROVIDER_PACKAGES[name])
               if not _is_installed(m)]
    if missing:
        return (f"The agent's packages for {name} are not installed "
                f"({', '.join(missing)}). Rebuild the image: `docker compose up --build`.")
    return ""
