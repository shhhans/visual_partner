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
