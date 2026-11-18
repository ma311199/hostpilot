"""Verify Linux Python execution is bounded and isolated from host resources."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from hostpilot.sandbox.linux import (
    LinuxJavaScriptSandbox,
    LinuxPythonSandbox,
    LinuxShellSandbox,
)


def test_host_supported_requires_a_successful_isolation_probe(monkeypatch) -> None:
    sandbox = LinuxPythonSandbox(bubblewrap_path="/fake/bwrap", prlimit_path="/fake/prlimit")
    calls = 0

    def fail_probe(*args, **kwargs):
        nonlocal calls
        calls += 1
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr("hostpilot.sandbox.linux.sys.platform", "linux")
    monkeypatch.setattr("hostpilot.sandbox.linux.subprocess.run", fail_probe)

    assert not sandbox.host_supported
    assert not sandbox.host_supported
    assert "Linux 隔离探针失败" in sandbox.unavailable_reason
    assert calls == 1


def test_missing_node_runtime_provides_install_hint(tmp_path) -> None:
    sandbox = LinuxJavaScriptSandbox(node_executable=tmp_path / "missing-node")

    assert not sandbox.host_supported
    assert "Node.js" in sandbox.unavailable_reason


def test_missing_bubblewrap_provides_linux_package_hint(monkeypatch) -> None:
    monkeypatch.setattr(
        "hostpilot.sandbox.linux.shutil.which",
        lambda name: "/usr/bin/prlimit" if name == "prlimit" else None,
    )
    sandbox = LinuxPythonSandbox()

    assert not sandbox.host_supported
    assert "bubblewrap" in sandbox.unavailable_reason
    assert "apt install bubblewrap util-linux" in sandbox.unavailable_reason


def test_linux_sandbox_denies_execution_when_bubblewrap_is_missing() -> None:
    result = asyncio.run(LinuxPythonSandbox(bubblewrap_path="/missing/bwrap").run("print(1)"))

    assert result.status == "sandbox_unavailable"
    assert result.stdout == ""
    assert "source was not executed" in result.stderr


@pytest.mark.skipif(
    not LinuxPythonSandbox().host_supported,
    reason="Linux bubblewrap and prlimit are not installed on this host.",
)
def test_linux_sandbox_blocks_host_files_network_and_process_creation() -> None:
    code = "\n".join(
        (
            "import os, pathlib, socket",
            "print('host_file_visible=' + "
            "str(pathlib.Path('/home/mameng/hostpilot/README.md').exists()))",
            "print('passwd_visible=' + str(pathlib.Path('/etc/passwd').exists()))",
            "try:",
            "    socket.create_connection(('1.1.1.1', 80), timeout=0.2)",
            "except OSError:",
            "    print('network_blocked=True')",
            "else:",
            "    print('network_blocked=False')",
            "try:",
            "    os.fork()",
            "except OSError:",
            "    print('fork_blocked=True')",
            "else:",
            "    print('fork_blocked=False')",
        )
    )

    result = asyncio.run(LinuxPythonSandbox().run(code))

    assert result.status == "completed"
    assert "host_file_visible=False" in result.stdout
    assert "passwd_visible=False" in result.stdout
    assert "network_blocked=True" in result.stdout
    assert "fork_blocked=True" in result.stdout


@pytest.mark.skipif(
    not LinuxPythonSandbox().host_supported,
    reason="Linux bubblewrap and prlimit are not installed on this host.",
)
def test_linux_sandbox_bounds_output_and_wall_time() -> None:
    output_runner = LinuxPythonSandbox(max_output_bytes=64)
    output_result = asyncio.run(output_runner.run("print('x' * 100_000)"))

    timeout_runner = LinuxPythonSandbox(wall_time_seconds=0.1)
    timeout_result = asyncio.run(timeout_runner.run("while True: pass"))

    assert output_result.status == "output_limit_exceeded"
    assert len(output_result.stdout.encode("utf-8")) <= 64
    assert timeout_result.status == "timed_out"


@pytest.mark.skipif(
    not LinuxJavaScriptSandbox().host_supported,
    reason="Linux bubblewrap and a runnable Node.js runtime are unavailable.",
)
def test_javascript_sandbox_runs_source_without_host_files_or_network(tmp_path) -> None:
    host_file = tmp_path / "private.txt"
    host_file.write_text("host-secret", encoding="utf-8")
    source = (
        "const fs = require('node:fs'); "
        "const net = require('node:net'); "
        f"console.log('host_file=' + fs.existsSync({str(host_file)!r})); "
        "const socket = net.createConnection({host:'1.1.1.1', port:80}); "
        "socket.setTimeout(250); "
        "socket.on('error', () => { console.log('network_blocked=true'); }); "
        "socket.on('timeout', () => { socket.destroy(); console.log('network_blocked=true'); });"
    )

    result = asyncio.run(LinuxJavaScriptSandbox().run(source))

    assert result.status == "completed"
    assert "host_file=false" in result.stdout
    assert "network_blocked=true" in result.stdout


@pytest.mark.skipif(
    not LinuxShellSandbox().host_supported,
    reason="Linux bubblewrap and a POSIX shell runtime are unavailable.",
)
def test_shell_sandbox_has_read_only_system_tools_and_no_host_access(tmp_path) -> None:
    host_file = tmp_path / "private.txt"
    host_file.write_text("host-secret", encoding="utf-8")
    source = (
        f"if [ -e {str(host_file)!r} ]; then echo host_file=true; "
        "else echo host_file=false; fi; "
        "if [ -x /usr/bin/cat ]; then echo system_tools=available; "
        "else echo system_tools=unavailable; fi; "
        "if touch /tmp/sandbox-write-test 2>/dev/null; then echo write_allowed=true; "
        "else echo write_allowed=false; fi;"
    )

    result = asyncio.run(LinuxShellSandbox().run(source))

    assert result.status == "completed"
    assert "host_file=false" in result.stdout
    assert "system_tools=available" in result.stdout
    assert "write_allowed=false" in result.stdout
