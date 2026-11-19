# -*- coding: utf-8 -*-
"""上下文窗口管理：超长会话按 token 估算截断，避免把 provider 上下文撑爆。

纯壳逻辑：只依赖消息的 role/content 形状，不 import 任何 provider 实现，
由编排层在透传前调用。当前实现：保留 system + 最早一条 user（主题锚点）+ 最近消息。
真正的摘要式压缩留待 Agent 能力阶段（需 provider 生成摘要，见调研）。
"""
from __future__ import annotations

import re
from collections.abc import Sequence

# 默认上下文预算（token）：桌面单机场景偏保守，可按需由调用方覆盖
DEFAULT_MAX_TOKENS = 8000
# 主题锚点内容上限（token）：防止超长首条消息（如粘贴大段代码）吃掉整个预算
_ANCHOR_MAX_TOKENS = 2000

_CJK = re.compile(r"[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]")


def estimate_tokens(text: str) -> int:
    """粗略 token 估算：CJK 按 1 字 1 token，其余按 4 字符 1 token。"""
    if not text:
        return 0
    cjk = len(_CJK.findall(text))
    other = len(text) - cjk
    return cjk + max(1, other // 4)


def _message_text(content: object) -> str:
    """把 content（字符串或多模态 parts 数组）归一为纯文本用于估算。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text") or ""))
        return "\n".join(parts)
    return ""


def _message_tokens(message: dict[str, object]) -> int:
    content = _message_text(message.get("content"))
    return estimate_tokens(content) + 4  # 每条约 4 token 的角色/结构开销


def _truncate_text(text: str, max_tokens: int) -> str:
    """按 token 预算截断文本（保留开头，末尾追加省略号）。"""
    if estimate_tokens(text) <= max_tokens:
        return text
    out: list[str] = []
    cjk = 0
    other = 0
    for ch in text:
        if _CJK.match(ch):
            cjk += 1
        else:
            other += 1
        # 保守预留 8 token 给省略号
        if cjk + (other // 4) + 8 > max_tokens:
            break
        out.append(ch)
    return "".join(out) + " …"


def trim_messages(
    messages: Sequence[dict[str, object]],
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> list[dict[str, object]]:
    """截断历史：保留 system + 最早一条 user（主题锚点）+ 最近消息，使总量不超预算。

    主题锚点让长会话被截断后仍保留对话起点（任务/主题），避免完全失忆；
    始终至少保留最后一条（最新一轮）消息，保证对话能继续。
    """
    if not messages:
        return []
    system = [m for m in messages if m.get("role") == "system"]
    rest = [m for m in messages if m.get("role") != "system"]

    budget = max_tokens - sum(_message_tokens(m) for m in system)
    if budget <= 0:
        # system 已占满预算：只留最新一条，保证仍可继续对话
        return system + rest[-1:]

    # 主题锚点：最早一条非空 user 消息，内容限长避免吃掉预算
    anchor: dict[str, object] | None = None
    anchor_index = -1
    for i, m in enumerate(rest):
        if m.get("role") != "user":
            continue
        content = _message_text(m.get("content"))
        if content.strip():
            anchor = {**m, "content": _truncate_text(content, _ANCHOR_MAX_TOKENS)}
            anchor_index = i
            break

    used = 0
    kept: list[dict[str, object]] = []
    if anchor is not None:
        cost = _message_tokens(anchor)
        if cost < budget:
            kept.append(anchor)
            used += cost

    # 最近消息：从最新往回装，跳过已被锚点占用的那条
    recent: list[dict[str, object]] = []
    for i in range(len(rest) - 1, -1, -1):
        if i == anchor_index:
            continue
        message = rest[i]
        cost = _message_tokens(message)
        if recent and used + cost > budget:
            break
        recent.append(message)
        used += cost
    recent.reverse()
    return system + kept + recent
