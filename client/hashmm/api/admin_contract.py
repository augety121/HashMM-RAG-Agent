"""Stable wire contracts shared by desktop and mobile administrator clients."""
from __future__ import annotations


def admin_user_wire(row: dict) -> dict:
    """Return the non-secret, type-stable member directory representation.

    SQLite stores some legacy ``created_at`` values as numeric Unix timestamps,
    while Supabase uses timestamp strings.  Clients should not lose the whole
    directory because one account uses the old storage type.
    """
    def text(key: str, default: str = "") -> str:
        value = row.get(key, default)
        return default if value is None else str(value)

    return {
        "id": text("id"),
        "username": text("username"),
        "display_name": text("display_name"),
        "role": text("role", "user") or "user",
        "created_at": text("created_at"),
        "identity_source": text("identity_source", "local") or "local",
        "email": text("email"),
        "last_sign_in_at": text("last_sign_in_at"),
    }
