"""PDF 解析器：pypdf 直接提取文本（可复制文本的 PDF 优先直取，不 OCR）。"""
from __future__ import annotations

from pathlib import Path

from app.parsers.base import DocumentParser, ParseError


class PdfParser(DocumentParser):
    def parse(self, path: Path) -> str:
        try:
            from pypdf import PdfReader
        except ImportError as e:  # pragma: no cover
            raise ParseError("缺少依赖 pypdf，无法解析 PDF") from e
        try:
            reader = PdfReader(str(path))
            pages = []
            for page in reader.pages:
                text = (page.extract_text() or "").strip()
                if text:
                    pages.append(text)
            return "\n\n".join(pages)
        except Exception as e:
            raise ParseError(f"PDF 解析失败（{path.name}）：{e}") from e
