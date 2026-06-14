// 后端 WS 地址。Electron 当外壳、Python 当本地服务，二者解耦：
// 默认连本机 8000；可用 VITE_BACKEND_WS 覆盖（如指向远端或改端口）。
export const WS_URL =
  (import.meta.env.VITE_BACKEND_WS as string | undefined) ?? 'ws://127.0.0.1:8000/ws';
