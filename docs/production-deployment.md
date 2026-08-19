# Native SIP production deployment

Telephony uses the standard Frappe HTTPS and Socket.IO endpoints in the browser. There is no public Telephony WebSocket endpoint to proxy.

```text
Browser -> Frappe /socket.io -> realtime/handlers.js
        -> private Unix WebSocket -> Telephony runtime -> SIP/RTP -> PBX
```

## Requirements

- Telephony is installed on the site.
- **TP SIP Settings** is enabled and contains the native SIP server, port, transport, and network settings.
- At least one **TP Telephony Agent** has SIP enabled and valid SIP credentials.
- The Telephony runtime host/container can reach the PBX SIP endpoint and the required RTP address/port range.
- Frappe's normal `/socket.io` endpoint is available through the site's HTTPS reverse proxy.
- Background browser call notifications additionally require the configured Frappe push relay.

`telephony-local-https` is a development helper only. Do not run or expose it in production.

## Install or upgrade

```bash
cd /path/to/frappe-bench
bench get-app <telephony-repository>
bench --site example.com install-app telephony
bench --site example.com migrate
bench build --app telephony
```

### Upgrade from the legacy browser SIP/WebRTC softphone

Take a site backup before upgrading. On an existing Telephony site, update the app checkout and run `bench --site example.com migrate` followed by `bench build --app telephony`.

The migration preserves the existing SIP-enabled agents and their username, encrypted password, display name, and extension. If native `sip_server` is still blank, it derives the PBX hostname from the legacy `wss_uri`; legacy per-agent WSS overrides are converted to native per-agent server overrides. Existing native settings are never overwritten.

A WSS port is not a native SIP port. When a legacy WSS hostname is migrated, native SIP defaults to port `5060` and transport `UDP` unless native values already exist. Confirm these values against the PBX before cutover. If no legacy PBX hostname can be inferred, the patch disables TP SIP Settings rather than starting a broken runtime; configure the native SIP endpoint and re-enable it.

The upgrade also removes the retired TP SIP Telemetry Event DocType, its database table, API writer, and scheduled cleanup job. Twilio and Exotel configuration is not changed. Legacy WSS/STUN/TURN fields remain hidden only for non-destructive record compatibility and are not used by the native runtime.

After the migration, deploy/restart the runtime manager and verify `bench telephony-runtime-status` before placing calls. A PBX endpoint that previously accepted only SIP-over-WSS must also be configured to accept the selected native SIP transport (UDP, TCP, or TLS).

## Runtime manager

Run exactly one runtime manager per Bench:

```bash
cd /path/to/frappe-bench
bench telephony-runtime-manager
```

The manager discovers Telephony sites every minute and starts one native SIP runtime for each site that has TP SIP Settings enabled and at least one SIP-enabled agent. It restarts failed child runtimes with backoff and stops runtimes for sites that are no longer eligible.

For classic production Bench installations, run this command under the same Supervisor or systemd instance that owns the other Frappe processes. Example templates are included in:

- `deploy/supervisor/telephony-runtime-manager.conf.example`
- `deploy/systemd/telephony-runtime-manager.service.example`

Bench does not currently provide an app hook for arbitrary long-running daemons, so the Telephony manager must be declared as one additional managed program rather than inserted into an RQ worker or Socket.IO handler.

To intentionally suppress the native runtime for a site, set `telephony_runtime_disabled` to `1` in that site's `site_config.json`.

## Health check

```bash
bench telephony-runtime-status
bench telephony-runtime-status --json-output
```
The status command reports only sites that are configured for the native SIP runtime. It returns exit code `1` when any configured site is not ready, which makes it suitable for monitoring and deployment verification.

A ready site requires:

- the runtime status process PID is alive;
- the private voice WebSocket is running;
- every configured SIP identity reports `registered`.

For container deployments, run `bench telephony-runtime-manager` as a long-running service from the same application image as the Frappe backend. Mount the same Bench `sites` data and give the service the SIP/RTP network access required by the PBX. Do not publish the private Unix WebSocket.

## Production smoke test

After every installation or upgrade, verify:

1. `bench telephony-runtime-status` reports `READY`.
2. Place an outgoing call and verify two-way audio.
3. Receive an incoming call with Desk open.
4. Receive an incoming call with the site closed and open it from the browser push notification.
5. Verify mute, hold/resume, blind transfer, and attended transfer.
6. Refresh/reopen Desk during idle state and verify the Socket.IO handset reconnects.
7. Restart the Telephony runtime manager and verify SIP registration and incoming calls recover.

The browser transport uses Frappe Socket.IO only. Production reverse proxies should expose Frappe's normal HTTPS and `/socket.io` endpoints; there is no `/telephony-voice` route.
