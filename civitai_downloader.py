#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Civitai 模型下载:单 worker 顺序队列 + Range 断点续传 + 下载即建档。
建档复用 civitai_fetcher.process_model_info / download_preview,
落盘格式与 WebUI 插件双向兼容;自定义信息写顶层 x_civitai_fetcher(不影响插件)。
"""

import json
import os
import re
import threading
import time
import urllib.parse

import requests

import civitai_fetcher as cf
import civitai_browser as cb

_DL_STATE = {
    "queue": [],        # 待处理 task dict
    "active": None,     # 正在下载的 task
    "done": [],         # 已完结(保留最近 200 条)
    "worker": None,
    "lock": threading.Lock(),
}
_progress_last_ts = 0.0
_listeners = []


def _history_path():
    return os.path.join(os.path.dirname(cf.HASH_CACHE_FILE), "download_history.json")


def _load_history():
    try:
        with open(_history_path(), "r", encoding="utf-8") as f:
            _DL_STATE["done"] = json.load(f)
    except (OSError, ValueError):
        pass


def _save_history():
    try:
        slim = [{k: v for k, v in t.items() if k != "_opts"}
                for t in _DL_STATE["done"][-200:]]
        with open(_history_path(), "w", encoding="utf-8") as f:
            json.dump(slim, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


# 历史加载延迟到运行时:模块加载期 cf.HASH_CACHE_FILE 尚未被 fetcher_app 重定向,
# exe 环境会读到临时解包目录的空文件(只写不读的根源)
_history_loaded = {"done": False}


def _load_history_once():
    if not _history_loaded["done"]:
        _history_loaded["done"] = True
        _load_history()


def _finish(task):
    """任务终结:移出活动区、写入历史并落盘"""
    with _DL_STATE["lock"]:
        if _DL_STATE["active"] is task:
            _DL_STATE["active"] = None
        _DL_STATE["done"].append(task)
        del _DL_STATE["done"][:-200]
        _save_history()
    _emit_tasks(task.get("status") or "finished")


def on_event(fn):
    _listeners.append(fn)


def _emit(detail):
    for fn in list(_listeners):
        try:
            fn(detail)
        except Exception:
            pass


def _snapshot():
    with _DL_STATE["lock"]:
        return {
            "queue": [t["id"] for t in _DL_STATE["queue"]],
            "active": _DL_STATE["active"]["id"] if _DL_STATE["active"] else None,
            "doneCount": len(_DL_STATE["done"]),
        }


def _public_task(t):
    keys = ("id", "modelId", "versionId", "modelName", "versionName", "thumbUrl", "type",
            "targetDir", "status", "percent", "speed", "downloadedMB", "totalMB",
            "error", "category", "paused")
    return {k: t.get(k) for k in keys}


def _emit_tasks(reason):
    with _DL_STATE["lock"]:
        payload = {
            "queue": [_public_task(t) for t in _DL_STATE["queue"]],
            "active": _public_task(_DL_STATE["active"]) if _DL_STATE["active"] else None,
            "done": [_public_task(t) for t in _DL_STATE["done"][-50:]],
        }
    _emit({"type": "downloads", "reason": reason, "state": payload})


def ensure_worker():
    if _DL_STATE["worker"] and _DL_STATE["worker"].is_alive():
        return
    _DL_STATE["worker"] = threading.Thread(target=_worker_loop, daemon=True)
    _DL_STATE["worker"].start()


def enqueue_download(opts):
    """opts: {modelId, versionId, targetRoot, subfolder, autoCategory,
             withExtras, apiKey, proxy}"""
    task = {
        "id": f"{int(time.time() * 1000)}",
        "modelId": opts.get("modelId"), "versionId": opts.get("versionId"),
        "targetRoot": opts.get("targetRoot"), "subfolder": opts.get("subfolder", ""),
        "autoCategory": bool(opts.get("autoCategory", True)),
        "withExtras": bool(opts.get("withExtras", True)),
        "api_key": opts.get("apiKey", ""), "proxy": opts.get("proxy", ""),
        "status": "queued", "percent": 0, "speed": 0,
        "downloadedMB": 0, "totalMB": 0, "error": "", "category": "",
        "modelName": str(opts.get("modelName") or ""), "versionName": str(opts.get("versionName") or ""),
        "thumbUrl": str(opts.get("thumbUrl") or ""),
        "type": "", "targetDir": "",
        "cancel": False, "paused": False,
        "_opts": dict(opts),
    }
    with _DL_STATE["lock"]:
        _DL_STATE["queue"].append(task)
    ensure_worker()
    _emit_tasks("enqueued")
    return task["id"]


def cancel_download(task_id):
    with _DL_STATE["lock"]:
        for t in _DL_STATE["queue"]:
            if t["id"] == task_id:
                t["cancel"] = True
                t["status"] = "cancelled"
                _DL_STATE["queue"].remove(t)
                _finish(t)
                return True
        active = _DL_STATE["active"]
        if active and active["id"] == task_id:
            active["cancel"] = True
            return True
    return False


def pause_download(task_id):
    with _DL_STATE["lock"]:
        for t in _DL_STATE["queue"]:
            if t["id"] == task_id:
                t["paused"] = True
                t["status"] = "paused"
                _emit_tasks("paused")
                return True
        active = _DL_STATE["active"]
        if active and active["id"] == task_id:
            active["paused"] = True
            return True
    return False


def resume_download(task_id):
    with _DL_STATE["lock"]:
        for t in _DL_STATE["queue"]:
            if t["id"] == task_id and t.get("paused"):
                t["paused"] = False
                t["status"] = "queued"
                _emit_tasks("resumed")
                return True
        active = _DL_STATE["active"]
        if active and active["id"] == task_id:
            active["paused"] = False
            return True
    return False


def retry_download(task_id):
    """从历史重试:复用原参数;Key/代理缺失时用当前设置补上"""
    with _DL_STATE["lock"]:
        src = next((t for t in _DL_STATE["done"] if t["id"] == task_id), None)
        saved = dict(src["_opts"]) if src and isinstance(src.get("_opts"), dict) else None
    if not saved:
        return False
    if not saved.get("apiKey") or not saved.get("proxy"):
        try:
            cur_path = os.path.join(os.path.dirname(cf.HASH_CACHE_FILE), "gui_settings.json")
            with open(cur_path, "r", encoding="utf-8") as f:
                cur = json.load(f) or {}
            saved["apiKey"] = saved.get("apiKey") or cur.get("api_key", "")
            saved["proxy"] = saved.get("proxy") or cur.get("proxy", "")
        except (OSError, ValueError):
            pass
    return enqueue_download(saved)


def downloads_state():
    _load_history_once()
    with _DL_STATE["lock"]:
        return {
            "queue": [_public_task(t) for t in _DL_STATE["queue"]],
            "active": _public_task(_DL_STATE["active"]) if _DL_STATE["active"] else None,
            "done": [_public_task(t) for t in _DL_STATE["done"][-50:]],
        }


# ---------------------------------------------------------------- worker

def _worker_loop():
    while True:
        with _DL_STATE["lock"]:
            task = _DL_STATE["queue"][0] if _DL_STATE["queue"] else None
            if task:
                _DL_STATE["queue"].pop(0)
                if task.get("cancel"):
                    task["status"] = "cancelled"
                    _finish(task)
                    task = None
                else:
                    _DL_STATE["active"] = task
        if task is None:
            if not _DL_STATE["queue"]:
                break
            continue
        # 开始前被暂停:挂起等待继续/取消
        while task.get("paused") and not task.get("cancel"):
            task["status"] = "paused"
            time.sleep(0.3)
        try:
            _run_task(task)
            # 下载中暂停:_run_task 会以 paused 状态返回,挂起等继续(续传)
            while (task.get("status") == "paused"
                   and not task.get("cancel")
                   and task.get("paused")):
                time.sleep(0.3)
            if task.get("cancel") and task.get("status") == "paused":
                task["status"] = "cancelled"
                _finish(task)
        except Exception as e:
            task["status"] = "failed"
            task["error"] = repr(e)
            _finish(task)
        time.sleep(0.3)


def _run_task(task):
    cf.ensure_session(task["proxy"], task["api_key"])
    task["status"] = "fetching"
    _emit_tasks("fetching")

    ver = cb._get(f'{cb.api_base()}/model-versions/{task["versionId"]}',
                  api_key=task["api_key"] if cb.api_base() == cb.API_BASES["com"] else "")
    model = cb._get(f'{cb.api_base()}/models/{ver.get("modelId")}',
                    api_key=task["api_key"] if cb.api_base() == cb.API_BASES["com"] else "")

    task["modelName"] = model.get("name", "")
    task["versionName"] = ver.get("name", "")
    task["type"] = model.get("type", "")
    # 任务缩略图:版本首图优先,退回入队时带的卡片图
    if not task.get("thumbUrl"):
        imgs = ver.get("images") or []
        if imgs and imgs[0].get("url"):
            task["thumbUrl"] = imgs[0]["url"]

    # 目标目录:类型默认目录 → 子文件夹 → 大类
    cf_type = cb.TYPE_TO_CF.get(model.get("type"), "lora")
    folder_name = cb.CF_TO_FOLDER.get(cf_type, "Lora")
    target_root = task["targetRoot"] or os.path.join(cf.WEBUI_ROOT, "models")
    target_dir = os.path.join(target_root, folder_name)
    if task["subfolder"]:
        target_dir = os.path.join(target_dir, task["subfolder"].strip("/\\ "))
    if task["autoCategory"]:
        cat, hit = cb.resolve_category(model.get("tags"), model.get("type"))
        if cat:
            task["category"] = f"{cat} ({hit})" if hit else cat
            target_dir = os.path.join(target_dir, cat)
    task["targetDir"] = target_dir
    os.makedirs(target_dir, exist_ok=True)

    files = ver.get("files") or []
    primary = [f for f in files if f.get("primary")] or \
              [f for f in files if f.get("type") == "Model"] or files[:1]
    extras = [f for f in files if f not in primary] if task["withExtras"] else []
    plan = [(f, True) for f in primary] + [(f, False) for f in extras]

    downloaded_path = None
    for f, is_primary in plan:
        if task.get("cancel"):
            task["status"] = "cancelled"
            break
        path = _download_one(task, ver, f, target_dir)
        if path == "PAUSED":
            task["status"] = "paused"
            return
        if path and is_primary:
            downloaded_path = path

    if task.get("cancel"):
        _finish(task)
        return

    # 下载即建档:version JSON + 父级合并 → 三件套
    if downloaded_path:
        try:
            model_info = _build_model_info(ver, model)
            result = cf.process_model_info(downloaded_path, model_info, cf_type, False)
            cf.download_preview(downloaded_path, force=False)
            task["status"] = "done"
        except Exception as e:
            task["status"] = "done"
            task["error"] = f"模型文件已下载,建档失败: {e!r}"
    else:
        task["status"] = task.get("status") or "failed"
        if not task["error"]:
            task["error"] = "主文件未能下载"
    _finish(task)


def _build_model_info(ver, model):
    """与插件 append_parent_model_metadata 同构:版本 JSON + 父级字段 + x_ 扩展"""
    metadatas = ["description", "tags", "allowNoCredit", "allowCommercialUse",
                 "allowDerivatives", "allowDifferentLicense"]
    model_meta = ver.get("model") or {}
    for key in metadatas:
        model_meta[key] = model.get(key, "")
    ver["model"] = model_meta
    ver["creator"] = model.get("creator", {})
    ver["x_civitai_fetcher"] = {
        "source": "civitai-fetcher",
        "modelId": model.get("id"),
        "fetchedAt": int(time.time()),
    }
    return ver


def _sanitize_filename(name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name or "").strip()
    return name or "model.bin"


def _filename_for(task, ver, file_info, resp):
    cd = resp.headers.get("Content-Disposition", "")
    if "filename=" in cd:
        try:
            name = cd.split("filename=")[1].strip('"').encode("iso8859-1").decode("utf-8")
            if name:
                return _sanitize_filename(name)
        except (IndexError, UnicodeDecodeError):
            pass
    name = file_info.get("name")
    if not name:
        url = file_info.get("downloadUrl") or ver.get("downloadUrl") or ""
        name = os.path.basename(urllib.parse.urlparse(url).path) or "model.bin"
    return _sanitize_filename(name)


def _download_url(file_info, ver, api_key):
    url = file_info.get("downloadUrl") or ver.get("downloadUrl") or ""
    if not url:
        raise RuntimeError("无 downloadUrl")
    # 官方源才携带 token;镜像源(第三方)不泄露凭证
    if api_key and cb.api_base() == cb.API_BASES["com"]:
        url += ("&" if "?" in url else "?") + "token=" + urllib.parse.quote(api_key)
    return url


def _download_one(task, ver, file_info, target_dir):
    url = _download_url(file_info, ver, task["api_key"])
    resp = cf.SESSION.get(url, stream=True, timeout=cf.REQUEST_TIMEOUT)
    if resp.status_code in (401, 402, 403):
        resp.close()
        task["error"] = {401: "需要 Civitai API Key(登录墙)",
                         402: "Early Access / 付费模型,跳过",
                         403: "无权限下载"}.get(resp.status_code, "无权限")
        return None
    resp.raise_for_status()

    filename = _filename_for(task, ver, file_info, resp)
    final_path = os.path.join(target_dir, filename)
    if os.path.isfile(final_path):
        resp.close()
        if file_info.get("primary") or not file_info:
            # 已存在不算失败:返回路径让主流程补全元数据
            task["error"] = f"文件已存在,跳过下载(已补全信息): {filename}"
            return final_path
        return None
    resp.close()

    tmp = final_path + ".downloading"
    offset = os.path.getsize(tmp) if os.path.isfile(tmp) else 0
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    resp = cf.SESSION.get(url, stream=True, timeout=cf.REQUEST_TIMEOUT, headers=headers)
    resp.raise_for_status()
    resumed = offset and resp.status_code == 206
    if offset and not resumed:
        offset = 0  # 服务端不支持 Range,整段重下
    total_size = offset + int(resp.headers.get("Content-Length") or 0)
    mode = "ab" if resumed else "wb"

    task["status"] = "downloading"
    task["totalMB"] = round(total_size / 1048576, 1)
    last_emit = 0.0
    start = time.time()
    got = 0

    with open(tmp, mode) as f:
        for chunk in resp.iter_content(chunk_size=256 * 1024):
            if task.get("cancel"):
                resp.close()
                task["status"] = "cancelled"
                return None
            if task.get("paused"):
                # 暂停:保留 .downloading 临时文件,继续时按字节数续传
                resp.close()
                return "PAUSED"
            if not chunk:
                continue
            f.write(chunk)
            got += len(chunk)
            now = time.time()
            if now - last_emit > 0.35:
                done_bytes = offset + got
                speed = got / max(now - start, 0.1)
                task["percent"] = int(done_bytes * 100 / total_size) if total_size else 0
                task["speed"] = round(speed / 1048576, 1)
                task["downloadedMB"] = round(done_bytes / 1048576, 1)
                _emit({"type": "download_progress", "task": _public_task(task)})
                last_emit = now
    resp.close()

    if total_size and offset + got < total_size:
        task["error"] = f"下载不完整({offset + got}/{total_size} 字节),可重试续传"
        return None
    os.replace(tmp, final_path)
    return final_path
