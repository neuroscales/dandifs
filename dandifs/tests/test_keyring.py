"""Tests for lazy auth/token resolution order."""

import dandifs._keyring as kr
from dandifs._keyring import resolve_token


def test_explicit_wins(monkeypatch):
    monkeypatch.setenv("DANDI_API_KEY", "from-env")
    assert resolve_token("dandi", explicit="explicit") == "explicit"


def test_env_dandi_api_key(monkeypatch):
    monkeypatch.setenv("DANDI_API_KEY", "env-token")
    monkeypatch.setattr(kr, "keyring_lookup", lambda name: "kr-token")
    assert resolve_token("dandi") == "env-token"


def test_per_instance_env(monkeypatch):
    monkeypatch.delenv("DANDI_API_KEY", raising=False)
    monkeypatch.setenv("EMBER_API_KEY", "ember-token")
    assert resolve_token("ember") == "ember-token"


def test_instance_name_normalized(monkeypatch):
    monkeypatch.delenv("DANDI_API_KEY", raising=False)
    monkeypatch.setenv("DANDI_STAGING_API_KEY", "staging-token")
    assert resolve_token("dandi-staging") == "staging-token"


def test_keyring_last(monkeypatch):
    monkeypatch.delenv("DANDI_API_KEY", raising=False)
    monkeypatch.setattr(kr, "keyring_lookup", lambda name: "kr-token")
    assert resolve_token("dandi") == "kr-token"


def test_keyring_skipped_when_disabled(monkeypatch):
    monkeypatch.delenv("DANDI_API_KEY", raising=False)
    monkeypatch.setattr(kr, "keyring_lookup", lambda name: "kr-token")
    assert resolve_token("dandi", use_keyring=False) is None


def test_none_when_nothing_configured(monkeypatch):
    monkeypatch.delenv("DANDI_API_KEY", raising=False)
    monkeypatch.setattr(kr, "keyring_lookup", lambda name: None)
    assert resolve_token("dandi") is None


def test_keyring_import_failure_is_silent(monkeypatch):
    # Simulate keyring not installed: lookup returns None, never raises.
    monkeypatch.delenv("DANDI_API_KEY", raising=False)
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "keyring":
            raise ImportError("no keyring")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert kr.keyring_lookup("dandi") is None
