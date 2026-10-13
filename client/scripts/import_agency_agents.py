"""Vendor the complete Agency Agents catalog as inert HashMM role assets.

The imported Markdown is untrusted prompt data.  This script deliberately
does not translate role text into tools, permissions, hooks, or executable
plugins.  Runtime code loads only the small generated index until a user
selects a concrete role.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "hashmm" / "agent" / "role_catalog" / "agency-agents"
INDEX = DEST / "index.json"
SOURCE_PREFIX = "agency-agents-main/"
FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)


def _field(block: str, key: str) -> str:
    match = re.search(rf"(?m)^{re.escape(key)}:\s*(.+?)\s*$", block)
    if not match:
        return ""
    value = match.group(1).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'\"', "'"}:
        value = value[1:-1]
    return value.strip()


def import_zip(source: Path) -> int:
    if not source.is_file():
        raise SystemExit(f"Agency Agents archive not found: {source}")
    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)

    records: list[dict[str, object]] = []
    with zipfile.ZipFile(source) as archive:
        names = set(archive.namelist())
        license_name = SOURCE_PREFIX + "LICENSE"
        if license_name not in names:
            raise SystemExit("Archive has no root LICENSE; refusing to vendor it")
        (DEST / "LICENSE.upstream").write_bytes(archive.read(license_name))

        for name in sorted(names):
            relative = name.removeprefix(SOURCE_PREFIX)
            parts = Path(relative).parts
            if (
                not name.startswith(SOURCE_PREFIX)
                or len(parts) < 2
                or parts[0].startswith(".")
                or parts[-1].lower() == "readme.md"
                or not parts[-1].lower().endswith(".md")
            ):
                continue
            raw = archive.read(name)
            text = raw.decode("utf-8")
            front = FRONT_MATTER.match(text)
            if not front:
                continue
            title = _field(front.group(1), "name")
            description = _field(front.group(1), "description")
            if not title or not description:
                continue
            category, filename = parts[0], parts[-1]
            slug = Path(filename).stem.lower()
            identity_parts = [part.lower() for part in parts[:-1]] + [slug]
            role_id = "agency." + ".".join(identity_parts)
            target_dir = DEST.joinpath(*parts[:-1])
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / filename
            target.write_bytes(raw)
            records.append({
                "id": role_id,
                "name": title,
                "skill": description,
                "description": description,
                "category": category,
                "source": "agency-agents",
                "license": "MIT",
                "path": "/".join(parts),
                "sha256": hashlib.sha256(raw).hexdigest(),
            })

    if len(records) < 250:
        raise SystemExit(f"Parsed only {len(records)} roles; refusing incomplete import")
    manifest = {
        "schema": "hashmm.agent-catalog.v1",
        "source": "https://github.com/msitarzewski/agency-agents",
        "license": "MIT",
        "archive_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "count": len(records),
        "roles": records,
    }
    INDEX.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Imported {len(records)} roles into {DEST}")
    return len(records)


if __name__ == "__main__":
    archive = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"D:\edge downloads\agency-agents-main.zip")
    import_zip(archive)
