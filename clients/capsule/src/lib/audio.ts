// 音频 I/O：麦克风采集（16kHz 上行 PCM）与 TTS 播放队列（22.05kHz 下行）。
// 采样率两端契约见 server/config.py。
// 二进制帧格式：前 4 字节 = 代号（uint32 LE），其余 = 16-bit 小端 PCM。
// 移植自 clients/web/audio.js，额外在麦克风链路挂一个 analyser 供 UI 实时音量（零延迟驱动外观）。
// 语音活动判定用 Silero VAD（@ricky0123/vad-web，端侧 ONNX），区分人声与键盘/敲击等瞬态噪声；
// RMS 音量仅用于驱动律动幅度，不再作为「是否在说话」的判据。

import { MicVAD } from '@ricky0123/vad-web';

const MIC_RATE = 16000;
const TTS_RATE = 22050;

export type MicOptions = {
  /** pcm-worklet.js 的可加载 URL（由 Vite ?url 提供）。 */
  workletUrl: string;
  /** Silero 模型 / worklet / onnx wasm 所在目录的绝对 URL（末尾带 /），见 public/vad。 */
  vadAssetBaseUrl: string;
  /** Silero 判定到人声开始（开口即触发，端侧零网络延迟）。 */
  onSpeechStart?: () => void;
  /** Silero 判定到人声结束（含 redemption 宽限）。 */
  onSpeechEnd?: () => void;
};

export type MicHandle = {
  stop: () => void;
  /** 当前麦克风 RMS 音量（0..1），供 UI 律动；不经后端，无延迟。 */
  getLevel: () => number;
  /** Silero VAD 是否成功加载并接管语音判定；为 false 时调用方应回退到 RMS 门限。 */
  vadActive: boolean;
};

/**
 * 启动麦克风采集：每 100ms 回调一块 16k PCM 供上传，同时暴露实时音量，
 * 并在同一条流上跑 Silero VAD，开口/收声时回调 onSpeechStart/onSpeechEnd。
 */
export async function startMic(
  onPcm: (pcm: ArrayBuffer) => void,
  opts: MicOptions,
): Promise<MicHandle> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      // 回声消除靠浏览器（端侧免费），否则外放 TTS 会自激打断
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
      channelCount: 1,
    },
  });
  const ctx = new AudioContext({ sampleRate: MIC_RATE });
  await ctx.audioWorklet.addModule(opts.workletUrl);
  const src = ctx.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(ctx, 'pcm-processor');
  node.port.onmessage = (e) => onPcm(e.data as ArrayBuffer);
  src.connect(node);

  // 同一条流派生 analyser，仅供 UI 读音量，不连 destination（不外放麦克风）
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 1024;
  src.connect(analyser);
  const buf = new Uint8Array(analyser.fftSize);

  // Silero VAD：复用上面的 AudioContext（已是 16kHz，Silero 原生采样率）与麦克风流，
  // 不二次 getUserMedia。pauseStream/resumeStream 置空，确保流的生命周期只由本函数掌控
  // （vad-web 默认 pauseStream 会 stop tracks，会误杀上行 PCM）。
  let vad: MicVAD | null = null;
  try {
    vad = await MicVAD.new({
      model: 'v5',
      audioContext: ctx,
      getStream: async () => stream,
      pauseStream: async () => {},
      resumeStream: async () => stream,
      baseAssetPath: opts.vadAssetBaseUrl,
      onnxWASMBasePath: opts.vadAssetBaseUrl,
      startOnLoad: false,
      onSpeechStart: () => opts.onSpeechStart?.(),
      onSpeechEnd: () => opts.onSpeechEnd?.(),
      // 段太短被判为误触发：也按「结束」处理，避免外观卡在展开态
      onVADMisfire: () => opts.onSpeechEnd?.(),
    });
    await vad.start();
  } catch (e) {
    // 资源缺失（未跑 copy-vad-assets）或 wasm 加载失败时不应连累上行链路；
    // 调用方据 vadActive=false 回退到 RMS 门限。
    console.error('[vad] Silero VAD 初始化失败，回退到 RMS 门限', e);
    vad = null;
  }

  return {
    stop() {
      void vad?.destroy();
      stream.getTracks().forEach((t) => t.stop());
      src.disconnect();
      node.disconnect();
      analyser.disconnect();
      void ctx.close();
    },
    getLevel() {
      analyser.getByteTimeDomainData(buf);
      let sum = 0;
      for (const sample of buf) {
        const n = (sample - 128) / 128;
        sum += n * n;
      }
      return Math.min(1, Math.sqrt(sum / buf.length));
    },
    vadActive: vad !== null,
  };
}

export class Speaker {
  private ctx: AudioContext;
  private nextTime = 0;
  private playing = new Set<AudioBufferSourceNode>();
  // 最小可接受代号：小于此值的帧为 interrupt/start 之前的过期帧，丢弃
  private minGen = 0;

  constructor() {
    this.ctx = new AudioContext({ sampleRate: TTS_RATE });
  }

  /** 入队一个二进制帧：前 4 字节为代号（uint32 LE），其余为 PCM。 */
  enqueue(arrayBuffer: ArrayBuffer) {
    const gen = new DataView(arrayBuffer).getUint32(0, /* littleEndian */ true);
    if (gen < this.minGen) return; // 过期帧，丢弃

    const i16 = new Int16Array(arrayBuffer.slice(4));
    const f32 = new Float32Array(i16.length);
    for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 0x8000;

    const audioBuf = this.ctx.createBuffer(1, f32.length, TTS_RATE);
    audioBuf.copyToChannel(f32, 0);
    const node = this.ctx.createBufferSource();
    node.buffer = audioBuf;
    node.connect(this.ctx.destination);

    const t = Math.max(this.ctx.currentTime, this.nextTime);
    node.start(t);
    this.nextTime = t + audioBuf.duration;
    this.playing.add(node);
    node.onended = () => this.playing.delete(node);
  }

  /**
   * 冲掉所有已排期音频，并拒绝代号小于 minGen 的后续帧。
   * @param minGen 新的最小可接受代号（来自服务端 interrupt/start 消息）
   */
  flush(minGen = this.minGen + 1) {
    this.minGen = minGen;
    for (const node of this.playing) {
      try {
        node.stop();
      } catch {
        /* 已停止的 source 再 stop 会抛，忽略 */
      }
    }
    this.playing.clear();
    this.nextTime = 0;
  }

  /** 是否仍有已排期未播完的音频（用于判断助手是否还在出声）。 */
  get isPlaying() {
    return this.playing.size > 0;
  }

  /** 释放 AudioContext；关闭连接时调用。 */
  close() {
    this.flush();
    void this.ctx.close();
  }
}
