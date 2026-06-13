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
let toolCards = {};       // call_id → 工具卡片节点，用于 tool_result 回填

function addLine(role) {
  const div = document.createElement('div');
  div.className = `line ${role}`;
  transcriptEl.appendChild(div);
  transcriptEl.scrollTop = transcriptEl.scrollHeight;
  return div;
}

// ReAct 工具可视化：图标/中文名 + 入参友好摘要（设计见对话流内联卡片）
const TOOL_META = {
  look_at_camera: { icon: '📷', label: '查看摄像头' },
  get_datetime:   { icon: '🕐', label: '查询时间' },
  calculate:      { icon: '🧮', label: '计算' },
  get_weather:    { icon: '🌤️', label: '查询天气' },
  web_search:     { icon: '🔍', label: '联网搜索' },
};

function summarizeArgs(name, args) {
  switch (name) {
    case 'look_at_camera': return args.question || '';
    case 'calculate':      return args.expression || '';
    case 'get_weather':    return args.city || '';
    case 'web_search':     return args.query || '';
    default:               return '';
  }
}

// 全程用 DOM 构建，不用 innerHTML：入参/结果均为不可信文本，避免 XSS
function addToolCard(msg) {
  const meta = TOOL_META[msg.name] || { icon: '🔧', label: msg.name };
  const card = document.createElement('div');
  card.className = 'tool-card pending';
  card.dataset.callId = msg.call_id;
  card.dataset.tool = msg.name;

  const head = document.createElement('div');
  head.className = 'tool-head';
  head.append(spanEl('tool-icon', meta.icon), spanEl('tool-label', meta.label));
  const summary = summarizeArgs(msg.name, msg.arguments);
  if (summary) head.append(spanEl('tool-arg', summary));
  card.append(head);

  // VL 卡片预留缩略图槽，由后续 capture 事件填入实际抓帧
  if (msg.name === 'look_at_camera') {
    const img = document.createElement('img');
    img.className = 'tool-thumb'; // 故意不设 src，capture 时再填，便于 :not([src]) 选中
    card.append(img);
  }

  const body = document.createElement('div');
  body.className = 'tool-body';
  body.textContent = '处理中…';
  card.append(body);

  transcriptEl.appendChild(card);
  transcriptEl.scrollTop = transcriptEl.scrollHeight;
  return card;
}

function spanEl(cls, text) {
  const s = document.createElement('span');
  s.className = cls;
  s.textContent = text;
  return s;
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
      // 未完成的工具卡片随回复一起标记中断（结果已不会再回来）
      transcriptEl.querySelectorAll('.tool-card.pending').forEach((c) => {
        c.classList.replace('pending', 'interrupted');
        c.querySelector('.tool-body').textContent = '（已中断）';
      });
      toolCards = {};
      break;
    case 'metrics':
      renderMetrics(msg);
      break;
    case 'tool_call':
      // ReAct：工具开始执行，插入卡片占位
      toolCards[msg.call_id] = addToolCard(msg);
      assistantLine = null; // 工具调用后续的回复另起一行
      break;
    case 'tool_result': {
      // ReAct：工具结果回填对应卡片
      const card = toolCards[msg.call_id];
      if (card) {
        card.classList.replace('pending', 'done');
        card.querySelector('.tool-body').textContent = msg.content;
        delete toolCards[msg.call_id];
      }
      break;
    }
    case 'capture': {
      // 视觉链路：服务端索取一帧（ReAct 工具触发）
      const b64 = captureFrame(videoEl);
      ws.send(JSON.stringify({ type: 'frame', id: msg.id, data: b64 }));
      // 把实际抓到的这一帧贴进等待中的 VL 卡片缩略图槽
      const slot = transcriptEl.querySelector(
        '.tool-card[data-tool="look_at_camera"].pending img.tool-thumb:not([src])'
      );
      if (slot) slot.src = `data:image/jpeg;base64,${b64}`;
      break;
    }
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
