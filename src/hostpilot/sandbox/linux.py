"""Fail-closed script execution inside Linux bubblewrap namespaces."""

from __future__ import annotations

import asyncio
import math
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Literal

from hostpilot.contracts import SandboxPolicy, SandboxResourceLimits

SandboxRunStatus = Literal[
    "completed",
    "failed",
    "timed_out",
    "output_limit_exceeded",
    "sandbox_unavailable",
]


@dataclass(frozen=True)
class SandboxRunResult:
    """Bounded result returned by one isolated Python invocation."""

    status: SandboxRunStatus
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int


class LinuxPythonSandbox:
    """Run Python source with no host workspace, network, or inherited environment."""

    def __init__(
        self,
        *,
        policy: SandboxPolicy | None = None,
        bubblewrap_path: str | None = None,
        prlimit_path: str | None = None,
        python_executable: Path | str | None = None,
        wall_time_seconds: float = 5,
        max_source_bytes: int = 65_536,
        max_output_bytes: int = 16_384,
        capability: str = "python.execute",
        extra_mounts: tuple[tuple[str, str, str], ...] = (),
    ) -> None:
        if wall_time_seconds <= 0 or max_source_bytes < 1 or max_output_bytes < 1:
            raise ValueError("Sandbox limits must be positive.")
        self.policy = policy or SandboxPolicy(
            allowed=True,
            capabilities=frozenset({"python.execute"}),
            network_access=False,
            filesystem_access=False,
            resource_limits=SandboxResourceLimits(
                cpu_seconds=2,
                memory_bytes=268_435_456,
                max_processes=1,
            ),
        )
        self.bubblewrap_path = bubblewrap_path or shutil.which("bwrap")
        self.prlimit_path = prlimit_path or shutil.which("prlimit")
        self.python_executable = Path(python_executable or sys.executable).resolve()
        self.python_root = Path(sys.base_prefix).resolve()
        self.wall_time_seconds = wall_time_seconds
        self.max_source_bytes = max_source_bytes
        self.max_output_bytes = max_output_bytes
        self.capability = capability
        self.extra_mounts = extra_mounts
        self.runtime_name = "Python"

    def _runtime_supported(self) -> bool:
        """Check whether this runner's executable and runtime tree are available."""
        return (
            self.python_executable.is_file()
            and self.python_root.is_dir()
            and self.python_executable.is_relative_to(self.python_root)
        )

    def _runtime_command(self, source: str) -> tuple[str, ...]:
        """Build the runtime-specific command executed inside the namespace."""
        python_relative = self.python_executable.relative_to(self.python_root)
        return (
            str(Path("/") / self.python_root.relative_to("/") / python_relative),
            "-I",
            "-S",
            "-c",
            source,
        )

    def _probe_source(self) -> str:
        """Return harmless runtime source used to verify namespace startup."""
        return "pass"

    def _extra_mounts(self) -> tuple[tuple[str, str, str], ...]:
        """Return extra trusted runtime mounts needed by specialized runners."""
        return ()

    @cached_property
    def host_supported(self) -> bool:
        """Probe whether the configured runtime can start inside the sandbox."""
        return self.unavailable_reason is None

    @cached_property
    def unavailable_reason(self) -> str | None:
        """Explain which host prerequisite prevents this sandbox from starting."""
        if sys.platform != "linux":
            return "此沙箱目前只支持 Linux；请在 Linux 环境运行 HostPilot。"
        if self.bubblewrap_path is None:
            return (
                "缺少 Bubblewrap（bwrap）。请安装系统包 bubblewrap；"
                "Ubuntu/Debian 可运行：sudo apt install bubblewrap util-linux。"
            )
        if self.prlimit_path is None:
            return (
                "缺少 prlimit（util-linux）。请安装 util-linux；"
                "Ubuntu/Debian 可运行：sudo apt install util-linux。"
            )
        if not self._runtime_supported():
            return f"未找到可用的{self.runtime_name}沙箱运行时，请安装后重启 HostPilot。"
        if not Path("/usr").is_dir() or not Path("/lib").exists():
            return "系统缺少沙箱运行所需的 /usr 或 /lib 运行时目录。"
        try:
            result = subprocess.run(
                self._command(self._probe_source()),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=3,
            )
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return (
                "Bubblewrap/prlimit 已检测到，但隔离探针无法启动；"
                "请检查 Linux 用户命名空间是否启用，以及容器是否允许创建命名空间。"
            )
        if result.returncode != 0:
            return (
                "Bubblewrap/prlimit 已检测到，但 Linux 隔离探针失败；"
                "请检查用户命名空间和容器权限，详见 Linux 安装指南。"
            )
        return None

    async def run(
        self,
        source: str,
        *,
        extra_mounts: tuple[tuple[str, str, str], ...] = (),
    ) -> SandboxRunResult:
        """Execute one source string, returning bounded output or a fail-closed status."""
        started = time.monotonic()
        try:
            encoded = source.encode("utf-8")
        except UnicodeEncodeError:
            return self._result("failed", None, "", "Source must be valid UTF-8.", started)
        if not source.strip() or len(encoded) > self.max_source_bytes or "\x00" in source:
            return self._result(
                "failed", None, "", "Source is empty or exceeds the size limit.", started
            )
        if (
            not self.policy.allowed
            or self.policy.network_access
            or self.policy.filesystem_access
            or self.capability not in self.policy.capabilities
        ):
            return self._result(
                "sandbox_unavailable", None, "", "Sandbox policy is not supported.", started
            )
        if not self.host_supported:
            return self._result(
                "sandbox_unavailable",
                None,
                "",
                "Linux bubblewrap and prlimit are required; source was not executed.",
                started,
            )

        try:
            process = await asyncio.create_subprocess_exec(
                *self._command(source, extra_mounts=extra_mounts),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except (OSError, ValueError):
            return self._result(
                "sandbox_unavailable",
                None,
                "",
                "The isolated script runtime could not be started; source was not executed.",
                started,
            )

        output = {"stdout": bytearray(), "stderr": bytearray()}
        output_exceeded = asyncio.Event()
        readers = (
            asyncio.create_task(
                self._read_limited(
                    process.stdout, output["stdout"], output, output_exceeded, process
                )
            ),
            asyncio.create_task(
                self._read_limited(
                    process.stderr, output["stderr"], output, output_exceeded, process
                )
            ),
        )
        try:
            async with asyncio.timeout(self.wall_time_seconds):
                await asyncio.gather(*readers)
                exit_code = await process.wait()
        except TimeoutError:
            await self._kill_process_group(process, readers)
            return self._result(
                "timed_out",
                None,
                self._decode(output["stdout"]),
                self._decode(output["stderr"]),
                started,
            )
        except asyncio.CancelledError:
            await self._kill_process_group(process, readers)
            raise

        stdout = self._decode(output["stdout"])
        stderr = self._decode(output["stderr"])
        if output_exceeded.is_set():
            return self._result("output_limit_exceeded", exit_code, stdout, stderr, started)
        if stderr.startswith(("bwrap:", "prlimit:")) and exit_code != 0:
            return self._result(
                "sandbox_unavailable",
                exit_code,
                stdout,
                "Linux isolation setup failed; source was not executed.",
                started,
            )
        status: SandboxRunStatus = "completed" if exit_code == 0 else "failed"
        return self._result(status, exit_code, stdout, stderr, started)

    def _command(
        self,
        source: str,
        *,
        extra_mounts: tuple[tuple[str, str, str], ...] = (),
    ) -> tuple[str, ...]:
        limits = self.policy.resource_limits
        command = [
            self.bubblewrap_path or "bwrap",
            "--unshare-all",
            "--die-with-parent",
            "--new-session",
            "--cap-drop",
            "ALL",
            "--ro-bind",
            "/usr",
            "/usr",
            "--ro-bind",
            "/lib",
            "/lib",
        ]
        if Path("/lib64").exists():
            command.extend(("--ro-bind", "/lib64", "/lib64"))
        command.extend(("--dir", "/etc"))
        if Path("/etc/ld.so.cache").is_file():
            command.extend(("--ro-bind", "/etc/ld.so.cache", "/etc/ld.so.cache"))
        if not self.python_root.is_relative_to(Path("/usr")):
            command.extend(("--ro-bind", str(self.python_root), str(self.python_root)))
        mounts = (*self._extra_mounts(), *self.extra_mounts, *extra_mounts)
        for mount_mode, source_path, target_path in mounts:
            target_parent = str(Path(target_path).parent)
            if target_parent != "/":
                command.extend(("--dir", target_parent))
            if Path(source_path).is_dir():
                command.extend(("--dir", target_path))
            command.extend((mount_mode, source_path, target_path))
        command.extend(
            (
                "--symlink",
                "usr/bin",
                "/bin",
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "--clearenv",
                "--setenv",
                "HOME",
                "/nonexistent",
                "--setenv",
                "PATH",
                "/usr/bin:/bin",
                "--chdir",
                "/",
                "--",
                self.prlimit_path or "prlimit",
                f"--cpu={math.ceil(limits.cpu_seconds)}:{math.ceil(limits.cpu_seconds) + 1}",
                f"--as={limits.memory_bytes}:{limits.memory_bytes}",
                f"--nproc={limits.max_processes}:{limits.max_processes}",
                "--",
            )
        )
        command.extend(self._runtime_command(source))
        return tuple(command)

    async def _read_limited(
        self,
        stream: asyncio.StreamReader | None,
        destination: bytearray,
        buffers: dict[str, bytearray],
        exceeded: asyncio.Event,
        process: asyncio.subprocess.Process,
    ) -> None:
        if stream is None:
            return
        while chunk := await stream.read(4096):
            total = sum(len(buffer) for buffer in buffers.values())
            available = max(0, self.max_output_bytes - total)
            destination.extend(chunk[:available])
            if len(chunk) > available:
                exceeded.set()
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    async def _kill_process_group(
        self,
        process: asyncio.subprocess.Process,
        readers: tuple[asyncio.Task[None], asyncio.Task[None]],
    ) -> None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()
        await asyncio.gather(*readers, return_exceptions=True)

    def _result(
        self,
        status: SandboxRunStatus,
        exit_code: int | None,
        stdout: str,
        stderr: str,
        started: float,
    ) -> SandboxRunResult:
        return SandboxRunResult(
            status=status,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_ms=round((time.monotonic() - started) * 1000),
        )

    @staticmethod
    def _decode(content: bytearray) -> str:
        return bytes(content).decode("utf-8", errors="replace")


class LinuxJavaScriptSandbox(LinuxPythonSandbox):
    """Run Node.js source in a networkless, read-only Linux namespace."""

    def __init__(
        self,
        *,
        policy: SandboxPolicy | None = None,
        bubblewrap_path: str | None = None,
        prlimit_path: str | None = None,
        node_executable: Path | str | None = None,
        wall_time_seconds: float = 5,
        max_source_bytes: int = 65_536,
        max_output_bytes: int = 16_384,
    ) -> None:
        node_path = Path(node_executable or shutil.which("node") or "/missing/node")
        self.node_executable = node_path.resolve()
        self.node_runtime_root = self.node_executable.parent
        super().__init__(
            policy=policy
            or SandboxPolicy(
                allowed=True,
                capabilities=frozenset({"javascript.execute"}),
                network_access=False,
                filesystem_access=False,
                resource_limits=SandboxResourceLimits(
                    cpu_seconds=2,
                    memory_bytes=2_147_483_648,
                    max_processes=16,
                ),
            ),
            bubblewrap_path=bubblewrap_path,
            prlimit_path=prlimit_path,
            python_executable=sys.executable,
            wall_time_seconds=wall_time_seconds,
            max_source_bytes=max_source_bytes,
            max_output_bytes=max_output_bytes,
            capability="javascript.execute",
        )
        self.runtime_name = "Node.js"

    def _runtime_supported(self) -> bool:
        return self.node_executable.is_file() and self.node_runtime_root.is_dir()

    def _runtime_command(self, source: str) -> tuple[str, ...]:
        return ("/opt/hostpilot-node/node", "--max-old-space-size=256", "-e", source)

    def _probe_source(self) -> str:
        return "console.log('sandbox-probe')"

    def _extra_mounts(self) -> tuple[tuple[str, str, str], ...]:
        return (("--ro-bind", str(self.node_runtime_root), "/opt/hostpilot-node"),)


class LinuxShellSandbox(LinuxPythonSandbox):
    """Run POSIX shell source without network or writable host filesystem mounts."""

    def __init__(
        self,
        *,
        policy: SandboxPolicy | None = None,
        bubblewrap_path: str | None = None,
        prlimit_path: str | None = None,
        shell_executable: Path | str = "/bin/sh",
        wall_time_seconds: float = 5,
        max_source_bytes: int = 65_536,
        max_output_bytes: int = 16_384,
    ) -> None:
        self.shell_executable = Path(shell_executable).resolve()
        super().__init__(
            policy=policy
            or SandboxPolicy(
                allowed=True,
                capabilities=frozenset({"shell.execute"}),
                network_access=False,
                filesystem_access=False,
                resource_limits=SandboxResourceLimits(
                    cpu_seconds=2,
                    memory_bytes=268_435_456,
                    max_processes=16,
                ),
            ),
            bubblewrap_path=bubblewrap_path,
            prlimit_path=prlimit_path,
            python_executable=sys.executable,
            wall_time_seconds=wall_time_seconds,
            max_source_bytes=max_source_bytes,
            max_output_bytes=max_output_bytes,
            capability="shell.execute",
        )
        self.runtime_name = "POSIX Shell"

    def _runtime_supported(self) -> bool:
        return self.shell_executable.is_file() and self.shell_executable.is_relative_to("/usr")

    def _runtime_command(self, source: str) -> tuple[str, ...]:
        shell_path = "/bin/" + self.shell_executable.name
        return (shell_path, "-c", source)

    def _probe_source(self) -> str:
        return ":"


__all__ = [
    "LinuxJavaScriptSandbox",
    "LinuxPythonSandbox",
    "LinuxShellSandbox",
    "SandboxRunResult",
    "SandboxRunStatus",
]
