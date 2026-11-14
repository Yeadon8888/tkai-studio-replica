"""由文件数据推导工作流阶段，并且一次只推进一步。"""
import datetime as dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import checkpoints
import products
import settings
from common import read_json

KIT = Path(__file__).resolve().parent

STAGE_LABELS = {
    "product": "产品卡", "benchmark": "对标与档位", "plan": "计划", "shots": "镜头表",
    "script": "脚本", "budget": "预算", "keyframe": "定场图", "generation": "生成",
    "compose": "合成", "review": "评审",
    "verdict": "人工判定", "publish": "发布", "done": "完成",
}


def _checkpoint(product, run, kind, subject):
    cid = checkpoints._id(product, run or "", kind, subject)
    try:
        return checkpoints.get(cid, product)
    except KeyError:
        return None


def _latest_run(meta, requested=None):
    run = requested or ((meta.get("runs") or [""])[-1])
    return run, products.product_dir(meta["product_id"]) / "runs" / run if run else None


def _briefs(run_dir):
    return sorted((run_dir / "briefs").glob("*.json")) if run_dir and (run_dir / "briefs").exists() else []


def _estimate(brief_paths):
    values = []
    for path in brief_paths:
        data = read_json(path, {}) or {}
        value = data.get("estimated_cny", data.get("estimate_cny"))
        if value is None:
            return None
        values.append(float(value))
    return round(sum(values), 4)


def _budget_details(brief_paths):
    """预算必须同时列出视频、定场图与 TTS；任一价格未知时总额保持未知。"""
    units = []
    needs_tts = False
    for path in brief_paths:
        brief = read_json(path, {}) or {}
        segments = brief.get("segments") or []
        if segments:
            for index, segment in enumerate(segments, 1):
                units.append({"kind": "video", "id": f"{brief.get('id', path.stem)}-s{index}",
                              "model": segment.get("model") or settings.video_route_model(segment.get("video_route", "default")),
                              "duration": segment.get("request_duration", segment.get("duration")),
                              "unitPrice": settings.video_unit_price(
                                  segment.get("model") or settings.video_route_model(segment.get("video_route", "default")),
                                  segment.get("resolution")),
                              "totalCny": segment.get("estimated_cny")})
                if segment.get("first_frame") == "chain:prev":
                    units.append({"kind": "chain_keyframe",
                                  "id": f"{brief.get('id', path.stem)}-s{index}-last",
                                  "model": settings.model("image"), "duration": None,
                                  "unitPrice": settings.image_price(),
                                  "totalCny": settings.image_price()})
                needs_tts = needs_tts or segment.get("audio_route", "tts") == "tts"
        else:
            units.append({"kind": "video", "id": str(brief.get("id") or path.stem),
                          "model": brief.get("model") or settings.video_route_model(brief.get("video_route", "default")),
                          "duration": brief.get("duration"),
                          "unitPrice": settings.video_unit_price(
                              brief.get("model") or settings.video_route_model(brief.get("video_route", "default")),
                              brief.get("resolution")),
                          "totalCny": brief.get("estimated_cny", brief.get("estimate_cny"))})
    units.append({"kind": "keyframe", "id": "scene", "model": settings.model("image"),
                  "duration": None, "unitPrice": settings.image_price(),
                  "totalCny": settings.image_price()})
    if needs_tts:
        units.append({"kind": "tts", "id": "voiceover", "model": settings.model("tts"),
                      "duration": None, "unitPrice": settings.tts_price(),
                      "totalCny": settings.tts_price()})
    values = [row.get("totalCny") for row in units]
    total = round(sum(float(value) for value in values), 4) if all(value is not None for value in values) else None
    return {"units": units, "estimated_cny": total, "totalCny": total}


def _file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def _queue_manual(product):
    for row in products.queue_list():
        if row["product"] == product:
            return row.get("stage")
    return None


def _benchmark_selection(meta, run_dir):
    """合并产品卡选择、运行镜头表和调研批次人工档位。"""
    selected = [dict(row) if isinstance(row, dict) else {"id": str(row)}
                for row in (meta.get("selected_benchmarks") or [])]
    shots = read_json(run_dir / "shots.json", {}) if run_dir else {}
    candidate_id = (shots or {}).get("candidate_id")
    if candidate_id and candidate_id not in {row.get("id") for row in selected}:
        selected.append({
            "id": candidate_id,
            "batch": (shots or {}).get("research_batch"),
            "reference_url": (shots or {}).get("reference_url") or (shots or {}).get("source_video_url"),
            "source": "shots.json",
        })
    for row in selected:
        batch_value = row.get("batch") or ((shots or {}).get("research_batch")
                                            if row.get("id") == candidate_id else None)
        if not batch_value:
            continue
        raw = Path(batch_value)
        candidates = [raw] if raw.is_absolute() else [
            settings.studio_home() / raw,
            settings.studio_home() / "research" / raw,
            products.product_dir(meta["product_id"]) / "research" / raw,
        ]
        batch = next((path for path in candidates if path.is_dir()), candidates[0])
        override = (read_json(batch / "tier_overrides.json", {}) or {}).get(row.get("id"))
        if override:
            row["tier"] = override.get("tier")
            row["tier_reason"] = override.get("note") or override.get("reason") or ""
            row["tier_source"] = "tier_overrides.json"
    return selected


def _registered_references(meta):
    refs = []
    for value in meta.get("reference_images") or []:
        path = Path(value).expanduser()
        refs.append(str(path if path.is_absolute() else settings.studio_home() / path))
    return refs


def _run_checkpoints(product, run):
    root = products.product_dir(product) / "checkpoints"
    rows = []
    for path in root.glob("cp-*.json") if root.exists() else ():
        row = read_json(path, {}) or {}
        if row.get("run") == run:
            rows.append(row)
    return rows


def _has_legacy_outputs(run_dir):
    if not run_dir:
        return False
    if (run_dir / "shots.json").is_file() or (run_dir / "script.json").is_file():
        return True
    return any(any((run_dir / name).iterdir()) for name in ("briefs", "takes", "reviews")
               if (run_dir / name).is_dir())


def _final_takes(run_dir):
    """父成片是独立判定对象；分段和 raw 文件只是父成片的附属。"""
    if not run_dir or not (run_dir / "takes").exists():
        return []
    segment_ids = set()
    for path in _briefs(run_dir):
        brief = read_json(path, {}) or {}
        if brief.get("segment_of"):
            segment_ids.add(path.stem)
        for segment in brief.get("segments") or []:
            if isinstance(segment, dict) and segment.get("id"):
                segment_ids.add(str(segment["id"]))
    takes = []
    for path in sorted((run_dir / "takes").glob("*.mp4")):
        if path.stem.endswith(".raw") or path.stem in segment_ids:
            continue
        takes.append(path.stem)
    return takes


def _review_time(record, decision, *paths):
    for source in (record, decision):
        for key in ("reviewed_at", "reevaluated_at", "created_at", "time", "timestamp"):
            if source.get(key):
                value = str(source[key])
                return value, value
    stamp = max((path.stat().st_mtime for path in paths if path.exists()), default=0)
    sort_time = dt.datetime.fromtimestamp(stamp, dt.timezone.utc).isoformat(timespec="seconds")
    return "旧记录（按文件时间）", sort_time


def review_history(run_dir, take):
    """返回同一成片的历次评审，最新一条在最后。"""
    reviews = run_dir / "reviews"
    if not reviews.exists():
        return []
    take_ids = sorted((path.stem for path in (run_dir / "takes").glob("*.mp4")
                       if not path.stem.endswith(".raw")), key=len, reverse=True)
    rows = []
    for decision_path in reviews.glob("*.decision.json"):
        review_id = decision_path.name.removesuffix(".decision.json")
        record_path = reviews / f"{review_id}.json"
        decision = read_json(decision_path, {}) or {}
        record = read_json(record_path, {}) or {}
        explicit = record.get("source_take") or record.get("take") or decision.get("take")
        if explicit in take_ids:
            record_take = explicit
        else:
            record_take = next((name for name in take_ids
                                if review_id == name or review_id.startswith(name + "-")), review_id)
        if record_take != take:
            continue
        reviewed_at, reviewed_at_sort = _review_time(record, decision, record_path, decision_path)
        rows.append({"take": take, "review_id": review_id, "reviewed_at": reviewed_at,
                     "reviewed_at_sort": reviewed_at_sort,
                     "decision": decision, "record": record,
                     "review": str(decision_path.relative_to(settings.studio_home()))})
    return sorted(rows, key=lambda row: (row["reviewed_at_sort"], row["review_id"]))


def _verdict_subject(run_dir, take):
    history = review_history(run_dir, take)
    if not history:
        return {}, {"take": take, "decision": None, "review": "", "reviewed_at": ""}
    latest = history[-1]
    decision = latest["decision"]
    return decision, {"take": take, "decision": decision.get("decision"),
                      "review": latest["review"], "reviewed_at": latest["reviewed_at"]}


def _verdict_checkpoint(product, run, take, subject, summary):
    existing = next((row for row in _run_checkpoints(product, run)
                     if row.get("kind") == "verdict" and
                     (row.get("subject") or {}).get("take") == take), None)
    if existing and existing.get("subject") != subject:
        return checkpoints.update_subject(existing["id"], summary, subject)
    return existing or _checkpoint(product, run, "verdict", subject)


def adopt(product, run=None):
    """显式接管引擎上线前的运行，只补建已有下游产物所证明的上游批准节点。"""
    meta = products.load(product)
    run_id, run_dir = _latest_run(meta, run)
    if not run_id or not run_dir or not run_dir.exists():
        raise ValueError(f"运行不存在：{run_id or '<空>'}")
    # 迁移旧运行时先把调研媒体本地化，确认台仍只需读 products/ 下的文件。
    from shot_list import copy_benchmark_media
    benchmark_media = copy_benchmark_media(run_dir, read_json(run_dir / "shots.json", {}) or {})
    evidence = []
    selected = _benchmark_selection(meta, run_dir)
    if (run_dir / "shots.json").exists() or _briefs(run_dir) or _final_takes(run_dir):
        evidence.append(("benchmark", "确认迁移运行已有对标与档位", {"selected": selected},
                         [run_dir / "shots.json"]))
    briefs = _briefs(run_dir)
    if (run_dir / "script.json").exists():
        script_path = run_dir / "script.json"
        script_subject = {"script": str(script_path.relative_to(settings.studio_home())),
                          "sha256": _file_sha256(script_path)}
        script_files = [run_dir / "script.json"]
    else:
        script_subject = {"briefs": [str(path.relative_to(settings.studio_home())) for path in briefs]}
        script_files = briefs
    if script_files and (_final_takes(run_dir) or (run_dir / "reviews").exists()):
        evidence.append(("script", "确认迁移运行已有脚本或生成简报", script_subject, script_files))
    if briefs and (_final_takes(run_dir) or (run_dir / "reviews").exists()):
        budget_subject = {"briefs": [str(path.relative_to(settings.studio_home())) for path in briefs],
                          **_budget_details(briefs)}
        evidence.append(("budget", "确认迁移运行已有生成预算", budget_subject, briefs))
    adopted = []
    for kind, summary, subject, paths in evidence:
        row = checkpoints.create(product, run_id, kind, summary, subject, ["迁移批准"])
        if row["status"] == "pending":
            names = [str(path.relative_to(settings.studio_home())) for path in paths if path.exists()]
            row = checkpoints.resolve(row["id"], "approved",
                                      "依据已有下游产物补建：" + "、".join(names), "migration")
        adopted.append(row)
    cover = benchmark_media["cover"]
    if cover["status"] == "copied":
        cover_note = "已复制对标封面"
    elif cover["status"] == "existing":
        cover_note = "已有对标封面本地副本"
    else:
        cover_note = f"未复制对标封面：{cover['reason']}"
    return {"product": product, "run": run_id, "adopted": adopted,
            "benchmark_media": benchmark_media,
            "message": f"已补建 {len(adopted)} 个迁移批准节点；{cover_note}"}


def status(product, run=None):
    """返回完全由当前数据推导的阶段与下一动作。"""
    pdir = products.product_dir(product)
    state_path = pdir / "product_state.json"
    budget = products.spent_cny(product) if state_path.exists() else None
    base = {"product": product, "run": run or "", "budget": budget, "blocking_checkpoint": None}
    if not state_path.exists():
        return {**base, "stage": "product", "stage_label": STAGE_LABELS["product"],
                "action": {"type": "human", "command": f"python3 kit/products.py create --product {product} --name '<品名>'",
                           "summary": "先建立产品卡"}, "manual_stage": _queue_manual(product), "stage_differs": False}
    meta = products.load(product)
    run_id, run_dir = _latest_run(meta, run)
    base["run"] = run_id
    plan_path = pdir / "plan.json"
    selected = _benchmark_selection(meta, run_dir)
    if _has_legacy_outputs(run_dir) and not _run_checkpoints(product, run_id):
        result = {**base, "stage": "benchmark", "stage_label": STAGE_LABELS["benchmark"],
                  "action": {"type": "human", "command": f"python3 kit/studio.py adopt --product {product} --run {run_id}",
                             "summary": "这是引擎上线前的运行；请显式 adopt，按已有下游产物补建可追溯批准节点"}}
        return _with_manual(result)
    # subject 只包含选择本身；生成 plan.json 后仍应命中同一个批准节点。
    benchmark_subject = {"selected": selected}
    benchmark_cp = _checkpoint(product, run_id, "benchmark", benchmark_subject)
    if not benchmark_cp or benchmark_cp["status"] != "approved":
        action = {"type": "checkpoint", "kind": "benchmark", "summary": "确认对标视频、复刻档位和制作范围",
                  "subject": benchmark_subject, "options": ["确认当前选择", "调整对标", "调整档位"]}
        result = {**base, "stage": "benchmark", "stage_label": STAGE_LABELS["benchmark"], "action": action,
                  "blocking_checkpoint": benchmark_cp}
        return _with_manual(result)
    if not plan_path.exists() and not (run_dir and (run_dir / "shots.json").exists()):
        result = {**base, "stage": "plan", "stage_label": STAGE_LABELS["plan"],
                  "action": {"type": "local", "command": [sys.executable, str(KIT / "intake.py"), "plan", "--product", product, "--n", "1", "--all"],
                             "summary": "本地计算一条制作计划", "estimated_cny": 0.0}}
        return _with_manual(result)
    if not run_id or not run_dir or not run_dir.exists():
        result = {**base, "stage": "plan", "stage_label": STAGE_LABELS["plan"],
                  "action": {"type": "local", "command": [sys.executable, str(KIT / "products.py"), "new-run", "--product", product, "--name", "engine"],
                             "summary": "建立本次运行目录", "estimated_cny": 0.0}}
        return _with_manual(result)
    if not (run_dir / "shots.json").exists():
        first = selected[0] if selected else {}
        command = [sys.executable, str(KIT / "shot_list.py"), "--project", str(run_dir),
                   "--batch", str(first.get("batch") or "<调研批次>"), "--id", str(first.get("id") or "<候选id>")]
        result = {**base, "stage": "shots", "stage_label": STAGE_LABELS["shots"],
                  "action": {"type": "paid", "command": command, "summary": "调用模型拆出镜头表", "estimated_cny": None,
                             "approval": benchmark_cp["id"]}}
        return _with_manual(result)
    early_briefs = _briefs(run_dir)
    if not (run_dir / "script.json").exists() and not early_briefs:
        facts = meta.get("anchor") or "<已确认产品事实>"
        result = {**base, "stage": "script", "stage_label": STAGE_LABELS["script"],
                  "action": {"type": "paid", "command": [sys.executable, str(KIT / "script_writer.py"), "--project", str(run_dir), "--facts", facts],
                             "summary": "调用脚本模型并运行代码校验", "estimated_cny": None, "approval": benchmark_cp["id"]}}
        return _with_manual(result)
    if (run_dir / "script.json").exists():
        script_path = run_dir / "script.json"
        script_subject = {"script": str(script_path.relative_to(settings.studio_home())),
                          "sha256": _file_sha256(script_path)}
    else:
        # H/M/L 路线的简报本身就是交给人确认的脚本与生成输入。
        script_subject = {"briefs": [str(path.relative_to(settings.studio_home())) for path in early_briefs]}
    script_cp = _checkpoint(product, run_id, "script", script_subject)
    if not script_cp or script_cp["status"] != "approved":
        result = {**base, "stage": "script", "stage_label": STAGE_LABELS["script"],
                  "action": {"type": "checkpoint", "kind": "script", "summary": "确认脚本、字幕和画面提示词",
                             "subject": script_subject, "options": ["批准", "要求修改", "驳回"]},
                  "blocking_checkpoint": script_cp}
        return _with_manual(result)
    briefs = early_briefs
    if not briefs:
        refs = _registered_references(meta)
        if not refs:
            result = {**base, "stage": "plan", "stage_label": STAGE_LABELS["plan"],
                      "action": {"type": "human", "command": f"python3 kit/products.py register-references --product {product} --files <产品图...>",
                                 "summary": "产品卡没有登记参考图；请先登记真实产品图，流程不会扫描目录猜测"}}
            return _with_manual(result)
        result = {**base, "stage": "plan", "stage_label": STAGE_LABELS["plan"],
                  "action": {"type": "local", "command": [sys.executable, str(KIT / "shot_plan.py"), "--project", str(run_dir), "--refs", ",".join(refs) or "<产品参考图>"],
                             "summary": "本地生成连续性计划", "estimated_cny": 0.0}}
        return _with_manual(result)
    budget = _budget_details(briefs)
    amount = budget["estimated_cny"]
    generation_values = [row.get("totalCny") for row in budget["units"]
                         if row.get("kind") in ("video", "chain_keyframe")]
    generation_amount = (round(sum(float(value) for value in generation_values), 4)
                         if all(value is not None for value in generation_values) else None)
    budget_subject = {"briefs": [str(p.relative_to(settings.studio_home())) for p in briefs], **budget}
    budget_cp = _checkpoint(product, run_id, "budget", budget_subject)
    if not budget_cp or budget_cp["status"] != "approved":
        result = {**base, "stage": "budget", "stage_label": STAGE_LABELS["budget"],
                  "action": {"type": "checkpoint", "kind": "budget", "summary": f"确认本轮预计费用 {amount if amount is not None else '未知'} 元",
                             "subject": budget_subject, "options": ["批准预算", "缩减计划", "驳回"]},
                  "blocking_checkpoint": budget_cp}
        return _with_manual(result)
    refs = _registered_references(meta)
    takes = sorted((run_dir / "takes").glob("*.mp4")) if (run_dir / "takes").exists() else []
    if not takes and not (run_dir / "scene.png").exists():
        if not refs:
            result = {**base, "stage": "keyframe", "stage_label": STAGE_LABELS["keyframe"],
                      "action": {"type": "human", "command": f"python3 kit/products.py register-references --product {product} --files <产品图...>",
                                 "summary": "生成定场图前必须登记真实产品图"}}
            return _with_manual(result)
        result = {**base, "stage": "keyframe", "stage_label": STAGE_LABELS["keyframe"],
                  "action": {"type": "paid", "command": [sys.executable, str(KIT / "keyframe.py"), "scene",
                             "--project", str(run_dir), "--product-reference", refs[0]],
                             "summary": "生成共用定场图后停止", "estimated_cny": settings.image_price(),
                             "approval": budget_cp["id"]}}
        return _with_manual(result)
    if not takes:
        brief = briefs[0]
        brief_data = read_json(brief, {}) or {}
        cap = max(1, len(brief_data.get("segments") or []))
        result = {**base, "stage": "generation", "stage_label": STAGE_LABELS["generation"],
                  "action": {"type": "paid", "command": [sys.executable, str(KIT / "loop.py"), "gen", "--project", str(run_dir), "--brief", str(brief), "--cap", str(cap), "--no-review"],
                             "summary": "生成一个视频步骤后停止", "estimated_cny": generation_amount, "approval": budget_cp["id"]}}
        return _with_manual(result)
    final_takes = _final_takes(run_dir)
    if not final_takes:
        final_takes = [takes[0].stem]
    for take in final_takes:
        job = read_json(run_dir / "jobs" / f"{take}.json", {}) or {}
        if not job or job.get("composed"):
            continue
        brief = next((path for path in briefs if path.stem == take), briefs[0])
        brief_data = read_json(brief, {}) or {}
        needs_tts = any(row.get("audio_route", "tts") == "tts"
                        for row in brief_data.get("segments") or [])
        refs = _registered_references(meta)
        action_type = "paid" if needs_tts else "local"
        action = {"type": action_type,
                  "command": [sys.executable, str(KIT / "compose.py"), "--project", str(run_dir),
                              "--brief", str(brief), "--refs", ",".join(refs)],
                  "summary": "按声音路线合成字幕、口播和音乐后停止",
                  "estimated_cny": settings.tts_price() if needs_tts else 0.0}
        if action_type == "paid":
            action["approval"] = budget_cp["id"]
        result = {**base, "stage": "compose", "stage_label": STAGE_LABELS["compose"], "action": action}
        return _with_manual(result)
    for take in final_takes:
        if review_history(run_dir, take):
            continue
        refs = _registered_references(meta)
        if not refs:
            result = {**base, "stage": "review", "stage_label": STAGE_LABELS["review"],
                      "action": {"type": "human", "command": f"python3 kit/products.py register-references --product {product} --files <产品图...>",
                                 "summary": "产品卡没有登记参考图；评审前必须登记，不能传空 --refs"}}
            return _with_manual(result)
        result = {**base, "stage": "review", "stage_label": STAGE_LABELS["review"],
                  "action": {"type": "paid", "command": [sys.executable, str(KIT / "loop.py"), "review", "--project", str(run_dir), "--take", take, "--refs", ",".join(refs)],
                             "summary": "评审一条成片后停止", "estimated_cny": None, "approval": budget_cp["id"]}}
        return _with_manual(result)
    for take in final_takes:
        decision, verdict_subject = _verdict_subject(run_dir, take)
        if decision.get("decision") == "repair":
            result = {**base, "stage": "review", "stage_label": STAGE_LABELS["review"],
                      "action": {"type": "human", "summary": f"{take} 的闸门要求返修，请按 review 中的 retry_from 处理", "command": "查看评审记录"}}
            return _with_manual(result)
        if decision.get("decision") not in ("pass", "escalate"):
            result = {**base, "stage": "review", "stage_label": STAGE_LABELS["review"],
                      "action": {"type": "human", "summary": f"{take} 的闸门结论无法自动推进：{decision.get('decision') or '缺失'}",
                                 "command": "查看评审记录"}}
            return _with_manual(result)
        verdict_summary = f"人工判断成片 {take} 是否可用"
        verdict_cp = _verdict_checkpoint(product, run_id, take, verdict_subject, verdict_summary)
        if decision.get("decision") in ("pass", "escalate") and (not verdict_cp or verdict_cp["status"] == "pending"):
            result = {**base, "stage": "verdict", "stage_label": STAGE_LABELS["verdict"],
                      "action": {"type": "checkpoint", "kind": "verdict", "summary": verdict_summary,
                                 "subject": verdict_subject, "options": ["可用", "要求修改", "不用"]},
                      "blocking_checkpoint": verdict_cp}
            return _with_manual(result)
        if verdict_cp and verdict_cp["status"] == "approved":
            publish_subject = {"take": take, "account": "", "url": ""}
            publish_cp = _checkpoint(product, run_id, "publish", publish_subject)
            if not publish_cp or publish_cp["status"] == "pending":
                result = {**base, "stage": "publish", "stage_label": STAGE_LABELS["publish"],
                          "action": {"type": "checkpoint", "kind": "publish", "summary": f"记录成片 {take} 是否发布、发布账号和链接",
                                     "subject": publish_subject, "options": ["已发布", "暂不发布"]}, "blocking_checkpoint": publish_cp}
                return _with_manual(result)
    return _with_manual({**base, "stage": "done", "stage_label": STAGE_LABELS["done"], "action": None})


def _with_manual(result):
    manual = _queue_manual(result["product"])
    result["manual_stage"] = manual
    result["stage_differs"] = bool(manual and manual != result["stage"])
    return result


def _approved_amount(row):
    return checkpoints._amount(row.get("subject"))


def advance(product, run=None, dry_run=False, runner=None):
    """推进恰好一步。返回 (退出码, 结构化结果)。"""
    current = status(product, run)
    action = current.get("action")
    if not action:
        return 0, {**current, "message": "流程已经完成"}
    if action["type"] == "checkpoint":
        if dry_run:
            return 0, {**current, "message": "将创建确认节点（dry-run 未写入）"}
        cp = checkpoints.create(product, current.get("run"), action["kind"], action["summary"],
                                action["subject"], action.get("options"))
        if cp["status"] == "approved":
            return 0, {**current, "blocking_checkpoint": cp, "message": "确认节点已自动批准；再次运行 next 继续"}
        return 3, {**current, "blocking_checkpoint": cp,
                   "message": f"等待确认：{cp['summary']}；打开确认台：python3 kit/studio.py console；"
                              f"也可使用命令批准：python3 kit/studio.py approve {cp['id']}"}
    if dry_run:
        return 0, {**current, "message": "dry-run：未执行", "would_run": action.get("command")}
    if action["type"] == "human":
        return 3, {**current, "message": action["summary"]}
    if action["type"] == "paid":
        approval = checkpoints.get(action["approval"], product)
        if approval["status"] != "approved":
            return 3, {**current, "message": "付费步骤尚未批准"}
        amount = action.get("estimated_cny")
        approved = _approved_amount(approval)
        if amount is not None and approved is not None and amount > approved + 1e-9:
            return 3, {**current, "message": "预计金额超过已批准额度"}
        if amount is not None and not products.can_spend(product, amount)[0]:
            return 3, {**current, "message": "预计金额超过品剩余预算"}
    run_command = runner or (lambda command: subprocess.run(command, check=False).returncode)
    code = run_command(action["command"])
    return code, {**current, "executed": action["command"],
                  "message": "已执行一个步骤并停止" if code == 0 else f"步骤执行失败，退出码 {code}"}
