"""Tiny length-prefixed JSON framing over sockets, plus fd passing (spawner IPC)."""

import json
import socket
import struct
from typing import Any, List, Optional, Tuple

_HEADER = struct.Struct("!I")


def send_msg(sock: socket.socket, obj: Any) -> None:
    """Send one JSON message (4-byte length prefix)."""
    data = json.dumps(obj).encode("utf-8")
    sock.sendall(_HEADER.pack(len(data)) + data)


def _recv_exact(sock: socket.socket, n: int) -> Optional[bytes]:
    chunks = b""
    while len(chunks) < n:
        part = sock.recv(n - len(chunks))
        if not part:
            return None
        chunks += part
    return chunks


def recv_msg(sock: socket.socket) -> Optional[Any]:
    """Receive one JSON message; None on EOF (peer died)."""
    header = _recv_exact(sock, _HEADER.size)
    if header is None:
        return None
    (length,) = _HEADER.unpack(header)
    data = _recv_exact(sock, length)
    if data is None:
        return None
    return json.loads(data.decode("utf-8"))


def send_fds(sock: socket.socket, fds: List[int]) -> None:
    """Pass file descriptors over a unix socket (POSIX only)."""
    socket.send_fds(sock, [b"F"], fds)


def recv_fds(sock: socket.socket, max_fds: int = 1) -> Tuple[bytes, List[int]]:
    """Receive passed file descriptors (POSIX only)."""
    msg, fds, _flags, _addr = socket.recv_fds(sock, 1, max_fds)
    return msg, list(fds)
