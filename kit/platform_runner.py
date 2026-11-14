#!/usr/bin/env python3
"""从流域中台领取制作请求，并按 studio 引擎推进完整流水线。"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

KIT = Path(__file__).resolve().parent
sys.path.insert(0, str(KIT))

import checkpoints  # noqa: E402
import engine  # noqa: E402
import intake  # noqa: E402
import loop  # noqa: E402
import products  # noqa: E402
import settings  # noqa: E402
import tt_research as tt  # noqa: E402
from common import _data_uri, read_json, write_json  # noqa: E402
from platform_client import PlatformClient, PlatformError  # noqa: E402


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _safe_id(value):
    cleaned = re.sub(r"[^a-z0-9_-]+", "-", str(value).lower()).strip("-")
    return "platform-" + (cleaned or hashlib.sha256(str(value).encode()).hexdigest()[:16])


def _state_root():
    return settings.studio_home() / "platform-runner"


def _state_path(request_id):
    return _state_root() / f"{_safe_id(request_id)}.json"


def _save_state(state):
    write_json(_state_path(state["request"]["id"]), state)


def _active_states():
    rows = []
    for path in sorted(_state_root().glob("platform-*.json")) if _state_root().exists() else ():
        row = read_json(path, {}) or {}
        if row.get("phase") not in ("done", "failed") and row.get("request"):
            rows.append(row)
    return rows


def _default_anchor(product_name, image_paths):
    prompt = (
        "Inspect the product reference images. Return exactly one concise English sentence describing only "
        "the product's visible appearance. The sentence must contain this exact product name verbatim: "
        f"{product_name}. Do not use Markdown or add unverified claims."
    )
    parts = [{"type": "text", "text": prompt}]
    parts += [{"type": "image_url", "image_url": {"url": _data_uri(path)}} for path in image_paths]
    value = loop.gemini(parts, project="platform-anchor", ref=str(product_name),
                        max_tokens=500, temperature=0.1, role="anchor")
    anchor = " ".join(str(value).strip().strip('"').split())
    if not anchor or str(product_name).lower() not in anchor.lower():
        raise ValueError("产品锚点必须非空并包含产品名")
    return anchor


def _default_benchmark(product_id, url):
    """复用 intake 的手动对标登记；已有同 URL 时不再次查询。"""
    existing = read_json(intake.batch_dir(product_id) / "candidates.json", []) or []
    found = next((row for row in existing if row.get("url") == url), None)
    if found:
        return found
    client = tt.Client(tt.load_key(), intake.batch_dir(product_id),
                       budget_calls=settings.get_int("LIMIT_TIKHUB_CALLS"))
    candidate = intake.fetch_url_candidate(url, client)
    return intake.register(product_id, candidate, "same")[0]


def _run_command(command):
    return subprocess.run(command, check=False).returncode


def _content_extension(headers):
    value = next((v for k, v in headers.items() if k.lower() == "content-type"), "")
    return {"image/png": ".png", "image/webp": ".webp"}.get(value.split(";")[0], ".jpg")


def _script_payload(run_dir):
    script = read_json(run_dir / "script.json", {}) or {}
    shots = read_json(run_dir / "shots.json", {}) or {}
    source = {str(row.get("id")): row for row in shots.get("shots") or []}
    audio_mode = shots.get("audio_mode") or "voiceover"
    try:
        route = __import__("shot_plan").audio_route(audio_mode)
    except ValueError:
        route = "tts"
    segments = []
    for row in script.get("shots") or []:
        timing = source.get(str(row.get("id")), {})
        segments.append({
            "id": row.get("id"),
            "duration": round(float(timing.get("end", 0)) - float(timing.get("start", 0)), 3),
            "visualPrompt": row.get("visual_prompt", ""),
            "voiceover": row.get("voiceover", ""),
            "caption": row.get("caption", ""),
            "onscreenCopy": row.get("onscreen_copy", ""),
            "audioRoute": route,
        })
    return {"segments": segments, "scene": script.get("scene", "")}


def _checkpoint_payload(row, run_dir):
    if row["kind"] == "script":
        return _script_payload(run_dir)
    if row["kind"] == "budget":
        subject = row.get("subject") or {}
        return {"units": subject.get("units") or [], "totalCny": subject.get("totalCny"),
                "estimatedCny": subject.get("estimated_cny")}
    return {"subject": row.get("subject") or {}}


def _duration(path):
    raw = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                   "-of", "default=nw=1:nk=1", str(path)], text=True).strip()
    value = float(raw)
    if value <= 0:
        raise ValueError("成片时长无效")
    return value


def _internal_result(product_id, run_dir, take):
    review = read_json(run_dir / "reviews" / f"{take}.json", {}) or {}
    decision = read_json(run_dir / "reviews" / f"{take}.decision.json", {}) or {}
    issues = []
    for expert in review.get("experts") or []:
        for issue in expert.get("issues") or expert.get("problems") or []:
            issues.append({"expert": expert.get("name") or expert.get("id"), "issue": issue})
    jobs = []
    for path in sorted((run_dir / "jobs").glob("*.json")) if (run_dir / "jobs").exists() else ():
        row = read_json(path, {}) or {}
        jobs.append({"id": row.get("id") or path.stem, "model": row.get("model"),
                     "status": row.get("status")})
    essence = review.get("essence") or decision.get("essence") or {}
    return {
        "essence": essence,
        "expertIssues": issues,
        "cost": products.spent_cny(product_id),
        "models": {"configured": settings.stamp().get("models"), "jobs": jobs},
        "reviewDecision": decision,
    }


def _verdict_result(data):
    review_status = str(data.get("reviewStatus") or "")
    if review_status in ("approved", "rejected", "changes_requested"):
        reason = next((row.get("reason") for row in reversed(data.get("cases") or [])
                       if row.get("decision") == review_status and row.get("reason")), "")
        return review_status, reason
    for row in reversed(data.get("cases") or []):
        decision = row.get("decision")
        if decision in ("approved", "rejected", "changes_requested"):
            return decision, row.get("reason") or ""
    return None, ""


def _append_human_decision(run_dir, take, decision, reason, expert_decision):
    now = dt.datetime.now(dt.timezone.utc)
    verdict = "accept" if decision == "approved" else ("iterate" if decision == "changes_requested" else "abandon")
    note = str(reason or ("中台审核通过" if decision == "approved" else "中台审核未通过")).strip()
    if len(note) < 8:
        note += "（来自中台审核）"
    path = run_dir / "human_decisions.jsonl"
    if path.exists():
        for line in reversed(path.read_text(encoding="utf-8").splitlines()):
            previous = json.loads(line)
            previous_key = (previous.get("take"), previous.get("decision"),
                            previous.get("reason"), previous.get("reviewer"))
            if previous_key == (take, verdict, note, "platform-request"):
                return previous
    row = {
        "schema_version": "VIDEO-HITL-decision-v1",
        "decision_id": f"VHITL-{now:%Y%m%d}-platform-{now:%H%M%S%f}",
        "gate": "H1", "decision": verdict, "project": run_dir.name, "take": take,
        "expert_decision": expert_decision, "reason": note,
        "reviewer": "platform-request", "decided_at": now.isoformat(timespec="seconds"),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


class Runner:
    def __init__(self, client, *, anchor_builder=None, benchmark_registrar=None,
                 command_runner=None, sleeper=time.sleep):
        self.client = client
        self.anchor_builder = anchor_builder or _default_anchor
        self.benchmark_registrar = benchmark_registrar or _default_benchmark
        self.command_runner = command_runner or _run_command
        self.sleep = sleeper

    def run_once(self):
        active = _active_states()
        if active:
            return self.process(active[0])
        requests = self.client.queued()
        if not requests:
            print("没有待领取的中台制作请求")
            return None
        request = requests[0]
        try:
            claimed = self.client.claim(request["id"])
        except PlatformError as exc:
            if exc.status == 409:
                print(f"请求 {request['id']} 已被其他执行机领取")
                return None
            raise
        state = {"request": {**request, **claimed}, "phase": "claimed", "createdAt": _now(),
                 "checkpointMap": {}}
        _save_state(state)
        return self.process(state)

    def _fail(self, state, message):
        state.update(phase="failed", error=str(message)[:1000], updatedAt=_now())
        _save_state(state)
        try:
            self.client.progress(state["request"]["id"], "failed", str(message)[:1000], "failed")
        except Exception:  # 原始异常优先；上报失败只保存在本地状态。
            pass
        print(f"请求 {state['request']['id']} 失败：{message}")

    def process(self, state):
        try:
            if not state.get("productId"):
                self._prepare(state)
            if state.get("phase") == "waiting_verdict":
                return self._poll_verdict(state)
            return self._advance(state)
        except Exception as exc:  # noqa: BLE001
            self._fail(state, f"{type(exc).__name__}: {str(exc)[:800]}")
            return state

    def _prepare(self, state):
        request = state["request"]
        budget = float(request["budgetCny"])
        limit = settings.get_float("PRODUCT_BUDGET_CNY")
        if budget > limit + 1e-9:
            raise ValueError(f"请求预算 {budget:g} 元超过单品上限 {limit:g} 元")
        pid = _safe_id(request["id"])
        try:
            meta = products.load(pid)
        except SystemExit:
            meta = products.create(pid, request["productName"], budget_cny=budget)
        products.set_facts(pid, request.get("productFacts") or [])
        ref_dir = products.product_dir(pid) / "platform-refs"
        refs = []
        for index in range(1, int(request.get("productRefCount") or 0) + 1):
            existing = next(iter(ref_dir.glob(f"{index}.*")), None) if ref_dir.exists() else None
            if existing:
                refs.append(str(existing.resolve()))
                continue
            body, headers = self.client.download_ref(request["id"], index)
            path = ref_dir / f"{index}{_content_extension(headers)}"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            refs.append(str(path.resolve()))
        if not refs:
            raise ValueError("中台请求没有产品参考图")
        products.register_references(pid, refs)
        anchor = str(meta.get("anchor") or "").strip()
        if not anchor:
            anchor = self.anchor_builder(request["productName"], refs)
            if not anchor or request["productName"].lower() not in anchor.lower():
                raise ValueError("产品锚点必须非空并包含产品名")
            products.set_anchor(pid, anchor)
        candidate = self.benchmark_registrar(pid, request["benchmarkUrl"])
        cid = candidate.get("candidate_id") or candidate.get("id")
        if not cid:
            raise ValueError("手动对标登记没有返回候选 id")
        intake.confirm_tier(pid, cid, request["tier"], "中台请求已由人选择档位",
                            decided_by="platform-request")
        products.select_benchmarks(pid, [cid], str(intake.batch_dir(pid)))
        run_name = "platform-" + hashlib.sha256(request["id"].encode()).hexdigest()[:10]
        existing_runs = products.load(pid).get("runs") or []
        run_id = next((value for value in existing_runs if value.endswith("-" + run_name)), None)
        run_dir = products.product_dir(pid) / "runs" / run_id if run_id else products.new_run(pid, run_name)
        run_id = run_dir.name
        current = engine.status(pid, run_id)
        action = current.get("action") or {}
        if action.get("kind") == "benchmark":
            cp = checkpoints.create(pid, run_id, "benchmark", action["summary"], action["subject"], action.get("options"))
            if cp["status"] == "pending":
                checkpoints.resolve(cp["id"], "approved", "档位和对标来自中台已确认请求", "platform-request")
        state.update(productId=pid, run=run_id, phase="running", updatedAt=_now())
        _save_state(state)
        self.client.progress(request["id"], "prepared", "已建品、登记产品图与对标，并确认请求档位")

    def _advance(self, state):
        pid, run_id = state["productId"], state["run"]
        run_dir = products.product_dir(pid) / "runs" / run_id
        for _ in range(50):
            current = engine.status(pid, run_id)
            action = current.get("action")
            if not action:
                state.update(phase="done", updatedAt=_now())
                _save_state(state)
                return state
            if current["stage"] == "verdict":
                return self._upload(state, current)
            if action["type"] == "checkpoint":
                code, result = engine.advance(pid, run_id)
                cp = result.get("blocking_checkpoint")
                if code != 3 or not cp:
                    continue
                if cp["kind"] not in ("script", "budget"):
                    raise RuntimeError(f"无法映射的本地确认节点：{cp['kind']}")
                if not self._resolve_platform_checkpoint(state, cp, run_dir):
                    return state
                continue
            code, result = engine.advance(pid, run_id, runner=self.command_runner)
            if code != 0:
                raise RuntimeError(result.get("message") or f"引擎步骤退出码 {code}")
            self.client.progress(state["request"]["id"], current["stage"], result.get("message") or action["summary"])
            state.update(phase="running", stage=current["stage"], updatedAt=_now())
            _save_state(state)
        raise RuntimeError("单轮推进超过 50 步，停止以防状态机失控")

    def _resolve_platform_checkpoint(self, state, cp, run_dir):
        request_id = state["request"]["id"]
        mapping = state.setdefault("checkpointMap", {})
        external_id = mapping.get(cp["id"], cp["id"])
        if cp["id"] not in mapping:
            self.client.create_checkpoint(request_id, external_id, cp["kind"], cp["summary"],
                                          _checkpoint_payload(cp, run_dir))
            mapping[cp["id"]] = external_id
            _save_state(state)
        remote = next((row for row in self.client.checkpoints(request_id)
                       if row.get("externalId") == external_id or row.get("external_id") == external_id), None)
        if not remote or remote.get("status") == "pending":
            state.update(phase="waiting_human", stage=cp["kind"], updatedAt=_now())
            _save_state(state)
            self.client.progress(request_id, cp["kind"], cp["summary"], "waiting_human")
            return False
        status = remote.get("status")
        if status not in ("approved", "rejected", "changes_requested"):
            raise ValueError(f"中台节点状态无效：{status}")
        note = remote.get("decisionNote") or remote.get("decision_note") or ""
        edits = remote.get("edits") or []
        try:
            checkpoints.resolve(cp["id"], status, note, remote.get("decidedBy") or "platform-request", edits)
        except (KeyError, TypeError, ValueError) as exc:
            if cp["kind"] != "script":
                raise
            message = str(exc)
            retry_id = f"{cp['id']}:validation:{hashlib.sha256(message.encode()).hexdigest()[:10]}"
            payload = _checkpoint_payload(cp, run_dir)
            payload.update(validationError=message, previousExternalId=external_id)
            self.client.create_checkpoint(request_id, retry_id, "script", "脚本修改校验失败，请修正后重新提交", payload)
            mapping[cp["id"]] = retry_id
            state.update(phase="waiting_human", stage="script", updatedAt=_now())
            _save_state(state)
            self.client.progress(request_id, "script", f"脚本修改校验失败：{message}", "waiting_human")
            return False
        if status != "approved":
            raise RuntimeError(f"中台{cp['kind']}节点{status}：{note or '未填写原因'}")
        state.update(phase="running", updatedAt=_now())
        _save_state(state)
        return True

    def _upload(self, state, current):
        request, pid, run_id = state["request"], state["productId"], state["run"]
        run_dir = products.product_dir(pid) / "runs" / run_id
        cp = current.get("blocking_checkpoint")
        if not cp:
            engine.advance(pid, run_id)
            current = engine.status(pid, run_id)
            cp = current.get("blocking_checkpoint")
        take = (cp.get("subject") or {}).get("take")
        video = run_dir / "takes" / f"{take}.mp4"
        if not video.is_file():
            raise FileNotFoundError(f"成片不存在：{video}")
        version_id = state.get("versionId")
        if not version_id:
            upload_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"studio:{request['id']}:{take}"))
            version_id = self.client.upload_video(video, company_id=request["companyId"],
                                                  project_id=request["projectId"],
                                                  title=f"{request['productName']} · studio 成片",
                                                  duration=_duration(video), upload_id=upload_id)
            state.update(versionId=version_id, take=take, verdictCheckpoint=cp["id"], updatedAt=_now())
            _save_state(state)
        self.client.complete(request["id"], version_id, _internal_result(pid, run_dir, take))
        state.update(phase="waiting_verdict", updatedAt=_now())
        _save_state(state)
        print(f"请求 {request['id']} 成片已上传，等待中台审核")
        return self._poll_verdict(state)

    def _poll_verdict(self, state):
        data = self.client.verdict(state["request"]["id"])
        decision, reason = _verdict_result(data)
        if not decision:
            print(f"请求 {state['request']['id']} 尚无最终审核结果")
            return state
        run_dir = products.product_dir(state["productId"]) / "runs" / state["run"]
        cp = checkpoints.get(state["verdictCheckpoint"], state["productId"])
        local_status = "approved" if decision == "approved" else (
            "changes_requested" if decision == "changes_requested" else "rejected")
        note = reason or ("中台审核通过" if decision == "approved" else "中台审核未通过")
        if cp.get("status") != local_status or cp.get("note") != note:
            checkpoints.resolve(cp["id"], local_status, note, "platform-request")
        _append_human_decision(run_dir, state["take"], decision, reason,
                               (cp.get("subject") or {}).get("decision"))
        state.update(phase="done", platformVerdict=decision, verdictReason=reason, updatedAt=_now())
        _save_state(state)
        print(f"请求 {state['request']['id']} 中台审核结果已回写：{decision}")
        return state


DRY_RUN_STEPS = [
    "领取 queued 请求", "校验请求预算不超过单品上限", "建品并下载、登记产品参考图",
    "视觉模型生成含产品名的英文产品锚点", "登记手动对标并按 platform-request 确认档位",
    "本地批准 benchmark 节点", "拆镜头表", "生成并校验脚本", "上报 script 并等待决定",
    "生成连续性计划", "上报含视频、定场图、TTS 的 budget 并等待决定", "生成定场图",
    "生成视频（在途任务只查询原任务）", "按声音路线合成", "评审并计算神韵",
    "上传成片版本并 complete", "读取中台 verdict，回写本地节点与 human_decisions.jsonl",
]


def print_dry_run():
    print("假中台完整步骤序列（不写文件、不调用付费接口）：")
    for index, step in enumerate(DRY_RUN_STEPS, 1):
        print(f"{index:02d}. {step}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="流域中台 studio 制作执行机")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="处理一轮后退出")
    mode.add_argument("--loop", action="store_true", help="持续轮询（默认间隔 20 秒）")
    parser.add_argument("--interval", type=float, default=20)
    parser.add_argument("--dry-run", action="store_true", help="只打印假中台完整步骤序列")
    args = parser.parse_args(argv)
    if args.dry_run:
        print_dry_run()
        return 0
    runner = Runner(PlatformClient())
    if not args.loop:
        runner.run_once()
        return 0
    print(f"执行机已启动，每 {args.interval:g} 秒轮询一次；Ctrl-C 安全退出")
    try:
        while True:
            runner.run_once()
            time.sleep(max(0.1, args.interval))
    except KeyboardInterrupt:
        print("执行机已安全退出")
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, PlatformError) as exc:
        sys.exit(f"执行机失败：{exc}")
