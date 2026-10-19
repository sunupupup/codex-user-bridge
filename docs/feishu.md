# 飞书应用机器人：逐步联调

仅支持自建应用的用户私聊，不是群 Webhook 自定义机器人。当前代码使用模拟 API 和官方 SDK 数据模型验证，真实账号待你测试。

1. 在[飞书开发者后台](https://open.feishu.cn/app)创建企业自建应用，开启机器人能力，记录 App ID、App Secret，把自己纳入可用范围。
2. 开通以应用身份发消息 `im:message:send_as_bot`，以及读取发给机器人的单聊消息 `im:message.p2p_msg:readonly`（以后台当前对应名称为准）。订阅接收消息 `im.message.receive_v1`。按组织要求创建版本、发布和审批。
3. 在本机 .env 配置 FEISHU_APP_ID、FEISHU_APP_SECRET，不要覆盖已有 .env，不要把凭据发到聊天或写进 manifest。
4. 启动下面的助手，在后台“事件与回调”选择长连接接收并保存。若提示未连接，检查应用凭据与网络。SDK 长连接默认直连，不保证支持系统代理。

```powershell
uv sync --frozen
uv run --frozen --env-file .env python scripts/feishu_setup.py
```

5. 给机器人发私聊，助手显示 open_id；确认是你自己后填入 FEISHU_OPEN_ID。Ctrl+C 停止助手，避免多个长连接实例分流消息。
6. 停止占用 5555 的其他渠道服务，启动飞书，建议单独数据库：

```powershell
uv run --frozen --env-file .env python scripts/serve.py --channel feishu --db .local/feishu.sqlite3
```

7. 从当前源码刷新插件后开新 Codex 聊天，用 bridge_send_question 发问题。手机引用该问题回复，或发送 `/reply q_完整编号 回答`。
8. 调用 bridge_get_reply，预期 status=answered、reply.source=feishu、sender_id 为你的 open_id、is_simulated=false。

queued 是本地排队；sent 仅表示平台接受，不代表已读。其他用户、其他 App、群聊和无关联文字不会生成回答。以 message_id 去重，首个有效回答生效。语音、图片和卡片暂不支持。

REST 超时保留 unknown，不自动重发；HTTP 429 保存冷却。平台明确拒绝时检查权限、可用范围和发布状态。换接收者使用新 --db。相同 App 只运行一个接收实例。

官方 lark-oapi SDK 在可终止的子进程运行，父进程先保存事件再确认；长连接进程退出时会记录脱敏错误，请检查配置网络并重启服务。正常 Ctrl+C 关闭接收进程。

参考：[飞书开放平台](https://open.feishu.cn/document/)。
