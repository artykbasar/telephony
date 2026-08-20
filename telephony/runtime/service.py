from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from collections.abc import Callable

from telephony.runtime.accounts import TelephonyRuntimeAccount
from telephony.runtime.call_log import TelephonyCallLogWriter
from telephony.voice.call_state import CallStateError, TelephonyCallDirection, TelephonyCallState, TelephonyCallStateMachine
from telephony.voice.media.bridge import PcmMediaBridge
from telephony.voice.sip import RfcVoipEngine, SipCallState, SipIncomingCall, SipRegistrationState


class TelephonyRuntimeError(RuntimeError):
    pass


class TelephonyRuntimeAccountNotFound(TelephonyRuntimeError):
    pass


IncomingOwnerCallback = Callable[[TelephonyRuntimeAccount, SipIncomingCall], None]
EngineFactory = Callable[[], object]


class TelephonySipRuntime:
    """Own one server-side SIP engine per enabled Telephony agent."""

    def __init__(
        self,
        accounts: tuple[TelephonyRuntimeAccount, ...],
        *,
        engine_factory: EngineFactory = RfcVoipEngine,
        incoming_callback: IncomingOwnerCallback | None = None,
        call_log_writer=None,
        registration_monitor_interval: float = 1.0,
        registration_recovery_backoff: tuple[float, ...] = (1.0, 2.0, 5.0, 10.0, 30.0),
        browser_disconnect_grace_seconds: float = 10.0,
    ) -> None:
        self.accounts = tuple(accounts)
        if len({account.user for account in self.accounts}) != len(self.accounts):
            raise TelephonyRuntimeError("Each Frappe user may own only one enabled SIP runtime account.")
        if len({account.agent for account in self.accounts}) != len(self.accounts):
            raise TelephonyRuntimeError("Each enabled Telephony agent may own only one SIP runtime account.")
        self._engine_factory = engine_factory
        self._by_user = {account.user: account for account in self.accounts}
        self._by_agent = {account.agent: account for account in self.accounts}
        self.engines = {account.agent: engine_factory() for account in self.accounts}
        self.incoming_callback = incoming_callback
        self._incoming_listeners = []
        self._state_listeners = []
        self.call_log_writer = call_log_writer if call_log_writer is not None else TelephonyCallLogWriter()
        self._call_agents: dict[str, str] = {}
        self.calls = TelephonyCallStateMachine()
        self._call_metadata: dict[str, dict] = {}
        self._connected_at: dict[str, float] = {}
        self._media_bridges: dict[str, set[PcmMediaBridge]] = {}
        self._media_starting: set[str] = set()
        self._voice_user_counts: dict[str, int] = {}
        self._voice_sessions: dict[str, str] = {}
        self._voice_session_tabs: dict[str, str] = {}
        self._handset_leases: dict[str, dict[str, str | float | None]] = {}
        self._handset_disconnect_timers: dict[str, threading.Timer] = {}
        self.browser_disconnect_grace_seconds = max(0.0, float(browser_disconnect_grace_seconds))
        self._started = False
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._registration_monitor_interval = max(0.01, float(registration_monitor_interval))
        self._registration_recovery_backoff = tuple(max(0.01, float(x)) for x in registration_recovery_backoff) or (1.0,)
        self._monitor_thread: threading.Thread | None = None
        self._recovering: set[str] = set()
        self._reloading: set[str] = set()
        self._account_operation_locks: dict[str, threading.Lock] = {
            account.agent: threading.Lock() for account in self.accounts
        }

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._stop_event.clear()
            started: list[str] = []
            writer_started = False
            try:
                if self.call_log_writer is not None and hasattr(self.call_log_writer, "start"):
                    self.call_log_writer.start()
                    writer_started = True
                for account in self.accounts:
                    engine = self.engines[account.agent]
                    engine.on_incoming_call(self._incoming_handler(account))
                    engine.on_call_state(self._state_handler(account))
                    engine.start()
                    started.append(account.agent)
                    state = engine.register_account(account.config)
                    if state != SipRegistrationState.REGISTERED:
                        raise TelephonyRuntimeError(
                            f"SIP registration failed for agent {account.agent}: {state.value}"
                        )
            except BaseException:
                for agent in reversed(started):
                    try:
                        self.engines[agent].stop()
                    except BaseException:
                        pass
                if writer_started and hasattr(self.call_log_writer, "stop"):
                    self.call_log_writer.stop()
                raise
            self._started = True
            self._monitor_thread = threading.Thread(target=self._registration_monitor_loop, name="Telephony SIP Registration Monitor", daemon=True)
            self._monitor_thread.start()

    def stop(self) -> None:
        errors: list[BaseException] = []
        self._stop_event.set()
        monitor = self._monitor_thread
        if monitor is not None and monitor is not threading.current_thread():
            monitor.join(timeout=2.0)
        with self._lock:
            for account in reversed(self.accounts):
                try:
                    self.engines[account.agent].stop()
                except BaseException as exc:
                    errors.append(exc)
            if self.call_log_writer is not None and hasattr(self.call_log_writer, "stop"):
                try:
                    self.call_log_writer.stop()
                except BaseException as exc:
                    errors.append(exc)
            disconnect_timers = list(self._handset_disconnect_timers.values())
            self._handset_disconnect_timers.clear()
            bridges = [bridge for items in self._media_bridges.values() for bridge in items]
            self._media_bridges.clear()
            self._media_starting.clear()
            self._voice_sessions.clear()
            self._voice_session_tabs.clear()
            self._voice_user_counts.clear()
            self._handset_leases.clear()
            self._started = False
            self._monitor_thread = None
            self._call_agents.clear()
            self._call_metadata.clear()
            self._connected_at.clear()
        for timer in disconnect_timers:
            timer.cancel()
        for bridge in bridges:
            try:
                bridge.stop()
            except BaseException as exc:
                errors.append(exc)
        if errors:
            raise TelephonyRuntimeError(f"Failed to stop {len(errors)} SIP engine(s).") from errors[0]

    def add_incoming_listener(self, callback) -> None:
        with self._lock:
            self._incoming_listeners.append(callback)

    def add_state_listener(self, callback) -> None:
        with self._lock:
            self._state_listeners.append(callback)

    def account_for_user(self, user: str) -> TelephonyRuntimeAccount:
        try:
            account = self._by_user[user]
        except KeyError as exc:
            raise TelephonyRuntimeAccountNotFound(f"No enabled SIP agent for user {user}.") from exc
        with self._lock:
            if account.agent in self._reloading:
                raise TelephonyRuntimeError("SIP account configuration is being reloaded.")
        return account

    def dial(self, *, user: str, number: str) -> str:
        account = self.account_for_user(user)
        call_id = self.engines[account.agent].make_call(number)
        with self._lock:
            self._call_agents[call_id] = account.agent
            self._call_metadata[call_id] = {
                "from_number": account.extension or account.config.username,
                "to_number": number,
            }
        self.calls.create(call_id, TelephonyCallDirection.OUTGOING)
        self.calls.transition(call_id, TelephonyCallState.DIALING, ignore_invalid=True)
        if self.call_log_writer is not None:
            self.call_log_writer.outgoing_started(account, call_id, number)
        return call_id

    def account_for_call(self, call_id: str) -> TelephonyRuntimeAccount:
        with self._lock:
            agent = self._call_agents.get(call_id)
        if agent is None:
            raise TelephonyRuntimeAccountNotFound(f"No SIP account owns call {call_id}.")
        return self._by_agent[agent]

    def engine_for_call(self, call_id: str):
        account = self.account_for_call(call_id)
        return self.engines[account.agent]

    def registration_snapshot(self) -> dict[str, str]:
        with self._lock:
            accounts = tuple(self.accounts)
            engines = dict(self.engines)
            reloading = set(self._reloading)
        return {
            account.user: (
                SipRegistrationState.REGISTERING.value
                if account.agent in reloading
                else engines[account.agent].registration_state.value
            )
            for account in accounts
            if account.agent in engines
        }

    def reconcile_accounts(self, accounts: tuple[TelephonyRuntimeAccount, ...]) -> dict[str, tuple[str, ...]]:
        desired = tuple(accounts)
        if len({account.user for account in desired}) != len(desired):
            raise TelephonyRuntimeError("Each Frappe user may own only one enabled SIP runtime account.")
        if len({account.agent for account in desired}) != len(desired):
            raise TelephonyRuntimeError("Each enabled Telephony agent may own only one SIP runtime account.")

        with self._lock:
            current = dict(self._by_agent)
        wanted = {account.agent: account for account in desired}
        removed = tuple(sorted(set(current) - set(wanted)))
        added = tuple(sorted(set(wanted) - set(current)))
        changed = tuple(sorted(
            agent for agent in set(current).intersection(wanted)
            if current[agent] != wanted[agent]
        ))

        for agent in removed:
            self._remove_account(agent)
        for agent in changed:
            self._replace_account(wanted[agent])
        for agent in added:
            self._add_account(wanted[agent])

        with self._lock:
            self.accounts = desired
            self._by_user = {account.user: account for account in desired}
            self._by_agent = {account.agent: account for account in desired}
        return {"added": added, "changed": changed, "removed": removed}

    def _account_operation_lock(self, agent: str) -> threading.Lock:
        with self._lock:
            return self._account_operation_locks.setdefault(agent, threading.Lock())

    def _prepare_engine(self, account: TelephonyRuntimeAccount):
        engine = self._engine_factory()
        engine.on_incoming_call(self._incoming_handler(account))
        engine.on_call_state(self._state_handler(account))
        engine.start()
        return engine

    def _register_reloaded_engine(self, account: TelephonyRuntimeAccount, engine) -> None:
        try:
            state = engine.register_account(account.config)
        except BaseException as exc:
            print(
                f"TELEPHONY_SIP_ACCOUNT_RELOAD_ERROR agent={account.agent} error={type(exc).__name__}",
                flush=True,
            )
            return
        print(
            f"TELEPHONY_SIP_ACCOUNT_RELOAD agent={account.agent} state={state.value}",
            flush=True,
        )

    def _disconnect_agent_calls(self, agent: str, engine) -> None:
        with self._lock:
            call_ids = [call_id for call_id, owner in self._call_agents.items() if owner == agent]
            bridges = [
                bridge for call_id in call_ids for bridge in self._media_bridges.get(call_id, ())
            ]
        for bridge in bridges:
            try:
                self.stop_media(bridge)
            except BaseException:
                pass
        for call_id in call_ids:
            try:
                snapshot = self.calls.get(call_id)
            except CallStateError:
                continue
            if snapshot.state in {TelephonyCallState.ENDED, TelephonyCallState.FAILED}:
                continue
            self.calls.transition(call_id, TelephonyCallState.DISCONNECTING, ignore_invalid=True)
            try:
                engine.hangup_call(call_id)
            except BaseException:
                self.calls.transition(
                    call_id, TelephonyCallState.FAILED,
                    failure_reason="sip_account_reloaded", ignore_invalid=True,
                )

    def _purge_agent_calls(self, agent: str) -> None:
        timers: list[threading.Timer] = []
        with self._lock:
            call_ids = [call_id for call_id, owner in self._call_agents.items() if owner == agent]
            for call_id in call_ids:
                self._call_agents.pop(call_id, None)
                self._call_metadata.pop(call_id, None)
                self._connected_at.pop(call_id, None)
                self._handset_leases.pop(call_id, None)
                self._media_starting.discard(call_id)
                timer = self._handset_disconnect_timers.pop(call_id, None)
                if timer is not None:
                    timers.append(timer)
        for timer in timers:
            timer.cancel()

    def _remove_account(self, agent: str) -> None:
        with self._lock:
            self._reloading.add(agent)
        try:
            with self._account_operation_lock(agent):
                with self._lock:
                    engine = self.engines.get(agent)
                if engine is not None:
                    self._disconnect_agent_calls(agent, engine)
                    try:
                        engine.stop()
                    except BaseException as exc:
                        print(
                            f"TELEPHONY_SIP_ACCOUNT_STOP_ERROR agent={agent} error={type(exc).__name__}",
                            flush=True,
                        )
                self._purge_agent_calls(agent)
                with self._lock:
                    self.engines.pop(agent, None)
                    old = self._by_agent.pop(agent, None)
                    if old is not None:
                        self._by_user.pop(old.user, None)
                    self._recovering.discard(agent)
        finally:
            with self._lock:
                self._reloading.discard(agent)

    def _replace_account(self, account: TelephonyRuntimeAccount) -> None:
        agent = account.agent
        with self._lock:
            self._reloading.add(agent)
        try:
            with self._account_operation_lock(agent):
                with self._lock:
                    old_engine = self.engines.get(agent)
                    old_account = self._by_agent.get(agent)
                if old_engine is not None:
                    self._disconnect_agent_calls(agent, old_engine)
                    try:
                        old_engine.stop()
                    except BaseException as exc:
                        print(
                            f"TELEPHONY_SIP_ACCOUNT_STOP_ERROR agent={agent} error={type(exc).__name__}",
                            flush=True,
                        )
                engine = self._prepare_engine(account)
                with self._lock:
                    self.engines[agent] = engine
                    self._by_agent[agent] = account
                    if old_account is not None and old_account.user != account.user:
                        self._by_user.pop(old_account.user, None)
                    self._by_user[account.user] = account
                    self._recovering.discard(agent)
                self._register_reloaded_engine(account, engine)
        finally:
            with self._lock:
                self._reloading.discard(agent)

    def _add_account(self, account: TelephonyRuntimeAccount) -> None:
        agent = account.agent
        with self._lock:
            self._reloading.add(agent)
        try:
            with self._account_operation_lock(agent):
                engine = self._prepare_engine(account)
                with self._lock:
                    self.engines[agent] = engine
                    self._by_agent[agent] = account
                    self._by_user[account.user] = account
                self._register_reloaded_engine(account, engine)
        finally:
            with self._lock:
                self._reloading.discard(agent)

    def call_media_status(self, call_id: str, bridge: PcmMediaBridge | None = None) -> dict:
        engine = self.engine_for_call(call_id)
        result = {
            "call_id": call_id,
            "engine": type(engine).__name__,
            "sip": dict(engine.call_media_status(call_id)),
        }
        if bridge is not None:
            result["bridge"] = bridge.status()
        return result

    def active_call_snapshots(self, *, user: str) -> list[dict]:
        now = time.time()
        results = []
        with self._lock:
            call_ids = [call_id for call_id, agent in self._call_agents.items() if self._by_agent[agent].user == user]
            metadata = {call_id: dict(self._call_metadata.get(call_id) or {}) for call_id in call_ids}
            connected = dict(self._connected_at)
        for call_id in call_ids:
            try:
                snapshot = self.calls.get(call_id)
            except Exception:
                continue
            if snapshot.state in {TelephonyCallState.ENDED, TelephonyCallState.FAILED}:
                continue
            connected_at = connected.get(call_id)
            results.append({
                "call_id": call_id,
                "direction": snapshot.direction.value,
                "state": snapshot.state.value,
                "sequence": snapshot.sequence,
                "from_number": metadata[call_id].get("from_number"),
                "to_number": metadata[call_id].get("to_number"),
                "caller_name": metadata[call_id].get("caller_name"),
                "ivr_route": metadata[call_id].get("ivr_route"),
                "connected_at": datetime.fromtimestamp(connected_at, tz=timezone.utc).isoformat() if connected_at else None,
                "connected_duration_seconds": max(0.0, now - connected_at) if connected_at else None,
            })
        return results

    def _require_owned_call(self, *, user: str, call_id: str):
        account = self.account_for_call(call_id)
        if account.user != user:
            raise TelephonyRuntimeAccountNotFound(f"Call {call_id} does not belong to user {user}.")
        return account, self.engines[account.agent]

    def answer(self, *, user: str, call_id: str) -> None:
        _, engine = self._require_owned_call(user=user, call_id=call_id)
        snapshot = self.calls.get(call_id)
        if snapshot.state != TelephonyCallState.RINGING:
            raise TelephonyRuntimeError("Answer requires a ringing Telephony call.")
        self.calls.transition(call_id, TelephonyCallState.CONNECTING)
        engine.answer_call(call_id)

    def reject(self, *, user: str, call_id: str) -> None:
        _, engine = self._require_owned_call(user=user, call_id=call_id)
        engine.reject_call(call_id)

    def hangup(self, *, user: str, call_id: str) -> None:
        _, engine = self._require_owned_call(user=user, call_id=call_id)
        engine.hangup_call(call_id)

    def hold(self, *, user: str, call_id: str) -> None:
        _, engine = self._require_owned_call(user=user, call_id=call_id)
        engine.hold_call(call_id)
        self._set_call_media_held(call_id, True)
        self.calls.transition(call_id, TelephonyCallState.HELD, ignore_invalid=True)

    def resume(self, *, user: str, call_id: str) -> None:
        _, engine = self._require_owned_call(user=user, call_id=call_id)
        engine.resume_call(call_id)
        self._set_call_media_held(call_id, False)
        self.calls.transition(call_id, TelephonyCallState.CONNECTED, ignore_invalid=True)

    def dtmf(self, *, user: str, call_id: str, digit: str) -> bool:
        _, engine = self._require_owned_call(user=user, call_id=call_id)
        return bool(engine.send_dtmf(call_id, digit))

    def transfer(self, *, user: str, call_id: str, target: str) -> dict:
        _, engine = self._require_owned_call(user=user, call_id=call_id)
        return engine.transfer_call(call_id, target).as_dict()

    def attended_transfer(self, *, user: str, call_id: str, consult_call_id: str) -> dict:
        account, engine = self._require_owned_call(user=user, call_id=call_id)
        consult_account, consult_engine = self._require_owned_call(user=user, call_id=consult_call_id)
        if consult_account.agent != account.agent or consult_engine is not engine:
            raise TelephonyRuntimeError("Attended transfer calls must use the same SIP agent.")
        return engine.attended_transfer(call_id, consult_call_id).as_dict()

    def voice_client_connected(self, user: str, session_id: str, *, tab_id: str | None = None) -> None:
        if not user or not session_id:
            return
        with self._lock:
            previous_user = self._voice_sessions.get(session_id)
            if previous_user == user:
                if tab_id:
                    self._voice_session_tabs[session_id] = tab_id
                return
            if previous_user:
                previous_count = max(0, self._voice_user_counts.get(previous_user, 0) - 1)
                if previous_count:
                    self._voice_user_counts[previous_user] = previous_count
                else:
                    self._voice_user_counts.pop(previous_user, None)
            self._voice_sessions[session_id] = user
            if tab_id:
                self._voice_session_tabs[session_id] = tab_id
            else:
                self._voice_session_tabs.pop(session_id, None)
            self._voice_user_counts[user] = self._voice_user_counts.get(user, 0) + 1

    def voice_client_disconnected(self, user: str, session_id: str) -> list[str]:
        if not user or not session_id:
            return []
        orphaned: list[str] = []
        with self._lock:
            if self._voice_sessions.get(session_id) == user:
                self._voice_sessions.pop(session_id, None)
                self._voice_session_tabs.pop(session_id, None)
                count = max(0, self._voice_user_counts.get(user, 0) - 1)
                if count:
                    self._voice_user_counts[user] = count
                else:
                    self._voice_user_counts.pop(user, None)
            for call_id, lease in self._handset_leases.items():
                if lease.get("user") != user or lease.get("session_id") != session_id:
                    continue
                lease["session_id"] = None
                lease["orphaned_at"] = time.monotonic()
                orphaned.append(call_id)
        for call_id in orphaned:
            self._schedule_handset_disconnect(call_id, user)
        return orphaned

    def voice_tab_restore_pending(self, *, user: str, session_id: str, tab_id: str | None) -> bool:
        if not user or not session_id or not tab_id:
            return False
        with self._lock:
            if self._voice_sessions.get(session_id) != user:
                return False
            if self._voice_session_tabs.get(session_id) != tab_id:
                return False
            for lease in self._handset_leases.values():
                if lease.get("user") != user or str(lease.get("tab_id") or "") != tab_id:
                    continue
                owner_session_id = str(lease.get("session_id") or "")
                if owner_session_id and owner_session_id != session_id:
                    return True
        return False

    def voice_restore_handsets(self, *, user: str, session_id: str, tab_id: str | None) -> list[str]:
        if not user or not session_id or not tab_id:
            return []
        restored: list[str] = []
        timers: list[threading.Timer] = []
        now = time.monotonic()
        with self._lock:
            if self._voice_sessions.get(session_id) != user:
                return []
            if self._voice_session_tabs.get(session_id) != tab_id:
                return []
            for call_id, lease in self._handset_leases.items():
                if lease.get("user") != user or lease.get("session_id"):
                    continue
                if str(lease.get("tab_id") or "") != tab_id:
                    continue
                orphaned_at = lease.get("orphaned_at")
                if orphaned_at is None or now - float(orphaned_at) > self.browser_disconnect_grace_seconds:
                    continue
                lease["session_id"] = session_id
                lease["orphaned_at"] = None
                restored.append(call_id)
                timer = self._handset_disconnect_timers.pop(call_id, None)
                if timer is not None:
                    timers.append(timer)
        for timer in timers:
            timer.cancel()
        return restored

    def voice_handset_state(self, call_id: str, *, user: str, session_id: str) -> dict:
        with self._lock:
            lease = self._handset_leases.get(call_id)
            if lease is None:
                return {
                    "status": "available", "owner": False, "can_claim": True,
                    "grace_seconds": self.browser_disconnect_grace_seconds,
                }
            lease_user = str(lease.get("user") or "")
            owner_session_id = str(lease.get("session_id") or "")
            if lease_user != user:
                return {
                    "status": "unavailable", "owner": False, "can_claim": False,
                    "grace_seconds": self.browser_disconnect_grace_seconds,
                }
            if owner_session_id == session_id:
                return {
                    "status": "owner", "owner": True, "can_claim": False,
                    "grace_seconds": self.browser_disconnect_grace_seconds,
                }
            if not owner_session_id:
                return {
                    "status": "orphaned", "owner": False, "can_claim": True,
                    "grace_seconds": self.browser_disconnect_grace_seconds,
                }
            return {
                "status": "observer", "owner": False, "can_claim": False,
                "grace_seconds": self.browser_disconnect_grace_seconds,
            }

    def voice_claim_handset(self, call_id: str, *, user: str, session_id: str) -> dict:
        if not user or not session_id:
            raise TelephonyRuntimeError("Telephony handset session is missing.")
        snapshot = self.calls.get(call_id)
        if snapshot.state in {TelephonyCallState.ENDED, TelephonyCallState.FAILED, TelephonyCallState.DISCONNECTING}:
            raise TelephonyRuntimeError("Telephony call is no longer active.")
        with self._lock:
            if self._voice_sessions.get(session_id) != user:
                raise TelephonyRuntimeError("Telephony handset session is not connected.")
            lease = self._handset_leases.get(call_id)
            if lease is not None:
                lease_user = str(lease.get("user") or "")
                owner_session_id = str(lease.get("session_id") or "")
                if lease_user != user or (owner_session_id and owner_session_id != session_id):
                    return self.voice_handset_state(call_id, user=user, session_id=session_id)
            self._handset_leases[call_id] = {
                "user": user,
                "session_id": session_id,
                "tab_id": self._voice_session_tabs.get(session_id),
                "orphaned_at": None,
            }
            timer = self._handset_disconnect_timers.pop(call_id, None)
        if timer is not None:
            timer.cancel()
        return self.voice_handset_state(call_id, user=user, session_id=session_id)

    def voice_handset_is_owner(self, call_id: str, *, user: str, session_id: str) -> bool:
        return bool(self.voice_handset_state(call_id, user=user, session_id=session_id).get("owner"))

    def _schedule_handset_disconnect(self, call_id: str, user: str) -> None:
        if self._stop_event.is_set():
            return
        with self._lock:
            lease = self._handset_leases.get(call_id)
            if lease is None or lease.get("user") != user or lease.get("session_id"):
                return
            if call_id in self._handset_disconnect_timers:
                return
            timer = threading.Timer(
                self.browser_disconnect_grace_seconds, self._expire_handset_disconnect, args=(call_id, user)
            )
            timer.daemon = True
            self._handset_disconnect_timers[call_id] = timer
        timer.start()

    def _expire_handset_disconnect(self, call_id: str, user: str) -> None:
        with self._lock:
            lease = self._handset_leases.get(call_id)
            if lease is None or lease.get("user") != user or lease.get("session_id"):
                self._handset_disconnect_timers.pop(call_id, None)
                return
            self._handset_disconnect_timers.pop(call_id, None)
        try:
            snapshot = self.calls.get(call_id)
        except CallStateError:
            return
        if snapshot.state in {TelephonyCallState.ENDED, TelephonyCallState.FAILED, TelephonyCallState.DISCONNECTING}:
            return
        self.calls.transition(call_id, TelephonyCallState.DISCONNECTING, ignore_invalid=True)
        try:
            _, engine = self._require_owned_call(user=user, call_id=call_id)
            engine.hangup_call(call_id)
        except Exception:
            self.calls.transition(
                call_id, TelephonyCallState.FAILED,
                failure_reason="handset_disconnected", ignore_invalid=True,
            )

    def start_media(self, *, user: str, session_id: str, call_id: str, send_to_browser, **kwargs) -> PcmMediaBridge:
        _, engine = self._require_owned_call(user=user, call_id=call_id)
        with self._lock:
            lease = self._handset_leases.get(call_id)
            if lease is None or lease.get("user") != user or lease.get("session_id") != session_id:
                raise TelephonyRuntimeError("This browser session does not own the Telephony call media.")
            if self._media_bridges.get(call_id) or call_id in self._media_starting:
                raise TelephonyRuntimeError("Telephony call media is already attached to another browser session.")
            self._media_starting.add(call_id)
        bridge: PcmMediaBridge | None = None
        try:
            state = self.calls.get(call_id).state
            if state not in {TelephonyCallState.CONNECTED, TelephonyCallState.HELD}:
                raise TelephonyRuntimeError("Telephony call is not connected yet.")
            bridge = PcmMediaBridge(engine, call_id, send_to_browser, **kwargs)
            bridge.start()
            bridge.set_held(state == TelephonyCallState.HELD)
            with self._lock:
                self._media_bridges.setdefault(call_id, set()).add(bridge)
            return bridge
        except Exception:
            if bridge is not None:
                try:
                    bridge.stop()
                except Exception:
                    pass
            raise
        finally:
            with self._lock:
                self._media_starting.discard(call_id)

    def stop_media(self, bridge: PcmMediaBridge | None) -> None:
        if bridge is None:
            return
        try:
            bridge.stop()
        finally:
            status = bridge.status()
            print(
                "TELEPHONY_MEDIA_STATS "
                f"call_id={bridge.call_id} "
                f"browser_frames={status['browser_frames_received']} "
                f"browser_dropped={status['browser_frames_dropped']} "
                f"sip_written={status['sip_frames_written']} "
                f"sip_read={status['sip_frames_read']} "
                f"sip_dropped={status['sip_frames_dropped']} "
                f"sip_null_ticks={status['sip_null_ticks']} "
                f"browser_sent={status['browser_frames_sent']} "
                f"xoff={status['flow_xoff_events']} "
                f"xon={status['flow_xon_events']}",
                flush=True,
            )
            self._unregister_media_bridge(bridge)

    def _unregister_media_bridge(self, bridge: PcmMediaBridge) -> None:
        with self._lock:
            bridges = self._media_bridges.get(bridge.call_id)
            if bridges is None:
                return
            bridges.discard(bridge)
            if not bridges:
                self._media_bridges.pop(bridge.call_id, None)

    def _set_call_media_held(self, call_id: str, held: bool) -> None:
        with self._lock:
            bridges = list(self._media_bridges.get(call_id, ()))
        for bridge in bridges:
            bridge.set_held(held)


    def _registration_monitor_loop(self) -> None:
        while not self._stop_event.wait(self._registration_monitor_interval):
            with self._lock:
                accounts = tuple(self.accounts)
            for account in accounts:
                if self._stop_event.is_set():
                    return
                with self._lock:
                    if account.agent in self._reloading:
                        continue
                    engine = self.engines.get(account.agent)
                if engine is None:
                    continue
                try:
                    state = engine.registration_state
                except BaseException:
                    state = SipRegistrationState.FAILED
                if state in {SipRegistrationState.REGISTERED, SipRegistrationState.REGISTERING, SipRegistrationState.DEREGISTERING}:
                    continue
                with self._lock:
                    if account.agent in self._recovering:
                        continue
                    self._recovering.add(account.agent)
                threading.Thread(
                    target=self._recover_account, args=(account,),
                    name=f"Telephony SIP Recovery {account.agent}", daemon=True,
                ).start()

    def _recover_account(self, account: TelephonyRuntimeAccount) -> None:
        try:
            attempt = 0
            while not self._stop_event.is_set():
                with self._account_operation_lock(account.agent):
                    with self._lock:
                        if account.agent in self._reloading:
                            return
                        engine = self.engines.get(account.agent)
                        current = self._by_agent.get(account.agent)
                    if engine is None or current != account:
                        return
                    try:
                        engine.stop()
                    except BaseException:
                        pass
                    if self._stop_event.is_set():
                        return
                    try:
                        engine.on_incoming_call(self._incoming_handler(account))
                        engine.on_call_state(self._state_handler(account))
                        engine.start()
                        state = engine.register_account(account.config)
                    except BaseException:
                        state = SipRegistrationState.FAILED
                if state == SipRegistrationState.REGISTERED:
                    return
                delay = self._registration_recovery_backoff[min(attempt, len(self._registration_recovery_backoff) - 1)]
                attempt += 1
                if self._stop_event.wait(delay):
                    return
        finally:
            with self._lock:
                self._recovering.discard(account.agent)

    def _incoming_handler(self, account: TelephonyRuntimeAccount):
        def handle(call: SipIncomingCall) -> None:
            with self._lock:
                self._call_agents[call.call_id] = account.agent
                self._call_metadata[call.call_id] = {
                    "from_number": call.caller_id or "Unknown",
                    "to_number": call.called_number or account.extension or account.config.username,
                    "caller_name": call.caller_name,
                    "ivr_route": call.ivr_route,
                }
            self.calls.create(call.call_id, TelephonyCallDirection.INCOMING)
            self.calls.transition(call.call_id, TelephonyCallState.RINGING, ignore_invalid=True)
            if self.call_log_writer is not None:
                self.call_log_writer.incoming_started(account, call)
            if self.incoming_callback is not None:
                self.incoming_callback(account, call)
            with self._lock:
                listeners = list(self._incoming_listeners)
            for callback in listeners:
                callback(account, call)

        return handle

    def _state_handler(self, account: TelephonyRuntimeAccount):
        def handle(call_id, state) -> None:
            with self._lock:
                self._call_agents.setdefault(call_id, account.agent)
            direction = TelephonyCallDirection.OUTGOING if self._call_metadata.get(call_id, {}).get("from_number") in {account.extension, account.config.username} else TelephonyCallDirection.INCOMING
            self.calls.observe_sip_state(call_id, state, direction=direction)
            if state.value == "ended":
                with self._lock:
                    self._handset_leases.pop(call_id, None)
                    timer = self._handset_disconnect_timers.pop(call_id, None)
                if timer is not None:
                    timer.cancel()
            if state.value == "connected":
                with self._lock:
                    self._connected_at.setdefault(call_id, time.time())
            if self.call_log_writer is not None:
                outcome = None
                if state == SipCallState.ENDED:
                    try:
                        outcome = self.engines[account.agent].terminal_outcome(call_id)
                    except Exception:
                        outcome = None
                self.call_log_writer.state_changed(account, call_id, state, outcome=outcome)
            with self._lock:
                listeners = list(self._state_listeners)
            for callback in listeners:
                callback(account, call_id, state)

        return handle


__all__ = [
    "TelephonySipRuntime",
    "TelephonyRuntimeAccountNotFound",
    "TelephonyRuntimeError",
]
