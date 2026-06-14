// 摄像头：本地预览 + 按需抓帧（视觉链路，设计见 docs/vision-pipeline.md）。
// 画面只在被服务端索取时上云，平时零流量。移植自 clients/web/camera.js。

const MAX_EDGE = 768; // qwen-vl 按图块计 token，缩图直接省钱
const JPEG_QUALITY = 0.7;

export async function startCamera(video: HTMLVideoElement): Promise<MediaStream> {
  const stream = await navigator.mediaDevices.getUserMedia({
    video: { width: { ideal: 1280 }, height: { ideal: 720 } },
  });
  video.srcObject = stream;
  await video.play();
  return stream;
}

/** 抓一帧并返回 base64（不含 dataURL 前缀），供 WS frame 消息回传。 */
export function captureFrame(video: HTMLVideoElement): string {
  const scale = Math.min(1, MAX_EDGE / Math.max(video.videoWidth, video.videoHeight));
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(video.videoWidth * scale);
  canvas.height = Math.round(video.videoHeight * scale);
  canvas.getContext('2d')!.drawImage(video, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL('image/jpeg', JPEG_QUALITY).split(',')[1];
}
