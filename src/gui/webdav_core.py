# -*- coding: utf-8 -*-
"""
WebDAV 备份/恢复：把 ~/.qimao_gui 下的持久化 JSON 同步到 WebDAV 服务器
（坚果云 / Nextcloud / 自建 WebDAV 等）。

仅依赖 requests，支持：
- test_connection()  连通性与认证校验（PROPFIND）
- backup(files)      上传本地文件到远端目录（PUT）
- restore(files)     从远端下载覆盖本地（GET）
- list_remote()      列出远端已备份文件

所有操作带超时与清晰错误；凭据由调用方传入（URL/用户名/密码/应用密码）。
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from .logutil import logger


class WebDavError(Exception):
    """WebDAV 操作异常。"""


def _join_url(base: str, path: str) -> str:
    return base.rstrip("/") + "/" + path.lstrip("/")


class WebDavClient:
    """轻量 WebDAV 客户端。"""

    def __init__(self, url: str, username: str = "", password: str = "",
                 timeout: int = 20) -> None:
        self.url = (url or "").strip().rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout
        if not self.url:
            raise WebDavError("未填写 WebDAV 地址")
        self._auth = (self.username, self.password) if self.username else None

    def _request(self, method: str, path: str = "", **kwargs) -> requests.Response:
        url = _join_url(self.url, path) if path else self.url
        try:
            resp = requests.request(
                method, url, auth=self._auth, timeout=self.timeout, **kwargs)
            return resp
        except Exception as exc:  # noqa: BLE001
            logger.error(f"WebDAV {method} 请求失败", exc=exc, tag="webdav")
            raise WebDavError(f"请求失败：{exc}") from exc

    def test_connection(self) -> Tuple[bool, str]:
        """用 PROPFIND 校验连通性与认证。"""
        try:
            resp = self._request(
                "PROPFIND", headers={"Depth": "0", "Content-Type": "application/xml"})
            if resp.status_code in (200, 207):
                return True, "连接成功"
            if resp.status_code in (401, 403):
                return False, f"认证失败（HTTP {resp.status_code}），请检查用户名/应用密码"
            return False, f"服务器返回 HTTP {resp.status_code}"
        except WebDavError as exc:
            return False, str(exc)

    def ensure_dir(self, remote_dir: str) -> None:
        """确保远端目录存在（MKCOL，已存在则忽略）。"""
        if not remote_dir:
            return
        segs = [s for s in remote_dir.split("/") if s]
        cur = ""
        for seg in segs:
            cur = f"{cur}/{seg}" if cur else seg
            resp = self._request("MKCOL", cur)
            if resp.status_code not in (201, 405, 409):
                # 405/409 通常表示已存在
                logger.warning(f"WebDAV MKCOL {cur} 返回 {resp.status_code}", tag="webdav")

    def list_remote(self, remote_dir: str = "") -> List[str]:
        """列出远端目录中的文件名。"""
        body = '<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:getetag/></d:prop></d:propfind>'
        resp = self._request(
            "PROPFIND", remote_dir,
            headers={"Depth": "1", "Content-Type": "application/xml"},
            data=body.encode("utf-8"))
        if resp.status_code not in (200, 207):
            raise WebDavError(f"列目录失败：HTTP {resp.status_code}")
        import re
        # 从 href 提取文件名
        names: List[str] = []
        for href in re.findall(r"<[dD]?:?href>(.*?)</[dD]?:?href>", resp.text):
            name = href.rstrip("/").split("/")[-1]
            try:
                from urllib.parse import unquote
                name = unquote(name)
            except Exception:  # noqa: BLE001
                pass
            if name:
                names.append(name)
        return names

    def backup(self, local_files: List[str], remote_dir: str = "qimao_backup",
               progress_cb: Optional[Callable[[int, int, str], None]] = None) -> Dict[str, Any]:
        """上传本地文件到远端。返回成功/失败统计。"""
        self.ensure_dir(remote_dir)
        ok, fail = 0, 0
        errors: List[str] = []
        total = len(local_files)
        for i, path in enumerate(local_files, 1):
            name = os.path.basename(path)
            if not os.path.isfile(path):
                fail += 1
                errors.append(f"本地不存在：{name}")
                continue
            try:
                with open(path, "rb") as f:
                    data = f.read()
                resp = self._request(
                    "PUT", _join_url(remote_dir, name), data=data,
                    headers={"Content-Type": "application/json"})
                if resp.status_code in (200, 201, 204):
                    ok += 1
                else:
                    fail += 1
                    errors.append(f"{name}：HTTP {resp.status_code}")
            except Exception as exc:  # noqa: BLE001
                fail += 1
                errors.append(f"{name}：{exc}")
                logger.error(f"WebDAV 上传失败 {name}", exc=exc, tag="webdav")
            if progress_cb:
                progress_cb(i, total, name)
        return {"success": fail == 0, "ok": ok, "fail": fail, "errors": errors}

    def restore(self, local_files: List[str], remote_dir: str = "qimao_backup",
                progress_cb: Optional[Callable[[int, int, str], None]] = None,
                overwrite: bool = True) -> Dict[str, Any]:
        """从远端下载覆盖本地。返回成功/失败统计。"""
        ok, fail, skipped = 0, 0, 0
        errors: List[str] = []
        total = len(local_files)
        for i, path in enumerate(local_files, 1):
            name = os.path.basename(path)
            if os.path.exists(path) and not overwrite:
                skipped += 1
                continue
            try:
                resp = self._request("GET", _join_url(remote_dir, name))
                if resp.status_code == 200:
                    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                    with open(path, "wb") as f:
                        f.write(resp.content)
                    ok += 1
                elif resp.status_code == 404:
                    skipped += 1
                    errors.append(f"远端不存在：{name}")
                else:
                    fail += 1
                    errors.append(f"{name}：HTTP {resp.status_code}")
            except Exception as exc:  # noqa: BLE001
                fail += 1
                errors.append(f"{name}：{exc}")
                logger.error(f"WebDAV 下载失败 {name}", exc=exc, tag="webdav")
            if progress_cb:
                progress_cb(i, total, name)
        return {"success": fail == 0, "ok": ok, "fail": fail,
                "skipped": skipped, "errors": errors}
