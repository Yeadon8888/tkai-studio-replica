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


def validate(script, shots_data, facts, competitor_terms=()):
    """返回阻塞错误；不修补模型输出。"""
    errors = []
    if not isinstance(script, dict) or not str(script.get("scene") or "").strip():
        return ["scene 必须是非空英文场景描述"]
    if shots_data.get("audio_mode") == "music_only":
        errors.extend(_validate_music(script, shots_data))
    source_shots = shots_data.get("shots") or []
    rows = script.get("shots")
    if not isinstance(rows, list) or len(rows) != len(source_shots):
        return [f"shots 必须有 {len(source_shots)} 段"]
    by_id = {str(row.get("id")): row for row in rows if isinstance(row, dict)}
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
        if missing:
            errors.append(f"{sid} 缺字段：{', '.join(missing)}")
            continue
        voice = str(row.get("voiceover") or "").strip()
        actual_voice += bool(voice)
        words = re.findall(r"\b[\w'-]+\b", voice)
        limit = (float(shot["end"]) - float(shot["start"])) * 2.6
        if len(words) > limit + 1e-9:
            errors.append(f"{sid} 口播 {len(words)} 词，超过上限 {limit:.1f}")
        visual = str(row.get("visual_prompt") or "").strip()
        if not visual:
            errors.append(f"{sid} visual_prompt 不能为空")
        elif VISUAL_TEXT.search(visual):
            errors.append(f"{sid} visual_prompt 要求渲染文字、logo 或字幕")
        if shots_data.get("audio_mode") == "on_camera_speech" and voice:
            required = f"The person says in English, in a "
            if required.lower() not in visual.lower() or voice.lower() not in visual.lower():
                errors.append(f"{sid} 出镜口播必须把完整台词和语气写进 visual_prompt")
        checked = " ".join(str(row.get(field) or "") for field in ("voiceover", "caption", "onscreen_copy"))
        for number in NUMBER.findall(checked):
            if _compact_number(number) not in fact_numbers:
                errors.append(f"{sid} 数字或参数没有产品事实依据：{number.strip()}")
        all_text = " ".join(str(row.get(field) or "") for field in FIELDS).lower()
        for term in terms:
            if term in all_text:
                errors.append(f"{sid} 出现竞品品牌词：{term}")
        if re.search(r"\b(?:this|our|a|an)\s+product\b", all_text):
            errors.append(f"{sid} 产品称呼只能使用产品锚点名称或 the product")
        original_text = " ".join(str(row.get(field) or "") for field in FIELDS)
        for match in NAMED_PRODUCT.finditer(original_text):
            phrase = match.group(0).lower()
            if phrase != "the product" and phrase not in fact_blob:
                errors.append(f"{sid} 产品称呼不在产品锚点中：{match.group(0)}")
    if actual_voice != expected_voice:
        errors.append(f"非空口播段数应为 {expected_voice}，实际 {actual_voice}")
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
    return {"product_state": state, "product_anchor": anchor, "confirmed_selling_points": list(extra)}


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


def _prompt(shots_data, facts, terms, errors=None, feedback=None, previous=None):
    correction = ""
    if errors:
        correction = "\n上一次输出被代码阻塞，请完整重写，不要解释。错误：\n- " + "\n- ".join(errors)
    human_feedback = ""
    if feedback:
        human_feedback = f"""
人的修改意见：{json.dumps(feedback, ensure_ascii=False)}
上一版脚本：{json.dumps(previous or {}, ensure_ascii=False)}
优先满足这些意见，其他要求不变。不得只解释意见，必须输出完整的新脚本。"""
    return f"""你是产品短视频脚本模型。只能依据给定镜头表和产品事实写脚本，不得照抄对标台词。
共用场景 scene 用英文；每镜 visual_prompt 用英文且只写画面和动作，禁止要求画任何文字、数字、logo、字幕或水印。
voiceover、caption、onscreen_copy 用英文；产品只称为产品锚点中的名字或 the product；不得出现竞品词。
镜头表里原 voiceover 为空的段，你的 voiceover 也必须为空；非空段按每秒最多 2.6 个英文词。
audio_mode=on_camera_speech 时，必须把每镜完整 voiceover 原文同时写进 visual_prompt，格式必须是：The person says in English, in a <语气> tone: '...'；语气依据 rhythm，不得改台词。
voiceover、special_voice 只写 voiceover 字段，不把台词写进画面提示词；music_only、ambient 不得编造台词。
额外输出 music，只描述原创配乐风格：必须包含 mood、tempo_bpm、era_style、instruments、energy_curve；不得出现歌名、歌手名、in the style of 或 like <name>。
所有数字和参数必须逐字存在于产品事实。end_state 要描述镜头结束一刻手、产品、道具的位置。
只输出 JSON：{{"scene":"...","music":{{"mood":"...","tempo_bpm":90,"era_style":"...","instruments":["..."],"energy_curve":"..."}},"shots":[{{"id":"S01","visual_prompt":"...","voiceover":"...","caption":"...","onscreen_copy":"...","end_state":"..."}}]}}
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


def _archive_legacy(project, shots_data, facts, terms):
    versions = _versions(project)
    current = read_json(project / "script.json", {}) or {}
    if current and not versions:
        write_json(project / "script.v1.json", current)
        errors = validate(current, shots_data, facts, terms)
        generation = current.get("generation") if isinstance(current, dict) else {}
        _append_history(project, 1, None, (generation or {}).get("model") or "历史脚本（模型未知）",
                        {"ok": not errors, "errors": errors}, "legacy")
        versions = _versions(project)
    return current, (versions[-1][0] if versions else 0)


def build(project, extra_facts=(), model_call=loop.gemini, terms=None, feedback=None):
    project = Path(project)
    shots_data = read_json(project / "shots.json")
    if not shots_data:
        raise FileNotFoundError(f"缺少 shots.json：{project / 'shots.json'}")
    facts = load_facts(project, extra_facts)
    terms = competitor_terms(project, shots_data) if terms is None else list(terms)
    previous, latest_version = _archive_legacy(project, shots_data, facts, terms)
    raw_outputs, checks = [], []
    prompt = _prompt(shots_data, facts, terms, feedback=feedback, previous=previous)
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    parsed = None
    for attempt in range(2):
        raw = model_call([{"type": "text", "text": prompt}], project=project.name,
                         ref=f"script:{attempt + 1}", max_tokens=8000,
                         temperature=TEMPERATURE, role="script")
        raw_outputs.append(raw)
        parsed = parse_json(raw, require=("scene", "shots"))
        errors = validate(parsed, shots_data, facts, terms) if parsed is not None else ["输出不是含 scene、shots 的 JSON 对象"]
        checks.append({"attempt": attempt + 1, "ok": not errors, "errors": errors})
        if not errors:
            break
        if attempt == 0:
            prompt = _prompt(shots_data, facts, terms, errors, feedback, previous)
    if checks[-1]["errors"]:
        raise ValueError("脚本模型两次输出均未通过：" + "；".join(checks[-1]["errors"]))
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
    args = parser.parse_args(argv)
    try:
        feedback = json.loads(args.feedback_json) if args.feedback_json else None
        result = build(args.project, _parse_facts(args.facts), feedback=feedback)
    except ValueError as exc:
        raise SystemExit(f"脚本校验失败，已停止：{exc}") from None
    print(json.dumps({"script": str(Path(args.project) / "script.json"),
                      "model": result["generation"]["model"],
                      "rewrite_count": result["generation"]["rewrite_count"],
                      "validation": result["generation"]["validation"][-1]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
