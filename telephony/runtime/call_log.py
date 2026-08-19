from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from pathlib import Path

import frappe

from telephony.push import send_incoming_call_notification
from telephony.runtime.accounts import TelephonyRuntimeAccount
from telephony.voice.sip import SipCallOutcome, SipCallState, SipIncomingCall


_TERMINAL = {SipCallState.ENDED}
_STATUS = {
    SipCallState.DIALING: "Initiated",
    SipCallState.RINGING: "Ringing",
    SipCallState.CONNECTED: "In Progress",
}
_STOP = object()


@dataclass(frozen=True, slots=True)
class _CallLogEvent:
    kind: str
    account: TelephonyRuntimeAccount
    call_id: str
    number: str | None = None
    incoming: SipIncomingCall | None = None
    state: SipCallState | None = None
    outcome: SipCallOutcome | None = None


class TelephonyCallLogWriter:
    """Persist native SIP events without letting Frappe DB context block call control.

    Unit tests and request-bound callers may use the synchronous default mode. The
    managed runtime supplies ``site`` and ``bench_path`` which activates a dedicated
    Frappe worker thread with its own persistent site/database context.
    """

    def __init__(
        self,
        *,
        site: str | None = None,
        bench_path: Path | None = None,
        queue_size: int = 256,
        startup_timeout: float = 5.0,
        shutdown_timeout: float = 5.0,
    ) -> None:
        self.site = site
        self.bench_path = Path(bench_path).resolve() if bench_path is not None else None
        self.queue: queue.Queue[_CallLogEvent | object] = queue.Queue(maxsize=max(8, int(queue_size)))
        self.startup_timeout = max(0.1, float(startup_timeout))
        self.shutdown_timeout = max(0.1, float(shutdown_timeout))
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None
        self._startup_error: BaseException | None = None
        self._runtime_error: BaseException | None = None

    @property
    def managed(self) -> bool:
        return bool(self.site and self.bench_path)

    def start(self) -> None:
        if not self.managed or self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name="Telephony Frappe Call Log Writer",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(self.startup_timeout):
            raise RuntimeError("Timed out starting Telephony call-log writer.")
        if self._startup_error is not None:
            raise RuntimeError(
                f"Failed to start Telephony call-log writer: {type(self._startup_error).__name__}"
            ) from self._startup_error

    def stop(self) -> None:
        thread = self._thread
        if thread is None:
            return
        try:
            self.queue.put_nowait(_STOP)
        except queue.Full:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass
            self.queue.put_nowait(_STOP)
        thread.join(self.shutdown_timeout)
        if thread.is_alive():
            raise RuntimeError("Timed out stopping Telephony call-log writer.")
        self._thread = None

    def outgoing_started(self, account: TelephonyRuntimeAccount, call_id: str, number: str) -> None:
        if self.managed:
            self._publish(_CallLogEvent("outgoing", account, call_id, number=number))
            return
        self._outgoing_started(account, call_id, number)

    def incoming_started(self, account: TelephonyRuntimeAccount, call: SipIncomingCall) -> None:
        if self.managed:
            self._publish(_CallLogEvent("incoming", account, call.call_id, incoming=call))
            return
        self._incoming_started(account, call)

    def state_changed(
        self, account: TelephonyRuntimeAccount, call_id: str, state: SipCallState,
        outcome: SipCallOutcome | None = None,
    ) -> None:
        if self.managed:
            self._publish(_CallLogEvent("state", account, call_id, state=state, outcome=outcome))
            return
        self._state_changed(account, call_id, state, outcome)

    def _publish(self, event: _CallLogEvent) -> None:
        if self._thread is None or not self._ready.is_set() or self._stopped.is_set():
            print(f"TELEPHONY_CALL_LOG_DROP call_id={event.call_id} reason=writer_not_running", flush=True)
            return
        if self._runtime_error is not None:
            print(
                f"TELEPHONY_CALL_LOG_DROP call_id={event.call_id} "
                f"reason={type(self._runtime_error).__name__}",
                flush=True,
            )
            return
        try:
            self.queue.put_nowait(event)
        except queue.Full:
            print(f"TELEPHONY_CALL_LOG_DROP call_id={event.call_id} reason=queue_full", flush=True)

    def _run(self) -> None:
        assert self.site is not None and self.bench_path is not None
        started = False
        try:
            frappe.init(site=self.site, sites_path=str(self.bench_path / "sites"))
            frappe.connect()
            started = True
            self._ready.set()
            while True:
                item = self.queue.get()
                if item is _STOP:
                    break
                assert isinstance(item, _CallLogEvent)
                try:
                    self._ensure_db_connection()
                    if item.kind == "outgoing":
                        self._outgoing_started(item.account, item.call_id, item.number or "")
                    elif item.kind == "incoming" and item.incoming is not None:
                        self._incoming_started(item.account, item.incoming)
                    elif item.kind == "state" and item.state is not None:
                        self._state_changed(item.account, item.call_id, item.state, item.outcome)
                except Exception as exc:
                    try:
                        frappe.db.rollback()
                    except Exception:
                        pass
                    print(
                        f"TELEPHONY_CALL_LOG_ERROR call_id={item.call_id} error={type(exc).__name__}",
                        flush=True,
                    )
        except BaseException as exc:
            if not started:
                self._startup_error = exc
                self._ready.set()
            else:
                self._runtime_error = exc
        finally:
            self._stopped.set()
            try:
                frappe.destroy()
            except Exception:
                pass

    @staticmethod
    def _ensure_db_connection() -> None:
        try:
            frappe.db.sql("select 1")
        except Exception as exc:
            if not (frappe.db.is_interface_error(exc) or isinstance(exc, frappe.db.OperationalError)):
                raise
            frappe.db.connect()

    def _outgoing_started(self, account: TelephonyRuntimeAccount, call_id: str, number: str) -> None:
        self._upsert(
            account=account, call_id=call_id, direction="Outgoing",
            from_number=account.extension or account.config.username, to_number=number,
            state=SipCallState.DIALING,
        )

    def _incoming_started(self, account: TelephonyRuntimeAccount, call: SipIncomingCall) -> None:
        self._upsert(
            account=account, call_id=call.call_id, direction="Incoming",
            from_number=call.caller_id or "Unknown",
            to_number=call.called_number or account.extension or account.config.username,
            state=SipCallState.RINGING,
            caller_name=call.caller_name,
            ivr_route=call.ivr_route,
        )
        send_incoming_call_notification(account.user, call)

    def _state_changed(
        self, account: TelephonyRuntimeAccount, call_id: str, state: SipCallState,
        outcome: SipCallOutcome | None = None,
    ) -> None:
        del account
        name = frappe.db.exists("TP Call Log", {"id": call_id})
        if not name:
            return
        doc = frappe.get_doc("TP Call Log", name)
        now = frappe.utils.now_datetime()
        if state == SipCallState.CONNECTED:
            doc.status = _STATUS[state]
            if not getattr(doc, "connected_at", None):
                doc.connected_at = now
        elif state in _TERMINAL:
            doc.status = self._terminal_status(doc, outcome)
            doc.end_time = now
            connected_at = getattr(doc, "connected_at", None)
            doc.duration = (
                max(0, int((now - connected_at).total_seconds()))
                if connected_at else 0
            )
        else:
            doc.status = _STATUS[state]
        doc.save(ignore_permissions=True)
        frappe.db.commit()  # nosemgrep

    @staticmethod
    def _terminal_status(doc, outcome: SipCallOutcome | None) -> str:
        if outcome is not None:
            return outcome.value
        if getattr(doc, "connected_at", None):
            return SipCallOutcome.COMPLETED.value
        if getattr(doc, "type", None) == "Incoming":
            return SipCallOutcome.NO_ANSWER.value
        if getattr(doc, "status", None) == "Ringing":
            return SipCallOutcome.NO_ANSWER.value
        return SipCallOutcome.FAILED.value

    def _upsert(
        self, *, account: TelephonyRuntimeAccount, call_id: str, direction: str,
        from_number: str, to_number: str, state: SipCallState,
        caller_name: str | None = None, ivr_route: str | None = None,
    ) -> None:
        name = frappe.db.exists("TP Call Log", {"id": call_id})
        if name:
            doc = frappe.get_doc("TP Call Log", name)
        else:
            doc = frappe.get_doc({"doctype": "TP Call Log", "id": call_id})
        now = frappe.utils.now_datetime()
        doc.telephony_medium = "SIP"
        doc.medium = "Native SIP"
        doc.sip_call_id = call_id
        doc.type = direction
        setattr(doc, "from", from_number)
        doc.to = to_number
        doc.status = _STATUS[state]
        if direction == "Incoming":
            doc.caller_name = caller_name
            doc.ivr_route = ivr_route
        if not doc.start_time:
            doc.start_time = now
        if direction == "Incoming":
            doc.receiver = account.user
        else:
            doc.caller = account.user
        doc.save(ignore_permissions=True)
        frappe.db.commit()  # nosemgrep


__all__ = ["TelephonyCallLogWriter"]
