#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
演示后端:离线复现 browse/detail/thumbnail/download 的完整行为。
数据来自内置快照(demo-assets/demo_data.json,采集自真实 Civitai API);
下载演示 = 模拟进度事件 + 把内置三件套真实复制到目标目录(产物与真实下载一致)。
所有接口与 civitai_browser / civitai_downloader 同构,fetcher_app 按模式分流。
"""

import json
import os
import random
import shutil
import threading
import time

import civitai_fetcher as cf
import civitai_browser as cb
import civitai_downloader as cd

_DATA = None


def _bundled_assets():
    import sys
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "demo-assets")


def load():
    global _DATA
    if _DATA is None:
        with open(os.path.join(_bundled_assets(), "demo_data.json"), "r", encoding="utf-8") as f:
            _DATA = json.load(f)
    return _DATA


def is_available():
    return os.path.isfile(os.path.join(_bundled_assets(), "demo_data.json"))


# ---------------------------------------------------------------- 浏览(过滤 + cursor 翻页)

def _match_query(q, opts):
    types = opts.get("types") or []
    if types:
        want = {t.lower() for t in types}
        if q.get("types") and not ({t.lower() for t in q["types"]} & want):
            return False
    bases = opts.get("baseModels") or []
    if bases:
        want = {b.lower() for b in bases}
        if q.get("baseModels") and not ({b.lower() for b in q["baseModels"]} & want):
            return False
    return True


def search_models(opts):
    time.sleep(0.35)  # 模拟网络延迟,演示真实节奏
    _synth_all()
    data = load()
    merged, cursors = [], []
    for block in data["searches"]:
        if _match_query(block["query"], opts):
            merged.extend(block["items"])
            cursors.append((block["cursor"], block["nextCursor"], len(block["items"])))

    # 自定义时间段:按版本发布日过滤(演示数据本地执行)
    sd, ed = opts.get("startDate"), opts.get("endDate")
    if sd or ed:
        sd_s = time.strftime("%Y-%m-%d", time.gmtime(int(sd))) if sd else ""
        ed_s = time.strftime("%Y-%m-%d", time.gmtime(int(ed))) if ed else "9999-99-99"

        def _in_range(i):
            c = ((i.get("version") or {}).get("createdAt") or "")
            return (not sd_s or not c or c >= sd_s) and (not ed or not c or c <= ed_s)
        merged = [i for i in merged if _in_range(i)]

    query = (opts.get("query") or "").lower()
    tag = (opts.get("tag") or "").lower()
    if query:
        merged = [i for i in merged if query in i["name"].lower()
                  or query in (i["creator"] or "").lower()]
    if tag:
        merged = [i for i in merged if tag in [t.lower() for t in i.get("tags", [])]
                  or tag in i["name"].lower()]

    # 去重后按 cursor 翻页(limit 一页)
    seen, items = set(), []
    for i in merged:
        if i["id"] not in seen:
            seen.add(i["id"])
            items.append(i)
    sort = opts.get("sort") or "Most Downloaded"
    if sort in ("Most Downloaded",):
        items.sort(key=lambda i: -(i.get("downloads") or 0))
    elif sort in ("Most Liked", "Highest Rated"):
        items.sort(key=lambda i: -(i.get("thumbsUp") or 0))
    elif sort == "Newest":
        items.sort(key=lambda i: (i.get("version") or {}).get("createdAt") or "", reverse=True)
    elif sort == "Oldest":
        items.sort(key=lambda i: (i.get("version") or {}).get("createdAt") or "")
    limit = max(1, min(int(opts.get("limit") or 20), 100))
    cur = opts.get("cursor")
    if cur:
        try:
            start = int(cur)
        except (TypeError, ValueError):
            start = 0
    else:
        start = 0
    page = items[start:start + limit]
    return {"items": page, "nextCursor": str(start + limit) if start + limit < len(items) else None}


def get_model_detail(model_id, api_key=""):
    time.sleep(0.3)
    _synth_all()
    data = load()
    for d in data["details"]:
        if str(d["id"]) == str(model_id):
            return d
    # 快照外:从卡片(含合成卡)生成详情,保证任何卡都能打开
    it = None
    for blk in data["searches"]:
        for c in blk["items"]:
            if str(c["id"]) == str(model_id):
                it = c
                break
        if it:
            break
    if not it:
        raise RuntimeError("not_found")
    v = dict(it.get("version") or {})
    files = [{"name": it["name"] + ".safetensors",
              "sizeKB": 36000 + (abs(int(it["id"])) % 90) * 300,
              "type": "Model", "primary": True, "downloadUrl": ""}]
    if it["type"] == "Checkpoint":
        files.append({"name": it["name"] + ".vae.pt", "sizeKB": 330000,
                      "type": "VAE", "primary": False, "downloadUrl": ""})
    ver = {
        "id": v.get("id"), "name": v.get("name") or "v1.0",
        "baseModel": v.get("baseModel"), "trainedWords": v.get("trainedWords") or [],
        "images": v.get("images") or [],
        "downloadUrl": "", "createdAt": v.get("createdAt") or "2026-08-01",
        "files": files,
        "description": f"「{it['name']}」演示版本说明:推荐权重 0.7-0.9,配合底模 {v.get('baseModel') or 'SD 1.5'} 使用效果最佳。",
        "descriptionHtml": f"<p><b>{it['name']}</b> 是演示数据合成的模型条目。</p>"
                           f"<p>推荐权重 <b>0.7-0.9</b>;底模 <i>{v.get('baseModel') or 'SD 1.5'}</i>。</p>",
    }
    return {
        "id": it["id"], "name": it["name"], "type": it["type"],
        "creator": it.get("creator", ""), "tags": it.get("tags") or [],
        "description": f"{it['name']} —— 演示条目:由快照卡片合成的完整详情,字段结构与真实 API 一致。",
        "descriptionHtml": f"<h3>{it['name']}</h3><p>演示条目说明。作者 @{it.get('creator','')} 倾力制作,"
                            f"标签:{'、'.join((it.get('tags') or [])[:5])}。</p>"
                            f"<p>下载量 {it.get('downloads', 0):,} · 点赞 {it.get('thumbsUp', 0):,}。</p>",
        "downloads": it.get("downloads", 0), "thumbsUp": it.get("thumbsUp", 0),
        "versions": [ver],
    }


def get_thumbnails(urls, width=450):
    time.sleep(0.15)
    data = load()
    mapping = data.get("thumbFiles") or {}
    out = {}
    for u in urls:
        fname = mapping.get(u)
        if fname:
            out[u] = os.path.join(_bundled_assets(), "thumbs", fname)
    return out


# ---------------------------------------------------------------- 演示下载(模拟进度 + 真实落盘)

def enqueue_download(opts):
    data = load()
    pick = None
    for d in data["demoDownloads"]:
        if str(d["modelId"]) == str(opts.get("modelId")) and \
           str(d["versionId"]) == str(opts.get("versionId")):
            pick = d
            break
    # 快照外/详情外的一律走"模拟进度 + 不落盘"分支(pick2 = None 但仍演示)
    task = {
        "id": f"{int(time.time() * 1000)}",
        "modelId": opts.get("modelId"), "versionId": opts.get("versionId"),
        "targetRoot": opts.get("targetRoot"), "subfolder": opts.get("subfolder", ""),
        "autoCategory": bool(opts.get("autoCategory", True)),
        "api_key": "", "proxy": "",
        "status": "queued", "percent": 0, "speed": 0,
        "downloadedMB": 0, "totalMB": 0, "error": "", "category": "",
        "modelName": "演示模型", "versionName": "", "type": "LORA",
        "targetDir": "", "cancel": False, "paused": False,
        "_opts": dict(opts),
    }
    detail = next((d for d in data["details"] if str(d["id"]) == str(opts.get("modelId"))), None)
    if detail:
        task["modelName"] = detail["name"]
        ver = next((v for v in detail["versions"] if str(v["id"]) == str(opts.get("versionId"))), None)
        if ver:
            task["versionName"] = ver["name"]
            prim = [f for f in (ver.get("files") or []) if f.get("primary")] or (ver.get("files") or [])[:1]
            if prim:
                task["totalMB"] = round((prim[0].get("sizeKB") or 0) / 1024, 1)

    threading.Thread(target=_simulate, args=(task, pick, detail), daemon=True).start()
    cd._emit_tasks("enqueued")
    return task["id"]


def _simulate(task, pick, detail):
    cd._DL_STATE["active"] = task
    task["status"] = "fetching"
    cd._emit_tasks("fetching")
    time.sleep(0.8)

    target_dir = task["targetRoot"] or os.path.join(cf.WEBUI_ROOT, "models")
    target_dir = os.path.join(target_dir, "Lora")
    if task["subfolder"]:
        target_dir = os.path.join(target_dir, task["subfolder"].strip("/\\ "))
    if task["autoCategory"] and detail:
        cat, hit = cb.resolve_category(detail.get("tags"), detail.get("type"))
        if cat:
            task["category"] = cat
            target_dir = os.path.join(target_dir, cat)
    task["targetDir"] = target_dir
    if not task["totalMB"]:
        task["totalMB"] = round(random.uniform(50, 200), 1)

    os.makedirs(target_dir, exist_ok=True)
    task["status"] = "downloading"
    step = 0
    while step < 100:
        if task.get("cancel"):
            task["status"] = "cancelled"
            cd._finish(task)
            return
        while task.get("paused"):
            task["status"] = "paused"
            time.sleep(0.3)
        task["status"] = "downloading"
        time.sleep(0.12)
        step = min(100, step + random.randint(6, 14))
        task["percent"] = step
        task["downloadedMB"] = round(task["totalMB"] * step / 100, 1)
        task["speed"] = round(random.uniform(4, 18), 1)
        cd._emit({"type": "download_progress", "task": cd._public_task(task)})

    # 完成:把内置三件套真实复制到目标目录(演示产物 = 真实产物)
    copied = 0
    if pick:
        src_root = os.path.join(_bundled_assets(), "models")
        for dp, _, fns in os.walk(src_root):
            for fn in fns:
                src = os.path.join(dp, fn)
                dst = os.path.join(target_dir, os.path.basename(fn))
                if os.path.isfile(dst):
                    continue
                shutil.copy2(src, dst)
                copied += 1
    task["status"] = "done"
    task["error"] = f"(演示)已落盘 {copied} 个文件" if copied else "(演示)进度模拟完成"
    cd._finish(task)


# ---------------------------------------------------------------- 其余页面的演示数据(纯显示,不动文件)

_SCAN_SEED = None


def _seeded_names():
    """从快照取一批稳定的模型名(演示用)"""
    global _SCAN_SEED
    if _SCAN_SEED is None:
        names = []
        for blk in load()["searches"][:2]:
            for it in blk["items"]:
                names.append((it["name"], (it.get("tags") or ["style"])[0]))
        _SCAN_SEED = names[:36]
    return _SCAN_SEED


def local_model_index(webui_root=None, refresh=False):
    """演示'已入库'索引:快照前 1/3 视为本地已有 → 浏览页出现已下载弱化卡片"""
    names = _seeded_names()
    data = load()
    model_ids, version_ids = set(), set()
    for blk in data["searches"][:2]:
        for it in blk["items"]:
            if len(model_ids) >= max(6, len(names) // 3):
                break
            model_ids.add(str(it["id"]))
            if it.get("version", {}).get("id"):
                version_ids.add(str(it["version"]["id"]))
    return {"modelIds": sorted(model_ids), "versionIds": sorted(version_ids)}


def run_scan(args, emit):
    """演示扫描:逐行日志 + 进度 + 统计卡数字,约 8 秒走完,不读任何磁盘"""
    import random as _r
    names = _seeded_names()
    total = len(names)
    cf.stats.update({"scanned": 0, "info_written": 0, "skeleton": 0, "preview_new": 0,
                     "preview_fail": 0, "skipped": 0, "api_error": 0,
                     "hash_computed": 0, "hash_cached": total, "api_not_found": 0})
    emit(f"◆ 演示扫描 · {getattr(args, 'types', 'lora,lycoris')} · {getattr(args, 'webui_root', '')}")
    emit(f"共发现 {total} 个模型文件(演示数据)")
    written = 0
    for i, (name, tag) in enumerate(names, 1):
        time.sleep(0.18)
        emit(f"[{i}/{total}] lora")
        roll = (i * 7) % 10
        if roll < 5:
            emit("    已有缓存,跳过")
            cf.stats["skipped"] += 1
        elif roll < 8:
            emit(f"  扫描 models\Lora\{name}.safetensors")
            cf.stats["scanned"] += 1
            cf.stats["info_written"] += 1
            cf.stats["preview_new"] += 1
            written += 1
        elif roll < 9:
            emit("    Civitai 上没有此模型 → 写骨架文件")
            cf.stats["skeleton"] += 1
        else:
            emit(f"  扫描 models\Lora\{name}.safetensors")
            cf.stats["scanned"] += 1
            cf.stats["info_written"] += 1
        cf.PROGRESS(i, total)
    emit("\n===== 汇总 =====")
    for k in ("scanned", "info_written", "skeleton", "preview_new", "skipped"):
        emit(f"{k}: {cf.stats[k]}")
    emit(f"耗时 {total * 0.2:.0f}s(演示)")
    return dict(cf.stats)


_PLANS = {}


def preview_reorganize(folder, fast=False):
    if not fast:
        time.sleep(0.5)
    names = _seeded_names()
    folder = folder or r"C:\SD-WebUI\models\Lora(演示)"
    moves = []
    for i, (name, tag) in enumerate(names):
        cat, hit = cb.resolve_category([tag], "LORA")
        cat = cat or "未分类"
        moves.append({"src": os.path.join(folder, name + ".safetensors"),
                      "category": cat, "hit": hit or tag,
                      "targetDir": os.path.join(folder, cat),
                      "fileCount": 4,
                      "fileNames": [name + ".safetensors", name + ".civitai.info",
                                    name + ".json", name + ".preview.png"]})
    plan_id = str(int(time.time() * 1000))
    _PLANS[plan_id] = len(moves)
    return {"planId": plan_id, "folder": folder, "moves": moves,
            "counts": {"moves": len(moves), "alreadyOk": 5, "noInfo": 3, "noHit": 4}}


def apply_reorganize(plan_id, emit=lambda d: None):
    n = _PLANS.get(plan_id)
    if n is None:
        return {"error": "计划不存在或已过期,请重新预览"}
    for i in range(n):
        time.sleep(0.1)
        emit({"type": "reorg_progress", "done": i + 1, "total": n})
    emit({"type": "reorg_done", "executed": n, "skipped": 0, "errors": []})
    _UNDO["count"] = n * 4
    return {"executed": n, "skipped": 0, "errors": []}


_UNDO = {"count": 0}


def undo_last_reorganize(emit=lambda d: None):
    n = _UNDO.get("count") or 16
    for i in range(0, n, 4):
        time.sleep(0.1)
        emit({"type": "undo_progress", "done": i + 4, "total": n})
    emit({"type": "undo_done", "restored": n, "errors": []})
    return {"restored": n, "errors": []}


def has_undo():
    return True


def downloads_state(state):
    """预置演示下载历史(仅当真实历史为空时),让下载页一打开就有内容"""
    if state.get("done"):
        return state
    names = _seeded_names()[:4]
    statuses = ["done", "done", "failed", "done"]
    errors = ["", "", "需要 Civitai API Key(登录墙)", ""]
    out = []
    for i, (name, tag) in enumerate(names):
        out.append({"id": f"demo{i}", "modelName": name, "versionName": "v1.0",
                    "type": "LORA", "targetDir": os.path.join("C:\SD-WebUI\models\Lora", "画风" if i % 2 else "角色"),
                    "status": statuses[i], "percent": 100, "speed": 0,
                    "downloadedMB": 120 + i * 30, "totalMB": 120 + i * 30,
                    "error": errors[i], "category": "画风 (style)" if i % 2 else "角色 (character)",
                    "paused": False})
    state["done"] = out
    return state


# ---------------------------------------------------------------- 合成数据:填满所有类别,任何卡片可开详情

_SYNTH = {"done": False}

_ADJ = ["量子", "星辰", "幻梦", "极光", "暗夜", "翡翠", "绯樱", "琥珀", "霓虹", "银河",
        "薄暮", "晨曦", "深渊", "霜白", "苍穹", "流光", "失落", "共鸣", "缠绕", "低语"]
_STYLE_NOUNS = ["水彩画风", "油画质感", "厚涂插画", "浮世绘风", "赛博朋克", "蒸汽波",
                "极简线条", "水墨渲染", "吉卜力风", "新艺术风"]
_CHAR_NOUNS = ["剑士", "歌姬", "魔女", "侦探", "偶像", "机甲驾驶员", "僧侣", "舞者",
               "炼金术士", "太空人"]
_CLOTH_NOUNS = ["水手服", "旗袍", "哥特长裙", "宇航服", "和服浴衣", "学院制服",
                "铠甲礼服", "雨衣", "婚礼白纱", "冬装大衣"]
_SCENE_NOUNS = ["雨夜街景", "花海原野", "废土荒漠", "海底神殿", "云端之城", "竹林小径",
                "极地冰原", "黄昏车站", "图书馆", "温泉旅店"]
_CREATORS = ["AI_NekoStudio", "pixel_witch", "SakuraLabs", "cyber_ink", "mirage_ai",
             "kanata_design", "虚白工坊", "grid painter", "夜行灯", "mo_art"]

_SYNTH_TYPE_NOUNS = {
    "Checkpoint": ["底模", "融合底模", "写实底模", "动漫底模", "高对比底模"],
    "TextualInversion": ["嵌入", "风格嵌入", "角色嵌入"],
    "Hypernetwork": ["超网络", "风格网络"],
    "VAE": ["修色彩VAE", "高细节VAE", "动漫VAE"],
    "Upscaler": ["放大器", "细节放大", "4x超分"],
    "MotionModel": ["动作模型", "镜头运动", "运镜模组"],
    "Controlnet": ["线稿控制", "姿态控制", "深度控制", "上色控制"],
}
_SYNTH_TYPE_TAG = {
    "Checkpoint": ["base model"], "TextualInversion": ["concept"],
    "Hypernetwork": ["style"], "VAE": ["tool"], "Upscaler": ["tool"],
    "MotionModel": ["action"], "Controlnet": ["tool"],
}
_SUB_TAG_NOUNS = {
    "character": _CHAR_NOUNS, "clothing": _CLOTH_NOUNS, "style": _STYLE_NOUNS,
    "concept": _SCENE_NOUNS, "poses": ["站姿", "坐姿", "回眸", "战斗姿态", "舞蹈姿势"],
    "celebrity": ["歌手脸", "演员脸", "偶像脸"],
    "background": ["背景", "场景", "天空盒"], "animal": ["猫", "犬", "龙", "凤凰"],
    "objects": ["道具", "武器", "载具", "乐器"],
}


def _synth_all():
    """补齐所有 类型×底模×Lora次级分类 的演示卡片(稳定伪随机,不落盘)"""
    if _SYNTH["done"]:
        return
    _SYNTH["done"] = True
    data = load()
    img_urls = list((data.get("thumbFiles") or {}).keys())
    if not img_urls:
        return
    import random as _r
    have = {(tuple(q.get("types") or []), tuple(q.get("baseModels") or []), q.get("tag"))
            for q in (b["query"] for b in data["searches"])}
    rng = _r.Random(20260914)
    next_id = [-100000]

    def card(civ_type, base, tags, noun):
        nonlocal_dummy = None
        adj = rng.choice(_ADJ)
        name = f"{adj}{noun}"
        creator = rng.choice(_CREATORS)
        mid = next_id[0]; next_id[0] -= 1
        vid = next_id[0]; next_id[0] -= 1
        url = img_urls[abs(vid) % len(img_urls)]
        return {
            "id": mid, "name": name, "type": civ_type,
            "creator": creator,
            "downloads": rng.randint(500, 90000),
            "thumbsUp": rng.randint(50, 9000),
            "tags": tags, "nsfw": False,
            "version": {
                "id": vid, "name": "v1.0",
                "baseModel": base or rng.choice(["SD 1.5", "SDXL 1.0", "Illustrious"]),
                "trainedWords": [name.split(" ")[0][:12]] if rng.random() < 0.6 else [],
                "images": [{"url": url, "nsfwLevel": 1, "width": 832, "height": 1216}],
                "downloadUrl": "", "createdAt": f"2026-0{rng.randint(1,9)}-{rng.randint(10,28)}",
            },
        }

    def add_block(types, bases, cards, tag=None):
        key = (tuple(types), tuple(bases), tag)
        if key in have or not cards:
            return
        have.add(key)
        data["searches"].append({
            "query": {"types": list(types), "baseModels": list(bases), "tag": tag,
                      "sort": "Most Downloaded", "period": "AllTime"},
            "cursor": None, "items": cards, "nextCursor": None,
        })

    # 每类型一组通用卡
    for civ_type, nouns in _SYNTH_TYPE_NOUNS.items():
        cards = []
        for i in range(24):
            noun = nouns[i % len(nouns)]
            base = rng.choice(["SD 1.5", "SDXL 1.0", "Pony", "Illustrious", "Flux.1 D"])
            cards.append(card(civ_type, base, list(_SYNTH_TYPE_TAG[civ_type]), noun))
        add_block([civ_type], [], cards)

    # Lora 各底模补块
    for base in ["Pony", "NoobAI", "SD 3.5", "SD 3.5 Medium", "SD 3.5 Large", "Flux.1 D", "Flux.1 S"]:
        cards = []
        for i in range(24):
            noun = _STYLE_NOUNS[i % len(_STYLE_NOUNS)]
            cards.append(card("LORA", base, ["style"], noun))
        add_block(["LORA", "LoCon"], [base], cards)

    # Lora 次级分类(tags 单复数都带,匹配 tab 的单数 value)
    for tag_key, nouns in _SUB_TAG_NOUNS.items():
        cards = []
        for i in range(18):
            noun = nouns[i % len(nouns)]
            tags = [tag_key, tag_key.rstrip("s")] if tag_key.endswith("s") else [tag_key, tag_key + "s"]
            cards.append(card("LORA", rng.choice(["SD 1.5", "Illustrious", "SDXL 1.0"]), tags, noun))
        add_block(["LORA", "LoCon"], [], cards, tag=tag_key)
