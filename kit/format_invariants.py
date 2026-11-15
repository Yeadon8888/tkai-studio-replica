#!/usr/bin/env python3
"""从对标镜头表提取可在花钱前校验的形式不变量。"""
import argparse
import json
import re
import sys
from pathlib import Path

import yaml

KIT = Path(__file__).resolve().parent
sys.path.insert(0, str(KIT))

from common import read_json, write_json  # noqa: E402


VOICE_MODES = {"on_camera_speech", "voiceover", "special_voice"}
REPLY_PREFIX = re.compile(r"^\s*(?:reply\s+to|replying\s+to)\b", re.I)
DEFAULT_CTA_PATTERNS = (r"\bshop\b", r"\bbuy\b", r"\border\b", r"\blink\b",
                        r"\bget\s+yours\b", r"\bcheck\b.{0,30}\btoday\b",
                        r"\btap\b", r"\bclick\b")


def config(path=None):
    path = Path(path or KIT / "config" / "rubric.yaml")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("format_invariants") or {}


def cta_patterns(path=None):
    return tuple(config(path).get("cta_patterns") or DEFAULT_CTA_PATTERNS)


def has_cta(text, patterns=None):
    value = str(text or "")
    return any(re.search(pattern, value, re.I) for pattern in (patterns or cta_patterns()))


def text_value(value):
    """把新贴纸对象与旧字符串统一成可校验文本。"""
    if isinstance(value, dict):
        return " ".join(str(value.get(key) or "").strip() for key in ("handle", "comment")).strip()
    return str(value or "").strip()


def _reference_names(shots):
    names = shots.get("reference_product_names") or shots.get("product_names") or []
    if isinstance(names, str):
        names = [names]
    for key in ("reference_product_name", "product_name"):
        if shots.get(key):
            names.append(shots[key])
    return list(dict.fromkeys(str(name).strip() for name in names if str(name).strip()))


def count_names(texts, names):
    total = 0
    for name in names:
        pattern = re.compile(r"(?<!\w)" + re.escape(name) + r"(?!\w)", re.I)
        total += sum(len(pattern.findall(str(text or ""))) for text in texts)
    return total


def script_product_names(facts):
    state = facts.get("product_state") if isinstance(facts, dict) else {}
    names = [facts.get("product_name") if isinstance(facts, dict) else None,
             state.get("name") if isinstance(state, dict) else None]
    return list(dict.fromkeys(str(name).strip() for name in names if str(name or "").strip()))


def script_texts(script):
    for row in (script or {}).get("shots") or []:
        for field in ("voiceover", "caption", "onscreen_copy"):
            yield text_value(row.get(field))


def script_style(script):
    values = [row.get("onscreen_copy") for row in (script or {}).get("shots") or []
              if text_value(row.get("onscreen_copy"))]
    if any(isinstance(value, dict) and value.get("style") == "reply_sticker" for value in values):
        return "reply_sticker"
    return "text_overlay" if values else "none"


def derive(shots, patterns=None):
    rows = [row for row in shots.get("shots") or [] if row.get("keep") in (None, "keep", "redo")]
    captions = any(str(row.get("caption") or "").strip() for row in rows)
    voice = shots.get("audio_mode") in VOICE_MODES
    onscreen = []
    for row in rows:
        value = str(row.get("onscreen_text") or "").strip()
        style = "reply_sticker" if value and REPLY_PREFIX.search(value) else ("text_overlay" if value else "none")
        onscreen.append({"id": str(row.get("id") or ""), "has_text": bool(value),
                         "style": style, "text": value})
    sticker_shots = [row["id"] for row in onscreen if row["style"] == "reply_sticker"]
    style = "reply_sticker" if sticker_shots else (
        "text_overlay" if any(row["has_text"] for row in onscreen) else "none")
    reference_texts = [str(row.get("voiceover") or "") for row in rows]
    reference_texts += [str(row.get("onscreen_text") or "") for row in rows]
    cta = any(has_cta(value, patterns) for value in reference_texts)
    names = _reference_names(shots)
    mentions = count_names(reference_texts, names)
    parts = ["对标有连续字幕" if captions else "对标无字幕",
             "对标有人声" if voice else "对标无人声"]
    if style == "reply_sticker":
        parts.append("钩子是回复评论贴纸（出现在 %s）" % "、".join(sticker_shots))
    elif style == "text_overlay":
        parts.append("屏幕文字是普通文字叠加")
    else:
        parts.append("对标无屏幕文字")
    parts += ["有 CTA" if cta else "无 CTA", f"品名出现 {mentions} 次"]
    return {"version": 1, "captions": captions, "voice": voice, "onscreen_style": style,
            "onscreen": onscreen, "reply_sticker_shots": sticker_shots, "cta": cta,
            "product_mentions": mentions, "reference_product_names": names,
            "description": "；".join(parts)}


def build(project):
    project = Path(project)
    shots = read_json(project / "shots.json")
    if not shots:
        raise FileNotFoundError(f"缺少 shots.json：{project / 'shots.json'}")
    result = derive(shots)
    write_json(project / "format.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="从对标镜头表生成形式不变量")
    parser.add_argument("--project", required=True)
    args = parser.parse_args(argv)
    result = build(args.project)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
