"""AI 助手编排服务（阶段四 FR-07 / FR-08）。

能力：
- 会话持久化：chat_sessions / chat_messages 多会话新建/切换/读取。
- stream_chat：SSE 流式对话，基于混合检索（图谱 + 知识库）上下文 + 引用溯源。
- generate_questions：根据知识库/图谱内容由 AI 出题。
- retrieve_import：检索原始文件区（raw/）文件并读取内容（AI 工具）。
- generate_md：把内容/对话总结落为 md，默认写入 ./input/（防路径穿越）。

设计约定：任何外部依赖（DashScope 等）不可用均优雅降级，不阻断（对齐 NFR-04）。
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path, PurePosixPath

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import ChatMessage, ChatSession
from app.services.llm.base import LLMClient
from app.services.llm.dashscope_client import DashScopeClient
from app.services.llm.prompt_templates import search_answer_system
from app.services.retriever import build_coordinator

log = get_logger("services.assistant")

# SSE 事件类型
EV_DELTA = "delta"      # 增量文本
EV_CITATIONS = "citations"  # 引用溯源（开始回答前先发出）
EV_STAGE = "stage"      # 工具/规划阶段进度（LangGraph 节点）
EV_DONE = "done"        # 流结束，附 message_id / session_id

# 检索导入区时读取的文件内容预览上限
IMPORT_SNIPPET_CHARS = 800
IMPORT_READ_EXT = {".md", ".markdown", ".txt", ".text"}


class AssistantService:
    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or DashScopeClient()

    # ================= 会话持久化 =================
    def list_sessions(self, db: Session, limit: int = 50) -> list[dict]:
        rows = (
            db.query(ChatSession)
            .order_by(ChatSession.updated_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {"id": s.id, "title": s.title or f"会话 {s.id}",
             "created_at": _iso(s.created_at), "updated_at": _iso(s.updated_at)}
            for s in rows
        ]

    def create_session(self, db: Session, title: str | None = None) -> dict:
        s = ChatSession(title=title)
        db.add(s)
        db.commit()
        db.refresh(s)
        return {"id": s.id, "title": s.title or f"会话 {s.id}", "created_at": _iso(s.created_at)}

    def list_messages(self, db: Session, session_id: int, limit: int = 200) -> list[dict]:
        rows = (
            db.query(ChatMessage)
            .filter(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.id.asc())
            .limit(limit)
            .all()
        )
        return [
            {"id": m.id, "role": m.role, "content": m.content,
             "citations": _load_json(m.citations_json), "created_at": _iso(m.created_at)}
            for m in rows
        ]

    def delete_session(self, db: Session, session_id: int) -> bool:
        """删除会话及其全部消息（级联清理）。不存在返回 False。"""
        session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
        if session is None:
            return False
        db.query(ChatMessage).filter(ChatMessage.session_id == session_id).delete()
        db.delete(session)
        db.commit()
        return True

    def rename_session(self, db: Session, session_id: int, title: str) -> dict | None:
        """手动重命名会话；标题为空或会话不存在则返回 None。"""
        title = (title or "").strip()
        if not title:
            return None
        session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
        if session is None:
            return None
        session.title = title[:255]
        db.commit()
        return {"id": session.id, "title": session.title,
                "created_at": _iso(session.created_at), "updated_at": _iso(session.updated_at)}

    def _auto_title(self, db: Session, session_id: int, message: str) -> None:
        """没有自定义题目时，用首条提问给会话自动命名。"""
        session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
        if session is not None and not (session.title or "").strip():
            session.title = (message or "").strip()[:30] or f"会话 {session.id}"
            db.commit()

    def _add_message(self, db: Session, session_id: int, role: str, content: str, citations: list | None = None) -> int:
        m = ChatMessage(session_id=session_id, role=role, content=content,
                        citations_json=_dump_json(citations))
        db.add(m)
        db.flush()
        db.commit()
        return m.id

    # ================= 会话上下文（混合检索） =================
    def _build_context(self, db: Session, query: str, top_k: int) -> tuple[list[str], list[dict]]:
        """把知识库/图谱混合检索结果组织成上下文文本 + 引用清单。检索失败降级为空。"""
        try:
            coord = build_coordinator(db, top_k=top_k)
            res = coord.search(query, top_k=top_k)
        except Exception as e:
            log.warning("对话检索失败（降级为无上下文）：%s", e)
            return [], []
        results = res.get("results", [])
        chunks: list[str] = []
        citations: list[dict] = []
        for i, r in enumerate(results, start=1):
            meta = r.get("metadata", {}) or {}
            title = r.get("title") or meta.get("note_title") or f"资料{i}"
            # 子块精排 + 父块上下文增强（parent_text 存在时优先）
            body = r.get("parent_text") or r.get("text") or ""
            if body:
                chunks.append(f"[来源{i}] {title}\n{body}")
                citations.append({
                    "idx": i, "title": title, "note_id": meta.get("note_id"),
                    "note_path": meta.get("note_path"), "chunk_id": meta.get("chunk_id"),
                })
        return chunks, citations

    def stream_chat(self, db: Session, session_id: int | None, message: str, top_k: int) -> Iterator[dict]:
        """SSE 流式对话（LangGraph 编排 + 结构化规划，产出事件 dict：{type, ...}）。

        - 先由 LangGraph 图做「结构化规划 → 检索知识库/图谱 → 查找导入区(按需)」，
          逐步产出 stage 事件展示进度；
        - 再把规划采集到的上下文注入，流式生成答案并落库助手消息。
        - 导入区工具仅在用户明确要求时（need_import）才被调用。
        """
        if not settings.dashscope_api_key:
            yield {"type": EV_DELTA, "content": "未配置 DASHSCOPE_API_KEY，AI 助手不可用。"}
            yield {"type": EV_DONE, "session_id": session_id, "message_id": None}
            return

        if session_id is None:
            s = self.create_session(db, title=message[:30])
            session_id = s["id"]
        else:
            # 续接会话：未自定义标题时，用本次提问作为会话名（按提问改名）
            self._auto_title(db, session_id, message)

        self._add_message(db, session_id, "user", message)

        # ---------- LangGraph 编排：结构化规划 + 按需工具调用 ----------
        from app.services.assistant_workflow import AssistantWorkflow

        history = self.list_messages(db, session_id)
        state: dict = {
            "message": message,
            "history": history[-12:],
            "context_blocks": [],
            "citations": [],
            "catalog_summary": "",
            "import_snippets": [],
        }
        wf = AssistantWorkflow(self, db, top_k=top_k)
        acc = dict(state)
        try:
            for node_id, update in wf.steps(state):
                acc.update(update)
                alias = wf.NODE_ALIAS.get(node_id)
                if alias:
                    yield {"type": EV_STAGE, "node": node_id, "text": alias}
        except Exception as e:
            log.warning("AI 编排图执行失败（降级为空上下文继续回答）：%s", e)

        # ---------- 组装模型输入 ----------
        messages: list[dict] = [{"role": "system", "content": search_answer_system()}]
        for h in history[-12:]:
            role = h["role"]
            if role in ("user", "assistant"):
                messages.append({"role": role, "content": h["content"]})

        ctx_parts: list[str] = []
        blocks = acc.get("context_blocks") or []
        cites = acc.get("citations") or []
        imp = acc.get("import_snippets") or []
        cat = acc.get("catalog_summary") or ""
        if blocks:
            ctx_parts.append("【知识库/图谱参考上下文（仅作依据）】\n" + "\n\n".join(blocks))
        if imp:
            ctx_parts.append("【导入区原始文件片段（依据）】\n" + "\n\n".join(imp))
        elif cat and (acc.get("plan") or {}).get("need_import"):
            # 用户只要求查看/定位导入区文件时，给出现成的实时目录
            ctx_parts.append(cat)
        if ctx_parts:
            messages.append({"role": "user", "content": "\n\n".join(ctx_parts)})
        messages.append({"role": "user", "content": f"请回答（中文）：\n{message}"})

        yield {"type": EV_DELTA, "content": ""}
        if cites:
            yield {"type": EV_CITATIONS, "citations": cites}

        full: list[str] = []
        try:
            for delta in self.llm.stream(messages):
                full.append(delta)
                yield {"type": EV_DELTA, "content": delta}
        except Exception as e:
            msg = f"\n\n[生成中断：{e}]"
            full.append(msg)
            yield {"type": EV_DELTA, "content": msg}

        mids = self._add_message(db, session_id, "assistant", "".join(full), cites)
        yield {"type": EV_DONE, "session_id": session_id, "message_id": mids}

    # ================= LangGraph 结构化规划（工具门控）=================
    def _structured_plan(self, db: Session, message: str, history: list[dict]) -> dict:
        """结构化规划：判定是否检索知识库/图谱、是否查找导入区文件，并给出检索 query。

        导入区（need_import）仅在用户**明确要求**以导入区文件为依据或查找导入区文件时置真，
        由系统提示词约束模型实现工具调用门控。
        """
        default = {
            "need_knowledge": True, "need_import": False,
            "queries": [message], "reason": "规划解析失败，回退为检索知识库",
        }
        if not settings.dashscope_api_key:
            return default
        sys = (
            "你是助手的中枢规划器，只做结构化判断，不直接回答。请判定本回合要调用的工具。规则：\n"
            "1) 问题需要基于资料作答（概念/事实/总结/出题/对比/归纳等）→ need_knowledge=true（检索知识库与图谱）；"
            "纯寒暄、闲聊、无需资料的问题 → need_knowledge=false。\n"
            "2) 只有当用户**明确要求**以导入区(原始文件)为依据作答，或要求查找/列出/定位/检索导入区文件时 → "
            "need_import=true；否则一律 false——即使提到某个文件名，只要没要求以导入区内容为依据，就为 false。\n"
            "3) queries：给出 1~2 个最利于检索的中文短查询词，若无需检索则为 []。\n"
            "只输出合法 JSON，勿加多余文字，格式："
            '{"need_knowledge": bool, "need_import": bool, "queries": ["..."], "reason": "一句话说明"}'
        )
        hist_text = "\n".join(
            f"{'用户' if h.get('role') == 'user' else '助手'}：{(h.get('content') or '')[:500]}"
            for h in (history or [])[-6:]
        )
        user = f"历史对话：\n{hist_text or '（无）'}\n\n当前问题：{message}"
        try:
            raw = self.llm.generate(
                [{"role": "system", "content": sys}, {"role": "user", "content": user}],
                json_mode=True, temperature=0.1,
            )
            plan = _parse_json_obj(raw)
            plan["need_knowledge"] = bool(plan.get("need_knowledge", False))
            plan["need_import"] = bool(plan.get("need_import", False))
            queries = plan.get("queries")
            plan["queries"] = [str(q) for q in (queries or []) if str(q).strip()][:2] or [message]
            return plan
        except Exception as e:
            log.warning("结构化规划失败（回退默认）：%s", e)
            return default

    def _import_catalog(self, db: Session) -> str:
        """导入区实时目录：列出 raw/ 下所有文件及相对路径。"""
        if not settings.raw_dir.exists():
            return "【导入区实时目录】\n（导入区为空）"
        rows = [
            f"- {p.relative_to(settings.raw_dir).as_posix()}"
            for p in sorted(settings.raw_dir.rglob("*")) if p.is_file()
        ]
        return "【导入区实时目录】\n" + ("\n".join(rows) if rows else "（导入区为空）")

    # ================= 出题 =================
    def generate_questions(self, db: Session, topic: str | None, count: int) -> dict:
        if not settings.dashscope_api_key:
            return {"ok": False, "reason": "未配置 DASHSCOPE_API_KEY"}
        material = self._collect_material(db, topic, limit=2000)
        sys = (
            "你是一名学习出题助手。请根据给定的资料生成练习题。题目要贴合资料内容、覆盖关键概念。"
            "请以 Markdown 输出，题型可混合选择题/填空题/简答题。选择题请给出选项与答案，简答题给出要点答案。"
            f"共 {count} 题。"
        )
        user = f"{material}\n\n请据此出题。"
        try:
            reply = self.llm.generate(
                [{"role": "system", "content": sys}, {"role": "user", "content": user}],
            )
        except Exception as e:
            return {"ok": False, "reason": f"出题失败：{e}"}
        return {"ok": True, "questions": reply, "count": count}

    def _collect_material(self, db: Session, topic: str | None, limit: int) -> str:
        """出题素材：有 topic 则混合检索相关片段，否则取最近若干笔记正文。"""
        if topic and topic.strip():
            try:
                coord = build_coordinator(db, top_k=5)
                res = coord.search(topic, top_k=5)
                parts = [r.get("parent_text") or r.get("text") or "" for r in res.get("results", [])]
                if parts:
                    return "素材片段：\n" + "\n\n".join(p[:500] for p in parts)[:limit]
            except Exception:
                pass
        from app.db.models import KbNote
        notes = (db.query(KbNote).filter(KbNote.content_md.isnot(None))
                 .order_by(KbNote.updated_at.desc()).limit(5).all())
        if not notes:
            return "（当前知识库为空，请先写入知识库或提供主题。）"
        return "知识库笔记节选：\n" + "\n\n".join((n.content_md or "")[:800] for n in notes)[:limit]

    # ================= 检索导入区（原始文件） =================
    def retrieve_import(self, db: Session, query: str, limit: int) -> dict:
        """按关键词在 raw/ 原始文件区检索文件，返回命中文件的路径、名称与内容预览。"""
        keywords = [k for k in re.split(r"[\s,，、;；]+", query.strip()) if k]
        hits: list[dict] = []
        if not settings.raw_dir.exists():
            return {"ok": True, "files": [], "total": 0}
        for p in sorted(settings.raw_dir.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(settings.raw_dir).as_posix()
            snippet = self._read_snippet(p)
            # 支持按文件名或文本内容命中（大小写不敏感）
            hay = f"{p.name}\n{snippet}".lower()
            if keywords and not any(k.lower() in hay for k in keywords):
                continue
            info = {"path": rel, "name": p.name, "dir": PurePosixPath(rel).parent.as_posix(),
                    "snippet": snippet}
            hits.append(info)
            if len(hits) >= limit:
                break
        return {"ok": True, "files": hits, "total": len(hits)}

    @staticmethod
    def _read_snippet(p: Path) -> str:
        if p.suffix.lower() not in IMPORT_READ_EXT or p.stat().st_size > 200_000:
            return ""
        try:
            return p.read_text(encoding="utf-8", errors="ignore")[:IMPORT_SNIPPET_CHARS]
        except Exception:
            return ""

    # ================= 生成 md（默认 ./input/） =================
    def generate_md(self, db: Session, content: str, title: str | None = None, target_subpath: str | None = None) -> dict:
        root = settings.input_dir.resolve()
        root.mkdir(parents=True, exist_ok=True)
        name = _safe_basename(title) if title else datetime.now().strftime("%Y%m%d_%H%M%S")
        sub = target_subpath or ""
        if sub:
            sub = sub.replace("\\", "/").lstrip("/")
        rel = (PurePosixPath(sub) / f"{name}.md").as_posix() if sub else f"{name}.md"
        target = (root / rel).resolve()
        # 防路径穿越：目标必须落在 input_dir 之内
        if not str(target).startswith(str(root)):
            raise ValueError("不允许的路径（防路径穿越拦截）：目标超出默认目录")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        log.info("已生成 md：%s", target)
        return {"ok": True, "path": target.relative_to(settings.project_root).as_posix()
                if str(target).startswith(str(settings.project_root)) else str(target),
                "abs_path": str(target)}


def _safe_basename(title: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\s]+", "_", title.strip())
    return cleaned.strip("_") or "note"


def _parse_json_obj(raw: str) -> dict:
    """容错解析结构化 JSON：去除围栏代码块与前后缀，必要时剥离首个 JSON 对象。"""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    # 若模型混入解释文字，截取第一个 { 到最后一个 }
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end >= start:
            try:
                return json.loads(text[start : end + 1])
            except (TypeError, ValueError):
                pass
        raise ValueError(f"无法解析为 JSON：{raw[:120]!r}")


def _iso(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""


def _dump_json(obj) -> str | None:
    try:
        return json.dumps(obj, ensure_ascii=False) if obj else None
    except (TypeError, ValueError):
        return None


def _load_json(raw) -> list | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None