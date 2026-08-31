"""周期回顾服务（FR-09）：按周期回顾用户近期工作内容。

核心逻辑：
- 按 period_type（week/month）计算时间窗口，收集该窗口内**用户活动**：
  1. AI 助手对话记录（会话标题 + 关键消息片段，限条数节省 token）
  2. 导入的文件目录（仅文件名/路径，不读正文，节省 token 和时间）
  3. 新增/更新的知识库笔记目录（仅标题/路径，不读正文）
- LLM（DashScope）将上述信息压缩为 Markdown 工作回顾报告，带 `#weekly-review` / `#monthly-review` 标签。
- 摘要写入知识库 `reviews/{period_key}.md`（同时登记 `review_records` 表，记录 `note_id` 关联）。
- 定时（APScheduler）与手动（API）共用同一入口；无数据 / 无 Key 时优雅降级，不阻断。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from pathlib import Path, PurePosixPath

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import ChatMessage, ChatSession, DocChunk, ImportFile, KbNote, ReviewRecord
from app.db.scoping import workspace_scope
from app.services.kb_service import chunk_text
from app.services.llm.base import LLMClient
from app.services.llm.dashscope_client import DashScopeClient

log = get_logger("services.review")

# 数据采集限制
MAX_CHAT_SESSIONS = 10        # 最多纳入的 AI 对话数
MAX_MESSAGES_PER_SESSION = 6  # 每段对话最多取的消息数（user + assistant 配对）
MESSAGE_EXCERPT_CHARS = 300   # 单条消息截断长度
MAX_IMPORT_FILES = 50         # 最多纳入的导入文件数
MAX_KB_NOTES = 50             # 最多纳入的知识库笔记数
# 回顾笔记在知识库中的目录（与导入来源笔记区分）
REVIEW_SUBDIR = "reviews"

REVIEW_SYSTEM_PROMPT = (
    "你是一位个人工作回顾助手。用户会提供某一周期内的以下信息：\n"
    "1. AI 助手对话记录（会话标题 + 关键消息片段）\n"
    "2. 导入的知识库文件（文件名列表，仅目录）\n"
    "3. 新增/更新的知识库笔记（标题列表，仅目录）\n\n"
    "请生成一份结构化的 Markdown 工作回顾报告，要求：\n"
    "1. 首行给出该周期的工作主题总结（一句话）。\n"
    "2. 用 ## 分节归纳主要工作内容/学习进展，条目式列出，忠实于原文，不臆造。\n"
    "3. 末尾给出『待办/启发』小节，列出值得继续跟进的内容。\n"
    "4. 只输出 Markdown，不要代码块包裹。"
)


class ReviewService:
    def __init__(self, kb_dir: Path | str | None = None, llm: LLMClient | None = None,
                 workspace_id: int = 1, user_id: int | None = None) -> None:
        self.kb_dir = Path(kb_dir or settings.kb_dir)
        self.llm = llm or DashScopeClient()
        self.workspace_id = workspace_id  # 工作区隔离键（阶段七）
        self.user_id = user_id  # 用户 ID（可选，用于查询 AI 对话记录）

    # ---------- 周期与时间窗口 ----------
    @staticmethod
    def period_key(period_type: str, ref: dt.date | None = None) -> str:
        """当前周期的标识：week → '2026-W34'；month → '2026-08'。"""
        ref = ref or dt.date.today()
        if period_type == "month":
            return ref.strftime("%Y-%m")
        # week：ISO 周
        iso = ref.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"

    @staticmethod
    def _week_range(year: int, week: int) -> tuple[dt.datetime, dt.datetime]:
        """ISO 周 [周一 00:00, 下周一 00:00)。"""
        monday = dt.date.fromisocalendar(year, week, 1)
        start = dt.datetime.combine(monday, dt.time.min)
        return start, start + dt.timedelta(days=7)

    @staticmethod
    def _month_range(year: int, month: int) -> tuple[dt.datetime, dt.datetime]:
        start = dt.datetime(year, month, 1)
        end = dt.datetime(year + 1, 1, 1) if month == 12 else dt.datetime(year, month + 1, 1)
        return start, end

    def time_window(self, period_type: str, period_key: str) -> tuple[dt.datetime, dt.datetime] | None:
        """由 period_key 解析时间窗口；解析失败返回 None（走默认近 7/30 天）。"""
        if period_type == "month":
            m = re.match(r"^(\d{4})-(\d{2})$", period_key)
            if m:
                return self._month_range(int(m.group(1)), int(m.group(2)))
        else:
            m = re.match(r"^(\d{4})-W(\d{2})$", period_key)
            if m:
                return self._week_range(int(m.group(1)), int(m.group(2)))
        return None

    # ---------- 多源数据采集 ----------
    def _time_bounds(self, period_type: str, period_key: str) -> tuple[dt.datetime, dt.datetime]:
        """获取时间窗口边界。"""
        window = self.time_window(period_type, period_key)
        if window is None:
            days = 30 if period_type == "month" else 7
            start = dt.datetime.now() - dt.timedelta(days=days)
            end = dt.datetime.now()
        else:
            start, end = window
        return start, end

    def _collect_chat_sessions(self, db: Session, start: dt.datetime, end: dt.datetime) -> list[dict]:
        """采集时间窗口内的 AI 对话记录（仅摘要，节省 token）。"""
        if self.user_id is None:
            return []

        sessions = (
            db.query(ChatSession)
            .filter(
                ChatSession.user_id == self.user_id,
                ChatSession.created_at >= start,
                ChatSession.created_at < end,
            )
            .order_by(ChatSession.created_at.desc())
            .limit(MAX_CHAT_SESSIONS)
            .all()
        )

        result = []
        for s in sessions:
            messages = (
                db.query(ChatMessage)
                .filter(ChatMessage.session_id == s.id)
                .order_by(ChatMessage.created_at.asc())
                .limit(MAX_MESSAGES_PER_SESSION)
                .all()
            )
            msg_list = []
            for m in messages:
                content = (m.content or "").strip()
                if len(content) > MESSAGE_EXCERPT_CHARS:
                    content = content[:MESSAGE_EXCERPT_CHARS] + "…"
                msg_list.append({"role": m.role, "content": content})
            result.append({
                "title": s.title or f"对话 {s.id}",
                "message_count": len(msg_list),
                "messages": msg_list,
            })
        return result

    def _collect_import_files(self, db: Session, start: dt.datetime, end: dt.datetime) -> list[dict]:
        """采集时间窗口内导入的文件目录（仅文件名/路径，不读正文）。"""
        files = (
            db.query(ImportFile)
            .filter(
                workspace_scope(ImportFile, self.workspace_id),
                ImportFile.created_at >= start,
                ImportFile.created_at < end,
            )
            .order_by(ImportFile.created_at.desc())
            .limit(MAX_IMPORT_FILES)
            .all()
        )
        return [
            {"rel_path": f.rel_path, "file_name": f.file_name, "is_dir": bool(f.is_dir)}
            for f in files
        ]

    def _collect_kb_notes(self, db: Session, start: dt.datetime, end: dt.datetime) -> list[dict]:
        """采集时间窗口内新增/更新的知识库笔记目录（仅标题/路径，不读正文，排除回顾笔记自身）。"""
        prefix = f"{REVIEW_SUBDIR}/"
        notes = (
            db.query(KbNote)
            .filter(
                ~KbNote.note_path.like(prefix + "%"),
                workspace_scope(KbNote, self.workspace_id),
                ((KbNote.created_at >= start) & (KbNote.created_at < end))
                | ((KbNote.updated_at >= start) & (KbNote.updated_at < end)),
            )
            .order_by(KbNote.updated_at.desc())
            .limit(MAX_KB_NOTES)
            .all()
        )
        return [{"title": n.title, "note_path": n.note_path} for n in notes]

    def _collect_period_data(self, db: Session, period_type: str, period_key: str) -> dict:
        """采集周期内所有活动数据。"""
        start, end = self._time_bounds(period_type, period_key)
        return {
            "chat_sessions": self._collect_chat_sessions(db, start, end),
            "imported_files": self._collect_import_files(db, start, end),
            "kb_notes": self._collect_kb_notes(db, start, end),
        }

    # ---------- LLM 生成 ----------
    def _build_llm_input(self, data: dict, period_type: str, period_key: str) -> str:
        """将采集到的多源数据组装为 LLM 输入文本。"""
        parts = [f"周期：{period_key}（{'周回顾' if period_type == 'week' else '月回顾'}）\n"]

        # AI 对话记录
        if data["chat_sessions"]:
            parts.append("## AI 助手对话记录\n")
            for s in data["chat_sessions"]:
                parts.append(f"### {s['title']}（共 {s['message_count']} 条消息）\n")
                for m in s["messages"]:
                    role_label = "用户" if m["role"] == "user" else "AI"
                    parts.append(f"- [{role_label}] {m['content']}\n")
                parts.append("\n")

        # 导入文件目录
        if data["imported_files"]:
            parts.append("## 导入的文件\n")
            for f in data["imported_files"]:
                icon = "📁" if f["is_dir"] else "📄"
                parts.append(f"- {icon} {f['rel_path']}\n")
            parts.append("\n")

        # 知识库笔记目录
        if data["kb_notes"]:
            parts.append("## 新增/更新的知识库笔记\n")
            for n in data["kb_notes"]:
                parts.append(f"- {n['title']}（{n['note_path']}）\n")
            parts.append("\n")

        return "".join(parts)

    def _summarize(self, data: dict, period_type: str, period_key: str) -> str:
        """调用 LLM 将多源数据压缩为回顾 Markdown。"""
        user_content = self._build_llm_input(data, period_type, period_key)
        if not user_content.strip():
            return ""
        return self.llm.generate(
            [
                {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
                {"role": "user", "content": f"请根据以下信息生成工作回顾报告：\n\n{user_content}"},
            ],
            temperature=0.4,
        ).strip()

    # ---------- 写库 ----------
    @staticmethod
    def _sha(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _rel_hash(self, rel: str) -> str:
        # 阶段七：纳入 workspace_id 盐，跨工作区同名回顾笔记各自独立唯一
        return hashlib.sha256(f"{self.workspace_id}\x00{rel}".encode("utf-8")).hexdigest()

    def _write_review_note(self, db: Session, period_key: str, md: str) -> KbNote:
        """将回顾摘要作为知识库笔记写入 reviews/ 目录，并建立父子块。"""
        tag = "#weekly-review" if "W" in period_key else "#monthly-review"
        note_path = f"{REVIEW_SUBDIR}/{period_key}.md"
        content_md = f"# 回顾 {period_key}\n\n> 标签：{tag}\n\n{md}\n"
        title = f"回顾 {period_key}"

        note = db.query(KbNote).filter(KbNote.note_path == note_path,
                                       workspace_scope(KbNote, self.workspace_id)).first()
        if note is None:
            note = KbNote(
                note_path=note_path, note_path_hash=self._rel_hash(note_path), title=title,
                content_md=content_md, content_hash=self._sha(content_md),
                origin_import_id=None, workspace_id=self.workspace_id,
            )
            db.add(note)
            db.flush()
        else:
            note.title = title
            note.content_md = content_md
            note.content_hash = self._sha(content_md)
            db.flush()

        # 写文件 + 重建父子块（复用 kb_service 分块逻辑）
        target = (self.kb_dir / note_path).resolve()
        if self.kb_dir.resolve() not in target.parents:
            raise ValueError("回顾笔记路径越界")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content_md, encoding="utf-8")

        db.query(DocChunk).filter(DocChunk.note_id == note.id).delete()
        raw = chunk_text(content_md)
        rows: list[DocChunk] = []
        for i, c in enumerate(raw):
            rows.append(DocChunk(
                note_id=note.id, chunk_index=i, chunk_text=c["text"],
                char_start=c["start"], char_end=c["end"], parent_chunk_id=None, chroma_id=None,
                workspace_id=self.workspace_id,
            ))
            db.add(rows[-1])
        db.flush()
        id_by_idx = {r.chunk_index: r.id for r in rows}
        for i, c in enumerate(raw):
            pid = c.get("parent_chunk_id")
            if pid is not None:
                rows[i].parent_chunk_id = id_by_idx[pid]
        db.flush()
        return note

    # ---------- 对外入口 ----------
    def generate_review(self, db: Session, period_type: str, period_key: str | None = None) -> dict:
        """生成一个周期的回顾摘要并入库（幂等：同周期重复生成则更新原笔记）。"""
        if period_type not in ("week", "month"):
            raise ValueError("period_type 仅支持 week/month")
        if not settings.dashscope_api_key:
            return {"status": "skipped", "reason": "未配置 DASHSCOPE_API_KEY", "period_type": period_type}

        period_key = period_key or self.period_key(period_type)
        data = self._collect_period_data(db, period_type, period_key)

        # 统计总数
        chat_count = len(data["chat_sessions"])
        import_count = len(data["imported_files"])
        note_count = len(data["kb_notes"])

        if chat_count == 0 and import_count == 0 and note_count == 0:
            return {"status": "skipped", "reason": f"周期 {period_key} 内无活动数据", "period_key": period_key}

        md = self._summarize(data, period_type, period_key)
        if not md:
            return {"status": "skipped", "reason": "LLM 生成结果为空", "period_key": period_key}

        note = self._write_review_note(db, period_key, md)

        record = db.query(ReviewRecord).filter(
            ReviewRecord.period_type == period_type, ReviewRecord.period_key == period_key,
            workspace_scope(ReviewRecord, self.workspace_id),
        ).first()
        if record is None:
            record = ReviewRecord(period_type=period_type, period_key=period_key,
                                  summary_md=md, note_id=note.id,
                                  workspace_id=self.workspace_id)
            db.add(record)
        else:
            record.summary_md = md
            record.note_id = note.id
        db.commit()

        # 向量 + 图谱构建（可选增强，失败不阻断）
        try:
            from app.services.graph_service import GraphService
            GraphService(workspace_id=self.workspace_id).build_note_vectors(db, note.id)
            GraphService(workspace_id=self.workspace_id).build_note_graph(db, note.id)
        except Exception as e:
            log.warning("回顾笔记向量/图谱构建失败：%s", e)
        db.commit()

        log.info("周期回顾已生成：%s %s（对话%s 导入%s 笔记%s）",
                 period_type, period_key, chat_count, import_count, note_count)
        return {
            "status": "ok", "period_type": period_type, "period_key": period_key,
            "chat_count": chat_count, "import_count": import_count, "note_count": note_count,
            "note_id": note.id, "note_path": note.note_path,
        }

    # ---------- 查询 / 删除 ----------
    @staticmethod
    def _record_to_dict(r: ReviewRecord) -> dict:
        return {
            "id": r.id, "period_type": r.period_type, "period_key": r.period_key,
            "note_id": r.note_id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }

    def list_reviews(self, db: Session) -> list[dict]:
        rows = (db.query(ReviewRecord)
                .filter(workspace_scope(ReviewRecord, self.workspace_id))
                .order_by(ReviewRecord.id.desc()).all())
        return [self._record_to_dict(r) for r in rows]

    def get_review(self, db: Session, review_id: int) -> dict:
        r = (db.query(ReviewRecord)
             .filter(ReviewRecord.id == review_id, workspace_scope(ReviewRecord, self.workspace_id))
             .first())
        if r is None:
            raise FileNotFoundError("回顾记录不存在")
        out = self._record_to_dict(r)
        out["summary_md"] = r.summary_md
        return out

    def delete_review(self, db: Session, review_id: int) -> dict:
        r = (db.query(ReviewRecord)
             .filter(ReviewRecord.id == review_id, workspace_scope(ReviewRecord, self.workspace_id))
             .first())
        if r is None:
            raise FileNotFoundError("回顾记录不存在")
        note_id = r.note_id
        db.delete(r)
        # 同步删除知识库中的回顾笔记（含文件与分块）
        if note_id:
            try:
                from app.services.kb_service import KbService
                KbService(self.kb_dir, settings.raw_dir, workspace_id=self.workspace_id).delete_note(db, note_id)
            except Exception as e:
                log.warning("删除回顾笔记失败（note=%s）：%s", note_id, e)
        db.commit()
        log.info("回顾记录已删除：id=%s", review_id)
        return {"id": review_id, "deleted": True}