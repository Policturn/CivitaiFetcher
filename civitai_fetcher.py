#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
civitai_fetcher — Stable-Diffusion-Webui-Civitai-Helper 的独立复刻(扫描部分)

等效于在 WebUI 里跑 Civitai Helper 的 "Scan Model Info":
  遍历模型目录 → 全文件 SHA256 → by-hash 反查 Civitai → 落盘
  {模型名}.civitai.info + {模型名}.json + {模型名}.preview.png

与插件同源的逻辑: zixaphir/Stable-Diffusion-Webui-Civitai-Helper ch_lib 1.8.13
差异见同目录 README.md。

用法(管理员无需,直接 python):
  python civitai_fetcher.py                        # 全量扫描(默认 webui 根目录)
  python civitai_fetcher.py --types lora           # 只扫 Lora
  python civitai_fetcher.py --limit 5              # 只处理前 5 个文件(试跑)
  python civitai_fetcher.py --no-names             # 输出中隐藏文件名
  python civitai_fetcher.py --dry-run              # 只统计不动网络
"""

import argparse
import hashlib
import json
import os
import re
import struct
import sys
import time

import requests
import urllib3

# ---------------------------------------------------------------- 常量(与插件对齐)

WEBUI_ROOT = r"H:\sd-webui-aki-v4.4"

EXTS = (".bin", ".pt", ".safetensors", ".ckpt", ".gguf", ".zip")
SUFFIX = ".civitai"
CIVITAI_EXT = ".info"
SDWEBUI_EXT = ".json"

# 插件 extensions 块写的就是它自己的版本号;沿用可让插件把我们的产物
# 视为"新格式"而不会重复重扫(COMPAT_VERSION_CIVITAI = 1.7.2)。
SHORT_NAME = "sd_civitai_helper"
EXT_VERSION = "1.8.13"
COMPAT_VERSION_CIVITAI = "1.7.2"

URLS = {
    "modelId": "https://civitai.com/api/v1/models/",
    "modelVersionId": "https://civitai.com/api/v1/model-versions/",
    "hash": "https://civitai.com/api/v1/model-versions/by-hash/",
}

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPad; CPU OS 12_2 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
    )
}

REQUEST_TIMEOUT = 300
MAX_RETRIES = 30
DELAY = 0.2  # 每次 API 请求间隔,防封

PREVIEW_EXTS = ["png", "jpg", "jpeg", "webp", "gif"]

# ---------------------------------------------------------------- 基础设施

report = []          # 每个模型的处理结果,落盘 last_scan_report.json
stats = {"scanned": 0, "info_written": 0, "skeleton": 0,
         "preview_new": 0, "preview_exists": 0, "preview_fail": 0,
         "hash_computed": 0, "hash_cached": 0, "hash_from_info": 0,
         "api_not_found": 0, "api_error": 0, "skipped": 0}


class Names:
    """--no-names 时把文件名从一切输出里抹掉"""

    def __init__(self, hide):
        self.hide = hide
        self.counter = 0

    def mask(self, model_path):
        if not self.hide:
            return model_path
        self.counter += 1
        rel = os.path.relpath(model_path, FOLDERS_ROOT) if FOLDERS_ROOT else model_path
        return f"{os.path.dirname(rel)}\\<#{self.counter}>"


def out(msg):
    OUT(msg)


# GUI 挂载点:输出重定向 / 停止开关 / 进度回调(供 civitai_fetcher 程序化调用)
OUT = lambda msg: print(msg, flush=True)
STOP_CHECK = None          # callable -> bool,每处理一个文件前询问
PROGRESS = None            # callable(done, total)


def report_progress(done, total):
    if PROGRESS:
        try:
            PROGRESS(done, total)
        except Exception:
            pass


def should_stop():
    try:
        return bool(STOP_CHECK()) if STOP_CHECK else False
    except Exception:
        return False


def init_stdout():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass


# ---------------------------------------------------------------- HTTP 层(复刻 downloader.request_get)

def make_session(proxy, api_key):
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    session.verify = False  # 与插件一致
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}
    else:
        # 与插件一致:显式禁用环境/系统代理,直连
        # (requests 在 Windows 会读注册表系统代理,Clash 关掉后所有请求都会撞死在 127.0.0.1)
        session.proxies = {"http": None, "https": None}
        session.trust_env = False
    if api_key:
        session.headers["Authorization"] = f"Bearer {api_key}"
    return session


SESSION = None
_SESSION_SIG = None


def ensure_session(proxy="", api_key=""):
    """浏览/下载/扫描共用的会话入口:按 (proxy, api_key) 签名复用或重建"""
    global SESSION, _SESSION_SIG
    sig = (proxy or "", api_key or "")
    if SESSION is None or _SESSION_SIG != sig:
        SESSION = make_session(proxy, api_key)
        _SESSION_SIG = sig
    return SESSION


def request_get(url, max_retries=MAX_RETRIES, timeout=REQUEST_TIMEOUT):
    """GET,404/401/403 不重试,其他失败按插件步退曲线重试。返回 (ok, response|错误码串)"""
    retries = 0
    while True:
        try:
            response = SESSION.get(url, stream=True, timeout=timeout)
        except requests.RequestException as e:
            if retries >= 3:
                return False, f"net_error: {e}"
            out(f"    网络异常,3s 后重试({retries + 1}/3): {type(e).__name__}")
            time.sleep(3)
            retries += 1
            continue

        if response.ok:
            return True, response

        code = response.status_code
        response.close()
        if code == 404:
            return False, "not_found"
        if code == 401:
            return False, "auth"
        if code == 403:
            return False, "forbidden"

        if retries >= max_retries:
            return False, f"http_{code}"

        delay = 3 + ((retries >> 1) ** 2)
        out(f"    HTTP {code},{delay}s 后重试({retries + 1}/{max_retries})")
        time.sleep(delay)
        retries += 1


STATUS_LABEL = {
    "not_found": "Civitai 上没有此模型 → 写骨架文件",
    "auth": "需要 Civitai API Key(--api-key 或环境变量 CIVITAI_API_KEY)→ 写骨架文件",
    "forbidden": "403 拒绝访问 → 写骨架文件",
}


def get_json(url, allow_404=False):
    ok, resp = request_get(url)
    if not ok:
        return None, resp if isinstance(resp, str) else "error"
    try:
        return resp.json(), "ok"
    except ValueError:
        return None, "error"
    finally:
        resp.close()


# ---------------------------------------------------------------- API 层(复刻 civitai.py)

def get_model_info_by_hash(sha256):
    """by-hash 反查版本信息,并合并父模型字段;404 返回 (None,'not_found')"""
    content, status = get_json(f'{URLS["hash"]}{sha256}', allow_404=True)
    if content is None:
        return None, status

    # append_parent_model_metadata
    parent, status2 = get_json(f'{URLS["modelId"]}{content.get("modelId", "")}', allow_404=True)
    if parent is None:
        parent = {}
    metadatas = ["description", "tags", "allowNoCredit",
                 "allowCommercialUse", "allowDerivatives", "allowDifferentLicense"]
    content["creator"] = parent.get("creator", "{}")
    model_metadata = content.get("model", {})
    for key in metadatas:
        model_metadata[key] = parent.get(key, "")

    return content, "ok"


def get_metadata_skeleton():
    return {
        "id": "", "modelId": "", "name": "", "trainedWords": [],
        "baseModel": "Unknown", "description": "",
        "model": {"name": "", "type": "", "nsfw": "", "poi": ""},
        "files": [{"name": "", "sizeKB": 0, "type": "Model",
                   "hashes": {"AutoV2": "", "SHA256": ""}}],
        "tags": [], "downloadUrl": "", "skeleton_file": True,
    }


def read_safetensors_metadata(path):
    """复刻 sd_models.read_metadata_from_safetensors(骨架文件取 ss_tag_frequency 用)"""
    if not path.lower().endswith(".safetensors"):
        return None
    with open(path, "rb") as f:
        length = struct.unpack("<Q", f.read(8))[0]
        if length > 100 * 1024 * 1024:
            return None
        header = json.loads(f.read(length))
    meta = header.get("__metadata__", {})
    for k, v in list(meta.items()):
        if isinstance(v, str) and v[:1] == "{":
            try:
                meta[k] = json.loads(v)
            except ValueError:
                pass
    return meta


def dummy_model_info(path, sha256):
    """模型不在 Civitai 上时,用本地信息造骨架(复刻 dummy_model_info)"""
    if not sha256:
        return {}
    info = get_metadata_skeleton()
    info["model"]["name"] = os.path.basename(path)
    info["model"]["type"] = "Model"
    file_meta = info["files"][0]
    file_meta["name"] = os.path.basename(path)
    file_meta["sizeKB"] = os.path.getsize(path) // 1024
    file_meta["hashes"]["SHA256"] = sha256
    file_meta["hashes"]["AutoV2"] = sha256[:10]

    meta = read_safetensors_metadata(path)
    if meta is None:
        return info

    tag_frequency = meta.get("ss_tag_frequency", {})
    prefix_re = re.compile(r"^\d+_")
    _kohya_junk = {"image_dir", "images", "img", "data"}
    if isinstance(tag_frequency, dict):
        for trained_word in tag_frequency.keys():
            word = prefix_re.sub("", trained_word).strip()
            if word.lower() in _kohya_junk or not word:
                continue  # kohya 默认目录名不是触发词
            info["trainedWords"].append(word)
            for tag in tag_frequency[trained_word].keys():
                tag = str(tag).replace(",", "").strip()
                if tag and tag not in info["tags"]:
                    info["tags"].append(tag)
    return info


# ---------------------------------------------------------------- 哈希层(全文件 SHA256 + 本地缓存)

HASH_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hash_cache.json")
_hash_cache = None


def load_hash_cache():
    global _hash_cache
    if _hash_cache is None:
        try:
            with open(HASH_CACHE_FILE, "r", encoding="utf-8") as f:
                _hash_cache = json.load(f)
        except (OSError, ValueError):
            _hash_cache = {}
    return _hash_cache


def save_hash_cache():
    tmp = HASH_CACHE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_hash_cache, f)
    os.replace(tmp, HASH_CACHE_FILE)


def sha256_from_info_file(info_file):
    """已有 .info 里记录的 SHA256(64位十六进制)可直接复用,免算大文件"""
    try:
        with open(info_file, "r", encoding="utf-8") as f:
            info = json.load(f)
        h = info["files"][0]["hashes"]["SHA256"]
        if isinstance(h, str) and re.fullmatch(r"[0-9a-fA-F]{64}", h):
            return h.lower()
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        pass
    return None


def gen_file_sha256(path):
    """优先:缓存(mtime+size) → 已有 .info 记录 → 现算(1MB 分块流式)"""
    cache = load_hash_cache()
    mtime = os.path.getmtime(path)
    size = os.path.getsize(path)
    entry = cache.get(path)
    if entry and entry.get("mtime") == mtime and entry.get("size") == size:
        stats["hash_cached"] += 1
        return entry["sha256"]

    info_file = f"{os.path.splitext(path)[0]}{SUFFIX}{CIVITAI_EXT}"
    seeded = sha256_from_info_file(info_file)
    if seeded:
        stats["hash_from_info"] += 1
        cache[path] = {"mtime": mtime, "size": size, "sha256": seeded}
        save_hash_cache()
        return seeded

    out(f"    计算全文件 SHA256({size / 1024 / 1024:.0f} MB)…")
    h = hashlib.sha256()
    with open(path, "rb") as f:
        last = time.time()
        while True:
            block = f.read(1 << 20)
            if not block:
                break
            h.update(block)
            if time.time() - last > 5:
                out(f"    …已读 {f.tell() / 1024 / 1024:.0f} MB")
                last = time.time()
    digest = h.hexdigest()
    stats["hash_computed"] += 1
    cache[path] = {"mtime": mtime, "size": size, "sha256": digest}
    save_hash_cache()
    return digest


# ---------------------------------------------------------------- 信息落盘(复刻 model.py)

def info_version(metadata):
    try:
        return metadata["extensions"][SHORT_NAME]["version"]
    except (KeyError, TypeError):
        return False


def newer_version(ver1, ver2):
    """ver1 > ver2(逐段数值比较,够用且不引包)"""

    def nums(v):
        return [int(x) for x in re.findall(r"\d+", str(v))]

    return nums(ver1) > nums(ver2)


COMPAT_VERSION_SDWEBUI = "1.8.0"


def metadata_needed(path, refetch_old, retry_skeleton=False, compat=COMPAT_VERSION_CIVITAI):
    if not os.path.isfile(path):
        return True
    if not refetch_old and not retry_skeleton:
        return False
    meta_version = info_version(load_json_file(path))
    if not meta_version:
        return True
    if retry_skeleton:
        try:
            with open(path, "r", encoding="utf-8") as f:
                if json.load(f).get("skeleton_file"):
                    return True
        except (OSError, ValueError):
            return True
    if refetch_old:
        return newer_version(compat, meta_version)
    return False


def load_json_file(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def write_json(data, path):
    with open(os.path.realpath(path), "w", encoding="utf-8") as f:
        f.write(json.dumps(data, indent=4))


def merge_write_json(new_data, path, owned_keys=None):
    """读-改-写合并:旧文件中不属于本次写入的键(用户手填内容、其他工具写入的
    自定义键)原样保留;新数据出现的键覆盖(嵌套 dict 浅合并,列表整体替换)。
    owned_keys 为本工具自有的键:新数据缺席时删除以维持一致性。
    格式可行性依据:JSON 开放键值 + 插件对未知键零校验(已实测全量读键枚举)。"""
    if not os.path.isfile(path):
        write_json(new_data, path)
        return
    old = load_json_file(path)
    if not isinstance(old, dict):
        write_json(new_data, path)
        return
    merged = dict(old)
    for k, v in new_data.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = {**merged[k], **v}
        else:
            merged[k] = v
    for k in (owned_keys or ()):
        if k not in new_data:
            merged.pop(k, None)
    write_json(merged, path)


def extension_block(data=None, skeleton=False):
    block = {SHORT_NAME: {"version": EXT_VERSION, "last_update": int(time.time()),
                          "skeleton_file": skeleton}}
    if not data:
        return block
    data[SHORT_NAME] = block[SHORT_NAME]
    return data


def overwrite_eligible(path, new_data):
    """refetch_old 时防止新数据盖掉有用的旧数据(复刻 verify_overwrite_eligibility)"""
    if not os.path.isfile(path):
        return True
    old = load_json_file(path)
    if "civitai" in path:
        if old.get("id", "") and new_data.get("id", "") != old.get("id", ""):
            return False
    if new_data.get("description", "") == "" and old.get("description", "") != "":
        return False
    return True


def sd_version_of(base_model):
    version = None
    if base_model:
        try:
            version = base_model[3]
        except IndexError:
            version = 0
    return {"1": "SD1", "2": "SD2", "L": "SDXL"}.get(version, "Unknown")


def write_sd15_info(sd15_file, model_info, parent, model_type, refetch_old):
    # 合并保护:绝不覆盖用户手写的触发词/笔记(旧值存在且新值无效时保留旧值)
    old = load_json_file(sd15_file) if os.path.isfile(sd15_file) else {}
    is_skeleton = bool(model_info.get("skeleton_file"))
    new_words = (model_info.get("trainedWords") or [])
    new_words = [w for w in new_words if str(w).strip()]

    sd_data = {"description": parent.get("description", "")}
    notes = model_info.get("description")
    if notes:  # 版本描述为空时不写键 → 合并保留旧值(可能是用户手填)
        sd_data["notes"] = notes
    sd_data["sd version"] = sd_version_of(model_info.get("baseModel", None))
    for filedata in model_info.get("files", []):
        if filedata.get("type") == "VAE":
            sd_data["vae"] = filedata.get("name")
    activator = new_words
    if activator and activator[0]:
        # 新数据无触发词时不设置此键 → 合并写入自动保留旧值(用户手填)
        if "," in activator[0]:
            sd_data["activation text"] = " || ".join(activator)
        else:
            sd_data["activation text"] = ", ".join(activator)
    if model_type in ["lora", "lycoris"]:
        sd_data["preferred weight"] = 0
    sd_data["extensions"] = extension_block(model_info.get("extensions", None),
                                            model_info.get("skeleton_file", False))
    # 缺席即删的只有 vae(新版本无 VAE 就该清掉);
    # activation text/notes 缺席时保留旧值(可能是用户手填),不进清单
    owned = ("vae",)
    if refetch_old:
        if not overwrite_eligible(sd15_file, sd_data):
            return
    merge_write_json(sd_data, sd15_file, owned_keys=owned)


def process_model_info(model_path, model_info, model_type, refetch_old, force=False):
    """写 .civitai.info + .json;force=True 时跳过 .info 的存在性检查(重试骨架)"""
    if model_info is None:
        return "error"

    info_file = f"{os.path.splitext(model_path)[0]}{SUFFIX}{CIVITAI_EXT}"
    sd15_file = f"{os.path.splitext(model_path)[0]}{SDWEBUI_EXT}"
    parent = model_info["model"]

    model_info["extensions"] = extension_block(model_info.get("extensions", None),
                                               model_info.get("skeleton_file", False))

    if metadata_needed(info_file, refetch_old) or force:
        if refetch_old and not overwrite_eligible(info_file, model_info):
            return "kept_old"
        # 合并写入:API 键更新,外来/自定义键保留;
        # skeleton_file 为自有标记:真数据落地时清除,骨架数据时写入
        merge_write_json(model_info, info_file, owned_keys=("skeleton_file",))

    write_sd15_info(sd15_file, model_info, parent, model_type, refetch_old)
    return "skeleton" if model_info.get("skeleton_file") else "written"


# ---------------------------------------------------------------- 缩略图(复刻 civitai.verify_preview,NSFW 不过滤)

def preview_paths(model_path):
    base = os.path.splitext(model_path)[0]
    paths = []
    for ext in PREVIEW_EXTS:
        paths.append(f"{base}.{ext}")
        paths.append(f"{base}.preview.{ext}")
    return paths


def download_preview(model_path, force=False):
    """有任意预览图则跳过(force 时覆盖);否则按 .info 里 images 顺序下载第一张
    type==image 的。与插件差异:不按 NSFW 等级过滤(用户要求)。"""
    if not force:
        existing = [p for p in preview_paths(model_path) if os.path.isfile(p)]
        if existing:
            stats["preview_exists"] += 1
            return "exists"

    info_file = f"{os.path.splitext(model_path)[0]}{SUFFIX}{CIVITAI_EXT}"
    if not os.path.isfile(info_file):
        return "no_info"

    images = load_json_file(info_file).get("images", [])
    if not isinstance(images, list):
        return "no_images"

    preview_path = f"{os.path.splitext(model_path)[0]}.preview.png"
    for img in images:
        url = img.get("url")
        if not url or img.get("type") != "image":
            continue

        tmp = preview_path + ".downloading"
        try:
            # 预览图用短超时少重试,失败跳下一张,避免卡住整个下载任务
            ok, resp = request_get(url, max_retries=1, timeout=30)
            if not ok:
                continue
            with open(tmp, "wb") as f:
                for chunk in resp.iter_content(chunk_size=256 * 1024):
                    if chunk:
                        f.write(chunk)
            resp.close()
            os.replace(tmp, preview_path)
            stats["preview_new"] += 1
            return "downloaded"
        except (OSError, requests.RequestException):
            if os.path.isfile(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            continue

    stats["preview_fail"] += 1
    return "fail"


# ---------------------------------------------------------------- 主流程

def build_folders(webui_root):
    return {
        "ti": os.path.join(webui_root, "embeddings"),
        "hyper": os.path.join(webui_root, "models", "hypernetworks"),
        "ckp": os.path.join(webui_root, "models", "Stable-diffusion"),
        "lora": os.path.join(webui_root, "models", "Lora"),
        "lycoris": os.path.join(webui_root, "models", "LyCORIS"),
        "vae": os.path.join(webui_root, "models", "VAE"),
    }


FOLDERS_ROOT = None


_local_index_cache = {"ts": 0.0, "data": None}


def local_model_index(webui_root, refresh=False):
    """扫描模型目录里的 .civitai.info,返回已入库的 modelId/versionIds(30s 缓存)"""
    now = time.time()
    if not refresh and _local_index_cache["data"] and now - _local_index_cache["ts"] < 30:
        return _local_index_cache["data"]
    folders = build_folders(webui_root)
    model_ids, version_ids = set(), set()
    for folder in folders.values():
        if not folder or not os.path.isdir(folder):
            continue
        for root, _, files in os.walk(folder):
            for fn in files:
                if not fn.endswith(SUFFIX + CIVITAI_EXT):
                    continue
                try:
                    with open(os.path.join(root, fn), "r", encoding="utf-8") as f:
                        info = json.load(f)
                except (OSError, ValueError):
                    continue
                if info.get("skeleton_file") or not info.get("modelId"):
                    continue
                model_ids.add(str(info["modelId"]))
                if info.get("id"):
                    version_ids.add(str(info["id"]))
    data = {"modelIds": sorted(model_ids), "versionIds": sorted(version_ids)}
    _local_index_cache.update(ts=now, data=data)
    return data


def collect_models(folders, types):
    models = []
    for model_type in types:
        folder = folders.get(model_type)
        if not folder or not os.path.isdir(folder):
            continue
        for root, _, files in os.walk(folder):
            for filename in files:
                if os.path.splitext(filename)[1].lower() in EXTS:
                    models.append((os.path.join(root, filename), model_type))
    return models


def scan_one(filepath, model_type, args, names):
    path = names.mask(filepath)
    info_file = f"{os.path.splitext(filepath)[0]}{SUFFIX}{CIVITAI_EXT}"
    entry = {"path": filepath, "type": model_type, "info": "skip", "preview": "-", "error": ""}

    need = (metadata_needed(info_file, args.refetch_old)
            or metadata_needed(f"{os.path.splitext(filepath)[0]}{SDWEBUI_EXT}",
                               args.refetch_old, compat=COMPAT_VERSION_SDWEBUI))
    if not need and args.retry_skeletons:
        need = metadata_needed(info_file, False, retry_skeleton=True)

    if need:
        out(f"  扫描 {path}")
        sha256 = gen_file_sha256(filepath)
        if not sha256:
            entry["error"] = "sha256 failed"
            return entry

        model_info, status = get_model_info_by_hash(sha256)
        time.sleep(DELAY)

        if model_info is None:
            if status == "not_found":
                stats["api_not_found"] += 1
            else:
                stats["api_error"] += 1
                entry["error"] = status
            out(f"    {STATUS_LABEL.get(status, f'请求失败({status}) → 写骨架文件(--retry-skeletons 可重试)')}")
            model_info = dummy_model_info(filepath, sha256)

        result = process_model_info(filepath, model_info, model_type, args.refetch_old,
                                    force=getattr(args, "retry_skeletons", False))
        entry["info"] = result
        if result == "skeleton":
            stats["skeleton"] += 1
        elif result == "written":
            stats["info_written"] += 1
        stats["scanned"] += 1
    else:
        entry["info"] = "skip"
        stats["skipped"] += 1

    entry["preview"] = download_preview(filepath, force=args.force_preview)
    return entry


def run_scan(args):
    """执行一次扫描(可被 CLI 或 GUI 调用);返回汇总 stats dict"""
    global FOLDERS_ROOT
    urllib3.disable_warnings()
    ensure_session(args.proxy, args.api_key)
    names = Names(args.no_names)

    folders = build_folders(args.webui_root)
    FOLDERS_ROOT = args.webui_root
    types = [t.strip() for t in args.types.split(",") if t.strip()]
    models = collect_models(folders, types)

    out(f"共发现 {len(models)} 个模型文件({', '.join(types)})")
    if args.dry_run:
        need_info = sum(
            1 for filepath, _ in models
            if metadata_needed(f"{os.path.splitext(filepath)[0]}{SUFFIX}{CIVITAI_EXT}", args.refetch_old)
            or metadata_needed(f"{os.path.splitext(filepath)[0]}{SDWEBUI_EXT}", args.refetch_old)
        )
        have_preview = 0
        for filepath, _ in models:
            if any(os.path.isfile(p) for p in preview_paths(filepath)):
                have_preview += 1
        out(f"待补信息: {need_info} 个;已有预览图: {have_preview} 个;缺预览图: {len(models) - have_preview} 个")
        return {"total": len(models), "need_info": need_info,
                "have_preview": have_preview, "no_preview": len(models) - have_preview}

    if args.limit or args.offset:
        models = models[args.offset: args.offset + args.limit if args.limit else None]

    start = time.time()
    done = 0
    for filepath, model_type in models:
        if should_stop():
            out("⏹ 已停止(本次进度已记入报告)")
            break
        done += 1
        # 跳过的模型逐个打日志会在全库已缓存时 1 秒灌入近千行,前端渲染雪崩;
        # 静默跳过,仅对有动作的模型打行,进度事件每 20 个发一次
        if done % 20 == 0 or done == len(models):
            report_progress(done, len(models))
        try:
            before = len(report)
            report.append(scan_one(filepath, model_type, args, names))
            entry = report[-1]
            if entry.get("info") != "skip":
                out(f"[{done}/{len(models)}] {model_type}")
        except Exception as e:  # 单模型失败不断整个扫描
            out(f"[{done}/{len(models)}] {model_type} 异常: {e!r}")
            report.append({"path": filepath, "type": model_type,
                           "info": "exception", "preview": "-", "error": repr(e)})

    report_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_scan_report.json")
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump({"finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "elapsed_sec": round(time.time() - start, 1),
                   "stats": stats, "items": report}, f, ensure_ascii=False, indent=1)

    out("\n===== 汇总 =====")
    for k, v in stats.items():
        out(f"{k}: {v}")
    out(f"耗时 {time.time() - start:.0f}s,明细见 {report_file}")
    return stats


def build_arg_parser():
    parser = argparse.ArgumentParser(description="Civitai Helper 扫描功能的独立复刻")
    parser.add_argument("--webui-root", default=WEBUI_ROOT, help="SD WebUI 根目录")
    parser.add_argument("--types", default="ckp,ti,hyper,lora,lycoris,vae",
                        help="逗号分隔: ckp,ti,hyper,lora,lycoris,vae")
    parser.add_argument("--refetch-old", action="store_true", help="重扫旧版本格式的 .info")
    parser.add_argument("--retry-skeletons", action="store_true", help="重试骨架文件(含请求失败的)")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 个文件(0=不限)")
    parser.add_argument("--offset", type=int, default=0, help="跳过前 N 个文件(断点续扫)")
    parser.add_argument("--no-names", action="store_true", help="输出中隐藏文件名")
    parser.add_argument("--proxy", default="", help="HTTP 代理,如 http://127.0.0.1:7890")
    parser.add_argument("--api-key", default=os.environ.get("CIVITAI_API_KEY", ""),
                        help="Civitai API Key(下载登录内容才需要)")
    parser.add_argument("--dry-run", action="store_true", help="只统计待处理数量,不发请求")
    parser.add_argument("--force-preview", action="store_true",
                        help="已有预览图也重新下载(覆盖 .preview.png)")
    return parser


def main():
    init_stdout()
    args = build_arg_parser().parse_args()
    run_scan(args)


if __name__ == "__main__":
    main()
