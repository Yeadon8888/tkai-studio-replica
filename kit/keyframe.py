#!/usr/bin/env python3
"""产品首帧路线：产品参考图 → 首帧 → 新简报。

原简报不修改；新简报 id 加 ``-kf``，并把首帧和产品参考图放在 images 前两位。
图片编辑只提交一次，失败不自动重试。
"""
import argparse
import base64
import json
import mimetypes
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

KIT = Path(__file__).resolve().parent
sys.path.insert(0, str(KIT))
from common import _now, _read, _write  # noqa: E402
import ledger  # noqa: E402
import settings  # noqa: E402
import tt_research as tt  # noqa: E402


IMAGE_ROLE_PREFIX = "图 1 是首帧，图 2 是产品参考。"


def keyframe_prompt(brief):
    """优先读 keyframe.prompt；否则取原提示词的前 3 秒描述。"""
    configured = brief.get("keyframe")
    if isinstance(configured, dict) and str(configured.get("prompt") or "").strip():
        return str(configured["prompt"]).strip()
    if isinstance(configured, str) and configured.strip():
        return configured.strip()
    prompt = str(brief.get("prompt") or "").strip()
    patterns = (
        r"(?is)(?:^|[.\n]\s*)(0\s*[-–—]\s*3\s*seconds?\s*:[\s\S]*?)(?=\s*3\s*[-–—]\s*\d+\s*seconds?\s*:|$)",
        r"(?is)(?:^|[.\n]\s*)(前\s*3\s*秒\s*[:：][\s\S]*?)(?=\s*3\s*[-–—至]\s*\d+\s*秒\s*[:：]|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, prompt)
        if match:
            # 角色、场景和画面风格常写在 0–3 秒时间句之前，一并保留。
            return prompt[:match.end(1)].strip().rstrip(".")
    if not prompt:
        raise ValueError("简报缺少 keyframe.prompt 和 prompt，无法生成首帧描述")
    first = re.split(r"(?<=[.!?。！？])\s+", prompt, maxsplit=1)[0].strip()
    return first or prompt


def _ratio(value):
    match = re.fullmatch(r"\s*(\d+)\s*:\s*(\d+)\s*", str(value or ""))
    if not match or int(match.group(1)) <= 0 or int(match.group(2)) <= 0:
        raise ValueError(f"无效画面比例：{value!r}")
    return int(match.group(1)), int(match.group(2))


def _request_size(aspect_ratio):
    width, height = _ratio(aspect_ratio)
    if width == height:
        return "1024x1024"
    return "1024x1536" if width < height else "1536x1024"


def _multipart(fields, file_field, file_path):
    boundary = f"----studio-keyframe-{uuid.uuid4().hex}"
    chunks = []
    for name, value in fields.items():
        chunks.extend((f"--{boundary}\r\n".encode(),
                       f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                       str(value).encode("utf-8"), b"\r\n"))
    path = Path(file_path)
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    chunks.extend((f"--{boundary}\r\n".encode(),
                   f'Content-Disposition: form-data; name="{file_field}"; filename="{path.name}"\r\n'.encode(),
                   f"Content-Type: {mime}\r\n\r\n".encode(), path.read_bytes(), b"\r\n",
                   f"--{boundary}--\r\n".encode()))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _image_bytes(response, base, key):
    rows = response.get("data") or []
    if not rows or not isinstance(rows[0], dict):
        raise ValueError("图片编辑返回里没有 data[0]")
    item = rows[0]
    encoded = item.get("b64_json")
    if encoded:
        return base64.b64decode(encoded, validate=True)
    url = item.get("url")
    if not isinstance(url, str) or not url.startswith(("https://", "http://")):
        raise ValueError("图片编辑返回里没有可用的 b64_json 或 url")
    headers = {"User-Agent": settings.get("HTTP_USER_AGENT")}
    if url.startswith(base.rstrip("/") + "/"):
        headers["Authorization"] = f"Bearer {key}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=300) as result:
        return result.read()


def request_edit(product_reference, prompt, size):
    """调用 TokensFactory GPT Image 文件编辑接口，返回图片字节。"""
    base, key = tt.load_tf_env()
    body, content_type = _multipart(
        {"model": settings.model("image"), "prompt": prompt, "size": size, "n": 1},
        "image", product_reference,
    )
    req = urllib.request.Request(
        base.rstrip("/") + "/v1/images/edits", data=body, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": content_type,
                 "User-Agent": settings.get("HTTP_USER_AGENT")},
    )
    with urllib.request.urlopen(req, timeout=300) as result:
        response = json.loads(result.read().decode("utf-8"))
    return _image_bytes(response, base, key)


def _dimensions(path):
    raw = subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path),
    ], text=True).strip()
    match = re.fullmatch(r"(\d+)x(\d+)", raw)
    if not match:
        raise ValueError(f"ffprobe 无法读取图片尺寸：{raw[:100]}")
    return int(match.group(1)), int(match.group(2))


def normalize_aspect(path, aspect_ratio):
    """中心裁切为简报比例，再用 ffprobe 核验。"""
    path = Path(path)
    source_w, source_h = _dimensions(path)
    ratio_w, ratio_h = _ratio(aspect_ratio)
    target_w = min(source_w, source_h * ratio_w // ratio_h)
    target_h = target_w * ratio_h // ratio_w
    if target_h > source_h or target_w <= 0 or target_h <= 0:
        target_h = min(source_h, source_w * ratio_h // ratio_w)
        target_w = target_h * ratio_w // ratio_h
    # 向下取整为比例的整数倍，保证像素比精确。
    scale = min(target_w // ratio_w, target_h // ratio_h)
    target_w, target_h = ratio_w * scale, ratio_h * scale
    if (source_w, source_h) != (target_w, target_h):
        tmp = path.with_name(path.stem + ".crop.png")
        subprocess.run([
            "ffmpeg", "-v", "error", "-y", "-i", str(path),
            "-vf", f"crop={target_w}:{target_h}", "-frames:v", "1", str(tmp),
        ], check=True)
        os.replace(tmp, path)
    actual = _dimensions(path)
    if actual != (target_w, target_h) or actual[0] * ratio_h != actual[1] * ratio_w:
        raise ValueError(f"首帧尺寸核验失败：{actual[0]}x{actual[1]}，期望比例 {aspect_ratio}")
    return actual


def _append_image_ledger(project, ref, status):
    """图片调用只写一行；未配价时 estimate_cny 为 null。"""
    price = settings.image_price()
    ledger.LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    row = {
        "time": _now(), "provider": "tokensfactory",
        "endpoint": settings.model("image"), "status": str(status),
        "project": project, "ref": ref, "units": 1, "usage": {},
        "estimate_cny": price, "kind": "image",
    }
    with ledger.LEDGER_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def generate_frame(project, frame_id, product_reference, description, aspect_ratio=None, edit=request_edit):
    """为一个分段生成锁定真实产品的关键帧；同一 ``frame_id`` 永不重复提交。"""
    project = Path(project)
    product_reference = Path(product_reference).resolve()
    if not product_reference.is_file():
        raise FileNotFoundError(f"产品参考图不存在：{product_reference}")
    aspect = aspect_ratio or settings.get("VIDEO_ASPECT")
    out = project / "keyframes" / f"{frame_id}.png"
    if out.exists():
        raise FileExistsError(f"{frame_id} 已存在，为避免重复付费不再提交")
    prompt = (
        "以上传图片中的产品为唯一产品参考，严格保留形状、颜色、材质、比例和标签，"
        "标签区域保持参考图原样，不新增文字、数字、标识或水印。"
        f"生成竖屏视频关键帧，画面比例 {aspect}，主体位于安全区中央。\n关键帧描述：{description}"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        out.write_bytes(edit(product_reference, prompt, _request_size(aspect)))
        normalize_aspect(out, aspect)
    except Exception as exc:
        out.unlink(missing_ok=True)
        status = exc.code if isinstance(exc, urllib.error.HTTPError) else type(exc).__name__
        _append_image_ledger(project.name, frame_id, status)
        raise
    _append_image_ledger(project.name, frame_id, "ok")
    return out.resolve()


def build(project, brief_path, product_reference, edit=request_edit):
    project = Path(project)
    brief_path = Path(brief_path)
    product_reference = Path(product_reference).resolve()
    if not product_reference.is_file():
        raise FileNotFoundError(f"产品参考图不存在：{product_reference}")
    brief = _read(brief_path)
    new_id = str(brief["id"]) + "-kf"
    out = project / "keyframes" / f"{new_id}.png"
    if out.exists() or (project / "briefs" / f"{new_id}.json").exists():
        raise FileExistsError(f"{new_id} 已存在，为避免重复付费不再提交")
    description = keyframe_prompt(brief)
    aspect = brief.get("aspect_ratio") or settings.get("VIDEO_ASPECT")
    edit_prompt = (
        f"以上传图片中的产品为唯一产品参考，严格保留形状、颜色、材质、比例和标签。"
        f"生成视频首帧，画面比例 {aspect}。构图中心内容留在画面中央，不要新增文字、数字、标识或水印。\n"
        f"首帧描述：{description}"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        image = edit(product_reference, edit_prompt, _request_size(aspect))
        out.write_bytes(image)
        dimensions = normalize_aspect(out, aspect)
    except Exception as exc:
        out.unlink(missing_ok=True)
        status = exc.code if isinstance(exc, urllib.error.HTTPError) else type(exc).__name__
        _append_image_ledger(project.name, new_id, status)
        raise
    _append_image_ledger(project.name, new_id, "ok")
    generated = dict(brief)
    original_keyframe = brief.get("keyframe") if isinstance(brief.get("keyframe"), dict) else {}
    last_frame = brief.get("last_frame") or original_keyframe.get("last_frame")
    generated.update({
        "id": new_id,
        "parent": brief["id"],
        "images": [str(out.resolve()), str(product_reference)] + [
            str(x) for x in brief.get("images", []) if Path(str(x)).resolve() != product_reference
        ],
        "prompt": IMAGE_ROLE_PREFIX + " " + str(brief.get("prompt") or ""),
        "first_frame": str(out.resolve()),
        "keyframe": {"prompt": description, "image": str(out.resolve()),
                     "width": dimensions[0], "height": dimensions[1], "model": settings.model("image")},
    })
    if last_frame:
        generated["last_frame"] = str(last_frame)
        generated["video_route"] = "keyframe"
        generated["prompt"] = str(brief.get("prompt") or "")
    destination = project / "briefs" / f"{new_id}.json"
    _write(destination, generated)
    return destination, out


def main(argv=None):
    parser = argparse.ArgumentParser(description="用产品参考图生成首帧并写出 -kf 简报")
    parser.add_argument("--project", required=True, help="新运行目录")
    parser.add_argument("--brief", required=True, help="原简报 JSON")
    parser.add_argument("--product-reference", required=True, help="真实产品参考图")
    args = parser.parse_args(argv)
    try:
        brief, image = build(args.project, args.brief, args.product_reference)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"图片编辑失败 HTTP {exc.code}，不重试") from None
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, ValueError) as exc:
        raise SystemExit(f"图片编辑失败 {type(exc).__name__}，不重试：{str(exc)[:180]}") from None
    print(f"首帧 → {image}")
    print(f"新简报 → {brief}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
