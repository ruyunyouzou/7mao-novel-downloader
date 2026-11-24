# -*- coding: utf-8 -*-
"""
EPUB 结构校验：下载/导出后检查 EPUB 是否合法。

两层策略：
1) 若系统装有 epubcheck（java -jar），优先用它做权威校验；
2) 否则用纯 Python 做结构校验（ZIP/mimetype/container.xml/OPF/manifest/spine），
   覆盖绝大多数"坏书"情形，无需外部依赖。

返回统一结构：{ "ok": bool, "engine": str, "errors": [...], "warnings": [...] }
"""

from __future__ import annotations

import os
import shutil
import subprocess
import zipfile
from typing import Any, Dict, List, Optional

from .logutil import logger

# epubcheck jar 的可选路径（环境变量或项目 tools 目录）
EPUBCHECK_ENV = "EPUBCHECK_JAR"


def _find_epubcheck() -> Optional[str]:
    jar = os.environ.get(EPUBCHECK_ENV, "")
    if jar and os.path.isfile(jar):
        return jar
    from .resource_path import resource_path
    local = resource_path("tools", "epubcheck.jar")
    return local if os.path.isfile(local) else None


def _epubcheck_validate(path: str, jar: str) -> Dict[str, Any]:
    java = shutil.which("java")
    if not java:
        return {"ok": False, "engine": "none", "errors": ["未找到 Java"], "warnings": []}
    try:
        proc = subprocess.run(
            [java, "-jar", jar, path], capture_output=True, text=True, timeout=60)
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        errors = [l.strip() for l in out.splitlines()
                  if "ERROR(" in l.upper() or "FATAL" in l.upper()]
        warnings = [l.strip() for l in out.splitlines() if "WARNING(" in l.upper()]
        return {"ok": proc.returncode == 0 and not errors, "engine": "epubcheck",
                "errors": errors, "warnings": warnings}
    except Exception as exc:  # noqa: BLE001
        logger.error("epubcheck 执行失败", exc=exc, tag="epubcheck")
        return {"ok": False, "engine": "epubcheck",
                "errors": [f"执行失败：{exc}"], "warnings": []}


def _python_validate(path: str) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []
    try:
        if not os.path.isfile(path):
            return {"ok": False, "engine": "python", "errors": ["文件不存在"], "warnings": warnings}
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            # 1) mimetype 必须是首个条目且不压缩
            if not names:
                errors.append("EPUB 为空")
            else:
                if names[0] != "mimetype":
                    errors.append("mimetype 不是 ZIP 首个条目")
                else:
                    info = z.getinfo("mimetype")
                    if info.compress_type != zipfile.ZIP_STORED:
                        errors.append("mimetype 不应被压缩")
                    if z.read("mimetype").strip() != b"application/epub+zip":
                        errors.append("mimetype 内容不正确")
            if "META-INF/container.xml" not in names:
                errors.append("缺少 META-INF/container.xml")
            else:
                import re
                container = z.read("META-INF/container.xml").decode("utf-8", "replace")
                m = re.search(r'full-path="([^"]+\.opf)"', container)
                if not m:
                    errors.append("container.xml 未指向 OPF")
                else:
                    opf_path = m.group(1)
                    if opf_path not in names:
                        errors.append(f"OPF 文件不存在：{opf_path}")
                    else:
                        opf = z.read(opf_path).decode("utf-8", "replace")
                        base = os.path.dirname(opf_path)
                        # manifest 中引用文件是否都存在
                        for href in re.findall(r'\bhref="([^"]+)"', opf):
                            target = href if not base else f"{base}/{href}"
                            target = os.path.normpath(target).replace("\\", "/")
                            if target not in names and not href.startswith(("http:", "https:")):
                                warnings.append(f"manifest 引用缺失：{href}")
                        if "<spine" not in opf:
                            errors.append("OPF 缺少 spine")
                        if "<manifest" not in opf:
                            errors.append("OPF 缺少 manifest")
            if not any(n.endswith(".xhtml") or n.endswith(".html") for n in names):
                warnings.append("未发现章节 XHTML 文件")
        return {"ok": not errors, "engine": "python", "errors": errors, "warnings": warnings}
    except zipfile.BadZipFile:
        return {"ok": False, "engine": "python", "errors": ["不是合法的 ZIP/EPUB 文件"], "warnings": warnings}
    except Exception as exc:  # noqa: BLE001
        logger.error("EPUB 校验异常", exc=exc, tag="epubcheck")
        return {"ok": False, "engine": "python", "errors": [f"校验异常：{exc}"], "warnings": warnings}


def validate_epub(path: str) -> Dict[str, Any]:
    """校验 EPUB，自动选择引擎。"""
    jar = _find_epubcheck()
    if jar and shutil.which("java"):
        return _epubcheck_validate(path, jar)
    return _python_validate(path)
