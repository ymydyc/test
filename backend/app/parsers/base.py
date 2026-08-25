"""文档解析器抽象接口 + 注册表。

设计约定（对齐《开发.md》第 6 章扩展点）：
- 新增文档格式：实现 `DocumentParser` 并 `register()` 挂载，不改动其他层。
- 解析失败抛 `ParseError`，由调用方（kb_service）跳过该文件并给出原因，不中断批量写入。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class ParseError(Exception):
    """文档解析失败（跳过并提示原因）。"""


class DocumentParser(ABC):
    """文档解析器接口：把原始文件内容转为 Markdown 文本。"""

    @abstractmethod
    def parse(self, path: Path) -> str:
        """读取文件并返回 Markdown 文本。"""


# 全局注册表：ext（不含点，小写）→ 解析器实例
_REGISTRY: dict[str, DocumentParser] = {}

# 兜底文本解析器（未知扩展名时按纯文本尝试读取）
_FALLBACK: DocumentParser | None = None


def register(parser: DocumentParser, *exts: str) -> None:
    """按扩展名注册解析器（去点、小写）。"""
    for ext in exts:
        _REGISTRY[ext.lower()] = parser


def set_fallback(parser: DocumentParser) -> None:
    """设置兜底解析器（未知扩展名时使用，通常为纯文本读取）。"""
    global _FALLBACK
    _FALLBACK = parser


def get_parser(ext: str) -> DocumentParser | None:
    return _REGISTRY.get(ext.lower().lstrip("."))


def parse_file(path: Path) -> str:
    """按扩展名将文件解析为 Markdown。

    - 已注册格式走对应解析器；
    - 未知格式走兜底纯文本读取（二进制不可解码时抛 `ParseError`）。
    """
    ext = path.suffix.lower().lstrip(".")
    parser = get_parser(ext)
    if parser is None:
        parser = _FALLBACK
    if parser is None:
        raise ParseError(f"不支持解析的文件类型：.{ext}")
    return parser.parse(path)
