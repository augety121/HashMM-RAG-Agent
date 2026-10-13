"""hashmm/tools/net_guard.py — SSRF 防护（浏览/抓取类工具的安全底座，对齐大厂标准）。

浏览/抓取类工具（fetch_url / browser use）若不校验目标地址，会被 SSRF 利用去访问
内网服务或云元数据端点（169.254.169.254）窃取凭证。旧版只做字符串黑名单（"127.0.0.1"/"10." 等），
可被绕过：① 域名解析到内网（DNS rebinding，如 internal.evil.com → 127.0.0.1）；② 172.16/12 与
IPv6 未覆盖；③ 十进制/十六进制 IP（http://2130706433=127.0.0.1）；④ 公网 URL 302 跳转到内网。

本模块提供：
  · check_url_safe(url) —— 解析 URL → 若主机是 IP 字面量直接判定；若是域名则 **DNS 解析后逐个 IP 校验**，
                           用 stdlib ipaddress 统一挡回环/内网/链路本地/保留/多播地址（含十进制/十六进制/IPv6 变体）。
  · safe_get(url, ...)  —— 校验通过才发请求，且 **逐跳校验重定向**（禁用自动跳转，手动跟随并重校验每一跳）。

纯 stdlib 判定（socket/ipaddress），无 GPU/网络也能单测其分类逻辑。**永不抛错（校验函数）**。
"""
from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlparse

from hashmm.utils import get_logger

logger = get_logger("hashmm.tools.net_guard")

_ALLOWED_SCHEMES = {"http", "https"}
_MAX_REDIRECTS = 5


def _ip_is_public(ip_str: str) -> bool:
    """该 IP 是否为可安全外访的公网地址（排除回环/内网/链路本地/保留/多播/未指定）。"""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    # is_private：10/8、172.16/12、192.168/16 及 IPv6 ULA(fc00::/7)
    # is_link_local：169.254/16（含 169.254.169.254 云元数据）与 fe80::/10
    # is_loopback：127/8 与 ::1
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved \
            or ip.is_multicast or ip.is_unspecified:
        return False
    # IPv4-mapped IPv6（::ffff:127.0.0.1）还原成 IPv4 再判一次
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        return _ip_is_public(str(mapped))
    return True


def check_url_safe(url: str, *, resolve: bool = True) -> tuple[bool, str]:
    """判断 URL 是否可安全抓取。返回 (ok, reason)。**永不抛错**。"""
    try:
        raw = (url or "").strip()
        if not raw:
            return False, "URL 为空"
        p = urlparse(raw if "://" in raw else "https://" + raw)
        if p.scheme.lower() not in _ALLOWED_SCHEMES:
            return False, f"仅允许 http/https（收到 '{p.scheme or '空'}'）"
        host = p.hostname
        if not host:
            return False, "URL 缺少主机名"
        # ① 主机名本身是 IP 字面量（含十进制/十六进制/IPv6）→ ipaddress 归一后直接判定
        try:
            ipaddress.ip_address(host)
            return (True, "ok") if _ip_is_public(host) else (False, f"目标为内网/保留地址：{host}")
        except ValueError:
            pass
        # Legacy IPv4 spellings accepted by OS resolvers (for example decimal
        # 2130706433 == 127.0.0.1, plus octal/hex and shortened dotted forms)
        # are rejected by ipaddress. Normalize numeric-only hosts before DNS.
        if re.fullmatch(r"[0-9A-Fa-fxX.]+", host):
            try:
                normalized = socket.inet_ntoa(socket.inet_aton(host))
                return ((True, "ok") if _ip_is_public(normalized)
                        else (False, f"目标为内网/保留地址：{host} ({normalized})"))
            except OSError:
                return False, f"非法数字地址：{host}"
        # ② 明显的内网主机名
        low = host.lower().rstrip(".")
        if low == "localhost" or low.endswith((".localhost", ".local", ".internal", ".intranet", ".lan")):
            return False, f"内网主机名：{host}"
        if not resolve:
            return True, "ok(未解析DNS)"
        # ③ 域名 → 解析出所有 IP，任一落在内网/保留即拦（挡 DNS rebinding / 内网映射）
        try:
            infos = socket.getaddrinfo(host, None)
        except Exception as e:  # noqa: BLE001
            return False, f"域名解析失败：{type(e).__name__}"
        addrs = {info[4][0] for info in infos}
        for a in addrs:
            if not _ip_is_public(a):
                return False, f"域名 {host} 解析到内网/保留地址 {a}（疑似 SSRF）"
        return True, "ok"
    except Exception as e:  # noqa: BLE001
        return False, f"URL 校验异常：{type(e).__name__}"


def safe_get(url, *, timeout: int = 15, headers: dict | None = None,
             stream: bool = False, max_redirects: int = _MAX_REDIRECTS):
    """校验后发起 GET，并**逐跳校验重定向**（防公网→内网跳转绕过）。返回 requests.Response。
    校验失败或超跳数抛 PermissionError（调用方按错误信息提示，不吞）。"""
    import requests
    cur = str(url) if "://" in str(url) else "https://" + str(url)
    for _ in range(max_redirects + 1):
        ok, reason = check_url_safe(cur)
        if not ok:
            raise PermissionError(f"URL 被安全策略拦截：{reason}")
        resp = requests.get(cur, timeout=timeout, headers=headers or {},
                            stream=stream, allow_redirects=False)
        if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("Location"):
            nxt = requests.compat.urljoin(cur, resp.headers["Location"])
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass
            cur = nxt
            continue
        return resp
    raise PermissionError("重定向次数过多（可能是重定向环或绕过尝试）")


# ── run_shell 危险命令策略（与 tool_registry 共用同一份，避免两处规则漂移）──
import re as _re_cmd

_SHELL_DANGER = _re_cmd.compile(
    r"rm\s+-[rf]{1,2}\s+(/(\s|$)|~|\$HOME|/\*|\*\s*$)|"          # rm -rf 根/家/通配
    r"\bmkfs\b|:\(\)\s*\{[^}]*\}\s*;\s*:|\bdd\s+if=.*of=/dev/|"   # 格式化 / fork炸弹 / dd写块设备
    r"\bshutdown\b|\breboot\b|\bhalt\b|\binit\s+0\b|"
    r"format\s+[a-z]:|del\s+/[fsq]|Remove-Item\s+-Recurse\s+-Force\s+[Cc]:|"
    r">\s*/dev/sd|--no-preserve-root|"
    r"\bchmod\s+-R\s+777\s+/|"
    r"(curl|wget|Invoke-WebRequest|iwr|fetch)\b[^|;&]*[|;&]+\s*(sudo\s+)?(bash|sh|zsh|python3?|perl|ruby|powershell|pwsh)\b",
    _re_cmd.IGNORECASE)


def is_shell_command_dangerous(cmd: str) -> tuple[bool, str]:
    """判断 shell 命令是否命中危险模式（毁灭性命令 / 管道执行远程脚本 curl|bash）。
    返回 (dangerous, reason)。**永不抛错**。"""
    try:
        c = str(cmd or "")
        m = _SHELL_DANGER.search(c)
        if m:
            return True, f"命中危险模式：{m.group(0)[:60]}"
        return False, ""
    except Exception as e:  # noqa: BLE001
        return False, ""
