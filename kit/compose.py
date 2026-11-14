#!/usr/bin/env python3
"""逐镜头素材 → Hypit 时间线、字幕、画面文字、口播与音乐成片。"""
import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import textwrap
import re
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

KIT = Path(__file__).resolve().parent
sys.path.insert(0, str(KIT))

from common import _now, read_json, write_json  # noqa: E402
import ledger  # noqa: E402
import review_panel  # noqa: E402


def timeline(brief):
    """生成严格首尾相接的字幕/画面文字时间线。"""
    cursor, rows = 0.0, []
    for index, segment in enumerate(brief.get("segments") or [], 1):
        duration = float(segment.get("duration") or 0)
        if duration <= 0:
            raise ValueError(f"第 {index} 段 duration 必须大于 0")
        row = {"id": f"S{index:02d}", "start": round(cursor, 3), "end": round(cursor + duration, 3),
               "caption": str(segment.get("caption") or segment.get("voiceover_line") or "").strip(),
               "onscreen_copy": str(segment.get("onscreen_copy") or "").strip()}
        if not row["caption"]:
            raise ValueError(f"第 {index} 段缺少字幕文案")
        rows.append(row)
        cursor += duration
    expected = float(brief.get("duration") or cursor)
    if not rows or abs(cursor - expected) > 0.05:
        raise ValueError(f"分段总时长 {cursor:g} 与简报时长 {expected:g} 不一致")
    return rows


def apply_script(brief, script):
    """合成前以 script.json 为文案唯一来源，避免 Agent 或旧简报手改文案。"""
    rows = {str(row.get("id")): row for row in (script or {}).get("shots") or []}
    if not rows:
        raise ValueError("script.json 没有镜头脚本")
    result = dict(brief)
    segments = []
    for index, segment in enumerate(brief.get("segments") or [], 1):
        ids = segment.get("shot_ids") or [segment.get("shot_id")]
        selected = [rows[sid] for sid in ids if sid in rows]
        if not selected:
            raise ValueError(f"第 {index} 段在 script.json 找不到对应镜头")
        updated = dict(segment)
        updated["voiceover_line"] = " ".join(str(x.get("voiceover") or "").strip() for x in selected).strip()
        updated["caption"] = " ".join(str(x.get("caption") or "").strip() for x in selected).strip()
        updated["onscreen_copy"] = " ".join(str(x.get("onscreen_copy") or "").strip() for x in selected).strip()
        segments.append(updated)
    result["segments"] = segments
    return result


def srt_text(rows):
    def stamp(value):
        millis = int(round(value * 1000))
        hour, millis = divmod(millis, 3_600_000)
        minute, millis = divmod(millis, 60_000)
        second, millis = divmod(millis, 1000)
        return f"{hour:02d}:{minute:02d}:{second:02d},{millis:03d}"
    return "\n\n".join(f"{i}\n{stamp(row['start'])} --> {stamp(row['end'])}\n{row['caption']}"
                         for i, row in enumerate(rows, 1)) + "\n"


def _probe_duration(path):
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                          "-of", "default=nw=1:nk=1", str(path)], text=True).strip())


def mean_volume(path):
    result = subprocess.run(["ffmpeg", "-hide_banner", "-v", "info", "-i", str(path),
                             "-af", "volumedetect", "-f", "null", "-"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, check=False)
    match = re.search(r"mean_volume:\s*(-?(?:inf|[0-9.]+)) dB", result.stderr or "", re.I)
    return float(match.group(1)) if match and match.group(1).lower() != "-inf" else None


def _tts_helper():
    override = os.environ.get("TOKENSFACTORY_MEDIA_CLI")
    path = Path(override).expanduser() if override else Path.home() / ".codex/skills/tokensfactory-media/scripts/tokensfactory_media.py"
    if not path.is_file():
        raise FileNotFoundError("未找到 TokensFactory Media 的 generate-speech helper")
    return path


def _tts_calls(project_name):
    if not ledger.LEDGER_FILE.exists():
        return 0
    return sum(1 for line in ledger.LEDGER_FILE.read_text(encoding="utf-8").splitlines()
               if line.strip() and (row := json.loads(line)).get("project") == project_name and row.get("kind") == "tts")


def synthesize_speech(project, text, output, voice="Kore"):
    """原生 TTS 同步调用一次；失败不重试。模型和接口由已安装 helper 固定。"""
    project, output = Path(project), Path(output)
    if output.exists():
        return output
    if _tts_calls(project.name) >= 5:
        raise SystemExit("TTS 调用已达任务上限 5 次，停止")
    output.parent.mkdir(parents=True, exist_ok=True)
    status = "ok"
    try:
        subprocess.run([sys.executable, str(_tts_helper()), "generate-speech", "--text", text,
                        "--voice", voice, "--output", str(output)], check=True)
    except subprocess.CalledProcessError as exc:
        status = f"exit_{exc.returncode}"
        raise
    finally:
        ledger.append("tokensfactory", "native-tts", status, project=project.name, ref="voiceover",
                      units=1, kind="tts")
    return output


def fit_audio(source, output, duration):
    source_duration = _probe_duration(source)
    if source_duration <= 0:
        raise ValueError("TTS 音频时长无效")
    speed = source_duration / duration
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(source),
                    "-filter:a", f"atempo={speed:.8f},apad=pad_dur={duration:.3f}",
                    "-t", f"{duration:.3f}", "-ar", "48000", "-ac", "2", str(output)], check=True)
    return output


def make_music(output, duration):
    """生成无版权的轻量环境音床，确切文案仍由口播承担。"""
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=196:sample_rate=48000", "-filter:a",
                    "volume=0.035,lowpass=f=900,afade=t=in:st=0:d=0.4,afade=t=out:st="
                    f"{max(0.0, duration - 0.6):.3f}:d=0.6", "-t", f"{duration:.3f}", str(output)], check=True)
    return output


def make_overlay(output, text, kind="caption"):
    """生成透明全画幅文字图；文字图层的时间与叠放由 Hypit 管理。"""
    output = Path(output)
    text_path = output.with_suffix(".txt")
    wrapped = "\n".join(textwrap.wrap(text, width=34, break_long_words=False,
                                       break_on_hyphens=False)) if kind == "caption" else text
    text_path.write_text(wrapped, encoding="utf-8")
    y = "h*0.72" if kind == "caption" else "h*0.10"
    size = "54" if kind == "caption" else "46"
    box = "1" if kind == "caption" else "0"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=black@0.0:s=1080x1920:d=0.04,format=rgba", "-vf",
                    f"drawtext=font='Arial':textfile='{text_path}':fontcolor=white:fontsize={size}:"
                    f"x=(w-text_w)/2:y={y}:line_spacing=12:box={box}:boxcolor=black@0.62:boxborderw=22",
                    "-frames:v", "1", str(output)], check=True)
    return output


def _run(command, cwd=None):
    return subprocess.run(command, cwd=cwd, check=True, text=True, capture_output=True)


def _import(hypit_project, path, asset_id):
    _run(["media-stack", "import", str(Path(path).resolve()), "--id", asset_id,
          "--project", str(hypit_project)])


def _find_build_id(value):
    if isinstance(value, dict):
        for key in ("buildId", "build_id", "id"):
            if isinstance(value.get(key), str) and value[key]:
                return value[key]
        for item in value.values():
            found = _find_build_id(item)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _find_build_id(item)
            if found:
                return found
    return None


def _hypit_source(rows, duration, overlays):
    windows = []
    for index, row in enumerate(rows, 1):
        windows.append(f'    <time:Window id="caption-{index}" from="{row["start"]}s" for="{row["end"] - row["start"]}s"/>')
        if row.get("onscreen_copy"):
            windows.append(f'    <time:Window id="copy-{index}" from="{row["start"]}s" for="{row["end"] - row["start"]}s"/>')
    visual_tracks = ['  <visual:Track id="picture-track" timeline={program.timeline}>',
                     '    <visual:Clip id="picture-clip" media={picture.media} during={program.window} frame={canvas.bounds} z="1" fit="cover"/>',
                     '  </visual:Track>']
    film_tracks = ['    <film:Track source={picture-track.visual}/>']
    for index, row in enumerate(rows, 1):
        for kind in ("caption", "copy"):
            if kind == "copy" and not row.get("onscreen_copy"):
                continue
            ident = f"{kind}-{index}"
            visual_tracks += [f'  <visual:Track id="{ident}-track" timeline={{program.timeline}}>',
                              f'    <visual:Clip id="{ident}-clip" image={{external.{ident}}} extent={{overlay-size}} during={{program.{ident}}} frame={{canvas.bounds}} z="{20 if kind == "caption" else 15}" fit="contain"/>',
                              '  </visual:Track>']
            film_tracks.append(f'    <film:Track source={{{ident}-track.visual}}/>')
    film_tracks += ['    <film:Track source={voice-track.audio}/>',
                    '    <film:Track source={music-track.audio}/>']
    return "\n".join([
        '<?svml using="@hypit/markup@1"?>', '<svml>',
        '  <import as="op" from="@hypit/media-operations@1"/>',
        '  <import as="time" from="@hypit/timeline-author@1"/>',
        '  <import as="space" from="@hypit/spatial@1"/>',
        '  <import as="visual" from="@hypit/visual-track@1"/>',
        '  <import as="audio" from="@hypit/audio-track@1"/>',
        '  <import as="film" from="@hypit/film@1"/>',
        '  <import as="html" from="@hypit/html-video@1"/>',
        '  <import as="external" source="./assets/external/assets.svml"/>',
        '  <import as="style" source="./style.svs"/>',
        '  <time:Clock id="clock" frame-rate="30"/>',
        '  <op:Normalize id="picture" source={external.picture} video="primary-moving" audio="none" span-authority="video" clock={clock}/>',
        '  <op:Normalize id="voice" source={external.voice} video="none" audio="default" span-authority="audio" clock={clock}/>',
        '  <op:Normalize id="music" source={external.music} video="none" audio="default" span-authority="audio" clock={clock}/>',
        f'  <time:Timeline id="program" clock={{clock}} end="{duration}s">', *windows,
        '  </time:Timeline>', '  <space:Canvas id="canvas" width="1080" height="1920"/>',
        '  <space:Extent id="overlay-size" width="1080" height="1920"/>', *visual_tracks,
        '  <audio:Track id="voice-track" timeline={program.timeline}>',
        '    <audio:Clip id="voice-clip" source={voice.media} during={program.window} gain="1.0"/>',
        '  </audio:Track>',
        '  <audio:Track id="music-track" timeline={program.timeline}>',
        '    <audio:Clip id="music-clip" source={music.media} during={program.window} gain="0.16"/>',
        '  </audio:Track>',
        '  <film:Film id="main" canvas={canvas.canvas} timeline={program.timeline} appearance={style.film.main}>',
        *film_tracks, '  </film:Film>',
        '  <html:Video id="final" composition={main.composition} timeline={program.timeline}/>', '</svml>', ''
    ])


def compose_hypit(project, source_video, voice, music, rows, output):
    hypit_project = Path(project) / "compose" / "hypit"
    hypit_project.mkdir(parents=True, exist_ok=True)
    if not (hypit_project / "package.json").exists():
        _run(["media-stack", "init", "--project", str(hypit_project)])
    for path, asset_id in ((source_video, "picture"), (voice, "voice"), (music, "music")):
        _import(hypit_project, path, asset_id)
    overlays = []
    overlay_dir = Path(project) / "compose" / "overlays"
    overlay_dir.mkdir(parents=True, exist_ok=True)
    for index, row in enumerate(rows, 1):
        caption = make_overlay(overlay_dir / f"caption-{index}.png", row["caption"], "caption")
        _import(hypit_project, caption, f"caption-{index}")
        overlays.append(caption)
        if row.get("onscreen_copy"):
            copy = make_overlay(overlay_dir / f"copy-{index}.png", row["onscreen_copy"], "copy")
            _import(hypit_project, copy, f"copy-{index}")
            overlays.append(copy)
    duration = rows[-1]["end"]
    (hypit_project / "main.svml").write_text(_hypit_source(rows, duration, overlays), encoding="utf-8")
    (hypit_project / "style.svs").write_text(
        '<?svml using="@hypit/recipe@1"?>\n<sheet version="1">\n  film.main { background: "#000000"; }\n</sheet>\n', encoding="utf-8")
    (hypit_project / "build.svrun").write_text(
        '<?svml using="@hypit/markup/run@1"?>\n<svrun version="1">\n  <author source="./main.svml"/>\n  <target output="final.video"/>\n</svrun>\n', encoding="utf-8")
    _run(["hypit", "check", "build.svrun"], cwd=hypit_project)
    result = _run(["hypit", "build", "build.svrun", "--title", "高质量复刻成片", "--follow", "--json"], cwd=hypit_project)
    payload = json.loads(result.stdout)
    build_id = _find_build_id(payload)
    if not build_id:
        raise ValueError("Hypit Build 未返回 build id")
    _run(["hypit", "get", build_id, "--output", "final.video", "--to", str(Path(output).resolve()), "--project", str(hypit_project)])
    return {"engine": "hypit", "build_id": build_id, "project": str(hypit_project)}


def compose_ffmpeg(source_video, voice, music, rows, output, workdir):
    srt = Path(workdir) / "captions.srt"
    srt.write_text(srt_text(rows), encoding="utf-8")
    escaped = str(srt).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(source_video), "-i", str(voice), "-i", str(music),
                    "-filter_complex", f"[0:v]subtitles='{escaped}'[v];[1:a][2:a]amix=inputs=2:duration=longest[a]",
                    "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart",
                    "-t", f"{rows[-1]['end']:.3f}", str(output)], check=True)
    return {"engine": "ffmpeg", "reason": "Hypit 不可用或执行失败"}


def compose_native(source_video, music, rows, output, workdir):
    """保留 H3 原声、统一响度、叠字幕；音乐仅作低音量音床。"""
    loudness = mean_volume(source_video)
    if loudness is None or loudness <= -50:
        raise ValueError("native 单元没有可用音轨或平均响度不高于 -50 dB")
    srt = Path(workdir) / "captions.srt"
    srt.write_text(srt_text(rows), encoding="utf-8")
    escaped = str(srt).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(source_video), "-i", str(music),
                    "-filter_complex", f"[0:v]subtitles='{escaped}'[v];[0:a]loudnorm=I=-16:TP=-1.5:LRA=11[n];"
                    "[1:a]volume=0.12[m];[n][m]amix=inputs=2:duration=first:dropout_transition=2[a]",
                    "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-c:a", "aac",
                    "-movflags", "+faststart", "-t", f"{rows[-1]['end']:.3f}", str(output)], check=True)
    return {"engine": "ffmpeg-native", "native_mean_volume_db": loudness, "music_gain": 0.12}


def compose_mixed(source_video, voice, music, rows, segments, output, workdir):
    """按单元在 H3 原声和 TTS 之间切换，最后统一响度并压低音乐。"""
    if mean_volume(source_video) is None:
        raise ValueError("混合路线中的 native 单元没有音轨")
    srt = Path(workdir) / "captions.srt"
    srt.write_text(srt_text(rows), encoding="utf-8")
    escaped = str(srt).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    pieces, labels, tts_cursor = [], [], 0.0
    for index, (row, segment) in enumerate(zip(rows, segments)):
        start, end = float(row["start"]), float(row["end"])
        duration, delay = end - start, int(round(start * 1000))
        label = f"unit{index}"
        if segment.get("audio_route", "tts") == "native":
            pieces.append(f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS,adelay={delay}|{delay}[{label}]")
        else:
            pieces.append(f"[1:a]atrim=start={tts_cursor:.3f}:end={tts_cursor + duration:.3f},asetpts=PTS-STARTPTS,adelay={delay}|{delay}[{label}]")
            tts_cursor += duration
        labels.append(f"[{label}]")
    filters = [f"[0:v]subtitles='{escaped}'[v]", *pieces,
               f"{''.join(labels)}amix=inputs={len(labels)}:duration=longest,loudnorm=I=-16:TP=-1.5:LRA=11[dialogue]",
               "[2:a]volume=0.12[music]",
               "[dialogue][music]amix=inputs=2:duration=first:dropout_transition=2[a]"]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(source_video), "-i", str(voice), "-i", str(music),
                    "-filter_complex", ";".join(filters), "-map", "[v]", "-map", "[a]",
                    "-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart",
                    "-t", f"{rows[-1]['end']:.3f}", str(output)], check=True)
    return {"engine": "ffmpeg-mixed", "audio_routes": [x.get("audio_route", "tts") for x in segments],
            "music_gain": 0.12}


def build(project, brief_path, refs=None, voice="Kore", review=False):
    project, brief_path = Path(project), Path(brief_path)
    brief = read_json(brief_path)
    script_path = project / "script.json"
    if not script_path.is_file():
        raise FileNotFoundError(f"缺少 script.json；请先运行 script_writer.py：{script_path}")
    brief = apply_script(brief, read_json(script_path))
    rows = timeline(brief)
    take_id = brief["id"]
    source = project / "takes" / f"{take_id}.mp4"
    if not source.is_file():
        raise FileNotFoundError(f"缺少逐段拼接视频：{source}")
    work = project / "compose"
    work.mkdir(parents=True, exist_ok=True)
    raw = project / "takes" / f"{take_id}.raw.mp4"
    if not raw.exists():
        shutil.copy2(source, raw)
    music = make_music(work / "music.wav", rows[-1]["end"])
    output = project / "takes" / f"{take_id}.composed.mp4"
    routes = {str(x.get("audio_route") or "tts") for x in brief.get("segments") or []}
    segments = brief.get("segments") or []
    if routes == {"native"}:
        info = compose_native(raw, music, rows, output, work)
    else:
        speech_raw = synthesize_speech(project, " ".join(
            row["caption"] for row, segment in zip(rows, brief.get("segments") or [])
            if segment.get("audio_route", "tts") == "tts"), work / "voiceover.raw.wav", voice)
        tts_duration = sum(row["end"] - row["start"] for row, segment in zip(rows, segments)
                           if segment.get("audio_route", "tts") == "tts")
        speech = fit_audio(speech_raw, work / "voiceover.wav", tts_duration)
        if "native" in routes:
            info = compose_mixed(raw, speech, music, rows, segments, output, work)
        else:
            try:
                info = compose_hypit(project, raw, speech, music, rows, output)
            except (FileNotFoundError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
                info = compose_ffmpeg(raw, speech, music, rows, output, work)
                info["hypit_error"] = type(exc).__name__
    output.replace(source)
    info.update(output=str(source), composed_at=_now(), subtitles=rows)
    write_json(work / "compose.json", info)
    job_path = project / "jobs" / f"{take_id}.json"
    job = read_json(job_path, {})
    # 两条合成路线都导出 1080×1920；最终评审应按成片规格检查，
    # 不能继续沿用生成段或父简报的分辨率。
    job.update(composed=True, compose_engine=info["engine"], composed_at=info["composed_at"],
               video=str(source), resolution="1080p")
    write_json(job_path, job)
    if review:
        review_panel.review_take(project, take_id, refs or [])
    return info


def main(argv=None):
    parser = argparse.ArgumentParser(description="用 Hypit 合成口播、字幕、画面文字与音乐")
    parser.add_argument("--project", required=True)
    parser.add_argument("--brief", required=True)
    parser.add_argument("--refs", default="", help="最终评审用产品图，逗号分隔")
    parser.add_argument("--voice", default="Kore")
    parser.add_argument("--review", action="store_true")
    args = parser.parse_args(argv)
    info = build(args.project, args.brief, [x for x in args.refs.split(",") if x.strip()], args.voice, args.review)
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
