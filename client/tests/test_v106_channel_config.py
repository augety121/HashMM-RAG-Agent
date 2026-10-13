"""V106 — 渠道配置层：DB(settings_store) → 环境变量 → 默认（沙箱无 DB → 验证 env 兜底）。

让微信/飞书开关与凭证可在客户端 UI 配置存进 DB，不必写 AutoDL 启动命令的环境变量。
"""
import os

from hashmm.channels import config, feishu, wechat_ilink as wx

_KEYS = ["HASHMM_FEISHU_ENABLE", "HASHMM_FEISHU_APP_ID", "HASHMM_FEISHU_APP_SECRET",
         "HASHMM_WECHAT_ENABLE"]


def _clear():
    for k in _KEYS:
        os.environ.pop(k, None)


def test_unconfigured_disabled():
    _clear()
    assert feishu.enabled() is False
    assert wx.enabled() is False


def test_env_fallback_resolves():
    _clear()
    os.environ["HASHMM_FEISHU_ENABLE"] = "1"
    os.environ["HASHMM_FEISHU_APP_ID"] = "cli_x"
    os.environ["HASHMM_FEISHU_APP_SECRET"] = "sec_y"
    assert config.feishu("APP_ID") == "cli_x"
    assert config.feishu("APP_SECRET") == "sec_y"
    assert feishu.enabled() is True
    _clear()


def test_feishu_needs_credentials_not_just_enable():
    _clear()
    os.environ["HASHMM_FEISHU_ENABLE"] = "1"  # 只开开关、不配 app_id/secret
    assert feishu.enabled() is False
    _clear()


def test_wechat_enable():
    _clear()
    os.environ["HASHMM_WECHAT_ENABLE"] = "1"
    assert wx.enabled() is True
    _clear()


def test_settings_keys_registered():
    from hashmm.api import settings_store
    for k in ["channel_feishu_enable", "channel_feishu_app_id", "channel_feishu_app_secret",
              "channel_wechat_enable"]:
        assert k in settings_store._KNOWN, f"{k} 未注册到 settings_store._KNOWN"
    # app_secret 应标记为密钥（脱敏）
    assert settings_store._KNOWN["channel_feishu_app_secret"][1] is True


if __name__ == "__main__":
    test_unconfigured_disabled()
    test_env_fallback_resolves()
    test_feishu_needs_credentials_not_just_enable()
    test_wechat_enable()
    test_settings_keys_registered()
    print("test_v106_channel_config: all passed")
