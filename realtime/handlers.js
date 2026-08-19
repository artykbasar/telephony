const fs = require("node:fs");
const path = require("node:path");
const WebSocket = require("ws");

const EVENT_CONNECT = "telephony_voice_connect";
const EVENT_CONTROL = "telephony_voice_control";
const EVENT_MEDIA = "telephony_voice_media";
const EVENT_CLOSE = "telephony_voice_close";
const EVENT_TRANSPORT_READY = "telephony_voice_transport_ready";
const EVENT_TRANSPORT_CLOSED = "telephony_voice_transport_closed";
const VOICE_PROTOCOL_VERSION = 2;
const MAX_BUFFERED_MEDIA_BYTES = 640 * 10;
const MAX_RUNTIME_MESSAGE_BYTES = 65_500;

function benchPath() {
	return path.resolve(__dirname, "../../..");
}

function runtimeStatus(socket) {
	const statusPath = path.join(
		benchPath(),
		"sites",
		String(socket.site_name || ""),
		"private",
		"telephony",
		"runtime-status.json",
	);
	const status = JSON.parse(fs.readFileSync(statusPath, "utf8"));
	if (!status.voice_running || !status.voice_socket) {
		throw new Error("Telephony native voice runtime is not ready.");
	}
	return status;
}

async function issueTicket(socket) {
	const response = await socket.frappe_request(
		"/api/method/telephony.voice.wss.auth.issue_ticket",
	);
	if (!response.ok) {
		throw new Error(`Telephony voice authentication failed (${response.status}).`);
	}
	const payload = await response.json();
	const ticket = payload?.message?.ticket;
	if (!ticket) throw new Error("Telephony voice authentication returned no ticket.");
	return ticket;
}

function runtimeUrl(socketPath) {
	return `ws+unix://${socketPath}:/`;
}

function closeRuntime(socket, reason = "Telephony Socket.IO bridge closed.") {
	const runtime = socket.telephony_voice_runtime;
	socket.telephony_voice_runtime = null;
	if (!runtime) return;
	try {
		runtime.close(1000, reason);
	} catch (_) {}
}

function emitClosed(socket, reason) {
	socket.emit(EVENT_TRANSPORT_CLOSED, {
		reason: String(reason || "Telephony voice transport closed."),
	});
}

function payloadBytes(value) {
	if (typeof value === "string") return Buffer.byteLength(value);
	if (Buffer.isBuffer(value)) return value.length;
	if (value instanceof ArrayBuffer) return value.byteLength;
	if (ArrayBuffer.isView(value)) return value.byteLength;
	return 0;
}

function socketBufferedBytes(socket) {
	const packets = socket?.conn?.writeBuffer || [];
	return packets.reduce((total, packet) => total + payloadBytes(packet?.data), 0);
}

function sendBrowserMedia(socket, data) {
	// Socket.IO volatile events silently discard a 20 ms audio frame whenever
	// Engine.IO is briefly non-writable. Keep a small, explicit queue instead:
	// regular emit absorbs short scheduling stalls while this bound prevents
	// stale speech from accumulating behind a slow browser.
	if (socketBufferedBytes(socket) > MAX_BUFFERED_MEDIA_BYTES) return false;
	socket.emit(EVENT_MEDIA, data);
	return true;
}

async function openRuntime(socket, request = {}) {
	closeRuntime(socket, "Replacing Telephony voice bridge.");
	if (!socket.user || socket.user === "Guest") {
		throw new Error("A signed-in Frappe user is required for Telephony voice.");
	}
	const status = runtimeStatus(socket);
	const ticket = await issueTicket(socket);
	const runtime = new WebSocket(runtimeUrl(status.voice_socket), {
		perMessageDeflate: false,
		maxPayload: MAX_RUNTIME_MESSAGE_BYTES,
	});
	socket.telephony_voice_runtime = runtime;

	runtime.on("open", () => {
		if (socket.telephony_voice_runtime !== runtime) return;
		runtime.send(JSON.stringify({
			type: "auth",
			ticket,
			tab_id: request.tab_id || null,
			protocol_version: VOICE_PROTOCOL_VERSION,
		}));
		socket.emit(EVENT_TRANSPORT_READY, { protocol_version: VOICE_PROTOCOL_VERSION });
	});

	runtime.on("message", (data, isBinary) => {
		if (socket.telephony_voice_runtime !== runtime) return;
		if (isBinary) sendBrowserMedia(socket, data);
		else socket.emit(EVENT_CONTROL, data.toString());
	});
	runtime.on("close", (_code, reason) => {
		if (socket.telephony_voice_runtime !== runtime) return;
		socket.telephony_voice_runtime = null;
		emitClosed(socket, reason?.toString() || "Telephony runtime closed the voice bridge.");
	});

	runtime.on("error", (error) => {
		if (socket.telephony_voice_runtime !== runtime) return;
		emitClosed(socket, error?.message || "Telephony runtime voice bridge failed.");
	});
}

function sendControl(socket, raw) {
	const runtime = socket.telephony_voice_runtime;
	if (!runtime || runtime.readyState !== WebSocket.OPEN) return;
	const message = typeof raw === "string" ? raw : JSON.stringify(raw || {});
	if (Buffer.byteLength(message) > 16_384) return;
	runtime.send(message);
}

function sendMedia(socket, raw) {
	const runtime = socket.telephony_voice_runtime;
	if (!runtime || runtime.readyState !== WebSocket.OPEN) return;
	if (runtime.bufferedAmount > MAX_BUFFERED_MEDIA_BYTES) return;
	if (raw instanceof ArrayBuffer) runtime.send(Buffer.from(raw));
	else if (ArrayBuffer.isView(raw)) runtime.send(Buffer.from(raw.buffer, raw.byteOffset, raw.byteLength));
	else if (Buffer.isBuffer(raw)) runtime.send(raw);
}

module.exports = function setupTelephonyRealtime(socket) {
	socket.on(EVENT_CONNECT, async (request = {}) => {
		try {
			await openRuntime(socket, request);
		} catch (error) {
			emitClosed(socket, error?.message || "Unable to start Telephony voice bridge.");
		}
	});

	socket.on(EVENT_CONTROL, (message) => sendControl(socket, message));
	socket.on(EVENT_MEDIA, (frame) => sendMedia(socket, frame));
	socket.on(EVENT_CLOSE, () => closeRuntime(socket));
	socket.on("disconnect", () => closeRuntime(socket, "Frappe Socket.IO disconnected."));
};

module.exports._test = {
	benchPath,
	runtimeStatus,
	runtimeUrl,
	payloadBytes,
	socketBufferedBytes,
	sendBrowserMedia,
	sendControl,
	sendMedia,
};
