"""共享的小工具：时间、JSON、产品锚点、HTTP 与图片 data URI。"""
import base64
import datetime as dt
import json
import re
import urllib.request
from pathlib import Path

CST = dt.timezone(dt.timedelta(hours=8))


def _now():
    return dt.datetime.now(CST).isoformat(timespec="seconds")


def read_json(path, default=None):
    """读取 JSON；文件不存在时返回 ``default``。"""
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def write_json(path, obj):
    """创建父目录并以易读格式写入 JSON。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def read_anchor(directory):
    """读取目录里的产品锚点；文件不存在时返回空字符串。"""
    path = Path(directory) / "product_anchor.txt"
    return path.read_text(encoding="utf-8").strip() if path.is_file() else ""


def http_request(url, data=None, method=None, headers=None):
    """构造唯一的对外 HTTP 请求。

    无论调用方是否传入 User-Agent，都统一使用配置的 HTTP_USER_AGENT，
    避免 Python 默认 UA 被 Cloudflare 拒绝。
    """
    import settings  # 延迟导入，避免工具模块初始化循环
    request = url if isinstance(url, urllib.request.Request) else urllib.request.Request(
        str(url), data=data, method=method, headers=headers or {})
    if isinstance(url, urllib.request.Request):
        for key, value in (headers or {}).items():
            request.add_header(key, value)
    request.remove_header("User-agent")
    request.add_header("User-Agent", settings.get("HTTP_USER_AGENT"))
    return request


def http_open(url, data=None, method=None, headers=None, timeout=30, opener=None):
    """统一打开对外 HTTP 请求；返回的响应由调用方关闭。"""
    request = http_request(url, data=data, method=method, headers=headers)
    return (opener or urllib.request.urlopen)(request, timeout=timeout)


def http_json(method, url, headers=None, body=None, timeout=30, opener=None):
    """发送 HTTP 请求并解析 JSON；非 JSON/网络错误原样抛给调用方。"""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    with http_open(url, data=data, method=method, headers=headers, timeout=timeout, opener=opener) as response:
        return json.loads(response.read().decode("utf-8") or "{}")


# 兼容已有模块；实现只保留在上面的公开函数中。
def _read(path):
    sentinel = object()
    value = read_json(path, sentinel)
    if value is sentinel:
        raise FileNotFoundError(path)
    return value


def _write(path, obj):
    write_json(path, obj)


def _data_uri(path):
    p = Path(path)
    mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}[p.suffix.lower()]
    return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode("ascii")


def parse_json(text, require=()):
    """从模型输出中取出第一个完整 JSON 值；支持代码围栏和前后说明，失败返回 None。
    require：必须包含的键。给了 require 时，只返回包含这些键的对象——
    输出被截断时，外层对象解析失败，不能把里面的子对象（例如 scores）误当成整份结果。"""
    if not isinstance(text, str):
        return None
    s = re.sub(r"```(?:json)?\s*", "", text, flags=re.I).replace("```", "")
    decoder = json.JSONDecoder()
    for match in re.finditer(r"[\[{]", s):
        try:
            value, _ = decoder.raw_decode(s[match.start():])
        except ValueError:
            continue
        if require and not (isinstance(value, dict) and all(k in value for k in require)):
            continue
        return value
    return None
