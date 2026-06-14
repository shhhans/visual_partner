// 将 Silero VAD 模型、vad-web worklet 与 onnxruntime-web 的 wasm/glue 拷贝到 public/vad。
// 为什么不直接 import：vad-web 在运行时按「目录 + 固定文件名」拼路径加载这些资源
// （见 real-time-vad.js 的 baseAssetPath/onnxWASMBasePath），需要它们以原文件名躺在一个可访问目录里，
// 而非被 Vite 指纹化打散。放进 public/ 后 dev 由 vite 服务、build 时自动复制进 dist。
// 这些是二进制大文件（onnx 2.3M + wasm 13M），不入库，由本脚本在 dev/build 前从 node_modules 重建。

import { mkdirSync, copyFileSync, existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..');
const outDir = join(root, 'public', 'vad');

// [源文件, 目标文件名]
const assets = [
  ['node_modules/@ricky0123/vad-web/dist/vad.worklet.bundle.min.js', 'vad.worklet.bundle.min.js'],
  ['node_modules/@ricky0123/vad-web/dist/silero_vad_v5.onnx', 'silero_vad_v5.onnx'],
  // onnxruntime-web 的 wasm 后端：运行时同时加载 .mjs(glue) 与 .wasm，二者缺一不可
  ['node_modules/onnxruntime-web/dist/ort-wasm-simd-threaded.mjs', 'ort-wasm-simd-threaded.mjs'],
  ['node_modules/onnxruntime-web/dist/ort-wasm-simd-threaded.wasm', 'ort-wasm-simd-threaded.wasm'],
];

mkdirSync(outDir, { recursive: true });

for (const [src, name] of assets) {
  const from = join(root, src);
  if (!existsSync(from)) {
    console.error(`[copy-vad-assets] 缺少源文件：${src}（请先 npm install）`);
    process.exit(1);
  }
  copyFileSync(from, join(outDir, name));
}

console.log(`[copy-vad-assets] 已拷贝 ${assets.length} 个 VAD 资源到 public/vad`);
