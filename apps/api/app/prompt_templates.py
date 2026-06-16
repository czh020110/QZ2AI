# RAG 提示词模板（阶段三完善）。
# 对召回上下文与用户问题做明确边界分隔，不让外部输入直接拼成可执行指令。

SYSTEM_PROMPT = (
    "你是博客的 AI 助手，只依据下方提供的博客内容片段回答问题。"
    "若内容不足以回答，请如实说明，不要编造。回答末尾会附上引用来源。"
)


def build_messages(question: str, contexts: list[dict], history: list | None = None) -> list[dict]:
    """把系统提示 + 召回上下文 + 历史 + 用户问题拼成 OpenAI 格式 messages。"""
    context_block = "\n\n".join(
        f"[来源:{c.get('title', '')}]\n{c.get('text', '')}" for c in contexts
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": f"以下是检索到的博客内容片段：\n{context_block}"},
    ]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": question})
    return messages
