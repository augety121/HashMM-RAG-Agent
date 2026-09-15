#!/usr/bin/env python
"""Re-encrypt stored API keys from legacy XOR to Fernet (idempotent).

After deploying the Fernet change, existing rows still DECRYPT fine (the
decryptor reads both formats). This script upgrades them at rest so nothing
remains XOR-"encrypted". Safe to run repeatedly: rows already in Fernet form
(prefixed ``f1:``) are skipped.

    python scripts/migrate_secrets_to_fernet.py            # migrate
    python scripts/migrate_secrets_to_fernet.py --dry-run  # report only

Requires the same HASHMM_SECRET the app uses (the Fernet key is derived from it).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    ap = argparse.ArgumentParser(description="Migrate stored secrets XOR → Fernet")
    ap.add_argument("--dry-run", action="store_true", help="只报告需迁移的行，不写库。")
    args = ap.parse_args()

    from hashmm.api.database import DB_PATH
    from hashmm.secrets_crypto import (
        decrypt_secret, encrypt_secret, is_legacy_ciphertext, fernet_available,
    )

    if not fernet_available():
        print("❌ cryptography 不可用，无法迁移到 Fernet。请先 `pip install cryptography`。")
        return 2
    if not Path(DB_PATH).exists():
        print(f"❌ 找不到数据库：{DB_PATH}")
        return 2

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, api_key_enc FROM models").fetchall()

    legacy = [(r["id"], r["api_key_enc"]) for r in rows if is_legacy_ciphertext(r["api_key_enc"])]
    total = len(rows)
    print(f"models 表共 {total} 行；需迁移（旧 XOR）{len(legacy)} 行。")

    if not legacy:
        print("✅ 没有需要迁移的行（都已是 Fernet 或为空）。")
        conn.close()
        return 0

    if args.dry_run:
        for mid, _ in legacy:
            print(f"  [dry-run] 待迁移 model id={mid}")
        conn.close()
        return 0

    migrated, failed = 0, 0
    for mid, enc in legacy:
        plain = decrypt_secret(enc)          # reads legacy XOR
        if not plain:
            # Either empty or undecryptable; skip rather than corrupt.
            failed += 1
            print(f"  ⚠️ 跳过 id={mid}（旧密文解不出，可能 HASHMM_SECRET 与当初不一致）")
            continue
        new_enc = encrypt_secret(plain)      # writes Fernet (f1:)
        conn.execute("UPDATE models SET api_key_enc=? WHERE id=?", (new_enc, mid))
        migrated += 1
    conn.commit()
    conn.close()

    print(f"\n✅ 迁移完成：{migrated} 行已转 Fernet" + (f"，{failed} 行跳过" if failed else ""))
    print("再次运行本脚本应显示『没有需要迁移的行』（幂等）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
