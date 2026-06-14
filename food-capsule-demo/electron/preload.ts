import { contextBridge, ipcRenderer } from 'electron';

contextBridge.exposeInMainWorld('electronAPI', {
  closeWindow: () => ipcRenderer.send('window:close'),
  setWindowMode: (mode: 'expanded' | 'mini') => ipcRenderer.send('window:set-mode', mode),
  resizeCapsule: (size: { width: number; height: number }) => ipcRenderer.send('capsule:resize', size),
});
