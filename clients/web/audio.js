// 音频 I/O：麦克风采集（16kHz 上行）与 TTS 播放队列（22.05kHz 下行）。
// 采样率两端契约见 server/config.py。
// 二进制帧格式：前4字节 = 代号（uint32 LE），其余 = 16-bit 小端 PCM。

const MIC_RATE = 16000;
const TTS_RATE = 22050;

/**
 * 启动麦克风采集，返回 {stop()} 句柄供外部停止。
 * stop() 会停止所有 track、断开节点并关闭 AudioContext。
 */
export async function startMic(onPcm) {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      // 回声消除靠浏览器（成本策略 C10：端侧免费能力优先），否则外放会自激打断
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
      channelCount: 1,
    },
  });
  const ctx = new AudioContext({ sampleRate: MIC_RATE });
  await ctx.audioWorklet.addModule('pcm-worklet.js');
  const src = ctx.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(ctx, 'pcm-processor');
  node.port.onmessage = (e) => onPcm(e.data);
  src.connect(node);

  return {
    stop() {
      stream.getTracks().forEach((t) => t.stop());
      src.disconnect();
      node.disconnect();
      ctx.close();
    },
  };
}

export class Speaker {
  constructor() {
    this.ctx = new AudioContext({ sampleRate: TTS_RATE });
    this.nextTime = 0;
    this.playing = new Set(); // 跟踪已排期的 source，barge-in 时统一停掉
    // 最小可接受代号：小于此值的帧为 interrupt/start 之前的过期帧，丢弃
    this._minGen = 0;
  }

  /**
   * 入队一个二进制帧。
   * @param {ArrayBuffer} arrayBuffer 前4字节为代号（uint32 LE），其余为 PCM。
   */
  enqueue(arrayBuffer) {
    const gen = new DataView(arrayBuffer).getUint32(0, /* littleEndian */ true);
    if (gen < this._minGen) return; // 过期帧，丢弃

    const pcm = arrayBuffer.slice(4);
    const i16 = new Int16Array(pcm);
    const f32 = new Float32Array(i16.length);
    for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 0x8000;

    const buf = this.ctx.createBuffer(1, f32.length, TTS_RATE);
    buf.copyToChannel(f32, 0);
    const src = this.ctx.createBufferSource();
    src.buffer = buf;
    src.connect(this.ctx.destination);

    const t = Math.max(this.ctx.currentTime, this.nextTime);
    src.start(t);
    this.nextTime = t + buf.duration;
    this.playing.add(src);
    src.onended = () => this.playing.delete(src);
  }

  /**
   * 冲掉所有已排期音频，并拒绝代号小于 minGen 的后续帧。
   * @param {number} minGen 新的最小可接受代号（来自服务端 interrupt/start 消息）。
   */
  flush(minGen = this._minGen + 1) {
    this._minGen = minGen;
    for (const src of this.playing) {
      try { src.stop(); } catch (_) {}
    }
    this.playing.clear();
    this.nextTime = 0;
  }

  /** 释放 AudioContext；关闭 WebSocket 时调用。 */
  close() {
    this.flush();
    this.ctx.close();
  }
}
