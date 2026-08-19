# Desk softphone — native SIP runtime

The Desk softphone does not register SIP in the browser. SIP signalling, RTP, codecs, registration, hold, and transfer are owned by the server-side Telephony runtime. The browser carries control and 16 kHz mono PCM audio through Frappe Socket.IO.

```text
Desk browser -> Frappe /socket.io -> Telephony private Unix socket
             -> native SIP runtime -> SIP/RTP -> PBX
```

## PBX prerequisites

- A normal SIP endpoint reachable from the server/container running the Telephony runtime.
- A SIP identity for each Telephony agent.
- RTP routing between the runtime host/container and the PBX.
- A supported audio codec. The runtime currently includes Opus, G.722, PCMU, and PCMA support.
- Correct NAT/public-address configuration when the runtime and PBX are separated by NAT.

The PBX does **not** need a browser WebSocket/WSS SIP endpoint for Telephony. Browser STUN/TURN is not part of the native media path.

## TP SIP Settings

Enable **TP SIP Settings** and configure the native SIP fields:

- **SIP Server** — PBX/registrar hostname or IP.
- **SIP Port** — normally `5060`, or the port used by your PBX.
- **Transport** — UDP, TCP, or TLS.
- **Local Bind Address** — interface/address used by the runtime.
- **Advertised Address** — set when SIP/RTP must advertise a different reachable address.
- **TLS Server Name** — optional SNI/certificate hostname for TLS.
- **Proxy / Proxy Port** — optional outbound SIP proxy.

The older WSS, ICE, STUN, TURN, and browser preflight fields are not used by the native SIP runtime.

## TP Telephony Agent

For each Frappe user who needs the native handset:

1. Open **TP Telephony Agent**.
2. Link the Frappe **User**.
3. Enable **SIP**.
4. Set **SIP Username** and **SIP Password**.
5. Set **Extension** and **Display Name** when required by your PBX/routing.
6. Use **Native SIP Server Override** only when this agent must register to different SIP/network settings from TP SIP Settings.

The SIP password stays on the server. It is not sent to the Desk browser.

## Browser connection

Serve the Frappe site through normal HTTPS with its normal Socket.IO endpoint. No extra `/telephony-voice` reverse-proxy route is required.

The browser asks for microphone permission and exchanges media with Frappe Socket.IO. Background incoming-call notifications use the configured Frappe push relay and service worker.

## Runtime

Production must run one `telephony-runtime-manager` per Bench. See [production deployment](production-deployment.md) for Supervisor/systemd examples, health checks, and the release smoke test.
