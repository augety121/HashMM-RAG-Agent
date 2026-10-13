from types import SimpleNamespace

import base64
import hashlib
import hmac

from hashmm.api.remote_transport import (
    LEGACY_REMOTE_PROTOCOL,
    assess_transport_scope, dynamic_turn_configured, issue_ice_servers, load_ice_servers,
    remote_bootstrap_config, request_is_secure,
    turn_configuration_status,
)


def test_v4_bootstrap_is_canonical_and_never_derived_from_client(monkeypatch):
    monkeypatch.setenv("HASHMM_ENV", "production")
    monkeypatch.setenv("HASHMM_REQUIRE_SECURE_REMOTE", "1")
    config = remote_bootstrap_config("https://hashmm.hashlens.org")
    assert config["schema"] == "hashmm.remote-bootstrap.v4"
    assert config["protocol"] == "hashmm.remote.v4"
    assert config["api_base"] == "https://hashmm.hashlens.org"
    assert config["control_wss"] == "wss://hashmm.hashlens.org/api/remote/v4/ws"
    assert config["time_budgets"]["first_frame_ms"] == 8000
    assert len(config["config_revision"]) == 16


def test_v3_bootstrap_remains_an_explicit_one_release_compatibility_path(monkeypatch):
    monkeypatch.setenv("HASHMM_ENV", "production")
    config = remote_bootstrap_config("https://hashmm.hashlens.org", protocol=LEGACY_REMOTE_PROTOCOL)
    assert config["schema"] == "hashmm.remote-bootstrap.v3"
    assert config["protocol"] == "hashmm.remote.v3"
    assert config["control_wss"].endswith("/api/remote/v3/ws")


def test_v3_bootstrap_rejects_public_http_in_production(monkeypatch):
    monkeypatch.setenv("HASHMM_ENV", "production")
    try:
        remote_bootstrap_config("http://111.115.7.14:20014")
        assert False, "public HTTP must not become a remote control endpoint"
    except ValueError as exc:
        assert str(exc) == "canonical_public_https_url_missing"


def test_transport_scope_accepts_cloudflare_tunnel_only_via_trusted_peer(monkeypatch):
    monkeypatch.setenv("HASHMM_TRUSTED_PROXIES", "127.0.0.1/32")
    secure = assess_transport_scope({
        "scheme": "ws", "client": ("127.0.0.1", 50123),
        "headers": [(b"x-forwarded-proto", b"https"), (b"cf-ray", b"ray-one"),
                    (b"cf-connecting-ip", b"203.0.113.8")],
    })
    spoofed = assess_transport_scope({
        "scheme": "ws", "client": ("203.0.113.8", 50123),
        "headers": [(b"x-forwarded-proto", b"https")],
    })
    assert secure["secure"] is True
    assert secure["source"] == "trusted-forwarded-proto"
    assert secure["cloudflare"] is True
    assert spoofed["secure"] is False


def test_ice_policy_rejects_incomplete_turn_and_unknown_schemes():
    raw = '''[
      {"urls":"turn:relay.example:3478"},
      {"urls":"turns:relay.example:5349","username":"u","credential":"p"},
      {"urls":"https://not-an-ice-server"},
      "stun:stun.example:3478"
    ]'''
    assert load_ice_servers(raw) == [
        {"urls": "turns:relay.example:5349", "username": "u", "credential": "p"},
        {"urls": "stun:stun.example:3478"},
    ]


def test_ice_policy_falls_back_for_invalid_configuration():
    assert load_ice_servers("not-json") == [{"urls": "stun:stun.l.google.com:19302"}]
    assert load_ice_servers('[{"urls":"turn:relay.example"}]') == [{"urls": "stun:stun.l.google.com:19302"}]


def test_secure_transport_honors_reverse_proxy_header():
    secure = SimpleNamespace(headers={"x-forwarded-proto": "https"}, url=SimpleNamespace(scheme="http"),
                             client=SimpleNamespace(host="127.0.0.1"))
    insecure = SimpleNamespace(headers={}, url=SimpleNamespace(scheme="http"),
                               client=SimpleNamespace(host="127.0.0.1"))
    assert request_is_secure(secure)
    assert not request_is_secure(insecure)


def test_secure_transport_rejects_spoofed_forwarded_header(monkeypatch):
    monkeypatch.setenv("HASHMM_TRUSTED_PROXIES", "127.0.0.1/32")
    spoofed = SimpleNamespace(headers={"x-forwarded-proto": "https"}, url=SimpleNamespace(scheme="http"),
                              client=SimpleNamespace(host="203.0.113.9"))
    assert not request_is_secure(spoofed)


def test_dynamic_coturn_credentials_are_short_lived_and_owner_device_bound(monkeypatch):
    secret = "turn-secret-" + "x" * 40
    monkeypatch.setenv("HASHMM_TURN_SHARED_SECRET", secret)
    monkeypatch.setenv("HASHMM_TURN_URLS", '["turn:relay.example:3478?transport=udp","turns:relay.example:5349?transport=tcp"]')
    monkeypatch.setenv("HASHMM_TURN_CREDENTIAL_TTL", "600")
    servers = issue_ice_servers("owner-a", "device-a", now=1000)
    turn = servers[-1]
    assert turn["username"].startswith("1600:")
    expected = base64.b64encode(hmac.new(secret.encode(), turn["username"].encode(), hashlib.sha1).digest()).decode()
    assert turn["credential"] == expected
    assert issue_ice_servers("owner-a", "device-b", now=1000)[-1]["username"] != turn["username"]
    assert dynamic_turn_configured()
    assert turn_configuration_status()["dynamic_credentials"] is True


def test_short_or_incomplete_dynamic_turn_secret_never_mints_credentials(monkeypatch):
    monkeypatch.setenv("HASHMM_TURN_SHARED_SECRET", "short")
    monkeypatch.setenv("HASHMM_TURN_URLS", '["turn:relay.example:3478"]')
    servers = issue_ice_servers("owner-a", "device-a", now=1000)
    assert all("credential" not in server for server in servers)
    assert not dynamic_turn_configured()
