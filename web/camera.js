// 摄像头：本地预览 + 按需抓帧（视觉链路，设计见 docs/vision-pipeline.md）。
// 画面只在被索取时上云，平时零流量零成本（成本策略 C1）。

const MAX_EDGE = 768; // qwen-vl 按图块计 token，缩图直接省钱（成本策略 C2）
const JPEG_QUALITY = 0.7;

export async function startCamera(videoEl) {
  const stream = await navigator.mediaDevices.getUserMedia({
    video: { width: { ideal: 1280 }, height: { ideal: 720 } },
  });
  videoEl.srcObject = stream;
  await videoEl.play();
}

export function captureFrame(videoEl) {
  const scale = Math.min(1, MAX_EDGE / Math.max(videoEl.videoWidth, videoEl.videoHeight));
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(videoEl.videoWidth * scale);
  canvas.height = Math.round(videoEl.videoHeight * scale);
  canvas.getContext('2d').drawImage(videoEl, 0, 0, canvas.width, canvas.height);
  // 去掉 dataURL 前缀，只传 base64 体
  return canvas.toDataURL('image/jpeg', JPEG_QUALITY).split(',')[1];
}

// --- 主动视觉：本地帧采样原语（设计见 docs/vision-pipeline.md）---
// 把画面缩到极小尺寸取灰度，供上层搭流水线（帧差 / 关键帧对比）。
// 纯本地计算，不上云；只有上层判定「画面真的变了」才触发后端取帧 VL（成本策略 C1）。

const DIFF_EDGE = 64; // 64×64 灰度近似足够判大幅变化，运算量可忽略
const _diffCanvas = document.createElement('canvas');
_diffCanvas.width = _diffCanvas.height = DIFF_EDGE;
// willReadFrequently：提示浏览器走 CPU 后端，频繁 getImageData 更快
const _diffCtx = _diffCanvas.getContext('2d', { willReadFrequently: true });

/**
 * 抓当前画面的 64×64 灰度缓冲（取 R 通道近似灰度，长度 DIFF_EDGE²）。
 * 视频未就绪时返回 null（metadata 尚未加载，画面不可读）。
 */
export function grabScenePixels(videoEl) {
  if (!videoEl.videoWidth) return null;
  _diffCtx.drawImage(videoEl, 0, 0, DIFF_EDGE, DIFF_EDGE);
  const rgba = _diffCtx.getImageData(0, 0, DIFF_EDGE, DIFF_EDGE).data;
  const gray = new Uint8Array(DIFF_EDGE * DIFF_EDGE);
  for (let i = 0, j = 0; i < rgba.length; i += 4, j++) gray[j] = rgba[i];
  return gray;
}

/** 两个等长灰度缓冲的归一化平均像素差（0~255）。供帧差与关键帧对比共用。 */
export function pixelDiff(a, b) {
  let sad = 0;
  for (let i = 0; i < a.length; i++) sad += Math.abs(a[i] - b[i]);
  return sad / a.length;
}
