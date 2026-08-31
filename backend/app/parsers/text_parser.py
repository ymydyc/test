"""纯文本解析器（txt 及未知扩展名兜底）。"""
from __future__ import annotations

from pathlib import Path

from app.parsers.base import DocumentParser, ParseError


class TextParser(DocumentParser):
    def parse_bytes(self, data: bytes, name: str = "") -> str:
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            try:
                return data.decode("gbk")
            except UnicodeDecodeError as e:
                raise ParseError(f"无法以文本方式读取：{name or '文件'}（疑似二进制文件）") from e

    def parse(self, path: Path) -> str:
        return self.parse_bytes(path.read_bytes(), path.name)
