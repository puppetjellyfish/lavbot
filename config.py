import os
from dotenv import load_dotenv

from data_paths import USER_ENV_PATH, ensure_userdata_dirs
from user_db import get_persona_for_user, get_setting, is_allowed_user

ensure_userdata_dirs()
load_dotenv(dotenv_path=str(USER_ENV_PATH))
load_dotenv()

# -----------------------------------------------------------------------------
# Token / secret accessors (stored in user.db, but fall back to env vars for
# backwards compatibility).
# -----------------------------------------------------------------------------

def get_discord_token() -> str:
    return get_setting("DiscordToken") or os.getenv("DISCORD_TOKEN") or ""


def get_openweather_key() -> str:
    return get_setting("OPENWEATHER_KEY") or os.getenv("OPENWEATHER_KEY") or ""


def get_news_key() -> str:
    return get_setting("NEWS_API_KEY") or os.getenv("NEWS_API_KEY") or ""


OLLAMA_DEFAULT_HOST = "localhost"
OLLAMA_DEFAULT_PORT = "11434"
LOCAL_PROVIDER_DEFAULT = "auto"
LOCAL_API_BASE_URL_DEFAULT = f"http://{OLLAMA_DEFAULT_HOST}:{OLLAMA_DEFAULT_PORT}"

OPENAI_COMPATIBLE_PROVIDER_ALIASES = {
    "openai": "openai",
    "openai-compatible": "openai",
    "openai_compatible": "openai",
    "lmstudio": "openai",
    "lm-studio": "openai",
    "lm studio": "openai",
    "vllm": "openai",
    "llamacpp": "openai",
    "llama.cpp": "openai",
    "koboldcpp": "openai",
    "oobabooga": "openai",
    "text-generation-webui": "openai",
    "textgen": "openai",
}


def normalize_local_api_base_url(url: str | None) -> str:
    """Return a normalized local API base URL with scheme and no trailing slash."""
    raw = (url or "").strip()
    if not raw:
        return LOCAL_API_BASE_URL_DEFAULT
    if "://" not in raw:
        raw = f"http://{raw}"
    return raw.rstrip("/")


def get_local_api_base_url() -> str:
    """Return the configured local AI provider base URL.

    Prefers the newer LOCAL_API_BASE_URL setting, while still honoring the
    legacy OLLAMA_HOST / OLLAMA_PORT values for backwards compatibility.
    """
    explicit_url = get_setting("LOCAL_API_BASE_URL") or os.getenv("LOCAL_API_BASE_URL")
    if explicit_url:
        return normalize_local_api_base_url(explicit_url)

    host = get_setting("OLLAMA_HOST") or os.getenv("OLLAMA_HOST") or OLLAMA_DEFAULT_HOST
    port = get_setting("OLLAMA_PORT") or os.getenv("OLLAMA_PORT") or OLLAMA_DEFAULT_PORT
    return normalize_local_api_base_url(f"http://{host}:{port}")


def get_local_provider_name() -> str:
    """Return the configured local provider label."""
    return (get_setting("LOCAL_PROVIDER") or os.getenv("LOCAL_PROVIDER") or LOCAL_PROVIDER_DEFAULT).strip() or LOCAL_PROVIDER_DEFAULT


def resolve_local_provider_kind(provider_name: str | None = None, base_url: str | None = None) -> str:
    """Collapse provider labels into the API family used by the code."""
    provider = (provider_name or "").strip().lower()
    normalized_url = normalize_local_api_base_url(base_url)

    if provider in {"", "auto"}:
        if normalized_url.endswith("/v1") or "/v1/" in normalized_url or ":1234" in normalized_url:
            return "openai"
        return "ollama"

    if provider == "ollama":
        return "ollama"

    return OPENAI_COMPATIBLE_PROVIDER_ALIASES.get(provider, "openai")


def get_local_provider_kind() -> str:
    """Return the resolved API family for the active local provider."""
    return resolve_local_provider_kind(get_local_provider_name(), get_local_api_base_url())


def get_ollama_base_url() -> str:
    """Backward-compatible alias for the local provider base URL."""
    return get_local_api_base_url()


# -----------------------------------------------------------------------------
# User/permission helpers
# -----------------------------------------------------------------------------

def who_is(user_id: int) -> str:
    """Return a persona string for a user ID (e.g. ally / muggy)."""
    persona = get_persona_for_user(user_id)
    return persona or "someone else"


# -----------------------------------------------------------------------------
# Application metadata (no PII)
# -----------------------------------------------------------------------------

TIMEZONE = "America/Vancouver"
