import ReactDOM from 'react-dom/client';
import App from './App';
import './styles/index.css';

// 不用 StrictMode：dev 下它会 mount→unmount→mount，导致重复建立 WS / 开两次麦克风。
// 本应用是单实例悬浮助手，副作用须严格只跑一次。
ReactDOM.createRoot(document.getElementById('root')!).render(<App />);
