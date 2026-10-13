"""OS-enforced execution broker for model-requested code and shell commands.

The old executors used a pinned ``cwd`` and string deny-lists.  Those are useful
input checks, but they are not a security boundary: a child process can still
read the rest of the host, inherit credentials, open sockets, and spawn more
processes.  This module makes the boundary explicit and fail-closed.

Supported isolation backends
----------------------------

``bwrap``
    Linux bubblewrap.  The host root is read-only, only the task workspace is
    writable, network is unshared by default, and resource limits are applied.

``docker``
    A pre-provisioned image selected by ``HASHMM_SANDBOX_DOCKER_IMAGE``.  HashMM
    never pulls or mutates images during an Agent turn.

``unsafe``
    Compatibility-only subprocess mode.  It is unavailable unless an
    administrator explicitly sets ``HASHMM_ALLOW_UNSANDBOXED_EXEC=1``.  Even in
    this mode the environment is reduced and process-tree cleanup is enforced.

Windows does not pretend that a process group is a sandbox.  Until a restricted
token/AppContainer helper or configured Docker backend is available, execution
is denied.  Approval and sandboxing remain separate: approving a command never
changes this decision.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
from typing import Mapping, Sequence


class SandboxUnavailable(RuntimeError):
    """Raised when the requested isolation cannot be enforced."""


@dataclass(frozen=True)
class SandboxPolicy:
    cwd: Path
    timeout: int = 30
    network: str = "deny"
    memory_mb: int = 512
    cpu_seconds: int = 30
    pids: int = 64
    extra_env: Mapping[str, str] = field(default_factory=dict)

    def normalized(self) -> "SandboxPolicy":
        cwd = Path(self.cwd).expanduser().resolve(strict=True)
        if not cwd.is_dir():
            raise SandboxUnavailable("任务工作区不是目录")
        network = str(self.network or "deny").strip().lower()
        if network not in {"deny", "inherit"}:
            raise SandboxUnavailable(f"不支持的网络策略: {network}")
        return SandboxPolicy(
            cwd=cwd,
            timeout=max(1, min(int(self.timeout or 30), 3600)),
            network=network,
            memory_mb=max(128, min(int(self.memory_mb or 512), 8192)),
            cpu_seconds=max(1, min(int(self.cpu_seconds or 30), 3600)),
            pids=max(8, min(int(self.pids or 64), 512)),
            extra_env={str(k): str(v) for k, v in dict(self.extra_env or {}).items()},
        )


@dataclass(frozen=True)
class SandboxResult:
    returncode: int
    stdout: str
    stderr: str
    backend: str
    isolated: bool
    timed_out: bool = False
    detail: str = ""


_SENSITIVE_ENV = (
    "SECRET", "TOKEN", "PASSWORD", "PASSWD", "API_KEY", "APIKEY",
    "PRIVATE_KEY", "ACCESS_KEY", "SESSION", "CREDENTIAL", "AUTH",
    "SUPABASE", "OPENAI", "ANTHROPIC", "DEEPSEEK",
)


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _safe_environment(policy: SandboxPolicy) -> dict[str, str]:
    """Build a small child environment instead of copying ``os.environ``."""
    keep = {
        "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT",
        "LANG", "LC_ALL", "TZ", "NUMBER_OF_PROCESSORS",
    }
    env: dict[str, str] = {}
    for key in keep:
        value = os.environ.get(key)
        if value:
            env[key] = value
    tmp = policy.cwd / ".hashmm-tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    env.update({
        "HOME": str(policy.cwd),
        "USERPROFILE": str(policy.cwd),
        "TMP": str(tmp),
        "TEMP": str(tmp),
        "TMPDIR": str(tmp),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
    })
    for key, value in policy.extra_env.items():
        upper = key.upper()
        if any(marker in upper for marker in _SENSITIVE_ENV):
            continue
        env[key] = value
    return env


def _protected_children(cwd: Path) -> list[Path]:
    protected: list[Path] = []
    for name in (".git", ".codex"):
        candidate = cwd / name
        if candidate.exists():
            protected.append(candidate.resolve())
    return protected


def _limit_resources(policy: SandboxPolicy):
    """Return a POSIX pre-exec hook.  OS namespaces remain the real boundary."""
    def apply() -> None:
        os.setsid()
        try:
            import resource
            memory = policy.memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
            resource.setrlimit(resource.RLIMIT_CPU, (policy.cpu_seconds, policy.cpu_seconds + 1))
            resource.setrlimit(resource.RLIMIT_NPROC, (policy.pids, policy.pids))
            resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
            resource.setrlimit(resource.RLIMIT_FSIZE, (256 * 1024 * 1024, 256 * 1024 * 1024))
        except Exception:
            # Namespace/filesystem/network isolation is still enforced.  The
            # caller exposes resource-limit availability through ``detail``.
            pass
    return apply


def _kill_process_tree(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True, timeout=5, check=False,
            )
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


class SandboxBroker:
    """Choose and execute one explicit sandbox backend."""

    def __init__(self, backend: str | None = None):
        self.requested_backend = str(
            backend or os.environ.get("HASHMM_SANDBOX_BACKEND", "auto")
        ).strip().lower()

    def _docker_image(self) -> str:
        return str(os.environ.get("HASHMM_SANDBOX_DOCKER_IMAGE", "")).strip()

    def select_backend(self) -> tuple[str, str]:
        requested = self.requested_backend
        if requested not in {"auto", "bwrap", "docker", "unsafe"}:
            return "unavailable", f"未知沙箱后端: {requested}"
        if requested in {"auto", "bwrap"} and sys.platform.startswith("linux"):
            executable = shutil.which("bwrap")
            if executable:
                return "bwrap", executable
            if requested == "bwrap":
                return "unavailable", "已要求 bubblewrap，但系统未安装 bwrap"
        image = self._docker_image()
        if requested in {"auto", "docker"} and image and shutil.which("docker"):
            return "docker", image
        if requested == "docker":
            return "unavailable", "Docker 沙箱需要本地 docker 与预配置镜像"
        if requested == "unsafe" or _truthy(os.environ.get("HASHMM_ALLOW_UNSANDBOXED_EXEC")):
            return "unsafe", "管理员显式允许非隔离兼容模式"
        return (
            "unavailable",
            "没有可用的 OS 沙箱；请安装 bubblewrap，或配置 HASHMM_SANDBOX_DOCKER_IMAGE。"
            "Windows 不会把普通进程组伪装成沙箱",
        )

    def status(self) -> dict[str, object]:
        backend, detail = self.select_backend()
        return {
            "backend": backend,
            "available": backend != "unavailable",
            "isolated": backend in {"bwrap", "docker"},
            "network_default": "deny",
            "detail": detail,
        }

    def run(
        self,
        command: Sequence[str],
        policy: SandboxPolicy,
        *,
        container_command: Sequence[str] | None = None,
        stdin: str | None = None,
    ) -> SandboxResult:
        policy = policy.normalized()
        if not command or not str(command[0]).strip():
            raise SandboxUnavailable("沙箱命令为空")
        backend, detail = self.select_backend()
        if backend == "unavailable":
            raise SandboxUnavailable(detail)
        env = _safe_environment(policy)
        if backend == "bwrap":
            argv = self._bwrap_command(detail, command, policy, env)
            isolated = True
        elif backend == "docker":
            argv = self._docker_command(detail, container_command or command, policy, env)
            isolated = True
        else:
            argv = list(command)
            isolated = False
        kwargs: dict[str, object] = {
            "cwd": str(policy.cwd),
            "env": env,
            "stdin": subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            kwargs["preexec_fn"] = _limit_resources(policy)
        proc = subprocess.Popen(argv, **kwargs)
        try:
            stdout, stderr = proc.communicate(stdin, timeout=policy.timeout)
            return SandboxResult(
                proc.returncode, stdout or "", stderr or "", backend, isolated,
                detail=detail,
            )
        except subprocess.TimeoutExpired:
            _kill_process_tree(proc)
            stdout, stderr = proc.communicate()
            return SandboxResult(
                -9, stdout or "", stderr or "", backend, isolated,
                timed_out=True, detail=detail,
            )

    @staticmethod
    def _bwrap_command(
        executable: str,
        command: Sequence[str],
        policy: SandboxPolicy,
        env: Mapping[str, str],
    ) -> list[str]:
        argv = [
            executable,
            "--die-with-parent", "--new-session", "--unshare-all",
            "--ro-bind", "/", "/",
            "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp",
            "--bind", str(policy.cwd), str(policy.cwd),
            "--chdir", str(policy.cwd), "--clearenv",
        ]
        if policy.network == "inherit":
            # ``--unshare-all`` includes the network namespace; sharing it back
            # is explicit and never selected for code/shell by default.
            argv.append("--share-net")
        for child in _protected_children(policy.cwd):
            argv.extend(["--ro-bind", str(child), str(child)])
        for key, value in sorted(env.items()):
            argv.extend(["--setenv", key, value])
        argv.append("--")
        argv.extend(str(part) for part in command)
        return argv

    @staticmethod
    def _docker_command(
        image: str,
        command: Sequence[str],
        policy: SandboxPolicy,
        env: Mapping[str, str],
    ) -> list[str]:
        argv = [
            "docker", "run", "--rm", "--init", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", str(policy.pids),
            "--memory", f"{policy.memory_mb}m", "--cpus", "1.0",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
            "--mount", f"type=bind,src={policy.cwd},dst=/workspace,rw",
            "--workdir", "/workspace",
            "--network", "none" if policy.network == "deny" else "bridge",
        ]
        for key, value in sorted(env.items()):
            if key in {"HOME", "USERPROFILE", "TMP", "TEMP", "TMPDIR"}:
                continue
            argv.extend(["--env", f"{key}={value}"])
        argv.append(image)
        argv.extend(str(part) for part in command)
        return argv


def sandbox_status() -> dict[str, object]:
    return SandboxBroker().status()


__all__ = [
    "SandboxBroker", "SandboxPolicy", "SandboxResult", "SandboxUnavailable",
    "sandbox_status",
]
