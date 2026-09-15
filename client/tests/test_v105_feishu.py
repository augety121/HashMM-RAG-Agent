"""V105 — 飞书应用机器人适配纯逻辑：签名 / AES-256-CBC 解密 / 事件解析 / 去重。

AES 用飞书同方案加密再解密，证明解密实现与官方一致。无网络、可在沙箱直跑。
"""
import base64
import hashlib
import json
import os

from hashmm.channels import feishu


def _feishu_encrypt(plaintext: str, encrypt_key: str) -> str:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    key = hashlib.sha256(encrypt_key.encode()).digest()
    iv = os.urandom(16)
    pad = 16 - (len(plaintext.encode()) % 16)
    data = plaintext.encode() + bytes([pad]) * pad
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ct = enc.update(data) + enc.finalize()
    return base64.b64encode(iv + ct).decode()


def test_signature_compute_and_verify():
    ts, nonce, ek = "1700000000", "abc123", "myEncryptKey"
    body = b'{"hello":"world"}'
    sig = feishu.compute_signature(ts, nonce, ek, body)
    assert feishu.verify_signature(ts, nonce, ek, body, sig)
    assert not feishu.verify_signature(ts, nonce, ek, body, "wrong")
    assert not feishu.verify_signature(ts, "DIFFERENT", ek, body, sig)


def test_aes_256_cbc_decrypt_roundtrip():
    ek = "myEncryptKey"
    secret = json.dumps({"type": "url_verification", "challenge": "CH_42", "token": "vtok"})
    enc_b64 = _feishu_encrypt(secret, ek)
    dec = feishu.decrypt_event(enc_b64, ek)
    assert json.loads(dec)["challenge"] == "CH_42"
    assert feishu.decrypt_event("garbage!!", ek) == ""


def test_url_verification_parse():
    p = feishu.parse_inbound({"type": "url_verification", "challenge": "X9", "token": "t"})
    assert p == {"kind": "challenge", "challenge": "X9"}


def test_p2p_message_parse_and_should_reply():
    evt = {"header": {"event_type": "im.message.receive_v1", "event_id": "e1", "token": "t"},
           "event": {"message": {"chat_id": "oc_1", "chat_type": "p2p",
                                 "content": json.dumps({"text": "报销流程是什么"})},
                     "sender": {"sender_id": {"open_id": "ou_a"}}}}
    m = feishu.parse_inbound(evt)
    assert m["kind"] == "message" and m["text"] == "报销流程是什么"
    assert m["chat_id"] == "oc_1" and m["chat_type"] == "p2p"
    assert feishu.should_reply(m) is True


def test_group_message_strips_mention_and_reply_rule():
    evt = {"header": {"event_type": "im.message.receive_v1", "event_id": "e2"},
           "event": {"message": {"chat_id": "oc_2", "chat_type": "group",
                                 "content": json.dumps({"text": "@_user_1 年假几天"}),
                                 "mentions": [{"key": "@_user_1"}]},
                     "sender": {"sender_id": {"open_id": "ou_b"}}}}
    mg = feishu.parse_inbound(evt)
    assert mg["text"] == "年假几天" and mg["mentioned"]
    assert feishu.should_reply(mg) is True
    # 群聊未 @ → 不回
    no_at = feishu.parse_inbound({"header": {"event_type": "im.message.receive_v1"},
                                  "event": {"message": {"chat_id": "o", "chat_type": "group",
                                                        "content": json.dumps({"text": "闲聊"})}}})
    assert feishu.should_reply(no_at) is False


def test_event_dedup():
    d = feishu.EventDedup()
    assert d.seen_before("e1") is False
    assert d.seen_before("e1") is True
    assert d.seen_before("") is False


def test_unrelated_event_ignored():
    assert feishu.parse_inbound({"header": {"event_type": "contact.user.updated_v3"}})["kind"] == "ignore"


if __name__ == "__main__":
    test_signature_compute_and_verify()
    test_aes_256_cbc_decrypt_roundtrip()
    test_url_verification_parse()
    test_p2p_message_parse_and_should_reply()
    test_group_message_strips_mention_and_reply_rule()
    test_event_dedup()
    test_unrelated_event_ignored()
    print("test_v105_feishu: all passed")
