#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Civitai 站内浏览:搜索/详情 + 卡片 DTO 裁剪 + 大类解析。
所有请求走 civitai_fetcher 的 SESSION(复用其重试/退避),列表 DTO 就地裁剪,
避免 1~3MB/页的原始 JSON 拖垮 pywebview 桥。
"""

import hashlib
import json
import os
import re
import threading
import time
import html as html_mod

import civitai_fetcher as cf

API = "https://civitai.com/api/v1"  # 官方(登录校验/check_api_key 永远走这里)
API_BASES = {
    "com": "https://civitai.com/api/v1",
    "red": "https://civitai.red/api/v1",  # 第三方镜像(NSFW 免登录);绝不发送 API Key
}


def api_base():
    """当前数据源(设置 gui_settings.json 的 api_source;默认官方)"""
    try:
        path = os.path.join(os.path.dirname(cf.HASH_CACHE_FILE), "gui_settings.json")
        with open(path, "r", encoding="utf-8") as f:
            source = (json.load(f) or {}).get("api_source")
    except (OSError, ValueError):
        source = None
    return API_BASES.get(source, API_BASES["com"])

_last_request_ts = 0.0
_throttle_lock = threading.Lock()
MIN_GAP = 0.3  # 浏览请求间隔,礼貌限速

SORTS = ["Newest", "Oldest", "Most Downloaded", "Highest Rated",
         "Most Liked", "Most Discussed", "Most Collected", "Most Images"]
PERIODS = ["AllTime", "Year", "Month", "Week", "Day"]
TYPES = ["Checkpoint", "TextualInversion", "Hypernetwork", "LORA", "LoCon",
         "DoRA", "VAE", "Upscaler", "MotionModel", "Controlnet"]
BASE_MODELS = ["SD 1.5", "SDXL 1.0", "Pony", "Illustrious", "NoobAI",
               "SD 3.5", "SD 3.5 Medium", "SD 3.5 Large", "Flux.1 D", "Flux.1 S"]

# 大类 → 默认中文文件夹名(用户可在 category_folders.json 改)
DEFAULT_CATEGORY_MAP = {
    "character": "角色", "style": "画风", "clothing": "服装", "poses": "姿势",
    "concept": "概念", "celebrity": "名人", "background": "背景", "animal": "动物",
    "vehicle": "载具", "buildings": "建筑", "objects": "物品", "tool": "工具",
    "action": "动作", "assets": "素材",
    # Civitai 部分模型用单数形态的标签(实测 pose 模型有只带单数的)
    "pose": "姿势", "object": "物品", "building": "建筑", "asset": "素材",
}
# Civitai type → 插件/扫描器 type 键(建档与默认目录映射共用)
TYPE_TO_CF = {"LORA": "lora", "LoCon": "lora", "DoRA": "lora",
              "Checkpoint": "ckp", "TextualInversion": "ti",
              "Hypernetwork": "hyper", "VAE": "vae"}
# cf type → WebUI 默认目录名
CF_TO_FOLDER = {"lora": "Lora", "lycoris": "LyCORIS", "ckp": "Stable-diffusion",
                "vae": "VAE", "ti": "embeddings", "hyper": "hypernetworks"}

_tag_re = re.compile(r"<[^>]+>")


def strip_html(text):
    if not text:
        return ""
    text = _tag_re.sub(" ", text)
    text = html_mod.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def category_config_path():
    # 与 hash_cache 同目录(cli=脚本目录;exe=由 fetcher_app 重定向到 exe 旁)
    return os.path.join(os.path.dirname(cf.HASH_CACHE_FILE), "category_folders.json")


def load_category_folders():
    try:
        with open(category_config_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return dict(DEFAULT_CATEGORY_MAP)


def save_category_folders(mapping):
    with open(category_config_path(), "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=1)


# 多分类并存时的让位分类:只有它时才用它(概念太泛,服装/角色/画风等更具体)
CATEGORY_LOW_PRIORITY = {"concept"}


def resolve_category(tags, model_type, force=None):
    """tags 命中大类表 → (中文文件夹名, 命中标签);未命中 → (英文标签原样, 标签);
    完全未命中 → (None, None) 由调用方决定留原地或用 type 兜底。
    优先级:forceCategory(强制分类,.civitai.info 顶层,编辑器可写)>
      非让位分类按 tags 首个命中>让位分类(概念)仅在无其他分类时生效。
    force 值为分类名(如"画风")时同样吃专属文件夹绑定。"""
    if force and str(force).strip():
        return str(force).strip(), "强制分类"
    mapping = load_category_folders()
    fallback = None
    for tag in tags or []:
        key = str(tag).strip().lower()
        folder = mapping.get(key) or (DEFAULT_CATEGORY_MAP.get(key) if key in DEFAULT_CATEGORY_MAP else None)
        if not folder:
            continue
        if key in CATEGORY_LOW_PRIORITY:
            if fallback is None:
                fallback = (folder, tag)
            continue
        return folder, tag
    return fallback if fallback else (None, None)


def _get(url, params=None, api_key=""):
    """限速 + 复用 cf.SESSION 的 GET;401/403/404 立即抛,其余退避重试"""
    global _last_request_ts
    with _throttle_lock:
        gap = time.time() - _last_request_ts
        if gap < MIN_GAP:
            time.sleep(MIN_GAP - gap)
        _last_request_ts = time.time()

    import requests
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    retries = 0
    while True:
        try:
            resp = cf.SESSION.get(url, params=params, headers=headers,
                                  stream=True, timeout=cf.REQUEST_TIMEOUT)
        except requests.RequestException as e:
            if retries >= 3:
                raise RuntimeError(f"net_error: {e}")
            time.sleep(3)
            retries += 1
            continue
        if resp.ok:
            try:
                return resp.json()
            finally:
                resp.close()
        code = resp.status_code
        resp.close()
        if code in (401, 403, 404):
            raise RuntimeError(f"http_{code}")
        if retries >= 5:
            raise RuntimeError(f"http_{code}")
        time.sleep(3 + ((retries >> 1) ** 2))
        retries += 1


def _img_dto(img):
    return {"url": img.get("url"), "nsfwLevel": img.get("nsfwLevel", 0),
            "width": img.get("width", 0), "height": img.get("height", 0)}


def thumb_url(url, width=450):
    """把原图/original URL 改写成指定宽度的小图(Civitai CDN 支持 /width=N/ 路径段)"""
    if not url:
        return url
    url = re.sub(r"/original=true/", f"/width={width}/", url)
    url = re.sub(r"/width=\d+/", f"/width={width}/", url)
    return url


def thumbnail_cache_dir():
    return os.path.join(os.path.dirname(cf.HASH_CACHE_FILE), "thumb_cache")


_thumb_pool = None


_PRUNE_COUNTER = {"n": 0}


def _maybe_prune_thumb_cache(max_files=8000, keep=5000):
    """缩略图缓存超额时按最旧清理(每次调用计数,每 40 次查一次)"""
    _PRUNE_COUNTER["n"] += 1
    if _PRUNE_COUNTER["n"] % 40:
        return
    cache_dir = thumbnail_cache_dir()
    try:
        files = [(os.path.getmtime(os.path.join(cache_dir, f)), f)
                 for f in os.listdir(cache_dir) if f.endswith(".jpg")]
    except OSError:
        return
    if len(files) <= max_files:
        return
    files.sort()
    for _, f in files[:len(files) - keep]:
        try:
            os.remove(os.path.join(cache_dir, f))
        except OSError:
            pass


def get_thumbnail_cache_async(urls, width=450, on_ready=None, urgent=False):
    """缩略图两级加载:已缓存的同步秒回;缺失的后台并发下载,
    完成后 on_ready({原url: 本地路径}) 增量回调。"""
    if not urls:
        return {}
    cache_dir = thumbnail_cache_dir()
    os.makedirs(cache_dir, exist_ok=True)
    result, todo = {}, {}
    for u in urls:
        if not u:
            continue
        small = thumb_url(u, width)
        path = os.path.join(cache_dir, hashlib.md5(small.encode()).hexdigest() + ".jpg")
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            result[u] = path
        else:
            todo[small] = (u, path)
    _maybe_prune_thumb_cache()
    if todo and urgent and len(todo) <= 3:
        # 加急(卡片预取):同步直下,不等被批量占满的线程池
        cf.ensure_session()
        for small, (u, path) in list(todo.items()):
            if _fetch_thumb(small, path):
                result[u] = path
        return result
    if todo and on_ready:
        cf.ensure_session()
        threading.Thread(target=_bg_fetch_thumbs, args=(todo, on_ready),
                         daemon=True).start()
    return result


def _bg_fetch_thumbs(todo, on_ready):
    import concurrent.futures
    global _thumb_pool
    if _thumb_pool is None:
        _thumb_pool = concurrent.futures.ThreadPoolExecutor(max_workers=6)

    def _with_retry(url, path):
        for attempt in range(3):
            if _fetch_thumb(url, path):
                return True
            time.sleep(1 + attempt)
        return False

    futures = {_thumb_pool.submit(_with_retry, small, pair[1]): pair
               for small, pair in todo.items()}
    # 逐张完成逐张回调:一张到货前端亮一张,不等整批
    for fut in concurrent.futures.as_completed(futures, timeout=180):
        u, p = futures[fut]
        try:
            if fut.result() and os.path.isfile(p):
                on_ready({u: p})
        except Exception:
            continue


def get_thumbnail_cache(urls, width=450):
    """并发下载缩略图到本地缓存,返回 {原url: 本地路径};失败的 url 不在结果里。
    前端用 file:/// 加载,绕开 WebView2 的系统代理(Clash 关闭时图片全挂)。"""
    global _thumb_pool
    if not urls:
        return {}
    cache_dir = thumbnail_cache_dir()
    os.makedirs(cache_dir, exist_ok=True)
    import concurrent.futures
    todo = {}
    result = {}
    for u in urls:
        if not u:
            continue
        small = thumb_url(u, width)
        path = os.path.join(cache_dir, hashlib.md5(small.encode()).hexdigest() + ".jpg")
        result[u] = path
        if not (os.path.isfile(path) and os.path.getsize(path) > 0):
            todo[small] = (u, path)
    if todo:
        cf.ensure_session()
        if _thumb_pool is None:
            _thumb_pool = concurrent.futures.ThreadPoolExecutor(max_workers=6)

        def _with_retry(url, path):
            for attempt in range(3):
                if _fetch_thumb(url, path):
                    return True
                time.sleep(1 + attempt)
            return False

        futures = [_thumb_pool.submit(_with_retry, small, path)
                   for small, (u, path) in todo.items()]
        try:
            concurrent.futures.wait(futures, timeout=90)
        except Exception:
            pass
    return {u: p for u, p in result.items() if os.path.isfile(p) and os.path.getsize(p) > 0}


def _fetch_thumb(url, path):
    try:
        import requests
        tmp = path + ".tmp"
        resp = cf.SESSION.get(url, stream=True, timeout=20)
        if not resp.ok:
            resp.close()
            return False
        with open(tmp, "wb") as f:
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if chunk:
                    f.write(chunk)
        resp.close()
        os.replace(tmp, path)
        return True
    except Exception:
        if os.path.isfile(path + ".tmp"):
            try:
                os.remove(path + ".tmp")
            except OSError:
                pass
        return False


def _file_dto(f):
    return {"name": f.get("name"), "sizeKB": f.get("sizeKB", 0),
            "type": f.get("type"), "primary": bool(f.get("primary")),
            "downloadUrl": f.get("downloadUrl", "")}


def _version_dto(v, with_files=False, max_images=4):
    # 只保留 type=="image" 的(视频封面混在 images 里会让 <img> 黑块)
    images = [_img_dto(i) for i in (v.get("images") or []) if i.get("type", "image") == "image"][:max_images]
    dto = {
        "id": v.get("id"), "name": v.get("name"), "baseModel": v.get("baseModel"),
        "trainedWords": (v.get("trainedWords") or [])[:6],
        "images": images,
        "downloadUrl": v.get("downloadUrl", ""),
        "createdAt": (v.get("publishedAt") or v.get("createdAt") or "")[:10],
    }
    if with_files:
        dto["files"] = [_file_dto(f) for f in (v.get("files") or [])]
        dto["description"] = strip_html(v.get("description"))
        dto["descriptionHtml"] = v.get("description") or ""
    return dto


def _card_dto(item):
    versions = item.get("modelVersions") or []
    v = versions[0] if versions else {}
    stats = item.get("stats") or {}
    return {
        "id": item.get("id"), "name": item.get("name"), "type": item.get("type"),
        "creator": (item.get("creator") or {}).get("username", ""),
        "downloads": stats.get("downloadCount", 0),
        "thumbsUp": stats.get("thumbsUpCount", 0),
        "tags": (item.get("tags") or [])[:5],
        "nsfw": bool(item.get("nsfw")),
        "version": _version_dto(v),
    }


_range_ctx = {}   # ctx_id -> {"key":…, "cursor":…, "buf":[items], "done":bool, "ts":t}


def _range_key(opts):
    return "|".join(str(x) for x in (
        opts.get("query"), tuple(opts.get("types") or []),
        tuple(opts.get("baseModels") or []), opts.get("tag"),
        opts.get("username"), opts.get("startDate"), opts.get("endDate")))


def _range_search(opts):
    """/models 无日期区间参数 → Newest 翻页聚合 + 区间过滤(越界即停)。
    结果按 publishedAt 降序;nextCursor 为聚合上下文 id。"""
    import uuid
    key = _range_key(opts)
    limit = max(1, min(int(opts.get("limit") or 20), 100))
    sd = int(opts.get("startDate") or 0)
    ed = int(opts.get("endDate") or 9999999999)
    sd_s = time.strftime("%Y-%m-%d", time.gmtime(sd)) if sd else ""
    ed_s = time.strftime("%Y-%m-%d", time.gmtime(ed)) if ed < 9999999999 else ""

    ctx = None
    if opts.get("cursor"):
        ctx = _range_ctx.get(str(opts["cursor"]))
        if ctx and ctx["key"] != key:
            ctx = None
    if ctx is None:
        ctx = {"key": key, "cursor": None, "buf": [], "done": False, "ts": time.time()}
        cid = uuid.uuid4().hex[:12]
        _range_ctx[cid] = ctx
    else:
        cid = str(opts["cursor"])
        ctx["ts"] = time.time()
    # 容量控制:最多 50 个上下文,LRU
    if len(_range_ctx) > 50:
        for k in sorted(_range_ctx, key=lambda k: _range_ctx[k]["ts"])[:-50]:
            _range_ctx.pop(k, None)

    base_params = {"limit": 100, "nsfw": "true", "sort": "Newest"}
    if opts.get("query"):
        base_params["query"] = opts["query"]
    for t in opts.get("types") or []:
        base_params.setdefault("types", []).append(t)
    for b in opts.get("baseModels") or []:
        base_params.setdefault("baseModels", []).append(b)
    if opts.get("tag"):
        base_params["tag"] = opts["tag"]
    if opts.get("username"):
        base_params["username"] = opts["username"]

    out, exhausted = [], False
    pages = 0
    while len(out) < limit and pages < 5:
        # 先吃缓冲
        while ctx["buf"] and len(out) < limit:
            it = ctx["buf"].pop(0)
            d = ((it.get("version") or {}).get("createdAt") or "")
            if sd_s and d and d < sd_s:
                exhausted = True   # Newest 降序,更老的无须再看
                break
            if ed_s and d and d > ed_s:
                continue
            out.append(it)
        if len(out) >= limit or exhausted:
            break
        if ctx["done"]:
            break
        # 拉下一页 civitai
        params = dict(base_params)
        if ctx["cursor"]:
            params["cursor"] = ctx["cursor"]
        data = _get(f"{api_base()}/models", params=params,
                    api_key=opts.get("api_key", "") if api_base() == API_BASES["com"] else "")
        pages += 1
        items = [_card_dto(i) for i in data.get("items") or []]
        ctx["cursor"] = (data.get("metadata") or {}).get("nextCursor")
        ctx["buf"].extend(items)
        if not ctx["cursor"]:
            ctx["done"] = True
            if not items:
                break
    if exhausted:
        ctx["done"] = True   # 越界:后续不会再有区间内结果
    next_cursor = cid if (ctx["buf"] or not ctx["done"]) else None
    return {"items": out, "nextCursor": next_cursor}


def search_models(opts):
    """opts: {query, types[], baseModels[], sort, period, limit, cursor, api_key}"""
    # 自定义时间段(近半年/三个月/任意区间):走翻页聚合
    if opts.get("startDate") or opts.get("endDate"):
        return _range_search(opts)
    params = {"limit": max(1, min(int(opts.get("limit") or 20), 100))}
    # 必须显式 nsfw=true,否则服务端连登录用户也不给成熟内容;
    # 本地的"隐藏 NSFW"开关在前端做客户端过滤
    params["nsfw"] = "true"
    if opts.get("query"):
        params["query"] = opts["query"]
    if opts.get("tag"):
        params["tag"] = opts["tag"]
    if opts.get("username"):
        params["username"] = opts["username"]
    if opts.get("tag"):
        params["tag"] = opts["tag"]
    if opts.get("username"):
        params["username"] = opts["username"]
    for t in opts.get("types") or []:
        params.setdefault("types", []).append(t)
    for b in opts.get("baseModels") or []:
        params.setdefault("baseModels", []).append(b)
    if opts.get("sort"):
        params["sort"] = opts["sort"]
    if opts.get("period"):
        params["period"] = opts["period"]
    if opts.get("cursor"):
        params["cursor"] = opts["cursor"]
    # 镜像源不发送 API Key(第三方站,防凭证泄露;镜像本身 NSFW 免登录)
    data = _get(f"{api_base()}/models", params=params,
                api_key=opts.get("api_key", "") if api_base() == API_BASES["com"] else "")
    items = [_card_dto(i) for i in data.get("items") or []]
    meta = data.get("metadata") or {}
    return {"items": items, "nextCursor": meta.get("nextCursor")}


def get_model_detail(model_id, api_key=""):
    key = api_key if api_base() == API_BASES["com"] else ""
    item = _get(f"{api_base()}/models/{model_id}", api_key=key)
    stats = item.get("stats") or {}
    return {
        "id": item.get("id"), "name": item.get("name"), "type": item.get("type"),
        "creator": (item.get("creator") or {}).get("username", ""),
        "tags": item.get("tags") or [],
        "description": strip_html(item.get("description")),
        "descriptionHtml": item.get("description") or "",
        "downloads": stats.get("downloadCount", 0),
        "thumbsUp": stats.get("thumbsUpCount", 0),
        "versions": [_version_dto(v, with_files=True, max_images=6)
                     for v in (item.get("modelVersions") or [])[:12]],
    }


IMG_SORTS = ["Most Reactions", "Newest", "Most Discussed"]
IMG_PERIODS = ["AllTime", "Year", "Month", "Week", "Day"]


def search_images(opts):
    """图墙:/api/v1/images(cursor 翻页)。
    注:Civitai 已从公开 API 移除图片生成参数(prompt/meta),trpc 亦锁 401——
    本功能提供图片浏览 + 关联模型(触发词参考),prompt 无法获取属平台限制。"""
    params = {"limit": max(1, min(int(opts.get("limit") or 40), 100)),
              "nsfw": "true"}
    if opts.get("sort"):
        params["sort"] = opts["sort"]
    if opts.get("period"):
        params["period"] = opts["period"]
    if opts.get("cursor"):
        params["cursor"] = opts["cursor"]
    data = _get(f"{api_base()}/images", params=params,
                api_key=opts.get("api_key", "") if api_base() == API_BASES["com"] else "")
    items = []
    for i in data.get("items") or []:
        if i.get("type") != "image":
            continue
        stats = i.get("stats") or {}
        items.append({
            "id": i.get("id"), "url": i.get("url"),
            "width": i.get("width"), "height": i.get("height"),
            "nsfwLevel": i.get("nsfwLevel") or 0,
            "baseModel": i.get("baseModel") or "",
            "username": i.get("username") or "",
            "createdAt": (i.get("createdAt") or "")[:10],
            "reactions": stats.get("reactionCount") or stats.get("cryCount") or 0,
            "comments": stats.get("commentCount") or 0,
            "modelVersionIds": (i.get("modelVersionIds") or [])[:4],
        })
    return {"items": items,
            "nextCursor": (data.get("metadata") or {}).get("nextCursor")}


def check_api_key(api_key, proxy=""):
    """GET /api/v1/me 验证 Key,返回 {ok, username|error}"""
    import requests
    cf.ensure_session(proxy, api_key)
    try:
        resp = cf.SESSION.get(f"{API}/me", timeout=30)
    except requests.RequestException as e:
        return {"ok": False, "error": f"网络错误: {e}"}
    if resp.status_code == 401:
        return {"ok": False, "error": "Key 无效或已过期"}
    if not resp.ok:
        return {"ok": False, "error": f"HTTP {resp.status_code}"}
    try:
        return {"ok": True, "username": (resp.json() or {}).get("username", "")}
    finally:
        resp.close()
