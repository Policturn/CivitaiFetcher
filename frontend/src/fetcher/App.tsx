import { useEffect, useRef, useState } from 'react';
import { BarChart3, FolderOpen, Play, Settings, Square } from 'lucide-react';
import { Select, SelectTrigger, SelectValue, SelectPopup, SelectItem } from '@/components/ui/select';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Dialog, DialogPopup, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { cn } from '@/lib/utils';
import { api, pushEvent, useFeEvent, inputCls, CIVITAI_TYPES, TYPE_ZH, BASE_MODELS, SORTS, PERIODS, fmtSize } from './api';
import BrowseView from './BrowseView';
import DownloadsView from './DownloadsView';
import ReorganizeView from './ReorganizeView';

const NAV_TABS: Array<[string, string]> = [
    ['scan', '扫描补档'],
    ['browse', 'Civitai 浏览'],
    ['downloads', '下载队列'],
    ['reorganize', '整理归类'],
];

// ---------------------------------------------------------------- 后端桥(pywebview)

type FeEvent =
    | { type: 'log'; line: string; tag?: string }
    | { type: 'progress'; done: number; total: number }
    | { type: 'done'; stats: Record<string, number> }
    | { type: 'crash'; error: string };

/* eslint-disable @typescript-eslint/no-explicit-any */

function onEvent(cb: (e: FeEvent) => void) {
    const handler = (e: Event) => cb((e as CustomEvent).detail as FeEvent);
    window.addEventListener('fe:event', handler);
    return () => window.removeEventListener('fe:event', handler);
}
/* eslint-enable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- 常量

const TYPE_LABELS: Array<[string, string]> = [
    ['lora', 'Lora'],
    ['lycoris', 'LyCORIS'],
    ['ckp', 'Checkpoint'],
    ['vae', 'VAE'],
    ['ti', 'Embeddings'],
    ['hyper', 'Hypernetworks'],
];

const OPTION_LABELS: Array<[string, string]> = [
    ['no_names', '隐藏文件名'],
    ['retry_skeletons', '重试骨架文件'],
    ['refetch_old', '重扫旧格式缓存'],
    ['force_preview', '强制重下预览图'],
];

const STAT_KEYS: Array<[string, string, string]> = [
    ['info_written', '补到信息', 'text-emerald-400'],
    ['skeleton', '骨架文件', 'text-amber-500'],
    ['preview_new', '新增预览', 'text-emerald-400'],
    ['preview_fail', '预览失败', 'text-red-400'],
    ['skipped', '已有跳过', 'text-muted-foreground'],
    ['api_error', '网络错误', 'text-red-400'],
];

const LOG_TAG_CLASS: Record<string, string> = {
    muted: 'text-muted-foreground',
    amber: 'text-amber-500',
    red: 'text-red-400',
    green: 'text-emerald-400',
    bold: 'text-foreground font-medium',
};

// ---------------------------------------------------------------- 页面

export default function App() {
    const [ready, setReady] = useState(false);
    const [running, setRunning] = useState(false);
    const [webuiRoot, setWebuiRoot] = useState('H:\\sd-webui-aki-v4.4');
    const [types, setTypes] = useState<Record<string, boolean>>(
        () => Object.fromEntries(TYPE_LABELS.map(([k]) => [k, k === 'lora' || k === 'lycoris'])),
    );
    const [options, setOptions] = useState<Record<string, boolean>>(
        () => Object.fromEntries(OPTION_LABELS.map(([k, _]) => [k, k === 'no_names'])),
    );
    const [proxy, setProxy] = useState('');
    const [apiKey, setApiKey] = useState('');
    const [stats, setStats] = useState<Record<string, number>>({});
    const [progress, setProgress] = useState<[number, number]>([0, 0]);
    const [status, setStatus] = useState('就绪 — 点击「开始扫描」补全模型信息');
    const [lines, setLines] = useState<Array<{ text: string; tag?: string }>>([]);
    const [view, setView] = useState('scan');
    const [user, setUser] = useState('');
    const [demo, setDemo] = useState(false);
    const [apiSource, setApiSource] = useState('com');
    const demoAutoRan = useRef(false);
    const [settingsOpen, setSettingsOpen] = useState(false);
    const logBox = useRef<HTMLDivElement>(null);

    // 绑定后端事件与初始状态
    useEffect(() => {
        return onEvent((e) => {
            if (e.type === 'log') {
                setLines((prev) => [...prev.slice(-800), { text: e.line, tag: e.tag }]);
            } else if (e.type === 'progress') {
                setProgress([e.done, e.total]);
                setStatus(`运行中… ${e.done}/${e.total}`);
            } else if (e.type === 'done') {
                setStats(e.stats ?? {});
                setRunning(false);
                const n = e.stats?.need_info;
                if (n !== undefined) {
                    setStatus(`统计完成 — 待补信息 ${n},缺预览图 ${e.stats?.no_preview ?? 0}`);
                } else {
                    setStatus(`完成 — 补到信息 ${e.stats?.info_written ?? 0},骨架 ${e.stats?.skeleton ?? 0},预览 +${e.stats?.preview_new ?? 0}`);
                }
            } else if (e.type === 'crash') {
                setRunning(false);
                setStatus('出错,详见日志');
            }
        });
    }, []);

    useEffect(() => {
        const check = () => {
            const a = api();
            if (a) {
                a.get_initial().then((s: any) => {
                    if (s) {
                        setDemo(!!s.demo);
                        if (s?.api_source) setApiSource(String(s.api_source));
                        if (s?.browse?.view) setView(String(s.browse.view));
                        setWebuiRoot(s.webui_root ?? webuiRoot);
                        setProxy(s.proxy ?? '');
                        setApiKey(s.api_key ?? '');
                        if (s.types) setTypes((p) => ({ ...p, ...s.types }));
                        if (s.options) setOptions((p) => ({ ...p, ...s.options }));
                    }
                    setReady(true);
                    if (s?.demo && !demoAutoRan.current) {
                        demoAutoRan.current = true;
                        setTimeout(() => start(false), 1200);
                    }
                }).catch(() => setReady(true));
            } else {
                setTimeout(check, 120);
            }
        };
        check();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    useEffect(() => {
        logBox.current?.scrollTo({ top: logBox.current.scrollHeight });
    }, [lines]);

    // 页签记忆:切页防抖保存,重开恢复
    useEffect(() => {
        if (!ready || !api()) return;
        const t = setTimeout(() => { api().save_browse?.({ view }); }, 500);
        return () => clearTimeout(t);
    }, [view, ready]);

    // 登录状态:有 Key 时验证一次
    useEffect(() => {
        if (!apiKey || !ready || !api()) { setUser(''); return; }
        api().check_api_key({ apiKey, proxy })
            .then((r: any) => setUser(r?.ok ? r.username || '' : ''))
            .catch(() => setUser(''));
    }, [apiKey, proxy, ready]);

    const addLine = (text: string, tag?: string) =>
        setLines((prev) => [...prev.slice(-800), { text, tag }]);

    // ---------------------------------------------------------------- 动作

    const collectOpts = (dry: boolean) => ({
        webui_root: webuiRoot,
        types: Object.keys(types).filter((k) => types[k]).join(','),
        refetch_old: !!options.refetch_old,
        retry_skeletons: !!options.retry_skeletons,
        no_names: !!options.no_names,
        force_preview: !!options.force_preview,
        proxy,
        api_key: apiKey,
        dry_run: dry,
    });

    const guard = () => {
        if (!Object.values(types).some(Boolean)) {
            setStatus('至少选择一个模型类型');
            return false;
        }
        return true;
    };

    const start = async (dry: boolean) => {
        if (!guard() || running) return;
        setRunning(true);
        setLines([]);
        setProgress([0, 0]);
        addLine(`◆ ${dry ? '统计' : '扫描'} · ${Object.keys(types).filter((k) => types[k]).join(',')} · ${webuiRoot}`, 'bold');
        setStatus('运行中…');
        try {
            await api().start_scan({ ...collectOpts(dry) });
        } catch (e) {
            setRunning(false);
            setStatus('出错,详见日志');
        }
    };

    const stop = async () => {
        setStatus('正在停止…(等当前文件处理完)');
        await api().stop_scan();
    };

    const browse = async () => {
        const dir = await api().choose_folder();
        if (dir) setWebuiRoot(Array.isArray(dir) ? dir[0] : dir);
    };

    // ---------------------------------------------------------------- 视图

    const inputCls =
        'h-8 min-w-0 rounded-lg border border-input bg-card px-2.5 text-xs text-foreground outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background disabled:opacity-64';

    return (
        <div className="flex h-screen min-h-0 flex-col gap-3 bg-background p-4 font-sans text-foreground">
            <nav className="flex items-center justify-between border-b border-border pb-2">
                <div className="flex items-center gap-1">
                    {NAV_TABS.map(([v, label]) => (
                        <button
                            key={v}
                            onClick={() => setView(v)}
                            className={cn(
                                'cursor-pointer rounded-md px-3 py-1.5 text-sm transition-colors',
                                view === v
                                    ? 'bg-accent font-medium text-foreground'
                                    : 'text-muted-foreground hover:bg-accent/60 hover:text-foreground',
                            )}
                        >
                            {label}
                        </button>
                    ))}
                </div>
                <div className="flex items-center gap-2">
                    {demo && <span className="rounded bg-amber-500/15 px-2 py-0.5 text-xs font-medium text-amber-500">演示模式 · 离线数据</span>}
                    {user && <span className="text-xs text-emerald-400">已登录 @{user}</span>}
                    <Button variant="ghost" size="icon-sm" title="设置 / 登录" onClick={() => setSettingsOpen(true)}>
                        <Settings />
                    </Button>
                    <span className="h-4 w-px bg-border" />
                    <div className="text-xs text-muted-foreground">非猫 Civitai 信息补全器</div>
                </div>
            </nav>

            <div className={cn('flex min-h-0 flex-1', view !== 'scan' && 'hidden')}>
            <div className="flex min-h-0 flex-1">
            {/* ===== 左侧栏(组间横线分割) */}
            <aside className="flex w-60 shrink-0 flex-col overflow-y-auto pr-4">
                <div className="border-b border-border pb-3">
                    <div className="text-lg font-bold leading-tight">Civitai Fetcher</div>
                    <div className="text-xs text-muted-foreground">模型信息离线补全</div>
                </div>

                <div className="flex flex-col gap-1 border-b border-border py-3">
                <div className="mb-1 text-xs text-muted-foreground">模型类型</div>
                    {TYPE_LABELS.map(([key, label]) => (
                        <label key={key} className="flex cursor-pointer items-center gap-2 rounded-md px-1 py-1 text-sm hover:bg-accent">
                            <Checkbox
                                checked={!!types[key]}
                                onCheckedChange={(c) => setTypes((p) => ({ ...p, [key]: !!c }))}
                            />
                            {label}
                        </label>
                    ))}
                </div>

                <div className="flex flex-col gap-1 border-b border-border py-3">
                <div className="mb-1 text-xs text-muted-foreground">选项</div>
                    {OPTION_LABELS.map(([key, label]) => (
                        <label key={key} className="flex cursor-pointer items-center gap-2 rounded-md px-1 py-1 text-sm hover:bg-accent">
                            <Checkbox
                                checked={!!options[key]}
                                onCheckedChange={(c) => setOptions((p) => ({ ...p, [key]: !!c }))}
                            />
                            {label}
                        </label>
                    ))}
                </div>

                <div className="flex flex-col gap-2 py-3">
                <div className="mb-1 text-xs text-muted-foreground">网络</div>
                    <div className="text-xs text-muted-foreground">代理</div>
                    <input className={inputCls} value={proxy} onChange={(e) => setProxy(e.target.value)} placeholder="http://127.0.0.1:7890" />
                    <div className="text-xs text-muted-foreground">Civitai API Key</div>
                    <input className={inputCls} type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="登录内容才需要" />
                </div>
            </aside>

            {/* ===== 右侧主区(与侧栏竖线分割) */}
            <main className="flex min-w-0 flex-1 flex-col gap-3 border-l border-border pl-4">
                <div className="flex items-center gap-1">
                    <input
                        className={cn(inputCls, 'h-9 flex-1 text-sm')}
                        value={webuiRoot}
                        onChange={(e) => setWebuiRoot(e.target.value)}
                        placeholder="SD WebUI 根目录"
                    />
                    <span className="mx-1 h-5 w-px bg-border" />
                    <Button variant="ghost" size="icon" title="浏览" onClick={browse} disabled={running}>
                        <FolderOpen />
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => start(true)} disabled={running || !ready}>
                        <BarChart3 /> 统计
                    </Button>
                    <span className="mx-1 h-5 w-px bg-border" />
                    <Button
                        size="sm"
                        className="border-success bg-success text-success-foreground hover:bg-success/90 data-pressed:bg-success/90"
                        onClick={() => start(false)}
                        disabled={running || !ready}
                    >
                        <Play /> 开始扫描
                    </Button>
                    <Button
                        variant="ghost"
                        size="sm"
                        className="text-red-400 hover:bg-red-500/10 hover:text-red-300"
                        onClick={stop}
                        disabled={!running}
                    >
                        <Square /> 停止
                    </Button>
                </div>

                <div className="grid grid-cols-6 gap-2">
                    {STAT_KEYS.map(([key, label, color]) => (
                        <div key={key} className="rounded-lg border border-border bg-card px-3 py-2">
                            <div className={cn('text-lg font-bold leading-6', color)}>{stats[key] ?? 0}</div>
                            <div className="text-xs text-muted-foreground">{label}</div>
                        </div>
                    ))}
                </div>

                <div
                    ref={logBox}
                    className="min-h-0 flex-1 overflow-y-auto rounded-lg border border-border bg-card p-3 font-mono text-xs leading-5"
                >
                    {lines.map((l, i) => (
                        <div key={i} className={cn('whitespace-pre-wrap break-all', l.tag ? LOG_TAG_CLASS[l.tag] : undefined)}>
                            {l.text}
                        </div>
                    ))}
                </div>

                <div className="flex items-center gap-3">
                    <div className="min-w-0 flex-1 truncate text-xs text-muted-foreground">{status}</div>
                    <div className="h-2 w-56 overflow-hidden rounded-full bg-input">
                        <div
                            className="h-full rounded-full bg-success transition-[width] duration-200"
                            style={{ width: progress[1] ? `${(progress[0] / progress[1]) * 100}%` : '0%' }}
                        />
                    </div>
                </div>
            </main>
            </div>
            </div>

            <div className={cn('flex min-h-0 flex-1 flex-col', view !== 'browse' && 'hidden')}>
                <BrowseView
                    webuiRoot={webuiRoot} proxy={proxy} apiKey={apiKey} apiSource={apiSource}
                    onOpenDownloads={() => setView('downloads')}
                />
            </div>
            <div className={cn('flex min-h-0 flex-1 flex-col', view !== 'downloads' && 'hidden')}>
                <DownloadsView />
            </div>
            <div className={cn('flex min-h-0 flex-1 flex-col', view !== 'reorganize' && 'hidden')}>
                <ReorganizeView />
            </div>

            {settingsOpen && (
                <SettingsDialog
                    proxy={proxy} apiKey={apiKey}
                    onClose={() => setSettingsOpen(false)}
                    onSaved={(p, k) => { setProxy(p); setApiKey(k); }}
                />
            )}
        </div>
    );
}

const SOURCES: Array<[string, string]> = [
    ['com', '官方 civitai.com(需登录看 NSFW)'],
    ['red', '镜像 civitai.red(NSFW 免登录)'],
];

function SettingsDialog(props: {
    proxy: string; apiKey: string;
    onClose: () => void; onSaved: (proxy: string, apiKey: string) => void;
}) {
    const { proxy, apiKey, onClose, onSaved } = props;
    const [keyInput, setKeyInput] = useState(apiKey);
    const [proxyInput, setProxyInput] = useState(proxy);
    const [source, setSource] = useState('com');
    const [show, setShow] = useState(false);
    const [result, setResult] = useState('');
    const [busy, setBusy] = useState('');

    useEffect(() => {
        api()?.get_initial?.().then((s: any) => { if (s?.api_source) setSource(s.api_source); }).catch(() => {});
    }, []);

    const verify = async () => {
        setBusy('verify'); setResult('');
        try {
            const r = await api().check_api_key({ apiKey: keyInput.trim(), proxy: proxyInput.trim() });
            setResult(r?.ok ? `✓ 有效,登录身份 @${r.username}` : `✕ ${r?.error || '验证失败'}`);
        } catch (e: any) { setResult(`✕ ${String(e)}`); }
        finally { setBusy(''); }
    };

    const save = async () => {
        setBusy('save');
        try {
            await api().save_settings({ apiKey: keyInput.trim(), proxy: proxyInput.trim(), api_source: source });
            onSaved(proxyInput.trim(), keyInput.trim());
            onClose();
        } finally { setBusy(''); }
    };

    return (
        <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
            <DialogPopup className="max-w-md">
                <DialogHeader>
                    <DialogTitle>设置 · Civitai 登录</DialogTitle>
                </DialogHeader>
                <div className="flex flex-col gap-4 px-1 pb-1 text-sm">
                    <label className="flex flex-col gap-1.5 text-xs text-muted-foreground">
                        <span>Civitai API Key<span className="ml-2 opacity-70">站点头像 → Account Settings → API Keys</span></span>
                        <div className="flex gap-2">
                            <input
                                className={`${inputCls} h-9 flex-1 text-sm`}
                                type={show ? 'text' : 'password'}
                                value={keyInput}
                                onChange={(e) => setKeyInput(e.target.value)}
                                placeholder="粘贴 API Key"
                            />
                            <Button variant="ghost" size="sm" onClick={() => setShow((v) => !v)}>{show ? '隐藏' : '显示'}</Button>
                        </div>
                    </label>
                    <label className="flex flex-col gap-1.5 text-xs text-muted-foreground">
                        <span>HTTP 代理<span className="ml-2 opacity-70">留空 = 直连(Clash 关闭时务必留空)</span></span>
                        <input className={`${inputCls} h-9 text-sm`} value={proxyInput}
                            onChange={(e) => setProxyInput(e.target.value)} placeholder="http://127.0.0.1:7890" />
                    </label>
                    <label className="flex flex-col gap-1.5 text-xs text-muted-foreground">
                        <span>数据源</span>
                        <Select value={source} onValueChange={(v) => setSource(String(v))}
                            items={SOURCES.map(([v, l]) => ({ label: l, value: v }))}>
                            <SelectTrigger size="sm" className="w-full" aria-label="数据源"><SelectValue /></SelectTrigger>
                            <SelectPopup>
                                {SOURCES.map(([v, l]) => <SelectItem key={v} value={v}>{l}</SelectItem>)}
                            </SelectPopup>
                        </Select>
                    </label>
                    {result && (
                        <div className={`rounded-lg px-2.5 py-1.5 text-xs ${result.startsWith('✓') ? 'bg-emerald-500/10 text-emerald-400' : 'bg-red-500/10 text-red-400'}`}>{result}</div>
                    )}
                    <div className="rounded-lg bg-background px-2.5 py-2 text-xs leading-5 text-muted-foreground">
                        登录后可看到未登录时被拦截的模型与下载;镜像源(NSFW 免登录)不会向第三方站发送你的 Key。Key 只保存在本机 gui_settings.json
                    </div>
                    <div className="flex items-center justify-end gap-1">
                        <Button variant="ghost" size="sm" disabled={!!busy || !keyInput.trim()} onClick={verify}>
                            {busy === 'verify' ? '验证中…' : '验证 Key'}
                        </Button>
                        <span className="mx-1 h-4 w-px bg-border" />
                        <Button size="sm" className="border-success bg-success text-success-foreground hover:bg-success/90"
                            disabled={!!busy} onClick={save}>
                            {busy === 'save' ? '保存中…' : '保存'}
                        </Button>
                    </div>
                </div>
            </DialogPopup>
        </Dialog>
    );
}
