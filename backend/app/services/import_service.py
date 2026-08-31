"""导入区业务服务：原始文件区管理。

**阶段六起，导入区为「纯 MySQL 存储」**——文件字节存 `import_files.content`（LONGBLOB），
文件夹以 `import_files.is_dir=1` 行表示，不再落本地磁盘。

核心规则（对齐《需求.md》FR-01 / FR-05与《数据库设计.md》）：
- 上传原样保存、保留文件夹结构（字节存入 DB，自动补齐父文件夹行）；
- 文件级导入标记落库（`import_files.import_status`）；
- 文件夹级标记**不落库**，按需实时计算（全部子孙文件已导入则为"已导入"）；
- `sync_from_fs` 仅用于**迁移遗留磁盘文件进库**（老版本 MVP 数据），日常不再依赖磁盘。
"""
from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
from typing import Optional

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import ImportFile, KbNote
from app.db.scoping import workspace_scope

log = get_logger("services.import")


class PathSafetyError(ValueError):
    """路径校验失败（穿越/越界）。"""


class ImportService:
    def __init__(self, raw_dir: Path | str, workspace_id: int = 1) -> None:
        self.raw_dir = Path(raw_dir)
        self.workspace_id = workspace_id  # 工作区隔离键（阶段七）

    # ---------- 路径安全 ----------
    def _safe_rel(self, rel: str) -> PurePosixPath:
        """将前端传来的相对路径规整为安全相对路径，拦截穿越/绝对路径。"""
        if not rel:
            return PurePosixPath("")
        stripped = rel.strip()
        if stripped.startswith(("/", "\\")):
            raise PathSafetyError("路径非法：禁止绝对路径")
        p = PurePosixPath(rel.replace("\\", "/"))
        parts = [seg for seg in p.parts if seg not in ("", ".", "/")]
        if any(seg in ("..",) for seg in parts):
            raise PathSafetyError("路径非法：禁止路径穿越（..）")
        if parts and ":" in parts[0]:
            raise PathSafetyError("路径非法：禁止绝对路径")
        return PurePosixPath("/".join(parts))

    def _abs(self, rel: str) -> Path:
        rel_path = self._safe_rel(rel)
        target = (self.raw_dir / rel_path).resolve()
        root = self.raw_dir.resolve()
        if target != root and root not in target.parents:
            raise PathSafetyError("路径越界：不允许访问原始文件区之外")
        return target

    # ---------- 工具 ----------
    @staticmethod
    def _hash_bytes(data: bytes) -> str:
        """字节内容 SHA256 哈希（字段为 CHAR(64)），阶段六从内存计算。"""
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def _hash(path: Path) -> str:
        """本地文件 SHA256（迁移遗留磁盘文件时用）。"""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _ext(name: str) -> str:
        return Path(name).suffix.lower().lstrip(".")

    def _rel_hash(self, rel: str) -> str:
        """rel_path 的 SHA256（供唯一索引，规避 utf8mb4 长索引限制）。

        阶段七起把 `workspace_id` 纳入哈希盐，使**跨工作区同名文件各自有独立唯一哈希**，
        避免 `UNIQUE(rel_path_hash)` 在同名记录上冲突；同工作区内同一路径仍能幂等去重。
        """
        return hashlib.sha256(f"{self.workspace_id}\x00{rel}".encode("utf-8")).hexdigest()

    @staticmethod
    def _parent_of(rel: str) -> str:
        """返回 rel_path 的父目录（根目录为空串）。"""
        p = PurePosixPath(rel)
        return p.parent.as_posix() if str(p.parent) != "." else ""

    @staticmethod
    def _split_file(rel: str) -> tuple[str, str]:
        """拆分 rel_path 为 (父目录, 文件名)。"""
        p = PurePosixPath(rel)
        return ImportService._parent_of(rel), p.name

    def _ensure_folders(self, db: Session, rel: str) -> None:
        """幂等补齐 rel_path 的所有祖先文件夹行（is_dir=1，content=NULL）。"""
        parts = PurePosixPath(rel).parts
        for i in range(1, len(parts)):  # 逐级父文件夹
            folder_rel = "/".join(parts[:i])
            rec = db.query(ImportFile).filter(
                ImportFile.rel_path == folder_rel,
                workspace_scope(ImportFile, self.workspace_id)).first()
            if rec is None:
                parent = self._parent_of(folder_rel)
                db.add(ImportFile(
                    rel_path=folder_rel, rel_path_hash=self._rel_hash(folder_rel),
                    file_name=parts[i - 1], ext_type="", is_dir=1, parent_path=parent,
                    import_status=0, file_size=None, content=None,
                    workspace_id=self.workspace_id,
                ))

    def file_records_under(self, db: Session, rel: str) -> list[ImportFile]:
        """返回 rel 目录（含自身为文件夹时）下的所有文件记录，用于文件夹级标记计算。"""
        if rel:
            prefix = rel + "/"
            files = db.query(ImportFile).filter(
                ImportFile.is_dir == 0,
                ImportFile.rel_path.like(prefix + "%"),
                workspace_scope(ImportFile, self.workspace_id),
            ).all()
        else:
            files = db.query(ImportFile).filter(
                ImportFile.is_dir == 0,
                workspace_scope(ImportFile, self.workspace_id)).all()
        return files

    def _dir_import_status(self, db: Session, rel: str) -> int:
        """文件夹级"已导入"标记：全部子孙文件均已导入(1)，且至少有一个文件，才为 1。"""
        files = self.file_records_under(db, rel)
        if not files:
            return 0
        return 1 if all(f.import_status == 1 for f in files) else 0

    # ---------- 遗留磁盘数据迁移（历史 MVP 数据 → DB） ----------
    def sync_from_fs(self, db: Session) -> None:
        """将磁盘遗留文件迁移进 DB，幂等（内容已存在则跳过）。

        阶段六前导入区文件存本地磁盘；本方法扫描磁盘将文件注册/回填到 import_files(content)。
        日常纯 DB 模式下数据已存库，此方法基本为 no-op，仅用于迁移与容错。
        """
        if not self.raw_dir.exists():
            return
        for f in self.raw_dir.rglob("*"):
            if not f.is_file():
                continue
            rel = f.relative_to(self.raw_dir).as_posix()
            rec = db.query(ImportFile).filter(
                ImportFile.rel_path == rel,
                workspace_scope(ImportFile, self.workspace_id)).first()
            if rec is not None and rec.content is not None:
                continue
            data = f.read_bytes()
            self._ensure_folders(db, rel)
            parent, fname = self._split_file(rel)
            if rec is None:
                db.add(ImportFile(
                    rel_path=rel, rel_path_hash=self._rel_hash(rel),
                    file_name=fname, ext_type=self._ext(fname),
                    is_dir=0, parent_path=parent, content=data,
                    content_hash=self._hash_bytes(data), file_size=len(data), import_status=0,
                    workspace_id=self.workspace_id,
                ))
            else:
                rec.content = data
                rec.content_hash = self._hash_bytes(data)
                rec.file_size = len(data)
        db.commit()

    # ---------- 目录树 ----------
    def build_tree(self, db: Session) -> dict:
        """构建导入区文件树（根为虚拟节点，含导入标记），数据全部来自 import_files。"""
        self.sync_from_fs(db)  # 迁移容错：异步磁盘遗留文件并入库

        rows = db.query(ImportFile).filter(workspace_scope(ImportFile, self.workspace_id)).all()
        by_parent: dict[str, list[ImportFile]] = {}
        for r in rows:
            by_parent.setdefault(r.parent_path, []).append(r)

        def node_for(rec: ImportFile) -> dict:
            if rec.is_dir:
                return {
                    "name": rec.file_name,
                    "path": rec.rel_path,
                    "type": "dir",
                    "is_dir": True,
                    "import_status": self._dir_import_status(db, rec.rel_path),
                    "children": sorted(
                        (node_for(c) for c in sorted(by_parent.get(rec.rel_path, []),
                                                     key=lambda x: (x.file_name or "").lower())),
                        key=lambda n: (n["type"] != "dir", n["name"].lower()),
                    ),
                }
            note_id = None
            if rec.import_status == 1:
                note = db.query(KbNote).filter(
                    KbNote.origin_import_id == rec.id,
                    workspace_scope(KbNote, self.workspace_id)).first()
                note_id = note.id if note else None
            return {
                "name": rec.file_name,
                "path": rec.rel_path,
                "type": "file",
                "is_dir": False,
                "ext_type": rec.ext_type,
                "file_size": rec.file_size,
                "import_status": rec.import_status,
                "note_id": note_id,
            }

        root_children = by_parent.get("") or []
        return {
            "name": "/",
            "path": "",
            "type": "dir",
            "is_dir": True,
            "import_status": self._dir_import_status(db, ""),
            "children": sorted((node_for(r) for r in root_children),
                               key=lambda n: (n["type"] != "dir", n["name"].lower())),
        }

    def get_content(self, db: Session, rel: str) -> bytes | None:
        """读取导入区文件字节（阶段六从 DB 读取）。"""
        rec = db.query(ImportFile).filter(
            ImportFile.rel_path == rel, ImportFile.is_dir == 0,
            workspace_scope(ImportFile, self.workspace_id),
        ).first()
        return rec.content if rec else None

    def ensure_file_record(
        self, db: Session, rel: str, data: bytes, *, import_status: int = 0,
    ) -> ImportFile:
        """幂等写入文件记录（内容字节 + 哈希/大小），自动补齐父文件夹行。返回记录。"""
        self._ensure_folders(db, rel)
        parent, fname = self._split_file(rel)
        rec = db.query(ImportFile).filter(
            ImportFile.rel_path == rel,
            workspace_scope(ImportFile, self.workspace_id)).first()
        if rec is None:
            rec = ImportFile(
                rel_path=rel, rel_path_hash=self._rel_hash(rel),
                file_name=fname, ext_type=self._ext(fname), is_dir=0, parent_path=parent,
                content=data, content_hash=self._hash_bytes(data),
                file_size=len(data), import_status=import_status,
                workspace_id=self.workspace_id,
            )
            db.add(rec)
        else:
            rec.content = data
            rec.content_hash = self._hash_bytes(data)
            rec.file_size = len(data)
            rec.import_status = import_status
        db.flush()
        return rec

    # ---------- 文件夹管理 ----------
    def create_folder(self, db: Session, rel: str, name: str) -> dict:
        """>在指定目录下新建文件夹（DB 行，is_dir=1）。"""
        if not name or "/" in name or "\\" in name or name in (".", ".."):
            raise PathSafetyError("文件夹名称非法")
        # 父目录必须存在（根目录除外；空串/“.”均视为根目录）
        base = self._safe_rel(rel).as_posix()
        if base and base != ".":
            parent_rec = db.query(ImportFile).filter(
                ImportFile.rel_path == base, ImportFile.is_dir == 1,
                workspace_scope(ImportFile, self.workspace_id),
            ).first()
            if parent_rec is None:
                raise FileNotFoundError(f"目标目录不存在：{base}")
        new_rel = (self._safe_rel(rel) / name).as_posix()
        if db.query(ImportFile).filter(
                ImportFile.rel_path == new_rel,
                workspace_scope(ImportFile, self.workspace_id)).first():
            raise FileExistsError(f"同名文件/文件夹已存在：{name}")
        db.add(ImportFile(
            rel_path=new_rel, rel_path_hash=self._rel_hash(new_rel),
            file_name=name, ext_type="", is_dir=1, parent_path=self._parent_of(new_rel),
            import_status=0, file_size=None, content=None, workspace_id=self.workspace_id,
        ))
        db.commit()
        log.info("新建文件夹（DB）：%s", new_rel)
        return {"path": new_rel, "type": "dir"}

    def _migrate_records(
        self, db: Session, old_rel: str, new_rel: str, new_name: Optional[str] = None
    ) -> None:
        """迁移 DB 记录：自身 + 所有子记录（rel / parent_path 前缀迁移）。"""
        prefix = old_rel + "/"
        for rec in db.query(ImportFile).filter(
                workspace_scope(ImportFile, self.workspace_id),
                (ImportFile.rel_path == old_rel) | (ImportFile.rel_path.startswith(prefix))
        ).all():
            if rec.rel_path == old_rel:
                rec.rel_path = new_rel
                if new_name is not None:
                    rec.file_name = new_name
            else:
                rec.rel_path = new_rel + rec.rel_path[len(old_rel):]
            rec.rel_path_hash = self._rel_hash(rec.rel_path)
            rec.parent_path = self._parent_of(rec.rel_path)
        db.commit()

    def rename(self, db: Session, rel: str, new_name: str) -> dict:
        """重命名原文件区中的文件/文件夹（结构内，仅改 DB）。"""
        if not new_name or "/" in new_name or "\\" in new_name or new_name in (".", ".."):
            raise PathSafetyError("新名称非法")
        safe = self._safe_rel(rel)
        rec = db.query(ImportFile).filter(
            ImportFile.rel_path == safe.as_posix(),
            workspace_scope(ImportFile, self.workspace_id)).first()
        if rec is None:
            raise FileNotFoundError(f"目标不存在：{rel}")
        new_rel = (safe.parent / new_name).as_posix()
        if new_rel != safe.as_posix() and db.query(ImportFile).filter(
                ImportFile.rel_path == new_rel,
                workspace_scope(ImportFile, self.workspace_id)).first():
            raise FileExistsError(f"同名文件/文件夹已存在：{new_name}")
        self._migrate_records(db, safe.as_posix(), new_rel, new_name=new_name)
        log.info("重命名（DB）：%s -> %s", safe.as_posix(), new_rel)
        return {"path": new_rel}

    def move(self, db: Session, rel: str, target_dir: str) -> dict:
        """将文件/文件夹移动到目标父目录下（同一原始文件区内，仅改 DB）。"""
        safe = self._safe_rel(rel)
        if not safe.parts:
            raise PathSafetyError("不允许移动原始文件区根目录")
        rec = db.query(ImportFile).filter(
            ImportFile.rel_path == safe.as_posix(),
            workspace_scope(ImportFile, self.workspace_id)).first()
        if rec is None:
            raise FileNotFoundError(f"目标不存在：{rel}")

        target = self._safe_rel(target_dir)
        target_rel = target.as_posix()
        if target_rel:
            target_rec = db.query(ImportFile).filter(
                ImportFile.rel_path == target_rel, ImportFile.is_dir == 1,
                workspace_scope(ImportFile, self.workspace_id),
            ).first()
            if target_rec is None:
                raise FileNotFoundError(f"目标目录不存在：{target_dir}")
        # 不能移动到自身，或自身的子目录内
        if target_rel == safe.as_posix():
            raise PathSafetyError("目标目录不能是源自身")
        if safe.parts and target_rel.startswith(safe.as_posix() + "/"):
            raise PathSafetyError("不能移动到自身的子目录内")

        new_rel = (target / safe.name).as_posix()
        if new_rel != safe.as_posix() and db.query(ImportFile).filter(
                ImportFile.rel_path == new_rel,
                workspace_scope(ImportFile, self.workspace_id)).first():
            raise FileExistsError(f"目标目录下已存在同名项：{safe.name}")
        self._migrate_records(db, safe.as_posix(), new_rel, new_name=safe.name)
        log.info("移动（DB）：%s -> %s", safe.as_posix(), new_rel)
        return {"path": new_rel}

    def delete(self, db: Session, rel: str) -> dict:
        """删除原文件区中的文件/文件夹（递归），并清空对应 DB 记录。"""
        safe = self._safe_rel(rel)
        if not safe.parts:
            raise PathSafetyError("不允许删除原始文件区根目录")
        rel_posix = safe.as_posix()
        prefix = rel_posix + "/"
        records = db.query(ImportFile).filter(
            workspace_scope(ImportFile, self.workspace_id),
            (ImportFile.rel_path == rel_posix) | (ImportFile.rel_path.startswith(prefix))
        ).all()
        if not records:
            raise FileNotFoundError(f"目标不存在：{rel}")
        for rec in records:
            db.delete(rec)
        db.commit()
        log.info("删除（DB）：%s", rel_posix)
        return {"path": rel_posix, "deleted": True}

    # ---------- 上传 ----------
    def save_upload(
        self,
        db: Session,
        rel_paths: list[str],
        contents: list[bytes],
        target_dir: str = "",
    ) -> list[dict]:
        """保存多个上传文件（按各自相对路径，保留文件夹结构，字节存入 DB）。

        rel_paths[i] 为相对 raw 根的完整路径（含子目录）；target_dir 追加在最前作为公共前缀。
        同名已存在则直接覆盖文件（内容字节 + 哈希/大小）。
        """
        results: list[dict] = []
        base = self._safe_rel(target_dir)
        for rel, data in zip(rel_paths, contents):
            safe = self._safe_rel(rel)
            full_rel = (base / safe).as_posix() if base.parts else safe.as_posix()
            self.ensure_file_record(db, full_rel, data, import_status=0)
            results.append({"path": full_rel, "name": PurePosixPath(full_rel).name})
        db.commit()
        log.info("上传完成（入库），共 %s 个文件", len(results))
        return results