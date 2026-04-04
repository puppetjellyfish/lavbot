"""
Lightweight checks for local-provider URL and provider normalization.
Run with: python test_local_provider_config.py
"""

import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config import normalize_local_api_base_url, resolve_local_provider_kind


def test_normalize_local_api_base_url():
    assert normalize_local_api_base_url("localhost:1234/v1/") == "http://localhost:1234/v1"
    assert normalize_local_api_base_url(" http://127.0.0.1:11434/ ") == "http://127.0.0.1:11434"


def test_resolve_local_provider_kind():
    assert resolve_local_provider_kind("lmstudio", "http://localhost:1234/v1") == "openai"
    assert resolve_local_provider_kind("openai-compatible", "http://localhost:1234/v1") == "openai"
    assert resolve_local_provider_kind("auto", "http://localhost:11434") == "ollama"


if __name__ == "__main__":
    test_normalize_local_api_base_url()
    test_resolve_local_provider_kind()
    print("✅ Local provider config checks passed")
