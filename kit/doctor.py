#!/usr/bin/env python3
"""本地配置、在线模型清单与模型能力体检。"""
import argparse
import base64
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import urllib.error
from pathlib import Path

KIT = Path(__file__).resolve().parent
sys.path.insert(0, str(KIT))

import ledger  # noqa: E402
import loop  # noqa: E402
import media_host  # noqa: E402
import models  # noqa: E402
import settings  # noqa: E402
from common import http_json, parse_json  # noqa: E402


def local_checks():
    rows = []
    rows.append(("block", "Python ≥ 3.9", sys.version_info >= (3, 9), sys.version.split()[0]))
    try:
        import yaml  # noqa: F401
        rows.append(("block", "依赖 pyyaml", True, ""))
    except ImportError:
        rows.append(("block", "依赖 pyyaml", False, "pip install -r kit/requirements.txt"))
    for tool in ("ffmpeg", "ffprobe"):
        rows.append(("block", tool, shutil.which(tool) is not None, shutil.which(tool) or "未安装"))
    cfg = settings.load(force=True)
    rows.append(("warn", ".env 文件", bool(cfg["_env_file"]), cfg["_env_file"] or "未找到，正在使用默认值"))
    rows.append(("block", "配置版本", str(cfg["CONFIG_VERSION"]) == str(settings.CONFIG_VERSION),
                 f".env={cfg['CONFIG_VERSION']} 代码={settings.CONFIG_VERSION}"))
    for key in settings.SECRET_KEYS:
        rows.append(("block", f"密钥 {key}", bool(os.environ.get(key)),
                     "已找到" if os.environ.get(key) else "未找到：检查 CREDENTIALS_FILES"))
    home = settings.studio_home()
    try:
        home.mkdir(parents=True, exist_ok=True)
        writable = os.access(home, os.W_OK)
    except OSError:
        writable = False
    rows.append(("block", "数据目录可写", writable, str(home)))
    try:
        catalog = models.load(force=True)
        rows.append(("block", "模型目录", bool(catalog), f"{len(catalog)} 个模型"))
    except Exception as exc:
        rows.append(("block", "模型目录", False, str(exc)))
    try:
        weights = settings.tier_weights()
        rows.append(("block", "TIER_WEIGHTS 可解析", bool(weights) and all(v >= 0 for v in weights.values()), str(weights)))
    except ValueError as exc:
        rows.append(("block", "TIER_WEIGHTS 可解析", False, str(exc)))
    price = settings.video_price()
    rows.append(("block", "视频单价 > 0", price is not None and price > 0, f"{price} 元/秒"))
    return rows


def fetch_model_ids():
    base = settings.get("TOKENSFACTORY_BASE_URL").rstrip("/")
    key = os.environ.get("TOKENSFACTORY_API_KEY", "")
    data = http_json("GET", base + "/v1/models",
                     {"Authorization": f"Bearer {key}", "User-Agent": settings.get("HTTP_USER_AGENT")}, timeout=30)
    return {item.get("id") for item in data.get("data", []) if item.get("id")}


def online_checks(ids=None):
    rows = []
    try:
        ids = ids if ids is not None else fetch_model_ids()
    except Exception as exc:
        return [("block", "模型列表 /v1/models", False, type(exc).__name__)]
    rows.append(("block", "模型列表 /v1/models", True, f"{len(ids)} 个模型"))
    wanted = {"video": settings.model("video"), "image": settings.model("image"),
              "tts": settings.model("tts"), **{role: settings.model(role) for role in settings.TEXT_ROLES}}
    for role, model_id in wanted.items():
        rows.append(("block", f"模型 {role} = {model_id}", model_id in ids,
                     "存在" if model_id in ids else "供应方没有这个模型名"))
    return rows


def _append_probe(record):
    path = settings.studio_home() / "ledger" / "probes.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _update_health(model_id, healthy, reason, probe):
    path = settings.studio_home() / ".cache" / "model_health.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except ValueError:
        data = {}
    data.setdefault("models", {})[model_id] = {
        "healthy": bool(healthy), "status": "ok" if healthy else "failed", "reason": reason, "probe": probe,
        "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _record(model_id, probe, healthy, reason, usage=None):
    row = {"time": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "provider": "tokensfactory", "model": model_id, "probe": probe,
           "healthy": bool(healthy), "reason": reason, "usage": usage or {}}
    _append_probe(row)
    _update_health(model_id, healthy, reason, probe)
    return ("block", f"体检 {probe} / {model_id}", bool(healthy), reason)


def _chat(model_id, parts, ref, max_tokens=120):
    base = settings.get("TOKENSFACTORY_BASE_URL").rstrip("/")
    key = os.environ.get("TOKENSFACTORY_API_KEY", "")
    body = {"model": model_id, "messages": [{"role": "user", "content": parts}],
            "max_tokens": max_tokens, "temperature": 0}
    try:
        data = http_json("POST", base + "/v1/chat/completions",
                         {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                          "User-Agent": settings.get("HTTP_USER_AGENT")}, body, timeout=240)
    except urllib.error.HTTPError as exc:
        ledger.append("tokensfactory", model_id, exc.code, project="model-doctor", ref=ref, units=1, kind="text")
        raise
    usage = data.get("usage") or {}
    ledger.append("tokensfactory", model_id, "ok", project="model-doctor", ref=ref,
                  units=1, usage=usage, kind="text")
    return data["choices"][0]["message"].get("content") or "", usage


def probe_text(model_id):
    prompt = '只输出严格 JSON，不要代码围栏或说明：{"answer":4}。问题：2+2 等于多少？'
    try:
        text, usage = _chat(model_id, [{"type": "text", "text": prompt}], "text-json")
        value = parse_json(text, require=("answer",))
        ok = isinstance(value, dict) and value.get("answer") == 4
        return _record(model_id, "text", ok, "严格 JSON 可解析" if ok else "输出不是要求的严格 JSON", usage)
    except Exception as exc:
        return _record(model_id, "text", False, f"调用失败：{type(exc).__name__}")


def _make_probe_video(path):
    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=320x240:d=2:r=24",
           "-f", "lavfi", "-i", "sine=frequency=880:duration=2",
           "-vf", "drawtext=text=7:fontcolor=white:fontsize=120:x=(w-text_w)/2:y=(h-text_h)/2",
           "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("ffmpeg 测试片生成失败：" + result.stderr[-300:])


def probe_video_read(model_ids):
    rows = []
    clip = settings.studio_home() / ".cache" / "model-probe-red-7.mp4"
    upload_info = None
    try:
        clip.parent.mkdir(parents=True, exist_ok=True)
        _make_probe_video(clip)
        upload_info = media_host.upload(clip)
        for model_id in model_ids:
            parts = [{"type": "text", "text":
                      '只输出严格 JSON：{"color":"red","number":7}。回答视频背景主色和画面中央数字。'},
                     {"type": "video_url", "video_url": upload_info["url"]}]
            try:
                text, usage = _chat(model_id, parts, "video-read", max_tokens=160)
                ingested = loop.video_ingested(parts, usage)
                value = parse_json(text, require=("color", "number"))
                answer = isinstance(value, dict) and str(value.get("color", "")).lower() in {"red", "红", "红色"} \
                    and str(value.get("number")) == "7"
                ok = ingested is True and answer
                if ingested is False:
                    reason = "视频 token 判定未摄取"
                elif ingested is None:
                    reason = "响应缺少 token 明细，无法确认摄取"
                elif not answer:
                    reason = "画面问题回答错误"
                else:
                    reason = "视频已摄取且画面问题回答正确"
                rows.append(_record(model_id, "video_read", ok, reason, usage))
            except Exception as exc:
                rows.append(_record(model_id, "video_read", False, f"调用失败：{type(exc).__name__}"))
    except Exception as exc:
        for model_id in model_ids:
            rows.append(_record(model_id, "video_read", False, f"测试片准备失败：{type(exc).__name__}"))
    finally:
        if upload_info:
            try:
                deleted = media_host.delete(upload_info)
                media_host.log(upload_info, {"purpose": "model_probe", "deleted": deleted})
            except Exception as exc:
                rows.append(("block", "删除体检临时视频", False, type(exc).__name__))
        clip.unlink(missing_ok=True)
    return rows


def probe_tts(model_id):
    base = settings.get("TOKENSFACTORY_BASE_URL").rstrip("/")
    key = os.environ.get("TOKENSFACTORY_API_KEY", "")
    body = {"contents": [{"parts": [{"text": "模型体检正常。"}]}],
            "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Kore"}}}}}
    try:
        data = http_json("POST", f"{base}/v1beta/models/{model_id}:generateContent",
                         {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                          "User-Agent": settings.get("HTTP_USER_AGENT")}, body, timeout=240)
        usage = data.get("usageMetadata") or {}
        audio = next((part.get("inlineData") for cand in data.get("candidates", [])
                      for part in (cand.get("content") or {}).get("parts", []) if part.get("inlineData")), None)
        raw = base64.b64decode(audio["data"]) if audio and audio.get("data") else b""
        out = settings.studio_home() / ".cache" / "tts-probe.wav"
        out.write_bytes(raw)
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-of", "default=nw=1:nk=1", str(out)], capture_output=True, text=True)
        duration = float(probe.stdout.strip() or 0) if probe.returncode == 0 else 0
        out.unlink(missing_ok=True)
        ledger.append("tokensfactory", model_id, "ok", project="model-doctor", ref="tts",
                      units=1, usage=usage, kind="tts")
        return _record(model_id, "tts", duration > 0, f"音频时长 {duration:.3f}s", usage)
    except Exception as exc:
        ledger.append("tokensfactory", model_id, type(exc).__name__, project="model-doctor", ref="tts",
                      units=1, kind="tts")
        return _record(model_id, "tts", False, f"调用失败：{type(exc).__name__}")


def run_probes(spec, ids):
    rows = []
    for name in [x.strip() for x in spec.split(",") if x.strip()]:
        if name == "text":
            rows.append(probe_text(settings.model("text")))
        elif name == "video_read":
            rows.extend(probe_video_read([x.strip() for x in settings.get("MODEL_VIDEO_READ_PROBE").split(",")
                                          if x.strip()]))
        elif name == "tts":
            rows.append(probe_tts(settings.model("tts")))
        elif name in settings.TEXT_ROLES:
            rows.append(probe_text(settings.model(name)))
        else:
            try:
                item = models.get(name)
            except KeyError:
                rows.append(("block", f"体检 {name}", False, "未知 role 或模型"))
                continue
            if item["kind"] == "text":
                rows.append(probe_text(name))
            elif item["kind"] == "tts":
                rows.append(probe_tts(name))
            else:
                rows.append(_record(name, "availability", name in ids,
                                    "模型列表中存在" if name in ids else "模型列表中不存在"))
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description="studio-replica 体检")
    parser.add_argument("--online", action="store_true", help="查询免费模型列表")
    parser.add_argument("--probe", default="", help="逗号分隔：text、video_read、tts、文本 role 或模型 id")
    args = parser.parse_args(argv)
    rows = local_checks()
    ids = set()
    if args.online or args.probe:
        try:
            ids = fetch_model_ids()
            rows.extend(online_checks(ids) if args.online else [])
        except Exception as exc:
            rows.append(("block", "模型列表 /v1/models", False, type(exc).__name__))
    if args.probe and ids:
        rows.extend(run_probes(args.probe, ids))
    print(f"studio-replica {settings.skill_version()}")
    bad = 0
    for level, name, ok, note in rows:
        mark = "通过" if ok else ("阻塞" if level == "block" else "提醒")
        if not ok and level == "block":
            bad += 1
        print(f"[{mark}] {name}  {note}")
    if bad:
        print("提示：体检异常模型会被自动选模跳过；请检查 .env 的模型配置或 MODELS_ALLOWED。")
    print("结果：", "全部通过" if not bad else f"{bad} 项阻塞")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
