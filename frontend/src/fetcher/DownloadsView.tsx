import { useEffect, useState } from 'react';
import { Ban, Pause, Play, RotateCcw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { api, useFeEvent } from './api';

/* eslint-disable @typescript-eslint/no-explicit-any */

type Task = any;

const STATUS_ZH: Record<string, string> = {
    queued: '排队中', fetching: '获取信息', downloading: '下载中',
    paused: '已暂停', done: '完成', cancelled: '已取消', failed: '失败',
};

export default function DownloadsView() {
    const [state, setState] = useState<any>(null);

    const refresh = () => { api()?.downloads_state?.().then(setState); };
    useEffect(refresh, []);
    // 轮询兜底:事件通道异常时不至于永远看不到新任务
    useEffect(() => {
        const t = window.setInterval(refresh, 2500);
        return () => window.clearInterval(t);
    }, []);

    useFeEvent((e) => {
        if (e.type === 'downloads') setState(e.state);
        else if (e.type === 'download_progress' && e.task) {
            // 本地合并进度,不再桥查询(每次桥调用都是跨进程开销)
            setState((prev: any) => prev && {
                ...prev,
                active: prev.active?.id === e.task.id ? e.task : prev.active,
            });
        }
    });

    const cancel = async (id: string) => { await api().cancel_download(id); refresh(); };
    const pause = async (id: string) => { await api().pause_download(id); refresh(); };
    const resume = async (id: string) => { await api().resume_download(id); refresh(); };
    const retry = async (id: string) => { await api().retry_download(id); refresh(); };

    const TaskRow = ({ t, active }: { t: Task; active?: boolean }) => (
        <div className="rounded-lg border border-border bg-card p-2.5">
            <div className="flex items-center gap-2">
                <span className="min-w-0 flex-1 truncate text-sm font-medium">
                    {t.modelName || '…'} {t.versionName ? `· ${t.versionName}` : ''}
                </span>
                {t.category && <span className="rounded bg-secondary px-1.5 py-0.5 text-xs text-muted-foreground">{t.category}</span>}
                <span className={`text-xs ${t.status === 'failed' ? 'text-red-400' : t.status === 'done' ? 'text-emerald-400' : t.status === 'paused' ? 'text-amber-500' : 'text-muted-foreground'}`}>
                    {STATUS_ZH[t.status] || t.status}
                </span>
                {active && t.status === 'downloading' && (
                    <Button variant="ghost" size="icon-sm" title="暂停" onClick={() => pause(t.id)}><Pause /></Button>
                )}
                {t.status === 'paused' && (
                    <Button variant="ghost" size="icon-sm" title="继续" onClick={() => resume(t.id)}><Play /></Button>
                )}
                {(t.status === 'failed' || t.status === 'cancelled') && (
                    <Button variant="ghost" size="icon-sm" title="重试" onClick={() => retry(t.id)}><RotateCcw /></Button>
                )}
                {(active || t.status === 'queued') && (
                    <Button variant="ghost" size="icon-sm" title="取消" onClick={() => cancel(t.id)}><Ban /></Button>
                )}
            </div>
            <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
                <span className="truncate">{t.targetDir}</span>
                {t.error && <span className="ml-auto shrink-0 text-amber-500">{t.error}</span>}
            </div>
            {active && t.status === 'downloading' && (
                <div className="mt-2 flex items-center gap-2">
                    <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-input">
                        <div className="h-full rounded-full bg-success transition-[width]" style={{ width: `${t.percent || 0}%` }} />
                    </div>
                    <span className="shrink-0 text-xs text-muted-foreground">
                        {t.percent || 0}% · {t.downloadedMB}/{t.totalMB}MB · {t.speed}MB/s
                    </span>
                </div>
            )}
        </div>
    );

    return (
        <div className="min-h-0 flex-1 overflow-y-auto pr-1">
            <div className="flex flex-col gap-4">
                <section className="border-b border-border pb-4">
                    <div className="mb-2 text-xs font-medium text-muted-foreground">进行中</div>
                    {state?.active
                        ? <TaskRow t={state.active} active />
                        : <div className="rounded-lg border border-dashed border-border p-4 text-center text-xs text-muted-foreground">没有进行中的下载</div>}
                </section>
                <section className="border-b border-border pb-4">
                    <div className="mb-2 text-xs font-medium text-muted-foreground">队列({state?.queue?.length ?? 0})</div>
                    <div className="flex flex-col gap-2">
                        {(state?.queue || []).map((t: Task) => <TaskRow key={t.id} t={t} />)}
                        {!state?.queue?.length && <div className="rounded-lg border border-dashed border-border p-3 text-center text-xs text-muted-foreground">队列为空</div>}
                    </div>
                </section>
                <section>
                    <div className="mb-2 text-xs font-medium text-muted-foreground">历史</div>
                    <div className="flex flex-col gap-2">
                        {(state?.done || []).slice().reverse().map((t: Task, i: number) => <TaskRow key={`${t.id}-${i}`} t={t} />)}
                        {!state?.done?.length && <div className="rounded-lg border border-dashed border-border p-3 text-center text-xs text-muted-foreground">暂无记录</div>}
                    </div>
                </section>
            </div>
        </div>
    );
}
