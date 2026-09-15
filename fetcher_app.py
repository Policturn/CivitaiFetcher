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
        for d in batch:
            try:
                window.evaluate_js(f"window.__fePush({json.dumps(d, ensure_ascii=False)})")
            except Exception:
                time.sleep(0.2)


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
            on_ready=lambda m: _push({"type": "thumbs_ready", "map": m}))

    def check_api_key(self, opts):
        if DEMO_MODE:
            return {"ok": False, "error": "演示模式:离线运行,不验证 Key"}
        return cb.check_api_key(opts.get("apiKey", ""), proxy=opts.get("proxy", ""))

    def save_settings(self, opts):
        _save_settings({"proxy": opts.get("proxy", ""),
                        "api_key": opts.get("apiKey", ""),
                        "api_source": opts.get("api_source", "")})
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
        old["browse"] = opts["browse"]
    for key in ("types", "options"):
        if key in opts:
            old[key] = opts[key]
    try:
        with open(settings_path(), "w", encoding="utf-8") as f:
            json.dump(old, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


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
    _STATE["window"] = webview.create_window(
        "非猫 Civitai 信息补全器" + ("(演示版)" if DEMO_MODE else ""),
        url=index,
        js_api=api,
        background_color="#0f0f0f",
        width=width, height=height,
        min_size=(980, 640),
    )
    webview.start()


if __name__ == "__main__":
    main()
