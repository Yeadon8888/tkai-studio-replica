#!/usr/bin/env python3
"""只绑定本机的人工确认台：JSON 接口、单页界面和只读产品媒体。"""
import json
import mimetypes
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse
import urllib.error
import urllib.request

import calibrate
import checkpoints
import engine
import ledger
import models
import products
import review_panel
import settings
from common import read_json

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
HTML = Path(__file__).resolve().parent / "console" / "index.html"
PAID_BLOCKERS = {"benchmark", "script", "budget"}
MEDIA_SUFFIXES = {".mp4", ".mov", ".webm", ".m4v", ".mp3", ".wav", ".m4a",
                  ".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _product_ids():
    root = settings.studio_home() / "products"
    return sorted(path.name for path in root.iterdir()
                  if path.is_dir() and (path / "product_state.json").is_file()) if root.exists() else []


def status_rows():
    rows = []
    for product in _product_ids():
        row = engine.status(product)
        action = row.get("action") or {}
        if action.get("type") == "checkpoint" and not row.get("blocking_checkpoint"):
            row["blocking_checkpoint"] = checkpoints.create(
                product, row.get("run"), action["kind"], action["summary"],
                action["subject"], action.get("options"))
        rows.append(row)
    return rows


def pending_rows():
    # 首页并发请求时也保证先物化当前阻塞节点；create 本身幂等。
    status_rows()
    rows = checkpoints.pending()
    return sorted(rows, key=lambda row: (row.get("kind") not in PAID_BLOCKERS,
                                          row.get("created", ""), row.get("id", "")))


def _media_url(path):
    path = Path(path).resolve()
    root = (settings.studio_home() / "products").resolve()
    try:
        rel = path.relative_to(root)
    except ValueError:
        return ""
    from urllib.parse import quote
    return "/media/" + quote(rel.as_posix(), safe="/")


def _research_media_url(path):
    path = Path(path).resolve()
    root = _research_root().resolve()
    try:
        rel = path.relative_to(root)
    except ValueError:
        return ""
    from urllib.parse import quote
    return "/research-media/" + quote(rel.as_posix(), safe="/")


def _paths(subject, key):
    values = (subject or {}).get(key) or []
    if isinstance(values, str):
        values = [values]
    return [Path(value) if Path(value).is_absolute() else settings.studio_home() / value for value in values]


def _script_detail(row):
    subject = row.get("subject") or {}
    paths = _paths(subject, "briefs")
    if subject.get("script"):
        paths = _paths(subject, "script")
    segments = []
    for path in paths:
        data = read_json(path, {}) or {}
        shots = data.get("shots") or data.get("segments") or [data]
        for index, shot in enumerate(shots, 1):
            voiceover = shot.get("voiceover", shot.get("voiceover_line", ""))
            segments.append({
                "segment": len(segments) + 1,
                "source": str(path),
                "visual_prompt": shot.get("visual_prompt", shot.get("prompt", "")),
                "voiceover": voiceover,
                "caption": shot.get("caption", ""),
                "onscreen_copy": shot.get("onscreen_copy", ""),
                "duration": shot.get("duration", ""),
                "voiceover_words": len(str(voiceover).split()),
                "voiceover_word_limit": shot.get("voiceover_word_limit", shot.get("max_voiceover_words", "")),
            })
    return {"segments": segments}


def _brief_units(row):
    units = []
    for path in _paths(row.get("subject") or {}, "briefs"):
        brief = read_json(path, {}) or {}
        if brief.get("segments"):
            continue
        need = {"duration": brief.get("duration"), "resolution": brief.get("resolution"),
                "aspect_ratio": brief.get("aspect_ratio"), "images": brief.get("images") or [],
                "audio_urls": brief.get("audio_urls") or [], "first_last": False,
                "ref_video": False, "video_route": brief.get("video_route")}
        try:
            selected = models.select("video", need)
        except (KeyError, ValueError) as exc:
            selected = {"model": brief.get("model") or "未能选择", "reason": str(exc),
                        "estimate_cny": brief.get("estimated_cny", brief.get("estimate_cny"))}
        units.append({"id": brief.get("id") or path.stem, "duration": brief.get("duration"),
                      "unit_price_cny": (selected.get("estimate_cny") / float(brief.get("duration")))
                      if selected.get("estimate_cny") is not None and brief.get("duration") else None,
                      **selected})
    return units


def _find_candidate(value, candidate_id):
    if isinstance(value, dict):
        if str(value.get("id") or value.get("candidate_id") or "") == str(candidate_id):
            return value
        for child in value.values():
            found = _find_candidate(child, candidate_id)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_candidate(child, candidate_id)
            if found:
                return found
    return None


def _remote_video(url):
    if not str(url or "").startswith(("http://", "https://")):
        return False
    try:
        request = urllib.request.Request(url, headers={"Range": "bytes=0-0", "User-Agent": "StudioConsole/1"})
        with urllib.request.urlopen(request, timeout=4) as response:
            return response.status in (200, 206) and (
                response.headers.get_content_type().startswith("video/") or response.status == 206)
    except (OSError, urllib.error.URLError, ValueError):
        return False


def _batch_dir(shots):
    value = shots.get("research_batch")
    if value:
        path = Path(value)
        if not path.is_absolute():
            choices = [settings.studio_home() / value, _research_root() / value]
            path = next((candidate for candidate in choices if candidate.is_dir()), choices[0])
        return path if path.is_dir() else None
    candidate_id, aweme_id = str(shots.get("candidate_id") or ""), str(shots.get("aweme_id") or "")
    for path in _research_root().glob("*/candidates.json") if _research_root().exists() else ():
        candidate = _find_candidate(read_json(path, []), candidate_id)
        if candidate and (not aweme_id or str(candidate.get("aweme_id") or "") == aweme_id):
            return path.parent
    return None


def _research_root():
    configured = Path(settings.get("RESEARCH_ROOT"))
    configured = configured if configured.is_absolute() else settings.workspace_root() / configured
    local = settings.studio_home() / "research"
    return configured if configured.exists() or not local.exists() else local


def _reference_detail(run_dir, brief, shots):
    candidate_id, aweme_id = shots.get("candidate_id"), shots.get("aweme_id")
    batch = _batch_dir(shots)
    local_values = []
    for source in (brief, shots):
        local_values.extend(source.get(key) for key in
                            ("replication_reference_file", "reference_file", "source_video_file", "local_file")
                            if source.get(key))
    if batch and (batch / "candidates.json").is_file():
        candidate = _find_candidate(read_json(batch / "candidates.json", []), candidate_id)
        if candidate and candidate.get("local_file"):
            value = Path(candidate["local_file"])
            local_values.append(value if value.is_absolute() else batch / value)
    names = [str(value) for value in (aweme_id, candidate_id) if value]
    local_paths = [Path(value) if Path(value).is_absolute() else run_dir / value for value in local_values]
    local_paths += [run_dir / "reference.mp4"]
    for root in (run_dir / "benchmarks", run_dir / "media", batch / "videos" if batch else None,
                 batch / "media" if batch else None):
        if root:
            local_paths.extend(root / f"{name}{suffix}" for name in names
                               for suffix in (".mp4", ".mov", ".webm", ".m4v"))
    local = next((path for path in local_paths if path.is_file()), None)
    local_url = (_media_url(local) or _research_media_url(local)) if local else ""

    remote_values = [brief.get("replication_reference"), shots.get("source_video_url")]
    remote = next((value for value in remote_values if value and _remote_video(value)), "")
    original = shots.get("reference_url") or brief.get("reference_url") or ""

    cover_paths = []
    for root in (run_dir / "covers", batch / "covers" if batch else None):
        if root:
            cover_paths.extend(root / f"{name}{suffix}" for name in names
                               for suffix in (".jpg", ".jpeg", ".png", ".webp"))
    cover = next((path for path in cover_paths if path.is_file()), None)
    return {
        "reference_video": local_url or remote,
        "reference_source": "local" if local_url else ("remote" if remote else "cover" if cover else "missing"),
        "reference_cover": (_media_url(cover) or _research_media_url(cover)) if cover else "",
        "reference_original": original,
        "reference_expired": not bool(local_url or remote),
        "reference_refresh_hint": "播放地址已过期；如需刷新封面，请运行 python3 kit/covers.py --refresh",
    }


DIMENSION_LABELS = {
    "product_consistency": "产品一致性", "soul_fidelity": "灵魂还原",
    "person_consistency": "人物一致性", "visual_quality": "画面质量",
    "audio_text": "口播与字幕", "pacing_structure": "节奏结构",
    "platform_signals": "平台信号", "replication_fidelity": "对标还原度",
}


def _uncited_low_text(rows):
    experts = review_panel.load_experts().get("experts") or []
    by_id = {str(row.get("id")): row.get("name") for row in experts}
    by_number = {index: row.get("name") for index, row in enumerate(experts, 1)}
    grouped = {}
    for item in rows or []:
        name = item.get("judge_name") or by_id.get(str(item.get("judge_id") or ""))
        if not name:
            try:
                name = by_number.get(int(item.get("judge")))
            except (TypeError, ValueError):
                name = None
        name = name or f"专家{item.get('judge', '?')}"
        label = DIMENSION_LABELS.get(item.get("dim"), item.get("dim") or "未注明维度")
        grouped.setdefault(name, []).append(f"{label} {item.get('score', '未知')} 分")
    return [f"{name}：{'、'.join(values)}——未写原因" for name, values in grouped.items()]


def _benchmark_detail(row):
    result = []
    for source in (row.get("subject") or {}).get("selected") or []:
        item = dict(source) if isinstance(source, dict) else {"id": str(source)}
        batch_value = item.get("batch")
        if batch_value:
            batch = Path(batch_value)
            candidates = [batch] if batch.is_absolute() else [
                settings.studio_home() / batch,
                settings.studio_home() / "research" / batch,
                settings.studio_home() / "products" / row["product"] / "research" / batch,
            ]
            batch = next((path for path in candidates if path.is_dir()), None)
            if batch:
                for path in sorted(batch.glob("*.json")):
                    found = _find_candidate(read_json(path, {}), item.get("id"))
                    if found:
                        item = {**found, **item}
                        break
                for suffix in (".jpg", ".jpeg", ".png", ".webp"):
                    cover = batch / "covers" / f"{item.get('id')}{suffix}"
                    if cover.is_file():
                        item["cover"] = _media_url(cover)
                        break
        metrics = item.get("metrics") or {}
        item["plays"] = item.get("plays", metrics.get("plays", metrics.get("play_count")))
        item["url"] = item.get("reference_url") or item.get("url") or item.get("share_url") or ""
        item["cover"] = item.get("cover") or item.get("cover_url") or ""
        result.append(item)
    return {"benchmarks": result}


def _verdict_detail(row):
    subject = row.get("subject") or {}
    run_dir = settings.studio_home() / "products" / row["product"] / "runs" / row["run"]
    take = subject.get("take")
    history = engine.review_history(run_dir, take)
    latest = history[-1] if history else {"decision": {}, "record": {}, "reviewed_at": ""}
    decision, full = latest["decision"], latest["record"]
    issues = []
    for expert in full.get("experts") or []:
        judge = expert.get("judge") or {}
        for issue in expert.get("issues") or []:
            issues.append({"expert": judge.get("name") or judge.get("id") or "专家",
                           "at": issue.get("at") or issue.get("time") or "",
                           "detail": issue.get("detail") or issue.get("reason") or str(issue)})
        notes = str(expert.get("notes") or "")
        if notes and not expert.get("issues"):
            for at in list(dict.fromkeys(re.findall(r"\d{2}:\d{2}(?:[–-]\d{2}:\d{2})?", notes)))[-1:]:
                issues.append({"expert": judge.get("name") or judge.get("id") or "专家",
                               "at": at, "detail": notes[:500]})
    brief = read_json(run_dir / "briefs" / f"{take}.json", {}) or {}
    shots = read_json(run_dir / "shots.json", {}) or {}
    usage = ledger.usage_by_project(row["run"])
    review_rows = [{"review_id": item["review_id"], "reviewed_at": item["reviewed_at"],
                    "decision": item["decision"].get("decision")} for item in reversed(history)]
    return {"take": take, "video": _media_url(run_dir / "takes" / f"{take}.mp4"),
            **_reference_detail(run_dir, brief, shots),
            "decision": decision, "review": full, "issues": issues,
            "uncited_low": decision.get("uncited_low") or [],
            "uncited_low_text": _uncited_low_text(decision.get("uncited_low") or []),
            "reviewed_at": latest.get("reviewed_at") or "", "review_history": review_rows,
            "duration": brief.get("duration") or (full.get("machine") or {}).get("facts", {}).get("duration"),
            "cost_cny": usage["video_cny"]}


def checkpoint_detail(checkpoint_id):
    row = checkpoints.get(checkpoint_id)
    detail = {}
    if row["kind"] == "script":
        detail = _script_detail(row)
    elif row["kind"] == "budget":
        detail = {"units": _brief_units(row), "budget": products.spent_cny(row["product"])}
    elif row["kind"] == "verdict":
        detail = _verdict_detail(row)
    elif row["kind"] == "benchmark":
        detail = _benchmark_detail(row)
    return {**row, "detail": detail}


def statistics():
    projects = []
    for product in _product_ids():
        root = settings.studio_home() / "products" / product / "runs"
        projects.extend(path for path in root.iterdir() if path.is_dir()) if root.exists() else None
    calibration = calibrate.analyze(projects) if projects else {"total": 0, "agree": 0, "rate": None}
    usable = 0
    spent = 0.0
    for product in _product_ids():
        spent += products.spent_cny(product)["spent_cny"]
        root = settings.studio_home() / "products" / product / "checkpoints"
        for path in root.glob("cp-verdict-*.json") if root.exists() else ():
            usable += int((read_json(path, {}) or {}).get("status") == "approved")
    return {"calibration": calibration, "usable": usable, "spent_cny": round(spent, 4),
            "usable_per_cny": round(usable / spent, 4) if spent else None}


class Handler(BaseHTTPRequestHandler):
    server_version = "StudioConsole/1"

    def log_message(self, fmt, *args):
        return

    def _json(self, value, status=HTTPStatus.OK):
        body = json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, exc, status=HTTPStatus.BAD_REQUEST):
        text = str(exc)
        errors = [part for part in re.split(r"[；\n]", text.replace("脚本编辑未通过校验：", "")) if part]
        self._json({"error": text, "errors": errors}, status)

    def _body(self):
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length).decode("utf-8") or "{}")

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/":
                body = HTML.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers(); self.wfile.write(body); return
            if path == "/api/status":
                self._json(status_rows()); return
            if path == "/api/pending":
                self._json(pending_rows()); return
            if path == "/api/statistics":
                self._json(statistics()); return
            if path.startswith("/api/checkpoint/"):
                self._json(checkpoint_detail(unquote(path.removeprefix("/api/checkpoint/")))); return
            if path.startswith("/media/"):
                self._serve_media(unquote(path.removeprefix("/media/")), settings.studio_home() / "products"); return
            if path.startswith("/research-media/"):
                self._serve_media(unquote(path.removeprefix("/research-media/")), _research_root()); return
            self._json({"error": "接口不存在"}, HTTPStatus.NOT_FOUND)
        except KeyError as exc:
            self._error(exc, HTTPStatus.NOT_FOUND)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self._error(exc)

    def do_POST(self):
        path = urlparse(self.path).path
        match = re.fullmatch(r"/api/checkpoint/([^/]+)/(resolve|edit-script)", path)
        if not match:
            self._json({"error": "接口不存在"}, HTTPStatus.NOT_FOUND); return
        try:
            checkpoint_id, action = unquote(match.group(1)), match.group(2)
            body = self._body()
            if action == "edit-script":
                edits = body.get("edits") or []
                result = checkpoints.resolve(checkpoint_id, "changes_requested",
                                             body.get("note") or "确认台编辑脚本",
                                             body.get("by") or "确认台", edits)
            else:
                note = str(body.get("note") or "")
                if body.get("status") in ("rejected", "changes_requested") and not note.strip():
                    raise ValueError("驳回或要求修改时必须填写 note")
                fields = {key: value for key, value in (body.get("fields") or {}).items()
                          if str(value or "").strip()}
                if fields:
                    note = (note + "；" if note else "") + "界面填写：" + json.dumps(fields, ensure_ascii=False)
                result = checkpoints.resolve(checkpoint_id, body.get("status"), note,
                                             body.get("by") or "确认台")
            self._json(result)
        except KeyError as exc:
            self._error(exc, HTTPStatus.NOT_FOUND)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self._error(exc)

    def _serve_media(self, relative, root):
        root = Path(root).resolve()
        target = (root / relative).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            self._json({"error": "禁止访问 products 目录之外的文件"}, HTTPStatus.FORBIDDEN); return
        if not target.is_file():
            self._json({"error": "媒体不存在"}, HTTPStatus.NOT_FOUND); return
        if target.suffix.lower() not in MEDIA_SUFFIXES:
            self._json({"error": "只允许读取图片、音频或视频文件"}, HTTPStatus.FORBIDDEN); return
        size = target.stat().st_size
        start, end, status = 0, size - 1, HTTPStatus.OK
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if match:
            start = int(match.group(1) or 0); end = min(int(match.group(2) or end), end)
            if start > end:
                self._json({"error": "Range 无效"}, HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE); return
            status = HTTPStatus.PARTIAL_CONTENT
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with target.open("rb") as handle:
            handle.seek(start)
            try:
                self.wfile.write(handle.read(length))
            except (BrokenPipeError, ConnectionResetError):
                # 浏览器切换页面或只探测视频 Range 时会主动断开，不是服务错误。
                return


def make_server(port=DEFAULT_PORT, server_class=ThreadingHTTPServer):
    return server_class((HOST, int(port)), Handler)


def serve(port=DEFAULT_PORT):
    server = make_server(port)
    print(f"确认台：http://{HOST}:{server.server_address[1]}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
