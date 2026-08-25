"""知识库业务服务：选择性写入、跳过规则、笔记 CRUD、编辑标记联动、级联清理。

核心规则（对齐《需求.md》FR-02 / FR-11 / FR-01 与《数据库设计.md》）：
- 选择性写入：用户选中导入区文件/文件夹 → 解析为 Markdown → 写入/更新 `./kb/` → 登记导入标记。
- 跳过规则：文件已导入则跳过；文件夹**全部**文件已导入则整体跳过；**部分已导入**则逐个处理。
- 重写语义：按 `origin_import_id` 更新原笔记（非新建），同步重建向量块。
- 分块策略：写库/编辑时重建**父子块**（父块=按标题划分的章节、子块=章节内段落，含 `parent_chunk_id` 关联，见 FR-02）。
- 标记联动：内容区编辑保存后，来源导入文件 `import_status` 置 0（待重写）。
- 级联清理：删除笔记时移除其向量块、独占图谱关系与"孤立实体"（共享实体保留）。
- 解析失败的文件跳过并给出原因，不中断批量写入（NFR-04）。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import DocChunk, GraphEntity, GraphRelation, ImportFile, KbNote
from app.parsers import parse_file
from app.parsers.base import ParseError
from app.parsers.html_parser import HtmlParser
from app.parsers.markdown_parser import MarkdownParser
from app.parsers.text_parser import TextParser
from app.services.import_service import ImportService, PathSafetyError

log = get_logger("services.kb")

# 默认分块参数（阶段三向量化时的语义块粒度）
CHUNK_MAX_CHARS = 800
CHUNK_OVERLAP = 100


def _dashscope_ready() -> bool:
    """DashScope 是否已配置（阶段三向量/图谱启用开关）。"""
    from app.core.config import settings
    return bool(settings.dashscope_api_key)


def chunk_text(
    text: str,
    max_chars: int = CHUNK_MAX_CHARS,
    overlap: int = CHUNK_OVERLAP,
) -> list[dict]:
    """按标题章节划分父块、段落划分子块，返回父子块列表（对齐需求 FR-02 / 数据库设计 doc_chunks）。

    每个元素 dict：{'text','start','end','parent_chunk_id','is_parent'}（start/end 为原文字符区间，end 开区间）。
    - 父块：按 Markdown 标题（#, ##, ###）划分的章节大段；is_parent=True，parent_chunk_id=None。
    - 子块：章节内语义段落（按空行切分，≤max_chars，超长段落按 overlap 硬切）；is_parent=False，parent_chunk_id=父块在返回列表中的下标。
    """
    if not text:
        return []

    # 1. 按标题切分父块区间
    heading_re = re.compile(r"^(#{1,3})\s+", re.MULTILINE)
    starts = [m.start() for m in heading_re.finditer(text)]
    if starts:
        sections: list[dict] = []
        for i, s in enumerate(starts):
            end = starts[i + 1] if i + 1 < len(starts) else len(text)
            sections.append({"start": s, "end": end})
        sections[0]["start"] = 0  # 标题前的引言并入首个父块
    else:
        sections = [{"start": 0, "end": len(text)}]

    chunks: list[dict] = []
    for sec in sections:
        parent_idx = len(chunks)
        chunks.append({
            "text": text[sec["start"]:sec["end"]],
            "start": sec["start"],
            "end": sec["end"],
            "parent_chunk_id": None,
            "is_parent": True,
        })
        chunks.extend(_split_section(text, sec, parent_idx, max_chars, overlap))
    return chunks


def _split_section(
    text: str,
    section: dict,
    parent_idx: int,
    max_chars: int,
    overlap: int,
) -> list[dict]:
    """把父块区间内的文本切分为子块：按空行分段，合并相邻段落至 ≤max_chars，超长段落按 overlap 硬切。"""
    out: list[dict] = []
    paras: list[tuple[int, int]] = []
    seg_start = section["start"]
    for m in re.finditer(r"\n\n+", text[section["start"]:section["end"]]):
        end = section["start"] + m.start()
        if text[seg_start:end].strip():
            paras.append((seg_start, end))
        seg_start = section["start"] + m.end()
    if text[seg_start:section["end"]].strip():
        paras.append((seg_start, section["end"]))

    cur: list[tuple[int, int]] = []
    cur_len = 0

    def _flush() -> None:
        nonlocal cur, cur_len
        if not cur:
            return
        out.append({
            "text": text[cur[0][0]:cur[-1][1]],
            "start": cur[0][0],
            "end": cur[-1][1],
            "parent_chunk_id": parent_idx,
            "is_parent": False,
        })
        cur = []
        cur_len = 0

    for p_start, p_end in paras:
        p_len = p_end - p_start
        if p_len > max_chars:  # 超长段落：先冲掉已合并的，再硬切
            _flush()
            j = p_start
            while j < p_end:
                piece_end = min(j + max_chars, p_end)
                out.append({
                    "text": text[j:piece_end], "start": j, "end": piece_end,
                    "parent_chunk_id": parent_idx, "is_parent": False,
                })
                j = piece_end - overlap if piece_end < p_end else p_end
        elif cur and cur_len + p_len + 2 > max_chars:
            _flush()
            cur = [(p_start, p_end)]
            cur_len = p_len
        else:
            cur.append((p_start, p_end))
            cur_len += p_len + (2 if cur else 0)
    _flush()
    return out


class KbService:
    def __init__(self, kb_dir: Path | str, raw_dir: Path | str) -> None:
        self.kb_dir = Path(kb_dir)
        self.raw_dir = Path(raw_dir)
        self._graph: "GraphService | None" = None

    @property
    def _graph_service(self) -> "GraphService":
        """懒加载图谱/向量服务（需 DashScope Key 才真正启用）。"""
        if self._graph is None:
            from app.services.graph_service import GraphService
            self._graph = GraphService()
        return self._graph

    def _index_note(self, db: Session, note_id: int) -> None:
        """写库/编辑后触发向量索引 + 图谱增量构建（FR-03/FR-05）。无 Key 或失败均降级不阻塞。"""
        from app.core.config import settings
        if not settings.dashscope_api_key:
            return
        try:
            self._graph_service.build_note_vectors(db, note_id)
            self._graph_service.build_note_graph(db, note_id)
        except Exception as e:
            log.warning("笔记向量/图谱构建失败（note=%s）：%s", note_id, e)

    # ---------- 工具 ----------
    @staticmethod
    def _sha(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _rel_hash(rel: str) -> str:
        return hashlib.sha256(rel.encode("utf-8")).hexdigest()

    def _import_service(self) -> ImportService:
        return ImportService(self.raw_dir)

    @staticmethod
    def _is_text_based(abs_p: Path) -> bool:
        """是否纯文本格式（可安全回写原文件）。"""
        from app.parsers import get_parser
        parser = get_parser(abs_p.suffix.lower().lstrip("."))
        return isinstance(parser, (TextParser, MarkdownParser, HtmlParser))

    @staticmethod
    def _derive_title(md: str, fallback_name: str) -> str:
        for line in md.splitlines():
            line = line.strip()
            if line.startswith("#"):
                title = line.lstrip("#").strip()
                return title[:200] or fallback_name
            if line:
                return line[:200] or fallback_name
        return PurePosixPath(fallback_name).stem

    def _derive_note_path(self, db: Session, rel_path: str, origin_id: int) -> str:
        """由导入文件相对路径推导笔记相对路径（同目录 + .md），避免与其他来源笔记冲突。"""
        base = PurePosixPath(rel_path).with_suffix(".md").as_posix()
        candidate = base
        n = 2
        while True:
            existing = db.query(KbNote).filter(KbNote.note_path == candidate).first()
            if existing is None or existing.origin_import_id == origin_id:
                return candidate
            stem = PurePosixPath(base).stem
            candidate = PurePosixPath(base).with_name(f"{stem}-{n}.md").as_posix()
            n += 1

    def _rebuild_chunks(self, db: Session, note_id: int, content_md: str) -> None:
        db.query(DocChunk).filter(DocChunk.note_id == note_id).delete()
        raw = chunk_text(content_md)
        rows: list[DocChunk] = []
        for i, c in enumerate(raw):
            row = DocChunk(
                note_id=note_id, chunk_index=i, chunk_text=c["text"],
                char_start=c["start"], char_end=c["end"], parent_chunk_id=None, chroma_id=None,
            )
            rows.append(row)
            db.add(row)
        db.flush()  # 生成自增 id，用于父子块回填
        id_by_idx = {r.chunk_index: r.id for r in rows}
        for i, c in enumerate(raw):
            pid = c.get("parent_chunk_id")
            if pid is not None:
                rows[i].parent_chunk_id = id_by_idx[pid]
        db.flush()

    def _write_note_file(self, note_path: str, content_md: str) -> None:
        target = (self.kb_dir / note_path).resolve()
        if self.kb_dir.resolve() not in target.parents:
            raise PathSafetyError("笔记路径越界")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content_md, encoding="utf-8")

    # ---------- 选择性写入（FR-02） ----------
    def write_selected(self, db: Session, rel_paths: list[str]) -> dict:
        svc = self._import_service()
        svc.sync_from_fs(db)  # 确保 DB 记录与磁盘对齐
        results: list[dict] = []
        for rel in rel_paths:
            try:
                safe = svc._safe_rel(rel)
            except PathSafetyError as e:
                results.append({"path": rel, "type": "file", "status": "error", "reason": str(e)})
                continue
            abs_p = (self.raw_dir / safe).resolve()
            if not abs_p.exists():
                results.append({"path": rel, "type": "file", "status": "error", "reason": "文件不存在"})
                continue
            if abs_p.is_dir():
                self._write_folder(db, abs_p, results)
            else:
                self._write_file(db, abs_p, results)
        written = skipped = failed = 0
        for r in results:
            w, s, f = self._count_result(r)
            written += w
            skipped += s
            failed += f
        return {
            "results": results,
            "summary": {"written": written, "skipped": skipped, "failed": failed},
        }

    @staticmethod
    def _count_result(result: dict) -> tuple[int, int, int]:
        """统计单个结果条目对 summary 的贡献（文件夹按 items 递归统计到文件粒度）。"""
        if result.get("type") == "dir":
            written = skipped = failed = 0
            for it in result.get("items", []):
                w, s, f = KbService._count_result(it)
                written += w
                skipped += s
                failed += f
            return written, skipped, failed
        if result["status"] == "success":
            return 1, 0, 0
        if result["status"] == "skipped":
            return 0, 1, 0
        return 0, 0, 1

    def _write_folder(self, db: Session, abs_dir: Path, results: list[dict]) -> None:
        rel_dir = abs_dir.relative_to(self.raw_dir).as_posix()
        files = [f for f in abs_dir.rglob("*") if f.is_file()]
        if not files:
            results.append({"path": rel_dir, "type": "dir", "status": "skipped", "reason": "空文件夹"})
            return
        items: list[dict] = []
        for f in files:
            items.append(self._write_file(db, f, results, nested=True))
        statuses = [it["status"] for it in items]
        if all(s == "skipped" for s in statuses):
            results.append({"path": rel_dir, "type": "dir", "status": "skipped",
                            "reason": "文件夹内全部文件均已导入，整体跳过", "items": items})
        else:
            done = sum(1 for s in statuses if s == "success")
            skipped = sum(1 for s in statuses if s == "skipped")
            failed = sum(1 for s in statuses if s == "error")
            results.append({"path": rel_dir, "type": "dir", "status": "success",
                            "reason": f"已写入 {done} 个文件，跳过 {skipped} 个已导入，失败 {failed} 个",
                            "items": items})

    def _write_file(self, db: Session, abs_file: Path, results: list[dict], nested: bool = False) -> dict:
        rel = abs_file.relative_to(self.raw_dir).as_posix()
        rec = db.query(ImportFile).filter(ImportFile.rel_path == rel).first()
        if rec is None:
            result = {"path": rel, "type": "file", "status": "error", "reason": "导入区记录缺失，请刷新"}
            if not nested:
                results.append(result)
            return result
        if rec.import_status == 1:
            result = {"path": rel, "type": "file", "status": "skipped", "reason": "已导入"}
            if not nested:
                results.append(result)
            return result
        try:
            md = parse_file(abs_file)
        except ParseError as e:
            result = {"path": rel, "type": "file", "status": "error", "reason": f"解析失败：{e}"}
            if not nested:
                results.append(result)
            return result
        try:
            self._upsert_note(db, rec, md, abs_file)
            db.commit()
            result = {"path": rel, "type": "file", "status": "success", "reason": "已写入",
                      "note_path": self._note_path_of(db, rec.id), "note_id": self._note_id_of(db, rec.id)}
            if not nested:
                results.append(result)
            return result
        except Exception as e:  # 单文件失败不中断批量
            db.rollback()
            log.warning("写入失败 %s: %s", rel, e)
            result = {"path": rel, "type": "file", "status": "error", "reason": f"写入失败：{e}"}
            if not nested:
                results.append(result)
            return result

    @staticmethod
    def _note_id_of(db: Session, import_id: int) -> int | None:
        note = db.query(KbNote).filter(KbNote.origin_import_id == import_id).first()
        return note.id if note else None

    @staticmethod
    def _note_path_of(db: Session, import_id: int) -> str | None:
        note = db.query(KbNote).filter(KbNote.origin_import_id == import_id).first()
        return note.note_path if note else None

    def _upsert_note(self, db: Session, rec: ImportFile, md: str, abs_file: Path) -> KbNote:
        """按 origin_import_id 写入/更新原笔记（更新而非重建），并重建向量块。"""
        note_path = self._derive_note_path(db, rec.rel_path, rec.id)
        content_hash = self._sha(md)
        title = self._derive_title(md, rec.file_name)
        frontmatter = json.dumps({"origin": rec.rel_path, "origin_type": rec.ext_type}, ensure_ascii=False)
        note = db.query(KbNote).filter(KbNote.origin_import_id == rec.id).first()
        if note is None:
            note = KbNote(
                note_path=note_path, note_path_hash=self._rel_hash(note_path), title=title,
                content_md=md, content_hash=content_hash, origin_import_id=rec.id,
                frontmatter_json=frontmatter,
            )
            db.add(note)
            db.flush()
        else:
            if note.note_path != note_path:
                old = (self.kb_dir / note.note_path).resolve()
                if self.kb_dir.resolve() in old.parents and old.exists():
                    old.unlink()
            note.note_path = note_path
            note.note_path_hash = self._rel_hash(note_path)
            note.title = title
            note.content_md = md
            note.content_hash = content_hash
            note.frontmatter_json = frontmatter
            db.flush()
        self._write_note_file(note_path, md)
        self._rebuild_chunks(db, note.id, md)
        rec.import_status = 1
        self._index_note(db, note.id)  # 写库后触发向量索引 + 图谱增量构建
        return note

    # ---------- 笔记 CRUD ----------
    def list_notes(self, db: Session) -> list[dict]:
        rows = db.query(KbNote).order_by(KbNote.updated_at.desc()).all()
        out: list[dict] = []
        for n in rows:
            origin_rel = None
            if n.origin_import_id:
                rec = db.query(ImportFile).filter(ImportFile.id == n.origin_import_id).first()
                origin_rel = rec.rel_path if rec else None
            out.append({
                "id": n.id, "note_path": n.note_path, "title": n.title,
                "origin_import_id": n.origin_import_id, "origin_rel_path": origin_rel,
                "updated_at": n.updated_at.isoformat() if n.updated_at else None,
            })
        return out

    def get_note(self, db: Session, note_id: int) -> dict:
        n = db.query(KbNote).filter(KbNote.id == note_id).first()
        if n is None:
            raise FileNotFoundError("笔记不存在")
        origin_rel = None
        if n.origin_import_id:
            rec = db.query(ImportFile).filter(ImportFile.id == n.origin_import_id).first()
            origin_rel = rec.rel_path if rec else None
        return {
            "id": n.id, "note_path": n.note_path, "title": n.title,
            "content_md": n.content_md, "origin_import_id": n.origin_import_id,
            "origin_rel_path": origin_rel,
            "updated_at": n.updated_at.isoformat() if n.updated_at else None,
        }

    def save_note_edit(self, db: Session, note_id: int, content_md: str) -> dict:
        """编辑笔记并保存：更新原笔记 + 写回磁盘 + 重建向量块 + 来源导入文件标记置 0。"""
        n = db.query(KbNote).filter(KbNote.id == note_id).first()
        if n is None:
            raise FileNotFoundError("笔记不存在")
        n.content_md = content_md
        n.content_hash = self._sha(content_md)
        n.title = self._derive_title(content_md, Path(n.note_path).stem)
        self._write_note_file(n.note_path, content_md)
        self._rebuild_chunks(db, n.id, content_md)
        if n.origin_import_id:
            rec = db.query(ImportFile).filter(ImportFile.id == n.origin_import_id).first()
            if rec:
                rec.import_status = 0  # 内容已变更，标记消失（待重写）
        db.commit()
        self._index_note(db, n.id)  # 编辑后重做向量 + 图谱
        log.info("笔记已保存：%s（note_id=%s）", n.note_path, n.id)
        return {"note_id": n.id, "note_path": n.note_path, "import_status_reset": bool(n.origin_import_id)}

    def save_file_edit(self, db: Session, rel_path: str, content: str) -> dict:
        """编辑导入区文本文件并保存：写回原文件 + 更新哈希/大小 + 导入标记置 0。"""
        svc = self._import_service()
        abs_p = svc._abs(rel_path)
        if not abs_p.is_file():
            raise FileNotFoundError("文件不存在")
        if not self._is_text_based(abs_p):
            raise ValueError("二进制格式不支持直接编辑保存，请先写入知识库后编辑笔记")
        abs_p.write_text(content, encoding="utf-8")
        rec = db.query(ImportFile).filter(ImportFile.rel_path == rel_path).first()
        if rec:
            rec.content_hash = ImportService._hash(abs_p)
            rec.file_size = abs_p.stat().st_size
            rec.import_status = 0  # 内容变更，标记消失（待重写）
        db.commit()
        log.info("导入区文件已保存：%s", rel_path)
        return {"rel_path": rel_path, "import_status": 0}

    def preview(self, db: Session, rel_path: str) -> dict:
        """解析导入区文件为 Markdown（内容区预览/编辑底稿）。"""
        svc = self._import_service()
        abs_p = svc._abs(rel_path)
        if not abs_p.is_file():
            raise FileNotFoundError("文件不存在")
        md = parse_file(abs_p)
        return {
            "rel_path": rel_path,
            "content_md": md,
            "text_based": self._is_text_based(abs_p),
            "ext": abs_p.suffix.lower().lstrip("."),
        }

    def delete_note(self, db: Session, note_id: int) -> dict:
        """级联清理：向量块 + 独占图谱关系 + 孤立实体 + 笔记文件/记录，来源导入文件标记置 0。"""
        n = db.query(KbNote).filter(KbNote.id == note_id).first()
        if n is None:
            raise FileNotFoundError("笔记不存在")
        origin_id = n.origin_import_id
        db.query(DocChunk).filter(DocChunk.note_id == n.id).delete()
        # 图谱关系（MySQL）+ 对应 Neo4j 关系 级联清理；再移除向量
        self._graph_service.remove_relations_for_note(db, n.id)
        if _dashscope_ready():
            try:
                self._graph_service.remove_note_vectors(db, n.id)
            except Exception as e:
                log.warning("向量清理失败：%s", e)
        kb_file = (self.kb_dir / n.note_path).resolve()
        if self.kb_dir.resolve() in kb_file.parents and kb_file.exists():
            kb_file.unlink()
        db.delete(n)  # 先删笔记，再清理图谱，使被删笔记 id 不再被视为"存在"
        self._cleanup_orphan_entities(db, note_id)
        if origin_id:
            rec = db.query(ImportFile).filter(ImportFile.id == origin_id).first()
            if rec:
                rec.import_status = 0
        db.commit()
        log.info("笔记已删除：note_id=%s", note_id)
        return {"note_id": note_id, "deleted": True}

    def _cleanup_orphan_entities(self, db: Session, removed_note_id: int) -> None:
        """级联清理：从实体来源列表剔除已删笔记，并删除不再被任何笔记引用的"孤立实体"（共享实体保留）。"""
        for ent in db.query(GraphEntity).all():
            src_ids: list[int] = []
            if ent.source_note_ids:
                try:
                    src_ids = [x for x in json.loads(ent.source_note_ids) if isinstance(x, int)]
                except (ValueError, TypeError):
                    src_ids = []
            if removed_note_id in src_ids:
                src_ids = [x for x in src_ids if x != removed_note_id]
                ent.source_note_ids = json.dumps(src_ids, ensure_ascii=False)
            if not src_ids:
                db.query(GraphRelation).filter(
                    (GraphRelation.source_entity_id == ent.id) | (GraphRelation.target_entity_id == ent.id)
                ).delete()
                db.delete(ent)
