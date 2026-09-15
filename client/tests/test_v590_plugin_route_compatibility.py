"""V590 plugin API compatibility regressions."""
from __future__ import annotations

from pathlib import Path


def test_plugin_routes_keep_canonical_and_v580_compatibility_paths():
    source = (Path(__file__).parents[1] / "hashmm" / "api" / "routes" / "system.py").read_text(
        encoding="utf-8"
    )
    for path in (
        "/plugins",
        "/plugins/{name}/trust",
        "/plugins/{name}/load",
        "/plugins/{name}/revoke",
        "/plugins/tools",
    ):
        assert f'"{path}"' in source
        # V580 desktop clients called the accidental /api/system prefix. Keep
        # aliases until those installed clients have aged out.
        assert f'"/system{path}"' in source


def test_plugin_compatibility_aliases_are_hidden_from_openapi():
    source = (Path(__file__).parents[1] / "hashmm" / "api" / "routes" / "system.py").read_text(
        encoding="utf-8"
    )
    aliases = [
        line.strip() for line in source.splitlines()
        if '"/system/plugins' in line
    ]
    assert len(aliases) == 5
    assert all("include_in_schema=False" in line for line in aliases)
