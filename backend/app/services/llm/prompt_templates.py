"""提示词模板集中管理（实体/关系抽取、问答等）。"""
from __future__ import annotations

ENTITY_EXTRACT_SYSTEM = (
    "你是一个知识图谱构建助手。请从用户给定的一段或多段文本中抽取确定性实体与实体间关系，"
    "以 JSON 对象输出（不要 markdown 代码块），格式如下：\n"
    '{"entities":[{"name":"实体名","entity_type":"概念|人物|组织|地点|文档|技术",'
    '"description":"一句话描述"}],"relations":[{"source":"实体名","target":"实体名",'
    '"relation_type":"关系类型(如 包含/依赖/属于/相关/因果/提出/用于)","description":"关系描述"}]}\n'
    "要求：\n"
    "1. 实体名用词凝练，同义概念统一命名（便于后续向量合并去重）。\n"
    "2. 只抽取文本中明确出现的实体，不要臆造。\n"
    "3. 每条关系 source/target 必须在 entities.name 中存在。\n"
    "4. entities 至少 1 个；若确实无可抽取内容，输出 {\"entities\":[],\"relations\":[]}。\n"
    "5. 全中文实体直接输出中文名，不要翻译。"
)


def extract_entities_user_prompt(text: str) -> list[dict]:
    """构建抽取提示词（用户消息）。text 为笔记内容（可含多个分块拼接）。"""
    return [
        {"role": "system", "content": ENTITY_EXTRACT_SYSTEM},
        {"role": "user", "content": f"请抽取以下文本中的实体与关系：\n\n{text}\n"}
    ]


def search_answer_system() -> str:
    return (
        "你是一个基于知识库与知识图谱的第二大脑助手。回答时优先基于给定的上下文，"
        "并结合知识图谱关系。若上下文不足以回答，如实说明。引用来源用 [来源N] 标注。"
    )