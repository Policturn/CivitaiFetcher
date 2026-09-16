import { useEffect, useState } from 'react';
import { FolderOpen } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { Select, SelectTrigger, SelectValue, SelectPopup, SelectItem } from '@/components/ui/select';
import { api, inputCls } from './api';

const SOURCES: Array<[string, string]> = [
    ['com', '官方 civitai.com(需登录看 NSFW)'],
    ['red', '镜像 civitai.red(NSFW 免登录)'],
];

export type DownloadDefaults = {
    dlRoot: string;
    dlSubfolder: string;
    dlAutoCategory: boolean;
    dlWithExtras: boolean;
};

export default function SettingsView(props: {
    proxy: string; apiKey: string; apiSource: string;
    webuiRoot: string;
    defaults: DownloadDefaults;
    onSaved: (patch: { proxy?: string; apiKey?: string; apiSource?: string; defaults?: DownloadDefaults }) => void;
}) {
    const { proxy, apiKey, apiSource, webuiRoot, defaults, onSaved } = props;
    const [keyInput, setKeyInput] = useState(apiKey);
    const [proxyInput, setProxyInput] = useState(proxy);
    const [source, setSource] = useState(apiSource || 'com');
    const [dl, setDl] = useState<DownloadDefaults>(
        () => ({ dlRoot: '', dlSubfolder: '', dlAutoCategory: true, dlWithExtras: true, ...defaults }),
    );
    const [show, setShow] = useState(false);
    const [result, setResult] = useState('');
    const [busy, setBusy] = useState('');

    useEffect(() => { setKeyInput(apiKey); setProxyInput(proxy); }, [apiKey, proxy]);
    useEffect(() => {
        setDl((d) => (d.dlRoot ? d : { ...d, dlRoot: webuiRoot ? `${webuiRoot}\\models` : '' }));
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [webuiRoot]);

    const browse = async () => {
        const dir = await api().choose_folder();
        if (dir) setDl((d) => ({ ...d, dlRoot: Array.isArray(dir) ? dir[0] : dir }));
    };

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
            const nextDefaults = { ...dl, dlRoot: dl.dlRoot || (webuiRoot ? `${webuiRoot}\\models` : '') };
            await api().save_settings({ apiKey: keyInput.trim(), proxy: proxyInput.trim(), api_source: source });
            await api().save_settings({ defaults: nextDefaults });
            onSaved({ proxy: proxyInput.trim(), apiKey: keyInput.trim(), apiSource: source, defaults: nextDefaults });
            setResult('✓ 已保存');
        } finally { setBusy(''); }
    };

    const Row = ({ title, hint, children }: { title: string; hint?: string; children: any }) => (
        <div className="flex items-center gap-4 border-b border-border py-4">
            <div className="w-52 shrink-0">
                <div className="text-sm">{title}</div>
                {hint && <div className="mt-0.5 text-xs text-muted-foreground">{hint}</div>}
            </div>
            <div className="flex min-w-0 flex-1 items-center justify-end gap-2">{children}</div>
        </div>
    );

    return (
        <div className="min-h-0 flex-1 overflow-y-auto">
            <div className="mx-auto max-w-3xl">
                <div className="py-4 text-lg font-bold">设置</div>

                <div className="mb-2 text-xs font-medium text-muted-foreground">Civitai 账号</div>
                <Row title="Civitai API Key" hint="站点头像 → Account Settings → API Keys;登录后可看被拦的模型与下载">
                    <input
                        className={`${inputCls} h-9 w-72 text-sm`}
                        type={show ? 'text' : 'password'}
                        value={keyInput}
                        onChange={(e) => setKeyInput(e.target.value)}
                        placeholder="粘贴 API Key"
                    />
                    <Button variant="ghost" size="sm" onClick={() => setShow((v) => !v)}>{show ? '隐藏' : '显示'}</Button>
                    <span className="h-4 w-px bg-border" />
                    <Button variant="ghost" size="sm" disabled={!!busy || !keyInput.trim()} onClick={verify}>
                        {busy === 'verify' ? '验证中…' : '验证 Key'}
                    </Button>
                </Row>
                <Row title="HTTP 代理" hint="留空 = 直连(Clash 关闭时务必留空)">
                    <input className={`${inputCls} h-9 w-72 text-sm`} value={proxyInput}
                        onChange={(e) => setProxyInput(e.target.value)} placeholder="http://127.0.0.1:7890" />
                </Row>
                <Row title="数据源" hint="镜像源不会发送你的 API Key">
                    <Select value={source} onValueChange={(v) => setSource(String(v))}
                        items={SOURCES.map(([v, l]) => ({ label: l, value: v }))}>
                        <SelectTrigger size="sm" className="w-72" aria-label="数据源"><SelectValue /></SelectTrigger>
                        <SelectPopup>
                            {SOURCES.map(([v, l]) => <SelectItem key={v} value={v}>{l}</SelectItem>)}
                        </SelectPopup>
                    </Select>
                </Row>

                <div className="mb-2 mt-8 text-xs font-medium text-muted-foreground">默认下载(悬浮按钮一键下载时使用)</div>
                <Row title="目标根目录" hint="直接点卡片下载按钮时的落盘位置">
                    <input className={`${inputCls} h-9 min-w-0 flex-1 text-sm`} value={dl.dlRoot}
                        onChange={(e) => setDl((d) => ({ ...d, dlRoot: e.target.value }))}
                        placeholder="SD WebUI 的 models 目录" />
                    <Button variant="ghost" size="icon" title="浏览" onClick={browse}><FolderOpen /></Button>
                </Row>
                <Row title="子文件夹" hint="留空则放在类型目录下">
                    <input className={`${inputCls} h-9 w-72 text-sm`} value={dl.dlSubfolder}
                        onChange={(e) => setDl((d) => ({ ...d, dlSubfolder: e.target.value }))} placeholder="可选" />
                </Row>
                <Row title="按 Civitai 大类自动归类" hint="下载到 角色 / 画风 / 服装 … 子目录">
                    <Switch checked={dl.dlAutoCategory} onCheckedChange={(c) => setDl((d) => ({ ...d, dlAutoCategory: !!c }))} />
                </Row>
                <Row title="连同附带文件" hint="VAE / 配置等附属文件一起下载">
                    <Switch checked={dl.dlWithExtras} onCheckedChange={(c) => setDl((d) => ({ ...d, dlWithExtras: !!c }))} />
                </Row>

                {result && (
                    <div className={`mt-6 rounded-lg px-3 py-2 text-xs ${result.startsWith('✓') ? 'bg-emerald-500/10 text-emerald-400' : 'bg-red-500/10 text-red-400'}`}>{result}</div>
                )}
                <div className="sticky bottom-0 flex items-center justify-end gap-1 bg-background py-4">
                    <Button size="sm" className="border-success bg-success text-success-foreground hover:bg-success/90"
                        disabled={!!busy} onClick={save}>
                        {busy === 'save' ? '保存中…' : '保存设置'}
                    </Button>
                </div>
            </div>
        </div>
    );
}
