from __future__ import annotations

import asyncio
import json
import os
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import websockets
from websockets.asyncio.server import ServerConnection
from websockets.exceptions import ConnectionClosed

from telephony.runtime.service import TelephonyRuntimeAccountNotFound, TelephonySipRuntime
from telephony.voice.media.bridge import MediaBridgeFrameError, MediaBridgeStateError, PcmDownlinkFrame, PcmMediaBridge
from telephony.voice.media.transport import MediaTransportCapabilities, pack_downlink_media_frame
from telephony.voice.sip import SipCallState, SipIncomingCall
from telephony.voice.wss.auth import VOICE_PROTOCOL_VERSION, VoiceTicketError, verify_ticket

MAX_CONTROL_MESSAGE_BYTES = 16_384


def _json_message(message_type: str, **values: Any) -> str:
    return json.dumps({"type": message_type, **values}, separators=(",", ":"))


@dataclass(eq=False)
class _VoiceClient:
    websocket: ServerConnection
    user: str
    session_id: str
    tab_id: str | None = None
    outbound: asyncio.Queue[str | bytes] = field(default_factory=lambda: asyncio.Queue(maxsize=24))
    outbound_media_dropped: int = 0
    inbound_media_dropped: int = 0
    media_bridge: PcmMediaBridge | None = None
    media_started_at: float | None = None
    first_uplink_logged: bool = False
    sender_task: asyncio.Task | None = None


class TelephonyVoiceWebSocketServer:
    """Authenticated same-origin voice control and PCM endpoint for Telephony."""

    def __init__(
        self,
        runtime: TelephonySipRuntime,
        *,
        site: str,
        bench_path: Path,
        socket_path: Path,
    ) -> None:
        self.runtime = runtime
        self.site = site
        self.bench_path = Path(bench_path)
        self.socket_path = Path(socket_path)
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._startup_error: BaseException | None = None
        self._socket_inode: int | None = None
        self._clients_lock = threading.RLock()
        self._clients: dict[str, set[_VoiceClient]] = {}
        self._ticket_lock = threading.Lock()
        self._used_tickets: dict[str, int] = {}
        runtime.add_incoming_listener(self._on_incoming_call)
        runtime.add_state_listener(self._on_call_state)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and self._ready.is_set())

    @property
    def connection_count(self) -> int:
        with self._clients_lock:
            return sum(len(clients) for clients in self._clients.values())

    def start(self, timeout: float = 5.0) -> None:
        if self.running:
            return
        self.socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.socket_path.parent, 0o700)
        self._prepare_socket_path()
        self._ready.clear()
        self._stop.clear()
        self._startup_error = None
        self._thread = threading.Thread(target=self._thread_main, name=f"Telephony Voice WSS {self.site}", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise RuntimeError("Timed out starting Telephony voice WebSocket server.")
        if self._startup_error is not None:
            raise RuntimeError("Telephony voice WebSocket server failed to start.") from self._startup_error

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._loop is not None:
            self._loop.call_soon_threadsafe(lambda: None)
        if self._thread is not None:
            self._thread.join(timeout)
        self._thread = None
        self._loop = None
        self._unlink_owned_socket()
        self._socket_inode = None

    def _prepare_socket_path(self) -> None:
        if not self.socket_path.exists():
            return
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.settimeout(0.25)
            probe.connect(str(self.socket_path))
        except OSError:
            self.socket_path.unlink(missing_ok=True)
        else:
            raise RuntimeError(f"Telephony voice WebSocket server is already active for site {self.site}.")
        finally:
            probe.close()

    def _unlink_owned_socket(self) -> None:
        try:
            if (
                self._socket_inode is not None
                and self.socket_path.exists()
                and self.socket_path.stat().st_ino == self._socket_inode
            ):
                self.socket_path.unlink()
        except FileNotFoundError:
            pass

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run())
        except BaseException as exc:
            self._startup_error = exc
            self._ready.set()

    async def _run(self) -> None:
        self._loop = asyncio.get_running_loop()
        options = {
            "compression": None,
            "max_size": 65_500,
            "max_queue": 32,
            "ping_interval": 20,
            "ping_timeout": 20,
            "close_timeout": 2,
        }
        async with websockets.unix_serve(
            self._handle_connection,
            path=str(self.socket_path),
            **options,
        ):
            os.chmod(self.socket_path, 0o600)
            self._socket_inode = self.socket_path.stat().st_ino
            await self._serve_until_stopped()

    async def _serve_until_stopped(self) -> None:
        self._ready.set()
        while not self._stop.is_set():
            await asyncio.sleep(0.1)

    def _consume_ticket(self, payload: dict[str, Any]) -> None:
        nonce = str(payload.get("nonce") or "")
        expires = int(payload.get("exp") or 0)
        now = int(time.time())
        with self._ticket_lock:
            self._used_tickets = {key: expiry for key, expiry in self._used_tickets.items() if expiry >= now}
            if not nonce or nonce in self._used_tickets:
                raise VoiceTicketError("Telephony voice ticket has already been used.")
            self._used_tickets[nonce] = expires

    async def _authenticate(self, websocket: ServerConnection) -> tuple[str, str | None]:
        try:
            raw = await asyncio.wait_for(websocket.recv(), timeout=5.0)
        except TimeoutError as exc:
            raise VoiceTicketError("Telephony voice authentication timed out.") from exc
        if not isinstance(raw, str) or len(raw.encode()) > MAX_CONTROL_MESSAGE_BYTES:
            raise VoiceTicketError("Telephony voice authentication requires a JSON text frame.")
        try:
            request = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise VoiceTicketError("Invalid Telephony voice authentication JSON.") from exc
        if not isinstance(request, dict) or request.get("type") != "auth":
            raise VoiceTicketError("First Telephony voice message must be auth.")
        if int(request.get("protocol_version") or 0) != VOICE_PROTOCOL_VERSION:
            raise VoiceTicketError(f"Telephony voice protocol {VOICE_PROTOCOL_VERSION} is required.")
        tab_id = str(request.get("tab_id") or "").strip() or None
        if tab_id is not None and (len(tab_id) > 128 or any(not (ch.isalnum() or ch in "-_.:") for ch in tab_id)):
            raise VoiceTicketError("Invalid Telephony browser tab identifier.")
        payload = verify_ticket(str(request.get("ticket") or ""), bench_path=self.bench_path, expected_site=self.site)
        self._consume_ticket(payload)
        return str(payload["user"]), tab_id

    async def _sender(self, client: _VoiceClient) -> None:
        try:
            while True:
                item = await client.outbound.get()
                await client.websocket.send(item)
        except (asyncio.CancelledError, ConnectionClosed):
            return

    def _enqueue(self, client: _VoiceClient, item: str | bytes) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        def put() -> None:
            if client.outbound.full():
                retained_control: list[str] = []
                while True:
                    try:
                        queued = client.outbound.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if isinstance(queued, str):
                        retained_control.append(queued)
                    else:
                        client.outbound_media_dropped += 1
                for control in retained_control[-(client.outbound.maxsize - 1):]:
                    try:
                        client.outbound.put_nowait(control)
                    except asyncio.QueueFull:
                        break
            try:
                client.outbound.put_nowait(item)
            except asyncio.QueueFull:
                if isinstance(item, bytes):
                    client.outbound_media_dropped += 1
        loop.call_soon_threadsafe(put)

    def _register_client(self, client: _VoiceClient) -> None:
        with self._clients_lock:
            self._clients.setdefault(client.user, set()).add(client)

    def _unregister_client(self, client: _VoiceClient) -> None:
        with self._clients_lock:
            clients = self._clients.get(client.user)
            if clients is not None:
                clients.discard(client)
                if not clients:
                    self._clients.pop(client.user, None)

    def _handset_payload(self, client: _VoiceClient, call_id: str) -> dict[str, Any]:
        return dict(
            self.runtime.voice_handset_state(
                call_id, user=client.user, session_id=client.session_id
            )
        )

    def _send_active_snapshots(self, client: _VoiceClient, restored: set[str]) -> None:
        for snapshot in self.runtime.active_call_snapshots(user=client.user):
            call_id = str(snapshot.get("call_id") or "")
            values = dict(snapshot)
            values["handset"] = self._handset_payload(client, call_id)
            if call_id in restored:
                values["auto_restored"] = True
            self._enqueue(client, _json_message("call.snapshot", snapshot=values))

    def _claim_handset(self, client: _VoiceClient, call_id: str) -> bool:
        handset = self.runtime.voice_claim_handset(
            call_id, user=client.user, session_id=client.session_id
        )
        return bool(handset.get("owner"))

    def _is_owner(self, client: _VoiceClient, call_id: str) -> bool:
        return self.runtime.voice_handset_is_owner(
            call_id, user=client.user, session_id=client.session_id
        )

    def _broadcast_handset(self, user: str, call_id: str) -> None:
        with self._clients_lock:
            targets = list(self._clients.get(user, ()))
        for target in targets:
            self._enqueue(target, _json_message("call.handset", call_id=call_id, handset=self._handset_payload(target, call_id)))

    def _broadcast_call_state(self, user: str, call_id: str, state: str) -> None:
        with self._clients_lock:
            targets = list(self._clients.get(user, ()))
        for target in targets:
            self._enqueue(target, _json_message("call.state", state={
                "call_id": call_id,
                "state": state,
                "handset": self._handset_payload(target, call_id),
            }))

    def _require_call_access(self, client: _VoiceClient, call_id: str) -> None:
        account = self.runtime.account_for_call(call_id)
        if account.user != client.user:
            raise PermissionError("You do not have permission to control this Telephony call.")

    async def _handle_connection(self, websocket: ServerConnection) -> None:
        client = None
        try:
            user, tab_id = await self._authenticate(websocket)
            client = _VoiceClient(websocket=websocket, user=user, session_id=uuid.uuid4().hex, tab_id=tab_id)
            self._register_client(client)
            self.runtime.voice_client_connected(user, client.session_id, tab_id=tab_id)
            restored = set(self.runtime.voice_restore_handsets(
                user=user, session_id=client.session_id, tab_id=tab_id
            ))
            restore_deadline = asyncio.get_running_loop().time() + 0.5
            while (
                tab_id
                and self.runtime.voice_tab_restore_pending(
                    user=user, session_id=client.session_id, tab_id=tab_id
                )
                and asyncio.get_running_loop().time() < restore_deadline
            ):
                await asyncio.sleep(0.01)
                restored.update(self.runtime.voice_restore_handsets(
                    user=user, session_id=client.session_id, tab_id=tab_id
                ))
            client.sender_task = asyncio.create_task(self._sender(client))
            self._enqueue(client, _json_message(
                "session.ready",
                session_id=client.session_id,
                protocol_version=VOICE_PROTOCOL_VERSION,
                registration=self.runtime.registration_snapshot(),
                capabilities={"dtmf": True, "hold": True, "transfer": True, "attended_transfer": True},
                media=MediaTransportCapabilities().as_dict(),
                restored_calls=list(restored),
            ))
            self._send_active_snapshots(client, restored)
            for call_id in restored:
                self._broadcast_handset(client.user, call_id)
            async for raw in websocket:
                if isinstance(raw, bytes):
                    await self._handle_media(client, raw)
                else:
                    await self._handle_control(client, raw)
        except (VoiceTicketError, PermissionError) as exc:
            try:
                await websocket.send(_json_message("error", code="authentication", message=str(exc)))
            except ConnectionClosed:
                pass
        finally:
            if client is not None:
                if client.media_bridge is not None:
                    try:
                        self.runtime.stop_media(client.media_bridge)
                    except RuntimeError:
                        pass
                    client.media_bridge = None
                self._unregister_client(client)
                orphaned_calls = self.runtime.voice_client_disconnected(client.user, client.session_id)
                for call_id in orphaned_calls:
                    self._broadcast_handset(client.user, call_id)
                if client.sender_task is not None:
                    client.sender_task.cancel()

    async def _handle_media(self, client: _VoiceClient, chunk: bytes) -> None:
        if client.media_bridge is None or not client.media_bridge.running:
            # Live PCM can still be in flight while media.start/media.stop crosses the
            # browser -> Socket.IO -> private WSS boundary. Treat those boundary frames
            # like normal realtime loss instead of surfacing a false call error in Desk.
            client.inbound_media_dropped += 1
            return
        try:
            client.media_bridge.push_browser_audio(chunk)
            if not client.first_uplink_logged and client.media_started_at is not None:
                client.first_uplink_logged = True
                print(
                    "TELEPHONY_MEDIA_FIRST_UPLINK "
                    f"call_id={client.media_bridge.call_id} "
                    f"after_media_start_ms={(time.perf_counter() - client.media_started_at) * 1000:.1f}",
                    flush=True,
                )
        except MediaBridgeStateError:
            return
        except MediaBridgeFrameError as exc:
            self._enqueue(client, _json_message("error", code="media_frame", message=str(exc)))

    async def _handle_control(self, client: _VoiceClient, raw: str) -> None:
        if len(raw.encode()) > MAX_CONTROL_MESSAGE_BYTES:
            self._enqueue(client, _json_message("error", code="control_too_large", message="Control message too large."))
            return
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            self._enqueue(client, _json_message("error", code="bad_json", message="Invalid JSON control message."))
            return
        try:
            await self._dispatch_control(client, str(message.get("type") or ""), message)
        except (PermissionError, TelephonyRuntimeAccountNotFound) as exc:
            self._enqueue(client, _json_message("error", code="permission", message=str(exc)))
        except Exception as exc:
            self._enqueue(client, _json_message("error", code="control", message=str(exc)))

    async def _dispatch_control(self, client: _VoiceClient, message_type: str, message: dict[str, Any]) -> None:
        if message_type == "ping":
            self._enqueue(client, _json_message("pong", timestamp=time.time(), nonce=message.get("nonce"), client_timestamp=message.get("client_timestamp")))
            return
        if message_type == "call.dial":
            number = str(message.get("number") or "").strip()
            if not number:
                raise RuntimeError("number is required")
            call_id = await asyncio.to_thread(self.runtime.dial, user=client.user, number=number)
            if not self._claim_handset(client, call_id):
                raise PermissionError("This call is active in another browser session.")
            with self._clients_lock:
                targets = list(self._clients.get(client.user, ()))
            for target in targets:
                self._enqueue(target, _json_message("call.created", call={
                    "call_id": call_id, "number": number, "direction": "outgoing",
                    "handset": self._handset_payload(target, call_id),
                }))
            return
        call_id = str(message.get("call_id") or "").strip()
        if message_type == "call.answer":
            self._require_call_access(client, call_id)
            if not self._claim_handset(client, call_id):
                self._enqueue(client, _json_message("call.taken", call={"call_id": call_id}))
                return
            self._broadcast_handset(client.user, call_id)
            await asyncio.to_thread(self.runtime.answer, user=client.user, call_id=call_id)
            return
        if message_type == "call.reject":
            self._require_call_access(client, call_id)
            handset = self._handset_payload(client, call_id)
            if handset.get("status") in {"observer", "unavailable"}:
                self._enqueue(client, _json_message("call.handset", call_id=call_id, handset=handset))
                return
            await asyncio.to_thread(self.runtime.reject, user=client.user, call_id=call_id)
            return
        if message_type == "call.handset.claim":
            self._require_call_access(client, call_id)
            owner = self._claim_handset(client, call_id)
            self._broadcast_handset(client.user, call_id)
            if not owner:
                self._enqueue(client, _json_message(
                    "error", code="handset_busy", message="This call is active in another browser session."
                ))
            return
        if message_type in {"call.hangup", "call.hold", "call.resume", "call.dtmf", "call.transfer", "call.attended_transfer"}:
            self._require_call_access(client, call_id)
            if not self._is_owner(client, call_id):
                raise PermissionError("This call is active in another browser session.")
            if message_type == "call.hangup":
                await asyncio.to_thread(self.runtime.hangup, user=client.user, call_id=call_id)
            elif message_type == "call.hold":
                await asyncio.to_thread(self.runtime.hold, user=client.user, call_id=call_id)
                if client.media_bridge is not None and client.media_bridge.call_id == call_id:
                    client.media_bridge.set_held(True)
                self._broadcast_call_state(client.user, call_id, "held")
            elif message_type == "call.resume":
                await asyncio.to_thread(self.runtime.resume, user=client.user, call_id=call_id)
                if client.media_bridge is not None and client.media_bridge.call_id == call_id:
                    client.media_bridge.set_held(False)
                self._broadcast_call_state(client.user, call_id, "connected")
            elif message_type == "call.dtmf":
                digit = str(message.get("digit") or "")
                await asyncio.to_thread(self.runtime.dtmf, user=client.user, call_id=call_id, digit=digit)
                self._enqueue(client, _json_message("call.dtmf.sent", call_id=call_id, digit=digit))
            elif message_type == "call.transfer":
                result = await asyncio.to_thread(self.runtime.transfer, user=client.user, call_id=call_id, target=str(message.get("target") or "").strip())
                self._enqueue(client, _json_message("call.transfer.result", call_id=call_id, result=result))
            else:
                consult = str(message.get("consult_call_id") or "").strip()
                self._require_call_access(client, consult)
                if not self._is_owner(client, consult):
                    raise PermissionError("The consultation call is active in another browser session.")
                result = await asyncio.to_thread(self.runtime.attended_transfer, user=client.user, call_id=call_id, consult_call_id=consult)
                self._enqueue(client, _json_message("call.transfer.result", call_id=call_id, consult_call_id=consult, result=result))
            return
        if message_type == "media.client_timing":
            self._require_call_access(client, call_id)
            timing = message.get("timing") if isinstance(message.get("timing"), dict) else {}
            allowed = (
                "connected_to_start_ms", "stop_media_ms", "prime_audio_ms",
                "microphone_prepared", "prewarm_get_user_media_ms", "get_user_media_ms",
                "worklets_ms", "graph_ms", "realtime_attach_ms", "microphone_attach_ms", "total_ms",
            )
            values = []
            for key in allowed:
                try:
                    value = float(timing.get(key))
                except (TypeError, ValueError):
                    continue
                if value >= 0 and value < 120_000:
                    values.append(f"{key}={value:.1f}")
            print("TELEPHONY_BROWSER_MEDIA_TIMING " f"call_id={call_id} " + " ".join(values), flush=True)
            return
        if message_type == "media.start":
            self._require_call_access(client, call_id)
            if not self._is_owner(client, call_id):
                raise PermissionError("This call is active in another browser session.")
            if client.media_bridge is not None:
                await asyncio.to_thread(self.runtime.stop_media, client.media_bridge)
                client.media_bridge = None

            def flow(event: str, queue_length: int) -> None:
                self._enqueue(client, _json_message(f"media.{event}", queue_length=queue_length))

            def send_frame(frame: PcmDownlinkFrame) -> None:
                self._enqueue(client, pack_downlink_media_frame(frame.pcm, sequence=frame.sequence, timestamp=frame.timestamp))

            started = time.perf_counter()
            client.media_bridge = await asyncio.to_thread(
                self.runtime.start_media,
                user=client.user,
                session_id=client.session_id,
                call_id=call_id,
                send_to_browser=send_frame,
                on_flow_control=flow,
            )
            client.media_started_at = time.perf_counter()
            client.first_uplink_logged = False
            audio = client.media_bridge.audio_format
            bridge_status = client.media_bridge.status()
            call_media_status = self.runtime.call_media_status(call_id, bridge=client.media_bridge)
            print(
                "TELEPHONY_MEDIA_READY "
                f"call_id={call_id} start_ms={(client.media_started_at - started) * 1000:.1f}",
                flush=True,
            )
            self._enqueue(client, _json_message(
                "media.ready",
                call_id=call_id,
                sample_rate=audio.sample_rate,
                channels=audio.channels,
                bit_depth=audio.bit_depth,
                frame_ms=audio.frame_ms,
                frame_bytes=client.media_bridge.frame_size,
                queue_max_frames=bridge_status["browser_queue_max"],
                queue_xoff_frames=bridge_status["browser_queue_xoff"],
                queue_xon_frames=bridge_status["browser_queue_xon"],
                sip=call_media_status.get("sip") or {},
                transport=MediaTransportCapabilities().as_dict(),
            ))
            return
        if message_type == "media.status":
            status = {"running": False}
            if client.media_bridge is not None:
                try:
                    status = self.runtime.call_media_status(
                        client.media_bridge.call_id, bridge=client.media_bridge
                    )
                    status["running"] = client.media_bridge.running
                except RuntimeError:
                    status = {"running": False}
            status["outbound_media_dropped"] = client.outbound_media_dropped
            status["inbound_media_dropped"] = client.inbound_media_dropped
            self._enqueue(client, _json_message("media.status", status=status))
            return
        if message_type == "media.stop":
            requested_call_id = call_id
            bridge = client.media_bridge
            bridge_call_id = str(bridge.call_id) if bridge is not None else ""
            if bridge is not None and requested_call_id and bridge_call_id != requested_call_id:
                self._enqueue(client, _json_message("media.stopped", call_id=requested_call_id))
                return
            await asyncio.to_thread(self.runtime.stop_media, bridge)
            client.media_bridge = None
            client.media_started_at = None
            client.first_uplink_logged = False
            self._enqueue(client, _json_message("media.stopped", call_id=requested_call_id or bridge_call_id))
            return
        self._enqueue(client, _json_message("error", code="unknown_control", message=f"Unknown control: {message_type}"))

    def _deliver(self, user: str, payload: str) -> None:
        with self._clients_lock:
            targets = list(self._clients.get(user, ()))
        for client in targets:
            self._enqueue(client, payload)

    def _on_incoming_call(self, account, call: SipIncomingCall) -> None:
        self._deliver(account.user, _json_message("call.incoming", call={
            "call_id": call.call_id,
            "from": call.caller_id,
            "to": call.called_number or account.extension,
            "caller_id": call.caller_id,
            "caller_name": call.caller_name,
            "called_number": call.called_number or account.extension,
            "ivr_route": call.ivr_route,
            "direction": "incoming",
        }))

    def _on_call_state(self, account, call_id: str, state: SipCallState) -> None:
        self._broadcast_call_state(account.user, call_id, state.value)


__all__ = ["TelephonyVoiceWebSocketServer", "VOICE_PROTOCOL_VERSION"]
