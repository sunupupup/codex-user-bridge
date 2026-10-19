# 三个渠道逐个测试

先在仓库根目录 `git pull`、`uv sync --frozen`，按各渠道说明配置账号。每次只启动一个 5555 服务，切换前 Ctrl+C 停止旧服务；各用一个数据库，避免测试数据混淆。

| 渠道 | 准备与说明 | 启动参数（接在 `python scripts/serve.py` 后） |
| --- | --- | --- |
| Telegram | [创建 Bot、配置私聊 ID](./telegram.md) | `--channel telegram --db .local/telegram.sqlite3` |
| 飞书 | [应用、权限、open_id、长连接](./feishu.md) | `--channel feishu --db .local/feishu.sqlite3` |
| 个人微信 | [扫码登录，启动后先发“你好”](./weixin.md) | `--channel weixin --db .local/weixin.sqlite3` |

完整启动示例：

```powershell
uv run --frozen --env-file .env python scripts/serve.py --channel telegram --db .local/telegram.sqlite3
```

如果插件来自本地克隆，可在停止旧聊天后刷新安装（需要 Codex CLI）：

```powershell
codex plugin remove codex-user-bridge@codex-user-bridge
codex plugin marketplace remove codex-user-bridge
codex plugin marketplace add .
codex plugin add codex-user-bridge@codex-user-bridge
```

如果已经安装的是最新版本可跳过；刷新后使用新聊天，避免旧 MCP 进程仍保留旧版结果类型。这个流程需在本仓库根目录执行。

每个渠道使用相同的检查顺序：

1. 让 Codex 调用 `bridge_send_question`，确认返回的 `channel` 是本次渠道，并保存问题编号。
2. 手机上确认收到问题。`queued` 只是排队；查询到 `sent` 才表示平台接受，并不表示手机已读。
3. 用通知里的 `/reply 问题编号 回答` 回复（Telegram、飞书还支持引用回复）。
4. 让 Codex 调用 `bridge_get_reply` 查询同一问题，确认 `answered`、正确的 `reply.source`、`is_simulated: false`。
5. 重复查询仍是同一回答；额外随便发“你好”，不应串答到另一问题。

此插件通过工具返回回答，没有直接插入 user message，也不保证 Goal 一定自动定时查询。测试时明确要求“发送后继续独立工作，在下一阶段结束时查询回答”。只有真实平台的手机收发成功，才能算该渠道联调完成。
