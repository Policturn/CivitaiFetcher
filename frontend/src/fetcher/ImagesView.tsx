import { Fragment, useCallback, useEffect, useRef, useState } from 'react';
import { Copy, ExternalLink, Loader2, Search, SlidersHorizontal } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Select, SelectTrigger, SelectValue, SelectPopup, SelectItem } from '@/components/ui/select';
import { Dialog, DialogPopup, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { api, cn } from './api';

/* eslint-disable @typescript-eslint/no-explicit-any */

const IMG_SORTS: Array<[string, string]> = [
    ['Most Reactions', '最多反应'], ['Newest', '最新'], ['Most Discussed', '最多评论'],
];
const IMG_PERIODS: Array<[string, string]> = [
    ['AllTime', '全部时间'], ['Year', '今年'], ['Month', '本月'], ['Week', '本周'], ['Day', '今天'],
];

type Img = any;

/** CDN url 改写成指定宽度小图 */
function cdn(url: string, width: number) {
    if (!url) return url;
    return url.replace('original=true', 'width=' + width).replace(/width=\d+/, 'width=' + width);
}

export default function ImagesView(props: { proxy: string; apiKey: string; onOpenModel: (modelId: number) => void }) {
    const { proxy, apiKey, onOpenModel } = props;
    const [sort, setSort] = useState('Most Reactions');
    const [period, setPeriod] = useState('Month');
    const [hideNsfw, setHideNsfw] = useState(false);
    const [items, setItems] = useState<Img[]>([]);
    const [cursor, setCursor] = useState<string | undefined>();
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [detail, setDetail] = useState<Img | null>(null);
    const [relModel, setRelModel] = useState<any>(null);
    const [genMeta, setGenMeta] = useState<any>(null);
    const [metaBusy, setMetaBusy] = useState(false);
    const [copied, setCopied] = useState('');
    const reqSeq = useRef(0);
    const loadedOnce = useRef(false);
    const sentinel = useRef<HTMLDivElement>(null);

    const load = useCallback(async (cur?: string, replace = false) => {
        if (!api()) { setTimeout(() => load(cur, replace), 300); return; }
        const seq = ++reqSeq.current;
        setLoading(true);
        setError('');
        // 瞬时网络错误自动重试(最多2次,间隔1.5s),重试仍失败才显示红条
        for (let attempt = 0; attempt < 3; attempt++) {
            try {
                const res = await api().search_images({ sort, period, limit: 60, cursor: cur, apiKey, proxy });
                if (seq !== reqSeq.current) return;
                setItems((prev) => (replace ? res.items : [...prev, ...res.items]));
                setCursor(res.nextCursor || undefined);
                loadedOnce.current = true;
                setLoading(false);
                setError('');
                return;
            } catch (e: any) {
                if (seq !== reqSeq.current) return;
                if (attempt < 2) { await new Promise((r) => setTimeout(r, 1500)); continue; }
                setError(String(e?.message || e).replace('http_', 'HTTP '));
            }
        }
        setLoading(false);
    }, [sort, period, apiKey, proxy]);

    useEffect(() => {
        const t = setTimeout(() => load(undefined, true), 400);
        return () => clearTimeout(t);
    }, [load]);

    useEffect(() => {
        const el = sentinel.current;
        if (!el) return;
        const ob = new IntersectionObserver((entries) => {
            if (entries[0].isIntersecting && cursor && !loading) load(cursor);
        }, { rootMargin: '800px' });
        ob.observe(el);
        return () => ob.disconnect();
    }, [cursor, loading, load]);

    const readMeta = async (it: Img) => {
        setMetaBusy(true);
        setGenMeta(null);
        try {
            const m = await api().read_image_meta({ url: it.url, apiKey, proxy });
            setGenMeta(m);
        } catch (e: any) {
            setGenMeta({ ok: false, error: String(e?.message || e) });
        } finally { setMetaBusy(false); }
    };

    const openDetail = async (it: Img) => {
        setDetail(it);
        setRelModel(null);
        setGenMeta(null);
        setCopied('');
        readMeta(it);
        // 关联模型(触发词参考)
        const vids = it.modelVersionIds || [];
        if (vids.length && api()) {
            try {
                const v = await api().get_version_info?.({ versionId: vids[0], apiKey, proxy });
                if (v?.modelId) {
                    const m = await api().get_model_detail({ modelId: v.modelId, apiKey, proxy });
                    setRelModel({ ...m, versionId: vids[0] });
                }
            } catch { /* 无关联或失败,静默 */ }
        }
    };

    const copy = async (text: string, tag: string) => {
        try {
            await navigator.clipboard.writeText(text);
            setCopied(tag);
            setTimeout(() => setCopied(''), 1500);
        } catch { /* http://127.0.0.1 是安全上下文,一般不会失败 */ }
    };

    const shown = hideNsfw ? items.filter((i) => (i.nsfwLevel || 0) <= 1) : items;

    return (
        <div className="flex min-h-0 flex-1 flex-col gap-3">
            {/* 工具栏 */}
            <div className="flex items-center gap-2 border-b border-border pb-2.5">
                <span className="text-xs text-muted-foreground">图片参考</span>
                <span className="h-4 w-px bg-border" />
                <span className="text-xs text-muted-foreground">排序</span>
                <Select value={sort} onValueChange={(v) => setSort(String(v))}
                    items={IMG_SORTS.map(([v, l]) => ({ label: l, value: v }))}>
                    <SelectTrigger size="sm" className="w-28 border-transparent bg-transparent shadow-none before:hidden" aria-label="排序"><SelectValue /></SelectTrigger>
                    <SelectPopup>{IMG_SORTS.map(([v, l]) => <SelectItem key={v} value={v}>{l}</SelectItem>)}</SelectPopup>
                </Select>
                <span className="h-4 w-px bg-border" />
                <span className="text-xs text-muted-foreground">时段</span>
                <Select value={period} onValueChange={(v) => setPeriod(String(v))}
                    items={IMG_PERIODS.map(([v, l]) => ({ label: l, value: v }))}>
                    <SelectTrigger size="sm" className="w-24 border-transparent bg-transparent shadow-none before:hidden" aria-label="时段"><SelectValue /></SelectTrigger>
                    <SelectPopup>{IMG_PERIODS.map(([v, l]) => <SelectItem key={v} value={v}>{l}</SelectItem>)}</SelectPopup>
                </Select>
                <label className="ml-auto flex cursor-pointer items-center gap-1.5 text-xs text-muted-foreground">
                    <Checkbox checked={hideNsfw} onCheckedChange={(c) => setHideNsfw(!!c)} />
                    隐藏 NSFW
                </label>
            </div>
            <div className="text-xs leading-4 text-muted-foreground/70">
                Civitai 已从公开接口移除图片生成参数(prompt),点击图片可看关联模型的触发词作参考
            </div>

            {error && (
                <div className="flex items-center gap-2 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-400">
                    <span className="min-w-0 flex-1 truncate">{error}</span>
                    <Button variant="ghost" size="sm" onClick={() => load(undefined, true)}>重试</Button>
                </div>
            )}

            {/* 瀑布流 */}
            <div className="min-h-0 flex-1 overflow-y-auto pr-1">
                <div className="columns-2 gap-3 xl:columns-3 2xl:columns-4 [&>*]:mb-3">
                    {shown.map((it) => (
                        <button key={it.id} onClick={() => openDetail(it)}
                            className="group block w-full cursor-pointer overflow-hidden rounded-lg border border-border bg-card outline-none transition-shadow hover:shadow-md focus-visible:ring-2 focus-visible:ring-ring">
                            <div className="relative">
                                <img src={cdn(it.url, 550)} alt="" loading="lazy" className="w-full" style={{ aspectRatio: `${it.width}/${it.height}` }} />
                                {(it.nsfwLevel || 0) > 1 && (
                                    <span className="absolute right-1.5 top-1.5 rounded bg-red-500/85 px-1.5 py-0.5 text-[10px] font-medium text-white">NSFW</span>
                                )}
                                <div className="absolute inset-x-0 bottom-0 flex items-center gap-2 bg-gradient-to-t from-black/70 to-transparent px-2 pb-1.5 pt-6 text-[10px] text-white/90 opacity-0 transition-opacity group-hover:opacity-100">
                                    {it.reactions > 0 && <span>♥ {it.reactions}</span>}
                                    {it.baseModel && <span className="truncate">{it.baseModel}</span>}
                                    <span className="ml-auto truncate">@{it.username}</span>
                                </div>
                            </div>
                        </button>
                    ))}
                </div>
                {!loading && !shown.length && !error && (
                    <div className="flex h-40 items-center justify-center text-sm text-muted-foreground">没有图片</div>
                )}
                <div ref={sentinel} className="h-4" />
                {loading && (
                    <div className="flex justify-center py-4"><Loader2 className="size-5 animate-spin text-muted-foreground" /></div>
                )}
            </div>

            {/* 图片详情 */}
            <Dialog open={!!detail} onOpenChange={(open) => { if (!open) setDetail(null); }}>
                <DialogPopup className="max-w-3xl">
                    <DialogHeader>
                        <DialogTitle className="truncate">图片详情{detail?.username ? ` · @${detail.username}` : ''}</DialogTitle>
                    </DialogHeader>
                    {detail && (
                        <div className="flex max-h-[75vh] min-h-0 flex-col gap-3 overflow-y-auto pr-1">
                            <img src={cdn(detail.url, 1080)} alt="" className="max-h-[46vh] w-full rounded-lg border border-border object-contain" />
                            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
                                <span>{detail.width} × {detail.height}</span>
                                {detail.baseModel && <span className="rounded bg-secondary px-1.5 py-0.5">{detail.baseModel}</span>}
                                {detail.createdAt && <span>{detail.createdAt}</span>}
                                {detail.reactions > 0 && <span>♥ {detail.reactions}</span>}
                            </div>
                            {genMeta?.ok && genMeta.engine === 'a1111' && (
                                <div className="rounded-lg border border-border bg-card p-3">
                                    <div className="mb-1.5 flex items-center gap-2 text-xs font-medium text-muted-foreground">
                                        生成参数(WebUI 内嵌)
                                        <Button variant="ghost" size="sm" className="ml-auto"
                                            onClick={() => copy(genMeta.prompt, 'allprompt')}>
                                            {copied === 'allprompt' ? '✓ 已复制' : '复制全部正向'}
                                        </Button>
                                    </div>
                                    <div className="flex flex-wrap gap-1.5">
                                        {genMeta.prompt.split(',').map((w: string, i: number) => {
                                            const t = w.trim();
                                            if (!t) return null;
                                            return (
                                                <button key={i} onClick={() => copy(t, 'p' + i)}
                                                    className="cursor-pointer rounded-md bg-secondary px-2 py-0.5 text-xs text-foreground transition-colors hover:bg-accent"
                                                    title="点击复制">
                                                    {copied === 'p' + i ? '✓' : t}
                                                </button>
                                            );
                                        })}
                                    </div>
                                    {genMeta.negative && (
                                        <details className="mt-2">
                                            <summary className="cursor-pointer text-xs text-muted-foreground">负向提示词({genMeta.negative.split(',').length} 项)</summary>
                                            <div className="mt-1.5 flex flex-wrap gap-1.5">
                                                {genMeta.negative.split(',').map((w: string, i: number) => {
                                                    const t = w.trim();
                                                    if (!t) return null;
                                                    return (
                                                        <button key={i} onClick={() => copy(t, 'n' + i)}
                                                            className="cursor-pointer rounded-md bg-input/40 px-2 py-0.5 text-xs text-muted-foreground hover:bg-accent">
                                                            {copied === 'n' + i ? '✓' : t}
                                                        </button>
                                                    );
                                                })}
                                            </div>
                                        </details>
                                    )}
                                    {Object.keys(genMeta.params || {}).length > 0 && (
                                        <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 border-t border-border pt-2 text-xs text-muted-foreground">
                                            {['Model', 'Steps', 'Sampler', 'CFG scale', 'Seed', 'Size', 'Clip skip', 'Model hash'].map((k) =>
                                                genMeta.params[k] ? <span key={k}>{k}: <span className="text-foreground">{String(genMeta.params[k]).slice(0, 24)}</span></span> : null)}
                                        </div>
                                    )}
                                </div>
                            )}
                            {metaBusy && (
                                <div className="flex items-center gap-2 text-xs text-muted-foreground"><Loader2 className="size-3.5 animate-spin" />正在读取图片内嵌参数…</div>
                            )}
                            {genMeta && !genMeta.ok && !metaBusy && (
                                <div className="text-xs text-muted-foreground/70">无内嵌生成参数({String(genMeta.error || '').slice(0, 40)})</div>
                            )}
                            {relModel ? (
                                <div className="rounded-lg border border-border bg-card p-3">
                                    <div className="mb-1.5 text-xs font-medium text-muted-foreground">关联模型(tag 参考)</div>
                                    <div className="flex items-center gap-2">
                                        <span className="min-w-0 flex-1 truncate text-sm font-medium">{relModel.name}</span>
                                        <Button variant="outline" size="sm" onClick={() => { onOpenModel(relModel.id); setDetail(null); }}>
                                            到浏览页
                                        </Button>
                                    </div>
                                    {(relModel.versions?.[0]?.trainedWords || []).length > 0 && (
                                        <div className="mt-2 flex flex-wrap gap-1.5">
                                            {(relModel.versions[0].trainedWords || []).map((w: string, i: number) => (
                                                <button key={i} onClick={() => copy(w, 'tw' + i)}
                                                    className="cursor-pointer rounded-md bg-secondary px-2 py-0.5 text-xs text-foreground transition-colors hover:bg-accent"
                                                    title="点击复制">
                                                    {copied === 'tw' + i ? '✓ 已复制' : w}
                                                </button>
                                            ))}
                                        </div>
                                    )}
                                </div>
                            ) : (detail.modelVersionIds || []).length > 0 ? (
                                <div className="flex items-center gap-2 text-xs text-muted-foreground"><Loader2 className="size-3.5 animate-spin" />正在获取关联模型…</div>
                            ) : null}
                            <div className="flex justify-end gap-2">
                                <Button variant="ghost" size="sm" onClick={() => copy(cdn(detail.url, 3840), 'img')}>
                                    {copied === 'img' ? '✓ 已复制' : <><Copy /> 复制原图链接</>}
                                </Button>
                                <Button variant="ghost" size="sm" onClick={() => api()?.open_url?.(`https://civitai.com/images/${detail.id}`)}>
                                    <ExternalLink /> 在浏览器打开(网页内可看生成参数)
                                </Button>
                            </div>
                        </div>
                    )}
                </DialogPopup>
            </Dialog>
        </div>
    );
}
