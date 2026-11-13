#!/usr/bin/env python3
"""由配置文本模型生成可校验、可追溯的逐镜头脚本。"""
import argparse
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


def _compact_number(value):
    return re.sub(r"\s+", "", value).lower()


def _facts_text(facts):
    return json.dumps(facts, ensure_ascii=False, sort_keys=True) if not isinstance(facts, str) else facts


def validate(script, shots_data, facts, competitor_terms=()):
    """返回阻塞错误；不修补模型输出。"""
    errors = []
    if not isinstance(script, dict) or not str(script.get("scene") or "").strip():
        return ["scene 必须是非空英文场景描述"]
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


def _prompt(shots_data, facts, terms, errors=None):
    correction = ""
    if errors:
        correction = "\n上一次输出被代码阻塞，请完整重写，不要解释。错误：\n- " + "\n- ".join(errors)
    return f"""你是产品短视频脚本模型。只能依据给定镜头表和产品事实写脚本，不得照抄对标台词。
共用场景 scene 用英文；每镜 visual_prompt 用英文且只写画面和动作，禁止要求画任何文字、数字、logo、字幕或水印。
voiceover、caption、onscreen_copy 用英文；产品只称为产品锚点中的名字或 the product；不得出现竞品词。
镜头表里原 voiceover 为空的段，你的 voiceover 也必须为空；非空段按每秒最多 2.6 个英文词。
所有数字和参数必须逐字存在于产品事实。end_state 要描述镜头结束一刻手、产品、道具的位置。
只输出 JSON：{{"scene":"...","shots":[{{"id":"S01","visual_prompt":"...","voiceover":"...","caption":"...","onscreen_copy":"...","end_state":"..."}}]}}
竞品词：{json.dumps(terms, ensure_ascii=False)}
产品事实：{json.dumps(facts, ensure_ascii=False)}
镜头表：{json.dumps(shots_data, ensure_ascii=False)}{correction}"""


def build(project, extra_facts=(), model_call=loop.gemini, terms=None):
    project = Path(project)
    shots_data = read_json(project / "shots.json")
    if not shots_data:
        raise FileNotFoundError(f"缺少 shots.json：{project / 'shots.json'}")
    facts = load_facts(project, extra_facts)
    terms = competitor_terms(project, shots_data) if terms is None else list(terms)
    raw_outputs, checks = [], []
    prompt = _prompt(shots_data, facts, terms)
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
            prompt = _prompt(shots_data, facts, terms, errors)
    if checks[-1]["errors"]:
        raise ValueError("脚本模型两次输出均未通过：" + "；".join(checks[-1]["errors"]))
    result = dict(parsed)
    result["generation"] = {"model": settings.model("script"), "prompt_sha256": prompt_hash,
                            "temperature": TEMPERATURE, "raw_outputs": raw_outputs,
                            "validation": checks, "rewrite_count": len(raw_outputs) - 1}
    write_json(project / "script.json", result)
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
    args = parser.parse_args(argv)
    try:
        result = build(args.project, _parse_facts(args.facts))
    except ValueError as exc:
        raise SystemExit(f"脚本校验失败，已停止：{exc}") from None
    print(json.dumps({"script": str(Path(args.project) / "script.json"),
                      "model": result["generation"]["model"],
                      "rewrite_count": result["generation"]["rewrite_count"],
                      "validation": result["generation"]["validation"][-1]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
