"""纯文本解析器（txt 及未知扩展名兜底）。"""
from __future__ import annotations

from pathlib import Path

from app.parsers.base import DocumentParser, ParseError


class TextParser(DocumentParser):
    def parse(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                return path.read_text(encoding="gbk")
            except UnicodeDecodeError as e:
                raise ParseError(f"无法以文本方式读取：{path.name}（疑似二进制文件）") from e
