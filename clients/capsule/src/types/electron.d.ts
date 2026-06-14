export {};

declare global {
  interface Window {
    webkitAudioContext?: typeof AudioContext;
    electronAPI?: {
      closeWindow: () => void;
      setWindowMode: (mode: 'expanded' | 'mini') => void;
      resizeCapsule: (size: { width: number; height: number }) => void;
    };
  }
}
