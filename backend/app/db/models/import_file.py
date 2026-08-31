"""import_files 原始文件表。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CHAR, DateTime, Index, LargeBinary, String, func
from sqlalchemy.dialects.mysql import LONGBLOB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ImportFile(Base):
    """原始文件表——记录导入"原始文件区"的文件、文件夹、导入标记与**文件内容**（阶段六数据库化）。

    - 阶段六起，原始文件内容改为**存储在 MySQL（`content` LONGBLOB）**，不再落本地磁盘。
    - 文件夹（`is_dir=1`）同样记录为一行，`content` 为 NULL。
    """

    __tablename__ = "import_files"
    __table_args__ = (Index("idx_import_status", "import_status"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="自增主键")
    rel_path: Mapped[str] = mapped_column(String(1024), nullable=False, comment="相对导入区根目录的路径（保留原始文件夹结构）")
    rel_path_hash: Mapped[str] = mapped_column(CHAR(64), unique=True, nullable=False, comment="rel_path 的 SHA256（utf8mb4 索引长度限制，用哈希列做唯一约束）")
    file_name: Mapped[str] = mapped_column(String(512), nullable=False, comment="文件名（含扩展名）")
    ext_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="文件格式类型（pdf/docx/xlsx/txt/md/html 等，文件夹为空串）")
    is_dir: Mapped[int] = mapped_column(default=0, nullable=False, comment="是否文件夹：1=文件夹，0=文件")
    parent_path: Mapped[str | None] = mapped_column(String(1024), nullable=True, comment="上级文件夹相对路径（空为根目录）")
    content_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True, comment="文件内容哈希，用于变更检测")
    import_status: Mapped[int] = mapped_column(default=0, nullable=False, comment="导入标记：0=未写入，1=已写入")
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="文件大小（字节）")
    content: Mapped[bytes | None] = mapped_column(
        LargeBinary().with_variant(LONGBLOB, "mysql"),
        nullable=True,
        comment="原始文件内容（阶段六起直接存 MySQL，文件夹为 NULL）",
    )
    workspace_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="所属工作区 workspaces.id（阶段七：隔离键）")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False, comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False, comment="更新时间")