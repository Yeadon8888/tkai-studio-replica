"""三专家评审面板：机器检查 → 三位专家打分 → 合并 → 闸门判定。

流程（ARCHITECTURE-v2 §4）
1. 机器检查（计算型，阻塞级）：时长、分辨率、宽高比、文件大小。不过则不交给专家，直接 repair（回到 generate）。
2. 三位专家（推断型，见 config/experts.yaml）各自打分，只负责自己的维度。
3. gate.merge_judges：逐项取较低值；否决项取并集。
4. gate.evaluate：加权总分 ≥ 8.0、任一维度 ≥ 6.5、无否决，才 pass。

输出：reviews/<take>.json（完整记录）与 reviews/<take>.decision.json（判定结果，供看板和人工决定使用）。
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

KIT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, KIT)

import yaml  # noqa: E402

import gate  # noqa: E402
from common import _read, _write  # noqa: E402
import loop  # noqa: E402  (提供 gemini()、_frames()、_data_uri()、tt)

EXPERTS_PATH = os.path.join(KIT, "config", "experts.yaml")


def load_experts(path=EXPERTS_PATH):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _probe(video):
    out = subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-show_format",
                                   "-of", "json", str(video)])
    return json.loads(out.decode("utf-8"))


def machine_checks(video, brief):
    """计算型检查（阻塞级）。返回 {"blocking": [...], "info": [...], "facts": {...}}。"""
    blocking, info, facts = [], [], {}
    if not Path(video).exists():
        return {"blocking": ["没有成片文件"], "info": [], "facts": {}}
    pr = _probe(video)
    v = next((s for s in pr.get("streams", []) if s.get("codec_type") == "video"), None)
    if not v:
        blocking.append("没有视频流")
    else:
        w, h = int(v.get("width", 0)), int(v.get("height", 0))
        dur = float(pr.get("format", {}).get("duration", 0))
        facts.update(width=w, height=h, duration=round(dur, 2), codec=v.get("codec_name"))
        want = int(str(brief.get("resolution", "768p")).rstrip("p") or 768)
        if min(w, h) != want:
            blocking.append(f"短边 {min(w, h)} 与分辨率 {want}p 不符")
        ar = brief.get("aspect_ratio", "9:16")
        a, b = (int(x) for x in ar.split(":"))
        if w and abs((h / w) - (b / a)) > 0.03 and abs((w / h) - (b / a)) > 0.03:
            blocking.append(f"画面 {w}×{h} 与宽高比 {ar} 不符")
        want_dur = float(brief.get("duration", 10))
        if abs(dur - want_dur) > 1.0:
            blocking.append(f"时长 {dur:.1f}s 与简报 {want_dur:g}s 相差超过 1 秒")
    size = Path(video).stat().st_size
    facts["bytes"] = size
    if size < 50_000:
        blocking.append(f"文件只有 {size} 字节，疑似空视频")
    return {"blocking": blocking, "info": info, "facts": facts}


def frame_times(n, duration):
    """抽帧时间表：与 loop._frames 的 fps=n/dur 一致，第 k 帧在 (k-1)*dur/n 秒。给专家写时间码用。"""
    step = duration / n
    out = []
    for k in range(n):
        t = k * step
        out.append(f"f{k + 1:02d}={int(t // 60):02d}:{t % 60:04.1f}")
    return ", ".join(out)


NO_MUST_KEEP = "（简报没有列出必须项：只按简报摘要判断，无法判断的写'看不清'，不要自己假设动作顺序）"
AUDIO_NONE = "简报没有口播：写 \"无口播，不评\"，给 10 分，不列 issues。"
AUDIO_HAS = ("简报有口播，但你只看得到抽帧、听不到声音：audio_text 给 7 分，"
             "并写一条 issue（cause=audio_sync，detail=\"有口播，需人工听\"，at 取第一帧时间）。")


def must_keep_text(brief):
    """必须项来自简报本身：must_keep（列表）优先；自写脚本用各镜头动作；都没有则明说没有。"""
    items = brief.get("must_keep")
    if not items and isinstance(brief.get("script"), dict):
        items = [f"{s.get('start')}–{s.get('end')}s：{s.get('action_zh') or s.get('scene_zh') or ''}"
                 for s in brief["script"].get("shots", [])]
    if not items:
        return NO_MUST_KEEP
    return "\n      ".join(f"{i + 1}. {x}" for i, x in enumerate(items))


def _expert_prompt(exp, brief, anchor, frame_times_text=""):
    shared = exp.get("shared_rules", "")
    focus = (exp["focus"].strip()
             .replace("{must_keep}", must_keep_text(brief))
             .replace("{duration}", f"{float(brief.get('duration', 10)):g}")
             .replace("{audio_rule}", AUDIO_HAS if brief.get("voiceover") else AUDIO_NONE))
    return (exp["prompt"]
            .replace("{brief}", (brief.get("summary") or brief.get("prompt", ""))[:800])
            .replace("{anchor}", anchor or "（简报未提供锚点）")
            .replace("{frame_times}", frame_times_text or "（未提供）")
            .replace("{shared_rules}", shared.strip())
            .replace("{focus}", focus))


def run_expert(exp, project_name, take, brief, anchor, refs, frames):
    ft = frame_times(len(frames), float(brief.get("duration", 10)))
    prompt = _expert_prompt(exp, brief, anchor, ft)
    parts = [{"type": "text", "text": prompt + "\n前面是产品参考图，后面是成片抽帧（按时间顺序）。"}]
    parts += [{"type": "image_url", "image_url": {"url": loop._data_uri(p)}} for p in refs]
    parts += [{"type": "image_url", "image_url": {"url": loop._data_uri(p)}} for p in frames]
    text = loop.gemini(parts, project=project_name, ref=f"{take}:{exp['id']}", max_tokens=3500,
                       temperature=exp.get("temperature", 0.1), role="review")
    obj = loop.tt.parse_json_obj(text)
    if obj is None:
        return None
    obj["judge"] = {"id": exp["id"], "name": exp["name"], "temperature": exp.get("temperature", 0.1),
                    "frames": len(frames), "frame_times": ft}
    return obj


def review_take(project, take, refs, experts_path=EXPERTS_PATH):
    project = Path(project)
    brief = _read(project / "briefs" / f"{take}.json")
    video = project / "takes" / f"{take}.mp4"
    attempt = brief.get("attempt", 1)
    anchor = brief.get("product_anchor") or _anchor_from_refs(project)
    mc = machine_checks(video, brief)
    import settings
    record = {"take": take, "attempt": attempt, "machine": mc, "experts": [], "versions": settings.stamp()}
    if mc["blocking"]:
        # 计算型阻塞：不花钱调用专家，直接退回生成
        decision = {"attempt": attempt, "decision": "repair", "stage": "glitches", "retry_from": "generate",
                    "reason": "机器检查未通过：" + "；".join(mc["blocking"]),
                    "weighted": None, "low_dimensions": [], "vetoes": []}
        record["decision"] = decision
        _write(project / "reviews" / f"{take}.json", record)
        _write(project / "reviews" / f"{take}.decision.json", decision)
        return decision
    frames = loop._frames(video, project / "reviews" / "frames" / take)
    cfg = load_experts(experts_path)
    refs_p = [Path(p) for p in refs]
    judges, failed = [], []
    for exp in cfg["experts"]:
        obj = None
        for attempt_no in range(1, 4):  # 上游瞬时错误（524、断连）最多重试 3 次；评审是只读调用，重跑不改视频
            try:
                obj = run_expert(exp, project.name, take, brief, anchor, refs_p, frames)
                break
            except Exception as e:
                print(f"{take} {exp['id']} 第 {attempt_no} 次调用失败：{type(e).__name__} {getattr(e, 'code', '')}")
                if attempt_no < 3:
                    import time
                    time.sleep(10 * attempt_no)
        if obj is None:
            failed.append(exp["id"])
        else:
            judges.append(obj)
            record["experts"].append(obj)
    if failed:
        decision = {"attempt": attempt, "decision": None,
                    "reason": f"专家输出无法解析：{', '.join(failed)}，需重跑评审（不重生成视频）"}
        record["decision"] = decision
        _write(project / "reviews" / f"{take}.json", record)
        _write(project / "reviews" / f"{take}.decision.json", decision)
        return decision
    merged = gate.merge_judges(judges)
    res = gate.evaluate(merged, attempt)
    record["merged"] = merged
    record["decision"] = res
    _write(project / "reviews" / f"{take}.json", record)
    _write(project / "reviews" / f"{take}.decision.json", res)
    return res


def reevaluate(project, take):
    """用已保存的专家原始打分重新合并判定，不调用任何模型，不花钱。用于规则修正后的回放。"""
    project = Path(project)
    rec_path = project / "reviews" / f"{take}.json"
    rec = _read(rec_path)
    judges = rec.get("experts") or []
    if not judges:
        return rec.get("decision")
    attempt = rec.get("attempt", 1)
    merged = gate.merge_judges(judges)
    res = gate.evaluate(merged, attempt)
    rec["merged"] = merged
    rec["decision"] = res
    rec["reevaluated"] = True
    _write(rec_path, rec)
    _write(project / "reviews" / f"{take}.decision.json", res)
    return res


def _anchor_from_refs(project):
    p = project / "product_anchor.txt"
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


def _summary(take, res):
    if res.get("decision") is None:
        return f"{take}: 未判定（{res.get('reason')}）"
    w = res.get("weighted")
    low = ",".join(res.get("low_dimensions", []) or []) or "-"
    v = ",".join(res.get("vetoes", []) or []) or "-"
    return f"{take}: {res['decision']}  加权={w}  低于下限={low}  否决={v}  {res.get('reason', '')}"


def main(argv=None):
    ap = argparse.ArgumentParser(description="三专家评审面板")
    ap.add_argument("--project", required=True)
    ap.add_argument("--take", required=True, nargs="+")
    ap.add_argument("--refs", required=True, help="产品参考图，逗号分隔")
    a = ap.parse_args(argv)
    refs = [p for p in a.refs.split(",") if p.strip()]
    for t in a.take:
        res = review_take(a.project, t, refs)
        print(_summary(t, res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
