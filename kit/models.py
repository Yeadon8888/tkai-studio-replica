"""模型目录、按需求选择和付费请求前校验。"""
import json
import math
from pathlib import Path

import yaml

import settings


CATALOG_PATH = settings.KIT / "config" / "models.yaml"
_cache = None


def _validate(item):
    required = {"id", "provider", "kind", "prices", "policy", "verified", "notes"}
    missing = sorted(required - set(item))
    if missing:
        raise ValueError(f"模型目录 {item.get('id', '<unknown>')} 缺字段：{', '.join(missing)}")
    if item["provider"] != "tokensfactory":
        raise ValueError(f"模型 {item['id']} provider 必须是 tokensfactory")
    if item["kind"] not in {"text", "video", "image", "tts", "music"}:
        raise ValueError(f"模型 {item['id']} kind 非法：{item['kind']}")
    if item["policy"] not in {"allowed", "ask_first", "retired"}:
        raise ValueError(f"模型 {item['id']} policy 非法：{item['policy']}")
    if not isinstance(item["verified"], dict) or not item["verified"].get("date") \
            or not item["verified"].get("basis"):
        raise ValueError(f"模型 {item['id']} verified 必须包含 date 与 basis")
    if item["kind"] == "text":
        for key in ("reads_video", "reads_audio", "json_reliable"):
            if key not in item:
                raise ValueError(f"文本模型 {item['id']} 缺字段：{key}")
    if item["kind"] == "video":
        for key in ("durations", "resolutions", "aspects", "images_min", "images_max",
                    "audio_max", "first_last", "ref_video", "seed"):
            if key not in item:
                raise ValueError(f"视频模型 {item['id']} 缺字段：{key}")


def load(force=False):
    """读取并校验目录，返回模型列表；重复 id 会直接阻塞。"""
    global _cache
    if _cache is not None and not force:
        return _cache
    raw = yaml.safe_load(Path(CATALOG_PATH).read_text(encoding="utf-8")) or {}
    items = raw.get("models")
    if not isinstance(items, list) or not items:
        raise ValueError("models.yaml 的 models 必须是非空列表")
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("models.yaml 每一项必须是对象")
        _validate(item)
        if item["id"] in seen:
            raise ValueError(f"模型目录 id 重复：{item['id']}")
        seen.add(item["id"])
    _cache = items
    return _cache


def get(model_id):
    """按公开模型 id 取目录项。"""
    for item in load():
        if item["id"] == model_id:
            return item
    raise KeyError(f"模型目录没有 {model_id}")


def _health_bad(model_id):
    path = settings.studio_home() / ".cache" / "model_health.json"
    if not path.exists():
        return False, ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, ""
    row = (data.get("models") or data).get(model_id) if isinstance(data, dict) else None
    if not isinstance(row, dict):
        return False, ""
    bad = row.get("healthy") is False or row.get("status") in {"failed", "block", "异常"}
    return bad, str(row.get("reason") or row.get("detail") or "最近体检失败")


def _allowed_ids():
    raw = settings.get("MODELS_ALLOWED").strip()
    return {x.strip() for x in raw.split(",") if x.strip()} if raw else None


def _contains(value, spec):
    if spec is None:
        return None
    if isinstance(spec, list):
        return value in spec
    if isinstance(spec, dict):
        return spec.get("min", -math.inf) <= value <= spec.get("max", math.inf)
    return value == spec


def _base_resolution(value):
    return settings.base_resolution(value)


def _need_count(need, name):
    value = need.get(name)
    if isinstance(value, (list, tuple)):
        return len(value)
    if value is None:
        return None
    return int(value)


def unmet(item, need):
    """列出模型不满足需求的原因；未知能力不会被当作支持。"""
    reasons = []
    if need.get("duration") is not None:
        duration = float(need["duration"])
        if _contains(duration, item.get("durations")) is not True:
            reasons.append(f"时长 {duration:g}s 不支持")
    if need.get("resolution"):
        resolution = _base_resolution(need["resolution"])
        options = item.get("resolutions")
        normalized = [_base_resolution(x) for x in options] if isinstance(options, list) else options
        if normalized is None or resolution not in normalized:
            reasons.append(f"分辨率 {need['resolution']} 不支持")
    if need.get("aspect") or need.get("aspect_ratio"):
        aspect = str(need.get("aspect") or need.get("aspect_ratio"))
        if not isinstance(item.get("aspects"), list) or aspect not in item["aspects"]:
            reasons.append(f"宽高比 {aspect} 不支持")
    images = _need_count(need, "images")
    if images is not None:
        low, high = item.get("images_min"), item.get("images_max")
        if low is None or high is None or not low <= images <= high:
            reasons.append(f"图片数 {images} 不支持")
    audios = _need_count(need, "audio")
    if audios is None:
        audios = _need_count(need, "audio_urls")
    if audios is not None and (item.get("audio_max") is None or audios > item["audio_max"]):
        reasons.append(f"音频数 {audios} 不支持")
    for key, label in (("first_last", "首尾帧"), ("ref_video", "参考视频"),
                       ("reads_video", "读取视频"), ("reads_audio", "读取声音"),
                       ("json_reliable", "稳定 JSON")):
        if need.get(key) is True and item.get(key) is not True:
            reasons.append(f"不支持{label}")
        if need.get(key) is False and item.get(key) is True and key in ("first_last", "ref_video"):
            reasons.append(f"需要{label}输入")
    if need.get("seed") is not None:
        seed = item.get("seed")
        value = int(need["seed"])
        if not isinstance(seed, dict) or not seed.get("min", -math.inf) <= value <= seed.get("max", math.inf):
            reasons.append(f"seed {value} 不支持")
    return reasons


def estimate(item, need):
    prices = item.get("prices") or {}
    per_second = prices.get("per_second") or {}
    resolution = _base_resolution(need.get("resolution") or settings.get("VIDEO_RESOLUTION"))
    if resolution in per_second and need.get("duration") is not None:
        return round(float(per_second[resolution]) * float(need["duration"]), 4)
    per_request = prices.get("per_request") or {}
    if "default" in per_request:
        return float(per_request["default"])
    return None


def select(kind, need=None):
    """从允许、白名单内且健康的模型中选择满足需求、估价最低的一项。"""
    need = dict(need or {})
    explicit = need.pop("model", None)
    role = need.pop("role", None)
    route = need.pop("video_route", None)
    if not explicit and kind == "video" and route and route != "auto":
        explicit = settings.explicit_value(f"VIDEO_ROUTE_{str(route).upper()}") or settings.video_route_model(route)
    if not explicit:
        config_role = str(role or kind).upper()
        explicit = settings.explicit_value(f"MODEL_{config_role}")
    allow_ask = bool(need.pop("allow_ask_first", False))
    whitelist = _allowed_ids()
    candidates = [m for m in load() if m["kind"] == kind]
    failures = []
    eligible = []
    for item in candidates:
        reasons = []
        if explicit and item["id"] != explicit:
            continue
        if item["policy"] == "retired":
            reasons.append("已退役")
        elif item["policy"] == "ask_first" and not allow_ask:
            reasons.append("需要人先确认")
        if whitelist is not None and item["id"] not in whitelist:
            reasons.append("不在 MODELS_ALLOWED 白名单")
        bad, detail = _health_bad(item["id"])
        if bad:
            reasons.append("体检异常" + (f"：{detail}" if detail else ""))
        reasons.extend(unmet(item, need))
        if reasons:
            failures.append(f"{item['id']}：{'；'.join(reasons)}")
        else:
            cost = estimate(item, need)
            eligible.append((math.inf if cost is None else cost, item["id"], item, cost))
    if explicit and not any(m["id"] == explicit for m in candidates):
        raise ValueError(f"显式指定的模型 {explicit} 不在 {kind} 目录中")
    if not eligible:
        details = "\n".join(f"- {x}" for x in failures) or "- 没有该类型候选"
        prefix = f"显式指定的模型 {explicit} 不满足需求" if explicit else f"没有满足需求的 {kind} 模型"
        raise ValueError(prefix + "：\n" + details)
    _, _, item, cost = min(eligible, key=lambda row: (row[0], row[1]))
    reason = "满足全部需求"
    reason += f"，估价 {cost:.4g} 元且为候选最低" if cost is not None else "；目录没有可比较价格，按模型 id 稳定选择"
    if explicit:
        reason = "使用显式指定模型；" + reason
    return {"model": item["id"], "reason": reason, "estimate_cny": cost}


def check_request(model, body):
    """按目录校验视频请求能力；返回 True，失败时列出全部不满足项。"""
    item = get(model)
    if item["kind"] != "video":
        raise ValueError(f"{model} 不是视频模型")
    need = {
        "duration": body.get("duration"),
        "resolution": body.get("resolution"),
        "aspect_ratio": body.get("aspect_ratio") or _aspect_from_resolution(body.get("resolution")),
        "images": len(body.get("images") or []),
        "audio_urls": len(body.get("audio_urls") or []),
        "first_last": bool(body.get("first_frame") or body.get("last_frame")),
        "ref_video": bool(body.get("ref_video") or body.get("reference_videos")),
        "seed": body.get("seed"),
    }
    # 没有时长的动作迁移接口由参考视频决定，不把缺失值当作 0。
    need = {k: v for k, v in need.items() if v is not None}
    reasons = unmet(item, need)
    if bool(body.get("first_frame")) != bool(body.get("last_frame")):
        reasons.append("first_frame 与 last_frame 必须同时提供")
    if reasons:
        raise ValueError(f"{model} 请求不满足模型目录：" + "；".join(reasons))
    return True


def _aspect_from_resolution(value):
    value = str(value or "")
    if "竖" in value or value.startswith("464*832"):
        return "9:16"
    if "横" in value or value.startswith("832*464"):
        return "16:9"
    if "1:1" in value:
        return "1:1"
    return None
