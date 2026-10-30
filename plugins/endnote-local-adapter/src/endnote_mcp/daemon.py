"""Serve independent MCP sessions in one process over a private Unix socket."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import signal
import stat

import anyio
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage

from .server import mcp


MAX_MESSAGE_BYTES = 16 * 1024 * 1024
logger = logging.getLogger(__name__)


async def serve_session(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    read_send, read_receive = anyio.create_memory_object_stream[
        SessionMessage | Exception
    ](0)
    write_send, write_receive = anyio.create_memory_object_stream[SessionMessage](0)

    async def read_messages() -> None:
        async with read_send:
            while line := await reader.readline():
                try:
                    message = JSONRPCMessage.model_validate_json(line)
                except ValueError as error:
                    await read_send.send(error)
                else:
                    await read_send.send(SessionMessage(message))

    async def write_messages() -> None:
        async with write_receive:
            async for message in write_receive:
                line = message.message.model_dump_json(by_alias=True, exclude_none=True)
                writer.write(line.encode("utf-8") + b"\n")
                await writer.drain()

    async with read_receive, write_send, anyio.create_task_group() as tasks:
        tasks.start_soon(read_messages)
        tasks.start_soon(write_messages)
        # FastMCP exposes custom transports through its low-level server. Match
        # run_stdio_async, with new streams and a new ServerSession for each client.
        server = mcp._mcp_server
        await server.run(
            read_receive, write_send, server.create_initialization_options()
        )
        tasks.cancel_scope.cancel()


async def serve(path: Path, idle_timeout: float) -> None:
    if path.exists() or path.is_symlink():
        if not stat.S_ISSOCK(path.lstat().st_mode):
            raise RuntimeError(f"Refusing to replace a non-socket file: {path}")
        path.unlink()
    loop = asyncio.get_running_loop()
    stopped = asyncio.Event()
    clients: set[asyncio.Task[None]] = set()
    last_disconnect = loop.time()

    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            await serve_session(reader, writer)
        except Exception as error:
            # A broken or malformed client must not stop the other sessions.
            logger.warning("EndNote connection closed: %s", type(error).__name__)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (BrokenPipeError, ConnectionResetError):
                pass

    def disconnected(task: asyncio.Task[None]) -> None:
        nonlocal last_disconnect
        clients.discard(task)
        if not clients:
            last_disconnect = loop.time()

    def connected(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.create_task(handle(reader, writer))
        clients.add(task)
        task.add_done_callback(disconnected)

    listener = await asyncio.start_unix_server(
        connected, str(path), limit=MAX_MESSAGE_BYTES
    )
    path.chmod(0o600)
    pid_path = path.with_suffix(".pid")
    pid_path.write_text(str(os.getpid()), encoding="ascii")
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stopped.set)
    try:
        while not stopped.is_set():
            remaining = idle_timeout - (loop.time() - last_disconnect)
            if not clients and remaining <= 0:
                break
            try:
                await asyncio.wait_for(stopped.wait(), timeout=min(idle_timeout, 1.0))
            except TimeoutError:
                pass
    finally:
        listener.close()
        await listener.wait_closed()
        for task in clients:
            task.cancel()
        if clients:
            await asyncio.gather(*clients, return_exceptions=True)
        path.unlink(missing_ok=True)
        pid_path.unlink(missing_ok=True)


def run(path: Path, lock_fd: int, idle_timeout: float) -> None:
    # The starter acquired this lock and passed its open file description to us.
    # Keep it until the listening socket has been removed, including on shutdown.
    os.fstat(lock_fd)
    os.umask(0o077)
    logging.basicConfig(level=logging.WARNING)
    try:
        anyio.run(serve, path, idle_timeout)
    finally:
        os.close(lock_fd)
