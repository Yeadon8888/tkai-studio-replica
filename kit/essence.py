#!/usr/bin/env python3
"""从对标镜头表生成神韵清单，并合并机器/专家逐项判定。"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent
sys.path.insert(0, str(KIT))

from common import read_json, write_json  # noqa: E402
import format_invariants  # noqa: E402


EXPERT_CATEGORIES = ("hook", "camera", "action", "sound", "caption", "emotion")
SCRIPT_CATEGORIES = {"editing_rhythm", "duration", "narrative_beats", "hook", "sound", "caption", "emotion"}
GENERATE_CATEGORIES = {"camera", "action", "visual"}


def _tc(value):
    value = float(value or 0)
    return f"{int(value // 60):02d}:{value % 60:04.1f}"


def resolve_tier(shots, plan=None):
    """档位只读人工 override 或计划；缺失即停止，不从内容猜。"""
    batch, cid = shots.get("research_batch"), shots.get("candidate_id")
    if batch and cid:
        row = (read_json(Path(batch) / "tier_overrides.json", {}) or {}).get(cid)
        tier = row.get("tier") if isinstance(row, dict) else None
        if tier in ("H", "M", "L", "X"):
            return tier, "tier_overrides.json"
    for source, label in ((plan or {}, "plan"), (shots, "shots.json")):
        tier = source.get("tier") or source.get("benchmark_tier")
        if tier in ("H", "M", "L", "X"):
            return tier, label
    raise ValueError("读不到人工确认档位（tier_overrides.json 或计划），已停止，不猜档位")


def build_items(shots, plan=None):
    tier, tier_source = resolve_tier(shots, plan)
    duration = float(shots["duration"])
    format_data = format_invariants.derive(shots)
    structure = shots.get("structure") or []
    beat_text = ("叙事节拍顺序与对标一致：" + " → ".join(str(x.get("beat") or "") for x in structure)
                 if structure else "对标镜头表缺少节拍结构")
    items = [
        {"id": "code-editing-rhythm", "category": "editing_rhythm",
         "what": f"剪辑切点按总时长归一化后与对标镜头边界一致，容差 ±8%（对标 00:00.0–{_tc(duration)}）", "judge": "code"},
        {"id": "code-duration", "category": "duration",
         "what": f"总时长与对标 {duration:g} 秒相差不超过 ±15%", "judge": "code"},
        {"id": "code-narrative-beats", "category": "narrative_beats",
         "what": beat_text,
         "judge": "code"},
        {"id": "code-format-captions", "category": "caption", "label": "字幕形式",
         "what": "成片有连续字幕" if format_data["captions"] else "成片无字幕", "judge": "code"},
        {"id": "code-format-onscreen-style", "category": "hook", "label": "屏幕文字形式",
         "what": f"屏幕文字形式保持为 {format_data['onscreen_style']}", "judge": "code"},
        {"id": "code-format-cta", "category": "emotion", "label": "行动号召",
         "what": "CTA 有无与对标一致", "judge": "code"},
        {"id": "code-format-product-mentions", "category": "emotion", "label": "品名次数",
         "what": f"品名不超过对标 {format_data['product_mentions']} 次 + 1", "judge": "code"},
        {"id": "code-format-duration", "category": "duration", "label": "计划时长",
         "what": f"计划或成片时长与对标 {duration:g} 秒相差不超过 ±15%", "judge": "code"},
        {"id": "code-format-voice", "category": "sound", "label": "口播有无",
         "what": "口播有无与对标一致", "judge": "code"},
        {"id": "code-format-narrative-beats", "category": "narrative_beats", "label": "节拍顺序",
         "what": beat_text, "judge": "code"},
        {"id": "expert-hook", "category": "hook",
         "what": "前三秒钩子机制与对标一致（00:00.0–00:03.0）", "judge": "expert"},
        {"id": "expert-camera-sequence", "category": "camera",
         "what": "景别/机位顺序与对标一致：" + "；".join(
             f"{_tc(x['start'])}–{_tc(x['end'])} {x.get('scale','')} / {x.get('camera','')}"
             for x in shots.get("shots") or [] if x.get("keep") == "keep"), "judge": "expert"},
    ]
    for shot in shots.get("shots") or []:
        if shot.get("keep") != "keep":
            continue
        items.append({"id": f"expert-action-{shot['id']}", "category": "action",
                      "what": f"{_tc(shot['start'])}–{_tc(shot['end'])} 关键动作：{shot.get('action','')}",
                      "judge": "expert"})
    items.extend([
        {"id": "expert-sound", "category": "sound",
         "what": f"声音类型与语气对应对标（{shots.get('audio_mode', '未标注')}）：" + "；".join(
             f"{_tc(x['start'])}–{_tc(x['end'])} {x.get('voiceover') or x.get('music_sfx') or '环境声'}"
             for x in shots.get("shots") or [] if x.get("keep") == "keep"), "judge": "expert"},
        {"id": "expert-emotion", "category": "emotion",
         "what": "情绪起伏与叙事推进对应对标", "judge": "expert"},
    ])
    if format_data["captions"]:
        items.insert(-1, {"id": "expert-caption-rhythm", "category": "caption",
                          "what": "字幕出现、换句与停留节奏对应对标", "judge": "expert"})
    return {"version": 1, "candidate_id": shots.get("candidate_id"), "tier": tier,
            "tier_source": tier_source, "reference_duration": duration, "items": items}


def build(project, plan=None):
    project = Path(project)
    shots = read_json(project / "shots.json")
    if not shots:
        raise FileNotFoundError(f"缺少 shots.json：{project / 'shots.json'}")
    format_invariants.build(project)
    data = build_items(shots, plan)
    write_json(project / "essence.json", data)
    return data


def detect_cuts(video, runner=subprocess.run):
    command = ["ffmpeg", "-hide_banner", "-v", "info", "-i", str(video), "-vf",
               "select='gt(scene,0.35)',metadata=print", "-an", "-f", "null", "-"]
    result = runner(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, check=False)
    return [float(x) for x in re.findall(r"pts_time:([0-9.]+)", result.stderr or "")]


def video_duration(video):
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                          "-of", "default=nw=1:nk=1", str(video)], text=True).strip())


def _beats(plan):
    rows = plan.get("structure") or plan.get("beats") or []
    return [str(x.get("beat") if isinstance(x, dict) else x) for x in rows]


FORMAT_LABELS = {
    "code-format-captions": "字幕有无",
    "code-format-onscreen-style": "屏幕文字形式",
    "code-format-cta": "行动号召",
    "code-format-product-mentions": "品名次数",
    "code-format-duration": "计划时长",
    "code-format-voice": "口播有无",
    "code-format-narrative-beats": "节拍顺序",
}


def _format_results(shots, script=None, plan=None, format_data=None, facts=None, duration=None,
                    compose_data=None):
    expected = format_data or format_invariants.derive(shots)
    source_beats = [str(x.get("beat") or "") for x in shots.get("structure") or []]
    target_beats = _beats(plan or {}) if plan else source_beats
    target_duration = float(duration if duration is not None else
                            ((plan or {}).get("duration") or shots.get("duration") or 0))
    if script is None:
        actual_captions, actual_style = expected["captions"], expected["onscreen_style"]
        actual_cta, actual_mentions, actual_voice = expected.get("cta_kind", "none"), expected["product_mentions"], expected["voice"]
    else:
        texts = list(format_invariants.script_texts(script))
        actual_captions = any(str(row.get("caption") or "").strip()
                              for row in script.get("shots") or [])
        actual_style = format_invariants.script_style(script)
        actual_cta = format_invariants.cta_kind(script.get("shots") or [])
        names = format_invariants.script_product_names(facts or {})
        actual_mentions = format_invariants.count_names(texts, names)
        actual_voice = any(str(row.get("voiceover") or "").strip() for row in script.get("shots") or [])
    if isinstance(compose_data, dict) and isinstance(compose_data.get("subtitles"), list):
        actual_captions = any(str(row.get("caption") or "").strip()
                              for row in compose_data["subtitles"] if isinstance(row, dict))
    ref_duration = float(shots.get("duration") or 0)
    beat_status = "pass" if source_beats and target_beats == source_beats else (
        "unclear" if not source_beats else "fail")
    values = {
        "code-format-captions": (actual_captions == expected["captions"],
                                 f"对标字幕={expected['captions']}；脚本字幕={actual_captions}"),
        "code-format-onscreen-style": (actual_style == expected["onscreen_style"],
                                        f"对标 {expected['onscreen_style']}；脚本 {actual_style}"),
        "code-format-cta": (actual_cta == expected.get("cta_kind", "none"),
                            f"对标 CTA={expected.get('cta_kind', 'none')}；脚本 CTA={actual_cta}"),
        "code-format-product-mentions": (actual_mentions <= expected["product_mentions"] + 1,
                                         f"对标 {expected['product_mentions']} 次；脚本 {actual_mentions} 次；上限 {expected['product_mentions'] + 1}"),
        "code-format-duration": (bool(ref_duration) and abs(target_duration - ref_duration) <= ref_duration * .15,
                                 f"对标 {ref_duration:.3f}s；计划或成片 {target_duration:.3f}s"),
        "code-format-voice": (actual_voice == expected["voice"],
                              f"对标口播={expected['voice']}；脚本口播={actual_voice}"),
    }
    rows = [{"id": item_id, "label": FORMAT_LABELS[item_id],
             "status": "pass" if passed else "fail", "detail": detail}
            for item_id, (passed, detail) in values.items()]
    rows.append({"id": "code-format-narrative-beats", "label": FORMAT_LABELS["code-format-narrative-beats"],
                 "status": beat_status,
                 "detail": ("对标镜头表缺少节拍结构" if not source_beats
                            else f"对标 {source_beats}；计划 {target_beats}")})
    return rows


def preview(project, plan=None):
    project = Path(project)
    shots = read_json(project / "shots.json")
    script = read_json(project / "script.json")
    if not shots or not script:
        raise FileNotFoundError("预估神韵需要 shots.json 和 script.json")
    format_data = read_json(project / "format.json") or format_invariants.build(project)
    import script_writer
    facts = script_writer.load_facts(project, ())
    rows = _format_results(shots, script, plan, format_data, facts)
    passed = sum(row["status"] == "pass" for row in rows)
    result = {"items": rows, "pass_count": passed, "total": len(rows),
              "score": passed / len(rows) if rows else 0.0,
              "blocking": [{"id": row["id"], "label": row["label"], "detail": row["detail"]}
                           for row in rows if row["status"] != "pass"]}
    write_json(project / "essence_preview.json", result)
    return result


def code_results(essence_data, shots, video, plan=None, cuts=None, duration=None,
                 script=None, format_data=None, facts=None, compose_data=None):
    duration = float(duration if duration is not None else video_duration(video))
    cuts = list(cuts if cuts is not None else detect_cuts(video))
    ref_duration = float(shots["duration"])
    source_shots = shots.get("shots") or []
    expected = [float(source_shots[index]["end"]) / ref_duration for index in range(len(source_shots) - 1)
                if source_shots[index + 1].get("join") != "continuous"]
    actual = [x / duration for x in cuts if 0 < x < duration]
    cut_pass = len(expected) == len(actual) and all(abs(a - b) <= 0.08 for a, b in zip(expected, actual))
    source_beats = [str(x.get("beat") or "") for x in shots.get("structure") or []]
    planned = _beats(plan or {})
    beat_status = "pass" if planned and planned == source_beats else ("fail" if planned else "unclear")
    by_id = {
        "code-editing-rhythm": {"status": "pass" if cut_pass else "fail", "at": "00:00",
                                "detail": f"对标归一化切点 {expected}；成片 {actual}"},
        "code-duration": {"status": "pass" if abs(duration - ref_duration) <= ref_duration * .15 else "fail",
                          "at": "00:00", "detail": f"对标 {ref_duration:.3f}s；成片 {duration:.3f}s"},
        "code-narrative-beats": {"status": beat_status, "at": "00:00",
                                 "detail": ("对标镜头表缺少节拍结构" if not source_beats
                                            else f"对标 {source_beats}；计划 {planned or '未提供'}")},
    }
    by_id.update({row["id"]: {"status": row["status"], "at": "00:00", "detail": row["detail"]}
                  for row in _format_results(shots, script, plan, format_data, facts, duration, compose_data)})
    return [dict(item, **by_id[item["id"]]) for item in essence_data["items"] if item["judge"] == "code"]


def merge_results(essence_data, code_rows, expert_rows):
    supplied = {str(x.get("id")): x for x in [*code_rows, *expert_rows] if isinstance(x, dict)}
    rows = []
    for item in essence_data["items"]:
        result = supplied.get(item["id"], {})
        status = result.get("status") if result.get("status") in ("pass", "fail", "unclear") else "unclear"
        rows.append({**item, "status": status, "at": str(result.get("at") or ""),
                     "detail": str(result.get("detail") or "未返回逐项判定")})
    passed = sum(x["status"] == "pass" for x in rows)
    unclear = [x["id"] for x in rows if x["status"] == "unclear"]
    return {"tier": essence_data["tier"], "items": rows, "pass_count": passed,
            "total": len(rows), "essence_score": passed / len(rows) if rows else 0.0,
            "unclear": unclear}


def retry_from(essence_result):
    failed = {x["category"] for x in essence_result.get("items") or [] if x.get("status") != "pass"}
    if failed & SCRIPT_CATEGORIES:
        return "script"
    if failed & GENERATE_CATEGORIES:
        return "generate"
    return "script"


def main(argv=None):
    parser = argparse.ArgumentParser(description="生成或计算神韵还原度")
    parser.add_argument("--project", required=True)
    parser.add_argument("--video")
    parser.add_argument("--plan")
    parser.add_argument("--preview", action="store_true", help="只做脚本阶段神韵预估")
    args = parser.parse_args(argv)
    plan = read_json(args.plan, {}) if args.plan else {}
    if args.preview:
        print(json.dumps(preview(args.project, plan), ensure_ascii=False, indent=2))
        return 0
    data = build(args.project, plan)
    if args.video:
        shots = read_json(Path(args.project) / "shots.json")
        project = Path(args.project)
        script = read_json(project / "script.json")
        format_data = read_json(project / "format.json")
        compose_data = read_json(project / "compose" / "compose.json", {})
        import script_writer
        facts = script_writer.load_facts(project, ())
        result = merge_results(data, code_results(data, shots, args.video, plan,
                                                  script=script, format_data=format_data, facts=facts,
                                                  compose_data=compose_data), [])
        write_json(Path(args.project) / "essence-score.json", result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
