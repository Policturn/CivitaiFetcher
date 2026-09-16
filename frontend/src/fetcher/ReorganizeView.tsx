import { useEffect, useState } from 'react';
import { FolderOpen, Undo2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { api, inputCls, useFeEvent } from './api';

/* eslint-disable @typescript-eslint/no-explicit-any */

export default function ReorganizeView() {
    const [folder, setFolder] = useState('');
    const [plan, setPlan] = useState<any>(null);
    const [busy, setBusy] = useState('');
    const [result, setResult] = useState<any>(null);
    const [progress, setProgress] = useState<[number, number] | null>(null);
    const [canUndo, setCanUndo] = useState(false);
    const [mapping, setMapping] = useState<Record<string, string> | null>(null);
    const [mappingOpen, setMappingOpen] = useState(false);

    useEffect(() => {
        let cancelled = false;
        const boot = () => {
            const a = api();
            if (!a) { setTimeout(boot, 120); return; }
            a.reorganize_has_undo?.().then((v: boolean) => { if (!cancelled) setCanUndo(v); });
            a.get_category_folders?.().then(setMapping);
            a.get_initial?.().then((s: any) => {
                if (cancelled) return;
                if (s?.demo && s?.demoPlan) {
                    setFolder(s.demoFolder || '');
                    setPlan(s.demoPlan);
                }
            }).catch(() => {});
        };
        boot();
        return () => { cancelled = true; };
    }, []);

    useFeEvent((e) => {
        if (e.type === 'reorg_progress') setProgress([e.done, e.total]);
        else if (e.type === 'reorg_done') { setProgress(null); setResult(e); refreshUndo(); }
        else if (e.type === 'undo_progress') setProgress([e.done, e.total]);
        else if (e.type === 'undo_done') { setProgress(null); setResult(e); refreshUndo(); }
    });

    const refreshUndo = () => { api()?.reorganize_has_undo?.().then(setCanUndo); };

    const browse = async () => {
        const dir = await api().choose_folder();
        if (dir) setFolder(Array.isArray(dir) ? dir[0] : dir);
    };

    const doPreviewPinned = async () => {
        setBusy('preview'); setResult(null); setProgress(null);
        try {
            const res = await api().preview_reorganize_pinned({});
            setPlan(res);
        } finally { setBusy(''); }
    };

    const doPreview = async () => {
        setBusy('preview'); setResult(null); setProgress(null);
        try {
            const res = await api().preview_reorganize({ folder });
            setPlan(res);
        } finally { setBusy(''); }
    };

    const doApply = async () => {
        if (!plan?.planId) return;
        setBusy('apply');
        try { await api().apply_reorganize({ planId: plan.planId }); setPlan(null); }
        finally { setBusy(''); }
    };

    const doUndo = async () => {
        setBusy('undo');
        try { await api().undo_reorganize(); }
        finally { setBusy(''); }
    };

    const saveMapping = async () => {
        if (mapping) { await api().set_category_folders(mapping); setMappingOpen(false); }
    };

    const counts = plan?.counts;

    return (
        <div className="flex min-h-0 flex-1 flex-col gap-3">
            <div className="flex items-center gap-2 border-b border-border pb-2.5">
                <input className={`${inputCls} h-9 flex-1 text-sm`} value={folder}
                    onChange={(e) => setFolder(e.target.value)} placeholder="要整理的模型文件夹,如 H:\sd-webui-aki-v4.4\models\Lora" />
                <Button variant="outline" size="icon" title="浏览" onClick={browse}><FolderOpen /></Button>
                <Button variant="outline" size="sm" disabled={!folder || !!busy} onClick={doPreview}>
                    {busy === 'preview' ? '分析中…' : '预览归类'}
                </Button>
                <Button variant="outline" size="sm" disabled={!canUndo || !!busy} onClick={doUndo}>
                    <Undo2 /> 撤销上次
                </Button>
                <Button variant="ghost" size="sm" onClick={() => setMappingOpen((v) => !v)}>
                    {mappingOpen ? '收起映射表' : '文件夹映射表'}
                </Button>
                <span className="mx-1 h-4 w-px bg-border" />
                <Button variant="outline" size="sm" disabled={!!busy} onClick={doPreviewPinned}>
                    重整专属文件夹
                </Button>
            </div>

            {mappingOpen && mapping && (
                <div className="rounded-lg border border-border bg-card p-3">
                    <div className="mb-2 text-xs text-muted-foreground">Civitai 大类 → 文件夹名(改完点保存)</div>
                    <div className="grid max-h-56 grid-cols-3 gap-2 overflow-y-auto">
                        {Object.entries(mapping).map(([k, v]) => (
                            <label key={k} className="flex items-center gap-1.5 text-xs">
                                <span className="w-24 shrink-0 truncate text-muted-foreground" title={k}>{k}</span>
                                <input className={inputCls} value={v}
                                    onChange={(e) => setMapping((m) => ({ ...m!, [k]: e.target.value }))} />
                            </label>
                        ))}
                    </div>
                    <div className="mt-2 flex justify-end">
                        <Button size="sm" onClick={saveMapping}>保存映射表</Button>
                    </div>
                </div>
            )}

            {progress && (
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-input">
                        <div className="h-full rounded-full bg-success transition-[width]"
                            style={{ width: progress[1] ? `${(progress[0] / progress[1]) * 100}%` : '0%' }} />
                    </div>
                    {progress[0]}/{progress[1]}
                </div>
            )}

            {result && (
                <div className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-300">
                    {result.executed != null && `已移动 ${result.executed} 个模型`}
                    {result.restored != null && `已还原 ${result.restored} 个文件`}
                    {result.skipped ? `,跳过 ${result.skipped}` : ''}
                    {result.errors?.length ? `,错误: ${result.errors.join('; ')}` : ''}
                </div>
            )}

            {plan && !plan.error && (
                <div className="min-h-0 flex-1 overflow-y-auto pr-1">
                    <div className="mb-2 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                        <span>计划移动 <b className="text-foreground">{counts?.moves}</b> 个模型</span>
                        <span>已就位 {counts?.alreadyOk}</span>
                        <span>未命中大类(含未上架) {counts?.noHit}(留在原地)</span>
                        <span>无缓存信息 {counts?.noInfo}(先扫描再整理)</span>
                        <Button size="sm" className="ml-auto border-success bg-success text-success-foreground hover:bg-success/90"
                            disabled={busy === 'apply' || !counts?.moves} onClick={doApply}>
                            {busy === 'apply' ? '移动中…' : `确认移动 ${counts?.moves} 个模型`}
                        </Button>
                    </div>
                    <div className="flex flex-col gap-1.5">
                        {(plan.moves || []).map((m: any, i: number) => (
                            <div key={i} className="rounded-lg border border-border bg-card px-2.5 py-1.5 text-xs">
                                <div className="flex items-center gap-2">
                                    <span className="rounded bg-secondary px-1.5 py-0.5 text-muted-foreground">{m.category}</span>
                                    {m.hit && <span className="text-muted-foreground">命中: {m.hit}</span>}
                                    <span className="min-w-0 flex-1 truncate text-muted-foreground" title={m.src}>{m.src}</span>
                                    <span>→ {m.targetDir}</span>
                                    <span className="text-muted-foreground">({m.fileCount} 文件)</span>
                                </div>
                                {m.fileNames?.length > 0 && (
                                    <div className="mt-0.5 truncate text-muted-foreground/70" title={m.fileNames.join('、')}>
                                        连同搬移: {m.fileNames.join('、')}
                                    </div>
                                )}
                            </div>
                        ))}
                    </div>
                </div>
            )}
            {plan?.error && (
                <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-400">{plan.error}</div>
            )}
            {!plan && (
                <div className="flex min-h-0 flex-1 items-center justify-center text-sm text-muted-foreground">
                    选择文件夹后点「预览归类」,确认清单后才会移动文件(可撤销)
                </div>
            )}
        </div>
    );
}
