"""tests/test_remote_hub.py — 账号级远程信令中继（SignalHub）单测。

不依赖 FastAPI/网络：直接用 SignalHub + Conn，校验 add/on_message/remove 返回的「待发送动作」
是否正确（账号隔离、设备发现、viewer⇄host 双向信令与输入中继、掉线通知）。
可直接运行：PYTHONPATH=. python3 tests/test_remote_hub.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hashmm.api.remote_hub import SignalHub, Conn  # noqa: E402

_passed = 0


def ok(name):
    global _passed
    _passed += 1
    print("  \u2713 " + name)


def find(actions, conn=None, mtype=None):
    """从动作列表里找一条 (conn, msg)。"""
    for c, m in actions:
        if conn is not None and c is not conn:
            continue
        if mtype is not None and m.get("type") != mtype:
            continue
        return c, m
    return None, None


def test():
    hub = SignalHub(ice_servers=[{"urls": "stun:test:3478"}])

    # 账号 A：一台 host、一台 viewer
    hostA = Conn(uid="userA", role="host", name="A的台式机", platform="win32")
    viewA = Conn(uid="userA", role="viewer", name="A的手机", platform="android")

    a1 = hub.add(hostA)
    _, m = find(a1, conn=hostA, mtype="authOk")
    assert m and m["iceServers"][0]["urls"] == "stun:test:3478" and m["id"] == hostA.id
    hub.add(viewA)
    ok("add 注册成功并下发 authOk + ICE 配置 + 连接 id")

    # 设备发现：viewer 看到同账号的 host（且不含自己）
    dl = hub.on_message(viewA, {"type": "listDevices"})
    _, m = find(dl, conn=viewA, mtype="devices")
    assert m and len(m["list"]) == 1 and m["list"][0]["id"] == hostA.id and m["list"][0]["name"] == "A的台式机"
    ok("listDevices 返回同账号在线 host（含名字/平台，排除自己）")

    # 账号隔离：账号 B 的 host 不出现在账号 A 的列表里
    hostB = Conn(uid="userB", role="host", name="B的电脑")
    hub.add(hostB)
    dl2 = hub.on_message(viewA, {"type": "listDevices"})
    _, m = find(dl2, conn=viewA, mtype="devices")
    assert len(m["list"]) == 1 and m["list"][0]["id"] == hostA.id
    ok("账号隔离：别的账号的设备不会出现在本账号列表")

    # 连接不存在的设备 → pairFail offline
    bad = hub.on_message(viewA, {"type": "connect", "target": "nope"})
    _, m = find(bad, conn=viewA, mtype="pairFail")
    assert m and m["reason"] == "offline"
    ok("连接不存在/离线设备 → pairFail(offline)")

    # viewer 连接 host → host 收 viewerJoined{vid}，viewer 收 ready
    conn_actions = hub.on_message(viewA, {"type": "connect", "target": hostA.id})
    _, mj = find(conn_actions, conn=hostA, mtype="viewerJoined")
    _, mr = find(conn_actions, conn=viewA, mtype="ready")
    assert mj and mj["vid"] == viewA.id and mr
    assert viewA.peer == hostA.id and viewA.id in hostA.viewers
    ok("viewer connect → host 收 viewerJoined{vid}、viewer 收 ready（配对建立）")

    # viewer → host 的 rtcSignal（answer）中继，带上 viewer 的 vid
    s1 = hub.on_message(viewA, {"type": "rtcSignal", "kind": "answer", "data": {"sdp": "A"}})
    _, m = find(s1, conn=hostA, mtype="rtcSignal")
    assert m and m["vid"] == viewA.id and m["kind"] == "answer" and m["data"]["sdp"] == "A"
    ok("viewer→host 的 answer/ICE 中继（标注来源 vid，payload 透传）")

    # host → 指定 viewer 的 rtcSignal（offer）中继
    s2 = hub.on_message(hostA, {"type": "rtcSignal", "vid": viewA.id, "kind": "offer", "data": {"sdp": "O"}})
    _, m = find(s2, conn=viewA, mtype="rtcSignal")
    assert m and m["kind"] == "offer" and m["data"]["sdp"] == "O"
    ok("host→指定 viewer 的 offer/ICE 中继")

    # host 不能把信令发给未与自己配对的 viewer（安全）
    stray = Conn(uid="userA", role="viewer", name="陌生viewer")
    hub.add(stray)
    s3 = hub.on_message(hostA, {"type": "rtcSignal", "vid": stray.id, "kind": "offer", "data": {}})
    assert find(s3, conn=stray)[1] is None
    ok("host 无法向未与其配对的 viewer 发信令（防串台）")

    # viewer → host 的输入中继（host 渲染进程将据此转主进程注入）
    inp = hub.on_message(viewA, {"type": "input", "action": "left_click", "x": 500, "y": 500})
    _, m = find(inp, conn=hostA, mtype="input")
    assert m and m["action"] == "left_click" and m["x"] == 500 and m["vid"] == viewA.id
    ok("viewer→host 的输入事件中继（坐标透传 + vid）")

    # viewer 掉线 → host 收 viewerLeft{vid}
    r1 = hub.remove(viewA)
    _, m = find(r1, conn=hostA, mtype="viewerLeft")
    assert m and m["vid"] == viewA.id and viewA.id not in hostA.viewers
    ok("viewer 掉线 → host 收 viewerLeft{vid}（可销毁对应 PC）")

    # host 掉线 → 其已连 viewer 收 pairFail(offline)
    v2 = Conn(uid="userA", role="viewer", name="另一个viewer")
    hub.add(v2)
    hub.on_message(v2, {"type": "connect", "target": hostA.id})
    r2 = hub.remove(hostA)
    _, m = find(r2, conn=v2, mtype="pairFail")
    assert m and m["reason"] == "offline"
    ok("host 掉线 → 其 viewer 收 pairFail(offline)")

    print("\n\u2705 remote_hub 全部通过（共 %d 项：账号隔离 + 设备发现 + 配对 + 双向信令/输入中继 + 防串台 + 掉线通知）" % _passed)


if __name__ == "__main__":
    try:
        test()
        sys.exit(0)
    except AssertionError as e:
        print("\n\u274c 失败：", e)
        sys.exit(1)
