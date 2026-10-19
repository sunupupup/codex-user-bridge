# 本地用户通信插件设计

用户已在聊天确认实施：先使用本地 5555 服务，不接飞书、Telegram，不处理系统审批。

## 范围与用户故事

1. Agent 遇到需要用户决定的问题，发送后立即获得问题编号，继续独立工作。
2. 本地服务每 3 秒检查待回答问题，为达到等待时间的问题生成默认模拟回答。
3. Agent 在阶段边界主动查询，得到 pending 或 answered；查询不消费回答。
4. 多问题、重复发送、服务重启不能导致串答或静默丢失已接受的问题。
5. 服务不可用时，工具明确报错，不伪装消息已经送达，不自动重发未知结果。

## 设计与计划

- Python 3.11+，HTTP 与 SQLite 使用标准库，MCP 使用固定版本官方 SDK 1.30.0。
- Codex → MCP stdio 工具 → LocalHttpAdapter → 127.0.0.1:5555 → SQLite。
- 两个工具：bridge_send_question、bridge_get_reply；发送携带 task_id、request_id。
- Adapter 统一 send/get_reply；服务内部计时线程生成模拟回复，模型调用查询工具是另一层调度。
- SQLite 唯一键 (task_id, request_id)，同键不同内容返回冲突；跨 task 查询返回未找到。
- 回答带 source=local_mock、is_simulated=true，始终不能作为真实用户授权或花费许可。
- 插件根目录包含兼容格式 manifest、MCP 配置和 Skill；源码位于 src/codex_user_bridge。发布版 `.mcp.json` 使用相对 cwd，由宿主解析为安装目录，不含开发机绝对路径；uv 按锁文件准备依赖。
- 按顺序实现服务与持久化、Adapter 与 MCP、启动与插件配置、端到端测试与运行说明。

## 验收边界

真实 HTTP 服务与 MCP 子进程必须跑通；不调用模型、不用密钥、不产生模型费用。
插件安装与宿主中模型自动调用是独立验证，不用协议测试代替。
不承诺后台唤醒 Goal、定时触发模型，或将回答插入 user message。
