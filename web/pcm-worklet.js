// 麦克风 AudioWorklet：Float32 → Int16 PCM，按块上抛给主线程。
// 采样率由 AudioContext({sampleRate:16000}) 保证为 16k，与 server/config.py 的契约一致。
class PcmProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buf = [];
    this.len = 0;
    this.CHUNK = 1600; // 100ms @ 16kHz，兼顾延迟与消息频率
  }

  process(inputs) {
    const ch = inputs[0]?.[0];
    if (!ch) return true;
    this.buf.push(ch.slice());
    this.len += ch.length;
    if (this.len >= this.CHUNK) {
      const pcm = new Int16Array(this.len);
      let off = 0;
      for (const f32 of this.buf) {
        for (let i = 0; i < f32.length; i++) {
          const s = Math.max(-1, Math.min(1, f32[i]));
          pcm[off++] = s < 0 ? s * 0x8000 : s * 0x7fff;
        }
      }
      this.port.postMessage(pcm.buffer, [pcm.buffer]);
      this.buf = [];
      this.len = 0;
    }
    return true;
  }
}

registerProcessor('pcm-processor', PcmProcessor);
