import { io } from "socket.io-client";

let socket = null;
let bridgeReady = false;
let capturePort = null;
let playbackPort = null;
let mediaCallId = null;
let mediaReady = false;
let mediaPaused = false;
let statsTimer = null;
let pingTimer = null;
let mediaStatusTimer = null;
let pingSequence = 0;
let lastRttMs = null;
const pendingPings = new Map();
let sent = 0;
let received = 0;
let dropped = 0;
let captureTimes = [];
let receiveTimes = [];
let previousReceiveArrivalMs = null;
let previousMediaTimestamp = null;
let previousMediaSequence = null;
let jitterMs = 0;
let jitterTargetMs = 60;
let sequenceGaps = 0;
let protocolErrors = 0;
let mediaClockHz = 16000;
let timestampedDownlink = false;
let lastJitterIncreaseMs = 0;
let lastJitterDecreaseMs = 0;
const VOICE_PROTOCOL_VERSION = 2;
const MEDIA_PTIME_MS = 20;
const JITTER_BASE_MS = 60;
const JITTER_MULTIPLIER = 4;
const JITTER_MAX_MS = 200;
const JITTER_DECREASE_STEP_MS = 20;
const JITTER_DECREASE_INTERVAL_MS = 1000;
const MEDIA_FRAME_HEADER_BYTES = 12;
const MEDIA_FRAME_MAGIC = 0x54504d31;
const MAX_BUFFERED_MEDIA_BYTES = 640 * 10;
const EVENT_CONNECT = "telephony_voice_connect";
const EVENT_CONTROL = "telephony_voice_control";
const EVENT_MEDIA = "telephony_voice_media";
const EVENT_CLOSE = "telephony_voice_close";
const EVENT_TRANSPORT_READY = "telephony_voice_transport_ready";
const EVENT_TRANSPORT_CLOSED = "telephony_voice_transport_closed";

function post(type, data = {}) {
	self.postMessage({ type, ...data });
}

function socketWritable() {
	return Boolean(socket?.connected && bridgeReady && socket.io?.engine?.transport?.writable !== false);
}

function socketBufferedBytes() {
	const packets = socket?.io?.engine?.writeBuffer || [];
	return packets.reduce((total, packet) => {
		const data = packet?.data;
		if (typeof data === "string") return total + data.length;
		if (data instanceof ArrayBuffer) return total + data.byteLength;
		if (ArrayBuffer.isView(data)) return total + data.byteLength;
		return total;
	}, 0);
}

function sendJson(type, values = {}) {
	if (!socket?.connected || !bridgeReady) {
		post("error", { message: "Telephony Frappe Socket.IO transport is not connected." });
		return false;
	}
	socket.emit(EVENT_CONTROL, JSON.stringify({ type, ...values }));
	return true;
}

function percentile(values, fraction) {
	if (!values.length) return 0;
	const sorted = [...values].sort((a, b) => a - b);
	return sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * fraction))];
}

function intervals(values) {
	const result = [];
	for (let index = 1; index < values.length; index += 1) result.push(values[index] - values[index - 1]);
	return result;
}

function resetJitterHistory() {
	previousReceiveArrivalMs = null;
	previousMediaTimestamp = null;
	previousMediaSequence = null;
	jitterMs = 0;
	jitterTargetMs = JITTER_BASE_MS;
	sequenceGaps = 0;
	protocolErrors = 0;
	lastJitterIncreaseMs = performance.now();
	lastJitterDecreaseMs = 0;
}

function updateAdaptiveJitter(arrivalMs, mediaTimestamp) {
	if (previousReceiveArrivalMs === null || previousMediaTimestamp === null || mediaTimestamp === null) {
		previousReceiveArrivalMs = arrivalMs;
		previousMediaTimestamp = mediaTimestamp;
		return;
	}

	const timestampDelta = (mediaTimestamp - previousMediaTimestamp) >>> 0;
	const arrivalDeltaMs = arrivalMs - previousReceiveArrivalMs;
	previousReceiveArrivalMs = arrivalMs;
	previousMediaTimestamp = mediaTimestamp;
	if (!timestampDelta || timestampDelta > mediaClockHz * 2) return;

	const expectedMediaMs = timestampDelta * 1000 / mediaClockHz;
	const transitVariationMs = Math.abs(arrivalDeltaMs - expectedMediaMs);
	// Smooth transit-time variation against
	// the media clock, rather than treating WebSocket delivery time as playout time.
	jitterMs += (transitVariationMs - jitterMs) / 16;

	const desired = Math.min(
		JITTER_MAX_MS,
		Math.max(JITTER_BASE_MS, JITTER_BASE_MS + (jitterMs * JITTER_MULTIPLIER)),
	);
	let roundedTarget = jitterMs < 1
		? JITTER_BASE_MS
		: Math.ceil(desired / MEDIA_PTIME_MS) * MEDIA_PTIME_MS;
	if (roundedTarget > jitterTargetMs) {
		jitterTargetMs = roundedTarget;
		lastJitterIncreaseMs = arrivalMs;
		playbackPort?.postMessage({ type: "jitter-target", targetMs: jitterTargetMs });
		return;
	}
	if (roundedTarget >= jitterTargetMs) return;
	// NetEq-like hysteresis: latency is cheap to add when jitter appears but is
	// removed slowly after a full second of stability, never once per packet.
	if (arrivalMs - lastJitterIncreaseMs < JITTER_DECREASE_INTERVAL_MS) return;
	if (arrivalMs - lastJitterDecreaseMs < JITTER_DECREASE_INTERVAL_MS) return;
	jitterTargetMs = Math.max(roundedTarget, jitterTargetMs - JITTER_DECREASE_STEP_MS);
	lastJitterDecreaseMs = arrivalMs;
	playbackPort?.postMessage({ type: "jitter-target", targetMs: jitterTargetMs });
}

function postStats() {
	const captureIntervals = intervals(captureTimes);
	const receiveIntervals = intervals(receiveTimes);
	post("stats", {
		sent,
		received,
		dropped,
		media_paused: mediaPaused,
		buffered_bytes: socketBufferedBytes(),
		capture_p95_ms: percentile(captureIntervals, 0.95),
		capture_max_ms: captureIntervals.length ? Math.max(...captureIntervals) : 0,
		receive_p95_ms: percentile(receiveIntervals, 0.95),
		receive_max_ms: receiveIntervals.length ? Math.max(...receiveIntervals) : 0,
		jitter_ms: jitterMs,
		jitter_target_ms: jitterTargetMs,
		sequence_gaps: sequenceGaps,
		protocol_errors: protocolErrors,
		media_clock_hz: mediaClockHz,
		rtt_ms: lastRttMs,
	});
}

function resetMediaPorts() {
	capturePort?.close();
	playbackPort?.close();
	capturePort = null;
	playbackPort = null;
	mediaReady = false;
	mediaPaused = false;
	resetJitterHistory();
}

function captureFrame(frame) {
	if (!(frame instanceof ArrayBuffer)) return;
	captureTimes.push(performance.now());
	if (captureTimes.length > 1000) captureTimes.shift();
	if (!socketWritable() || !mediaReady) {
		dropped += 1;
		return;
	}

	// Microphone audio is live media, not bulk media. Use our explicit bounded
	// Engine.IO backlog instead of Socket.IO volatile events: volatile can drop
	// a frame in a race after this writable check and make the loss invisible.
	if (mediaPaused || socketBufferedBytes() > MAX_BUFFERED_MEDIA_BYTES) {
		dropped += 1;
		return;
	}

	sent += 1;
	socket.emit(EVENT_MEDIA, frame);
}

function parseDownlinkMedia(buffer) {
	if (!(buffer instanceof ArrayBuffer)) return null;
	if (!timestampedDownlink) return { pcm: buffer, sequence: null, timestamp: null };
	if (buffer.byteLength <= MEDIA_FRAME_HEADER_BYTES) return null;
	const view = new DataView(buffer);
	if (view.getUint32(0, false) !== MEDIA_FRAME_MAGIC) return null;
	return {
		pcm: buffer.slice(MEDIA_FRAME_HEADER_BYTES),
		sequence: view.getUint32(4, false),
		timestamp: view.getUint32(8, false),
	};
}

async function normalizeBinaryFrame(value) {
	if (value instanceof ArrayBuffer) return value;
	if (ArrayBuffer.isView(value)) {
		return value.buffer.slice(value.byteOffset, value.byteOffset + value.byteLength);
	}
	if (typeof Blob !== "undefined" && value instanceof Blob) return value.arrayBuffer();
	return null;
}

function receiveAudio(buffer) {
	if (!playbackPort || !(buffer instanceof ArrayBuffer)) return;
	const media = parseDownlinkMedia(buffer);
	if (!media || !(media.pcm instanceof ArrayBuffer) || !media.pcm.byteLength) {
		protocolErrors += 1;
		dropped += 1;
		return;
	}
	const arrivalMs = performance.now();
	receiveTimes.push(arrivalMs);
	if (receiveTimes.length > 1000) receiveTimes.shift();
	if (media.sequence !== null) {
		if (previousMediaSequence !== null) {
			const sequenceDelta = (media.sequence - previousMediaSequence) >>> 0;
			if (sequenceDelta > 1 && sequenceDelta < 0x80000000) sequenceGaps += sequenceDelta - 1;
		}
		previousMediaSequence = media.sequence;
	}
	updateAdaptiveJitter(arrivalMs, media.timestamp);
	received += 1;
	playbackPort.postMessage({
		type: "media-frame",
		pcm: media.pcm,
		sequence: media.sequence,
		timestamp: media.timestamp,
	}, [media.pcm]);
}

function sendPing() {
	if (!socket?.connected || !bridgeReady) return;
	const nonce = `p${++pingSequence}`;
	const sentAt = performance.now();
	pendingPings.set(nonce, sentAt);
	if (pendingPings.size > 8) pendingPings.delete(pendingPings.keys().next().value);
	sendJson("ping", { nonce, client_timestamp: sentAt });
}

function requestMediaStatus() {
	if (mediaReady) sendJson("media.status");
}

function handleText(raw) {
	let message;
	try {
		message = JSON.parse(raw);
	} catch (_) {
		post("error", { message: "Telephony voice server sent invalid JSON." });
		return;
	}
	const type = message?.type;
	if (type === "session.ready") {
		const media = message.media || {};
		timestampedDownlink = Boolean(media.timestamped_output && media.output_framing === "TPM1");
		mediaClockHz = Math.max(1, Number(media.media_clock_hz) || 16000);
		post("ready", {
			protocol_version: message.protocol_version,
			session_id: message.session_id,
			registration: message.registration,
			capabilities: message.capabilities || {},
			media,
		});
		sendPing();
		return;
	}
	if (type === "pong") {
		const sentAt = pendingPings.get(message.nonce);
		if (sentAt !== undefined) {
			pendingPings.delete(message.nonce);
			lastRttMs = Math.max(0, performance.now() - sentAt);
			post("latency", { rtt_ms: lastRttMs });
		}
		return;
	}
	if (type === "call.incoming") { post("incoming", { call: message.call || {} }); return; }
	if (type === "call.created") { post("created", { call: message.call || {} }); return; }
	if (type === "call.snapshot") { post("snapshot", { snapshot: message.snapshot || {} }); return; }
	if (type === "call.state") { post("state", { state: message.state || {} }); return; }
	if (type === "call.handset") { post("handset", { callId: message.call_id || "", handset: message.handset || {} }); return; }
	if (type === "call.taken") { post("taken", { call: message.call || {} }); return; }
	if (type === "call.declined") { post("declined", { call: message.call || {} }); return; }
	if (type === "call.dtmf.sent") { post("dtmf_sent", { dtmf: message }); return; }
	if (type === "call.transfer.result") {
		post("transfer_result", {
			callId: message.call_id || "",
			consultCallId: message.consult_call_id || "",
			result: message.result || {},
		});
		return;
	}
	if (type === "media.ready") {
		const readyCallId = message.call_id || null;
		if (!readyCallId || readyCallId !== mediaCallId) {
			if (readyCallId) sendJson("media.stop", { call_id: readyCallId });
			return;
		}
		mediaReady = true;
		mediaPaused = false;
		post("media_ready", { metadata: message });
		requestMediaStatus();
		return;
	}
	if (type === "media.status") {
		post("media_status", { status: message.status || {} });
		return;
	}
	if (type === "media.xoff") {
		mediaPaused = true;
		post("media_xoff", { queueLength: message.queue_length || 0 });
		return;
	}
	if (type === "media.xon") {
		mediaPaused = false;
		post("media_xon", { queueLength: message.queue_length || 0 });
		return;
	}
	if (type === "media.stopped") {
		const stoppedCallId = message.call_id || null;
		if (
			stoppedCallId && mediaCallId
			&& (stoppedCallId !== mediaCallId || capturePort || playbackPort)
		) {
			// stopMedia() closes both ports before sending its stop request. If new
			// ports already exist, this acknowledgement belongs to an older media
			// generation even when Hold/Resume reused the same call ID.
			post("media_stopped", { callId: stoppedCallId, stale: true });
			return;
		}
		mediaReady = false;
		mediaPaused = false;
		if (!stoppedCallId || stoppedCallId === mediaCallId) mediaCallId = null;
		post("media_stopped", { callId: stoppedCallId });
		return;
	}
	if (type === "error") {
		post("error", { message: message.message || "Telephony voice error.", code: message.code || "server" });
	}
}


function start(data) {
	if (socket) return;
	const url = String(data.url || "").trim();
	if (!url) {
		post("error", { message: "Frappe Socket.IO URL is required for Telephony voice." });
		return;
	}
	const tabId = data.tabId || null;
	const current = io(url, {
		transports: ["websocket"],
		upgrade: false,
		withCredentials: true,
		reconnection: true,
		reconnectionAttempts: Infinity,
		reconnectionDelay: 500,
		reconnectionDelayMax: 2000,
		timeout: 5000,
	});
	socket = current;
	current.on("connect", () => {
		if (socket !== current) return;
		bridgeReady = false;
		current.emit(EVENT_CONNECT, { tab_id: tabId, protocol_version: VOICE_PROTOCOL_VERSION });
	});
	current.on(EVENT_TRANSPORT_READY, () => {
		if (socket === current) bridgeReady = true;
	});
	current.on(EVENT_CONTROL, (message) => {
		if (socket !== current) return;
		if (typeof message === "string") handleText(message);
	});
	current.on(EVENT_MEDIA, async (frame) => {
		if (socket !== current) return;
		const buffer = await normalizeBinaryFrame(frame);
		if (socket === current && buffer) receiveAudio(buffer);
	});
	current.on(EVENT_TRANSPORT_CLOSED, (data = {}) => {
		if (socket !== current) return;
		bridgeReady = false;
		mediaCallId = null;
		mediaReady = false;
		mediaPaused = false;
		resetMediaPorts();
		post("disconnect", { reason: data.reason || "Telephony runtime voice bridge closed." });
	});
	current.on("connect_error", (error) => {
		if (socket === current) post("error", { message: error?.message || "Telephony Frappe Socket.IO connection failed." });
	});
	current.on("disconnect", (reason) => {
		if (socket !== current) return;
		bridgeReady = false;
		mediaCallId = null;
		mediaReady = false;
		mediaPaused = false;
		resetMediaPorts();
		post("disconnect", { reason: reason || "Frappe Socket.IO disconnected." });
	});
	clearInterval(statsTimer);
	clearInterval(pingTimer);
	clearInterval(mediaStatusTimer);
	statsTimer = setInterval(postStats, 500);
	pingTimer = setInterval(sendPing, 5000);
	mediaStatusTimer = setInterval(requestMediaStatus, 1000);
}

function startMedia(data) {
	const nextCallId = data.callId || null;
	if (mediaCallId && mediaCallId !== nextCallId && socketWritable()) {
		sendJson("media.stop", { call_id: mediaCallId });
	}
	resetMediaPorts();
	mediaCallId = nextCallId;
	sent = 0;
	received = 0;
	dropped = 0;
	captureTimes = [];
	receiveTimes = [];
	capturePort = data.capturePort;
	playbackPort = data.playbackPort;
	capturePort.onmessage = ({ data: frame }) => captureFrame(frame);
	capturePort.start?.();
	playbackPort.start?.();
	playbackPort.postMessage({ type: "jitter-target", targetMs: jitterTargetMs });
	capturePort.postMessage({ type: "probe" });
	if (!nextCallId || !sendJson("media.start", { call_id: nextCallId })) mediaCallId = null;
}

function stopMedia() {
	const stoppingCallId = mediaCallId;
	if (socketWritable() && stoppingCallId) {
		sendJson("media.stop", { call_id: stoppingCallId });
	}
	mediaCallId = null;
	resetMediaPorts();
}

function stop() {
	clearInterval(statsTimer);
	clearInterval(pingTimer);
	clearInterval(mediaStatusTimer);
	statsTimer = null;
	pingTimer = null;
	mediaStatusTimer = null;
	pendingPings.clear();
	stopMedia();
	if (socket) {
		const current = socket;
		socket = null;
		bridgeReady = false;
		try { if (current.connected) current.emit(EVENT_CLOSE); } catch (_) {}
		try { current.removeAllListeners(); } catch (_) {}
		try { current.disconnect(); } catch (_) {}
	}
}

self.onmessage = ({ data }) => {
	if (!data?.type) return;
	if (data.type === "start") { start(data); return; }
	if (data.type === "dial") { sendJson("call.dial", { number: data.number }); return; }
	if (data.type === "answer") { sendJson("call.answer", { call_id: data.callId }); return; }
	if (data.type === "reject") { sendJson("call.reject", { call_id: data.callId }); return; }
	if (data.type === "handset_claim") { sendJson("call.handset.claim", { call_id: data.callId }); return; }
	if (data.type === "hangup") { sendJson("call.hangup", { call_id: data.callId }); return; }
	if (data.type === "hold") { sendJson("call.hold", { call_id: data.callId }); return; }
	if (data.type === "resume") { sendJson("call.resume", { call_id: data.callId }); return; }
	if (data.type === "transfer") { sendJson("call.transfer", { call_id: data.callId, target: data.target }); return; }
	if (data.type === "attended_transfer") {
		sendJson("call.attended_transfer", { call_id: data.callId, consult_call_id: data.consultCallId });
		return;
	}
	if (data.type === "dtmf") { sendJson("call.dtmf", { call_id: data.callId, digit: data.digit }); return; }
	if (data.type === "media_client_timing") { sendJson("media.client_timing", { call_id: data.callId, timing: data.timing || {} }); return; }
	if (data.type === "media_start") { startMedia(data); return; }
	if (data.type === "media_stop") { stopMedia(); return; }
	if (data.type === "stop") stop();
};
