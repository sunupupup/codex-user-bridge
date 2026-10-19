# Telegram 私聊使用与验收

状态：0.3.0 已实现，模拟 API 验证通过；尚无真实 Bot 凭据，不代表手机收发已经联调。

## 1. 创建自己的 Bot

在 Telegram 打开官方 **@BotFather**，发送 `/newbot`，按提示创建，取得 Bot Token。
不要把 Token 发到聊天或写进 .mcp.json；不要与其他应用共用这个 Bot。
在 Telegram 打开新 Bot，先发送 `/start`。

## 2. 配置本地凭据

在项目根目录执行一次（已有 .env 时不要覆盖）：

```powershell
Copy-Item .env.example .env
```

编辑 .env，先填写 TELEGRAM_BOT_TOKEN。停止使用此 Bot 的其他接收进程，再查询私聊标识：

```powershell
uv run --frozen --env-file .env python scripts/telegram_setup.py
```

助手只读取 Bot 身份和消息里的 chat_id/user_id，不输出 Token 或消息文本，不主动发消息。
把你自己的正整数 ID 填入 TELEGRAM_CHAT_ID；TELEGRAM_USER_ID 留空时等于 CHAT_ID。此版本只支持个人私聊，不支持群聊。
如果多个候选 ID，必须选你自己的；不要直接选择陌生人。没有候选时，先给 Bot 发 /start 再查询。

## 3. 启动 Telegram 渠道

如果旧版模拟服务已占用 5555，请在原终端 Ctrl+C 停止，不要结束未知进程。

```powershell
uv run --frozen --env-file .env python scripts/serve.py --channel telegram
```

默认仍使用本地 5555 接口，数据库在 .local/messages.sqlite3；此模式不生成模拟回答。
服务启动检查 Token 和已有 webhook；存在 webhook 时停止，不会擅自删除。
需要代理时，可在本地 .env 配置标准 HTTPS_PROXY；实际网络可达性仍需本机验证。

MCP 不需要 Token。它只访问本机服务；常驻服务负责发送和接收，多个 Codex 聊天共用这一个进程。
更新源码后按 README 从当前本地目录重新安装插件，并开启新聊天；不要同时启用旧 agent-practice 和新 codex-user-bridge 两份同名工具。

## 4. 在手机上回复

Codex 发送后会获得 question_id；Telegram 稍后收到带问题编号、任务标识、正文和候选答案的消息。

任选一种方式：

1. 在 Telegram **引用该问题消息**，输入回答。
2. 发送 `/reply q_问题的完整编号 回答内容`，实际编号由通知给出。

无关联的普通文字不猜测对应问题；语音、图片、按钮、编辑消息暂不处理。首个有效文本回答生效，之后不覆盖。
候选项先以文字列表展示，回复文字原样保存，不自动执行任何业务操作。

## 5. Codex 主动查询

调用 bridge_get_reply，使用发送时 task_id 和返回的 question_id。

| 字段 | 含义 |
|---|---|
| status=pending / answered | 是否已取得对应用户回答 |
| delivery_status=queued / sending | 本地排队／正在尝试发送，不能宣称已通知 |
| delivery_status=sent | Telegram 接受了发送请求，不代表用户已读 |
| delivery_status=failed | 平台明确拒绝或问题太长，查看脱敏 delivery_error |
| delivery_status=unknown | 网络或进程异常，结果未知，不会自动重发 |
| reply.source=telegram | 来自所绑定私聊用户的回答 |
| reply.sender_id | 已校验的回答者 ID |
| is_simulated=false | 当前是真实渠道；不代表发送已经成功或用户已经回答 |

只有 reply 非空且 status=answered 才有回答。即使是真人回复，也只适用于原问题范围，不是其他操作或系统权限的通用授权。

## 失败与恢复

- 同一问题重试使用相同 request_id，不重复创建或发送；不同内容必须新编号。
- 网络超时、5xx 或发送中退出：保留 unknown，不自动重发。可先在手机确认是否收到；已收到可用 `/reply 问题编号 回答`。确需再发时由人明确决定新建问题。
- 429 限流：保存 retry_after 冷却时间，到期后才允许继续发送；冷却在重启后仍生效。
- 问题过长：明确失败，不截断关键决定或金额。缩短后用新 request_id。
- 回答和 getUpdates 游标同一事务落盘；重启继续接收，重复更新不覆盖答案。
- 每个数据库绑定一个 Bot 和接收者，换账号使用新的 --db；防止旧问题发送给新账号。
- 同一数据库租约阻止重复消费者；异常退出可能要等最多约 45 秒。不能跨不同机器或不同数据库约束相同 Bot，请不要共用。
- Bot API 的待接收更新不是永久存储；长时间停机可能错过回答，实际保留限制以官方文档为准。

## 验证

```powershell
uv run --frozen python -m unittest discover -s tests -v
```

21 项检查包括原有 10 项以及 Telegram 引用回复、编号路由、身份校验、幂等、持久化游标、旧库升级、发送未知、限流冷却、Webhook 冲突、单消费者与超长文本。
其中端到端测试实际启动 MCP 子进程、本地 HTTP 服务和模拟 Telegram Bot API；模拟平台输入的测试消息不是真实用户回答。

真实验收仍需：手机收到问题 → 引用回复 → Codex 查询到同一问题的文本和 source=telegram。当前未执行这一步。

参考：[BotFather 与 Bot 创建](https://core.telegram.org/bots/tutorial) · [Telegram Bot API](https://core.telegram.org/bots/api)。
