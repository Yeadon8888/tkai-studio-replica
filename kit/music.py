#!/usr/bin/env python3
"""Suno 原创纯音乐曲库：先复用，未命中时才提交一次付费任务。"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

KIT = Path(__file__).resolve().parent
sys.path.insert(0, str(KIT))

from common import _now, http_json, http_open, read_json, write_json  # noqa: E402
import ledger  # noqa: E402
import settings  # noqa: E402

STYLE_FIELDS = ("mood", "tempo_bpm", "era_style", "instruments", "energy_curve")


def music_root():
    return settings.studio_home() / "assets" / "music"


def library_path():
    return music_root() / "library.jsonl"


def _style(style):
    if not isinstance(style, dict):
        raise ValueError("配乐风格必须是对象")
    missing = [key for key in STYLE_FIELDS if style.get(key) in (None, "", [])]
    if missing:
        raise ValueError("配乐风格缺字段：" + ", ".join(missing))
    if isinstance(style["tempo_bpm"], bool) or not isinstance(style["tempo_bpm"], (int, float)) \
            or float(style["tempo_bpm"]) <= 0:
        raise ValueError("配乐 tempo_bpm 必须是正数")
    if not isinstance(style["instruments"], list) or not all(
            isinstance(value, str) and value.strip() for value in style["instruments"]):
        raise ValueError("配乐 instruments 必须是非空字符串列表")
    if any(not isinstance(style[key], str) for key in ("mood", "era_style", "energy_curve")):
        raise ValueError("配乐 mood、era_style、energy_curve 必须是字符串")
    return {key: style[key] for key in STYLE_FIELDS}


def _rows(path=None):
    path = Path(path or library_path())
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _track_file(row, path=None):
    value = Path(str(row.get("file") or ""))
    return value if value.is_absolute() else Path(path or library_path()).parent / value


def pick(style, min_duration, path=None):
    """心情和时代风格一致、时长足够且文件存在时复用曲库。"""
    wanted = _style(style)
    for row in _rows(path):
        existing = row.get("style") or {}
        file = _track_file(row, path)
        if (str(existing.get("mood", "")).casefold() == str(wanted["mood"]).casefold()
                and str(existing.get("era_style", "")).casefold() == str(wanted["era_style"]).casefold()
                and float(row.get("duration") or 0) >= float(min_duration)
                and file.is_file()):
            return dict(row, file=str(file.resolve()))
    return None


def description_prompt(style):
    style = _style(style)
    instruments = style["instruments"]
    if isinstance(instruments, list):
        instruments = ", ".join(str(x) for x in instruments)
    return (f"Mood: {style['mood']}; tempo: {style['tempo_bpm']} BPM; era/style: {style['era_style']}; "
            f"instruments: {instruments}; energy curve: {style['energy_curve']}. "
            "Original instrumental, no imitation of existing songs or artists.")


class Client:
    def __init__(self, post=http_json, opener=urllib.request.urlopen):
        settings.load()
        key = os.environ.get("TOKENSFACTORY_API_KEY", "")
        if not key:
            raise RuntimeError("未找到 TOKENSFACTORY_API_KEY")
        self.base = settings.get("TOKENSFACTORY_BASE_URL").rstrip("/")
        self.headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                        "User-Agent": settings.get("HTTP_USER_AGENT")}
        self._post = post
        self._open = opener

    def submit(self, style):
        body = {"model": settings.model("music"), "gpt_description_prompt": description_prompt(style),
                "make_instrumental": True, "mv": settings.get("SUNO_MV")}
        return self._post("POST", self.base + "/suno/submit/music", self.headers, body, timeout=60)

    def fetch(self, task_id):
        return self._post("POST", self.base + "/suno/fetch", self.headers,
                          {"ids": [task_id]}, timeout=60)

    def download(self, url, output):
        with http_open(url, timeout=300, opener=self._open) as response, Path(output).open("wb") as target:
            while chunk := response.read(1024 * 1024):
                target.write(chunk)


def _duration(path, probe=None):
    probe = probe or (lambda command: subprocess.check_output(command, text=True))
    raw = probe(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(path)])
    value = float(str(raw).strip())
    if value <= 0:
        raise ValueError("下载配乐的时长无效")
    return round(value, 3)


def _task_row(response, task_id):
    rows = response.get("data") if isinstance(response, dict) else None
    rows = rows if isinstance(rows, list) else []
    return next((row for row in rows if str(row.get("id") or row.get("task_id")) == task_id),
                rows[0] if rows else {})


def _append_library(row, path=None):
    path = Path(path or library_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def generate(style, project, music_id=None, client=None, sleep=time.sleep, interval=None,
             max_polls=None, probe=None, path=None):
    """从持久回执继续轮询；只有没有 task_id 时才提交一次。"""
    style = _style(style)
    interval = settings.get_int("MUSIC_POLL_INTERVAL_SECONDS") if interval is None else float(interval)
    max_polls = settings.get_int("MUSIC_MAX_POLLS") if max_polls is None else int(max_polls)
    minimum_interval = settings.get_int("MUSIC_MIN_POLL_INTERVAL_SECONDS")
    project = Path(project)
    digest = hashlib.sha256(json.dumps(style, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]
    music_id = music_id or digest
    job_path = project / "jobs" / f"music-{music_id}.json"
    job = read_json(job_path, {}) or {}
    client = client or Client()
    task_id = job.get("task_id")
    if not task_id and job.get("status") == "submit_unknown":
        raise RuntimeError("Suno 上次提交结果不明；请先查上游任务记录，不会重提")
    submitted_now = False
    if not task_id:
        try:
            response = client.submit(style)
        except Exception:
            write_json(job_path, {"id": music_id, "status": "submit_unknown", "style": style,
                                  "submitted_at": _now()})
            ledger.append("tokensfactory", settings.model("music"), "unknown", project=project.name,
                          ref=music_id, units=1, kind="music")
            raise
        if not isinstance(response, dict) or response.get("code") != "success" or not response.get("data"):
            write_json(job_path, {"id": music_id, "status": "submit_unknown", "style": style,
                                  "submitted_at": _now()})
            ledger.append("tokensfactory", settings.model("music"), "unknown", project=project.name,
                          ref=music_id, units=1, kind="music")
            raise RuntimeError("Suno 提交结果不明，未重提；请查上游任务记录")
        task_id = str(response["data"])
        job = {"id": music_id, "task_id": task_id, "status": "submitted", "style": style,
               "submitted_at": _now()}
        write_json(job_path, job)
        ledger.append("tokensfactory", settings.model("music"), "submitted", project=project.name,
                      ref=task_id, units=1, kind="music")
        submitted_now = True
    if submitted_now:
        sleep(max(minimum_interval, interval))
    for poll in range(max_polls + 1):
        row = _task_row(client.fetch(task_id), task_id)
        status = str(row.get("status") or "UNKNOWN").upper()
        job.update(status=status, last_checked_at=_now())
        write_json(job_path, job)
        if status == "FAILURE":
            job["fail_reason"] = row.get("fail_reason") or "上游未返回原因"
            write_json(job_path, job)
            ledger.append("tokensfactory", settings.model("music"), "failed", project=project.name,
                          ref=task_id, units=1, kind="music")
            raise RuntimeError(f"Suno 配乐失败：{job['fail_reason']}")
        if status == "SUCCESS":
            songs = row.get("data") or []
            first = songs[0] if isinstance(songs, list) and songs else {}
            url = first.get("audio_url") or first.get("cld2AudioUrl")
            if not url:
                raise RuntimeError("Suno 成功任务没有可下载音频")
            suffix = Path(str(url).split("?", 1)[0]).suffix.lower()
            suffix = suffix if suffix in (".mp3", ".m4a", ".wav") else ".mp3"
            track_id = hashlib.sha256(task_id.encode()).hexdigest()[:16]
            output = music_root() / f"{track_id}{suffix}"
            output.parent.mkdir(parents=True, exist_ok=True)
            client.download(url, output)
            duration = _duration(output, probe)
            record = {"id": track_id, "file": output.name, "style": style, "duration": duration,
                      "source": "suno", "task_id": task_id, "created": _now(),
                      "license_note": "TokensFactory Suno 原创纯音乐；不模仿已有歌曲或艺人"}
            if not any(x.get("task_id") == task_id for x in _rows(path)):
                _append_library(record, path)
            job.update(status="saved", library_id=track_id, file=str(output.resolve()),
                       duration=duration, saved_at=_now())
            write_json(job_path, job)
            ledger.append("tokensfactory", settings.model("music"), "saved", project=project.name,
                          ref=task_id, units=1, kind="music")
            return dict(record, file=str(output.resolve()))
        if poll < max_polls:
            sleep(max(minimum_interval, interval))
    raise TimeoutError(f"Suno 任务 {task_id} 仍为 {job.get('status')}；已保留回执，不会重提")


def prepare(project, brief, client=None, **kwargs):
    project, brief = Path(project), read_json(brief, {}) or {}
    style = _style(brief.get("music"))
    duration = float(brief.get("duration") or sum(float(x.get("duration") or 0) for x in brief.get("segments") or []))
    found = pick(style, duration)
    music_id = str(brief.get("id") or "music")
    job_path = project / "jobs" / f"music-{music_id}.json"
    if found:
        write_json(job_path, {"id": music_id, "status": "reused", "library_id": found["id"],
                              "file": found["file"], "duration": found["duration"], "style": style,
                              "selected_at": _now()})
        return found
    result = generate(style, project, music_id=music_id, client=client, **kwargs)
    if float(result["duration"]) + 1e-9 < duration:
        job_path = project / "jobs" / f"music-{music_id}.json"
        job = read_json(job_path, {}) or {}
        job.update(status="insufficient", required_duration=duration,
                   reason=f"Suno 配乐只有 {result['duration']:g} 秒，不足成片 {duration:g} 秒")
        write_json(job_path, job)
        raise ValueError(f"Suno 配乐只有 {result['duration']:g} 秒，不足成片 {duration:g} 秒，已停止")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="为运行复用或生成一首原创纯音乐")
    parser.add_argument("--project", required=True)
    parser.add_argument("--brief", required=True)
    args = parser.parse_args(argv)
    result = prepare(args.project, args.brief)
    print(json.dumps({"id": result["id"], "file": result["file"], "duration": result["duration"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
