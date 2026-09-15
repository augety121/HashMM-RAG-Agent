from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _renderer():
    path = ROOT / "scripts" / "render-turn-config.py"
    spec = importlib.util.spec_from_file_location("hashmm_turn_renderer", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_turn_template_renders_rest_secret_and_security_boundaries():
    module = _renderer()
    template = (ROOT / "deploy" / "turn" / "turnserver.conf.template").read_text("utf-8")
    rendered = module.render(
        template, secret="s" * 48, realm="turn.example.com", external_ip="203.0.113.10",
        cert="/certs/fullchain.pem", pkey="/certs/privkey.pem",
    )
    assert "use-auth-secret" in rendered
    assert "static-auth-secret=" + "s" * 48 in rendered
    assert "min-port=49160" in rendered and "max-port=49200" in rendered
    assert "denied-peer-ip=127.0.0.0-127.255.255.255" in rendered
    assert "__HASHMM_" not in rendered


def test_turn_renderer_rejects_short_or_multiline_values():
    module = _renderer()
    template = (ROOT / "deploy" / "turn" / "turnserver.conf.template").read_text("utf-8")
    with pytest.raises(ValueError):
        module.render(template, secret="short", realm="turn.example.com", external_ip="203.0.113.10",
                      cert="/certs/fullchain.pem", pkey="/certs/privkey.pem")
    with pytest.raises(ValueError):
        module.render(template, secret="s" * 48, realm="turn.example.com\nmalicious", external_ip="203.0.113.10",
                      cert="/certs/fullchain.pem", pkey="/certs/privkey.pem")


def test_turn_compose_requires_pinned_operator_input_and_is_read_only():
    compose = (ROOT / "deploy" / "turn" / "compose.yaml").read_text("utf-8")
    assert "HASHMM_COTURN_IMAGE:?" in compose
    assert "read_only: true" in compose
    assert "no-new-privileges:true" in compose
    assert "network_mode: host" in compose
    assert "coturn/coturn:latest" not in compose
