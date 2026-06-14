import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  // 生产由 Electron 以 file:// 加载 dist，必须用相对路径，否则 /assets 解析失败
  base: './',
  plugins: [react()],
});
