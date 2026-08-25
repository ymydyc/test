"""导入区业务服务：原始文件区（`./raw/`）管理与导入标记。

核心规则（对齐《需求.md》FR-01 / FR-05与《数据库设计.md》）：
- 导入原样保存、保留文件夹结构；
- 文件级导入标记落库（`import_files.import_status`）；
- 文件夹级标记**不落库**，按需实时计算（全部子孙文件已导入则为"已导入"）；
- 每次列目录时对磁盘与 DB 做幂等对齐（增量写库，不重建）。
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path, PurePosixPath
from typing import Optional

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import ImportFile, KbNote

log = get_logger("services.import")


class PathSafetyError(ValueError):
    """路径校验失败（穿越/越界）。"""


class ImportService:
    def __init__(self, raw_dir: Path | str) -> None:
        self.raw_dir = Path(raw_dir)

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
    def _hash(path: Path) -> str:
        """SHA256 内容哈希（字段为 CHAR(64)）。"""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _ext(name: str) -> str:
        return Path(name).suffix.lower().lstrip(".")

    @staticmethod
    def _rel_hash(rel: str) -> str:
        """rel_path 的 SHA256（供唯一索引，规避 utf8mb4 长索引限制）。"""
        return hashlib.sha256(rel.encode("utf-8")).hexdigest()

    def _dir_import_status(self, db: Session, abs_dir: Path) -> int:
        """文件夹级"已导入"标记：全部子孙文件均已导入(1)，且至少有一个文件，才为 1。"""
        files = [f for f in abs_dir.rglob("*") if f.is_file()]
        if not files:
            return 0
        for f in files:
            rel = f.relative_to(self.raw_dir).as_posix()
            rec = db.query(ImportFile).filter(ImportFile.rel_path == rel).first()
            if rec is None or rec.import_status != 1:
                return 0
        return 1

    # ---------- 磁盘 ⇄ DB 对齐 ----------
    def sync_from_fs(self, db: Session) -> None:
        """将磁盘现有文件幂等对齐到 DB（存在则更新大小/哈希，缺失则新建）。"""
        if not self.raw_dir.exists():
            self.raw_dir.mkdir(parents=True, exist_ok=True)
            return
        for f in self.raw_dir.rglob("*"):
            if not f.is_file():
                continue
            rel = f.relative_to(self.raw_dir).as_posix()
            parent = Path(rel).parent.as_posix() if Path(rel).parent != PurePosixPath(".") else ""
            size = f.stat().st_size
            content_hash = self._hash(f)
            rec = db.query(ImportFile).filter(ImportFile.rel_path == rel).first()
            if rec is None:
                db.add(ImportFile(
                    rel_path=rel, rel_path_hash=self._rel_hash(rel),
                    file_name=f.name, ext_type=self._ext(f.name),
                    is_dir=0, parent_path=parent, content_hash=content_hash,
                    import_status=0, file_size=size,
                ))
            else:
                # 内容变更时仅刷新哈希与大小；导入标记由写入/编辑流程管理（阶段二联动）
                changed = (rec.content_hash != content_hash) or (rec.file_size != size)
                rec.content_hash = content_hash
                rec.file_size = size
                if changed:
                    rec.updated_at = None  # 交给 onupdate，DB 时间戳
        db.commit()

    # ---------- 目录树 ----------
    def build_tree(self, db: Session) -> dict:
        """构建导入区文件树（根为虚拟节点，含导入标记）。"""
        self.sync_from_fs(db)

        def node_for(path: Path):
            if path.is_dir():
                return {
                    "name": path.name or "/",
                    "path": path.relative_to(self.raw_dir).as_posix() if path != self.raw_dir else "",
                    "type": "dir",
                    "is_dir": True,
                    "import_status": self._dir_import_status(db, path),
                    "children": sorted(
                        (node_for(c) for c in path.iterdir()),
                        key=lambda n: (n["type"] != "dir", n["name"].lower()),
                    ),
                }
            rel = path.relative_to(self.raw_dir).as_posix()
            rec = db.query(ImportFile).filter(ImportFile.rel_path == rel).first()
            note_id = None
            if rec is not None and rec.import_status == 1:
                note = db.query(KbNote).filter(KbNote.origin_import_id == rec.id).first()
                note_id = note.id if note else None
            return {
                "name": path.name,
                "path": rel,
                "type": "file",
                "is_dir": False,
                "ext_type": self._ext(path.name),
                "file_size": path.stat().st_size,
                "import_status": rec.import_status if rec else 0,
                "note_id": note_id,
            }

        return node_for(self.raw_dir)

    # ---------- 文件夹管理 ----------
    def create_folder(self, db: Session, rel: str, name: str) -> dict:
        """>在指定目录下新建文件夹。"""
        if not name or "/" in name or "\\" in name or name in (".", ".."):
            raise PathSafetyError("文件夹名称非法")
        parent = self._abs(rel)
        if not parent.is_dir():
            raise FileNotFoundError(f"目标目录不存在：{rel}")
        new_abs = (parent / name)
        if new_abs.exists():
            raise FileExistsError(f"同名文件/文件夹已存在：{name}")
        new_abs.mkdir()
        db.commit()
        log.info("新建文件夹：%s", new_abs.relative_to(self.raw_dir).as_posix())
        return {"path": new_abs.relative_to(self.raw_dir).as_posix(), "type": "dir"}

    def _migrate_records(
        self, db: Session, old_rel: str, new_rel: str, new_name: Optional[str] = None
    ) -> None:
        """迁移 DB 记录：自身 + 所有子记录（rel / parent_path 前缀迁移）。"""
        prefix = old_rel + "/"
        for rec in db.query(ImportFile).filter(
                (ImportFile.rel_path == old_rel) | (ImportFile.rel_path.startswith(prefix))
        ).all():
            if rec.rel_path == old_rel:
                rec.rel_path = new_rel
                if new_name is not None:
                    rec.file_name = new_name
            else:
                rec.rel_path = new_rel + rec.rel_path[len(old_rel):]
            rec.rel_path_hash = self._rel_hash(rec.rel_path)
            rec.parent_path = Path(rec.rel_path).parent.as_posix() if Path(rec.rel_path).parent != PurePosixPath(".") else ""
        db.commit()

    def rename(self, db: Session, rel: str, new_name: str) -> dict:
        """重命名原文件区中的文件/文件夹（结构内）。"""
        if not new_name or "/" in new_name or "\\" in new_name or new_name in (".", ".."):
            raise PathSafetyError("新名称非法")
        src = self._abs(rel)
        if not src.exists():
            raise FileNotFoundError(f"目标不存在：{rel}")
        dst = src.with_name(new_name)
        if dst.exists():
            raise FileExistsError(f"同名文件/文件夹已存在：{new_name}")
        src.rename(dst)

        old_rel = src.relative_to(self.raw_dir).as_posix()
        new_rel = dst.relative_to(self.raw_dir).as_posix()
        self._migrate_records(db, old_rel, new_rel, new_name=dst.name)
        log.info("重命名：%s -> %s", old_rel, new_rel)
        return {"path": new_rel}

    def move(self, db: Session, rel: str, target_dir: str) -> dict:
        """将文件/文件夹移动到目标父目录下（同一原始文件区内）。"""
        src = self._abs(rel)
        if not src.exists():
            raise FileNotFoundError(f"目标不存在：{rel}")
        if src.resolve() == self.raw_dir.resolve():
            raise PathSafetyError("不允许移动原始文件区根目录")

        target = self._abs(target_dir)
        if not target.is_dir():
            raise FileNotFoundError(f"目标目录不存在：{target_dir}")
        if target.resolve() == src.resolve():
            raise PathSafetyError("目标目录不能是源自身")
        if src.is_dir() and src.resolve() in target.resolve().parents:
            raise PathSafetyError("不能移动到自身的子目录内")

        new_abs = target / src.name
        if new_abs.resolve() == src.resolve():
            return {"path": src.relative_to(self.raw_dir).as_posix()}
        if new_abs.exists():
            raise FileExistsError(f"目标目录下已存在同名项：{src.name}")
        src.rename(new_abs)

        old_rel = src.relative_to(self.raw_dir).as_posix()
        new_rel = new_abs.relative_to(self.raw_dir).as_posix()
        self._migrate_records(db, old_rel, new_rel, new_name=new_abs.name)
        log.info("移动：%s -> %s", old_rel, new_rel)
        return {"path": new_rel}

    def delete(self, db: Session, rel: str) -> dict:
        """删除原文件区中的文件/文件夹（递归），并清空对应 DB 记录。"""
        target = self._abs(rel)
        if not target.exists():
            raise FileNotFoundError(f"目标不存在：{rel}")
        if target.resolve() == self.raw_dir.resolve():
            raise PathSafetyError("不允许删除原始文件区根目录")
        rel_posix = target.relative_to(self.raw_dir).as_posix()
        prefix = rel_posix + "/"
        records = db.query(ImportFile).filter(
            (ImportFile.rel_path == rel_posix) | (ImportFile.rel_path.startswith(prefix))
        ).all()
        for rec in records:
            db.delete(rec)
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        db.commit()
        log.info("删除：%s", rel_posix)
        return {"path": rel_posix, "deleted": True}

    # ---------- 上传 ----------
    def save_upload(
        self,
        db: Session,
        rel_paths: list[str],
        contents: list[bytes],
        target_dir: str = "",
    ) -> list[dict]:
        """保存多个上传文件（按各自相对路径，保留文件夹结构）。

        rel_paths[i] 为相对 raw 根的完整路径（含子目录）；target_dir 追加在最前作为公共前缀。
        同名已存在则直接覆盖文件（不重建记录语义）。
        """
        results: list[dict] = []
        base = self._safe_rel(target_dir)
        for rel, data in zip(rel_paths, contents):
            safe = self._safe_rel(rel)
            full_rel = (base / safe).as_posix() if base.parts else safe.as_posix()
            dest = self._abs(full_rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                f.write(data)

            parent = dest.parent.relative_to(self.raw_dir).as_posix()
            parent = "" if parent == "." else parent
            content_hash = self._hash(dest)
            rec = db.query(ImportFile).filter(ImportFile.rel_path == full_rel).first()
            if rec is None:
                db.add(ImportFile(
                    rel_path=full_rel, rel_path_hash=self._rel_hash(full_rel),
                    file_name=dest.name, ext_type=self._ext(dest.name),
                    is_dir=0, parent_path=parent, content_hash=content_hash,
                    import_status=0, file_size=dest.stat().st_size,
                ))
            else:
                rec.content_hash = content_hash      # 内容变更
                rec.file_size = dest.stat().st_size
                rec.import_status = 0                # 覆盖后视为未写入，需重新写库
            results.append({"path": full_rel, "name": dest.name})
        db.commit()
        log.info("上传完成，共 %s 个文件", len(results))
        return results