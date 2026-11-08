"""GitHub Contents API 客户端（授权文件、心跳、指令、发布物均走这里）。

桌面端只读取「授权 / 指令 / 发布清单」，并写入「心跳 / 指令结果」。
所有安全敏感内容在本地做签名校验，仓库仅作为传输与存储通道。
"""
from __future__ import annotations

import base64
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .config import default_headers
from .util import LOG

NETWORK_TIMEOUT = 15


class GitHubError(RuntimeError):
    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


class OfflineError(GitHubError):
    pass


class GitHubClient:
    def __init__(
        self,
        repo: str,
        token: str = "",
        branch: str = "main",
        api_base: str = "https://api.github.com",
        timeout: int = NETWORK_TIMEOUT,
    ) -> None:
        self.repo = (repo or "").strip().strip("/")
        self.token = token or ""
        self.branch = branch or "main"
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout
        self._sha_cache: dict[str, str] = {}

    # ------------------------------------------------------------------ HTTP
    def _contents_url(self, path: str) -> str:
        quoted = urllib.parse.quote(path.strip("/"), safe="/")
        return f"{self.api_base}/repos/{self.repo}/contents/{quoted}"

    def _open(self, request: urllib.request.Request) -> tuple[int, dict[str, str], bytes]:
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            if exc.code == 404:
                return 404, dict(exc.headers or {}), body.encode()
            message = body
            try:
                message = json.loads(body).get("message", body)
            except Exception:
                pass
            if exc.code in (401, 403, 429):
                message = f"GitHub 拒绝访问({exc.code})：{message}（请检查令牌权限/限流）"
            raise GitHubError(message, exc.code) from exc
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as exc:
            raise OfflineError(f"网络不可用：{exc}") from exc

    def _request(
        self,
        method: str,
        url: str,
        body: dict | None = None,
        raw: bool = False,
    ) -> bytes:
        headers = default_headers(self.token)
        if raw:
            headers["Accept"] = "application/vnd.github.raw"
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        status, _, payload = self._open(request)
        if status == 404:
            raise GitHubError("资源不存在", 404)
        return payload

    # ------------------------------------------------------------------ 读
    def get_file(self, path: str, ref: str | None = None) -> dict[str, Any] | None:
        url = self._contents_url(path)
        if ref:
            url += "?ref=" + urllib.parse.quote(ref)
        request = urllib.request.Request(url, headers=default_headers(self.token), method="GET")
        status, _, payload = self._open(request)
        if status == 404:
            return None
        try:
            meta = json.loads(payload.decode("utf-8"))
        except Exception as exc:
            raise GitHubError(f"解析仓库响应失败：{exc}") from exc
        if isinstance(meta, list):
            raise GitHubError("目标路径是目录，不是文件")
        content = b""
        if meta.get("encoding") == "base64" and meta.get("content"):
            content = base64.b64decode(meta["content"])
        elif meta.get("download_url"):
            content = self._request("GET", meta["download_url"], raw=True)
        sha = meta.get("sha", "")
        if sha:
            self._sha_cache[path] = sha
        return {"path": path, "sha": sha, "content": content, "meta": meta}

    def get_bytes(self, path: str) -> bytes | None:
        item = self.get_file(path)
        return item["content"] if item else None

    def get_text(self, path: str) -> str | None:
        data = self.get_bytes(path)
        return data.decode("utf-8") if data is not None else None

    def get_json(self, path: str) -> Any:
        text = self.get_text(path)
        if text is None:
            return None
        try:
            return json.loads(text)
        except Exception as exc:
            raise GitHubError(f"{path} 不是合法 JSON：{exc}") from exc

    def list_dir(self, path: str) -> list[dict[str, Any]]:
        url = self._contents_url(path)
        request = urllib.request.Request(url, headers=default_headers(self.token), method="GET")
        status, _, payload = self._open(request)
        if status == 404:
            return []
        try:
            data = json.loads(payload.decode("utf-8"))
        except Exception as exc:
            raise GitHubError(f"解析目录失败：{exc}") from exc
        return data if isinstance(data, list) else []

    # ------------------------------------------------------------------ 写
    def put_file(self, path: str, content: bytes | str, message: str, sha: str | None = None) -> dict[str, Any]:
        if isinstance(content, str):
            content = content.encode("utf-8")
        body: dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(content).decode("ascii"),
            "branch": self.branch,
        }
        if sha:
            body["sha"] = sha
        payload = self._request("PUT", self._contents_url(path), body)
        result = json.loads(payload.decode("utf-8")) if payload else {}
        new_sha = (result.get("content") or {}).get("sha")
        if new_sha:
            self._sha_cache[path] = new_sha
        return result

    def put_json(self, path: str, obj: Any, message: str, sha: str | None = None) -> dict[str, Any]:
        text = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)
        return self.put_file(path, text, message, sha=sha)

    def delete_file(self, path: str, message: str, sha: str) -> None:
        body = {"message": message, "sha": sha, "branch": self.branch}
        self._request("DELETE", self._contents_url(path), body)

    def cached_sha(self, path: str) -> str | None:
        return self._sha_cache.get(path)

    # ------------------------------------------------------------------ 工具
    def download_url(self, url: str) -> bytes:
        """下载发布物：支持 Contents API 地址、raw.githubusercontent.com 或直链。"""
        headers = default_headers(self.token)
        if "api.github.com" in url and "/contents/" in url:
            headers["Accept"] = "application/vnd.github.raw"
        request = urllib.request.Request(url, headers=headers, method="GET")
        status, _, payload = self._open(request)
        if status == 404:
            raise GitHubError("下载地址不存在", 404)
        return payload

    def test_connection(self) -> tuple[bool, str]:
        try:
            url = f"{self.api_base}/repos/{self.repo}"
            payload = self._request("GET", url)
            data = json.loads(payload.decode("utf-8"))
            return True, f"已连接 {data.get('full_name', self.repo)}"
        except GitHubError as exc:
            return False, str(exc)

    def ensure_path(self, path: str, message: str = "chore: init ops path") -> None:
        """在目录不存在时创建一个占位文件，确保后续写入可用。"""
        item = self.get_file(path)
        if item is None:
            self.put_file(path, b"", message)


def safe_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)


def log_github_error(context: str, exc: Exception) -> None:
    LOG.warning("%s: %s", context, exc)
