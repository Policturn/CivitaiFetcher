import { Fragment, memo, useCallback, useEffect, useRef, useState } from 'react';
import DOMPurify from 'dompurify';
import { ChevronDown, Download, Loader2, Search, SlidersHorizontal } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Select, SelectTrigger, SelectValue, SelectPopup, SelectItem } from '@/components/ui/select';
import { Dialog, DialogPopup, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Switch } from '@/components/ui/switch';
import { cn } from '@/lib/utils';
import { api, inputCls, useFeEvent, BASE_MODELS, SORTS, PERIODS, fmtSize, fileUrl, thumbUrl } from './api';

/* eslint-disable @typescript-eslint/no-explicit-any */

type Card = any;

// 类型下拉(value 对应 API types,'all' = 不筛选;Lora 涵盖 LoCon/LyCORIS)
const TYPE_ITEMS: Array<[string, string]> = [
    ['all', '全部'],
    ['Checkpoint', '底模'],
    ['LORA', 'Lora'],
    ['TextualInversion', 'Embedding'],
    ['Hypernetwork', '超网络'],
    ['VAE', 'VAE'],
    ['Upscaler', '放大器'],
    ['MotionModel', '动作模型'],
    ['Controlnet', 'ControlNet'],
];

// Lora 系的次级分类页签(仿 Civitai LoRA 子导航;value = 官方标签,走 tag= 筛选)
const LORA_SUB_TABS: Array<[string, string]> = [
    ['', '全部'],
    ['character', '角色'],
    ['clothing', '服装'],
    ['concept', '概念'],
    ['style', '画风'],
    ['pose', '姿势'],
    ['celebrity', '名人'],
    ['background', '背景'],
    ['animal', '动物'],
    ['objects', '物品'],
];

export default function BrowseView(props: { webuiRoot: string; proxy: string; apiKey: string; apiSource?: string; defaults?: any; onOpenDownloads: () => void }) {
    const { webuiRoot, proxy, apiKey, apiSource, defaults, onOpenDownloads } = props;
    // 页签与筛选
    const [typeTab, setTypeTab] = useState('all');
    const [query, setQuery] = useState('');
    const [baseModels, setBaseModels] = useState<string[]>([]);
    const [tag, setTag] = useState('');
    const [tagInput, setTagInput] = useState('');
    const [creator, setCreator] = useState('');
    const [creatorInput, setCreatorInput] = useState('');
    const [sort, setSort] = useState('Most Downloaded');
    const [period, setPeriod] = useState('AllTime');
    const [customStart, setCustomStart] = useState('');
    const [customEnd, setCustomEnd] = useState('');
    const [hideNsfw, setHideNsfw] = useState(false);
    // 布局
    const [sidebarOpen, setSidebarOpen] = useState(true);
    const [openSections, setOpenSections] = useState<Record<string, boolean>>({ base: true, nsfw: true });
    // 数据
    const [items, setItems] = useState<Card[]>([]);
    const [cursor, setCursor] = useState<string | undefined>();
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [detail, setDetail] = useState<any>(null);
    const [dlVersion, setDlVersion] = useState<any>(null);
    const [localIdx, setLocalIdx] = useState<{ modelIds: Set<string>; versionIds: Set<string> } | null>(null);
    const [thumbMap, setThumbMap] = useState<Record<string, string>>({});
    const reqSeq = useRef(0);
    const sentinel = useRef<HTMLDivElement>(null);

    // 每页卡片缩略图 → 本地缓存(绕开 WebView2 系统代理)
    useEffect(() => {
        const urls = items
            .map((it) => it?.version?.images?.[0]?.url)
            .filter((u: string | undefined) => !!u && !thumbMap[u]);
        if (!urls.length || !api()) return;
        api().get_thumbnails({ urls, width: 450 }).then((map: Record<string, string>) => {
            if (map && Object.keys(map).length) setThumbMap((prev) => ({ ...prev, ...map }));
        }).catch(() => {});
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [items]);

    // 本地已入库索引(弱化"已下载"卡片用)
    const refreshLocalIdx = useCallback((refresh = false) => {
        api()?.local_library_index?.({ webuiRoot, refresh })
            .then((d: any) => setLocalIdx({ modelIds: new Set<string>(d.modelIds), versionIds: new Set<string>(d.versionIds) }))
            .catch(() => {});
    }, [webuiRoot]);
    useEffect(() => { refreshLocalIdx(); }, [refreshLocalIdx]);
    // 下载完成 → 增量并入索引(不做全库重扫,过载源头之一)
    useFeEvent((e) => {
        if (e.type !== 'downloads' || (e.reason !== 'done' && e.reason !== 'failed')) return;
        const done = [...(e.state?.done || []), e.state?.active].filter(Boolean);
        const add = done.filter((t: any) => t.modelId || t.versionId);
        if (!add.length) return;
        setLocalIdx((prev) => {
            if (!prev) return prev;
            const mIds = new Set(prev.modelIds), vIds = new Set(prev.versionIds);
            for (const t of add) {
                if (t.modelId) mIds.add(String(t.modelId));
                if (t.versionId) vIds.add(String(t.versionId));
            }
            return { modelIds: mIds, versionIds: vIds };
        });
    });
    // 后台补齐的缩略图 → 增量点亮对应卡片
    useFeEvent((e) => { if (e.type === 'thumbs_ready' && e.map) setThumbMap((prev) => ({ ...prev, ...e.map })); });

    const isDownloaded = (it: any) =>
        !!localIdx && (localIdx.modelIds.has(String(it.id)) || localIdx.versionIds.has(String(it.version?.id)));

    // 卡片悬浮:一键下载 —— 用设置的默认位置;版本按当前筛选匹配(基础模型优先)
    const [toast, setToast] = useState('');
    const [toastKind, setToastKind] = useState<'ok' | 'warn'>('ok');
    const toastTimer = useRef<number | null>(null);
    const showToast = (msg: string, kind: 'ok' | 'warn' = 'ok') => {
        setToast(msg);
        setToastKind(kind);
        if (toastTimer.current) window.clearTimeout(toastTimer.current);
        toastTimer.current = window.setTimeout(() => setToast(''), 3200);
    };
    const quickDownload = async (it: any) => {
        if (!api()) return;
        try {
            // 拉详情挑符合当前筛选的版本(底模匹配优先,退回搜索词,再退回最新)
            const d = await api().get_model_detail({ modelId: it.id, apiKey, proxy });
            const vs = d?.versions || [];
            if (!vs.length) { showToast(`${it.name}:无可用版本`, 'warn'); return; }
            const idx = pickDefaultVersion(d, baseModels, query);
            const v = vs[idx];
            const dl = defaults || {};
            const root = dl.dlRoot || (webuiRoot ? `${webuiRoot}\\models` : '');
            await api().enqueue_download({
                modelId: it.id, versionId: v.id,
                targetRoot: root, subfolder: dl.dlSubfolder || '',
                autoCategory: dl.dlAutoCategory !== false, withExtras: dl.dlWithExtras !== false,
                apiKey, proxy,
            });
            showToast(`已入队:${it.name} · ${v.name || '?'}${v.baseModel ? `(${v.baseModel})` : ''}`);
        } catch (e: any) {
            showToast(`下载失败:${String(e?.message || e).slice(0, 80)}`, 'warn');
        }
    };

    const toggleSection = (key: string) => setOpenSections((s) => ({ ...s, [key]: !s[key] }));

    const load = useCallback(async (cur?: string, replace = false) => {
        const seq = ++reqSeq.current;
        setLoading(true);
        setError('');
        try {
            // 时段换算:半年/三个月/自定义 → startDate/endDate(Unix 秒),枚举原样传
            let p: string | undefined = period;
            let startDate: number | undefined;
            let endDate: number | undefined;
            const DAY = 86400;
            if (period === 'HalfYear') { p = undefined; startDate = Math.floor(Date.now() / 1000) - 180 * DAY; }
            else if (period === 'Quarter') { p = undefined; startDate = Math.floor(Date.now() / 1000) - 90 * DAY; }
            else if (period === 'Custom') {
                p = undefined;
                if (customStart) startDate = Math.floor(new Date(customStart + 'T00:00:00').getTime() / 1000);
                if (customEnd) endDate = Math.floor(new Date(customEnd + 'T23:59:59').getTime() / 1000);
            }
            const res = await api().browse_models({
                query,
                // Lora 涵盖 LoCon(两者使用上无差异)
                types: typeTab === 'all' ? [] : typeTab === 'LORA' ? ['LORA', 'LoCon'] : [typeTab],
                baseModels, tag, username: creator,
                sort, period: p, startDate, endDate, limit: 20, cursor: cur, apiKey, proxy,
            });
            if (seq !== reqSeq.current) return;
            setItems((prev) => (replace ? res.items : [...prev, ...res.items]));
            setCursor(res.nextCursor || undefined);
        } catch (e: any) {
            if (seq === reqSeq.current) setError(String(e?.message || e).replace('http_', 'HTTP ').replace(/^(TypeError: )?Cannot read properties of null.*/, '后端未就绪'));
        } finally {
            if (seq === reqSeq.current) setLoading(false);
        }
    }, [query, typeTab, baseModels, tag, creator, sort, period, customStart, customEnd, apiKey, proxy]);

    useEffect(() => {
        const t = setTimeout(() => load(undefined, true), 400);
        return () => clearTimeout(t);
    }, [load]);

    const loadingRef = useRef(false);
    useEffect(() => { loadingRef.current = loading; }, [loading]);
    useEffect(() => {
        const el = sentinel.current;
        if (!el) return;
        const ob = new IntersectionObserver((entries) => {
            if (entries[0].isIntersecting && cursor && !loadingRef.current) {
                loadingRef.current = true;  // 防重入:setState 异步,快速滚动会重复触发
                load(cursor);
            }
        }, { rootMargin: '600px' });
        ob.observe(el);
        return () => ob.disconnect();
    }, [cursor, load]);

    const openDetail = async (id: number) => {
        setDetail({ id, loading: true });
        try {
            const d = await api().get_model_detail({ modelId: id, apiKey, proxy });
            setDetail(d);
        } catch (e: any) {
            setDetail({ id, error: String(e) });
        }
    };

    const Section = ({ id, title, children }: { id: string; title: string; children: any }) => (
        <div className="border-b border-border pb-2">
            <button
                className="flex w-full cursor-pointer items-center justify-between py-1.5 text-xs font-medium text-foreground"
                onClick={() => toggleSection(id)}
            >
                {title}
                <ChevronDown aria-hidden className={`size-3.5 text-muted-foreground transition-transform ${openSections[id] ? '' : '-rotate-90'}`} />
            </button>
            {openSections[id] && <div className="pt-1">{children}</div>}
        </div>
    );

    // 浏览筛选持久化:启动恢复 + 变更防抖保存
    const [initDone, setInitDone] = useState(false);
    useEffect(() => {
        api()?.get_initial?.().then((s: any) => {
            const b = s?.browse || {};
            // 旧配置里的 LyCORIS 独立页签并入 Lora
            if (b.typeTab === 'LoCon') b.typeTab = 'LORA';
            if (b.typeTab !== undefined) setTypeTab(b.typeTab);
            if (Array.isArray(b.baseModels)) setBaseModels(b.baseModels);
            if (b.tag !== undefined) setTag(b.tag);
            if (b.creator) { setCreator(b.creator); setCreatorInput(b.creator); }
            if (b.sort) setSort(b.sort);
            if (b.customStart) setCustomStart(b.customStart);
            if (b.customEnd) setCustomEnd(b.customEnd);
            if (b.period) setPeriod(b.period);
            if (b.hideNsfw !== undefined) setHideNsfw(!!b.hideNsfw);
            if (b.sidebarOpen !== undefined) setSidebarOpen(!!b.sidebarOpen);
        }).finally(() => setInitDone(true));
    }, []);
    useEffect(() => {
        // 变更即存:防抖会被"改完立刻关窗"截胡,桥调用很轻,直接落盘
        if (initDone) {
            api()?.save_browse?.({ typeTab, baseModels, tag, creator, sort, period, customStart, customEnd, hideNsfw, sidebarOpen });
        }
    }, [initDone, typeTab, baseModels, tag, creator, sort, period, customStart, customEnd, hideNsfw, sidebarOpen]);

    const shown = hideNsfw ? items.filter((i) => !i.nsfw) : items;

    return (
        <div className="flex min-h-0 flex-1 flex-col gap-3">
            {/* 工具栏:类型 + 搜索 + 过滤器 + 排序/时段(无描边,竖线分割) */}
            <div className="flex items-center gap-2 border-b border-border pb-2.5">
                <span className="shrink-0 text-xs text-muted-foreground">类型</span>
                <Select
                    value={typeTab}
                    onValueChange={(v) => { const t = String(v); setTypeTab(t); if (t !== 'LORA' && t !== 'LoCon') setTag(''); }}
                    items={TYPE_ITEMS.map(([v, l]) => ({ label: l, value: v }))}
                >
                    <SelectTrigger
                        size="sm"
                        className="w-28 border-transparent bg-transparent shadow-none before:hidden"
                        aria-label="类型"
                    >
                        <SelectValue />
                    </SelectTrigger>
                    <SelectPopup>
                        {TYPE_ITEMS.map(([v, l]) => <SelectItem key={v} value={v}>{l}</SelectItem>)}
                    </SelectPopup>
                </Select>
                <span className="h-5 w-px bg-border" />
                <div className="relative w-64 max-w-full">
                    <Search aria-hidden className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
                    <input
                        className={`${inputCls} h-9 w-full pl-8 text-sm`}
                        placeholder="搜索模型…"
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                    />
                </div>
                <Button
                    variant="ghost"
                    size="sm"
                    className={cn(sidebarOpen && 'text-emerald-400')}
                    onClick={() => setSidebarOpen((v) => !v)}
                >
                    <SlidersHorizontal /> 过滤器
                </Button>
                <div className="ml-auto flex items-center gap-1">
                    <span className="text-xs text-muted-foreground">排序</span>
                    <Select value={sort} onValueChange={(v) => setSort(String(v))}
                        disabled={period === 'HalfYear' || period === 'Quarter' || period === 'Custom'}
                        items={SORTS.map(([v, l]) => ({ label: l, value: v }))}>
                        <SelectTrigger
                            size="sm"
                            title={(period === 'HalfYear' || period === 'Quarter' || period === 'Custom')
                                ? '自定义时段下按发布时间排序' : undefined}
                            className="w-32 border-transparent bg-transparent shadow-none before:hidden"
                            aria-label="排序"
                        >
                            <SelectValue />
                        </SelectTrigger>
                        <SelectPopup>{SORTS.map(([v, l]) => <SelectItem key={v} value={v}>{l}</SelectItem>)}</SelectPopup>
                    </Select>
                    <span className="h-4 w-px bg-border" />
                    <span className="text-xs text-muted-foreground">时段</span>
                    <Select value={period} onValueChange={(v) => setPeriod(String(v))}
                        items={PERIODS.map(([v, l]) => ({ label: l, value: v }))}>
                        <SelectTrigger
                            size="sm"
                            className={cn('border-transparent bg-transparent shadow-none before:hidden',
                                period === 'Custom' ? 'w-24' : 'w-24')}
                            aria-label="时段"
                        >
                            <SelectValue />
                        </SelectTrigger>
                        <SelectPopup>{PERIODS.map(([v, l]) => <SelectItem key={v} value={v}>{l}</SelectItem>)}</SelectPopup>
                    </Select>
                    {period === 'Custom' && (
                        <>
                            <span className="h-4 w-px bg-border" />
                            <input type="date" className="h-7 rounded-md border border-input bg-card px-1.5 text-xs text-foreground"
                                value={customStart} onChange={(e) => setCustomStart(e.target.value)} aria-label="开始日期" />
                            <span className="text-xs text-muted-foreground">至</span>
                            <input type="date" className="h-7 rounded-md border border-input bg-card px-1.5 text-xs text-foreground"
                                value={customEnd} onChange={(e) => setCustomEnd(e.target.value)} aria-label="结束日期" />
                        </>
                    )}
                </div>
            </div>

            {/* Lora 系次级分类:文字导航,竖线分割 */}
            {(typeTab === 'LORA' || typeTab === 'LoCon') && (
                <div className="flex items-center border-b border-border pb-2.5 text-xs text-muted-foreground">
                    {LORA_SUB_TABS.map(([v, label], i) => (
                        <Fragment key={v || 'all'}>
                            {i > 0 && <span className="mx-2.5 h-3 w-px bg-border" />}
                            <button
                                onClick={() => setTag(v)}
                                className={cn(
                                    'cursor-pointer transition-colors hover:text-foreground',
                                    tag === v && 'font-medium text-emerald-400',
                                )}
                            >
                                {label}
                            </button>
                        </Fragment>
                    ))}
                </div>
            )}

            <div className="flex min-h-0 flex-1 gap-3">
                {/* 可折叠过滤侧栏 */}
                {sidebarOpen && (
                    <aside className="w-52 shrink-0 overflow-y-auto border-r border-border pr-3">
                        <Section id="base" title="基础模型">
                            <div className="flex flex-col gap-1.5">
                                {BASE_MODELS.map((b) => (
                                    <label key={b} className="flex cursor-pointer items-center gap-2 text-xs hover:text-foreground">
                                        <Checkbox
                                            className="size-3.5"
                                            checked={baseModels.includes(b)}
                                            onCheckedChange={(c) => setBaseModels((p) => (c ? [...p, b] : p.filter((x) => x !== b)))}
                                        />
                                        {b}
                                    </label>
                                ))}
                            </div>
                        </Section>
                        <Section id="tags" title="标签">
                            <div className="flex items-center gap-1.5">
                                <input
                                    className={inputCls}
                                    placeholder="输入标签回车"
                                    value={tagInput}
                                    onChange={(e) => setTagInput(e.target.value)}
                                    onKeyDown={(e) => { if (e.key === 'Enter') setTag(tagInput.trim()); }}
                                />
                            </div>
                            {tag && <div className="mt-1.5 text-xs text-muted-foreground">已选: {tag}</div>}
                        </Section>
                        <Section id="creator" title="创作者">
                            <input
                                className={inputCls}
                                placeholder="用户名回车"
                                value={creatorInput}
                                onChange={(e) => setCreatorInput(e.target.value)}
                                onKeyDown={(e) => { if (e.key === 'Enter') setCreator(creatorInput.trim()); }}
                            />
                            {creator && (
                                <div className="mt-1.5 flex items-center gap-1.5 text-xs text-muted-foreground">
                                    <span className="min-w-0 truncate">已选: @{creator}</span>
                                    <button
                                        onClick={() => { setCreator(''); setCreatorInput(''); }}
                                        className="ml-auto shrink-0 cursor-pointer text-muted-foreground underline underline-offset-2 hover:text-foreground"
                                    >清除</button>
                                </div>
                            )}
                        </Section>
                        <Section id="nsfw" title="显示">
                            <label className="flex items-center justify-between text-xs">
                                隐藏 NSFW
                                <Switch checked={hideNsfw} onCheckedChange={(c) => setHideNsfw(!!c)} />
                            </label>
                        </Section>
                    </aside>
                )}

                {/* 结果网格 */}
                <div className="min-h-0 flex-1 overflow-y-auto pr-1">
                    {error && (
                        <div className="mb-2 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-400">{error}</div>
                    )}
                    <div className="grid gap-3 [grid-template-columns:repeat(auto-fill,minmax(230px,1fr))]">
                        {shown.map((it: any) => (
                            <ThumbCard
                                key={`${it.id}-${it.version?.id}`}
                                it={it}
                                downloaded={isDownloaded(it)}
                                localPath={it?.version?.images?.[0]?.url ? thumbMap[it.version.images[0].url] : undefined}
                                onOpen={() => openDetail(it.id)}
                                onQuickDownload={() => quickDownload(it)}
                            />
                        ))}
                        {loading && Array.from({ length: 8 }).map((_, i) => (
                            <div key={`sk${i}`} className="overflow-hidden rounded-lg border border-border bg-card">
                                <div className="aspect-[4/5] w-full animate-pulse bg-input/40" />
                                <div className="p-2.5">
                                    <div className="h-4 w-2/3 animate-pulse rounded bg-input/40" />
                                    <div className="mt-1.5 flex items-center justify-between gap-2">
                                        <div className="h-3 w-14 animate-pulse rounded bg-input/40" />
                                        <div className="h-3 w-9 animate-pulse rounded bg-input/40" />
                                    </div>
                                    <div className="mt-1 h-3 w-1/3 animate-pulse rounded bg-input/40" />
                                </div>
                            </div>
                        ))}
                    </div>
                    {!loading && !shown.length && !error && (
                        <div className="flex h-40 items-center justify-center text-sm text-muted-foreground">没有匹配的模型</div>
                    )}
                    <div ref={sentinel} className="h-4" />
                    {cursor && !loading && (
                        <div className="flex justify-center pb-2">
                            <Button variant="outline" size="sm" onClick={() => load(cursor)}>加载更多</Button>
                        </div>
                    )}
                </div>
            </div>

            {/* 详情抽屉(仿 Civitai:版本方块行 + 选中版本内容) */}
            <DetailDialog
                detail={detail}
                baseModels={baseModels}
                query={query}
                versionIds={localIdx?.versionIds}
                apiKey={apiKey}
                proxy={proxy}
                apiSource={apiSource}
                onClose={() => setDetail(null)}
                onDownload={(v) => setDlVersion(v)}
                onBrowseCreator={(name) => {
                    setCreator(name);
                    setCreatorInput(name);
                    setSidebarOpen(true);
                    setDetail(null);
                }}
            />

            {/* 下载确认框 */}
            {dlVersion && (
                <DownloadDialog
                    webuiRoot={webuiRoot}
                    proxy={proxy}
                    apiKey={apiKey}
                    modelId={detail?.id}
                    version={dlVersion}
                    onClose={() => setDlVersion(null)}
                    onQueued={onOpenDownloads}
                />
            )}

            {/* 快捷下载提示 */}
            {toast && (
                <div className={`fixed bottom-6 left-1/2 z-50 -translate-x-1/2 max-w-[80vw] truncate rounded-lg border px-4 py-2 text-xs shadow-lg ${toastKind === 'warn' ? 'border-amber-500/40 bg-amber-500/10 text-amber-400' : 'border-border bg-card text-foreground'}`}>
                    {toast}
                </div>
            )}
        </div>
    );
}

function DownloadDialog(props: {
    webuiRoot: string; proxy: string; apiKey: string;
    modelId: number; version: any;
    onClose: () => void; onQueued: () => void;
}) {
    const { webuiRoot, proxy, apiKey, modelId, version, onClose, onQueued } = props;
    const [targetRoot, setTargetRoot] = useState(webuiRoot ? `${webuiRoot}\\models` : '');
    const [subfolder, setSubfolder] = useState('');
    const [autoCategory, setAutoCategory] = useState(true);
    const [withExtras, setWithExtras] = useState(true);
    const [busy, setBusy] = useState(false);
    const totalKB = (version.files || []).reduce((s: number, f: any) => s + (f.sizeKB || 0), 0);

    const browse = async () => {
        const dir = await api().choose_folder();
        if (dir) setTargetRoot(Array.isArray(dir) ? dir[0] : dir);
    };

    const submit = async () => {
        setBusy(true);
        try {
            await api().enqueue_download({
                modelId, versionId: version.id,
                targetRoot, subfolder, autoCategory, withExtras,
                apiKey, proxy,
            });
            onQueued();
            onClose();
        } finally {
            setBusy(false);
        }
    };

    return (
        <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
            <DialogPopup className="max-w-lg">
                <DialogHeader>
                    <DialogTitle>下载 · {version.name}</DialogTitle>
                </DialogHeader>
                <div className="flex flex-col gap-3 text-sm">
                    <div className="text-xs text-muted-foreground">
                        {(version.files || []).length} 个文件 · 共 {fmtSize(totalKB)}
                        {version.baseModel ? ` · ${version.baseModel}` : ''}
                    </div>
                    <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                        目标根目录
                        <div className="flex gap-2">
                            <input className={`${inputCls} h-9 flex-1 text-sm`} value={targetRoot} onChange={(e) => setTargetRoot(e.target.value)} />
                            <Button variant="outline" size="sm" onClick={browse}>浏览</Button>
                        </div>
                    </label>
                    <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                        子文件夹(可选)
                        <input className={inputCls} value={subfolder} onChange={(e) => setSubfolder(e.target.value)} placeholder="留空则直接放在类型目录下" />
                    </label>
                    <div className="flex flex-col gap-2">
                        <label className="flex items-center justify-between text-sm">
                            按 Civitai 大类自动归类(角色/画风/服装…)
                            <Switch checked={autoCategory} onCheckedChange={(c) => setAutoCategory(!!c)} />
                        </label>
                        <label className="flex items-center justify-between text-sm">
                            连同附带文件(VAE/配置)
                            <Switch checked={withExtras} onCheckedChange={(c) => setWithExtras(!!c)} />
                        </label>
                    </div>
                    <div className="text-xs text-muted-foreground">下载完成后自动写入 .civitai.info / .json / 预览图(与 WebUI 插件同格式)</div>
                    <div className="flex justify-end gap-2">
                        <Button variant="outline" size="sm" onClick={onClose}>取消</Button>
                        <Button
                            size="sm" className="border-success bg-success text-success-foreground hover:bg-success/90"
                            disabled={busy || !targetRoot}
                            onClick={submit}
                        >
                            {busy ? <Loader2 className="size-4 animate-spin" /> : <Download />} 加入下载队列
                        </Button>
                    </div>
                </div>
            </DialogPopup>
        </Dialog>
    );
}


// ---------------------------------------------------------------- 详情(仿 Civitai:版本方块行)

// 默认版本:优先匹配基础模型筛选,其次搜索词命中 baseModel/版本名,否则取最新
function pickDefaultVersion(detail: any, baseModels: string[], query: string): number {
    const vs: any[] = detail?.versions || [];
    if (!vs.length) return 0;
    if (baseModels?.length) {
        const i = vs.findIndex((v) => {
            const bm = (v.baseModel || '').toLowerCase();
            return !!bm && baseModels.some((b) => bm === b.toLowerCase());
        });
        if (i >= 0) return i;
    }
    if (query) {
        const q = query.toLowerCase();
        const i = vs.findIndex((v) =>
            (v.baseModel || '').toLowerCase().includes(q) ||
            (v.name || '').toLowerCase().includes(q));
        if (i >= 0) return i;
    }
    return 0;
}

function DetailDialog(props: {
    detail: any; baseModels: string[]; query: string;
    versionIds?: Set<string>; apiKey: string; proxy: string; apiSource?: string;
    onClose: () => void; onDownload: (v: any) => void; onBrowseCreator: (name: string) => void;
}) {
    const { detail, baseModels, query, versionIds, apiKey, proxy, apiSource, onClose, onDownload, onBrowseCreator } = props;
    const openCreator = () => {
        // 应用内浏览该作者的模型(按作者目录挑模型/批量下载)
        if (detail?.creator) onBrowseCreator(detail.creator);
    };
    const [sel, setSel] = useState(0);
    const [html, setHtml] = useState('');
    const [imgMap, setImgMap] = useState<Record<string, string>>({});
    const loaded = !!detail && !detail.loading && !detail.error;
    const versions: any[] = loaded ? (detail.versions || []) : [];
    const v = versions[sel] || null;

    useEffect(() => {
        setSel(loaded ? pickDefaultVersion(detail, baseModels, query) : 0);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [detail]);

    // 作者描述富文本:净化 + 描述内图片走本地缩略图缓存
    useEffect(() => {
        if (!loaded || !detail.descriptionHtml) { setHtml(''); return; }
        const clean = DOMPurify.sanitize(detail.descriptionHtml, { ADD_ATTR: ['target'] });
        const doc = new DOMParser().parseFromString('<div id="r">' + clean + '</div>', 'text/html');
        const root = doc.getElementById('r')!;
        const imgs = Array.from(root.querySelectorAll('img'));
        const srcs = imgs.map((im) => im.getAttribute('src')).filter(Boolean) as string[];
        const finish = () => setHtml(root.innerHTML);
        if (!srcs.length || !api()) { finish(); return; }
        api().get_thumbnails({ urls: srcs, width: 450 }).then((map: Record<string, string>) => {
            imgs.forEach((im) => {
                const s = im.getAttribute('src') || '';
                if (map[s]) im.setAttribute('src', fileUrl(map[s]));
            });
            finish();
        }).catch(finish);
    }, [detail, loaded]);

    // 选中版本的示例图 → 本地缓存
    useEffect(() => {
        const urls = (v?.images || []).map((i: any) => i.url).filter(Boolean);
        if (!urls.length || !api()) return;
        api().get_thumbnails({ urls, width: 450 }).then((m: Record<string, string>) => {
            setImgMap((prev) => ({ ...prev, ...m }));
        }).catch(() => {});
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [v?.id]);

    return (
        <Dialog open={!!detail} onOpenChange={(open) => { if (!open) onClose(); }}>
            <DialogPopup className="max-w-2xl">
                <DialogHeader>
                    <DialogTitle className="truncate">{detail?.loading ? '加载中…' : detail?.name}</DialogTitle>
                </DialogHeader>
                {detail && (detail.loading || detail.error) && (
                    <div className="flex h-40 items-center justify-center gap-2 text-sm text-muted-foreground">
                        {detail.loading ? <><Loader2 className="size-4 animate-spin" />加载中…</> : String(detail.error)}
                    </div>
                )}
                {loaded && (
                    <div className="flex max-h-[70vh] min-h-0 flex-col gap-4 text-sm">
                        <div className="flex flex-wrap items-center gap-2.5 text-xs text-muted-foreground">
                            <span className="rounded bg-secondary px-1.5 py-0.5">{detail.type}</span>
                            <button
                                onClick={openCreator}
                                title="在浏览器打开作者主页"
                                className="cursor-pointer rounded bg-secondary px-1.5 py-0.5 text-foreground underline decoration-border underline-offset-2 transition-colors hover:text-emerald-400 hover:decoration-emerald-400"
                            >
                                @{detail.creator} ↗
                            </button>
                            <span>{(detail.downloads ?? 0).toLocaleString()} 下载</span>
                            <span>{(detail.thumbsUp ?? 0).toLocaleString()} 赞</span>
                            {(detail.tags || []).slice(0, 6).map((t: string) => (
                                <span key={t} className="rounded bg-secondary px-1.5 py-0.5">{t}</span>
                            ))}
                        </div>
                        {/* 版本方块行 */}
                        <div className="flex gap-2 overflow-x-auto pb-1.5">
                            {versions.map((ver: any, i: number) => (
                                <button
                                    key={ver.id}
                                    onClick={() => setSel(i)}
                                    className={cn(
                                        'shrink-0 cursor-pointer rounded-md border px-2.5 py-1.5 text-left text-xs transition-colors',
                                        i === sel
                                            ? 'border-emerald-500/60 bg-emerald-500/10 text-emerald-400'
                                            : 'border-border bg-card text-muted-foreground hover:text-foreground',
                                    )}
                                >
                                    <div className="max-w-36 truncate font-medium" title={ver.name}>
                                        {versionIds?.has(String(ver.id)) ? '✓ ' : ''}{ver.name || '版本 ' + (i + 1)}
                                    </div>
                                    {ver.baseModel && <div className="mt-0.5 text-[10px] opacity-80">{ver.baseModel}</div>}
                                </button>
                            ))}
                        </div>
                        {/* 选中版本内容 */}
                        {v && (
                            <div className="flex min-h-0 flex-col gap-3 overflow-y-auto pr-1.5">
                                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                                    {v.baseModel && <span className="rounded bg-secondary px-1.5 py-0.5">{v.baseModel}</span>}
                                    {v.createdAt && <span>{v.createdAt}</span>}
                                    {v.trainedWords?.length > 0 && <span className="min-w-0 truncate">触发词: {v.trainedWords.join(', ')}</span>}
                                </div>
                                {v.images?.length > 0 && (
                                    <div className="flex gap-2.5 overflow-x-auto pb-1">
                                        {v.images.map((im: any) => (
                                            <img
                                                key={im.url}
                                                src={imgMap[im.url] ? fileUrl(imgMap[im.url]) : im.url}
                                                alt="" loading="lazy"
                                                className="h-28 shrink-0 rounded-md border border-border object-cover"
                                            />
                                        ))}
                                    </div>
                                )}
                                <div className="flex flex-col gap-1.5">
                                    {(v.files || []).map((f: any) => (
                                        <div key={f.name} className="flex items-center gap-2 rounded-md border border-border bg-card px-2 py-1 text-xs">
                                            <span className="rounded bg-secondary px-1.5 py-0.5 text-muted-foreground">{f.type}</span>
                                            <span className="min-w-0 flex-1 truncate">{f.name}</span>
                                            <span className="shrink-0 text-muted-foreground">{fmtSize(f.sizeKB)}</span>
                                        </div>
                                    ))}
                                </div>
                                {html ? (
                                    <div
                                        className="rich-desc max-h-40 overflow-y-auto rounded-lg bg-background p-2 text-xs leading-5 text-muted-foreground [&_a]:text-emerald-400 [&_a]:underline [&_img]:max-h-64 [&_img]:rounded [&_img]:object-contain [&_ol]:list-decimal [&_ol]:pl-4 [&_p]:my-1 [&_ul]:list-disc [&_ul]:pl-4"
                                        dangerouslySetInnerHTML={{ __html: html }}
                                    />
                                ) : null}
                                <div className="flex justify-end">
                                    <Button
                                        size="sm"
                                        className="border-success bg-success text-success-foreground hover:bg-success/90"
                                        onClick={() => onDownload(v)}
                                    >
                                        <Download /> 下载此版本
                                    </Button>
                                </div>
                            </div>
                        )}
                    </div>
                )}
            </DialogPopup>
        </Dialog>
    );
}

// ---------------------------------------------------------------- 卡片(memo:增量更新不重渲整列表)

const ThumbCard = memo(function ThumbCard(props: {
    it: any; downloaded: boolean; localPath?: string;
    onOpen: () => void; onQuickDownload: () => void;
}) {
    const { it, downloaded, localPath, onOpen, onQuickDownload } = props;
    const firstUrl = it?.version?.images?.[0]?.url as string | undefined;
    const src = localPath ? fileUrl(localPath) : '';
    const onImgError = (e: any) => {
        // 本地读取失败 → 回退 450 小图(绝不直连原图,几 MB 的解码会压垮机器)
        const img = e.currentTarget as HTMLImageElement;
        if (!img.dataset.fallback && firstUrl) {
            img.dataset.fallback = '1';
            img.src = thumbUrl(firstUrl);
        }
    };
    return (
        <div
            role="button"
            tabIndex={0}
            onClick={onOpen}
            onKeyDown={(e) => { if (e.key === 'Enter') onOpen(); }}
            className={`group relative cursor-pointer overflow-hidden rounded-lg border border-border bg-card text-left outline-none transition-opacity focus-visible:ring-2 focus-visible:ring-ring ${downloaded ? 'opacity-55 hover:opacity-100' : ''}`}
        >
            <div className="relative aspect-[4/5] w-full overflow-hidden bg-input/40">
                {src ? (
                    <img src={src} alt="" loading="lazy" decoding="async" className="size-full object-cover" onError={onImgError} />
                ) : firstUrl ? (
                    <div className="flex size-full items-center justify-center bg-card">
                        <Loader2 className="size-4 animate-spin text-muted-foreground" />
                    </div>
                ) : (
                    <div className="flex size-full items-center justify-center text-xs text-muted-foreground">无预览图</div>
                )}
                {downloaded && (
                    <span className="absolute left-1.5 top-1.5 rounded bg-emerald-500/90 px-1.5 py-0.5 text-[10px] font-medium text-emerald-950">已下载</span>
                )}
                {it.nsfw && (
                    <span className="absolute right-1.5 top-1.5 rounded bg-red-500/85 px-1.5 py-0.5 text-[10px] font-medium text-white">NSFW</span>
                )}
                <button
                    title="下载此版本"
                    onClick={(e) => { e.stopPropagation(); onQuickDownload(); }}
                    className="absolute bottom-1.5 right-1.5 z-10 hidden size-8 cursor-pointer items-center justify-center rounded-md bg-emerald-500 text-emerald-950 shadow-md transition-colors hover:bg-emerald-400 group-hover:flex"
                >
                    <Download className="size-4" />
                </button>
            </div>
            <div className="p-2.5">
                <div className="truncate text-sm font-medium" title={it.name}>{it.name}</div>
                <div className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
                    {it.version?.baseModel && (
                        <span className="rounded bg-secondary px-1.5 py-0.5">{it.version.baseModel}</span>
                    )}
                    <span className="ml-auto">{(it.downloads ?? 0).toLocaleString()}↓</span>
                </div>
                <div className="mt-1 truncate text-xs text-muted-foreground">@{it.creator}</div>
            </div>
        </div>
    );
});
