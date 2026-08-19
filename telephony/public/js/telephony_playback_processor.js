class TelephonyPlaybackProcessor extends AudioWorkletProcessor {
	constructor(options) {
		super();
		this.queue = [];
		this.offset = 0;
		this.queuedSamples = 0;
		this.sourceSampleRate = options.processorOptions.sourceSampleRate || 16000;
		this.outputSampleRate = sampleRate;
		this.targetDelayMs = options.processorOptions.targetDelayMs || 60;
		this.maxDelayMs = options.processorOptions.maxDelayMs || 200;
		this.targetSamples = Math.max(1, Math.round(this.outputSampleRate * this.targetDelayMs / 1000));
		this.maxQueuedSamples = Math.max(this.targetSamples, Math.round(this.outputSampleRate * this.maxDelayMs / 1000));
		this.startThreshold = this.targetSamples;
		this.started = false;
		this.underruns = 0;
		this.droppedFrames = 0;
		this.preemptiveExpands = 0;
		this.accelerations = 0;
		this.plcEvents = 0;
		this.plcSamples = 0;
		this.playoutRatio = 1;
		this.correctionMode = "normal";
		this.maxCorrection = 0.02;
		this.processCount = 0;
		this.mediaPort = null;
		this.lastSequence = null;
		this.lastTimestamp = null;
		this.expectedSourceFrameSamples = Math.max(1, Math.round(this.sourceSampleRate * 0.020));
		this.mediaGapEvents = 0;
		this.mediaGapSamples = 0;
		this.gapSilenceSamples = 0;
		this.provisionalOutageSamples = 0;
		this.creditedGapSamples = 0;
		this.inUnderrun = false;
		this.inPlc = false;
		this.plcRunSamples = 0;
		this.plcLimitSamples = Math.max(1, Math.round(this.outputSampleRate * 0.060));
		this.historySize = Math.max(1, Math.round(this.outputSampleRate * 0.010));
		this.history = new Float32Array(this.historySize);
		this.historyWrite = 0;
		this.historyCount = 0;
		this.plcRead = 0;
		this.plcCycleLength = 0;
		this.lastPlcSample = 0;
		this.recoveryFadeSamples = Math.max(1, Math.round(this.outputSampleRate * 0.005));
		this.recoveryFadeRemaining = 0;
		this.port.onmessage = ({ data }) => {
			if (data?.type !== "connect" || !data.port) return;
			this.mediaPort = data.port;
			this.mediaPort.onmessage = ({ data: frame }) => this.handleMediaMessage(frame);
			this.mediaPort.start?.();
			this.port.postMessage({ type: "playback-port-ready" });
		};
	}

	handleMediaMessage(message) {
		if (message?.type === "jitter-target") {
			const nextMs = Math.max(40, Math.min(this.maxDelayMs, Number(message.targetMs) || 60));
			this.targetDelayMs = nextMs;
			this.targetSamples = Math.max(1, Math.round(this.outputSampleRate * nextMs / 1000));			this.maxQueuedSamples = Math.max(this.targetSamples, Math.round(this.outputSampleRate * this.maxDelayMs / 1000));
			if (!this.started) this.startThreshold = this.targetSamples;
			return;
		}
		if (message?.type === "media-frame" && message.pcm instanceof ArrayBuffer) {
			const sequence = Number.isFinite(message.sequence) ? (Number(message.sequence) >>> 0) : null;
			const timestamp = Number.isFinite(message.timestamp) ? (Number(message.timestamp) >>> 0) : null;
			let missingSourceSamples = 0;
			if (timestamp !== null && this.lastTimestamp !== null) {
				const delta = (timestamp - this.lastTimestamp) >>> 0;
				if (delta < this.sourceSampleRate * 2) {
					missingSourceSamples = Math.max(0, delta - this.expectedSourceFrameSamples);
				}
			}
			if (sequence !== null && this.lastSequence !== null) {
				const delta = (sequence - this.lastSequence) >>> 0;
				if (delta > 1 && delta < 0x80000000) {
					missingSourceSamples = Math.max(
						missingSourceSamples,
						(delta - 1) * this.expectedSourceFrameSamples,
					);
				}
			}
			if (missingSourceSamples > 0) {
				const missingOutputSamples = Math.max(
					1,
					Math.round(missingSourceSamples * this.outputSampleRate / this.sourceSampleRate),
				);
				const credited = Math.min(missingOutputSamples, this.provisionalOutageSamples);
				const remainingGapSamples = missingOutputSamples - credited;
				this.provisionalOutageSamples -= credited;
				this.creditedGapSamples += credited;
				if (remainingGapSamples > 0) {
					this.queue.push({ kind: "gap", remaining: remainingGapSamples });
				}
				this.mediaGapSamples += missingOutputSamples;
				this.mediaGapEvents += 1;
			} else {
				// Provisional PLC used while waiting for a merely late packet becomes
				// deliberate playout expansion, not media loss. Do not apply it later.
				this.provisionalOutageSamples = 0;
			}
			this.lastSequence = sequence ?? this.lastSequence;
			this.lastTimestamp = timestamp ?? this.lastTimestamp;
			this.enqueue(message.pcm);
			return;
		}
		if (message instanceof ArrayBuffer) this.enqueue(message);
	}

	enqueue(buffer) {
		if (!(buffer instanceof ArrayBuffer)) return;
		const input = new Int16Array(buffer);
		if (!input.length) return;
		const outputLength = Math.max(1, Math.round(input.length * this.outputSampleRate / this.sourceSampleRate));
		const samples = new Float32Array(outputLength);
		if (outputLength === input.length && this.outputSampleRate === this.sourceSampleRate) {
			for (let index = 0; index < input.length; index += 1) samples[index] = input[index] / 32768;
		} else if (outputLength === 1) {
			samples[0] = input[0] / 32768;
		} else {
			const scale = (input.length - 1) / (outputLength - 1);
			for (let index = 0; index < outputLength; index += 1) {
				const position = index * scale;
				const lower = Math.floor(position);
				const upper = Math.min(input.length - 1, lower + 1);				const fraction = position - lower;
				samples[index] = ((input[lower] * (1 - fraction)) + (input[upper] * fraction)) / 32768;
			}
		}
		this.queue.push({ kind: "audio", samples });
		this.queuedSamples += samples.length;
		while (this.bufferedSamples() > this.maxQueuedSamples && this.queue.length > 1) {
			const startIndex = this.offset > 0 ? 1 : 0;
			const dropIndex = this.queue.findIndex(
				(item, index) => index >= startIndex && item?.kind === "audio",
			);
			if (dropIndex < 0) break;
			const discarded = this.queue.splice(dropIndex, 1)[0];
			this.queuedSamples -= discarded.samples.length;
			if (dropIndex === 0) this.offset = 0;
			this.droppedFrames += 1;
		}
		if (!this.started && this.bufferedSamples() >= this.startThreshold) {
			this.started = true;
			this.startThreshold = this.targetSamples;
			this.inUnderrun = false;
			this.inPlc = false;
			this.plcRunSamples = 0;
		}
	}

	bufferedSamples() {
		return Math.max(0, this.queuedSamples - this.offset);
	}

	bufferedMs() {
		return this.bufferedSamples() * 1000 / this.outputSampleRate;
	}
	updatePlayoutRatio() {
		if (!this.started) {
			this.playoutRatio = 1;
			this.correctionMode = "normal";
			return;
		}
		const errorMs = this.bufferedMs() - this.targetDelayMs;
		let desired = 1;
		let mode = "normal";
		if (errorMs < -10) {
			const severity = Math.min(1, (-errorMs - 10) / Math.max(20, this.targetDelayMs));
			desired = 1 - (0.005 + severity * (this.maxCorrection - 0.005));
			mode = "expand";
		} else if (errorMs > 20) {
			const severity = Math.min(1, (errorMs - 20) / Math.max(20, this.targetDelayMs));
			desired = 1 + (0.005 + severity * (this.maxCorrection - 0.005));
			mode = "accelerate";
		}
		if (mode !== this.correctionMode) {
			if (mode === "expand") this.preemptiveExpands += 1;
			if (mode === "accelerate") this.accelerations += 1;
			this.correctionMode = mode;
		}
		this.playoutRatio += (desired - this.playoutRatio) * 0.08;
		if (Math.abs(this.playoutRatio - 1) < 0.0001 && mode === "normal") this.playoutRatio = 1;
	}

	readTimelineSample() {
		while (this.queue.length) {
			const item = this.queue[0];
			if (item?.kind === "gap") {
				if (item.remaining <= 0) {
					this.queue.shift();
					this.offset = 0;
					continue;
				}
				item.remaining -= 1;
				if (item.remaining <= 0) this.queue.shift();
				return { kind: "gap" };
			}
			if (item?.kind !== "audio" || !item.samples?.length) {
				this.queue.shift();
				this.offset = 0;
				continue;
			}

			const frame = item.samples;
			if (this.offset >= frame.length) {
				this.queue.shift();
				this.offset -= frame.length;
				this.queuedSamples -= frame.length;
				if (this.queue[0]?.kind === "gap") this.offset = 0;
				continue;
			}
			const index = Math.floor(this.offset);
			const fraction = this.offset - index;
			const first = frame[Math.min(index, frame.length - 1)];
			let second = first;
			if (index + 1 < frame.length) second = frame[index + 1];
			else if (this.queue[1]?.kind === "audio") second = this.queue[1].samples[0];
			const sample = first + (second - first) * fraction;
			this.offset += this.playoutRatio;
			return { kind: "audio", sample };
		}
		return { kind: "empty" };
	}

	recordHistory(sample) {
		this.history[this.historyWrite] = sample;
		this.historyWrite = (this.historyWrite + 1) % this.historySize;
		this.historyCount = Math.min(this.historySize, this.historyCount + 1);
	}

	beginPlc() {
		this.inPlc = true;
		this.plcEvents += 1;
		this.plcRunSamples = 0;
		this.plcCycleLength = Math.max(1, this.historyCount);
		this.plcRead = this.historyCount === this.historySize ? this.historyWrite : 0;
	}

	pendingGapSamples() {
		return this.queue.reduce(
			(total, item) => total + (item?.kind === "gap" ? Math.max(0, item.remaining || 0) : 0),
			0,
		);
	}

	nextPlcSample() {
		if (!this.historyCount || this.plcRunSamples >= this.plcLimitSamples) return null;
		if (!this.inPlc) this.beginPlc();
		const limit = this.plcCycleLength || this.historyCount;
		const sample = this.history[this.plcRead] || 0;
		this.plcRead += 1;
		if (this.plcRead >= limit && this.historyCount < this.historySize) this.plcRead = 0;
		else if (this.plcRead >= this.historySize) this.plcRead = 0;
		const progress = this.plcRunSamples / this.plcLimitSamples;
		const attenuated = sample * Math.max(0.35, 1 - progress * 0.65);
		this.plcRunSamples += 1;
		this.plcSamples += 1;
		this.lastPlcSample = attenuated;
		return attenuated;
	}

	mergeAfterPlc(sample) {
		if (!this.recoveryFadeRemaining) return sample;
		const progress = 1 - (this.recoveryFadeRemaining / this.recoveryFadeSamples);
		this.recoveryFadeRemaining -= 1;
		return (this.lastPlcSample * (1 - progress)) + (sample * progress);
	}

	reportStats() {
		if (this.processCount % 64 !== 0) return;
		this.port.postMessage({
			type: "playback-stats",
			queuedSamples: this.bufferedSamples(),
			queuedMs: this.bufferedMs(),
			targetDelayMs: this.targetDelayMs,
			underruns: this.underruns,
			droppedFrames: this.droppedFrames,
			started: this.started,
			outputSampleRate: this.outputSampleRate,
			playoutRatio: this.playoutRatio,
			correctionMode: this.correctionMode,
			preemptiveExpands: this.preemptiveExpands,
			accelerations: this.accelerations,
			plcEvents: this.plcEvents,
			plcMs: this.plcSamples * 1000 / this.outputSampleRate,
			mediaGapEvents: this.mediaGapEvents,
			mediaGapMs: this.mediaGapSamples * 1000 / this.outputSampleRate,
			gapSilenceMs: this.gapSilenceSamples * 1000 / this.outputSampleRate,
			pendingGapMs: this.pendingGapSamples() * 1000 / this.outputSampleRate,
			provisionalOutageMs: this.provisionalOutageSamples * 1000 / this.outputSampleRate,
			creditedGapMs: this.creditedGapSamples * 1000 / this.outputSampleRate,
			lastSequence: this.lastSequence,
			lastTimestamp: this.lastTimestamp,
		});
	}

	process(_inputs, outputs) {
		const output = outputs[0]?.[0];
		if (!output) return true;
		output.fill(0);
		this.processCount += 1;
		if (!this.started) {
			if (this.inUnderrun && this.historyCount > 0) {
				this.provisionalOutageSamples += output.length;
			}
			this.reportStats();
			return true;
		}
		this.updatePlayoutRatio();
		for (let outputOffset = 0; outputOffset < output.length; outputOffset += 1) {
			const next = this.readTimelineSample();
			if (next.kind === "gap") {
				const concealedGap = this.nextPlcSample();
				if (concealedGap !== null) output[outputOffset] = concealedGap;
				else this.gapSilenceSamples += 1;
				continue;
			}

			if (next.kind === "audio") {
				if (this.inPlc) {
					this.inPlc = false;
					this.plcRunSamples = 0;
					this.recoveryFadeRemaining = this.recoveryFadeSamples;
				}
				const sample = this.mergeAfterPlc(next.sample);
				output[outputOffset] = sample;
				this.recordHistory(sample);
				this.inUnderrun = false;
				continue;
			}

			const concealed = this.nextPlcSample();
			if (concealed !== null) {
				output[outputOffset] = concealed;
				this.provisionalOutageSamples += 1;
				continue;
			}

			this.provisionalOutageSamples += output.length - outputOffset;
			this.started = false;
			this.startThreshold = this.targetSamples;
			this.playoutRatio = 1;
			this.correctionMode = "normal";
			if (!this.inUnderrun) {
				this.inUnderrun = true;
				this.underruns += 1;
				this.port.postMessage({ type: "underrun", count: this.underruns });
			}
			break;
		}
		this.reportStats();
		return true;
	}
}

registerProcessor("telephony-playback-processor", TelephonyPlaybackProcessor);
