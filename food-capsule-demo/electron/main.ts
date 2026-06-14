import { app, BrowserWindow, ipcMain, screen, session } from 'electron';
import path from 'node:path';

// 极简胶囊两态：静息是小圆点，激活横向展开。窗口随渲染端 capsule:resize 切换。
// 数值需与 src/components/Capsule.tsx 的 REST/ACTIVE_WINDOW 一致。
const EXPANDED_WINDOW = { width: 300, height: 120 }; // 激活态
const MINI_WINDOW = { width: 120, height: 120 }; // 静息态
const WINDOW_MARGIN = 24;

let mainWindow: BrowserWindow | null = null;

function getWindowPosition() {
  const cursorPoint = screen.getCursorScreenPoint();
  const display = screen.getDisplayNearestPoint(cursorPoint);
  const { x, y, width, height } = display.workArea;
  const [windowWidth, windowHeight] = mainWindow?.getSize() ?? [EXPANDED_WINDOW.width, EXPANDED_WINDOW.height];

  return {
    x: x + width - windowWidth - WINDOW_MARGIN,
    y: y + height - windowHeight - WINDOW_MARGIN,
  };
}

function anchorWindowToBottomRight() {
  if (!mainWindow) return;
  const position = getWindowPosition();
  mainWindow.setPosition(position.x, position.y);
}

function createMainWindow() {
  const position = getWindowPosition();

  mainWindow = new BrowserWindow({
    width: MINI_WINDOW.width,
    height: MINI_WINDOW.height,
    x: position.x,
    y: position.y,
    frame: false,
    transparent: true,
    resizable: false,
    alwaysOnTop: true,
    skipTaskbar: false,
    backgroundColor: '#00000000',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      // 允许 TTS 的 AudioContext 无用户手势自动播放（悬浮助手无点击启动步骤）
      autoplayPolicy: 'no-user-gesture-required',
    },
  });

  mainWindow.setAlwaysOnTop(true, 'floating');

  const devServerUrl = process.env.VITE_DEV_SERVER_URL;
  if (devServerUrl) {
    mainWindow.loadURL(devServerUrl);
  } else {
    mainWindow.loadFile(path.join(__dirname, '../dist/index.html'));
  }

  mainWindow.once('ready-to-show', () => {
    mainWindow?.show();
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

ipcMain.on('window:close', (event) => {
  BrowserWindow.fromWebContents(event.sender)?.close();
});

ipcMain.on('window:set-mode', (event, mode: 'expanded' | 'mini') => {
  const targetWindow = BrowserWindow.fromWebContents(event.sender);
  if (!targetWindow) return;

  const size = mode === 'mini' ? MINI_WINDOW : EXPANDED_WINDOW;
  targetWindow.setSize(size.width, size.height);
  mainWindow = targetWindow;
  anchorWindowToBottomRight();
});

ipcMain.on('capsule:resize', (event, size: { width: number; height: number }) => {
  const targetWindow = BrowserWindow.fromWebContents(event.sender);
  if (!targetWindow) return;

  // 以中心点为参考：保持窗口中心不动、向四周对称生长，而不是 snap 回屏幕角落。
  // 否则用户拖走胶囊后，每次 VAD 触发展开都会把它拉回屏幕右下角。
  const [curWidth, curHeight] = targetWindow.getSize();
  const [curX, curY] = targetWindow.getPosition();
  targetWindow.setBounds({
    x: Math.round(curX + (curWidth - size.width) / 2),
    y: Math.round(curY + (curHeight - size.height) / 2),
    width: size.width,
    height: size.height,
  });
  mainWindow = targetWindow;
});

app.whenReady().then(() => {
  session.defaultSession.setPermissionRequestHandler((_webContents, permission, callback) => {
    callback(permission === 'media');
  });

  createMainWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createMainWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
