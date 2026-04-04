"""
Lightweight checks for local-provider URL and provider normalization.
Run with: python test_local_provider_config.py
"""

import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config import get_local_model, normalize_local_api_base_url, resolve_local_provider_kind


def test_normalize_local_api_base_url():
    assert normalize_local_api_base_url("localhost:1234/v1/") == "http://localhost:1234/v1"
    assert normalize_local_api_base_url(" http://127.0.0.1:11434/ ") == "http://127.0.0.1:11434"


def test_resolve_local_provider_kind():
    assert resolve_local_provider_kind("lmstudio", "http://localhost:1234/v1") == "openai"
    assert resolve_local_provider_kind("openai-compatible", "http://localhost:1234/v1") == "openai"
    assert resolve_local_provider_kind("auto", "http://localhost:11434") == "ollama"


def test_get_local_model_prefers_shared_model():
    previous_local = os.environ.get("LOCAL_MODEL")
    previous_chat = os.environ.get("CHAT_MODEL")
    previous_vision = os.environ.get("VISION_MODEL")

    try:
        os.environ["LOCAL_MODEL"] = "qwen3.5-moe"
        os.environ["CHAT_MODEL"] = "chat-only-model"
        os.environ["VISION_MODEL"] = "vision-only-model"

        assert get_local_model("chat") == "qwen3.5-moe"
        assert get_local_model("vision") == "qwen3.5-moe"
    finally:
        if previous_local is None:
            os.environ.pop("LOCAL_MODEL", None)
        else:
            os.environ["LOCAL_MODEL"] = previous_local

        if previous_chat is None:
            os.environ.pop("CHAT_MODEL", None)
        else:
            os.environ["CHAT_MODEL"] = previous_chat

        if previous_vision is None:
            os.environ.pop("VISION_MODEL", None)
        else:
            os.environ["VISION_MODEL"] = previous_vision


if __name__ == "__main__":
    test_normalize_local_api_base_url()
    test_resolve_local_provider_kind()
    test_get_local_model_prefers_shared_model()
    print("✅ Local provider config checks passed")
