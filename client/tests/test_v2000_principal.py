from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from hashmm.api.auth import principal_id


def test_principal_id_accepts_hashmm_and_supabase_shapes():
    assert principal_id({"uid": "sb_user-1", "sub": "user-1"}) == "sb_user-1"
    assert principal_id({"sub": "user-2"}) == "user-2"
    assert principal_id({"id": "legacy-user"}) == "legacy-user"


def test_principal_id_fails_closed_without_stable_identity():
    with pytest.raises(HTTPException) as exc:
        principal_id({"role": "user"})
    assert exc.value.status_code == 401


def test_collab_route_does_not_read_provider_specific_id():
    source = open("hashmm/api/routes/collab.py", encoding="utf-8").read()
    assert 'require_auth(request)["id"]' not in source
    assert "require_principal_id(request)" in source
