"""基于 LangGraph 的 AI 助手编排图（阶段四改版，FR-07/FR-08）。

用 LangGraph StateGraph 做「结构化规划 + 工具调用」编排：

- `plan` 节点：以结构化输出（合法 JSON）判定是否需要检索知识库/图谱（need_knowledge）、
  是否要查找导入区原始文件（need_import）、是否要在导入区创建/保存文档（need_create_doc），
  并给出检索 query 列表。
  **门控**：
  - 导入区 / need_import 仅在用户明确要求以导入区文件为依据或查找导入区文件时置真；
  - need_create_doc 仅在用户**明确表示**要在程序导入区（raw/）创建/保存一个文档时才置真，
    由系统提示词约束模型（工具调用门控），保证"只有用户明确要在导入区生成文件时才会调用写文档工具"，而非随意生成。
- `knowledge` 节点：调用混合检索（知识库 + 图谱），产出上下文块与引用清单。
- `import` 节点：列出导入区实时目录（文件+位置），并按规划 query 检索文件内容。
- `create_doc` 节点：每当 need_create_doc 为真时调用，由 LLM 据此前的上下文生成 Markdown 文档并
  落盘到导入区（默认 raw/output/，用户指定且已存在的子路径则落该处，否则回退 output/）；结果存入 gen_doc。

答案的 tokening 逐字流式由 `assistant_service.stream_chat` 消费本图的最终状态完成；
本图只负责"决定并采集要用的资料"。任何失败优雅降级为空上下文，不阻断对话。
"""
from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, StateGraph

from app.core.logging import get_logger

log = get_logger("services.assistant_workflow")


class AgentState(TypedDict, total=False):
    message: str          # 当前用户问题
    history: list[dict]   # 最近对话历史（user/assistant）
    plan: dict            # 结构化规划输出 {need_knowledge, need_import, need_create_doc, queries, reason}
    context_blocks: list[str]  # 知识库/图谱检索得到的上下文块
    citations: list[dict]      # 引用溯源清单
    catalog_summary: str       # 导入区实时目录
    import_snippets: list[str] # 导入区命中文件片段
    gen_doc: dict | None       # 写文档工具结果 {ok, path, abs_path, title, reason?}


class AssistantWorkflow:
    """规划（结构化）→ 检索知识库/图谱 → 查找导入区 的编排图。"""

    NODE_ALIAS = {
        "plan": "🧠 正在理解你的问题…",
        "knowledge": "🔍 检索知识库与图谱…",
        "import": "📂 查找导入区文件…",
        "create_doc": "📝 正在导入区生成文档…",
    }

    def __init__(self, svc, db, top_k: int = 6):
        self.svc = svc
        self.db = db
        self.top_k = top_k
        self._graph = self._build()

    # ---------- 节点 ----------
    def _node_plan(self, state: AgentState) -> dict:
        return {"plan": self.svc._structured_plan(self.db, state["message"], state.get("history", []))}

    def _node_knowledge(self, state: AgentState) -> dict:
        plan = state.get("plan") or {}
        queries: list[str] = plan.get("queries") or [state["message"]]
        blocks: list[str] = []
        cites: list[dict] = []
        for q in queries[:2]:
            b, c = self.svc._build_context(self.db, q, self.top_k)
            blocks.extend(b)
            cites.extend(c)
        return {"context_blocks": blocks, "citations": cites}

    def _node_import(self, state: AgentState) -> dict:
        plan = state.get("plan") or {}
        queries: list[str] = plan.get("queries") or [state["message"]]
        catalog = self.svc._import_catalog(self.db)
        snippets: list[str] = []
        for q in queries[:2]:
            res = self.svc.retrieve_import(self.db, q, limit=5)
            for f in res.get("files", []):
                line = f"- **{f['name']}**（{f['dir'] or '.'}/，路径 `{f['path']}`）"
                if f.get("snippet"):
                    line += f"\n{f['snippet']}"
                snippets.append(line)
        combined = catalog
        if snippets:
            combined += "\n\n【命中文件内容】\n" + "\n\n".join(snippets)
        return {"catalog_summary": combined, "import_snippets": snippets}

    def _node_create_doc(self, state: AgentState) -> dict:
        """写文档工具：仅当用户明确要求在导入区创建/保存文档时由路由触发。"""
        result = self.svc._create_doc(self.db, **{
            "message": state["message"],
            "history": state.get("history", []),
            "context_blocks": state.get("context_blocks", []),
            "import_snippets": state.get("import_snippets", []),
        })
        return {"gen_doc": result}

    # ---------- 条件路由 ----------
    def _route_plan(self, state: AgentState) -> str:
        plan = state.get("plan") or {}
        if plan.get("need_knowledge"):
            return "knowledge"
        if plan.get("need_import"):
            return "import"
        if plan.get("need_create_doc"):
            return "create_doc"
        return END

    def _route_knowledge(self, state: AgentState) -> str:
        plan = state.get("plan") or {}
        if plan.get("need_import"):
            return "import"
        if plan.get("need_create_doc"):
            return "create_doc"
        return END

    def _route_import(self, state: AgentState) -> str:
        plan = state.get("plan") or {}
        return "create_doc" if plan.get("need_create_doc") else END

    def _build(self):
        g = StateGraph(AgentState)
        g.add_node("plan", self._node_plan)
        g.add_node("knowledge", self._node_knowledge)
        g.add_node("import", self._node_import)
        g.add_node("create_doc", self._node_create_doc)
        g.set_entry_point("plan")
        g.add_conditional_edges("plan", self._route_plan,
                                {"knowledge": "knowledge", "import": "import", "create_doc": "create_doc", END: END})
        g.add_conditional_edges("knowledge", self._route_knowledge,
                                {"import": "import", "create_doc": "create_doc", END: END})
        g.add_conditional_edges("import", self._route_import, {"create_doc": "create_doc", END: END})
        return g.compile()

    def steps(self, state: AgentState):
        """逐步执行，产出 (节点名, 本次更新 dict)，供 SSE 展示工具进度并累积最终状态。"""
        for step in self._graph.stream(state, stream_mode="updates"):
            for node_id, update in step.items():
                if node_id != END:
                    yield node_id, update