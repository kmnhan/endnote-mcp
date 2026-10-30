from __future__ import annotations

import asyncio
from pathlib import Path
import threading

import anyio
import pytest

from endnote_mcp import server, shared


def test_runtime_directory_rejects_symlinks_and_public_permissions(
    tmp_path: Path,
) -> None:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(private, target_is_directory=True)
    with pytest.raises(RuntimeError, match="0700"):
        shared.private_runtime_directory(link)
    private.chmod(0o755)
    with pytest.raises(RuntimeError, match="0700"):
        shared.private_runtime_directory(private)


def test_startup_does_not_follow_lock_symlink(tmp_path: Path) -> None:
    target = tmp_path / "keep"
    target.write_text("preserve this file")
    path = tmp_path / "server.sock"
    path.with_suffix(".lock").symlink_to(target)
    with pytest.raises(OSError):
        shared._start_server(path, 300)
    assert target.read_text() == "preserve this file"


def test_server_identity_tracks_config_and_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "plugin"
    package = root / "src" / "endnote_mcp"
    package.mkdir(parents=True)
    for filename in ("pyproject.toml", "uv.lock"):
        (root / filename).write_text("unchanged")
    source = package / "shared.py"
    source.write_text("first version")
    monkeypatch.setattr(shared, "PLUGIN_ROOT", root)
    monkeypatch.setattr(shared, "__file__", str(source))
    monkeypatch.setenv("ENDNOTE_MCP_CONFIG", str(tmp_path / "first.json"))
    first = shared.server_key()
    monkeypatch.setenv("ENDNOTE_MCP_CONFIG", str(tmp_path / "second.json"))
    assert shared.server_key() != first
    monkeypatch.setenv("ENDNOTE_MCP_CONFIG", str(tmp_path / "first.json"))
    assert shared.server_key() == first
    source.write_text("second version")
    assert shared.server_key() != first


def test_slow_tool_does_not_block_another_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()

    def slow_search(**kwargs: object) -> dict[str, object]:
        started.set()
        if not release.wait(5):
            raise RuntimeError("Test did not release the slow tool")
        return {"results": [], "count": 0}

    monkeypatch.setattr(server.endnote, "search_references", slow_search)
    monkeypatch.setattr(server.endnote, "list_libraries", lambda: {"libraries": []})

    async def scenario() -> None:
        slow = asyncio.create_task(server.search_references("fixture"))
        try:
            with anyio.fail_after(2):
                while not started.is_set():
                    await anyio.sleep(0.01)
                assert await server.list_libraries() == {"libraries": []}
            assert not slow.done()
        finally:
            release.set()
            await slow

    anyio.run(scenario)
