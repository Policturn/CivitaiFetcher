#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地模型按 Civitai 大类重新归位:
  预览(dry-run)→ 确认执行 → 撤销清单。
移动单位 = 模型文件 + 全部伴生文件(.civitai.info/.json/预览图等)。
归类依据 = .info 的 model.tags/model.type(civitai_browser.resolve_category)。
"""

import json
import os
import time

import civitai_fetcher as cf
import civitai_browser as cb

# 模型的伴生文件后缀(相对模型 base 路径)
COMPANION_SUFFIXES = [".civitai.info", ".json", ".preview.png", ".preview.jpg",
                      ".preview.jpeg", ".preview.webp", ".preview.gif",
                      ".png", ".jpg", ".jpeg", ".webp", ".gif",
                      ".txt", ".vae.pt"]
MODEL_EXTS = (".bin", ".pt", ".safetensors", ".ckpt", ".gguf", ".zip")

_plans = {}       # plan_id -> {"folder":…, "moves":[{src,dst,category,base}], "created":ts}
_undo_stack = []  # [{plan_id, moves:[{src,dst}]}] 最近的在上


def _companion_files(base):
    files = []
    for suffix in COMPANION_SUFFIXES:
        p = base + suffix
        if os.path.isfile(p):
            files.append(p)
    dl = base + ".downloading"
    if os.path.isfile(dl):
        files.append(dl)
    return files


def _read_category(info_path):
    """返回 (folder_name|None, hit|None, has_info)"""
    try:
        with open(info_path, "r", encoding="utf-8") as f:
            info = json.load(f)
    except (OSError, ValueError):
        return None, None, False
    if info.get("skeleton_file"):
        return None, None, True
    model = info.get("model") or {}
    return cb.resolve_category(model.get("tags"), model.get("type") or "")


def preview_reorganize(folder):
    """扫描文件夹(一层内递归),返回移动计划(不执行)"""
    folder = os.path.abspath(folder)
    if not os.path.isdir(folder):
        return {"error": "目录不存在"}

    moves = []
    no_info, already_ok, no_hit = [], 0, []
    seen_ids = set()
    for root, _, files in os.walk(folder):
        for fn in files:
            if os.path.splitext(fn)[1].lower() not in MODEL_EXTS:
                continue
            path = os.path.join(root, fn)
            base = os.path.splitext(path)[0]
            info_path = base + ".civitai.info"
            cat, hit = _read_category(info_path)[:2]
            has_info = os.path.isfile(info_path)
            if not has_info:
                no_info.append(path)
                continue
            if cat is None:
                no_hit.append(path)
                continue
            cat_dir = os.path.join(folder, cat)
            if os.path.normcase(root) == os.path.normcase(cat_dir):
                already_ok += 1
                continue
            base_name = os.path.splitext(fn)[0]
            dst_base = os.path.join(cat_dir, base_name)
            companions = [path] + _companion_files(base)
            # 伴生文件保持各自相对后缀,一一对应到新目录
            file_pairs = [(p, dst_base + p[len(base):]) for p in companions]
            moves.append({
                "src": path, "category": cat, "hit": hit or "",
                "targetDir": cat_dir,
                "files": file_pairs,
            })

    plan_id = str(int(time.time() * 1000))
    _plans[plan_id] = {"folder": folder, "moves": moves, "created": time.time()}
    # 只保留最近 5 个计划
    for old in sorted(list(_plans.keys()))[:-5]:
        _plans.pop(old, None)

    return {
        "planId": plan_id,
        "folder": folder,
        "moves": [{"src": m["src"], "category": m["category"], "hit": m["hit"],
                   "targetDir": m["targetDir"],
                   "fileCount": len(m["files"]),
                   "fileNames": [os.path.basename(d) for _, d in m["files"]]}
                  for m in moves],
        "counts": {"moves": len(moves), "alreadyOk": already_ok,
                   "noInfo": len(no_info), "noHit": len(no_hit)},
    }


def apply_reorganize(plan_id, emit=lambda d: None):
    plan = _plans.get(plan_id)
    if not plan:
        return {"error": "计划不存在或已过期,请重新预览"}
    executed, skipped, errors = 0, 0, []
    record = {"planId": plan_id, "folder": plan["folder"],
              "created": time.time(), "moves": []}
    for i, m in enumerate(plan["moves"]):
        emit({"type": "reorg_progress", "done": i, "total": len(plan["moves"])})
        moved = []
        try:
            for src, dst in m["files"]:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                if os.path.exists(dst):
                    raise FileExistsError(os.path.basename(dst))
            for src, dst in m["files"]:
                os.rename(src, dst)
                moved.append({"src": src, "dst": dst})
            record["moves"].extend(moved)
            executed += 1
        except Exception as e:
            for mv in reversed(moved):  # 回滚该模型已移动的伴生文件
                try:
                    os.rename(mv["dst"], mv["src"])
                except OSError:
                    pass
            errors.append(f"{os.path.basename(m['src'])}: {e!r}")
            skipped += 1
    if record["moves"]:
        _undo_stack.insert(0, record)
        _save_undo_log(record)
    emit({"type": "reorg_done", "executed": executed, "skipped": skipped,
          "errors": errors[:20]})
    return {"executed": executed, "skipped": skipped, "errors": errors[:20]}


def undo_last_reorganize(emit=lambda d: None):
    if not _undo_stack:
        _load_undo_log()
    if not _undo_stack:
        return {"error": "没有可撤销的重分类记录"}
    record = _undo_stack.pop(0)
    restored, errors = 0, []
    total = len(record["moves"])
    for i, mv in enumerate(record["moves"]):
        emit({"type": "undo_progress", "done": i, "total": total})
        try:
            if os.path.isfile(mv["dst"]):
                os.makedirs(os.path.dirname(mv["src"]), exist_ok=True)
                os.rename(mv["dst"], mv["src"])
                restored += 1
                try:
                    os.rmdir(os.path.dirname(mv["dst"]))  # 清掉搬空的大类目录
                except OSError:
                    pass
        except Exception as e:
            errors.append(f"{os.path.basename(mv['dst'])}: {e!r}")
    _save_undo_stack()
    emit({"type": "undo_done", "restored": restored, "errors": errors[:20]})
    return {"restored": restored, "errors": errors[:20]}


def has_undo():
    if not _undo_stack:
        _load_undo_log()
    return bool(_undo_stack)


# ---------------------------------------------------------------- 撤销持久化

def _undo_log_path():
    return os.path.join(os.path.dirname(cf.HASH_CACHE_FILE), "reorganize_undo.json")


def _save_undo_log(record):
    _undo_stack.insert(0, record)
    _save_undo_stack()


def _save_undo_stack():
    try:
        with open(_undo_log_path(), "w", encoding="utf-8") as f:
            json.dump(_undo_stack[:10], f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def _load_undo_log():
    try:
        with open(_undo_log_path(), "r", encoding="utf-8") as f:
            _undo_stack.extend(json.load(f))
    except (OSError, ValueError):
        pass
