# 个人微信（ClawBot / iLink）

此渠道连接腾讯公开的 ClawBot/iLink 机器人，不是企业微信 Webhook，也不会控制普通微信好友或群聊。不需要安装 OpenClaw。你的微信账号是否能扫码绑定，以手机端实际结果为准。

## 1. 扫码登录

在仓库根目录运行（Python 3.11+、uv）：

```powershell
uv sync --frozen
uv run --frozen python scripts/weixin_setup.py
```

手机微信扫描终端二维码，按手机提示确认；需要配对验证码时在终端输入。过期可重新运行。登录后凭据仅保存在 `.local/weixin-account.json`，目录已被 Git 忽略。不要分享二维码、凭据或该目录。自定义路径可用 `WEIXIN_CREDENTIALS`，登录助手和服务必须使用同一路径。

## 2. 启动微信服务

先停止占用 5555 的旧服务，再运行：

```powershell
uv run --frozen --env-file .env python scripts/serve.py --channel weixin --db .local/weixin.sqlite3
```

没有 `.env` 时可去掉 `--env-file .env`；微信默认直接读扫码凭据文件。服务启动后，在手机给该机器人发一条“你好”，建立会话上下文。普通聊天不会自动成为某个问题的答案。

## 3. 从 Codex 测试

更新插件后在新聊天中说：“通过 user bridge 问我是否继续这次测试，稍后查询回答。”应返回 `channel: weixin`。在手机复制通知里的命令，补上回答：

```text
/reply q_通知里的32位问题编号 继续
```

请使用通知中的完整真实编号。让 Codex 查询同一问题，应得到 `answered`、`reply.source: weixin`、`is_simulated: false`。本版只支持编号关联的文本回复，不支持引用回复、语音或图片。

## 状态与限制

- `queued` 且提示等待上下文：先给机器人发消息，随后自动尝试发送。
- 微信 `-2`：原因可能涉及上下文或平台策略；清除旧上下文并等待新的手机入站消息后再尝试，不用旧 token 循环重试。
- `-14` 登录失效：停止服务、重新扫码、再启动；新问题保留队列，已经 failed 的问题不会自动重发。
- `unknown`：平台可能已接收，但本地没收到确认；不会自动重发。不要立刻换编号重复通知。
- 接收采用最长约 35 秒的平台长轮询，通知可能需要等待本轮返回；退出也可能等待在途请求结束。
- 同一账号只运行一个接收进程，不要与另一个 OpenClaw 实例争抢消息。换账号请使用新的数据库。

协议依据为腾讯维护的 [openclaw-weixin 协议与源码](https://github.com/Tencent/openclaw-weixin/blob/main/docs/protocol_zh_CN.md)。该文档也说明客户端字段不等于完整服务端契约。会话上下文有效期没有在本插件中写死；[上游用户反馈](https://github.com/Tencent/openclaw-weixin/issues/309)显示长期无人发言后的通知可能被拒绝，具体窗口仍需你的账号实测。版本号和 bot_agent 使用本插件自身版本，没有冒充 OpenClaw。

本地已覆盖模拟平台 HTTP、消息去重、身份绑定、会话失效、登录文件与真实 MCP 子进程。尚未使用真实个人微信账号联调，不能据此保证无人值守的长期通知可靠性。
