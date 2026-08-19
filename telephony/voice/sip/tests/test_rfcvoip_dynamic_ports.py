from __future__ import annotations

import socket
import threading
import unittest

from rfcvoip.RTP import PayloadType, RTPClient, TransmitType
from rfcvoip.VoIP import PhoneStatus

from telephony.voice.sip.rfcvoip_dynamic_ports import reserved_rtp_socket_count
from telephony.voice.sip.rfcvoip_engine import _RfcVoipPhone


def _header(request: str, name: str) -> str:
    prefix = name.lower() + ":"
    for line in request.split("\r\n"):
        if line.lower().startswith(prefix):
            return line.split(":", 1)[1].strip()
    return ""


class _Registrar:
    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.2)
        self.port = int(self.sock.getsockname()[1])
        self.requests: list[tuple[str, tuple[str, int]]] = []
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.sock.close()
        self.thread.join(1)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                data, addr = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                return
            request = data.decode("utf-8", "replace")
            if not request.startswith("REGISTER "):
                continue
            self.requests.append((request, addr))
            response = self._response(request)
            try:
                self.sock.sendto(response.encode(), addr)
            except OSError:
                return

    @staticmethod
    def _response(request: str) -> str:
        to_value = _header(request, "To")
        if ";tag=" not in to_value:
            to_value += ";tag=dynamic-port-test"
        lines = [
            "SIP/2.0 200 OK",
            f"Via: {_header(request, 'Via')}",
            f"From: {_header(request, 'From')}",
            f"To: {to_value}",
            f"Call-ID: {_header(request, 'Call-ID')}",
            f"CSeq: {_header(request, 'CSeq')}",
        ]
        contact = _header(request, "Contact")
        if contact:
            lines.append(f"Contact: {contact};expires=120")
        lines.extend(["Expires: 120", "Content-Length: 0", "", ""])
        return "\r\n".join(lines)


class _TcpRegistrar:
    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = int(self.sock.getsockname()[1])
        self.request: tuple[str, tuple[str, int]] | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.sock.close()
        self.thread.join(1)

    def _run(self) -> None:
        try:
            conn, addr = self.sock.accept()
        except OSError:
            return
        with conn:
            while True:
                data = b""
                while b"\r\n\r\n" not in data:
                    try:
                        chunk = conn.recv(65535)
                    except OSError:
                        return
                    if not chunk:
                        return
                    data += chunk
                request = data.decode("utf-8", "replace")
                if self.request is None:
                    self.request = (request, addr)
                try:
                    conn.sendall(_Registrar._response(request).encode())
                except OSError:
                    return


class RfcVoipDynamicPortTest(unittest.TestCase):
    def test_sip_registration_uses_actual_os_assigned_source_port(self):
        registrar = _Registrar()
        registrar.start()
        phone = _RfcVoipPhone(
            server="127.0.0.1",
            port=registrar.port,
            username="1001",
            password="",
            myIP="127.0.0.1",
        )
        self.assertEqual(phone.sip.myPort, 0)
        try:
            phone.start()
            self.assertEqual(phone.get_status(), PhoneStatus.REGISTERED)
            self.assertTrue(registrar.requests)
            request, source = registrar.requests[0]
            source_port = int(source[1])
            self.assertGreater(source_port, 0)
            self.assertEqual(phone.sip.myPort, source_port)
            self.assertEqual(phone.sip.connection.local_port, source_port)
            self.assertIn(f":{source_port}", _header(request, "Via"))
            self.assertIn(f":{source_port}", _header(request, "Contact"))
        finally:
            try:
                phone.stop()
            finally:
                registrar.stop()

    def test_tcp_registration_uses_actual_os_assigned_source_port(self):
        registrar = _TcpRegistrar()
        registrar.start()
        phone = _RfcVoipPhone(
            server="127.0.0.1", port=registrar.port, username="1001", password="",
            myIP="127.0.0.1", transport="TCP",
        )
        try:
            phone.start()
            self.assertEqual(phone.get_status(), PhoneStatus.REGISTERED)
            self.assertIsNotNone(registrar.request)
            request, source = registrar.request
            source_port = int(source[1])
            self.assertGreater(source_port, 0)
            self.assertEqual(phone.sip.myPort, source_port)
            self.assertEqual(phone.sip.connection.local_port, source_port)
            self.assertIn(f":{source_port}", _header(request, "Via"))
            self.assertIn(f":{source_port}", _header(request, "Contact"))
        finally:
            try:
                phone.stop()
            finally:
                registrar.stop()

    def test_reserved_rtp_socket_is_reused_by_rtp_client(self):
        phone = _RfcVoipPhone(
            server="127.0.0.1", port=5060, username="1001", password="", myIP="127.0.0.1"
        )
        port = phone.request_port(blocking=False)
        self.assertGreater(port, 0)
        self.assertEqual(reserved_rtp_socket_count(owner=phone._telephony_rtp_owner), 1)

        collision = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            with self.assertRaises(OSError):
                collision.bind(("127.0.0.1", port))
        finally:
            collision.close()

        client = RTPClient(
            {0: PayloadType.PCMU},
            "127.0.0.1",
            port,
            "127.0.0.1",
            9,
            TransmitType.SENDRECV,
            audio_sample_rate=16000,
            audio_bit_depth=16,
            audio_channels=1,
        )
        try:
            client.start()
            self.assertEqual(int(client.sin.getsockname()[1]), port)
            self.assertEqual(reserved_rtp_socket_count(owner=phone._telephony_rtp_owner), 0)
        finally:
            client.stop()
            phone.release_ports()

    def test_unused_rtp_reservation_is_released(self):
        phone = _RfcVoipPhone(
            server="127.0.0.1", port=5060, username="1001", password="", myIP="127.0.0.1"
        )
        port = phone.request_port(blocking=False)
        self.assertEqual(reserved_rtp_socket_count(owner=phone._telephony_rtp_owner), 1)
        phone.release_ports()
        self.assertEqual(reserved_rtp_socket_count(owner=phone._telephony_rtp_owner), 0)

        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.bind(("127.0.0.1", port))
            self.assertEqual(int(probe.getsockname()[1]), port)
        finally:
            probe.close()

    def test_multiple_phones_receive_distinct_dynamic_rtp_ports(self):
        first = _RfcVoipPhone(
            server="127.0.0.1", port=5060, username="1001", password="", myIP="127.0.0.1"
        )
        second = _RfcVoipPhone(
            server="127.0.0.1", port=5060, username="1002", password="", myIP="127.0.0.1"
        )
        try:
            first_port = first.request_port(blocking=False)
            second_port = second.request_port(blocking=False)
            self.assertNotEqual(first_port, second_port)
        finally:
            first.release_ports()
            second.release_ports()


if __name__ == "__main__":
    unittest.main()
