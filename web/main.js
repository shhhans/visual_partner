// 入口：连 WS、起摄像头麦克风、分发服务端事件。协议见 docs/voice-pipeline.md。

import { startMic, Speaker } from './audio.js';
import { startCamera, captureFrame } from './camera.js';

const videoEl = document.getElementById('camera');
const transcriptEl = document.getElementById('transcript');
const metricsEl = document.getElementById('metrics');
const statusEl = document.getElementById('status');
const startBtn = document.getElementById('start-btn');

// 延迟指标调试面板：每回合一行，最新在上，最多保留 8 行。字段含义见 server/metrics.py。
function fmtMs(v) { return v == null ? '—' : `${Math.round(v)}ms`; }
function renderMetrics(msg) {
  const line = document.createElement('div');
  line.className = 'metric-line' + (msg.interrupted ? ' interrupted' : '');
  line.textContent =
    `#${msg.turn_index} · closure ${fmtMs(msg.turn_closure_ms)} · ttft ${fmtMs(msg.ttft_ms)}` +
    ` · tts ${fmtMs(msg.tts_first_ms)} · e2e ${fmtMs(msg.e2e_first_audio_ms)}` +
    ` · total ${fmtMs(msg.total_ms)} · ${msg.reply_chars}字` +
    (msg.interrupted ? ' · 中断' : '');
  metricsEl.prepend(line);
  while (metricsEl.children.length > 8) metricsEl.removeChild(metricsEl.lastChild);
}

let ws = null;
let speaker = null;
let micHandle = null;     // startMic() 返回的 {stop()} 句柄
let userLine = null;      // 当前用户句的字幕节点（partial 持续覆写）
let assistantLine = null; // 当前助手回复的字幕节点（delta 持续追加）

function addLine(role) {
  const div = document.createElement('div');
  div.className = `line ${role}`;
  transcriptEl.appendChild(div);
  transcriptEl.scrollTop = transcriptEl.scrollHeight;
  return div;
}

function onMessage(event) {
  if (event.data instanceof ArrayBuffer) {
    // 二进制帧：前4字节为代号，Speaker 内部校验后丢弃过期帧
    speaker.enqueue(event.data);
    return;
  }
  let msg;
  try {
    msg = JSON.parse(event.data);
  } catch (e) {
    console.error('[ws] 收到格式错误的文本帧，忽略：', e);
    return;
  }
  switch (msg.type) {
    case 'start':
      // 新一轮回复开始，冲掉上一轮残余音频（含 done 后仍在播放的缓冲帧）
      speaker.flush(msg.gen);
      break;
    case 'asr':
      if (!userLine) userLine = addLine('user');
      userLine.textContent = msg.text;
      if (msg.final) {
        userLine = null;
        assistantLine = null; // 下一条 delta 开新行
      }
      break;
    case 'delta':
      if (!assistantLine) assistantLine = addLine('assistant');
      assistantLine.textContent += msg.text;
      transcriptEl.scrollTop = transcriptEl.scrollHeight;
      break;
    case 'done':
      assistantLine = null;
      break;
    case 'interrupt':
      // 携带代号：冲掉所有代号小于 msg.gen 的已排期及在途音频帧
      speaker.flush(msg.gen);
      if (assistantLine) assistantLine.classList.add('interrupted');
      assistantLine = null;
      break;
    case 'metrics':
      renderMetrics(msg);
      break;
    case 'capture':
      // 视觉链路：服务端索取一帧（ReAct 工具触发）
      ws.send(JSON.stringify({ type: 'frame', id: msg.id, data: captureFrame(videoEl) }));
      break;
    case 'error':
      statusEl.textContent = `出错：${msg.message}`;
      break;
  }
}

function cleanup(spk, mic, socket) {
  if (mic) { mic.stop(); }
  if (spk) { spk.close(); }
  if (socket && socket.readyState !== WebSocket.CLOSED) { socket.close(); }
}

async function start() {
  startBtn.disabled = true;
  statusEl.textContent = '正在连接…';

  let spk = null;
  let mic = null;
  let socket = null;
  try {
    await startCamera(videoEl);
    spk = new Speaker();

    socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
    socket.binaryType = 'arraybuffer';
    socket.onmessage = onMessage;
    socket.onclose = () => {
      statusEl.textContent = '连接已断开，刷新页面重试';
      // 释放麦克风和音频资源
      if (micHandle) { micHandle.stop(); micHandle = null; }
      if (speaker) { speaker.close(); speaker = null; }
    };
    await new Promise((ok, err) => { socket.onopen = ok; socket.onerror = err; });

    mic = await startMic((pcmBuffer) => {
      if (socket.readyState === WebSocket.OPEN) socket.send(pcmBuffer);
    });

    // 全部成功后提升到模块级变量
    ws = socket;
    speaker = spk;
    micHandle = mic;

    statusEl.textContent = '对话中：直接说话即可';
    startBtn.style.display = 'none';
  } catch (e) {
    statusEl.textContent = `启动失败：${e.message}`;
    // 释放已初始化的资源，防止泄漏
    cleanup(spk, mic, socket);
    startBtn.disabled = false;
  }
}

startBtn.addEventListener('click', start);
