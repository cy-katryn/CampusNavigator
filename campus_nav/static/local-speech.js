/* Captures one question for the local transcription endpoint. */
'use strict';

function encodeSpeechWav(chunks, sampleCount, inputRate) {
  const rate = 16000;
  const length = Math.floor(sampleCount * rate / inputRate);
  const bytes = new ArrayBuffer(44 + length * 2);
  const view = new DataView(bytes);
  function writeTag(offset, tag) {
    for (let i = 0; i < tag.length; i++) view.setUint8(offset + i, tag.charCodeAt(i));
  }
  writeTag(0, 'RIFF');
  view.setUint32(4, bytes.byteLength - 8, true);
  writeTag(8, 'WAVE');
  writeTag(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeTag(36, 'data');
  view.setUint32(40, length * 2, true);

  const samples = new Float32Array(sampleCount);
  let offset = 0;
  for (const chunk of chunks) { samples.set(chunk, offset); offset += chunk.length; }
  // Average source samples in each output interval before quantizing to PCM16.
  for (let i = 0; i < length; i++) {
    const start = Math.floor(i * inputRate / rate);
    const end = Math.max(start + 1, Math.floor((i + 1) * inputRate / rate));
    let sum = 0;
    for (let j = start; j < end; j++) sum += samples[j];
    const value = Math.max(-1, Math.min(1, sum / (end - start)));
    view.setInt16(44 + i * 2, Math.round(value * (value < 0 ? 32768 : 32767)), true);
  }
  return bytes;
}

class LocalSpeechInput {
  static supported() {
    return Boolean(window.AudioContext && window.AudioWorkletNode && navigator.mediaDevices?.getUserMedia);
  }

  constructor() {
    this.chunks = [];
    this.sampleCount = 0;
    this.lastSpeech = 0;
    this.hasSignal = false;
    this.phase = 'opening';
    this.request = new AbortController();
  }

  async start() {
    try {
      const context = new AudioContext();
      this.context = context;
      this.rate = context.sampleRate;
      // Resume on the microphone click, while it still counts as a user gesture.
      const ready = Promise.all([context.resume(), context.audioWorklet.addModule('/static/mic-worklet.js')]);
      const permission = navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      }).then((stream) => {
        if (this.phase === 'opening') this.stream = stream;
        else stream.getTracks().forEach((track) => track.stop());
      });
      await Promise.all([permission, ready]);
      if (this.phase !== 'opening') return;
      this.capture = new AudioWorkletNode(context, 'microphone-capture', {
        channelCount: 1, channelCountMode: 'explicit', outputChannelCount: [1],
      });
      const source = context.createMediaStreamSource(this.stream);
      source.connect(this.capture).connect(context.destination);
      this.capture.port.onmessage = (event) => this.collect(event.data);
      this.capture.onprocessorerror = () => this.fail('audio-capture');
      this.stream.getAudioTracks()[0].onended = () => this.fail('audio-capture');
      this.phase = 'recording';
      this.limit = setTimeout(() => this.stop(), 30000);
      this.onstart();
    } catch (err) {
      if (this.phase !== 'opening') return;
      const code = err.name === 'NotAllowedError' ? 'not-allowed' : 'audio-capture';
      this.fail(code);
    }
  }

  collect(samples) {
    if (this.phase !== 'recording') return;
    const chunk = samples.subarray(0, this.rate * 30 - this.sampleCount);
    this.chunks.push(chunk);
    this.sampleCount += chunk.length;
    let power = 0;
    for (const value of chunk) power += value * value;
    if (power > chunk.length * 0.0005 ** 2) this.hasSignal = true;
    if (power > chunk.length * 0.008 ** 2) this.lastSpeech = this.sampleCount;
    const silence = this.sampleCount - this.lastSpeech;
    if (!this.hasSignal && this.sampleCount >= this.rate * 8) {
      this.fail('no-speech');
      return;
    }
    if (this.sampleCount === this.rate * 30 ||
        (this.lastSpeech && silence >= this.rate * 1.5)) this.stop();
  }

  releaseMic() {
    clearTimeout(this.limit);
    if (this.capture) {
      this.capture.port.onmessage = null;
      this.capture.disconnect();
      this.capture = null;
    }
    if (this.stream) {
      this.stream.getTracks().forEach((track) => { track.onended = null; track.stop(); });
      this.stream = null;
    }
    if (this.context) {
      this.context.close().catch(() => {});
      this.context = null;
    }
  }

  abort() {
    this.phase = 'ended';
    this.request.abort();
    this.releaseMic();
    this.chunks.length = 0;
  }

  fail(error, detail) {
    if (this.phase === 'ended') return;
    this.abort();
    this.onerror({ error, detail });
    this.onend();
  }

  async stop() {
    if (this.phase !== 'recording') return;
    this.phase = 'transcribing';
    this.releaseMic();
    if (!this.hasSignal) {
      this.fail('no-speech');
      return;
    }
    this.onprocessing();
    try {
      const body = encodeSpeechWav(this.chunks, this.sampleCount, this.rate);
      this.chunks.length = 0;
      const res = await fetch('/api/transcribe?lang=' + encodeURIComponent(this.lang), {
        method: 'POST', headers: { 'Content-Type': 'audio/wav' }, body, signal: this.request.signal,
      });
      const reply = await res.json();
      if (this.phase !== 'transcribing') return;
      if (!res.ok || reply.error) {
        this.fail('local-transcription', reply.error);
        return;
      }
      if (reply.text?.trim()) this.onresult({ results: [[{ transcript: reply.text }]] });
      this.phase = 'ended';
      this.onend();
    } catch {
      this.fail('local-transcription');
    }
  }
}
