from __future__ import annotations

import concurrent.futures
import contextlib
import json
import os
import selectors
import signal
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="Shared server uses Unix sockets."
)

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def wait_for(predicate: Callable[[], bool], timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail("Timed out waiting for the shared server lifecycle change.")
        time.sleep(0.025)


class ProxyClient:
    def __init__(self, runtime: Path, config: Path):
        with contextlib.ExitStack() as resources:
            self.stderr = resources.enter_context(tempfile.TemporaryFile())
            self.process = subprocess.Popen(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "endnote_mcp.shared",
                    "--runtime-dir",
                    str(runtime),
                    "--idle-timeout",
                    "0.5",
                    "--startup-timeout",
                    "10",
                ],
                cwd=PLUGIN_ROOT,
                env={
                    **os.environ,
                    "PYTHONPATH": str(PLUGIN_ROOT / "src"),
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "ENDNOTE_MCP_CONFIG": str(config),
                },
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self.stderr,
                bufsize=0,
            )
            assert self.process.stdin is not None
            assert self.process.stdout is not None
            resources.callback(self.process.stdout.close)
            self.selector = resources.enter_context(selectors.DefaultSelector())
            self.selector.register(self.process.stdout, selectors.EVENT_READ)
            self.pending = bytearray()
            self.resources = resources.pop_all()

    def diagnostics(self) -> str:
        self.stderr.seek(0)
        return self.stderr.read().decode("utf-8", errors="replace")[-4000:]

    def send(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        request_id: int | None = None,
    ) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if request_id is not None:
            message["id"] = request_id
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(message).encode() + b"\n")
        self.process.stdin.flush()

    def response(self, request_id: int, timeout: float = 15.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            if b"\n" in self.pending:
                line, _, remainder = self.pending.partition(b"\n")
                self.pending = bytearray(remainder)
                message = json.loads(line)
                if "id" not in message:
                    continue
                assert message["id"] == request_id, message
                return message
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not self.selector.select(remaining):
                pytest.fail(
                    f"No response to request {request_id}: {self.diagnostics()}"
                )
            assert self.process.stdout is not None
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                pytest.fail(f"Proxy closed before replying: {self.diagnostics()}")
            self.pending.extend(chunk)

    def initialize(self) -> None:
        self.send(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "shared-server-test", "version": "1"},
            },
            request_id=1,
        )
        assert "result" in self.response(1)
        self.send("notifications/initialized")

    def list_libraries(self, request_id: int = 2) -> dict[str, Any]:
        self.send(
            "tools/call",
            {"name": "list_libraries", "arguments": {}},
            request_id=request_id,
        )
        result = self.response(request_id)["result"]
        assert not result.get("isError", False), result
        if "structuredContent" in result:
            return result["structuredContent"]
        return json.loads(result["content"][0]["text"])

    def eof(self) -> None:
        assert self.process.stdin is not None
        if not self.process.stdin.closed:
            self.process.stdin.close()

    def cleanup(self) -> None:
        self.eof()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        self.resources.close()


class SharedServerHarness:
    def __init__(self, runtime: Path, fixtures: Path):
        self.runtime = runtime
        self.fixtures = fixtures
        self.clients: list[ProxyClient] = []

    def config(self, label: str = "fixture", *, name: str = "Fixture library") -> Path:
        path = self.fixtures / f"{label}.json"
        path.write_text(
            json.dumps(
                {
                    "defaultLibraryId": label,
                    "libraries": [
                        {
                            "id": label,
                            "name": name,
                            "path": str(self.fixtures / f"{label}.enl"),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return path

    def launch(self, config: Path, *, initialize: bool = True) -> ProxyClient:
        client = ProxyClient(self.runtime, config)
        self.clients.append(client)
        if initialize:
            client.initialize()
            self.pids()
        return client

    def pids(self) -> set[int]:
        result = set()
        for path in self.runtime.glob("*.pid"):
            with contextlib.suppress(FileNotFoundError):
                result.add(int(path.read_text(encoding="utf-8").strip()))
        return result

    def cleanup(self) -> None:
        for client in self.clients:
            client.cleanup()
        deadline = time.monotonic() + 3
        while list(self.runtime.glob("*.pid")) and time.monotonic() < deadline:
            time.sleep(0.025)
        for pid in self.pids():
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGTERM)


@pytest.fixture()
def shared_server(tmp_path: Path) -> Iterator[SharedServerHarness]:
    # pytest's full test directory names can exceed macOS's Unix socket path limit.
    with tempfile.TemporaryDirectory(prefix="enmcp-", dir="/tmp") as directory:
        runtime = Path(directory) / "run"
        runtime.mkdir(mode=0o700)
        harness = SharedServerHarness(runtime, tmp_path)
        try:
            yield harness
        finally:
            harness.cleanup()


def test_simultaneous_clients_share_one_daemon(
    shared_server: SharedServerHarness,
) -> None:
    config = shared_server.config()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        clients = list(executor.map(lambda _: shared_server.launch(config), range(4)))
    assert len(shared_server.pids()) == 1
    assert len(list(shared_server.runtime.glob("*.sock"))) == 1
    for client in clients:
        assert client.list_libraries()["default_library_id"] == "fixture"


def test_request_ids_are_isolated_between_clients(
    shared_server: SharedServerHarness,
) -> None:
    config = shared_server.config()
    first = shared_server.launch(config)
    second = shared_server.launch(config)
    first.send("tools/list", {}, request_id=42)
    second.send(
        "tools/call",
        {"name": "list_libraries", "arguments": {}},
        request_id=42,
    )
    # Read in reverse order, with the same JSON-RPC ID pending on both connections.
    second_result = second.response(42)["result"]
    first_result = first.response(42)["result"]
    assert "tools" not in second_result
    assert not second_result.get("isError", False)
    assert {tool["name"] for tool in first_result["tools"]} >= {
        "list_libraries",
        "get_reference",
    }
    assert "content" not in first_result
    assert len(shared_server.pids()) == 1


def test_closing_one_client_preserves_the_other(
    shared_server: SharedServerHarness,
) -> None:
    config = shared_server.config()
    first = shared_server.launch(config)
    second = shared_server.launch(config)
    pids = shared_server.pids()
    first.eof()
    assert first.process.wait(timeout=8) == 0, first.diagnostics()
    time.sleep(
        1.0
    )  # More than one daemon idle timeout while the second client is open.
    assert second.process.poll() is None
    assert shared_server.pids() == pids
    assert second.list_libraries()["default_library_id"] == "fixture"


def test_all_client_eof_stops_idle_daemon(shared_server: SharedServerHarness) -> None:
    client = shared_server.launch(shared_server.config())
    assert len(shared_server.pids()) == 1
    client.eof()
    assert client.process.wait(timeout=8) == 0, client.diagnostics()
    wait_for(lambda: not list(shared_server.runtime.glob("*.pid")))
    assert not list(shared_server.runtime.glob("*.sock"))


def test_daemon_death_exits_proxy_and_next_launch_recovers(
    shared_server: SharedServerHarness,
) -> None:
    config = shared_server.config()
    first = shared_server.launch(config)
    (original_pid,) = shared_server.pids()
    os.kill(original_pid, signal.SIGKILL)
    first.process.wait(timeout=8)
    second = shared_server.launch(config)
    (replacement_pid,) = shared_server.pids()
    assert replacement_pid != original_pid
    assert second.list_libraries()["default_library_id"] == "fixture"


def test_config_paths_use_separate_daemons(shared_server: SharedServerHarness) -> None:
    first = shared_server.launch(shared_server.config("first"))
    second = shared_server.launch(shared_server.config("second"))
    assert len(shared_server.pids()) == 2
    assert first.list_libraries()["default_library_id"] == "first"
    assert second.list_libraries()["default_library_id"] == "second"


def test_large_response_is_not_truncated(shared_server: SharedServerHarness) -> None:
    name = ("Large fixture library \u03bb " * 5000).strip()
    assert len(name.encode("utf-8")) > 65536
    client = shared_server.launch(shared_server.config(name=name))
    assert client.list_libraries()["libraries"][0]["name"] == name


@pytest.mark.parametrize(
    "payload",
    [b"not valid JSON\n", b"x" * (16 * 1024 * 1024 + 1) + b"\n"],
    ids=["malformed", "oversized"],
)
def test_bad_connection_preserves_other_sessions(
    shared_server: SharedServerHarness, payload: bytes
) -> None:
    client = shared_server.launch(shared_server.config())
    pids = shared_server.pids()
    (socket_path,) = shared_server.runtime.glob("*.sock")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as broken:
        broken.settimeout(5)
        broken.connect(str(socket_path))
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            broken.sendall(payload)
    assert client.list_libraries()["default_library_id"] == "fixture"
    assert shared_server.pids() == pids
