"""基于 LangGraph 的 AI 助手编排图（阶段四改版，FR-07/FR-08）。

用 LangGraph StateGraph 做「结构化规划 + 工具调用」编排：

- `plan` 节点：以结构化输出（合法 JSON）判定是否需要检索知识库/图谱（need_knowledge）、
  是否要查找导入区原始文件（need_import），并给出检索 query 列表。
  **门控**：导入区 / need_import 仅在用户明确要求以导入区文件为依据或查找导入区文件时置真，
  由系统提示词约束模型（工具调用门控），从而保证"导入区内容只有用户要求时才被使用"。
- `knowledge` 节点：调用混合检索（知识库 + 图谱），产出上下文块与引用清单。
- `import` 节点：列出导入区实时目录（文件+位置），并按规划 query 检索文件内容。

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
    plan: dict            # 结构化规划输出 {need_knowledge, need_import, queries, reason}
    context_blocks: list[str]  # 知识库/图谱检索得到的上下文块
    citations: list[dict]      # 引用溯源清单
    catalog_summary: str       # 导入区实时目录
    import_snippets: list[str] # 导入区命中文件片段


class AssistantWorkflow:
    """规划（结构化）→ 检索知识库/图谱 → 查找导入区 的编排图。"""

    NODE_ALIAS = {
        "plan": "🧠 正在理解你的问题…",
        "knowledge": "🔍 检索知识库与图谱…",
        "import": "📂 查找导入区文件…",
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

    # ---------- 条件路由 ----------
    def _route_plan(self, state: AgentState) -> str:
        plan = state.get("plan") or {}
        if plan.get("need_knowledge"):
            return "knowledge"
        if plan.get("need_import"):
            return "import"
        return END

    def _route_knowledge(self, state: AgentState) -> str:
        plan = state.get("plan") or {}
        return "import" if plan.get("need_import") else END

    def _build(self):
        g = StateGraph(AgentState)
        g.add_node("plan", self._node_plan)
        g.add_node("knowledge", self._node_knowledge)
        g.add_node("import", self._node_import)
        g.set_entry_point("plan")
        g.add_conditional_edges("plan", self._route_plan, {"knowledge": "knowledge", "import": "import", END: END})
        g.add_conditional_edges("knowledge", self._route_knowledge, {"import": "import", END: END})
        return g.compile()

    def steps(self, state: AgentState):
        """逐步执行，产出 (节点名, 本次更新 dict)，供 SSE 展示工具进度并累积最终状态。"""
        for step in self._graph.stream(state, stream_mode="updates"):
            for node_id, update in step.items():
                if node_id != END:
                    yield node_id, update