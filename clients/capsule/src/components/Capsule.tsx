import { useEffect, useRef } from 'react';
import { useVisualPartner } from '../hooks/useVisualPartner';

// 窗口尺寸需与 electron/main.ts 的 REST/ACTIVE 约定一致：
// 圆球态是一个小圆点，展开态横向拉长成长胶囊。窗口比胶囊大一圈，给阴影/关闭按钮留余量。
const REST_WINDOW = { width: 120, height: 120 };
const ACTIVE_WINDOW = { width: 300, height: 120 };

export default function Capsule() {
  // 隐藏的抓帧源：摄像头画面只在服务端 capture 时上传，平时不显示
  const videoRef = useRef<HTMLVideoElement>(null);
  const { status, volume } = useVisualPartner(videoRef);

  const isError = status === 'error';
  const isSpeaking = status === 'speaking';
  const isLooking = status === 'looking';
  // 只有用户说话、助手回复、出错才横向展开；视觉采集与空闲都保持圆球形态
  const expanded = status === 'listening' || isSpeaking || isError;

  // 状态驱动 Electron 窗口尺寸跟随，避免透明留白挡住下层点击
  useEffect(() => {
    window.electronAPI?.resizeCapsule(expanded ? ACTIVE_WINDOW : REST_WINDOW);
  }, [expanded]);

  return (
    // 外层透明、非拖拽区、不接收指针：点空白/阴影处不会误拖窗口（误触根因是早先整窗都是拖拽区）。
    <div className="pointer-events-none flex h-screen w-screen items-center justify-center">
      {/* 抓帧用，视觉上不可见但需 play() 才有 videoWidth */}
      <video
        ref={videoRef}
        muted
        playsInline
        className="pointer-events-none fixed left-0 top-0 h-px w-px opacity-0"
      />

      <div
        // 仅胶囊本体可交互、可拖动；周围透明边距由外层 pointer-events-none 兜底。
        className={`app-region-drag pointer-events-auto group relative flex items-center justify-center rounded-full bg-neutral-950 transition-all duration-300 ease-out ${
          expanded
            ? 'h-14 w-[240px] shadow-[0_8px_24px_rgba(0,0,0,.45)]'
            : 'h-12 w-12 shadow-[0_4px_14px_rgba(0,0,0,.4)]'
        } ${
          isError
            ? 'ring-1 ring-red-500/40'
            : expanded
            ? 'ring-1 ring-white/15'
            : 'ring-1 ring-white/10'
        }`}
      >
        {/* 顶部一道极淡高光，给纯黑胶囊一点立体感 */}
        <div className="pointer-events-none absolute inset-0 rounded-full bg-gradient-to-b from-white/[0.06] to-transparent" />

        {isError ? (
          <span className="h-2.5 w-2.5 rounded-full bg-red-500 shadow-[0_0_10px_rgba(239,68,68,.8)]" />
        ) : isLooking ? (
          <LookingDot />
        ) : expanded ? (
          <Dots volume={volume} speaking={isSpeaking} />
        ) : (
          // 静息：单个呼吸光点
          <span className="h-2.5 w-2.5 rounded-full bg-white/70 animate-pulse" />
        )}

        {/* hover 浮现的关闭按钮（无边框窗口需要一个可达的关闭入口） */}
        <button
          className="app-region-no-drag absolute -right-1 -top-1 grid h-5 w-5 place-items-center rounded-full bg-neutral-800 text-[11px] leading-none text-white/80 opacity-0 transition hover:bg-neutral-700 group-hover:opacity-100"
          onClick={() => window.electronAPI?.closeWindow()}
          type="button"
          aria-label="Close"
          title="Close"
        >
          ×
        </button>
      </div>
    </div>
  );
}

// 视觉采集态：圆球内一个白点偏心放置，随旋转容器画圆“滴溜溜”巡视，像在四处张望。
function LookingDot() {
  return (
    <div className="absolute inset-0 grid place-items-center animate-eyeroll" aria-hidden="true">
      <span className="h-2.5 w-2.5 translate-x-[7px] rounded-full bg-white shadow-[0_0_8px_rgba(255,255,255,.75)]" />
    </div>
  );
}

// 激活态内部律动光点：一排小圆点，按相位 + 实时音量做明暗与缩放跳动。
// volume 每帧更新会触发重渲染，配合 Date.now() 相位形成连续动画。
// 助手回复（speaking）时点偏冷色（青），与用户说话（白）区分。
function Dots({ volume, speaking }: { volume: number; speaking: boolean }) {
  const count = 7;
  const now = Date.now();
  return (
    <div className="flex items-center gap-1.5" aria-hidden="true">
      {Array.from({ length: count }, (_, i) => {
        const wave = Math.sin(i * 0.7 + now / 180) * 0.5 + 0.5;
        const scale = Math.min(2.2, 0.6 + wave * 0.5 + volume * 1.6);
        const opacity = Math.min(1, 0.4 + wave * 0.4 + volume * 1.2);
        return (
          <span
            key={i}
            className={`h-1.5 w-1.5 rounded-full ${speaking ? 'bg-sky-300' : 'bg-white'}`}
            style={{ transform: `scale(${scale})`, opacity }}
          />
        );
      })}
    </div>
  );
}
