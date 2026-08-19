class TelephonyCaptureProcessor extends AudioWorkletProcessor {
	constructor(options) {
		super();
		this.targetSampleRate = options.processorOptions.targetSampleRate || 16000;
		this.frameSamples = options.processorOptions.frameSamples || Math.round(this.targetSampleRate * 0.02);
		this.sourceSampleRate = sampleRate;
		this.sourceSamplesPerOutput = this.sourceSampleRate / this.targetSampleRate;
		this.frame = new Int16Array(this.frameSamples);
		this.offset = 0;
		this.previousSample = null;
		this.sourcePosition = 0;
		this.nextOutputPosition = 0;
		this.mediaPort = null;
		this.port.onmessage = ({ data }) => {
			if (data?.type !== "connect" || !data.port) return;
			this.mediaPort = data.port;
			this.mediaPort.onmessage = ({ data: mediaData }) => {
				if (mediaData?.type === "probe") this.port.postMessage({ type: "capture-port-probe" });
			};
			this.mediaPort.start?.();
			this.port.postMessage({ type: "capture-port-ready" });
		};
	}

	emitSample(value) {
		const sample = Math.max(-1, Math.min(1, value));
		this.frame[this.offset] = sample < 0 ? sample * 32768 : sample * 32767;
		this.offset += 1;
		if (this.offset !== this.frameSamples) return;
		const buffer = this.frame.buffer;
		if (this.mediaPort) this.mediaPort.postMessage(buffer, [buffer]);
		this.frame = new Int16Array(this.frameSamples);
		this.offset = 0;
	}

	process(inputs, outputs) {
		const input = inputs[0]?.[0];
		const output = outputs[0]?.[0];
		if (output) output.fill(0);
		if (!input?.length) return true;

		let index = 0;
		if (this.previousSample === null) {
			this.previousSample = input[0];
			this.emitSample(input[0]);
			this.nextOutputPosition = this.sourceSamplesPerOutput;
			index = 1;
		}
		for (; index < input.length; index += 1) {
			const currentSample = input[index];
			this.sourcePosition += 1;
			while (this.nextOutputPosition <= this.sourcePosition) {
				const fraction = this.nextOutputPosition - (this.sourcePosition - 1);
				this.emitSample(this.previousSample + ((currentSample - this.previousSample) * fraction));
				this.nextOutputPosition += this.sourceSamplesPerOutput;
			}
			this.previousSample = currentSample;
		}
		return true;
	}
}

registerProcessor("telephony-capture-processor", TelephonyCaptureProcessor);
