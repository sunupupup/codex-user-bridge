"""Codex 启动的 stdio 子进程；stdout 只承载 MCP 协议。"""

import os
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from codex_user_bridge.adapters import ChannelAdapter, LocalHttpAdapter

mcp = FastMCP('user_bridge_mcp')
adapter: ChannelAdapter = LocalHttpAdapter(os.environ.get('BRIDGE_BASE_URL', 'http://127.0.0.1:5555'))
Identifier = Annotated[str, Field(min_length=1, max_length=200, pattern=r'^\S(?:.*\S)?$')]


class Reply(BaseModel):
    text: str
    source: Literal['local_mock']
    is_simulated: Literal[True]
    answered_at: float


class Question(BaseModel):
    question_id: str
    task_id: str
    message: str
    options: list[str]
    created_at: float
    status: Literal['pending', 'answered']
    reply: Reply | None
    is_simulated: Literal[True]
    poll_after_seconds: int


@mcp.tool(annotations={'readOnlyHint': False, 'destructiveHint': False,
                       'idempotentHint': True, 'openWorldHint': False})
async def bridge_send_question(
    task_id: Identifier,
    request_id: Identifier,
    message: Annotated[str, Field(min_length=1, max_length=4000)],
    options: Annotated[list[Annotated[str, Field(min_length=1, max_length=200)]],
                       Field(max_length=10)] = [],
) -> Question:
    """发送问题到本地模拟渠道，立即返回 question_id，不等待回答。

    task_id 是调用者为当前任务选择的稳定标识，并非自动获取的 Codex thread ID。
    request_id 是当前问题的稳定标识；重试相同问题必须复用，避免重复通知。
    options 为可选答案。返回值 is_simulated=true；本工具不取得真实用户授权。
    """
    return Question.model_validate(await adapter.send(task_id, request_id, message, options))


@mcp.tool(annotations={'readOnlyHint': True, 'destructiveHint': False,
                       'idempotentHint': True, 'openWorldHint': False})
async def bridge_get_reply(task_id: Identifier, question_id: Identifier) -> Question:
    """主动查询一个问题，立即返回 pending 或 answered，不等待、不消费回答。

    使用发送时相同的 task_id 和返回的 question_id；在独立工作阶段结束时查询。
    local_mock 回答只是模拟数据，不能据此使用真实 Key、花钱或执行需授权操作。
    本工具不会定时调用自身，也不会向聊天插入 user message。
    """
    return Question.model_validate(await adapter.get_reply(task_id, question_id))


def main():
    """命令行入口与插件入口共用同一个 stdio 服务。"""
    mcp.run(transport='stdio')


if __name__ == '__main__':
    main()
