"""流域中台制作请求客户端。

只使用 Python 标准库。令牌只从权限为 0600 的文件读取，异常文本不会包含令牌。
"""
from __future__ import annotations

import json
import socket
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import settings


class PlatformError(RuntimeError):
    """中台请求失败；message 已清理，可安全写日志。"""

    def __init__(self, message, status=None, body=None):
        super().__init__(message)
        self.status = status
        self.body = body


def read_token(path):
    """读取 ffa_ 令牌；文件必须是普通文件且权限恰好为 0600。"""
    token_path = Path(path).expanduser()
    try:
        info = token_path.stat()
    except OSError as exc:
        raise PlatformError(f"中台令牌文件不可读：{token_path}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise PlatformError("中台令牌路径不是普通文件")
    mode = stat.S_IMODE(info.st_mode)
    if mode != 0o600:
        raise PlatformError(f"中台令牌文件权限必须为 600，当前为 {mode:03o}")
    token = token_path.read_text(encoding="utf-8").strip()
    if not token.startswith("ffa_") or any(ch.isspace() for ch in token):
        raise PlatformError("中台令牌格式无效")
    return token


class PlatformClient:
    def __init__(self, base_url=None, token_file=None, runner_id=None, timeout=30, opener=None):
        self.base_url = (base_url or settings.get("PLATFORM_BASE_URL")).rstrip("/")
        self.runner_id = runner_id or settings.get("RUNNER_ID") or socket.gethostname()
        self.timeout = float(timeout)
        self._token = read_token(token_file or settings.get("PLATFORM_TOKEN_FILE"))
        self._open = opener or urllib.request.urlopen

    def _request(self, method, path, payload=None, data=None, headers=None, expect_json=True):
        if payload is not None and data is not None:
            raise ValueError("payload 与 data 不能同时提供")
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else data
        request_headers = {
            "Authorization": f"Bearer {self._token}",
            "X-Runner-ID": self.runner_id,
            "User-Agent": "studio-platform-runner/1",
            **({"Content-Type": "application/json"} if payload is not None else {}),
            **(headers or {}),
        }
        request = urllib.request.Request(self.base_url + path, data=body, method=method,
                                         headers=request_headers)
        last = None
        for attempt in range(2):
            try:
                with self._open(request, timeout=self.timeout) as response:
                    raw = response.read()
                    if not expect_json:
                        return raw, dict(response.headers.items())
                    return json.loads(raw.decode("utf-8")) if raw else {}
            except urllib.error.HTTPError as exc:
                try:
                    raw = exc.read(4096)
                finally:
                    exc.close()
                safe = raw.decode("utf-8", errors="replace").replace(self._token, "[REDACTED]")
                last = PlatformError(f"中台 HTTP {exc.code}：{safe[:300]}", exc.code, safe)
                if exc.code < 500 or attempt:
                    raise last from None
            except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError) as exc:
                last = PlatformError(f"中台连接失败：{type(exc).__name__}")
                if attempt:
                    raise last from None
            time.sleep(0.15)
        raise last or PlatformError("中台请求失败")

    def queued(self):
        data = self._request("GET", "/api/agent/production-requests?status=queued")
        return data.get("requests") or []

    def claim(self, request_id):
        return self._request("POST", f"/api/agent/production-requests/{urllib.parse.quote(request_id, safe='')}/claim", {})

    def progress(self, request_id, stage, message, status="running"):
        return self._request("POST", f"/api/agent/production-requests/{urllib.parse.quote(request_id, safe='')}/progress",
                             {"stage": stage, "message": message, "status": status})

    def create_checkpoint(self, request_id, external_id, kind, summary, payload):
        return self._request("POST", f"/api/agent/production-requests/{urllib.parse.quote(request_id, safe='')}/checkpoints",
                             {"external_id": external_id, "kind": kind,
                              "summary": summary, "payload": payload})

    def checkpoints(self, request_id):
        data = self._request("GET", f"/api/agent/production-requests/{urllib.parse.quote(request_id, safe='')}/checkpoints")
        return data.get("checkpoints") or []

    def download_ref(self, request_id, index):
        return self._request("GET", f"/api/agent/production-requests/{urllib.parse.quote(request_id, safe='')}/refs/{int(index)}",
                             expect_json=False)

    def upload_video(self, path, *, company_id, project_id, title, duration, upload_id=None):
        video = Path(path)
        if not video.is_file():
            raise PlatformError(f"待上传成片不存在：{video}")
        upload_id = upload_id or str(uuid.uuid4())
        headers = {
            "Content-Type": "video/mp4",
            "Content-Length": str(video.stat().st_size),
            "X-Company-ID": str(company_id),
            "X-Project-ID": str(project_id),
            "X-Upload-ID": upload_id,
            "X-Upload-Title": urllib.parse.quote(str(title), safe=""),
            "X-Duration-Seconds": f"{float(duration):.3f}",
        }
        result = self._request("POST", "/api/v3/uploads", data=video.read_bytes(), headers=headers)
        version_id = result.get("id")
        if not version_id:
            raise PlatformError("中台上传响应缺少成片 id")
        return version_id

    def complete(self, request_id, version_id, internal_result):
        return self._request("POST", f"/api/agent/production-requests/{urllib.parse.quote(request_id, safe='')}/complete",
                             {"version_id": version_id, "internal_result": internal_result})

    def verdict(self, request_id):
        return self._request("GET", f"/api/agent/production-requests/{urllib.parse.quote(request_id, safe='')}/verdict")
