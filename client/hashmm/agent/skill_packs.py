"""hashmm/agent/skill_packs.py — 技能包（Agent Skills / SKILL.md 规范兼容）。

与 evolution/skill_manager.py（对话中自动沉淀的"学习型技能"）互补：
  · 学习型技能 = 系统自己长出来的短提示词片段（SQLite，顶/踩驱动质量分）。
  · 技能包     = 人写的**成套操作手册**（一个文件夹 + SKILL.md，可含脚本/参考文件），
                 与 Claude Code / anthropics/skills 同一格式，可直接导入 GitHub 上
                 任何遵循该规范的技能仓库。

格式（agentskills.io 规范的子集，够用且向前兼容）：
    my-skill/
      SKILL.md          # 必需。YAML frontmatter(name/description[/triggers]) + Markdown 正文
      其它任意支持文件   # 参考资料/脚本，正文里可引用（当前版本注入正文，不执行脚本）

注入策略（渐进式披露，对标 Claude Code 的 skills 装载方式）：
  1) 只要有启用的技能包，就在 system 末尾附一份**极简索引**（名称 + 一句描述），
     让模型知道"有哪些手册可用"，成本恒定很小。
  2) 用当前 query 对每个包打分（名称/描述/triggers 关键词命中），得分过线的
     前 MAX_FULL 个包注入**完整 SKILL.md 正文**（每包截断 PACK_BODY_CAP）。
  3) 任何一步失败都静默跳过 —— 技能包故障绝不拦截主问答流程。

安全：
  · zip 导入防路径穿越（zip-slip）、单包 ≤50MB、≤400 个文件；
  · GitHub 导入只接受 github.com 的 https 链接，走 codeload zip（服务器侧下载）；
  · 本地路径导入要求路径真实存在且含 SKILL.md，复制进数据目录（不引用原地址）。

零新依赖：仅 stdlib（zipfile/urllib/json/re/shutil）。
"""
from __future__ import annotations

import io
import hashlib
import ipaddress
import json
import os
import re
import socket
import shutil
import time
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.skill_packs")

# ── 常量（集中放置，调参不用翻正文）──────────────────────────────
MAX_ZIP_BYTES = 50 * 1024 * 1024      # 单次导入压缩包上限
MAX_ZIP_FILES = 400                   # 单次导入文件数上限
MAX_UNPACKED_BYTES = 100 * 1024 * 1024  # 解压后总量上限（防 zip bomb）
MAX_SKILL_FILE_BYTES = 10 * 1024 * 1024 # 单个支持文件上限
MAX_PACKS_PER_IMPORT = 20             # 一个仓库 zip 里最多导入多少个技能包
PACK_BODY_CAP = 5000                  # 注入完整正文时每包截断（字符）
PACK_REFERENCE_CAP = 5000             # 命中正文所引用参考文件的总注入上限
MAX_REFERENCES = 4                    # 单包最多按需加载多少个一跳参考文件
INDEX_DESC_CAP = 140                  # 索引里每包描述截断
MAX_FULL = 2                          # 单次问答最多注入几个完整包
TOTAL_INJECT_CAP = 12000              # 单次注入总字符上限
_SKILL_MD = "SKILL.md"
_REFERENCE_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def _data_root() -> Path:
    """数据根：与 database.py 同一锚（HASHMM_DATA_DIR 优先，缺省项目根 data/）。"""
    try:
        from hashmm.api.database import DATA_ROOT
        return Path(DATA_ROOT)
    except Exception:
        env = os.environ.get("HASHMM_DATA_DIR", "").strip()
        return Path(env).expanduser().resolve() if env else Path("data").resolve()


def packs_root() -> Path:
    d = _data_root() / "skill_packs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def builtin_src_dir() -> Path:
    """仓库随发的内置技能包目录 <repo>/skills/packs（桌面 sidecar 同样随包）。"""
    return Path(__file__).resolve().parents[2] / "skills" / "packs"


# ── SKILL.md frontmatter 解析（YAML 极小子集：`key: value` 单行）──
_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def parse_skill_md(text: str) -> tuple[dict, str]:
    """返回 (frontmatter dict, 正文)。frontmatter 缺失/畸形时给空 dict + 全文。
    只解析 `key: value` 单行（value 可带引号）；`triggers` 支持逗号分隔或 [a, b] 形式。
    这是刻意的极小实现：Agent Skills 规范里 name/description 都是单行字符串，
    避免为它引入 PyYAML 依赖。"""
    meta: dict = {}
    body = text
    m = _FM_RE.match(text)
    if m:
        body = text[m.end():]
        for line in m.group(1).splitlines():
            line = line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, _, val = line.partition(":")
            key = key.strip().lower()
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            meta[key] = val
    # triggers → list[str]
    trig = meta.get("triggers", "")
    if isinstance(trig, str) and trig:
        trig = trig.strip()
        if trig.startswith("[") and trig.endswith("]"):
            trig = trig[1:-1]
        meta["triggers"] = [t.strip().strip("\"'") for t in trig.split(",") if t.strip()]
    else:
        meta["triggers"] = []
    # V300 第四期：Manifest 权限声明（安全扩展前提）——
    # allowed-tools（该技能能调哪些工具）、network（是否允许触网）、filesystem（是否允许写文件）。
    # 声明式：安装时告知用户、运行时按声明约束。缺省=保守（不额外授权）。
    _at = meta.get("allowed-tools") or meta.get("allowed_tools") or ""
    if isinstance(_at, str) and _at:
        _at = _at.strip()
        if _at.startswith("[") and _at.endswith("]"):
            _at = _at[1:-1]
        meta["allowed_tools"] = [t.strip().strip("\"'") for t in _at.split(",") if t.strip()]
    else:
        meta["allowed_tools"] = []
    def _truthy(v: object) -> bool:
        return str(v).strip().lower() in ("1", "true", "yes", "on", "allow", "allowed")
    meta["network"] = _truthy(meta.get("network", ""))
    meta["filesystem"] = _truthy(meta.get("filesystem", "")) or _truthy(meta.get("write", ""))
    return meta, body


def _slugify(name: str) -> str:
    """目录/主键安全的 slug；中文名保留（做前缀哈希兜底避免空 slug）。"""
    s = unicodedata.normalize("NFKC", (name or "").strip())
    s = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", s).strip("-").lower()
    return s[:64] or f"pack-{int(time.time())}"


def _referenced_markdown(pack_dir: Path, body: str, budget: int) -> str:
    """Load one-hop local Markdown references explicitly linked by SKILL.md.

    Skill references are untrusted data.  Only relative Markdown/text files
    inside the installed pack are eligible; URLs, anchors, traversal and
    symlink escapes are rejected.  This keeps progressive disclosure useful
    without turning a selected Skill into arbitrary filesystem access.
    """
    if budget <= 0:
        return ""
    try:
        root = pack_dir.resolve(strict=True)
    except OSError:
        return ""
    chunks: list[str] = []
    used: set[Path] = set()
    remaining = min(PACK_REFERENCE_CAP, budget)
    for raw_target in _REFERENCE_LINK_RE.findall(body or ""):
        if len(chunks) >= MAX_REFERENCES or remaining <= 200:
            break
        target = raw_target.strip().split("#", 1)[0].strip()
        parsed = urlparse(target)
        if not target or parsed.scheme or parsed.netloc:
            continue
        relative = Path(target)
        if relative.is_absolute() or relative.suffix.lower() not in {".md", ".txt"}:
            continue
        try:
            candidate = (root / relative).resolve(strict=True)
            candidate.relative_to(root)
        except (OSError, ValueError):
            continue
        if candidate in used or not candidate.is_file() or candidate.is_symlink():
            continue
        try:
            if candidate.stat().st_size > MAX_SKILL_FILE_BYTES:
                continue
            content = candidate.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if not content:
            continue
        label = candidate.relative_to(root).as_posix()
        allowance = max(0, remaining - len(label) - 40)
        if allowance <= 0:
            break
        excerpt = content[:allowance]
        chunks.append(f"\n#### 已加载参考：{label}\n{excerpt}")
        remaining -= len(chunks[-1])
        used.add(candidate)
    return "".join(chunks)


@dataclass
class SkillPack:
    id: str
    name: str
    description: str
    dir: str
    enabled: bool = True
    source: str = ""            # builtin / upload / github:<url> / path:<path>
    installed_at: float = 0.0
    license: str = ""
    triggers: list[str] = field(default_factory=list)
    file_count: int = 0
    size_bytes: int = 0
    # V300 第四期：Manifest 权限声明
    allowed_tools: list[str] = field(default_factory=list)
    network: bool = False
    filesystem: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "description": self.description,
            "enabled": self.enabled, "source": self.source,
            "installed_at": self.installed_at, "license": self.license,
            "triggers": self.triggers, "file_count": self.file_count,
            "size_bytes": self.size_bytes,
            "allowed_tools": self.allowed_tools, "network": self.network,
            "filesystem": self.filesystem,
        }


class SkillPackManager:
    """文件夹即真相（folder-of-truth）：SKILL.md 是元数据来源；
    registry.json 只存"目录之外"的状态（enabled/source/installed_at）。"""

    def __init__(self, root: Path | None = None):
        self.root = root or packs_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self._reg_path = self.root / "registry.json"

    # ── registry 读写（原子替换）──
    def _load_reg(self) -> dict:
        try:
            if self._reg_path.exists():
                return json.loads(self._reg_path.read_text(encoding="utf-8")) or {}
        except Exception as e:
            log_suppressed(logger, e)
        return {}

    def _save_reg(self, reg: dict) -> None:
        try:
            tmp = self._reg_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self._reg_path)
        except Exception as e:
            log_suppressed(logger, e)

    # ── 枚举 ──
    def _read_pack_dir(self, d: Path, reg: dict) -> SkillPack | None:
        md = d / _SKILL_MD
        if not md.is_file():
            return None
        try:
            meta, _ = parse_skill_md(md.read_text(encoding="utf-8", errors="replace"))
        except Exception as e:
            log_suppressed(logger, e)
            meta = {}
        entry = reg.get(d.name, {})
        files = [p for p in d.rglob("*") if p.is_file()]
        return SkillPack(
            id=d.name,
            name=str(meta.get("name") or d.name),
            description=str(meta.get("description") or ""),
            dir=str(d),
            enabled=bool(entry.get("enabled", True)),
            source=str(entry.get("source", "")),
            installed_at=float(entry.get("installed_at", 0) or 0),
            license=str(meta.get("license") or ""),
            triggers=list(meta.get("triggers") or []),
            file_count=len(files),
            size_bytes=sum(p.stat().st_size for p in files),
            allowed_tools=list(meta.get("allowed_tools") or []),
            network=bool(meta.get("network", False)),
            filesystem=bool(meta.get("filesystem", False)),
        )

    def list_packs(self) -> list[SkillPack]:
        reg = self._load_reg()
        out: list[SkillPack] = []
        try:
            for d in sorted(self.root.iterdir()):
                if not d.is_dir():
                    continue
                p = self._read_pack_dir(d, reg)
                if p:
                    out.append(p)
        except FileNotFoundError:
            pass
        return out

    def get_pack(self, pack_id: str) -> SkillPack | None:
        d = self._safe_pack_dir(pack_id)
        if not d:
            return None
        return self._read_pack_dir(d, self._load_reg())

    def read_skill_md(self, pack_id: str, cap: int = 60000) -> str:
        d = self._safe_pack_dir(pack_id)
        if not d:
            return ""
        try:
            return (d / _SKILL_MD).read_text(encoding="utf-8", errors="replace")[:cap]
        except Exception:
            return ""

    def file_tree(self, pack_id: str, limit: int = 200) -> list[dict]:
        d = self._safe_pack_dir(pack_id)
        if not d:
            return []
        out = []
        for p in sorted(d.rglob("*")):
            if p.is_file():
                out.append({"path": str(p.relative_to(d)), "size": p.stat().st_size})
                if len(out) >= limit:
                    break
        return out

    def _safe_pack_dir(self, pack_id: str) -> Path | None:
        """id → 目录，拒绝任何路径逃逸（id 必须是 root 的直接子目录名）。"""
        if not pack_id or "/" in pack_id or "\\" in pack_id or pack_id in (".", ".."):
            return None
        d = (self.root / pack_id)
        try:
            d = d.resolve()
            if d.parent != self.root.resolve() or not d.is_dir():
                return None
        except Exception:
            return None
        return d

    # ── 状态变更 ──
    def set_enabled(self, pack_id: str, enabled: bool) -> bool:
        if not self._safe_pack_dir(pack_id):
            return False
        reg = self._load_reg()
        entry = reg.setdefault(pack_id, {})
        entry["enabled"] = bool(enabled)
        self._save_reg(reg)
        return True

    def delete_pack(self, pack_id: str) -> bool:
        d = self._safe_pack_dir(pack_id)
        if not d:
            return False
        shutil.rmtree(d, ignore_errors=True)
        reg = self._load_reg()
        reg.pop(pack_id, None)
        self._save_reg(reg)
        return True

    # ── 导入：目录 → 数据区 ──
    def _unique_id(self, want: str) -> str:
        pid, i = want, 2
        while (self.root / pid).exists():
            pid = f"{want}-{i}"
            i += 1
        return pid

    def _register(self, pack_id: str, source: str) -> None:
        reg = self._load_reg()
        reg[pack_id] = {"enabled": True, "source": source, "installed_at": time.time()}
        self._save_reg(reg)

    def install_from_dir(self, src: Path, source: str) -> SkillPack:
        """把一个含 SKILL.md 的目录复制安装进数据区。"""
        src = Path(src)
        md = src / _SKILL_MD
        if not md.is_file():
            raise ValueError(f"目录里没有 {_SKILL_MD}：{src}")
        meta, _ = parse_skill_md(md.read_text(encoding="utf-8", errors="replace"))
        # V332：内置包（source="builtin"）一律用源目录名（ASCII，如 data-analysis）做安装
        # 目录名——中文目录名在 Windows zip/跨平台传输时曾产生 cp437 乱码目录（V332 清理的
        # 正是这批）。展示名不受影响（仍取 SKILL.md frontmatter 的 name）。用户自行导入的
        # 包保持原逻辑（slug 允许中文，尊重用户命名）。
        raw_name = src.name if source == "builtin" else str(meta.get("name") or src.name)
        pid = self._unique_id(_slugify(raw_name))
        dst = self.root / pid
        shutil.copytree(src, dst)
        self._register(pid, source)
        pack = self._read_pack_dir(dst, self._load_reg())
        logger.info(f"[SkillPacks] 已安装技能包 {pid} ← {source}")
        assert pack is not None
        return pack

    # ── 导入：zip 字节流（上传 / GitHub 下载共用）──
    def install_from_zip_bytes(self, data: bytes, source: str) -> list[SkillPack]:
        if len(data) > MAX_ZIP_BYTES:
            raise ValueError(f"压缩包超过 {MAX_ZIP_BYTES // (1024 * 1024)}MB 上限")
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise ValueError("文件不是有效的 ZIP 技能包") from exc
        names = zf.namelist()
        if len(names) > MAX_ZIP_FILES:
            raise ValueError(f"压缩包内文件数超过 {MAX_ZIP_FILES} 上限")
        file_infos = [item for item in zf.infolist() if not item.is_dir()]
        if any(item.file_size > MAX_SKILL_FILE_BYTES for item in file_infos):
            raise ValueError(
                f"压缩包含超过 {MAX_SKILL_FILE_BYTES // (1024 * 1024)}MB 的单个文件"
            )
        if sum(item.file_size for item in file_infos) > MAX_UNPACKED_BYTES:
            raise ValueError(
                f"压缩包解压后超过 {MAX_UNPACKED_BYTES // (1024 * 1024)}MB 上限"
            )
        if any(item.flag_bits & 0x1 for item in file_infos):
            raise ValueError("不支持加密 ZIP 技能包")
        # 找到所有含 SKILL.md 的"技能目录"（zip 内路径的父目录），根部裸 SKILL.md 记为 ""。
        skill_dirs = sorted({os.path.dirname(n) for n in names
                             if os.path.basename(n) == _SKILL_MD and not n.startswith("__MACOSX")})
        if not skill_dirs:
            raise ValueError(f"压缩包里没有找到任何 {_SKILL_MD}")
        installed: list[SkillPack] = []
        for sdir in skill_dirs[:MAX_PACKS_PER_IMPORT]:
            prefix = (sdir + "/") if sdir else ""
            # zip-slip 防护 + 逐文件落到临时目录
            tmp = self.root / f".import-{int(time.time() * 1000)}-{len(installed)}"
            tmp.mkdir(parents=True, exist_ok=True)
            try:
                for info in zf.infolist():
                    n = info.filename
                    if info.is_dir() or not n.startswith(prefix) or n.startswith("__MACOSX"):
                        continue
                    rel = n[len(prefix):]
                    if not rel:
                        continue
                    target = (tmp / rel).resolve()
                    if not str(target).startswith(str(tmp.resolve()) + os.sep) and target != tmp.resolve():
                        raise ValueError(f"压缩包含非法路径：{n}")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as fh:
                        target.write_bytes(fh.read())
                installed.append(self.install_from_dir(tmp, source))
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        return installed

    # ── 导入：GitHub 链接（在后端服务器侧下载 codeload zip）──
    def install_from_github(self, url: str) -> list[SkillPack]:
        owner, repo, branch, subpath = _parse_github_url(url)
        candidates = [branch] if branch else ["main", "master"]
        last_err: Exception | None = None
        data = None
        import urllib.request
        for br in candidates:
            dl = f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{br}"
            try:
                req = urllib.request.Request(dl, headers={"User-Agent": "HashMM-SkillPacks/1.0"})
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = resp.read(MAX_ZIP_BYTES + 1)
                if data:
                    branch = br
                    break
            except Exception as e:      # noqa: PERF203 —— 逐分支尝试，必须逐个捕获
                last_err = e
        if not data:
            raise ValueError(f"下载 GitHub 仓库失败：{last_err}")
        if len(data) > MAX_ZIP_BYTES:
            raise ValueError("仓库压缩包过大，请改用「上传 zip」导入单个技能目录")
        # 若 URL 指到子目录（/tree/<branch>/<subpath>），只导入该子树下的技能。
        if subpath:
            data = _filter_zip_to_subpath(data, subpath)
        return self.install_from_zip_bytes(data, f"github:{owner}/{repo}@{branch}" + (f"/{subpath}" if subpath else ""))

    def install_from_url(self, url: str, source_label: str = "website") -> list[SkillPack]:
        """Install a public HTTPS ZIP or a single public ``SKILL.md``.

        Website import is intentionally data-only: imported Markdown and
        supporting files are never executed.  Every redirect is revalidated
        and private/link-local destinations are rejected to keep this endpoint
        from becoming an SSRF tunnel into the deployment network.
        """
        value = (url or "").strip()
        parsed = urlparse(value)
        if parsed.netloc.lower() in {"github.com", "www.github.com"}:
            return self.install_from_github(value)
        data, final_url, content_type = _download_public_https(value)
        source = f"{source_label}:{final_url}"
        if data.startswith(b"PK\x03\x04") or "zip" in content_type.lower():
            return self.install_from_zip_bytes(data, source)
        if len(data) > 1024 * 1024:
            raise ValueError("单个 SKILL.md 超过 1MB 上限")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("网址内容既不是 ZIP，也不是 UTF-8 SKILL.md") from exc
        if Path(urlparse(final_url).path).name.lower() not in {"skill.md", ""} and "markdown" not in content_type.lower():
            raise ValueError("网址需直接返回 .zip 或 SKILL.md")
        tmp = self.root / f".web-{int(time.time() * 1000)}"
        tmp.mkdir(parents=True, exist_ok=False)
        try:
            (tmp / _SKILL_MD).write_text(text, encoding="utf-8")
            return [self.install_from_dir(tmp, source)]
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # ── 内置包播种：仓库 skills/packs/* → 数据区（只补缺，不覆盖用户改动）──
    def seed_builtins(self) -> list[str]:
        src_root = builtin_src_dir()
        if not src_root.is_dir():
            return []
        reg = self._load_reg()
        have_builtin_srcs = {v.get("src") for v in reg.values() if v.get("source") == "builtin"}
        added: list[str] = []
        for d in sorted(src_root.iterdir()):
            if not d.is_dir() or not (d / _SKILL_MD).is_file():
                continue
            if d.name in have_builtin_srcs:
                continue
            try:
                pack = self.install_from_dir(d, "builtin")
                reg2 = self._load_reg()
                reg2[pack.id]["src"] = d.name      # 记住来自哪个内置目录，避免重复播种
                self._save_reg(reg2)
                added.append(pack.id)
            except Exception as e:
                log_suppressed(logger, e)
        if added:
            logger.info(f"[SkillPacks] 已播种内置技能包：{added}")
        return added

    # ── 注入（渐进式披露）──
    def score(self, pack: SkillPack, query: str) -> int:
        """朴素而稳定的相关性分：triggers 命中 3 分/个，名称词 2 分，描述词 1 分。
        对中文：把 ≥2 字的词做子串匹配（query 通常无空格分词）。"""
        q = (query or "").lower()
        if not q:
            return 0
        pts = 0
        for t in pack.triggers:
            t = t.lower().strip()
            if len(t) >= 2 and t in q:
                pts += 3
        for word in re.findall(r"[\w\u4e00-\u9fff]{2,}", pack.name.lower()):
            if word in q:
                pts += 2
        for word in set(re.findall(r"[\w\u4e00-\u9fff]{2,}", pack.description.lower())):
            if word in q:
                pts += 1
        return pts

    def inject(self, query: str, messages: list[dict]) -> list[dict]:
        """在 messages 末尾（当前 user 消息之前由调用方保证）追加一条 system：
        技能包索引 + 命中包的完整正文。无启用包 / 出错 → 原样返回。"""
        try:
            packs = [p for p in self.list_packs() if p.enabled]
            if not packs:
                return messages
            lines = ["## 可用技能包（成套操作手册，按需遵循）"]
            for p in packs[:24]:
                desc = p.description[:INDEX_DESC_CAP]
                lines.append(f"- {p.name}：{desc}")
            budget = TOTAL_INJECT_CAP - sum(len(x) for x in lines)
            scored = sorted(((self.score(p, query), p) for p in packs), key=lambda t: -t[0])
            full_used = 0
            for pts, p in scored:
                if pts < 3 or full_used >= MAX_FULL or budget <= 400:
                    break
                try:
                    _, body = parse_skill_md((Path(p.dir) / _SKILL_MD).read_text(encoding="utf-8", errors="replace"))
                except Exception:
                    continue
                body = body.strip()[: min(PACK_BODY_CAP, budget)]
                if not body:
                    continue
                references = _referenced_markdown(
                    Path(p.dir),
                    body,
                    max(0, budget - len(body)),
                )
                loaded = body + references
                lines.append(
                    f"\n### 技能包「{p.name}」完整说明"
                    f"（已按当前任务加载，请按其步骤执行）\n{loaded}"
                )
                budget -= len(loaded)
                full_used += 1
            messages.append({"role": "system", "content": "\n".join(lines)})
        except Exception as e:
            log_suppressed(logger, e)
        return messages


def _parse_github_url(url: str) -> tuple[str, str, str, str]:
    """https://github.com/{owner}/{repo}[/tree/{branch}[/{subpath}]] →
    (owner, repo, branch|"", subpath|"")。非 github https 链接直接拒绝。"""
    u = urlparse((url or "").strip())
    if u.scheme != "https" or u.netloc.lower() not in ("github.com", "www.github.com"):
        raise ValueError("只支持 https://github.com/... 链接")
    parts = [p for p in u.path.split("/") if p]
    if len(parts) < 2:
        raise ValueError("链接需形如 https://github.com/<owner>/<repo>")
    owner, repo = parts[0], parts[1].removesuffix(".git")
    branch, subpath = "", ""
    if len(parts) >= 4 and parts[2] == "tree":
        branch = parts[3]
        subpath = "/".join(parts[4:])
    return owner, repo, branch, subpath


def _validate_public_https_url(value: str) -> str:
    normalized = (value or "").strip()
    if len(normalized) > 2048:
        raise ValueError("技能网址过长")
    parsed = urlparse(normalized)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("只支持不含账号信息的公开 HTTPS 网址")
    if parsed.port not in (None, 443):
        raise ValueError("网站导入只允许 HTTPS 默认端口")
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
        }
    except OSError as exc:
        raise ValueError("无法解析技能网址") from exc
    if not addresses:
        raise ValueError("技能网址没有可用地址")
    for raw in addresses:
        try:
            address = ipaddress.ip_address(raw)
        except ValueError as exc:
            raise ValueError("技能网址解析结果无效") from exc
        if not address.is_global:
            raise ValueError("拒绝访问本机、内网、链路本地或保留地址")
    return parsed.geturl()


def _download_public_https(url: str) -> tuple[bytes, str, str]:
    """Download with bounded redirects and a hard byte limit."""
    import urllib.error
    import urllib.request
    from urllib.parse import urljoin

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    current = _validate_public_https_url(url)
    for _ in range(4):
        req = urllib.request.Request(current, headers={"User-Agent": "HashMM-UserSkills/1.0"})
        try:
            with opener.open(req, timeout=30) as response:
                data = response.read(MAX_ZIP_BYTES + 1)
                if len(data) > MAX_ZIP_BYTES:
                    raise ValueError(f"下载内容超过 {MAX_ZIP_BYTES // (1024 * 1024)}MB 上限")
                return data, current, str(response.headers.get("Content-Type") or "")
        except urllib.error.HTTPError as exc:
            if exc.code not in {301, 302, 303, 307, 308}:
                raise ValueError(f"网站下载失败：HTTP {exc.code}") from exc
            location = str(exc.headers.get("Location") or "")
            if not location:
                raise ValueError("网站重定向缺少目标地址") from exc
            current = _validate_public_https_url(urljoin(current, location))
    raise ValueError("网站重定向次数过多")


def _filter_zip_to_subpath(data: bytes, subpath: str) -> bytes:
    """codeload zip 的顶层是 `<repo>-<branch>/`；重打包为只含 <top>/<subpath>/** 的 zip，
    便于统一走 install_from_zip_bytes 的 SKILL.md 发现逻辑。"""
    src = zipfile.ZipFile(io.BytesIO(data))
    names = src.namelist()
    top = names[0].split("/")[0] if names else ""
    want_prefix = f"{top}/{subpath.strip('/')}/"
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            if info.filename.startswith(want_prefix) and not info.is_dir():
                dst.writestr(info.filename[len(f"{top}/"):], src.read(info))
    buf = out.getvalue()
    if len(buf) <= 22:      # 空 zip 只有 EOCD 22 字节
        raise ValueError(f"仓库里没有找到子目录：{subpath}")
    return buf


_manager: SkillPackManager | None = None
_user_managers: dict[str, SkillPackManager] = {}


def get_skill_pack_manager() -> SkillPackManager:
    global _manager
    if _manager is None:
        _manager = SkillPackManager()
        try:
            _manager.seed_builtins()
        except Exception as e:
            log_suppressed(logger, e)
    return _manager


def get_user_skill_pack_manager(user_id: str) -> SkillPackManager:
    """Return an owner-isolated skill library without exposing the owner id."""
    owner = (user_id or "").strip()
    if not owner or owner == "anonymous":
        raise ValueError("用户技能需要已登录账号")
    key = hashlib.sha256(owner.encode("utf-8")).hexdigest()[:32]
    manager = _user_managers.get(key)
    if manager is None:
        manager = SkillPackManager(_data_root() / "user_skill_packs" / key)
        _user_managers[key] = manager
    return manager
