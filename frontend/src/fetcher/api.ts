/* Civitai Fetcher 前端公共层:桥访问 + 事件订阅 + 常量 */

/* eslint-disable @typescript-eslint/no-explicit-any */
export function api(): any {
    return (window as any).pywebview?.api ?? null;
}

export function pushEvent(detail: any) {
    window.dispatchEvent(new CustomEvent('fe:event', { detail }));
}

type Handler = (detail: any) => void;

export function useFeEvent(handler: Handler): () => void {
    const h = (ev: Event) => handler((ev as CustomEvent).detail);
    window.addEventListener('fe:event', h);
    return () => window.removeEventListener('fe:event', h);
}

export const inputCls =
    'h-8 min-w-0 rounded-lg border border-input bg-card px-2.5 text-xs text-foreground outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background disabled:opacity-64';

export const CIVITAI_TYPES = ['Checkpoint', 'TextualInversion', 'Hypernetwork',
    'LORA', 'LoCon', 'DoRA', 'VAE', 'Upscaler', 'MotionModel', 'Controlnet'];

export const TYPE_ZH: Record<string, string> = {
    Checkpoint: '底模', TextualInversion: 'Embedding', Hypernetwork: '超网络',
    LORA: 'Lora', LoCon: 'Lora', DoRA: 'DoRA', VAE: 'VAE',
    Upscaler: '放大器', MotionModel: '动作模型', Controlnet: 'ControlNet',
};

export const BASE_MODELS = ['SD 1.5', 'SDXL 1.0', 'Pony', 'Illustrious', 'NoobAI',
    'SD 3.5', 'SD 3.5 Medium', 'SD 3.5 Large', 'Flux.1 D', 'Flux.1 S',
    'Anima', 'ZImageTurbo', 'Krea 2', 'HiDream', 'Qwen'];

export const SORTS: Array<[string, string]> = [
    ['Most Downloaded', '下载最多'], ['Highest Rated', '评分最高'],
    ['Newest', '最新'], ['Most Liked', '点赞最多'],
    ['Most Collected', '收藏最多'], ['Most Discussed', '讨论最多'],
    ['Most Images', '图最多'], ['Oldest', '最早'],
];

export const PERIODS: Array<[string, string]> = [
    ['AllTime', '全部时间'], ['Year', '今年'], ['Month', '本月'],
    ['Week', '本周'], ['Day', '今天'],
];

/** 本地路径 → 合法 file:/// URL(中文/空格必须编码,否则 WebView2 加载失败回退远程大图) */
export function fileUrl(p: string): string {
    return 'file:///' + encodeURI(p.replace(/\\/g, '/'));
}

/** 原图 URL → CDN 小图(回退时也别碰原图,几十 MB 会压垮解码) */
export function thumbUrl(url: string, width = 450): string {
    if (!url) return url;
    return url.replace(/original=true/, 'width=' + width).replace(/width=\d+/, 'width=' + width);
}

export function fmtSize(sizeKB?: number): string {
    if (!sizeKB) return '—';
    if (sizeKB > 1048576) return (sizeKB / 1048576).toFixed(2) + ' GB';
    if (sizeKB > 1024) return (sizeKB / 1024).toFixed(1) + ' MB';
    return sizeKB + ' KB';
}
