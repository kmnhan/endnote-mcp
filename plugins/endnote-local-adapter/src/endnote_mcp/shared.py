"""Connect each stdio client to one private, shared EndNote server."""

from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import os
from pathlib import Path
import select
import socket
import stat
import subprocess
import sys
import time


PLUGIN_ROOT = Path(__file__).resolve().parents[2]
BUFFER_SIZE = 256 * 1024


def server_key() -> str:
    """Separate installations, code versions, environments, and library configs."""
    digest = hashlib.sha256()
    config = os.environ.get("ENDNOTE_MCP_CONFIG", "")
    if config:
        config = str(Path(config).expanduser().resolve())
        os.environ["ENDNOTE_MCP_CONFIG"] = config
    for value in (
        str(PLUGIN_ROOT),
        sys.executable,
        sys.version,
        str(Path.home()),
        config,
    ):
        digest.update(value.encode())
        digest.update(b"\0")
    paths = [PLUGIN_ROOT / "pyproject.toml", PLUGIN_ROOT / "uv.lock"]
    paths.extend(sorted(Path(__file__).parent.glob("*.py")))
    for path in paths:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def private_runtime_directory(path: Path | None = None) -> Path:
    # macOS Unix sockets have a short path limit; its normal TMPDIR is too long.
    if path is None:
        path = Path("/tmp") / f"endnote-mcp-{os.getuid()}"
    path.mkdir(mode=0o700, parents=False, exist_ok=True)
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise RuntimeError(
            f"Runtime directory must be owned by you with mode 0700: {path}"
        )
    return path.resolve()


def _connect(path: Path) -> socket.socket | None:
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connection.settimeout(0.2)
        connection.connect(str(path))
        connection.settimeout(None)
        return connection
    except OSError as error:
        connection.close()
        if error.errno in (errno.ENOENT, errno.ECONNREFUSED):
            return None
        raise


def _start_server(path: Path, idle_timeout: float) -> subprocess.Popen[bytes] | None:
    lock_path = path.with_suffix(".lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise RuntimeError(f"Invalid server lock: {lock_path}")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return None
        # The inherited lock is held for the entire server lifetime. Closing our
        # copy is safe; explicitly unlocking it would also unlock the child's copy.
        log_fd = os.open(
            path.with_suffix(".log"),
            os.O_CREAT | os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(log_fd, "ab") as log:
            log_info = os.fstat(log.fileno())
            if (
                not stat.S_ISREG(log_info.st_mode)
                or log_info.st_uid != os.getuid()
                or stat.S_IMODE(log_info.st_mode) != 0o600
            ):
                raise RuntimeError(f"Invalid server log: {path.with_suffix('.log')}")
            if log_info.st_size > 1024 * 1024:
                log.truncate(0)
            return subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "endnote_mcp.shared",
                    "--serve",
                    str(path),
                    "--lock-fd",
                    str(fd),
                    "--idle-timeout",
                    str(idle_timeout),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=log,
                cwd=PLUGIN_ROOT,
                start_new_session=True,
                pass_fds=(fd,),
            )
    finally:
        os.close(fd)


def connect_server(
    runtime_dir: Path | None = None,
    *,
    idle_timeout: float = 300.0,
    startup_timeout: float = 30.0,
) -> socket.socket:
    directory = private_runtime_directory(runtime_dir)
    path = directory / f"{server_key()}.sock"
    if len(os.fsencode(path)) > 100:
        raise RuntimeError(
            "The EndNote runtime directory is too long for a Unix socket"
        )
    deadline = time.monotonic() + startup_timeout
    child = None
    while time.monotonic() < deadline:
        connection = _connect(path)
        if connection is not None:
            return connection
        if child is not None and child.poll() is not None:
            raise RuntimeError(
                f"Shared server failed to start; see {path.with_suffix('.log')}"
            )
        if child is None:
            child = _start_server(path, idle_timeout)
        time.sleep(0.05)
    raise TimeoutError(
        f"Shared server did not become ready; see {path.with_suffix('.log')}"
    )


def proxy(connection: socket.socket) -> int:
    """Forward bytes with bounded buffers and independent flow in both directions."""
    stdin = sys.stdin.fileno()
    stdout = sys.stdout.fileno()
    blocking = (os.get_blocking(stdin), os.get_blocking(stdout))
    os.set_blocking(stdin, False)
    os.set_blocking(stdout, False)
    connection.setblocking(False)
    incoming = bytearray()
    outgoing = bytearray()
    stdin_open = True
    socket_open = True
    write_closed = False
    try:
        while socket_open or outgoing:
            if not stdin_open and not incoming and not write_closed and socket_open:
                connection.shutdown(socket.SHUT_WR)
                write_closed = True
            readers = []
            writers = []
            if stdin_open and socket_open and len(incoming) < BUFFER_SIZE:
                readers.append(stdin)
            if socket_open and len(outgoing) < BUFFER_SIZE:
                readers.append(connection)
            if incoming and socket_open:
                writers.append(connection)
            if outgoing:
                writers.append(stdout)
            readable, writable, _ = select.select(readers, writers, [])
            if stdin in readable:
                data = os.read(stdin, BUFFER_SIZE - len(incoming))
                if data:
                    incoming.extend(data)
                else:
                    stdin_open = False
            if connection in readable:
                data = connection.recv(BUFFER_SIZE - len(outgoing))
                if data:
                    outgoing.extend(data)
                else:
                    socket_open = False
                    incoming.clear()
            if connection in writable and socket_open:
                try:
                    del incoming[: connection.send(incoming)]
                except BlockingIOError:
                    pass
            if stdout in writable:
                try:
                    del outgoing[: os.write(stdout, outgoing)]
                except BlockingIOError:
                    pass
        return 0 if not stdin_open else 1
    except (BrokenPipeError, ConnectionResetError):
        return 1 if stdin_open else 0
    finally:
        os.set_blocking(stdin, blocking[0])
        os.set_blocking(stdout, blocking[1])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", type=Path)
    parser.add_argument("--idle-timeout", type=float, default=300.0)
    parser.add_argument("--startup-timeout", type=float, default=30.0)
    parser.add_argument("--serve", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--lock-fd", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.idle_timeout <= 0 or args.startup_timeout <= 0:
        parser.error("Timeouts must be greater than zero")
    try:
        if args.serve is not None:
            if args.lock_fd is None:
                parser.error("The server requires an inherited lifetime lock")
            from .daemon import run

            run(args.serve, args.lock_fd, args.idle_timeout)
            return 0
        with connect_server(
            args.runtime_dir,
            idle_timeout=args.idle_timeout,
            startup_timeout=args.startup_timeout,
        ) as connection:
            return proxy(connection)
    except Exception as error:
        print(f"endnote-mcp: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
