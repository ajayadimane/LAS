"""Regression tests for the three test-isolation guards in conftest.py.

Disclosed incident (PROGRESS.md milestone 5): a test's Settings(...) call
didn't explicitly null dpr_agentic_gemini_api_key, Settings read the real
.env for that unset field, and the real machine's .env had a real, working
Gemini key -- so the test made several real, billed calls to
generativelanguage.googleapis.com. These tests prove each of the three
guards conftest.py now installs actually does what it claims, so a
regression in any one of them is caught here rather than in another
accidental real API bill.
"""

import socket
from pathlib import Path

import httpx
import pytest
from dotenv import dotenv_values

from app.core.config import Settings
from app.services.dpr_agentic.gemini import GeminiAgentClient

_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


def test_settings_never_reads_the_real_dotenv():
    """Settings() with nothing explicitly passed must not pick up whatever
    is in the real .env -- regardless of what that file currently
    contains, including a real Gemini key."""
    assert Settings.model_config.get("env_file") is None
    settings = Settings()
    assert settings.dpr_agentic_gemini_api_key is None


def test_a_real_non_loopback_connection_is_blocked():
    with pytest.raises(RuntimeError, match="blocked during tests"):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect(("8.8.8.8", 53))


def test_loopback_connections_are_not_blocked():
    """The throwaway test Postgres is loopback -- the guard must let that
    class of address through (whether or not anything is listening is a
    separate question from whether the guard itself intervenes)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.2)
    try:
        s.connect(("127.0.0.1", 1))
    except RuntimeError:
        pytest.fail("loopback connection was blocked by the network guard")
    except OSError:
        pass  # refused/timed out is fine -- the guard let it through


def test_constructing_a_gemini_client_with_the_real_dotenv_key_fails():
    if not _ENV_PATH.exists():
        pytest.skip("no .env on this machine to compare against")
    real_key = dotenv_values(_ENV_PATH).get("DPR_AGENTIC_GEMINI_API_KEY")
    if not real_key:
        pytest.skip("no DPR_AGENTIC_GEMINI_API_KEY set in .env on this machine")
    with pytest.raises(RuntimeError, match="real DPR_AGENTIC_GEMINI_API_KEY"):
        GeminiAgentClient(httpx.AsyncClient(), real_key, "gemini-2.5-flash")


def test_constructing_a_gemini_client_with_an_explicit_fake_key_is_fine():
    client = GeminiAgentClient(
        httpx.AsyncClient(), "test-gemini-key", "gemini-2.5-flash"
    )
    assert client.api_key == "test-gemini-key"
