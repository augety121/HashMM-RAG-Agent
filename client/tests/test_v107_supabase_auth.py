"""V107 — Supabase JWT 验签（统一身份基石）：ES256 验签 + claims 映射 + admin 白名单。

用自造 EC P-256 密钥签 ES256 token（模拟 Supabase 非对称签发），证明本地验签实现正确。
无网络、可在沙箱直跑（PyJWT + cryptography 已装）。
"""
import os
import time

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from hashmm.api import supabase_auth as sa


def _make_keypair_and_token(claims):
    priv = ec.generate_private_key(ec.SECP256R1())
    priv_pem = priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption())
    pub_pem = priv.public_key().public_bytes(serialization.Encoding.PEM,
                                              serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    token = jwt.encode(claims, priv_pem, algorithm="ES256")
    return token, pub_pem


def _base_claims():
    now = int(time.time())
    return {"sub": "11111111-2222-3333-4444-555555555555", "email": "Alice@Corp.com",
            "aud": "authenticated", "role": "authenticated", "exp": now + 3600, "iat": now}


def test_es256_verify_roundtrip():
    c = _base_claims()
    token, pub = _make_keypair_and_token(c)
    out = sa.decode_with_key(token, pub)
    assert out and out["sub"] == c["sub"]


def test_wrong_audience_rejected():
    c = _base_claims()
    c["aud"] = "wrong"
    token, pub = _make_keypair_and_token(c)
    assert sa.decode_with_key(token, pub) is None


def test_expired_rejected():
    c = _base_claims()
    c["exp"] = int(time.time()) - 10
    token, pub = _make_keypair_and_token(c)
    assert sa.decode_with_key(token, pub) is None


def test_claims_to_user_default_role():
    os.environ.pop("HASHMM_SUPABASE_ADMIN_EMAILS", None)
    c = _base_claims()
    u = sa.claims_to_user(c)
    assert u["uid"] == "sb_" + c["sub"]
    assert u["role"] == "user"
    assert u["email"] == "alice@corp.com"  # 小写
    assert u["auth_provider"] == "supabase"


def test_admin_allowlist():
    os.environ["HASHMM_SUPABASE_ADMIN_EMAILS"] = "alice@corp.com, boss@corp.com"
    u = sa.claims_to_user(_base_claims())
    assert u["role"] == "admin"
    os.environ.pop("HASHMM_SUPABASE_ADMIN_EMAILS", None)


def test_disabled_when_no_url():
    os.environ.pop("HASHMM_SUPABASE_URL", None)
    assert sa.enabled() is False
    token, _ = _make_keypair_and_token(_base_claims())
    assert sa.verify_token(token) is None


def test_no_sub_rejected():
    assert sa.claims_to_user({"email": "x@y.com"}) is None


if __name__ == "__main__":
    test_es256_verify_roundtrip()
    test_wrong_audience_rejected()
    test_expired_rejected()
    test_claims_to_user_default_role()
    test_admin_allowlist()
    test_disabled_when_no_url()
    test_no_sub_rejected()
    print("test_v107_supabase_auth: all passed")
