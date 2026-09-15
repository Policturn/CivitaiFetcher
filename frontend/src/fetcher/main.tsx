import { createRoot } from 'react-dom/client';
import '@/index.css';
import App from './App';

// Civitai Fetcher 独立入口:与主应用共用 COSS 组件与主题 token,
// 由 pywebview 承载(window.pywebview.api 为后端桥)。
createRoot(document.getElementById('root')!).render(<App />);
