#!/usr/bin/env python3
"""由配置文本模型生成可校验、可追溯的逐镜头脚本。"""
import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent
sys.path.insert(0, str(KIT))

from common import parse_json, read_json, write_json  # noqa: E402
import loop  # noqa: E402
import settings  # noqa: E402
import format_invariants  # noqa: E402

TEMPERATURE = 0.2
FIELDS = ("visual_prompt", "voiceover", "caption", "onscreen_copy", "end_state")
VISUAL_TEXT = re.compile(
    r"\b(?:render|draw|write|add|show|display|generate|include)\b.{0,24}"
    r"\b(?:text|letters?|words?|captions?|subtitles?|logos?|watermarks?)\b|"
    r"\b(?:captions?|subtitles?|logos?|watermarks?)\b", re.I)
NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?:\s*[%x×]|\s*[a-zA-Z]+)?", re.I)
NAMED_PRODUCT = re.compile(
    r"\b([A-Z][A-Za-z0-9&.-]+(?:\s+[A-Z0-9][A-Za-z0-9&.-]+){0,3})\s+"
    r"([Pp]roduct|[Bb]ottle|[Kk]it|[Ss]trips?)\b")
NO_VOICE = "No dialogue, no speech, no singing, no voice-over; only quiet natural ambient sound."
MUSIC_FIELDS = ("mood", "tempo_bpm", "era_style", "instruments", "energy_curve")
CHARACTER_FIELDS = ("name", "appearance", "first_shot")
MAX_OVERLAY_WORDS = 7
SPEECH_TAIL_SECONDS = 0.8


def _compact_number(value):
    return re.sub(r"\s+", "", value).lower()


def _facts_text(facts):
    return json.dumps(facts, ensure_ascii=False, sort_keys=True) if not isinstance(facts, str) else facts


def _music_reference_terms(shots_data):
    """从镜头表的 music_sfx 中提取可识别的歌名和人名。"""
    terms = []
    for row in shots_data.get("shots") or []:
        value = str(row.get("music_sfx") or "").strip()
        terms.extend(re.findall(r"[\"'“”《》]([^\"'“”《》]{2,})[\"'“”《》]", value))
        terms.extend(re.findall(r"\b(?:by|artist)\s*[:\-]?\s*([A-Z][\w'.-]+(?:\s+[A-Z][\w'.-]+){0,4})", value))
        terms.extend(re.findall(r"\b(?:[A-Z][\w']+)(?:\s+(?:[A-Z][\w']+|of|the|to|on|a|an)){1,8}\b", value))
    return list(dict.fromkeys(x.strip().lower() for x in terms if len(x.strip()) >= 2))


def _validate_music(script, shots_data):
    music = script.get("music")
    if not isinstance(music, dict):
        return ["music 必须是包含配乐风格的对象"]
    missing = [field for field in MUSIC_FIELDS if field not in music or music[field] in (None, "", [])]
    errors = ["music 缺字段：" + ", ".join(missing)] if missing else []
    if not missing:
        if isinstance(music["tempo_bpm"], bool) or not isinstance(music["tempo_bpm"], (int, float)) \
                or float(music["tempo_bpm"]) <= 0:
            errors.append("music.tempo_bpm 必须是正数")
        if not isinstance(music["instruments"], list) or not all(
                isinstance(value, str) and value.strip() for value in music["instruments"]):
            errors.append("music.instruments 必须是非空字符串列表")
        for field in ("mood", "era_style", "energy_curve"):
            if not isinstance(music[field], str):
                errors.append(f"music.{field} 必须是字符串")
    blob = json.dumps(music, ensure_ascii=False).lower()
    if re.search(r"\bin\s+the\s+style\s+of\b|\blike\s+[\w'\-]", blob, re.I):
        errors.append("music 不得使用 in the style of 或 like <name> 模仿表述")
    for term in _music_reference_terms(shots_data):
        if term in blob:
            errors.append(f"music 不得出现镜头表中的歌名或人名：{term}")
    return errors


def music_errors(script, shots_data):
    """返回纯音乐脚本的配乐描述错误；其它声音路线无需补写。"""
    return _validate_music(script, shots_data) if shots_data.get("audio_mode") == "music_only" else []


GENERIC_NAME_WORDS = {"do", "not", "don't", "dont", "buy", "this", "that", "the", "a", "an", "our", "your",
                      "please", "it", "is", "all", "because", "of", "use", "test", "with", "and", "new"}


def _speech_sentence(row, shot):
    names = row.get("characters") if isinstance(row.get("characters"), list) else []
    speaker = str(names[0]).strip() if names else "The person"
    tone = str(row.get("tone") or shot.get("rhythm") or "natural").strip() or "natural"
    return f"{speaker} says in English, in a {tone} tone: '{str(row.get('voiceover') or '').strip()}'"


def normalize(script, shots_data):
    """代码能确定的内容由代码补齐，不要求模型逐字照做：
    出镜口播台词拼进 visual_prompt；角色 first_shot 由实际出场镜头推出。"""
    if not isinstance(script, dict) or not isinstance(script.get("shots"), list):
        return script
    source = {str(x.get("id")): x for x in shots_data.get("shots") or []}
    result = dict(script)
    rows = []
    for row in script["shots"]:
        item = dict(row) if isinstance(row, dict) else row
        if isinstance(item, dict):
            shot = source.get(str(item.get("id")), {})
            voice = str(item.get("voiceover") or "").strip()
            visual = str(item.get("visual_prompt") or "").strip()
            if shot.get("speaker_on_screen") and voice and voice.lower() not in visual.lower():
                item["visual_prompt"] = f"{visual.rstrip('.')}. {_speech_sentence(item, shot)}".strip(". ")
        rows.append(item)
    result["shots"] = rows
    if isinstance(script.get("characters"), list):
        characters = []
        for character in script["characters"]:
            if isinstance(character, dict) and character.get("name"):
                seen = [str(r.get("id")) for r in rows if isinstance(r, dict)
                        and isinstance(r.get("characters"), list) and character["name"] in r["characters"]]
                if seen:
                    character = {**character, "first_shot": seen[0]}
            characters.append(character)
        result["characters"] = characters
    return result


def _append_no_voice(script, audio_mode):
    if audio_mode not in ("music_only", "ambient"):
        return script
    result = dict(script)
    rows = []
    for row in result.get("shots") or []:
        item = dict(row)
        visual = str(item.get("visual_prompt") or "").strip()
        if not visual.endswith(NO_VOICE):
            item["visual_prompt"] = f"{visual.rstrip('.')}. {NO_VOICE}"
        rows.append(item)
    result["shots"] = rows
    return result


def _reply_parts(value):
    value = str(value or "").strip()
    match = re.match(r"reply\s+to\s+(@?[\w.-]+)'s\s+comment\s*(.*)", value, re.I)
    if match:
        return match.group(1).lower().lstrip("@"), match.group(2).strip()
    match = re.match(r"replying\s+to\s+@?([\w.-]+)\s*(.*)", value, re.I)
    return (match.group(1).lower(), match.group(2).strip()) if match else ("", value)


def _word_overlap(left, right):
    generated = set(re.findall(r"[a-z0-9']+", str(left or "").lower()))
    source = set(re.findall(r"[a-z0-9']+", str(right or "").lower()))
    return len(generated & source) / len(generated) if generated else 0.0


def format_errors(script, shots_data):
    """不依赖产品事实的形式不变量检查；引擎用它判断旧脚本是否需要系统重写。"""
    rows = script.get("shots") if isinstance(script, dict) else None
    if not isinstance(rows, list) or not shots_data.get("shots"):
        return []
    rows = [row for row in rows if isinstance(row, dict)]
    format_data = format_invariants.derive(shots_data)
    errors = []
    voiced = any(str(row.get("voiceover") or "").strip() for row in rows)
    if voiced != bool(format_data.get("voice")):
        errors.append("口播有无必须与对标 audio_mode 一致")
    captions = any(str(row.get("caption") or "").strip() for row in rows)
    if captions != bool(format_data.get("captions")):
        errors.append("字幕有无必须与对标一致")
    actual_cta = format_invariants.cta_kind(rows)
    if actual_cta != format_data.get("cta_kind", "direct" if format_data.get("cta") else "none"):
        errors.append(f"CTA 类型必须与对标一致：{format_data.get('cta_kind') or 'none'}")
    actual_style = format_invariants.script_style(script)
    if actual_style != format_data.get("onscreen_style"):
        errors.append(f"屏幕文字形式应为 {format_data.get('onscreen_style')}，实际为 {actual_style}")
    coverage = format_invariants.script_speech_coverage(script, shots_data.get("duration"))
    minimum = float(format_data.get("speech_coverage") or 0) * .8
    if coverage + 1e-9 < minimum:
        errors.append(f"计划口播覆盖率 {coverage:.1%} 低于对标的 80% 下限 {minimum:.1%}")
    return errors


def validate(script, shots_data, facts, competitor_terms=(), format_data=None):
    """返回阻塞错误；不修补模型输出。"""
    errors = []
    if not isinstance(script, dict) or not str(script.get("scene") or "").strip():
        return ["scene 必须是非空英文场景描述"]
    if shots_data.get("audio_mode") == "music_only":
        errors.extend(_validate_music(script, shots_data))
    source_shots = shots_data.get("shots") or []
    format_data = format_data or format_invariants.derive(shots_data)
    rows = script.get("shots")
    if not isinstance(rows, list) or len(rows) != len(source_shots):
        return [f"shots 必须有 {len(source_shots)} 段"]
    by_id = {str(row.get("id")): row for row in rows if isinstance(row, dict)}
    source_ids = [str(row.get("id")) for row in source_shots]
    needs_characters = any(bool(row.get("speaker_on_screen")) or row.get("characters")
                           for row in source_shots)
    character_rows = script.get("characters")
    characters = {}
    if needs_characters and (not isinstance(character_rows, list) or not character_rows):
        errors.append("characters 必须是非空角色设定表")
    for index, character in enumerate(character_rows or [], 1):
        if not isinstance(character, dict):
            errors.append(f"第 {index} 个角色不是对象")
            continue
        missing = [field for field in CHARACTER_FIELDS if not str(character.get(field) or "").strip()]
        if missing:
            errors.append(f"第 {index} 个角色缺字段：{', '.join(missing)}")
            continue
        name = str(character["name"]).strip()
        if name in characters:
            errors.append(f"角色名重复：{name}")
        if str(character["first_shot"]) not in source_ids:
            errors.append(f"角色 {name} 的 first_shot 不在镜头表中")
        characters[name] = character
    fact_blob = _facts_text(facts).lower()
    fact_numbers = {_compact_number(x) for x in NUMBER.findall(fact_blob)}
    terms = [str(x).strip().lower() for x in competitor_terms if len(str(x).strip()) >= 2]
    expected_voice = sum(bool(str(row.get("voiceover") or "").strip()) for row in source_shots)
    actual_voice = 0
    for shot in source_shots:
        sid = str(shot.get("id"))
        row = by_id.get(sid)
        if not row:
            errors.append(f"{sid} 缺失")
            continue
        missing = [field for field in FIELDS if field not in row]
        if format_data.get("cta_structured") and "cta" not in row:
            missing.append("cta")
        if missing:
            errors.append(f"{sid} 缺字段：{', '.join(missing)}")
            continue
        if "cta" in row:
            cta_text = " ".join(format_invariants.text_value(row.get(field))
                                for field in ("voiceover", "caption", "onscreen_copy"))
            cta_error = format_invariants.validate_cta(row.get("cta"), cta_text)
            if cta_error:
                errors.append(f"{sid} {cta_error}")
        voice = str(row.get("voiceover") or "").strip()
        actual_voice += bool(voice)
        words = re.findall(r"\b[\w'-]+\b", voice)
        limit = max(0.0, float(shot["end"]) - float(shot["start"]) - SPEECH_TAIL_SECONDS) * 2.6
        if len(words) > limit + 1e-9:
            errors.append(f"{sid} 口播 {len(words)} 词，超过上限 {limit:.1f}（含 {SPEECH_TAIL_SECONDS:g} 秒收尾余量）")
        appearing = row.get("characters", [])
        if needs_characters and not isinstance(appearing, list):
            errors.append(f"{sid} characters 必须是角色名列表")
            appearing = []
        unknown = [str(name) for name in appearing if str(name) not in characters]
        if unknown:
            errors.append(f"{sid} 出现未登记角色：{', '.join(unknown)}")
        visual = str(row.get("visual_prompt") or "").strip()
        if not visual:
            errors.append(f"{sid} visual_prompt 不能为空")
        elif VISUAL_TEXT.search(visual):
            errors.append(f"{sid} visual_prompt 要求渲染文字、logo 或字幕")
        if shot.get("speaker_on_screen") and voice:
            if voice.lower() not in visual.lower() or " says in english" not in visual.lower():
                errors.append(f"{sid} 出镜口播必须把完整台词和语气写进 visual_prompt")
        checked = " ".join(format_invariants.text_value(row.get(field))
                           for field in ("voiceover", "caption", "onscreen_copy"))
        for number in NUMBER.findall(checked):
            if _compact_number(number) not in fact_numbers:
                errors.append(f"{sid} 数字或参数没有产品事实依据：{number.strip()}")
        all_text = " ".join(format_invariants.text_value(row.get(field)) for field in FIELDS).lower()
        for term in terms:
            if term in all_text:
                errors.append(f"{sid} 出现竞品品牌词：{term}")
        if re.search(r"\b(?:this|our|a|an)\s+product\b", all_text):
            errors.append(f"{sid} 产品称呼只能使用产品锚点名称或 the product")
        original_text = " ".join(format_invariants.text_value(row.get(field)) for field in FIELDS)
        for match in NAMED_PRODUCT.finditer(original_text):
            name_tokens = [t for t in match.group(1).lower().split() if t not in GENERIC_NAME_WORDS]
            if not name_tokens:
                continue  # "DO NOT BUY The product" 之类：前面是普通词，不是产品名
            if not all(t in fact_blob for t in name_tokens):
                errors.append(f"{sid} 产品称呼不在产品锚点中：{match.group(0)}")
    if actual_voice != expected_voice:
        errors.append(f"非空口播段数应为 {expected_voice}，实际 {actual_voice}")
    if bool(actual_voice) != bool(format_data.get("voice")):
        errors.append("口播有无必须与对标 audio_mode 一致")
    coverage = format_invariants.script_speech_coverage(script, shots_data.get("duration"))
    minimum = float(format_data.get("speech_coverage") or 0) * .8
    if coverage + 1e-9 < minimum:
        errors.append(f"计划口播覆盖率 {coverage:.1%} 低于对标的 80% 下限 {minimum:.1%}")
    benefit_ok, benefit_detail = format_invariants.benefit_match(shots_data, script, facts)
    if not benefit_ok:
        errors.append("对标含产品作用的每个镜头都必须有一句来自产品事实的作用：" + benefit_detail)
    target_captions = any(str(row.get("caption") or "").strip() for row in rows if isinstance(row, dict))
    if target_captions != bool(format_data.get("captions")):
        errors.append("字幕有无必须与对标一致")
    all_script_text = list(format_invariants.script_texts(script))
    expected_cta = format_data.get("cta_kind", "direct" if format_data.get("cta") else "none")
    actual_cta = format_invariants.cta_kind(rows)
    if actual_cta != expected_cta:
        errors.append(f"CTA 类型必须与对标一致：期望 {expected_cta}，实际 {actual_cta}")
        if expected_cta == "none":
            errors.append("对标无 CTA，脚本不得新增行动号召")
    product_names = format_invariants.script_product_names(facts)
    mention_count = format_invariants.count_names(all_script_text, product_names)
    mention_limit = int(format_data.get("product_mentions") or 0) + 1
    if mention_count > mention_limit:
        errors.append(f"品名出现 {mention_count} 次，超过对标次数 + 1（上限 {mention_limit}）")
    sticker_ids = set(str(value) for value in format_data.get("reply_sticker_shots") or [])
    actual_style = format_invariants.script_style(script)
    if actual_style != format_data.get("onscreen_style"):
        errors.append(f"屏幕文字形式应为 {format_data.get('onscreen_style')}，实际为 {actual_style}")
    source_stickers = [row for row in format_data.get("onscreen") or []
                       if row.get("style") == "reply_sticker"]
    source_handles, source_comments = zip(*(_reply_parts(row.get("text")) for row in source_stickers)) \
        if source_stickers else ((), ())
    for row in rows:
        sid, value = str(row.get("id")), row.get("onscreen_copy")
        if format_data.get("onscreen_style") == "reply_sticker":
            if sid in sticker_ids:
                valid = (isinstance(value, dict) and value.get("style") == "reply_sticker"
                         and re.fullmatch(r"@[A-Za-z0-9._]{3,24}", str(value.get("handle") or ""))
                         and str(value.get("comment") or "").strip())
                if not valid:
                    errors.append(f"{sid} onscreen_copy 必须是含虚构 handle 和 comment 的 reply_sticker 对象")
                    continue
                if str(value["handle"]).lower().lstrip("@") in source_handles:
                    errors.append(f"{sid} 回复评论贴纸不得使用对标真实用户名")
                if any(_word_overlap(value["comment"], comment) > .60 for comment in source_comments):
                    errors.append(f"{sid} 回复评论与对标原文词重合率超过 60%")
            elif format_invariants.text_value(value):
                errors.append(f"{sid} 不在对标贴纸时段，onscreen_copy 必须为空")
        elif isinstance(value, dict):
            errors.append(f"{sid} 对标不是回复评论贴纸，onscreen_copy 必须是字符串")
        elif format_data.get("onscreen_style") == "text_overlay" and format_invariants.text_value(value):
            words = re.findall(r"\b[\w'-]+\b", str(value))
            if len(words) > MAX_OVERLAY_WORDS:
                errors.append(f"{sid} onscreen_copy {len(words)} 词，普通文字叠加最多 {MAX_OVERLAY_WORDS} 个英文词")
    for name, character in characters.items():
        appearances = [str(row.get("id")) for row in rows
                       if isinstance(row.get("characters"), list) and name in row["characters"]]
        if not appearances:
            errors.append(f"角色 {name} 没有出现在任何镜头")
        elif appearances[0] != str(character.get("first_shot")):
            errors.append(f"角色 {name} 的 first_shot 应为 {appearances[0]}")
    return list(dict.fromkeys(errors))


def _product_root(project):
    project = Path(project).resolve()
    for parent in (project, *project.parents):
        if (parent / "product_state.json").is_file():
            return parent
    meta = read_json(project / "project.json", {}) or {}
    pid = meta.get("product_id")
    root = settings.studio_home() / "products" / str(pid) if pid else None
    return root if root and root.is_dir() else project


def load_facts(project, extra):
    root = _product_root(project)
    state = read_json(root / "product_state.json", {}) or {}
    anchor_path = root / "product_anchor.txt"
    anchor = anchor_path.read_text(encoding="utf-8").strip() if anchor_path.is_file() else ""
    meta = read_json(Path(project) / "project.json", {}) or {}
    return {"product_state": state, "product_anchor": anchor, "confirmed_selling_points": list(extra),
            "replication_notes": meta.get("replication_notes") or ""}


def competitor_terms(project, shots_data):
    terms = [shots_data.get("reference_author")]
    batch = shots_data.get("research_batch")
    cid = shots_data.get("candidate_id")
    if batch and cid:
        candidates = read_json(Path(batch) / "candidates.json", []) or []
        candidate = next((x for x in candidates if x.get("candidate_id") == cid), {})
        terms.extend((candidate.get("brand"), candidate.get("author"),
                      candidate.get("title") or candidate.get("desc")))
        # 标题和拆解只提取明确的 @作者或带 Brand/品牌 键的值，避免把普通英语都误判为品牌。
        blob = json.dumps({"title": candidate.get("title") or candidate.get("desc") or "",
                           "breakdown": read_json(Path(batch) / "breakdown.json", [])}, ensure_ascii=False)
        terms.extend(re.findall(r"@([\w.-]{2,})|(?:brand|品牌)\s*[:：]\s*([\w.-]{2,})", blob, re.I))
    flat = []
    for value in terms:
        if isinstance(value, tuple):
            flat.extend(x for x in value if x)
        elif value:
            flat.append(value)
    return list(dict.fromkeys(str(x).strip() for x in flat if str(x).strip()))


def _prompt(shots_data, facts, terms, format_data, errors=None, feedback=None, previous=None):
    correction = ""
    if errors:
        correction = "\n上一次输出被代码阻塞，请完整重写，不要解释。错误：\n- " + "\n- ".join(errors)
    human_feedback = ""
    if feedback:
        human_feedback = f"""
人的修改意见：{json.dumps(feedback, ensure_ascii=False)}
上一版脚本：{json.dumps(previous or {}, ensure_ascii=False)}
优先满足这些意见，其他要求不变。不得只解释意见，必须输出完整的新脚本。"""
    sticker_rule = ("onscreen_copy 只在指定贴纸镜头输出对象 "
                    '{"style":"reply_sticker","handle":"@虚构用户名","comment":"一句虚构评论"}；'
                    "其它镜头必须为空字符串；评论只对应原评论意图，不得照抄原文或真实用户名。"
                    if format_data.get("onscreen_style") == "reply_sticker" else
                    f"onscreen_copy 在普通文字叠加时输出不超过 {MAX_OVERLAY_WORDS} 个英文词的短标题，"
                    "不得复述整句台词；对标无屏幕文字时输出空字符串。")
    caption_rule = ("对标无连续字幕，所有 caption 必须是空字符串。" if not format_data.get("captions")
                    else "对标有连续字幕，caption 保持与对标一致的出现方式。")
    cta_rule = ("CTA kind 必须是 reverse：保留至少一个反派反向求饶、求观众别买或别测的 CTA，"
                "不得改成正向下单或购买 CTA。" if format_data.get("cta_kind") == "reverse" else
                f"CTA kind 必须是 {format_data.get('cta_kind', 'none')}。")
    return f"""你是复刻对标的脚本模型：保持对标的形式（字幕有无、屏幕文字形式、有无 CTA、语气），只把对标里的同类产品换成我们的产品。只能依据给定镜头表和产品事实写脚本，不得照抄对标台词。
形式不变量：{format_data.get('description', '')}
{caption_rule}
{sticker_rule}
{cta_rule}
共用场景 scene 用英文；每镜 visual_prompt 用英文且只写画面和动作，禁止要求画任何文字、数字、logo、字幕或水印。
voiceover、caption、onscreen_copy 用英文；产品只称为产品锚点中的名字或 the product；不得出现竞品词。
镜头表里原 voiceover 为空的段，你的 voiceover 也必须为空；非空段按每秒最多 2.6 个英文词，并为每个生成单元结尾预留至少 {SPEECH_TAIL_SECONDS:g} 秒无台词余量。
整体计划口播覆盖率必须至少是对标转写口播覆盖率的 80%，以每秒 2.6 词估算。
镜头表 benefit_claim.present=true 的每个对应镜头，voiceover 必须有一句产品作用，且用词必须来自给定产品事实。
镜头的 speaker_on_screen=true 且 voiceover 非空时，必须把完整 voiceover 原文同时写进 visual_prompt，格式为：<说话角色名或 The person> says in English, in a <语气> tone: '...'（漏写时代码会按 characters 第一个角色自动补上）；语气依据 rhythm，不得改台词。只有说话者不在画面中时才是画外音。
speaker_on_screen=false 的 voiceover、special_voice 只写 voiceover 字段，不把台词写进画面提示词；music_only、ambient 不得编造台词。
额外输出 music，只描述原创配乐风格：必须包含 mood、tempo_bpm、era_style、instruments、energy_curve；不得出现歌名、歌手名、in the style of 或 like <name>。
所有数字和参数必须逐字存在于产品事实。end_state 要描述镜头结束一刻手、产品、道具的位置。
输出 characters 角色设定表；每个角色包含 name、固定英文 appearance、first_shot。每镜 characters 只列本镜实际出现的角色名，所有名字必须来自角色设定表；跨镜角色不得改变 appearance。
产品标签只允许描述为 "label text exactly as in the reference photo"，不得在 visual_prompt 中编写或猜测标签小字。
每镜输出 cta={{"present":true|false,"kind":"direct|reverse|none","evidence":"本镜台词或画面文字原文，无则空字符串"}}。
只输出 JSON：{{"scene":"...","characters":[{{"name":"Lead","appearance":"fixed English appearance","first_shot":"S01"}}],"music":{{"mood":"...","tempo_bpm":90,"era_style":"...","instruments":["..."],"energy_curve":"..."}},"shots":[{{"id":"S01","characters":["Lead"],"visual_prompt":"...","voiceover":"...","caption":"...","onscreen_copy":"字符串、空字符串或上述贴纸对象","end_state":"...","cta":{{"present":false,"kind":"none","evidence":""}}}}]}}
竞品词：{json.dumps(terms, ensure_ascii=False)}
产品事实：{json.dumps(facts, ensure_ascii=False)}
镜头表：{json.dumps(shots_data, ensure_ascii=False)}{human_feedback}{correction}"""


def _versions(project):
    rows = []
    for path in project.glob("script.v*.json"):
        match = re.fullmatch(r"script\.v(\d+)\.json", path.name)
        if match:
            rows.append((int(match.group(1)), path))
    return sorted(rows)


def _append_history(project, version, feedback, model_name, validation, source):
    row = {"time": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "version": version, "feedback": feedback, "model": model_name,
           "validation": validation, "source": source}
    with (project / "script_history.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _append_failure(project, model_name, prompt, raw, errors, attempt):
    row = {"time": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "model": model_name,
           "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
           "output_sha256": hashlib.sha256(str(raw).encode("utf-8")).hexdigest(),
           "errors": list(errors), "attempt": attempt}
    with (Path(project) / "script_failures.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _archive_legacy(project, shots_data, facts, terms, format_data):
    versions = _versions(project)
    current = read_json(project / "script.json", {}) or {}
    if current and not versions:
        write_json(project / "script.v1.json", current)
        errors = validate(current, shots_data, facts, terms, format_data)
        generation = current.get("generation") if isinstance(current, dict) else {}
        _append_history(project, 1, None, (generation or {}).get("model") or "历史脚本（模型未知）",
                        {"ok": not errors, "errors": errors}, "legacy")
        versions = _versions(project)
    return current, (versions[-1][0] if versions else 0)


def build(project, extra_facts=(), model_call=loop.gemini, terms=None, feedback=None, max_attempts=2):
    project = Path(project)
    if max_attempts not in (1, 2, 3):
        raise ValueError("单次脚本生成尝试次数只能是 1、2 或 3")
    shots_data = read_json(project / "shots.json")
    if not shots_data:
        raise FileNotFoundError(f"缺少 shots.json：{project / 'shots.json'}")
    facts = load_facts(project, extra_facts)
    terms = competitor_terms(project, shots_data) if terms is None else list(terms)
    format_data = format_invariants.build(project)
    previous, latest_version = _archive_legacy(project, shots_data, facts, terms, format_data)
    raw_outputs, checks = [], []
    prompt = _prompt(shots_data, facts, terms, format_data, feedback=feedback, previous=previous)
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    parsed = None
    for attempt in range(max_attempts):
        raw = model_call([{"type": "text", "text": prompt}], project=project.name,
                         ref=f"script:{attempt + 1}", max_tokens=24000,
                         temperature=TEMPERATURE, role="script")
        raw_outputs.append(raw)
        parsed = normalize(parse_json(raw, require=("scene", "shots")), shots_data)
        errors = validate(parsed, shots_data, facts, terms, format_data) if parsed is not None else ["输出不是含 scene、shots 的 JSON 对象"]
        checks.append({"attempt": attempt + 1, "ok": not errors, "errors": errors})
        if not errors:
            break
        _append_failure(project, settings.model("script"), prompt, raw, errors, attempt + 1)
        if attempt < max_attempts - 1:
            prompt = _prompt(shots_data, facts, terms, format_data, errors, feedback, previous)
    if checks[-1]["errors"]:
        raise ValueError(f"脚本模型本次 {len(checks)} 次输出均未通过：" +
                         "；".join(checks[-1]["errors"]))
    result = _append_no_voice(parsed, shots_data.get("audio_mode"))
    result["generation"] = {"model": settings.model("script"), "prompt_sha256": prompt_hash,
                            "temperature": TEMPERATURE, "raw_outputs": raw_outputs,
                            "validation": checks, "rewrite_count": len(raw_outputs) - 1}
    version = latest_version + 1
    result["generation"]["version"] = version
    write_json(project / f"script.v{version}.json", result)
    write_json(project / "script.json", result)
    source = "system_rewrite" if isinstance(feedback, dict) and feedback.get("source") == "system" \
        else ("human_feedback" if feedback else "generation")
    _append_history(project, version, feedback, result["generation"]["model"], checks[-1], source)
    return result


def _parse_facts(value):
    path = Path(value).expanduser()
    if path.is_file():
        data = read_json(path)
        return data if isinstance(data, list) else [data]
    return [x.strip() for x in value.split(",") if x.strip()]


def main(argv=None):
    parser = argparse.ArgumentParser(description="用配置文本模型生成并校验逐镜头脚本")
    parser.add_argument("--project", required=True)
    parser.add_argument("--facts", required=True, help="产品事实 JSON 文件或逗号分隔卖点")
    parser.add_argument("--feedback-json", default="", help="人的修改意见 JSON")
    parser.add_argument("--attempts", type=int, choices=(1, 2, 3), default=3,
                        help="本次最多调用脚本模型的次数；默认 3")
    args = parser.parse_args(argv)
    try:
        feedback = json.loads(args.feedback_json) if args.feedback_json else None
        result = build(args.project, _parse_facts(args.facts), feedback=feedback,
                       max_attempts=args.attempts)
    except ValueError as exc:
        raise SystemExit(f"脚本校验失败，已停止：{exc}") from None
    print(json.dumps({"script": str(Path(args.project) / "script.json"),
                      "model": result["generation"]["model"],
                      "rewrite_count": result["generation"]["rewrite_count"],
                      "validation": result["generation"]["validation"][-1]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
