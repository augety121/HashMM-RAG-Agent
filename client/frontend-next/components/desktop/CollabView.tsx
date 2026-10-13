"use client";
/** components/desktop/CollabView.tsx — 跨 Agent 协作面板（V320）。
 *
 * 四个标签页：
 *   · 好友协作 —— 加好友/接受/解除 + 向好友 agent 发起协作 + 交互 Agent 安全网关状态
 *   · 组织     —— 我加入的组织、成员列表、添加成员（owner/admin）
 *   · 跨机互联 —— 与其它机器的 HashMM 实例配对（签名 RPC），让不同机器的 agent 真正互联
 *   · 审计     —— 别人向我请求过什么（自我审计）
 *
 * 所有对外通信都经交互 Agent 这一道安全关卡；跨机走 HMAC 签名 + 防重放的 RPC。
 */
import { useCallback, useEffect, useState } from "react";
import {
  Users, RefreshCw, UserPlus, Check, X, Send, ShieldCheck, Activity,
  Building2, Network, KeyRound, Copy, Trash2, Link2, AlertTriangle,
} from "lucide-react";
import {
  collabFriends, collabFriendRequest, collabFriendAccept, collabFriendRemove,
  collabScopes, collabRequest, collabAuditIncoming, interactionAgentStatus,
  collabOrgs, collabOrgMembers, collabOrgAddMember,
  collabPeers, collabPeerGenSecret, collabPeerPair, collabPeerRemove,
  type CollabFriend, type CollabScope, type CollabAuditEntry, type OrgMember, type RpcPeer,
} from "@/lib/api";
import { PanelShell, PageHeader, Card, CardHeader, CardGrid, Button, Badge, StateView, SectionTitle, inputClass, inputStyle } from "./ui/PanelKit";

interface IAStatus { role: string; rate_limit: string; active_peers: number; guards: string[] }
type Tab = "friends" | "orgs" | "peers" | "audit";

export function CollabView() {
  const [tab, setTab] = useState<Tab>("friends");
  const [friends, setFriends] = useState<CollabFriend[] | undefined>(undefined);
  const [scopes, setScopes] = useState<CollabScope[]>([]);
  const [ia, setIa] = useState<IAStatus | null>(null);
  const [incoming, setIncoming] = useState<CollabAuditEntry[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string>("");

  // 好友 / 协作输入
  const [friendInput, setFriendInput] = useState("");
  const [collabTo, setCollabTo] = useState("");
  const [collabScope, setCollabScope] = useState("answer_question");
  const [collabTask, setCollabTask] = useState("");

  // 组织
  const [orgs, setOrgs] = useState<string[]>([]);
  const [activeOrg, setActiveOrg] = useState<string>("");
  const [members, setMembers] = useState<OrgMember[]>([]);
  const [newOrgId, setNewOrgId] = useState("");
  const [memberInput, setMemberInput] = useState("");
  const [memberRole, setMemberRole] = useState("member");

  // 跨机对等体
  const [selfPeerId, setSelfPeerId] = useState("");
  const [peers, setPeers] = useState<RpcPeer[]>([]);
  const [peersDenied, setPeersDenied] = useState(false);
  const [pPeerId, setPPeerId] = useState("");
  const [pEndpoint, setPEndpoint] = useState("");
  const [pSecret, setPSecret] = useState("");
  const [pLabel, setPLabel] = useState("");

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const [f, s, i, au, o] = await Promise.all([
        collabFriends().catch(() => null),
        collabScopes().catch(() => null),
        interactionAgentStatus().catch(() => null),
        collabAuditIncoming().catch(() => null),
        collabOrgs().catch(() => null),
      ]);
      setFriends(f && Array.isArray(f.friends) ? f.friends : []);
      setScopes(s && Array.isArray(s.scopes) ? s.scopes : []);
      setIa(i && i.ok ? (i as unknown as IAStatus) : null);
      setIncoming(au && Array.isArray(au.log) ? au.log : []);
      setOrgs(o && Array.isArray(o.orgs) ? o.orgs : []);
    } finally {
      setBusy(false);
    }
  }, []);

  const loadPeers = useCallback(async () => {
    const p = await collabPeers().catch(() => null);
    if (p && p.ok) { setSelfPeerId(p.self_peer_id || ""); setPeers(p.peers || []); setPeersDenied(false); }
    else { setPeersDenied(true); }
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { if (tab === "peers") loadPeers(); }, [tab, loadPeers]);
  useEffect(() => {
    if (activeOrg) collabOrgMembers(activeOrg).then(r => setMembers(r.ok ? r.members : [])).catch(() => setMembers([]));
  }, [activeOrg]);

  // ── 好友动作 ──
  const doAddFriend = async () => {
    if (!friendInput.trim()) return;
    const r = await collabFriendRequest(friendInput.trim());
    setMsg(r.detail || ""); setFriendInput(""); load();
  };
  const doAccept = async (requester: string) => { await collabFriendAccept(requester); load(); };
  const doRemove = async (other: string) => { await collabFriendRemove(other); load(); };
  const doCollab = async () => {
    if (!collabTo.trim() || !collabTask.trim()) return;
    setBusy(true);
    try {
      const r = await collabRequest(collabTo.trim(), collabScope, collabTask.trim());
      setMsg(r.ok
        ? `协作成功${r.redacted ? "（返回内容已脱敏）" : ""}：${(r.result || "").slice(0, 240)}`
        : `协作失败：${r.rejected_reason || "协作被拒绝"}`);
    } finally { setBusy(false); }
  };

  // ── 组织动作 ──
  const doCreateOrg = async () => {
    if (!newOrgId.trim()) return;
    // 空组织加成员时后端会把创建者设为 owner；用"把某成员加进去"来落地一个新组织
    const r = await collabOrgAddMember(newOrgId.trim(), memberInput.trim() || "__self_placeholder__", "member");
    setMsg(r.detail || ""); setNewOrgId(""); load();
  };
  const doAddMember = async () => {
    if (!activeOrg || !memberInput.trim()) return;
    const r = await collabOrgAddMember(activeOrg, memberInput.trim(), memberRole);
    setMsg(r.detail || ""); setMemberInput("");
    collabOrgMembers(activeOrg).then(x => setMembers(x.ok ? x.members : []));
  };

  // ── 对等体动作 ──
  const doGenSecret = async () => { const r = await collabPeerGenSecret(); if (r.ok) setPSecret(r.secret); };
  const doPair = async () => {
    if (!pPeerId.trim() || !pEndpoint.trim() || !pSecret.trim()) return;
    const r = await collabPeerPair(pPeerId.trim(), pEndpoint.trim(), pSecret.trim(), pLabel.trim());
    setMsg(r.detail || (r.ok ? "已配对" : "配对失败"));
    if (r.ok) { setPPeerId(""); setPEndpoint(""); setPSecret(""); setPLabel(""); loadPeers(); }
  };
  const doRemovePeer = async (id: string) => { await collabPeerRemove(id); loadPeers(); };
  const copyText = (t: string) => { try { navigator.clipboard?.writeText(t); setMsg("已复制"); } catch { /* */ } };

  const activeFriends = (friends || []).filter(f => f.status === "active");
  const incomingReqs = (friends || []).filter(f => f.status === "pending" && f.direction === "incoming");
  const outgoingReqs = (friends || []).filter(f => f.status === "pending" && f.direction === "outgoing");

  if (friends === undefined) {
    return <PanelShell><StateView kind="loading" message="加载协作数据…" /></PanelShell>;
  }

  const TABS: { id: Tab; label: string; icon: typeof Users }[] = [
    { id: "friends", label: "好友协作", icon: Users },
    { id: "orgs", label: "组织", icon: Building2 },
    { id: "peers", label: "跨机互联", icon: Network },
    { id: "audit", label: "审计", icon: Activity },
  ];

  return (
    <PanelShell>
      <PageHeader icon={Users} title="跨 Agent 协作" subtitle="和好友/同事的 agent 安全协作 · 跨机走签名 RPC · 所有通信经交互 Agent 安全网关"
        actions={<Button icon={RefreshCw} onClick={load} busy={busy} size="sm">刷新</Button>} />

      {/* 标签切换 */}
      <div className="flex gap-1 mb-4">
        {TABS.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)}
            className="px-3 py-1.5 rounded-[10px] text-[12px] inline-flex items-center gap-1.5 transition-colors"
            style={{
              background: tab === t.id ? "var(--accent)" : "var(--bg-tertiary)",
              color: tab === t.id ? "#fff" : "var(--text-secondary)",
            }}>
            <t.icon size={13} /> {t.label}
          </button>
        ))}
      </div>

      {msg && (
        <div className="mb-3 p-2 rounded-lg text-[11.5px]" style={{ background: "var(--bg-tertiary)", color: "var(--text-secondary)" }}>{msg}</div>
      )}

      {/* ══════════ 好友协作 ══════════ */}
      {tab === "friends" && (
        <>
          {ia && (
            <Card className="mb-4">
              <CardHeader icon={ShieldCheck} title="交互 Agent · 对外通信安全网关"
                sub={`速率限制 ${ia.rate_limit} · 活跃通信方 ${ia.active_peers}`} />
              <div className="flex flex-wrap gap-2 mt-2">
                {ia.guards.map(g => <Badge key={g} tone="success">{g}</Badge>)}
              </div>
            </Card>
          )}
          <CardGrid min={300}>
            <Card>
              <CardHeader icon={Users} title={`好友（${activeFriends.length}）`} />
              <div className="flex gap-2 mt-2 mb-3">
                <input className={inputClass} style={inputStyle} placeholder="输入用户名加好友"
                  value={friendInput} onChange={e => setFriendInput(e.target.value)}
                  onKeyDown={e => e.key === "Enter" && doAddFriend()} />
                <Button icon={UserPlus} onClick={doAddFriend} size="sm">添加</Button>
              </div>
              {incomingReqs.length > 0 && (
                <div className="mb-3">
                  <SectionTitle>待我确认</SectionTitle>
                  {incomingReqs.map(f => (
                    <div key={f.user} className="flex items-center justify-between py-1.5">
                      <span className="text-[12px]" style={{ color: "var(--text-primary)" }}>{f.user}</span>
                      <Button icon={Check} onClick={() => doAccept(f.user)} size="sm" variant="primary">接受</Button>
                    </div>
                  ))}
                </div>
              )}
              {outgoingReqs.length > 0 && (
                <div className="mb-2 text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                  已发出待对方确认：{outgoingReqs.map(f => f.user).join("、")}
                </div>
              )}
              <SectionTitle>好友列表</SectionTitle>
              {activeFriends.length === 0 ? (
                <div className="text-[11.5px] py-2" style={{ color: "var(--text-tertiary)" }}>还没有好友。跨机好友用 用户名@对方机器ID。</div>
              ) : activeFriends.map(f => (
                <div key={f.user} className="flex items-center justify-between py-1.5">
                  <span className="text-[12px]" style={{ color: "var(--text-primary)" }}>{f.user}</span>
                  <Button icon={X} onClick={() => doRemove(f.user)} size="sm">解除</Button>
                </div>
              ))}
            </Card>

            <Card>
              <CardHeader icon={Send} title="发起协作" sub="让好友的 agent 帮你完成任务（跨机填 用户名@机器ID）" />
              <div className="space-y-2 mt-2">
                <input className={inputClass} style={inputStyle} placeholder="对方用户名（跨机：alice@hm_xxxx）"
                  value={collabTo} onChange={e => setCollabTo(e.target.value)} />
                <select className={inputClass} style={inputStyle} value={collabScope} onChange={e => setCollabScope(e.target.value)}>
                  {scopes.map(s => <option key={s.scope} value={s.scope}>{s.desc}</option>)}
                </select>
                <textarea className={inputClass} style={{ ...inputStyle, minHeight: 60 }} placeholder="任务描述"
                  value={collabTask} onChange={e => setCollabTask(e.target.value)} />
                <Button icon={Send} onClick={doCollab} busy={busy} size="sm" variant="primary">发送协作请求</Button>
              </div>
            </Card>
          </CardGrid>
        </>
      )}

      {/* ══════════ 组织 ══════════ */}
      {tab === "orgs" && (
        <CardGrid min={300}>
          <Card>
            <CardHeader icon={Building2} title={`我的组织（${orgs.length}）`} sub="同组织成员的 agent 自动互信" />
            <div className="mt-2 mb-3">
              {orgs.length === 0 ? (
                <div className="text-[11.5px] py-2" style={{ color: "var(--text-tertiary)" }}>还没加入任何组织。下方新建一个，你将成为 owner。</div>
              ) : orgs.map(o => (
                <button key={o} onClick={() => setActiveOrg(o)}
                  className="w-full text-left px-3 py-2 rounded-lg text-[12px] mb-1 transition-colors"
                  style={{ background: activeOrg === o ? "var(--accent)" : "var(--bg-tertiary)", color: activeOrg === o ? "#fff" : "var(--text-primary)" }}>
                  {o}
                </button>
              ))}
            </div>
            <SectionTitle>新建组织</SectionTitle>
            <div className="flex gap-2 mt-1">
              <input className={inputClass} style={inputStyle} placeholder="组织 ID（如 acme-corp）"
                value={newOrgId} onChange={e => setNewOrgId(e.target.value)} />
              <Button onClick={doCreateOrg} size="sm">创建</Button>
            </div>
          </Card>

          <Card>
            <CardHeader icon={Users} title={activeOrg ? `成员 · ${activeOrg}` : "成员"} sub={activeOrg ? undefined : "先在左侧选一个组织"} />
            {activeOrg ? (
              <>
                <div className="mt-2 mb-3">
                  {members.length === 0 ? (
                    <div className="text-[11.5px] py-2" style={{ color: "var(--text-tertiary)" }}>无成员或无权查看。</div>
                  ) : members.map(m => (
                    <div key={m.user_id} className="flex items-center justify-between py-1.5">
                      <span className="text-[12px]" style={{ color: "var(--text-primary)" }}>{m.user_id}</span>
                      <Badge tone={m.role === "owner" ? "accent" : m.role === "admin" ? "warning" : "neutral"}>{m.role}</Badge>
                    </div>
                  ))}
                </div>
                <SectionTitle>添加成员（需 owner/admin）</SectionTitle>
                <div className="flex gap-2 mt-1">
                  <input className={inputClass} style={inputStyle} placeholder="用户名"
                    value={memberInput} onChange={e => setMemberInput(e.target.value)} />
                  <select className={inputClass} style={{ ...inputStyle, maxWidth: 100 }} value={memberRole} onChange={e => setMemberRole(e.target.value)}>
                    <option value="member">member</option>
                    <option value="admin">admin</option>
                  </select>
                  <Button icon={UserPlus} onClick={doAddMember} size="sm">加入</Button>
                </div>
              </>
            ) : (
              <div className="text-[11.5px] py-4" style={{ color: "var(--text-tertiary)" }}>选一个组织查看/管理成员。</div>
            )}
          </Card>
        </CardGrid>
      )}

      {/* ══════════ 跨机互联（RPC）══════════ */}
      {tab === "peers" && (
        peersDenied ? (
          <StateView kind="empty" icon={AlertTriangle} title="需要管理员权限"
            message="跨机配对建立的是机器级信任（共享验签密钥），仅管理员可管理。" />
        ) : (
          <>
            <Card className="mb-4">
              <CardHeader icon={Network} title="本机身份" sub="配对时把这个 ID 给对方，对方用它索引你的验签密钥" />
              <div className="flex items-center gap-2 mt-2">
                <code className="px-2 py-1 rounded text-[12px]" style={{ background: "var(--bg-tertiary)", color: "var(--accent)" }}>{selfPeerId || "…"}</code>
                <Button icon={Copy} onClick={() => copyText(selfPeerId)} size="sm">复制</Button>
              </div>
            </Card>

            <CardGrid min={320}>
              <Card>
                <CardHeader icon={KeyRound} title="配对新的远端实例" sub="两台机器交换 (机器ID · 地址 · 密钥)，各存一份" />
                <div className="space-y-2 mt-2">
                  <input className={inputClass} style={inputStyle} placeholder="对方机器 ID（对方的本机身份）"
                    value={pPeerId} onChange={e => setPPeerId(e.target.value)} />
                  <input className={inputClass} style={inputStyle} placeholder="对方地址 https://host:port（建议 HTTPS）"
                    value={pEndpoint} onChange={e => setPEndpoint(e.target.value)} />
                  <div className="flex gap-2">
                    <input className={inputClass} style={inputStyle} placeholder="共享密钥（两机相同）"
                      value={pSecret} onChange={e => setPSecret(e.target.value)} />
                    <Button icon={KeyRound} onClick={doGenSecret} size="sm">生成</Button>
                  </div>
                  <input className={inputClass} style={inputStyle} placeholder="备注（可选，如 Bob 的机器）"
                    value={pLabel} onChange={e => setPLabel(e.target.value)} />
                  <Button icon={Link2} onClick={doPair} size="sm" variant="primary">配对</Button>
                </div>
                <div className="mt-3 p-2 rounded-lg text-[11px]" style={{ background: "var(--bg-tertiary)", color: "var(--text-tertiary)" }}>
                  安全：每次跨机请求用共享密钥做 HMAC 签名 + 时间戳/nonce 防重放；对方身份被绑定，无法冒充他人。密钥仅配对时交换一次，列表里不回显。
                </div>
              </Card>

              <Card>
                <CardHeader icon={Network} title={`已配对（${peers.length}）`} />
                <div className="mt-2">
                  {peers.length === 0 ? (
                    <div className="text-[11.5px] py-2" style={{ color: "var(--text-tertiary)" }}>还没有配对的远端实例。</div>
                  ) : peers.map(p => (
                    <div key={p.peer_id} className="flex items-center justify-between py-2" style={{ borderBottom: "1px solid var(--border)" }}>
                      <div className="min-w-0">
                        <div className="text-[12px] truncate" style={{ color: "var(--text-primary)" }}>{p.label || p.peer_id}</div>
                        <div className="text-[10.5px] truncate" style={{ color: "var(--text-tertiary)" }}>
                          {p.peer_id} · {p.endpoint}{p.endpoint.startsWith("https://") ? "" : " · 非 HTTPS"}
                        </div>
                      </div>
                      <Button icon={Trash2} onClick={() => doRemovePeer(p.peer_id)} size="sm">移除</Button>
                    </div>
                  ))}
                </div>
              </Card>
            </CardGrid>
          </>
        )
      )}

      {/* ══════════ 审计 ══════════ */}
      {tab === "audit" && (
        <>
          <SectionTitle right={<Badge tone="neutral">{incoming.length} 条</Badge>}>
            <span className="inline-flex items-center gap-1"><Activity size={13} /> 别人向我请求过什么（自我审计）</span>
          </SectionTitle>
          <Card padding="p-3">
            {incoming.length === 0 ? (
              <div className="text-[11.5px] py-2" style={{ color: "var(--text-tertiary)" }}>暂无外部协作请求记录。</div>
            ) : (
              <div className="space-y-1">
                {incoming.slice(0, 40).map(e => (
                  <div key={e.id} className="flex items-center gap-2 text-[11px] py-1" style={{ borderBottom: "1px solid var(--border)" }}>
                    <Badge tone={e.ok ? "success" : "error"}>{e.ok ? "允许" : "拒绝"}</Badge>
                    <span style={{ color: "var(--text-primary)" }}>{e.from_user}</span>
                    <span style={{ color: "var(--text-tertiary)" }}>{e.action}</span>
                    <span className="ml-auto" style={{ color: "var(--text-tertiary)" }}>{e.scope}</span>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </>
      )}
    </PanelShell>
  );
}
