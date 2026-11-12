"""调研候选 + 拆解结果 → 裂变可用的对标文件。

链接候选会从 play_urls 中选可访问地址；本地候选临时上传，模型调用结束后立即删除。
只有通过 fission.check_inputs 的结果才写入 <运行目录>/benchmarks/ 。
"""
import argparse
import json
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent
sys.path.insert(0, str(KIT))

import fission  # noqa: E402
import settings  # noqa: E402
import tt_research  # noqa: E402


PROMPT = """你是短视频对标拆解师。根据视频、已有拆解和目标档位，只输出一个 JSON 对象。
所有档位都必须输出：
- fixed：可包含 style/action/scene/camera/rhythm/hook/proof/cta，值是具体字符串。
- must_keep：按视频时间顺序排列的必须保留项列表。
- prompt_fixed：可直接给视频模型的英文提示词，按时间顺序说清画面、动作、场景、机位和节奏。
- fixed_keywords：必须原样保留的英文关键词列表，没有则输出 []。
- has_person：视频中是否有人物，布尔值。
H 档必须把 style/action/scene/camera/rhythm 全部写入 fixed。
M 档的 must_keep 必须有 3–6 项。
L 档必须把 hook/proof/cta 的逻辑写入 fixed。
不要输出 Markdown，不要改写或编造产品锚点。"""


def _read(path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def _candidate_and_breakdown(batch, candidate_id):
    candidates = _read(Path(batch) / "candidates.json", []) or []
    candidate = next((c for c in candidates if c.get("candidate_id") == candidate_id), None)
    if not candidate:
        return None, None, [f"候选 {candidate_id} 不在 candidates.json 中"]
    rows = _read(Path(batch) / "breakdown.json", []) or []
    breakdown = next((r for r in rows if r.get("aweme_id") == candidate.get("aweme_id")
                      or r.get("candidate_id") == candidate_id), None)
    if not breakdown:
        return candidate, None, [f"候选 {candidate_id} 缺少 breakdown.json 拆解"]
    return candidate, breakdown, []


def _product_anchor(product_id):
    path = settings.studio_home() / "products" / product_id / "product_anchor.txt"
    if not path.is_file():
        return "", f"缺少产品锚点：{path}"
    anchor = path.read_text(encoding="utf-8").strip()
    return (anchor, "") if anchor else ("", f"产品锚点为空：{path}")


def _model_parts(candidate, breakdown, tier, video_url):
    context = {
        "candidate_id": candidate.get("candidate_id"),
        "description": candidate.get("desc", ""),
        "duration_s": candidate.get("duration_s") or breakdown.get("duration_s"),
        "tier": tier,
        "breakdown": breakdown,
    }
    return [
        {"type": "text", "text": PROMPT + "\n\n输入数据：\n" + json.dumps(context, ensure_ascii=False)},
        {"type": "video_url", "video_url": video_url},
    ]


def _response_errors(obj, tier):
    errors = []
    if not isinstance(obj, dict):
        return ["模型输出不是 JSON 对象"]
    if not isinstance(obj.get("fixed"), dict):
        errors.append("缺少对象 fixed")
    if not isinstance(obj.get("must_keep"), list) or not all(isinstance(x, str) and x.strip() for x in obj.get("must_keep", [])):
        errors.append("缺少非空字符串列表 must_keep")
    if not isinstance(obj.get("prompt_fixed"), str) or not obj.get("prompt_fixed", "").strip():
        errors.append("缺少英文提示词 prompt_fixed")
    if not isinstance(obj.get("fixed_keywords"), list):
        errors.append("缺少列表 fixed_keywords")
    elif not all(isinstance(x, str) and x.strip() for x in obj["fixed_keywords"]):
        errors.append("fixed_keywords 只能包含非空字符串")
    if tier == "M" and not 3 <= len(obj.get("must_keep") or []) <= 6:
        errors.append("M 档 must_keep 必须有 3–6 项")
    if tier == "H" and not isinstance(obj.get("has_person"), bool):
        errors.append("H 档缺少布尔值 has_person")
    return errors


def refresh_play_urls(candidate, batch):
    """播放链接过期时，用分享链接向 TikHub 重新取一次（1 次请求），并写回 candidates.json。"""
    import intake
    url = candidate.get("url")
    if not url:
        return []
    client = tt_research.Client(tt_research.load_key(), batch, budget_calls=1)
    fresh = intake.fetch_url_candidate(url, client)
    urls = fresh.get("play_urls") or []
    path = Path(batch) / "candidates.json"
    cands = json.loads(path.read_text(encoding="utf-8"))
    for c in cands:
        if c.get("aweme_id") == candidate.get("aweme_id"):
            c["play_urls"] = urls
    path.write_text(json.dumps(cands, ensure_ascii=False, indent=2), encoding="utf-8")
    return urls


def build(batch, candidate_id, product_id, tier, run_dir, model_call=None, reachable=None,
          upload=None, delete=None, media_log=None, overwrite=False, refresh=None):
    """构建一份对标文件。可注入模型、可访问性检查和媒体上传函数，便于无付费测试。"""
    batch, run_dir = Path(batch), Path(run_dir)
    out_path = run_dir / "benchmarks" / f"{candidate_id}.json"
    if out_path.exists() and not overwrite:
        return {"ok": True, "path": str(out_path), "skipped": True, "message": "对标文件已存在，未重复调用模型"}
    if tier not in ("H", "M", "L"):
        return {"ok": False, "errors": [f"档位 {tier} 不能构建裂变对标"]}
    candidate, breakdown, errors = _candidate_and_breakdown(batch, candidate_id)
    anchor, anchor_error = _product_anchor(product_id)
    if anchor_error:
        errors.append(anchor_error)
    if errors:
        return {"ok": False, "errors": errors}

    model_call = model_call or __import__("loop").gemini
    reachable = reachable or tt_research.media_reachable
    uploaded = None
    status = {"aweme_id": candidate.get("aweme_id"), "candidate_id": candidate_id,
              "source": "local" if candidate.get("local_file") else "url"}
    try:
        if candidate.get("local_file"):
            media_host = __import__("media_host")
            upload = upload or media_host.upload
            delete = delete or media_host.delete
            media_log = media_log or media_host.log
            uploaded = upload(candidate["local_file"])
            video_url = uploaded["url"]
            status["uploaded_url"] = video_url
        else:
            video_url = next((url for url in candidate.get("play_urls") or [] if reachable(url)), "")
            if not video_url:
                # 播放链接会过期：用分享链接刷新一次，仍不可访问才放弃
                refresh = refresh or refresh_play_urls
                fresh = refresh(candidate, batch) or []
                video_url = next((url for url in fresh if reachable(url)), "")
                status["play_urls_refreshed"] = True
            if not video_url:
                return {"ok": False, "errors": [f"候选 {candidate_id} 的 play_urls 都不可访问（已刷新一次）"]}
        text = model_call(_model_parts(candidate, breakdown, tier, video_url), project=run_dir.name,
                          ref=candidate_id, max_tokens=4000, role="breakdown")
        obj = tt_research.parse_json_obj(text)
    finally:
        if uploaded is not None:
            try:
                status["deleted"] = bool(delete(uploaded))
            except Exception as exc:  # 删除失败必须留痕，不掩盖原始错误
                status.update(deleted=False, delete_error=str(exc)[:200])
            if media_log:
                media_log(uploaded, status)

    errors = _response_errors(obj, tier)
    if uploaded is not None and status.get("deleted") is not True:
        errors.append("本地视频临时文件未确认删除，不写入对标文件")
    if errors:
        return {"ok": False, "errors": errors, "media_deleted": status.get("deleted")}
    bench = dict(obj)
    bench.update({
        "benchmark_id": candidate_id,
        "tier": tier,
        "classified_tier": tier,
        "product_anchor": anchor,
        "duration": settings.get_float("VIDEO_DURATION"),
        "resolution": settings.get("VIDEO_RESOLUTION"),
        "aspect_ratio": settings.get("VIDEO_ASPECT"),
        "source": {"batch": batch.name, "candidate_id": candidate_id,
                   "aweme_id": candidate.get("aweme_id"), "url": candidate.get("url", "")},
    })
    validation = fission.check_inputs(bench, {"axes": {"product": [anchor]}}, tier)
    if validation:
        return {"ok": False, "errors": validation, "media_deleted": status.get("deleted")}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(bench, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "path": str(out_path), "media_deleted": status.get("deleted")}


def main(argv=None):
    ap = argparse.ArgumentParser(description="从调研候选生成裂变对标文件")
    ap.add_argument("--batch", required=True, help="含 candidates.json 和 breakdown.json 的调研批次")
    ap.add_argument("--id", required=True, help="候选 id")
    ap.add_argument("--product", required=True, help="品 id")
    ap.add_argument("--tier", required=True, choices=("H", "M", "L"))
    ap.add_argument("--run", required=True, help="运行目录")
    ap.add_argument("--overwrite", action="store_true", help="覆盖已有对标（会再调用一次模型）")
    a = ap.parse_args(argv)
    result = build(a.batch, a.id, a.product, a.tier, a.run, overwrite=a.overwrite)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
