"""The service must refuse to start without a usable OpenRouter key.

It used to log a warning and carry on: `/health` answered `UP` with `api_key_configured: false`
beside it, the queue handed it work, and every call failed — each burning its retry budget (E36)
before the job dead-lettered. A missing key was indistinguishable from a broken model.

Only the key's *shape* is checked here. Whether it authenticates is OpenRouter's to say; the
point is to fail at startup rather than one expensive job at a time.
"""

from __future__ import annotations

import pytest

from app.settings import Settings, api_key_problem

REAL_LOOKING = "sk-or-v1-" + "a" * 64


def _settings(key: str) -> Settings:
    return Settings(openrouter_api_key=key)


def test_a_real_looking_key_is_accepted():
    assert api_key_problem(_settings(REAL_LOOKING)) is None


@pytest.mark.parametrize("key", ["", "   "])
def test_missing_key_is_reported(key):
    problem = api_key_problem(_settings(key))
    assert problem is not None
    assert ".env" in problem


def test_the_env_example_placeholder_is_caught():
    """Copying .env.example and forgetting to fill it in is the likeliest way to get here."""
    problem = api_key_problem(_settings("sk-or-v1-REPLACE_ME"))
    assert problem is not None
    assert "placeholder" in problem.lower()


def test_a_key_from_the_wrong_provider_is_caught():
    """CLAUDE.md constraint 1: this service talks to OpenRouter and nothing else."""
    problem = api_key_problem(_settings("sk-ant-api03-" + "b" * 40))
    assert problem is not None
    assert "sk-or-" in problem


def test_the_message_never_contains_the_key():
    """Diagnostics get logged. A key that reaches a log has to be treated as compromised."""
    secret = "sk-ant-" + "S3CRET" * 8
    problem = api_key_problem(_settings(secret))
    assert problem is not None
    assert secret not in problem
    assert "S3CRET" not in problem
