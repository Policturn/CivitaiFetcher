#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基模兼容性判定引擎:内置基准基模规则表(可编辑副本存 exe 旁 compat_rules.json)。
判定依据优先"示例图实测底模"(.civitai.info images[].meta.Model),退回标注 baseModel。
等级:perfect(完美)/good(良好)/reduced(打折)/weak(偏弱)/incompat(不兼容)/mixed(混血-待测)。
"""

import io
import json
import os
import re

import civitai_fetcher as cf

# 内置规则:每个基准基模一组正则(忽略大小写,顺序即优先级,incompat 最后单判混血)
BUILTIN_BASES = {
    "wai_ill_v16": {
        "label": "WAI-illustrious v16",
        "rules": {
            "perfect": ["wai.*ill|ill.*wai"],
            "good": ["illustrious|instill|awpainting|animagine|pvc|ill-v?\\d|ill v\\d"],
            "reduced": ["noob|nai[\\s-]?xl"],
            "weak": ["sdxl|sd ?xl|base ?1\\.0|autism|urn:air|juggler|realvis"],
            "incompat": ["pony", "anima(?!gine)|zimage|krea|hidream|qwen|mistral|lumina|chroma|omnigen"],
        },
    },
    "ill_base": {
        "label": "Illustrious 原版",
        "rules": {
            "perfect": ["(?<!wai)illustrious(?!.*wai)|ill-v?\\d"],
            "good": ["wai.*ill|instill|awpainting|animagine|pvc"],
            "reduced": ["noob|nai[\\s-]?xl"],
            "weak": ["sdxl|sd ?xl|base ?1\\.0|autism|urn:air"],
            "incompat": ["pony", "anima(?!gine)|zimage|krea|hidream|qwen|mistral|lumina|chroma|omnigen"],
        },
    },
    "noob_eps": {
        "label": "NoobAI (eps 系)",
        "rules": {
            "perfect": ["noob|nai[\\s-]?xl"],
            "good": ["illustrious|wai.*ill|instill|awpainting|pvc"],
            "reduced": ["eps.*v|vpred|v-pred|v_pred"],
            "weak": ["sdxl|sd ?xl|base ?1\\.0|autism|urn:air"],
            "incompat": ["pony"],
        },
    },
    "noob_v": {
        "label": "NoobAI (v-pred 系)",
        "rules": {
            "perfect": ["noob|nai[\\s-]?xl"],
            "good": ["illustrious|wai.*ill|instill|awpainting|pvc"],
            "reduced": ["eps|epsilon"],
            "weak": ["sdxl|sd ?xl|base ?1\\.0|autism|urn:air"],
            "incompat": ["pony", "anima(?!gine)|zimage|krea|hidream|qwen|mistral|lumina|chroma|omnigen"],
        },
    },
    "pony_v6": {
        "label": "Pony Diffusion V6",
        "rules": {
            "perfect": ["pony"],
            "good": ["autism"],
            "reduced": ["sdxl|sd ?xl|base ?1\\.0"],
            "weak": ["illustrious|noob|nai[\\s-]?xl|animagine"],
            "incompat": ["illustrious|noob|nai[\\s-]?xl|instill|awpainting", "anima(?!gine)|zimage|krea|hidream|qwen|mistral|lumina|chroma|omnigen"],
        },
    },
}

LEVEL_ZH = {
    "perfect": "完美", "good": "良好", "reduced": "打折",
    "weak": "偏弱", "incompat": "不兼容", "mixed": "混血-待测",
}


RULES_VERSION = 2  # 递增触发用户侧 compat_rules.json 自动升级到新内置规则


def _rules_path():
    return os.path.join(os.path.dirname(cf.HASH_CACHE_FILE), "compat_rules.json")


def load_bases():
    """用户副本优先;无文件或版本落后于内置时重写内置(规则升级)"""
    try:
        with io.open(_rules_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data and data.get("_version", 0) >= RULES_VERSION:
            return data
    except (OSError, ValueError):
        pass
    out = dict(BUILTIN_BASES)
    out["_version"] = RULES_VERSION
    try:
        with io.open(_rules_path(), "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    except OSError:
        pass
    return BUILTIN_BASES


def judge(bases, base_key, real, tagged):
    """返回 (等级, 判定依据字符串)。等级不含 unknown 的处理(空依据→unknown 由调用方排)。"""
    cfg = bases.get(base_key) or {}
    rules = cfg.get("rules") or {}
    s = (real or "").strip() or (tagged or "").strip()
    if not s or s.lower() == "unknown":
        return "unknown", ""
    hit_incompat = False
    for level in ("perfect", "good", "reduced", "weak"):
        for pat in rules.get(level) or []:
            if re.search(pat, s, re.I):
                # 与不兼容同时命中 → 混血待测
                for pat2 in rules.get("incompat") or []:
                    if re.search(pat2, s, re.I):
                        return "mixed", s
                return level, s
    for pat in rules.get("incompat") or []:
        if re.search(pat, s, re.I):
            hit_incompat = True
    if hit_incompat:
        return "incompat", s
    return "unknown", s
