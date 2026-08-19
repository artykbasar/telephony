from __future__ import annotations

import ipaddress
import socket
import threading
from typing import Any

from rfcvoip import RTP
from rfcvoip.SIP import SIPClient


_registry_lock = threading.RLock()
_reserved_rtp_sockets: dict[tuple[int, int], tuple[socket.socket, int]] = {}
_original_rtp_start = RTP.RTPClient.start
_original_sip_register = SIPClient.register
_installed = False


def _family_for_host(host: str) -> int:
    value = str(host or "").strip().strip("[]")
    if value in {"", "0.0.0.0"}:
        return socket.AF_INET
    if value == "::":
        return socket.AF_INET6
    try:
        return socket.AF_INET6 if ipaddress.ip_address(value).version == 6 else socket.AF_INET
    except ValueError:
        return socket.AF_INET6 if ":" in value else socket.AF_INET


def _bind_address(host: str, port: int, family: int):
    value = str(host or "").strip().strip("[]")
    if family == socket.AF_INET6:
        return (value if value and value != "0.0.0.0" else "::", port, 0, 0)
    return (value if value and value != "::" else "0.0.0.0", port)


def _new_bound_udp_socket(host: str, port: int, family: int) -> socket.socket:
    sock = socket.socket(family, socket.SOCK_DGRAM)
    try:
        sock.bind(_bind_address(host, port, family))
        return sock
    except Exception:
        sock.close()
        raise


def reserve_rtp_ports(bind_host: str, count: int, *, owner: int) -> list[int]:
    count = int(count)
    if count <= 0:
        raise ValueError("RTP port count must be positive.")
    family = _family_for_host(bind_host)

    for _attempt in range(64):
        sockets: list[socket.socket] = []
        try:
            first = _new_bound_udp_socket(bind_host, 0, family)
            sockets.append(first)
            base_port = int(first.getsockname()[1])
            if base_port + count - 1 > 65535:
                raise OSError("Ephemeral RTP allocation exceeded the UDP port range.")
            for offset in range(1, count):
                sockets.append(_new_bound_udp_socket(bind_host, base_port + offset, family))

            ports = [int(sock.getsockname()[1]) for sock in sockets]
            with _registry_lock:
                for port, sock in zip(ports, sockets, strict=True):
                    _reserved_rtp_sockets[(family, port)] = (sock, owner)
            return ports
        except OSError:
            for sock in sockets:
                sock.close()
            if count == 1:
                raise
    raise OSError(f"Unable to reserve {count} contiguous RTP sockets.")


def release_reserved_rtp_ports(*, owner: int, ports: set[int] | list[int] | tuple[int, ...]) -> None:
    wanted = {int(port) for port in ports}
    closing: list[socket.socket] = []
    with _registry_lock:
        for key, (sock, reservation_owner) in list(_reserved_rtp_sockets.items()):
            if reservation_owner == owner and key[1] in wanted:
                closing.append(sock)
                _reserved_rtp_sockets.pop(key, None)
    for sock in closing:
        sock.close()


def release_all_reserved_rtp_ports(*, owner: int) -> None:
    closing: list[socket.socket] = []
    with _registry_lock:
        for key, (sock, reservation_owner) in list(_reserved_rtp_sockets.items()):
            if reservation_owner == owner:
                closing.append(sock)
                _reserved_rtp_sockets.pop(key, None)
    for sock in closing:
        sock.close()


def _claim_reserved_rtp_socket(family: int, port: int) -> socket.socket | None:
    with _registry_lock:
        reserved = _reserved_rtp_sockets.pop((family, int(port)), None)
    return reserved[0] if reserved is not None else None


def reserved_rtp_socket_count(*, owner: int | None = None) -> int:
    with _registry_lock:
        if owner is None:
            return len(_reserved_rtp_sockets)
        return sum(1 for _sock, reservation_owner in _reserved_rtp_sockets.values() if reservation_owner == owner)


def synchronize_sip_client_local_port(client: Any) -> int | None:
    connection = getattr(client, "connection", None)
    sock = getattr(connection, "socket", None)
    if sock is None:
        return None
    try:
        actual_port = int(sock.getsockname()[1])
    except (OSError, TypeError, ValueError, IndexError):
        return None
    if actual_port <= 0:
        return None
    client.myPort = actual_port
    if connection is not None:
        connection.local_port = actual_port
    return actual_port


def _dynamic_rtp_start(client) -> None:
    reserved = _claim_reserved_rtp_socket(client._socket_family, client.inPort)
    if reserved is None:
        _original_rtp_start(client)
        return
    if client.NSD:
        reserved.close()
        raise RuntimeError("Attempted to start already started RTPClient")

    try:
        reserved.setblocking(False)
        with client._socket_lock:
            if client.NSD:
                raise RuntimeError("Attempted to start already started RTPClient")
            client.sin = reserved
            client.sout = reserved
        client.NSD = True
        threading.Thread(target=client.recv, name="RTP Receiver", daemon=True).start()
        threading.Thread(target=client.trans, name="RTP Transmitter", daemon=True).start()
    except Exception:
        client.NSD = False
        reserved.close()
        raise


def _dynamic_sip_register(client, *args, **kwargs):
    synchronize_sip_client_local_port(client)
    return _original_sip_register(client, *args, **kwargs)


def install_rfcvoip_dynamic_port_compatibility() -> None:
    global _installed
    if _installed:
        return
    RTP.RTPClient.start = _dynamic_rtp_start
    SIPClient.register = _dynamic_sip_register
    _installed = True


__all__ = [
    "install_rfcvoip_dynamic_port_compatibility",
    "release_all_reserved_rtp_ports",
    "release_reserved_rtp_ports",
    "reserve_rtp_ports",
    "reserved_rtp_socket_count",
    "synchronize_sip_client_local_port",
]
