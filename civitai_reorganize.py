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


def _pinned_folders():
    """设置里的分类专属文件夹:{分类中文名: 绝对路径}"""
    import json as _json
    try:
        path = os.path.join(os.path.dirname(cf.HASH_CACHE_FILE), "gui_settings.json")
        with open(path, "r", encoding="utf-8") as f:
            data = _json.load(f) or {}
        return {k: v for k, v in (data.get("pinned_folders") or {}).items()
                if isinstance(v, str) and os.path.isdir(v)}
    except (OSError, ValueError):
        return {}


def _target_dir_for(category, base_root):
    """分类目标目录:绑定了专属文件夹 → 专属夹;否则 base_root/分类名(不新建目录)"""
    pinned = _pinned_folders()
    if category in pinned:
        return pinned[category], True
    return os.path.join(base_root, category), False


def _read_category(info_path):
    """返回 (folder_name|None, hit|None, has_info)"""
    try:
        with open(info_path, "r", encoding="utf-8") as f:
            info = json.load(f)
    except (OSError, ValueError):
        return None, None, False
    if info.get("skeleton_file"):
        force = info.get("forceCategory")
        if force and str(force).strip():
            return str(force).strip(), "强制分类", True
        return None, None, True
    model = info.get("model") or {}
    return cb.resolve_category(model.get("tags"), model.get("type") or "",
                               force=info.get("forceCategory"))


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
            cat_dir, _pinned = _target_dir_for(cat, folder)
            if os.path.normcase(os.path.realpath(root)) == os.path.normcase(os.path.realpath(cat_dir)):
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

    pinned = _pinned_folders()
    return {
        "planId": plan_id,
        "folder": folder,
        "pinnedUsed": {k: v for k, v in pinned.items()},
        "moves": [{"src": m["src"], "category": m["category"], "hit": m["hit"],
                   "targetDir": m["targetDir"],
                   "fileCount": len(m["files"]),
                   "fileNames": [os.path.basename(d) for _, d in m["files"]]}
                  for m in moves],
        "counts": {"moves": len(moves), "alreadyOk": already_ok,
                   "noInfo": len(no_info), "noHit": len(no_hit)},
    }


def scan_by_compat(base_key, levels, scan_root, target_folder):
    """按基模兼容性扫描:命中勾选等级的模型整组移入用户指定的待测文件夹。
    自练(骨架)/无信息/无法判定的自动排除。返回与 preview_reorganize 同构的 plan,
    移动/撤销复用 apply_reorganize / undo_last_reorganize。"""
    import civitai_compat as cx

    bases = cx.load_bases()
    if base_key not in bases:
        return {"error": "未知基准基模: " + base_key}
    if not target_folder or not os.path.isdir(target_folder):
        return {"error": "待测文件夹不存在,请先选择或创建"}
    scan_root = os.path.abspath(scan_root)
    if not os.path.isdir(scan_root):
        return {"error": "扫描目录不存在"}

    wanted = [lv for lv in levels if lv in cx.LEVEL_ZH]
    moves = []
    counts = {"moves": 0, "excluded_skeleton": 0, "excluded_unknown": 0, "by_level": {}}
    for dp, _, fns in os.walk(scan_root):
        for fn in fns:
            if os.path.splitext(fn)[1].lower() not in MODEL_EXTS:
                continue
            path = os.path.join(dp, fn)
            base = os.path.splitext(path)[0]
            info_path = base + ".civitai.info"
            try:
                with open(info_path, "r", encoding="utf-8") as f:
                    info = json.load(f)
            except (OSError, ValueError):
                counts["excluded_unknown"] += 1
                continue
            if info.get("skeleton_file"):
                counts["excluded_skeleton"] += 1
                continue
            real = ""
            for im in (info.get("images") or []):
                m = im.get("meta") or {}
                real = m.get("Model") or m.get("baseModel") or ""
                if real:
                    break
            level, basis = cx.judge(bases, base_key, real, info.get("baseModel"))
            if level == "unknown":
                counts["excluded_unknown"] += 1
                continue
            if level not in wanted:
                continue
            dst_base = os.path.join(target_folder, os.path.splitext(fn)[0])
            companions = [path] + _companion_files(base)
            file_pairs = [(p2, dst_base + p2[len(base):]) for p2 in companions]
            moves.append({
                "src": path,
                "category": cx.LEVEL_ZH.get(level, level),
                "hit": "实测:" + (real[:24] or "-") + " / 标注:" + str(info.get("baseModel") or "-")[:14],
                "targetDir": target_folder, "files": file_pairs,
            })
            counts["by_level"][level] = counts["by_level"].get(level, 0) + 1

    plan_id = str(int(time.time() * 1000)) + "c"
    _plans[plan_id] = {"folder": scan_root, "moves": moves, "created": time.time()}
    counts["moves"] = len(moves)
    return {
        "planId": plan_id, "folder": scan_root,
        "moves": [{"src": m["src"], "category": m["category"], "hit": m["hit"],
                   "targetDir": m["targetDir"], "fileCount": len(m["files"]),
                   "fileNames": [os.path.basename(d) for _, d in m["files"]]}
                  for m in moves],
        "counts": counts,
    }


def preview_reorganize_pinned():
    """重整所有专属文件夹:把专属夹里的模型按当前元数据重新归位
    (规则变了/绑定了新夹后,把放错的挪到正确专属夹)。"""
    pinned = _pinned_folders()
    if not pinned:
        return {"error": "尚未绑定任何分类专属文件夹(设置 → 分类专属文件夹)"}

    all_moves = []
    seen_paths = set()
    no_info, no_hit, already_ok = [], [], 0
    for root in pinned.values():
        for dp, _, fns in os.walk(root):
            for fn in fns:
                if os.path.splitext(fn)[1].lower() not in MODEL_EXTS:
                    continue
                path = os.path.join(dp, fn)
                key = os.path.normcase(os.path.realpath(path))
                if key in seen_paths:
                    continue
                seen_paths.add(key)
                base = os.path.splitext(path)[0]
                info_path = base + ".civitai.info"
                cat, hit = _read_category(info_path)[:2]
                if not os.path.isfile(info_path):
                    no_info.append(path)
                    continue
                if cat is None:
                    no_hit.append(path)
                    continue
                cat_dir, _ = _target_dir_for(cat, root)
                if os.path.normcase(os.path.realpath(dp)) == os.path.normcase(os.path.realpath(cat_dir)):
                    already_ok += 1
                    continue
                dst_base = os.path.join(cat_dir, os.path.splitext(fn)[0])
                companions = [path] + _companion_files(base)
                file_pairs = [(p2, dst_base + p2[len(base):]) for p2 in companions]
                all_moves.append({"src": path, "category": cat, "hit": hit or "",
                                  "targetDir": cat_dir, "files": file_pairs})

    plan_id = str(int(time.time() * 1000)) + "p"
    _plans[plan_id] = {"folder": "专属文件夹集合", "moves": all_moves, "created": time.time()}
    return {
        "planId": plan_id, "folder": "专属文件夹集合", "pinnedUsed": pinned,
        "moves": [{"src": m["src"], "category": m["category"], "hit": m["hit"],
                   "targetDir": m["targetDir"], "fileCount": len(m["files"]),
                   "fileNames": [os.path.basename(d) for _, d in m["files"]]}
                  for m in all_moves],
        "counts": {"moves": len(all_moves), "alreadyOk": already_ok,
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
