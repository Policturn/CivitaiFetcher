import { useEffect, useState } from 'react';
import { Ban, Loader2, Pause, Play, RotateCcw } from 'lucide-react';
import { api, useFeEvent } from './api';

/* eslint-disable @typescript-eslint/no-explicit-any */

type Task = any;

const STATUS_ZH: Record<string, string> = {
    queued: '排队中', fetching: '获取信息', downloading: '下载中',
    paused: '已暂停', done: '完成', cancelled: '已取消', failed: '失败',
};

function TaskThumb({ url }: { url?: string }) {
    const [path, setPath] = useState('');
    useEffect(() => {
        if (!url || !api()) return;
        api().get_thumbnails({ urls: [url], width: 450, urgent: true }).then((m: any) => {
            if (m && m[url]) setPath(m[url]);
        }).catch(() => {});
    }, [url]);
    if (!url) {
        return <div className="flex size-full items-center justify-center bg-input/40 text-xs text-muted-foreground">无图</div>;
    }
    if (path) {
        return <img src={'/t/' + encodeURIComponent(path.split(/[\/]/).pop() || '')} alt="" className="size-full object-cover" />;
    }
    return (
        <div className="flex size-full items-center justify-center bg-input/40">
            <Loader2 className="size-5 animate-spin text-muted-foreground" />
        </div>
    );
}

function TaskCard(props: { t: Task; active?: boolean; onAction: (a: string, id: string) => void }) {
    const { t, active, onAction } = props;
    const downloading = active && (t.status === 'downloading' || t.status === 'fetching');
    return (
        <div className="group relative overflow-hidden rounded-lg border border-border bg-card text-left transition-shadow hover:shadow-md">
            <div className="relative aspect-[4/5] w-full overflow-hidden bg-input/40">
                <TaskThumb url={t.thumbUrl} />
                <span className={`absolute left-1.5 top-1.5 rounded px-1.5 py-0.5 text-[10px] font-medium ${t.status === 'done' ? 'bg-emerald-500/90 text-emerald-950' : t.status === 'failed' ? 'bg-red-500/85 text-white' : 'bg-black/60 text-white'}`}>
                    {STATUS_ZH[t.status] || t.status}
                </span>
                {t.category && (
                    <span className="absolute right-1.5 top-1.5 rounded bg-black/60 px-1.5 py-0.5 text-[10px] text-white">{t.category}</span>
                )}
                {downloading && (
                    <div className="absolute inset-x-2 bottom-1.5">
                        <div className="h-1.5 overflow-hidden rounded-full bg-black/60">
                            <div className="h-full rounded-full bg-emerald-400 transition-[width] duration-300"
                                style={{ width: `${t.percent || 0}%` }} />
                        </div>
                        <div className="mt-0.5 text-center text-[10px] font-medium text-emerald-300 drop-shadow">
                            {t.status === 'fetching' ? '获取信息…' : `${t.percent || 0}% · ${t.downloadedMB || 0}/${t.totalMB || '?'}MB · ${t.speed || 0}MB/s`}
                        </div>
                    </div>
                )}
                <div className="absolute bottom-1.5 right-1.5 z-10 flex gap-1">
                    {downloading && (
                        <button title="暂停" onClick={() => onAction('pause', t.id)}
                            className="hidden size-7 cursor-pointer items-center justify-center rounded-md bg-black/70 text-white hover:bg-black/90 group-hover:flex"><Pause className="size-3.5" /></button>
                    )}
                    {t.status === 'paused' && (
                        <button title="继续" onClick={() => onAction('resume', t.id)}
                            className="hidden size-7 cursor-pointer items-center justify-center rounded-md bg-emerald-500 text-emerald-950 hover:bg-emerald-400 group-hover:flex"><Play className="size-3.5" /></button>
                    )}
                    {(t.status === 'failed' || t.status === 'cancelled') && (
                        <button title="重试" onClick={() => onAction('retry', t.id)}
                            className="hidden size-7 cursor-pointer items-center justify-center rounded-md bg-emerald-500 text-emerald-950 hover:bg-emerald-400 group-hover:flex"><RotateCcw className="size-3.5" /></button>
                    )}
                    {(active || t.status === 'queued') && (
                        <button title="取消" onClick={() => onAction('cancel', t.id)}
                            className="hidden size-7 cursor-pointer items-center justify-center rounded-md bg-black/70 text-red-400 hover:bg-black/90 group-hover:flex"><Ban className="size-3.5" /></button>
                    )}
                </div>
            </div>
            <div className="p-2.5">
                <div className="truncate text-sm font-medium" title={t.modelName}>{t.modelName || '…'}</div>
                <div className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
                    {t.versionName && <span className="min-w-0 truncate">{t.versionName}</span>}
                    {t.status === 'done' && t.totalMB ? <span className="ml-auto shrink-0">{t.totalMB}MB</span> : null}
                </div>
                {t.error && <div className="mt-1 truncate text-xs text-amber-500" title={t.error}>{t.error}</div>}
            </div>
        </div>
    );
}

export default function DownloadsView() {
    const [state, setState] = useState<any>(null);

    const refresh = () => { api()?.downloads_state?.().then(setState); };
    useEffect(refresh, []);
    useEffect(() => {
        const t = window.setInterval(refresh, 2500);
        return () => window.clearInterval(t);
    }, []);

    useFeEvent((e) => {
        if (e.type === 'downloads') setState(e.state);
        else if (e.type === 'download_progress' && e.task) {
            setState((prev: any) => prev && {
                ...prev,
                active: prev.active?.id === e.task.id ? e.task : prev.active,
            });
        }
    });

    const onAction = async (a: string, id: string) => {
        if (a === 'pause') await api().pause_download(id);
        else if (a === 'resume') await api().resume_download(id);
        else if (a === 'retry') await api().retry_download(id);
        else if (a === 'cancel') await api().cancel_download(id);
        refresh();
    };

    return (
        <div className="min-h-0 flex-1 overflow-y-auto pr-1">
            <div className="flex flex-col gap-4">
                <section className="border-b border-border pb-4">
                    <div className="mb-2 text-xs font-medium text-muted-foreground">
                        {`进行中 / 队列(${((state?.queue || []).length + (state?.active ? 1 : 0))})`}
                    </div>
                    {state?.active || (state?.queue || []).length ? (
                        <div className="grid gap-3 [grid-template-columns:repeat(auto-fill,minmax(230px,1fr))]">
                            {state?.active && <TaskCard key={state.active.id} t={state.active} active onAction={onAction} />}
                            {(state?.queue || []).map((t: Task) => <TaskCard key={t.id} t={t} onAction={onAction} />)}
                        </div>
                    ) : (
                        <div className="rounded-lg border border-dashed border-border p-3 text-center text-xs text-muted-foreground">没有进行中的下载</div>
                    )}
                </section>
                <section>
                    <div className="mb-2 text-xs font-medium text-muted-foreground">历史</div>
                    {(state?.done || []).length ? (
                        <div className="grid gap-3 [grid-template-columns:repeat(auto-fill,minmax(230px,1fr))]">
                            {(state.done || []).slice().reverse().map((t: Task, i: number) => (
                                <TaskCard key={`${t.id}-${i}`} t={t} onAction={onAction} />
                            ))}
                        </div>
                    ) : (
                        <div className="rounded-lg border border-dashed border-border p-3 text-center text-xs text-muted-foreground">暂无记录</div>
                    )}
                </section>
            </div>
        </div>
    );
}
