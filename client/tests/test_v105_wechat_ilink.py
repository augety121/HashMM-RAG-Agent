"""V105 — 微信 iLink 客户端纯逻辑：UIN / 版本号编码 / headers / 发送体 / 消息解析。

移植自 fanbox 的 iLink 实现，协议为腾讯官方"微信 ClawBot"。无网络、可在沙箱直跑。
"""
import base64

from hashmm.channels import wechat_ilink as wx


def test_client_version_encoding():
    assert wx.client_version("1.0.11") == "65547"          # (1<<16)|11
    assert wx.client_version("2.3.4") == str((2 << 16) | (3 << 8) | 4)
    assert wx.client_version("bad") == "0"


def test_wechat_uin_random_base64_decimal():
    u1, u2 = wx.wechat_uin(), wx.wechat_uin()
    dec = base64.b64decode(u1).decode()
    assert dec.isdigit() and 0 <= int(dec) <= 0xFFFFFFFF
    assert u1 != u2


def test_post_headers_required_fields():
    h = wx.post_headers("TOKEN123")
    assert h["AuthorizationType"] == "ilink_bot_token"
    assert h["Authorization"] == "Bearer TOKEN123"
    assert "X-WECHAT-UIN" in h and "iLink-App-ClientVersion" in h
    assert h["Content-Type"] == "application/json"
    assert "Authorization" not in wx.post_headers()  # 登录前无 token


def test_build_text_send_body():
    b = wx.build_text_send_body("o_user@im.wechat", "CTX_TK", "你好")
    m = b["msg"]
    assert m["to_user_id"] == "o_user@im.wechat"
    assert m["message_type"] == 2 and m["message_state"] == 2
    assert m["context_token"] == "CTX_TK"
    assert m["item_list"][0]["type"] == 1
    assert m["item_list"][0]["text_item"]["text"] == "你好"


def test_content_from_msg_text_voice_media():
    msg = {"from_user_id": "u@im.wechat", "context_token": "CT9",
           "item_list": [{"type": 1, "text_item": {"text": "报销怎么走"}},
                         {"type": 2, "image_item": {"file_name": "单据.png"}}]}
    c = wx.content_from_msg(msg)
    assert c["text"] == "报销怎么走" and c["from_user_id"] == "u@im.wechat" and c["context_token"] == "CT9"
    assert c["medias"] and c["medias"][0]["kind"] == "image"
    # 语音转文字
    cv = wx.content_from_msg({"item_list": [{"type": 3, "voice_item": {"text": "语音转的字"}}]})
    assert cv["text"] == "语音转的字"
    # 畸形不抛错
    assert wx.content_from_msg({})["text"] == ""
    assert wx.content_from_msg({"item_list": [{}]})["text"] == ""


if __name__ == "__main__":
    test_client_version_encoding()
    test_wechat_uin_random_base64_decimal()
    test_post_headers_required_fields()
    test_build_text_send_body()
    test_content_from_msg_text_voice_media()
    print("test_v105_wechat_ilink: all passed")
