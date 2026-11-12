"""共享的小工具：时间、JSON 读写、图片转 data URI。被 loop.py、review_panel.py 共用，避免各自再写一份。"""
import base64
import datetime as dt
import json
import re
from pathlib import Path

CST = dt.timezone(dt.timedelta(hours=8))


def _now():
    return dt.datetime.now(CST).isoformat(timespec="seconds")


def _read(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _write(p, obj):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _data_uri(path):
    p = Path(path)
    mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}[p.suffix.lower()]
    return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode("ascii")


def parse_json(text):
    """从模型输出中取出第一个完整 JSON 值；支持代码围栏和前后说明，失败返回 None。"""
    if not isinstance(text, str):
        return None
    s = re.sub(r"```(?:json)?\s*", "", text, flags=re.I).replace("```", "")
    decoder = json.JSONDecoder()
    for match in re.finditer(r"[\[{]", s):
        try:
            value, _ = decoder.raw_decode(s[match.start():])
            return value
        except ValueError:
            continue
    return None
