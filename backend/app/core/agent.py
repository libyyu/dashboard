# -*- coding: utf-8 -*-
"""模型智能体端口：为 MCP / Skill / 多智能体预留扩展点，本轮只定契约。

扩展路径（都不需要改 Agent 调用方）：
- MCP：由 MCPToolAdapter 实现 ToolRegistry，把远端 tools 映射成 ToolSpec
- Skill：由 SkillRegistry 提供 instructions + 工具白名单，注入到 AgentContext
- 多智能体：由 Orchestrator 组合多个 Agent，对外仍是同一个 run() 事件流
- 模型：由 ModelProvider 适配供应商；当前唯一实现是包内 cb 的 OpenAI 兼容层

车道约定：推理走 STREAM（/stream，不占 REST inflight），取消走 CONTROL
（/cancel，永不 shed）；凭证只在服务端内存/登录态中，绝不进日志或前端。
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str
    name: str = ""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, object] = field(default_factory=dict)
    source: str = "builtin"  # builtin | mcp | skill


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    instructions: str
    tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentContext:
    session_id: str
    run_id: str
    trace_id: str
    request_id: str = ""
    profile: str = "default"
    skills: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentEvent:
    """统一的流式事件；前端只消费这一种形状，不依赖供应商协议。"""

    type: str  # message | delta | tool_call | tool_result | error | done
    run_id: str
    trace_id: str
    request_id: str = ""
    payload: dict[str, object] = field(default_factory=dict)


class ModelProvider(Protocol):
    """当前适配器规划：CBOpenAIProvider（包内 cb 的 /v1/*）。"""

    def list_models(self) -> Sequence[str]: ...
    async def stream(
        self,
        model: str,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSpec] = (),
    ) -> AsyncIterator[AgentEvent]: ...
    async def cancel(self, run_id: str) -> None: ...


class ToolRegistry(Protocol):
    """MCP 接入点：MCPToolAdapter 实现本接口即可，Agent 无感知。"""

    def list_tools(self) -> Sequence[ToolSpec]: ...
    def register(self, spec: ToolSpec) -> None: ...
    async def invoke(self, name: str, arguments: dict[str, object],
                     ctx: AgentContext) -> dict[str, object]: ...


class SkillRegistry(Protocol):
    def list_skills(self) -> Sequence[Skill]: ...
    def resolve(self, name: str) -> Skill | None: ...


class SessionStore(Protocol):
    """会话事件持久化；实现必须走 core.storage.Storage，不直接写库。"""

    def append_event(self, session_id: str, event: AgentEvent) -> None: ...
    def read_events(self, session_id: str, limit: int = 200) -> Sequence[AgentEvent]: ...


class Agent(Protocol):
    @property
    def name(self) -> str: ...
    async def run(self, ctx: AgentContext,
                  messages: Sequence[ChatMessage]) -> AsyncIterator[AgentEvent]: ...
    async def cancel(self, run_id: str) -> None: ...


class Orchestrator(Protocol):
    """多智能体扩展点：组合 Agent，对外仍是同一事件流。"""

    def add_agent(self, agent: Agent) -> None: ...
    async def run(self, ctx: AgentContext,
                  messages: Sequence[ChatMessage]) -> AsyncIterator[AgentEvent]: ...
