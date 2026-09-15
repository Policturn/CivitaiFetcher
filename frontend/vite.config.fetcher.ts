import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import path from 'node:path';

// Civitai Fetcher 独立构建入口:只打 fetcher.html,产物给 pywebview/PyInstaller 用。
// 不影响主应用 `vite build`(index.html)。
export default defineConfig({
    plugins: [react(), tailwindcss()],
    resolve: {
        alias: { '@': path.resolve(__dirname, './src') },
    },
    base: './',
    publicDir: false, // 词库/字体等 public 资源不进这个产物,字体由打包脚本单独拷贝
    build: {
        outDir: path.resolve(__dirname, '../../civitai-fetcher/ui-dist'),
        emptyOutDir: true,
        rollupOptions: {
            input: path.resolve(__dirname, 'fetcher.html'),
        },
    },
});
