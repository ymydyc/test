"""HTML 解析器：html2text 提取正文为 Markdown。"""
from __future__ import annotations

from pathlib import Path

from app.parsers.base import DocumentParser, ParseError


class HtmlParser(DocumentParser):
    def parse(self, path: Path) -> str:
        try:
            import html2text
        except ImportError as e:  # pragma: no cover
            raise ParseError("缺少依赖 html2text，无法解析 HTML") from e
        try:
            h = html2text.HTML2Text()
            h.body_width = 0            # 不自动折行
            h.ignore_images = True      # 阶段二不处理图片
            h.ignore_emphasis = False
            html = path.read_text(encoding="utf-8", errors="replace")
            return h.handle(html).strip()
        except Exception as e:
            raise ParseError(f"HTML 解析失败（{path.name}）：{e}") from e
