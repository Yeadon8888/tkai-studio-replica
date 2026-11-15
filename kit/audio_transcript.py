#!/usr/bin/env python3
"""只上传对标音轨，输出带原始时间码的台词分段。"""
import base64
import subprocess
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent
sys.path.insert(0, str(KIT))

from common import parse_json, write_json  # noqa: E402
import loop  # noqa: E402


PROMPT = """你是专业语音转写员。只根据音频原样转写，不润色、不改写；保留原语言（不要翻译进 text）。
每段另给英文直译 text_en（原文已是英文则与 text 相同）。保留说话人、语气和原始时间码；听不清写 [inaudible]。
只输出 JSON：{"language":"<原语言代码>","segments":[{"start":0.0,"end":2.4,"speaker":"...","text":"...","text_en":"..."}]}"""


def extract_audio(video, output):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vn", "-ac", "1",
                    "-ar", "16000", "-codec:a", "libmp3lame", str(output)], check=True)
    return output


def validate(data):
    if not isinstance(data, dict) or not isinstance(data.get("segments"), list):
        raise ValueError("转写输出必须含 segments 列表")
    rows = []
    for index, row in enumerate(data["segments"], 1):
        if not isinstance(row, dict) or not str(row.get("text") or "").strip():
            raise ValueError(f"第 {index} 个转写分段缺少 text")
        try:
            start, end = float(row["start"]), float(row["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"第 {index} 个转写分段时间非法") from exc
        if start < 0 or end <= start:
            raise ValueError(f"第 {index} 个转写分段时间非法")
        rows.append({**row, "start": round(start, 3), "end": round(end, 3)})
    return {**data, "segments": rows, "timebase": "reference_original_seconds"}


def _duration(path):
    import subprocess
    try:
        return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                              "-of", "default=nw=1:nk=1", str(path)], text=True).strip())
    except (subprocess.CalledProcessError, ValueError, FileNotFoundError):
        return None


def transcribe(video, project, ref="benchmark", model_call=loop.gemini):
    project = Path(project)
    audio = extract_audio(video, project / "transcript" / "reference-16k-mono.mp3")
    encoded = base64.b64encode(audio.read_bytes()).decode("ascii")
    raw = model_call([{"type": "text", "text": PROMPT},
                      {"type": "input_audio", "input_audio": {"data": encoded, "format": "mp3"}}],
                     project=project.name, ref=f"{ref}:transcript", max_tokens=24000,
                     temperature=0, role="transcribe", audio_seconds=_duration(audio))
    parsed = parse_json(raw, require=("segments",))
    result = validate(parsed)
    write_json(project / "transcript" / "transcript.json", result)
    return result
