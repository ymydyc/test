"""第二大脑 配置中心。

所有环境相关配置统一从环境变量 / `.env` 读取，不硬编码。
"""
from __future__ import annotations

import os
from pathlib import Path

# 项目根目录（仓库根，即 backend/ 所在目录）
# 本文件位于 <root>/backend/app/core/config.py
PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _load_dotenv(path: Path) -> None:
    """极简 .env 加载器：将 KEY=VALUE 写入 os.environ（已存在则不覆盖）。"""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(PROJECT_ROOT / ".env")


def _get(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _get_bool(key: str, default: bool) -> bool:
    val = os.environ.get(key)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


class AppConfig:
    """应用配置（阶段一侧重目录与 MySQL，其余为后续阶段预留）。"""

    # 后端服务
    host: str = _get("BACKEND_HOST", "0.0.0.0")
    port: int = int(_get("BACKEND_PORT", "8000"))
    debug: bool = _get_bool("BACKEND_DEBUG", True)

    # 数据目录（相对项目根）
    project_root: Path = PROJECT_ROOT
    data_dir: Path = PROJECT_ROOT / _get("DATA_DIR", "data")
    raw_dir: Path = PROJECT_ROOT / _get("RAW_DIR", "data/raw")          # 原始文件区
    kb_dir: Path = PROJECT_ROOT / _get("KB_DIR", "data/kb")             # 知识库笔记
    input_dir: Path = PROJECT_ROOT / _get("INPUT_DIR", "data/input")    # AI 生成 md 默认目录

    # MySQL（RAG 库，启动自动建库建表）
    mysql_host: str = _get("MYSQL_HOST", "127.0.0.1")
    mysql_port: int = int(_get("MYSQL_PORT", "3306"))
    mysql_user: str = _get("MYSQL_USER", "root")
    mysql_password: str = _get("MYSQL_PASSWORD", "root")
    mysql_db: str = _get("MYSQL_DB", "RAG")

    # DashScope（阶段三/四启用，预留）
    dashscope_api_key: str = _get("DASHSCOPE_API_KEY", "")
    llm_model: str = _get("DASHSCOPE_MODEL", "qwen3.7-flash")
    embedding_model: str = _get("DASHSCOPE_EMBEDDING", "text-embedding-v4")

    # Neo4j（阶段三接入，预留）
    neo4j_uri: str = _get("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user: str = _get("NEO4J_USER", "neo4j")
    neo4j_password: str = _get("NEO4J_PASSWORD", "brain2026")

    def ensure_dirs(self) -> None:
        """确保运行所需的目录存在。"""
        for d in (self.data_dir, self.raw_dir, self.kb_dir, self.input_dir):
            d.mkdir(parents=True, exist_ok=True)


# 全局单例配置
settings = AppConfig()