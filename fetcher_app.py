#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Civitai Fetcher 桌面壳 — pywebview 承载 COSS 前端(ui-dist),复用 civitai_fetcher 全部扫描逻辑。
开发:python fetcher_app.py
打包:PyInstaller --onefile --add-data ui-dist(见 README)

注意:pywebview 注入 JS 时会 dir()+getattr() 深遍历 js_api 对象来枚举可调用方法,
因此 Api 类**只能有纯方法**,任何状态(window/thread/queue)挂在实例属性上都会把
遍历拖进 WinForms/.NET 对象图造成递归与死锁。状态统一放模块级 _STATE。
"""

import json
import os
import queue
import sys
import threading
import time
from types import SimpleNamespace

import webview

import civitai_fetcher as cf
import civitai_browser as cb
import civitai_downloader as cd
import civitai_reorganize as cr
import civitai_demo as demo


def _civitai_reachable(timeout=4):
    """探测 Civitai API(直连,忽略系统代理);返回 bool"""
    import requests
    s = requests.Session()
    s.trust_env = False
    s.proxies = {"http": None, "https": None}
    try:
        r = s.get("https://civitai.com/api/v1/models?limit=1", timeout=timeout)
        return r.ok
    except Exception:
        return False


# 演示模式:CF_DEMO=1 强制;否则 = 演示资产存在且站点不可达
DEMO_MODE = (os.environ.get("CF_DEMO") == "1"
             or (demo.is_available() and not _civitai_reachable()))


def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def res_dir():
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def settings_path():
    return os.path.join(app_dir(), "gui_settings.json")


# 冻结环境下把哈希缓存/扫描报告重定向到 exe 旁边(默认落在 _MEIPASS 临时目录会丢)
cf.HASH_CACHE_FILE = os.path.join(app_dir(), "hash_cache.json")
cd._load_history_once()  # 路径定向后立即装载下载历史

EVENT_LOG_TAGS = [
    ("没有此模型", "amber"), ("骨架", "amber"),
    ("HTTP", "red"), ("网络异常", "red"), ("请求失败", "red"),
    ("异常", "red"), ("API Key", "red"),
    ("汇总", "green"), ("耗时", "green"), ("◆", "bold"),
]

# 模块级状态(Api 之外,避免被 pywebview 枚举)
_STATE = {
    "window": None,
    "worker": None,
    "stop": False,
    "events": queue.Queue(),
}


def classify(line):
    if line.startswith(("[", "    ")) and "SHA256" not in line:
        return "muted"
    for kw, tag in EVENT_LOG_TAGS:
        if kw in line:
            return tag
    return None


def _push(detail):
    _STATE["events"].put(detail)


def _emit_log(msg):
    line = str(msg).replace("\n", "")
    _push({"type": "log", "line": line, "tag": classify(line)})


def _emit_progress(done, total):
    _push({"type": "progress", "done": done, "total": total})


def _pump():
    """把事件队列刷给前端(pywebview 的 evaluate_js 任意线程可调),攒批减少跨界次数"""
    while _STATE["window"] is None:
        time.sleep(0.1)
    while True:
        batch = [_STATE["events"].get()]
        while True:
            try:
                batch.append(_STATE["events"].get_nowait())
            except queue.Empty:
                break
        window = _STATE["window"]
        payload = json.dumps(batch, ensure_ascii=False)
        for attempt in range(3):
            try:
                window.evaluate_js(f"window.__fePushBatch({payload})")
                break
            except Exception:
                time.sleep(0.3)  # 重试,不丢批


class Api:
    """只含纯方法——pywebview 会遍历本对象枚举 API"""

    def get_initial(self):
        data = {}
        try:
            with open(settings_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            pass
        return {
            "webui_root": data.get("webui_root", cf.WEBUI_ROOT),
            "proxy": data.get("proxy", ""),
            "api_key": data.get("api_key", ""),
            "api_source": data.get("api_source", "com"),
            "browse": data.get("browse", {}),
            "types": data.get("types", {}),
            "options": data.get("options", {}),
            "defaults": data.get("defaults", {}),
            "pinned_folders": data.get("pinned_folders", {}),
            "demo": DEMO_MODE,
            **({"demoFolder": r"C:\SD-WebUI\models\Lora(演示)",
                "demoPlan": demo.preview_reorganize("", fast=True)} if DEMO_MODE else {}),
        }

    def start_scan(self, opts):
        worker = _STATE["worker"]
        if worker and worker.is_alive():
            return False
        if DEMO_MODE:
            cf.OUT = _emit_log
            cf.PROGRESS = _emit_progress
            cf.STOP_CHECK = lambda: _STATE["stop"]
            args = SimpleNamespace(webui_root=opts.get("webui_root", cf.WEBUI_ROOT),
                                   types=opts.get("types", ""), dry_run=bool(opts.get("dry_run")))

            def demo_work():
                try:
                    stats = demo.run_scan(args, _emit_log)
                    _push({"type": "done", "stats": stats})
                except Exception as e:
                    _push({"type": "crash", "error": repr(e)})

            _STATE["worker"] = threading.Thread(target=demo_work, daemon=True)
            _STATE["worker"].start()
            return True
        _save_settings(opts)
        _STATE["stop"] = False
        cf.OUT = _emit_log
        cf.PROGRESS = _emit_progress
        cf.STOP_CHECK = lambda: _STATE["stop"]
        args = SimpleNamespace(
            webui_root=opts.get("webui_root", cf.WEBUI_ROOT),
            types=opts.get("types", "lora,lycoris"),
            refetch_old=bool(opts.get("refetch_old")),
            retry_skeletons=bool(opts.get("retry_skeletons")),
            limit=0, offset=0,
            no_names=bool(opts.get("no_names", True)),
            proxy=opts.get("proxy", ""),
            api_key=opts.get("api_key", ""),
            dry_run=bool(opts.get("dry_run")),
            force_preview=bool(opts.get("force_preview")),
        )

        def work():
            try:
                stats = cf.run_scan(args)
                _push({"type": "done", "stats": stats})
            except Exception as e:
                _push({"type": "crash", "error": repr(e)})

        _STATE["worker"] = threading.Thread(target=work, daemon=True)
        _STATE["worker"].start()
        return True

    def stop_scan(self):
        _STATE["stop"] = True
        return True

    def open_url(self, url):
        """外链用系统浏览器打开(仅允许 civitai 域,防注入)"""
        import webbrowser as _wb
        if not isinstance(url, str) or not url.startswith("https://"):
            return False
        from urllib.parse import urlparse as _up
        host = (_up(url).hostname or "").lower()
        if not (host == "civitai.com" or host.endswith(".civitai.com")
                or host == "civitai.red" or host.endswith(".civitai.red")):
            return False
        _wb.open(url)
        return True

    def choose_folder(self):
        window = _STATE["window"]
        result = window.create_file_dialog(webview.FOLDER_DIALOG)
        return result[0] if isinstance(result, (list, tuple)) and result else None

    # ---------------- 站内浏览(A)

    def browse_models(self, opts):
        if DEMO_MODE:
            return demo.search_models(opts)
        cf.ensure_session(opts.get("proxy", ""), opts.get("apiKey", ""))
        return cb.search_models(opts)

    def get_model_detail(self, opts):
        if DEMO_MODE:
            return demo.get_model_detail(opts.get("modelId"))
        cf.ensure_session(opts.get("proxy", ""), opts.get("apiKey", ""))
        return cb.get_model_detail(opts.get("modelId"), api_key=opts.get("apiKey", ""))

    def local_library_index(self, opts):
        if DEMO_MODE:
            return demo.local_model_index()
        return cf.local_model_index(opts.get("webui_root", cf.WEBUI_ROOT),
                                    refresh=bool(opts.get("refresh")))

    def get_thumbnails(self, opts):
        """缩略图本地缓存(两级):已缓存秒回;缺失的后台补齐后推 thumbs_ready 事件"""
        if DEMO_MODE:
            return demo.get_thumbnails(opts.get("urls") or [])
        cf.ensure_session(opts.get("proxy", ""), opts.get("apiKey", ""))
        return cb.get_thumbnail_cache_async(
            opts.get("urls") or [], width=int(opts.get("width") or 450),
            on_ready=lambda m: _push({"type": "thumbs_ready", "map": m}),
            urgent=bool(opts.get("urgent")))

    def check_api_key(self, opts):
        if DEMO_MODE:
            return {"ok": False, "error": "演示模式:离线运行,不验证 Key"}
        return cb.check_api_key(opts.get("apiKey", ""), proxy=opts.get("proxy", ""))

    def save_settings(self, opts):
        payload = {"proxy": opts.get("proxy", ""),
                   "api_key": opts.get("apiKey", ""),
                   "api_source": opts.get("api_source", "")}
        if isinstance(opts.get("defaults"), dict):
            payload["defaults"] = opts["defaults"]
        if isinstance(opts.get("pinned_folders"), dict):
            payload["pinned_folders"] = opts["pinned_folders"]
        _save_settings(payload)
        return True

    def save_browse(self, browse):
        _save_settings({"browse": browse})
        return True

    # ---------------- 下载(B)

    def enqueue_download(self, opts):
        if DEMO_MODE:
            return demo.enqueue_download(opts)
        cf.ensure_session(opts.get("proxy", ""), opts.get("apiKey", ""))
        return cd.enqueue_download(opts)

    def cancel_download(self, task_id):
        return cd.cancel_download(task_id)

    def pause_download(self, task_id):
        return cd.pause_download(task_id)

    def resume_download(self, task_id):
        return cd.resume_download(task_id)

    def retry_download(self, task_id):
        return cd.retry_download(task_id)

    def downloads_state(self):
        state = cd.downloads_state()
        if DEMO_MODE:
            state = demo.downloads_state(state)
        return state

    # ---------------- 本地重分类(D)

    def scan_by_compat(self, opts):
        return cr.scan_by_compat(opts.get("baseKey") or "", opts.get("levels") or [],
                                 opts.get("scanRoot") or "", opts.get("targetFolder") or "")

    def get_compat_bases(self):
        import civitai_compat as cx
        bases = cx.load_bases()
        return [{"key": k, "label": v.get("label") or k} for k, v in bases.items()]

    def preview_reorganize_pinned(self, opts):
        return cr.preview_reorganize_pinned()

    def preview_reorganize(self, opts):
        if DEMO_MODE:
            return demo.preview_reorganize(opts.get("folder", ""))
        return cr.preview_reorganize(opts.get("folder", ""))

    def apply_reorganize(self, opts):
        if DEMO_MODE:
            return demo.apply_reorganize(opts.get("planId"), emit=_push)
        return cr.apply_reorganize(opts.get("planId"), emit=_push)

    def undo_reorganize(self):
        if DEMO_MODE:
            return demo.undo_last_reorganize(emit=_push)
        return cr.undo_last_reorganize(emit=_push)

    def reorganize_has_undo(self):
        if DEMO_MODE:
            return demo.has_undo()
        return cr.has_undo()

    def get_category_folders(self):
        return cb.load_category_folders()

    def set_category_folders(self, mapping):
        cb.save_category_folders(mapping)
        return True


def _save_settings(opts):
    try:
        api = Api()
        old = api.get_initial()
    except Exception:
        old = {}
    old.update({
        "webui_root": opts.get("webui_root", old.get("webui_root")),
        "proxy": opts.get("proxy", old.get("proxy", "")),
        "api_key": opts.get("api_key", old.get("api_key", "")),
    })
    if opts.get("api_source") in ("com", "red"):
        old["api_source"] = opts["api_source"]
    if isinstance(opts.get("browse"), dict):
        old.setdefault("browse", {}).update(opts["browse"])
    if isinstance(opts.get("defaults"), dict):
        old.setdefault("defaults", {}).update(opts["defaults"])
    if isinstance(opts.get("pinned_folders"), dict):
        # None/空串 = 解除绑定
        pf = {k: v for k, v in opts["pinned_folders"].items() if v}
        old["pinned_folders"] = pf
    # types/options 只接受 dict(扫描参数里的 types 是逗号串,严禁入设置污染勾选状态)
    for key in ("types", "options"):
        if isinstance(opts.get(key), dict):
            old[key] = opts[key]
    try:
        with open(settings_path(), "w", encoding="utf-8") as f:
            json.dump(old, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


_ASSET_PORT = {"port": 0}


def _start_asset_server():
    """内嵌本地资产服务(127.0.0.1 随机端口):
    页面/ui-dist 资产 + /t/ 缩略图 + /dt/ 演示缩略图。
    WebView2 的 file:// 页面禁止加载目录外本地文件(实测三种编码全拒),
    本地缓存从未真正显示过——同源 http 是唯一正解。"""
    import http.server
    import socketserver

    ui_root = os.path.join(res_dir(), "ui-dist")
    thumb_root = os.path.join(app_dir(), "thumb_cache")
    demo_thumb_root = os.path.join(res_dir(), "demo-assets", "thumbs")

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, path, ctype):
            try:
                with open(path, "rb") as f:
                    data = f.read()
            except OSError:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            import posixpath
            url = self.path.split("?")[0]
            if url.startswith("/t/"):
                name = os.path.basename(posixpath.basename(url[3:]))
                self._send(os.path.join(thumb_root, name), "image/jpeg")
            elif url.startswith("/dt/"):
                name = os.path.basename(url[4:])
                self._send(os.path.join(demo_thumb_root, name), "image/jpeg")
            else:
                rel = os.path.normpath(posixpath.basename(url) if url == "/" or url == "/fetcher.html"
                                       else posixpath.basename(url))
                # 只取文件名,杜绝路径穿越;子目录资产按相对段拼
                parts = [seg for seg in url.split("/") if seg not in ("", ".", "..")]
                full = os.path.join(ui_root, *parts) if parts else os.path.join(ui_root, "fetcher.html")
                full = os.path.realpath(full)
                if not full.startswith(os.path.realpath(ui_root)):
                    self.send_error(403)
                    return
                ctype = "text/html" if full.endswith(".html") else                         "text/css" if full.endswith(".css") else                         "application/javascript" if full.endswith(".js") else                         "font/woff2" if full.endswith(".woff2") else                         "image/png" if full.endswith(".png") else "application/octet-stream"
                self._send(full, ctype)

    class TS(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    import socket
    for _ in range(8):
        try:
            srv = TS(("127.0.0.1", 0), Handler)
            _ASSET_PORT["port"] = srv.server_address[1]
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            return _ASSET_PORT["port"]
        except OSError:
            continue
    return 0


def _screen_size():
    """主显示器物理像素(pywebview 窗口按物理像素计)"""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    except Exception:
        return 1920, 1080


def main():
    cd.on_event(_push)  # 下载队列事件 → 前端
    threading.Thread(target=_pump, daemon=True).start()
    api = Api()
    index = os.path.join(res_dir(), "ui-dist", "fetcher.html")
    sw, sh = _screen_size()
    width, height = int(sw * 0.85), int(sh * 0.85)
    port = _start_asset_server()
    page = f"http://127.0.0.1:{port}/fetcher.html" if port else index  # 服务起不来退回 file://
    _STATE["window"] = webview.create_window(
        "非猫 Civitai 信息补全器" + ("(演示版)" if DEMO_MODE else ""),
        url=page,
        js_api=api,
        background_color="#0f0f0f",
        width=width, height=height,
        min_size=(980, 640),
    )
    webview.start()


if __name__ == "__main__":
    main()
