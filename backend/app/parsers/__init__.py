"""parsers 包：注册表初始化 + 全部内置解析器挂载。

新增格式在此 `register()` 挂载（见《开发.md》6.2 扩展点）。
"""
from __future__ import annotations

from app.parsers.base import (
    DocumentParser,
    ParseError,
    get_parser,
    parse_file,
    register,
    set_fallback,
)
from app.parsers.docx_parser import DocxParser
from app.parsers.excel_parser import ExcelParser
from app.parsers.html_parser import HtmlParser
from app.parsers.markdown_parser import MarkdownParser
from app.parsers.pdf_parser import PdfParser
from app.parsers.text_parser import TextParser

# 纯文本/常见文本类扩展名
_text = TextParser()
register(_text, "txt", "text", "csv", "tsv", "json", "xml", "yaml", "yml", "toml",
         "ini", "conf", "log", "py", "js", "ts", "jsx", "tsx", "java", "c", "cpp",
         "h", "go", "rs", "sh", "bat", "ps1", "sql", "properties")
register(MarkdownParser(), "md", "markdown")
register(PdfParser(), "pdf")
register(DocxParser(), "docx", "doc")
register(ExcelParser(), "xlsx", "xls")
register(HtmlParser(), "html", "htm")

# 兜底：未知扩展名按纯文本尝试（二进制不可解码时抛 ParseError，由调用方跳过）
set_fallback(_text)

__all__ = [
    "DocumentParser", "ParseError", "get_parser", "parse_file", "register",
]
