"""网页剪藏服务（FR-12）：URL → 原始文件区（Markdown）。

- 用 trafilatura 抓取网页并提取正文（优先），失败时降级 requests 直连。
- 提取结果为 Markdown，保存到 `raw/{target_dir}/{safe_name}.md`，并登记 `import_files` 记录（未写入状态）。
- 目标目录与文件名均做路径安全校验（防穿越）。
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import ImportFile
from app.services.import_service import ImportService, PathSafetyError

log = get_logger("services.clip")

try:
    import trafilatura
    _TRAFILATURA_OK = True
except Exception:  # pragma: no cover  环境缺 trafilatura 时降级纯 requests
    _TRAFILATURA_OK = False
    trafilatura = None


class ClipError(ValueError):
    """网页抓取/提取失败。"""


class ClipService:
    def __init__(self, raw_dir: Path | str | None = None) -> None:
        self.raw_dir = Path(raw_dir or settings.raw_dir)

    # ---------- 抓取与提取 ----------
    def _fetch(self, url: str) -> str:
        """抓取网页 HTML。优先 trafilatura，失败降级 requests（显式绕代理 + 关闭连接复用）。"""
        if _TRAFILATURA_OK:
            try:
                html = trafilatura.fetch_url(url)
                if html:
                    return html
            except Exception as e:
                log.warning("trafilatura 抓取失败，降级 requests：%s", e)
        import requests
        resp = requests.get(
            url, timeout=20,
            headers={"User-Agent": "Mozilla/5.0 (SecondBrain ClipBot)", "Connection": "close"},
            proxies={"http": None, "https": None},
        )
        resp.raise_for_status()
        return resp.text

    def _extract(self, html: str) -> dict:
        """提取正文 Markdown 与标题。"""
        if _TRAFILATURA_OK:
            try:
                result = trafilatura.extract(html, output_format="markdown", with_metadata=True)
                if isinstance(result, dict):
                    text = (result.get("text") or "").strip()
                    title = (result.get("title") or "").strip()
                    if text:
                        return {"text": text, "title": title}
                elif isinstance(result, str) and result.strip():
                    return {"text": result.strip(), "title": ""}
            except Exception as e:
                log.warning("trafilatura 提取失败，降级 html2text：%s", e)
        from html2text import HTML2Text
        h = HTML2Text()
        h.ignore_links = False
        h.body_width = 0
        return {"text": h.handle(html).strip(), "title": ""}

    # ---------- 命名与路径 ----------
    @staticmethod
    def _safe_stem(host: str, path: str) -> str:
        """由 URL 生成安全文件名（去特殊字符，限制长度）。"""
        raw = f"{host}_{path}"
        slug = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", unicodedata.normalize("NFKC", raw)).strip("._")
        return (slug or "clip")[:120]

    def _safe_target(self, target_dir: str) -> Path:
        svc = ImportService(self.raw_dir)
        return svc._safe_rel(target_dir)

    def _unique_path(self, rel_base: str, stem: str) -> Path:
        """避免与已存在文件重名：a.md / a-2.md / a-3.md ..."""
        n = 1
        while True:
            name = f"{stem}.md" if n == 1 else f"{stem}-{n}.md"
            rel = f"{rel_base}/{name}" if rel_base else name
            if not (self.raw_dir / rel).exists():
                return Path(rel)
            n += 1

    # ---------- 对外入口 ----------
    def clip_url(self, db: Session, url: str, target_dir: str = "") -> dict:
        """剪藏网页为 Markdown 并落入导入区。"""
        if not url or not url.startswith(("http://", "https://")):
            raise ClipError("URL 非法，仅支持 http/https")

        html = self._fetch(url)
        extracted = self._extract(html)
        text = extracted.get("text", "").strip()
        if not text:
            raise ClipError("未能从网页中提取到正文内容")

        try:
            parsed = urlparse(url)
        except Exception as e:
            raise ClipError(f"URL 解析失败：{e}") from e
        host = (parsed.netloc or "web").replace("www.", "")
        stem = self._safe_stem(host, parsed.path)
        target = self._safe_target(target_dir)

        svc = ImportService(self.raw_dir)
        rel = self._unique_path(target.as_posix(), stem)
        dest = (self.raw_dir / rel).resolve()
        root = self.raw_dir.resolve()
        if dest != root and root not in dest.parents:
            raise PathSafetyError("剪藏路径越界")
        dest.parent.mkdir(parents=True, exist_ok=True)

        title = extracted.get("title") or stem
        content_md = f"# {title}\n\n> 来源：{url}\n\n{text}\n"
        dest.write_text(content_md, encoding="utf-8")

        # 登记导入记录（未写入状态，与上传一致）
        parent = dest.parent.relative_to(self.raw_dir).as_posix()
        parent = "" if parent == "." else parent
        content_hash = ImportService._hash(dest)
        rec = db.query(ImportFile).filter(ImportFile.rel_path == rel.as_posix()).first()
        if rec is None:
            db.add(ImportFile(
                rel_path=rel.as_posix(), rel_path_hash=ImportService._rel_hash(rel.as_posix()),
                file_name=dest.name, ext_type="md", is_dir=0, parent_path=parent,
                content_hash=content_hash, import_status=0, file_size=dest.stat().st_size,
            ))
        else:
            rec.content_hash = content_hash
            rec.file_size = dest.stat().st_size
            rec.import_status = 0
        db.commit()

        log.info("网页剪藏完成：%s -> %s", url, rel.as_posix())
        return {"path": rel.as_posix(), "name": dest.name, "title": title, "source_url": url}
