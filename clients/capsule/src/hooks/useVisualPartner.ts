import { type RefObject, useEffect, useRef, useState } from 'react';
import { Speaker, startMic, type MicHandle } from '../lib/audio';
import { captureFrame, startCamera } from '../lib/camera';
import { WS_URL } from '../lib/config';
import workletUrl from '../lib/pcm-worklet.js?url';

// 胶囊外观由对话状态驱动：
//  connecting 连接中 / idle 空闲（收起）/ listening 用户说话 /
//  looking 调用视觉采集（眼睛盯着看）/ speaking 助手回复 / error 出错
export type VPStatus = 'connecting' | 'idle' | 'listening' | 'looking' | 'speaking' | 'error';

// 语音活动判定：优先用 Silero VAD（端侧 ONNX，能区分人声与键盘/敲击噪声），
// 见 lib/audio.ts。下面的 RMS 能量门限仅在 Silero 加载失败时作兜底——
// 它只看响度，会把键盘声误判为人声，故不作为首选。
const VAD_THRESHOLD = 0.04;
const SPEECH_START_MS = 120;
const SPEECH_END_MS = 700;

export type VisualPartner = {
  status: VPStatus;
  volume: number;
  errorMsg: string | null;
};

export function useVisualPartner(videoRef: RefObject<HTMLVideoElement | null>): VisualPartner {
  const [status, setStatus] = useState<VPStatus>('connecting');
  const [volume, setVolume] = useState(0);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // 这些值要在 rAF / WS 回调里读到最新，用 ref 旁路 React 闭包
  const statusRef = useRef<VPStatus>('connecting');
  const speakingRef = useRef(false); // 服务端：助手正在回复（start/delta→true，done/interrupt→false）

  const setStatusSafe = (next: VPStatus) => {
    if (statusRef.current === next) return;
    statusRef.current = next;
    setStatus(next);
  };

  useEffect(() => {
    let cancelled = false;
    let ws: WebSocket | null = null;
    let speaker: Speaker | null = null;
    let mic: MicHandle | null = null;
    let raf: number | null = null;
    let camStream: MediaStream | null = null;

    // ---------- 本地 VAD 状态（仅本 effect 内） ----------
    // vadSpeaking 由 Silero 回调驱动；下面三个仅 RMS 兜底路径使用。
    let aboveSince: number | null = null;
    let belowSince: number | null = null;
    let vadSpeaking = false;
    // startMic 返回后赋值：Silero 是否接管。false 时 tick 走 RMS 迟滞判定。
    let useSileroVad = false;

    // 视觉采集（look_at_camera）：tool_call 进入、tool_result 退出。
    // 记录 call_id 以便只对发起视觉的那次工具调用做匹配退出。
    let lookingActive = false;
    let lookingCallId: string | null = null;

    // ---------- 服务端事件分发（协议见 docs/voice-pipeline.md） ----------
    const onMessage = (event: MessageEvent) => {
      if (event.data instanceof ArrayBuffer) {
        // 二进制 TTS 帧：前 4 字节代号，Speaker 内部校验后丢弃过期帧
        speaker?.enqueue(event.data);
        return;
      }
      let msg: Record<string, unknown>;
      try {
        msg = JSON.parse(event.data as string);
      } catch {
        return; // 畸形帧忽略
      }
      switch (msg.type) {
        case 'start':
          // 新一轮回复开始，冲掉上一轮残余音频
          speaker?.flush(msg.gen as number);
          speakingRef.current = true;
          break;
        case 'delta':
          speakingRef.current = true;
          break;
        case 'done':
          speakingRef.current = false;
          lookingActive = false; // 回合结束，确保眼睛态不残留
          lookingCallId = null;
          break;
        case 'interrupt':
          // 携带代号：冲掉代号小于 gen 的在途音频帧
          speaker?.flush(msg.gen as number);
          speakingRef.current = false;
          lookingActive = false; // 被打断，视觉态一并清掉
          lookingCallId = null;
          break;
        case 'tool_call':
          // ReAct 视觉工具开始：进入眼睛态盯着看
          if (msg.name === 'look_at_camera') {
            lookingActive = true;
            lookingCallId = (msg.call_id as string) ?? null;
          }
          break;
        case 'tool_result':
          // 对应那次视觉调用看完，退出眼睛态
          if (lookingCallId !== null && msg.call_id === lookingCallId) {
            lookingActive = false;
            lookingCallId = null;
          }
          break;
        case 'capture': {
          // 视觉链路：服务端索取一帧（look_at_camera 触发）
          lookingActive = true; // 兜底：即使错过 tool_call，抓帧即说明正在看
          const video = videoRef.current;
          if (video && ws?.readyState === WebSocket.OPEN) {
            const data = captureFrame(video);
            ws.send(JSON.stringify({ type: 'frame', id: msg.id, data }));
          }
          break;
        }
        case 'error':
          setErrorMsg(String(msg.message ?? 'server error'));
          break;
        // asr / metrics / 其它 tool 暂不可视化
      }
    };

    // ---------- 外观循环：每帧更新音量与状态 ----------
    const tick = () => {
      const now = performance.now();
      const level = mic ? mic.getLevel() : 0;

      // RMS 兜底：仅当 Silero 未接管时启用迟滞判定；Silero 在时由回调维护 vadSpeaking。
      if (!useSileroVad) {
        if (level >= VAD_THRESHOLD) {
          belowSince = null;
          aboveSince ??= now;
          if (!vadSpeaking && now - aboveSince >= SPEECH_START_MS) vadSpeaking = true;
        } else {
          aboveSince = null;
          belowSince ??= now;
          if (vadSpeaking && now - belowSince >= SPEECH_END_MS) vadSpeaking = false;
        }
      }

      if (lookingActive) {
        // 视觉采集中：眼睛态优先于一切（此刻 speakingRef 可能仍为 true）
        setStatusSafe('looking');
      } else if (speakingRef.current || speaker?.isPlaying) {
        // 助手出声：合成律动（麦克风此时被回声消除压到接近 0，不能用真实音量）
        setStatusSafe('speaking');
        setVolume(0.28 + 0.18 * Math.abs(Math.sin(now / 180)));
      } else if (vadSpeaking) {
        setStatusSafe('listening');
        setVolume(level);
      } else {
        setStatusSafe('idle');
        setVolume(0);
      }

      raf = requestAnimationFrame(tick);
    };

    const cleanup = () => {
      if (raf !== null) cancelAnimationFrame(raf);
      mic?.stop();
      speaker?.close();
      camStream?.getTracks().forEach((t) => t.stop());
      if (ws && ws.readyState !== WebSocket.CLOSED) {
        ws.onclose = null; // 主动关闭，别再触发 onclose 改状态
        ws.close();
      }
    };

    (async () => {
      try {
        if (videoRef.current) camStream = await startCamera(videoRef.current);
        speaker = new Speaker();

        ws = new WebSocket(WS_URL);
        ws.binaryType = 'arraybuffer';
        ws.onmessage = onMessage;
        ws.onclose = () => {
          if (!cancelled) {
            setErrorMsg('连接已断开');
            setStatusSafe('error');
          }
        };
        await new Promise<void>((resolve, reject) => {
          ws!.onopen = () => resolve();
          ws!.onerror = () => reject(new Error('WS 连接失败'));
        });
        // onopen 后重新挂正式的 onerror/onclose（上面的 reject 版已完成使命）
        ws.onerror = null;

        mic = await startMic(
          (pcm) => {
            if (ws?.readyState === WebSocket.OPEN) ws.send(pcm);
          },
          {
            workletUrl,
            // public/vad 经 vite 服务/打包；dev 为 http、prod 为 file://，统一相对当前文档解析
            vadAssetBaseUrl: new URL('vad/', window.location.href).href,
            onSpeechStart: () => {
              vadSpeaking = true;
            },
            onSpeechEnd: () => {
              vadSpeaking = false;
            },
          },
        );
        useSileroVad = mic.vadActive;

        if (cancelled) {
          cleanup();
          return;
        }
        setErrorMsg(null);
        setStatusSafe('idle');
        raf = requestAnimationFrame(tick);
      } catch (e) {
        if (!cancelled) {
          setErrorMsg(e instanceof Error ? e.message : String(e));
          setStatusSafe('error');
        }
        cleanup();
      }
    })();

    return () => {
      cancelled = true;
      cleanup();
    };
  }, [videoRef]);

  return { status, volume, errorMsg };
}
