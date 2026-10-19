# Telegram 文本私聊渠道

状态：已实现并通过模拟 API 验证。真实 Bot/手机联调待用户配置。用户已明确启动 Telegram 实现，不接飞书、微信。

- 保持两个 MCP 工具，经本地 HTTP 服务写问题、查回复；发送先持久化为 queued，后台进程调用 Telegram。
- 默认 local_mock 不变；`--channel telegram` 启用真实渠道，关闭模拟自动回答。
- 仅接收配置私聊 chat_id / user_id 的文本；引用原消息，或 `/reply q_xxx 回答` 关联问题。普通无关联文本不猜测对应问题。
- 使用 Bot API sendMessage、getUpdates 长轮询；启动检查 getMe、getWebhookInfo，不自动删除已有 webhook。
- SQLite 迁移兼容旧库，保存渠道、送达状态、Telegram message_id 和更新游标；回答与游标同事务落盘。首个有效回答生效，后续不覆盖。
- 网络发送超时/崩溃保留 unknown，不自动重发；明确 429 限流可重新入队。queued/sent 不等于用户已读。
- 单数据库进程租约防止重复消费者；不同机器使用同一 Bot 仍需用户避免，不承诺跨机器排他。
- 凭据仅环境变量，.env.example 无真实值；日志与错误不输出含 Token 的请求 URL。
- 验收：模拟 Telegram HTTP API + 本地服务 + 真实 MCP 子进程，检查发送、引用回复、编号回复、身份隔离、幂等、游标恢复、未知结果与 Token 脱敏。
- 无凭据时不宣称真实 Telegram 已联调；不自动提交或推送。
