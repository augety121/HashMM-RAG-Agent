"""tests/test_remote_hub_ext.py — V103.51 远程能力扩展（对标 UU 远程）信令中继验证。

复用 remote_hub 的纯逻辑 SignalHub（返回待发送动作列表，可用假连接单测）。覆盖：
  - 文件传输握手 fileOffer/fileAccept 在已配对 viewer⇄host 间双向转发；
  - 多屏 selectMonitor(viewer→host) / monitorList(host→viewer)；
  - 画质 setQuality、隐私 setPrivacy（viewer→host）；
  - 未配对 viewer 的控制消息不被转发（安全）；
  - host 定向 vid 转发只到该 viewer，不串台。
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from hashmm.api.remote_hub import SignalHub, Conn


def _pair():
    """建一对已配对的 host+viewer，返回 (hub, host, viewer)。"""
    hub = SignalHub()
    host = Conn(uid="u1", role="host", name="PC", platform="win")
    viewer = Conn(uid="u1", role="viewer", name="Phone")
    hub.add(host); hub.add(viewer)
    hub.on_message(viewer, {"type": "connect", "target": host.id})  # viewer.peer=host
    return hub, host, viewer


def test_file_offer_viewer_to_host():
    hub, host, viewer = _pair()
    actions = hub.on_message(viewer, {"type": "fileOffer", "name": "a.zip", "size": 1000, "ftid": "x"})
    assert len(actions) == 1
    target, msg = actions[0]
    assert target is host
    assert msg["type"] == "fileOffer" and msg["name"] == "a.zip"
    assert msg["vid"] == viewer.id          # host learns which viewer sent it


def test_file_accept_host_to_specific_viewer():
    hub, host, viewer = _pair()
    actions = hub.on_message(host, {"type": "fileAccept", "vid": viewer.id, "ftid": "x"})
    assert len(actions) == 1
    target, msg = actions[0]
    assert target is viewer
    assert msg["type"] == "fileAccept"
    assert "vid" not in msg                  # vid stripped on delivery to viewer


def test_monitor_list_host_broadcasts_to_viewers():
    hub, host, viewer = _pair()
    actions = hub.on_message(host, {"type": "monitorList", "monitors": [{"id": 0}, {"id": 1}]})
    assert len(actions) == 1 and actions[0][0] is viewer
    assert actions[0][1]["type"] == "monitorList"


def test_select_monitor_viewer_to_host():
    hub, host, viewer = _pair()
    actions = hub.on_message(viewer, {"type": "selectMonitor", "monitor": 1})
    assert actions and actions[0][0] is host and actions[0][1]["monitor"] == 1


def test_quality_and_privacy_viewer_to_host():
    hub, host, viewer = _pair()
    a1 = hub.on_message(viewer, {"type": "setQuality", "fps": 15, "jpeg": 80})
    a2 = hub.on_message(viewer, {"type": "setPrivacy", "blankScreen": True})
    assert a1 and a1[0][0] is host and a1[0][1]["fps"] == 15
    assert a2 and a2[0][0] is host and a2[0][1]["blankScreen"] is True


def test_unpaired_viewer_control_not_relayed():
    hub = SignalHub()
    host = Conn(uid="u1", role="host"); viewer = Conn(uid="u1", role="viewer")
    hub.add(host); hub.add(viewer)
    # viewer has NOT connected → no peer → control messages must not forward
    assert hub.on_message(viewer, {"type": "fileOffer", "name": "a", "size": 1}) == []
    assert hub.on_message(viewer, {"type": "setQuality", "fps": 30}) == []


def test_host_directed_vid_no_crosstalk():
    hub = SignalHub()
    host = Conn(uid="u1", role="host")
    v1 = Conn(uid="u1", role="viewer"); v2 = Conn(uid="u1", role="viewer")
    hub.add(host); hub.add(v1); hub.add(v2)
    hub.on_message(v1, {"type": "connect", "target": host.id})
    hub.on_message(v2, {"type": "connect", "target": host.id})
    actions = hub.on_message(host, {"type": "fileProgress", "vid": v1.id, "pct": 50})
    assert len(actions) == 1 and actions[0][0] is v1   # only v1, not v2


def test_cross_account_isolation_for_new_messages():
    hub = SignalHub()
    hostA = Conn(uid="A", role="host"); viewerB = Conn(uid="B", role="viewer")
    hub.add(hostA); hub.add(viewerB)
    # viewerB cannot target hostA (different account) — connect fails, so no peer
    hub.on_message(viewerB, {"type": "connect", "target": hostA.id})
    assert hub.on_message(viewerB, {"type": "fileOffer", "name": "x", "size": 1}) == []


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn(); passed += 1; print(f"  PASS {fn.__name__}")
        except AssertionError as e:
            print(f"  FAIL {fn.__name__}: {e}")
        except Exception as e:
            print(f"  ERROR {fn.__name__}: {e}")
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)
