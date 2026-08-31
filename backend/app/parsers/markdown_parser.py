"""Markdown 解析器：md/markdown 原样读取（本身即 Markdown）。"""
from __future__ import annotations

from pathlib import Path

from app.parsers.base import DocumentParser, ParseError


class MarkdownParser(DocumentParser):
    def parse_bytes(self, data: bytes, name: str = "") -> str:
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as e:
            raise ParseError(f"无法读取 Markdown 文件：{name or '文件'}") from e

    def parse(self, path: Path) -> str:
        return self.parse_bytes(path.read_bytes(), path.name)
