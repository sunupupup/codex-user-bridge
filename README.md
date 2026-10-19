# Codex User Bridge

Codex 本地用户通信插件：遇到需要用户决定的问题时，发送后立即返回，继续独立工作，再主动查询回答。

**支持本地模拟、Telegram、飞书应用机器人和个人微信 ClawBot/iLink 文本问答。** 默认模拟模式每隔几秒生成回答；外部渠道发送通知并接收所绑定用户的文本回复。三个外部渠道已通过模拟 API 与 MCP 检查，真实账号/手机联调仍待你配置后测试。

Telegram 的 Bot 创建、.env 配置、私聊 ID 查询和启动步骤见 [Telegram 使用说明](./docs/telegram.md)。

飞书的应用、open_id 与长连接配置见 [飞书使用说明](./docs/feishu.md)。

个人微信的扫码登录和会话限制见 [微信使用说明](./docs/weixin.md)。三个渠道逐个测试见 [测试入口](./docs/channel-testing.md)。

## 项目结构

```text
codex-user-bridge/
├── .codex-plugin/plugin.json       # Codex 兼容插件清单
├── .mcp.json                       # MCP 启动配置，无开发机绝对路径
├── .agents/plugins/marketplace.json # 本地插件目录
├── skills/external-user-chat/      # 工具使用时机
├── src/codex_user_bridge/          # MCP 工具、Adapter、HTTP 服务与存储
├── scripts/                       # 安装准备、启动、演示与安装验证
├── tests/                         # 自动检查
├── docs/                          # 设计与验收记录
├── pyproject.toml                 # Python 包及命令入口
├── uv.lock                        # 锁定依赖
├── CHANGELOG.md
└── LICENSE
```

这是官方仍支持的 `.codex-plugin` 兼容布局。使用此布局是因为已在 Codex CLI 0.163.0-alpha.10.2 验证插件加载；没有宣称通过官方插件商店审核。

## 快速开始

需要 Python 3.11+、[uv](https://docs.astral.sh/uv/getting-started/installation/)，安装到 Codex 时还需要 Codex CLI。

在本项目根目录执行：

```powershell
uv sync --frozen
python scripts/serve.py
```

服务默认只监听 `127.0.0.1:5555`。每 3 秒扫描一次，问题等待至少 3 秒后生成回答，通常约 3～6 秒。数据库保存在项目 `.local/messages.sqlite3`，重启可继续查询。Ctrl+C 停止；Windows 可双击 `start.cmd`。

第二个终端运行：

```powershell
uv run --frozen python scripts/demo.py
```

预期输出：发送 `pending` → 主动查询 → `answered`，回复带 `source: local_mock`、`is_simulated: true`。演示程序的定时查询不等于模型会自动定时运行。

## 安装到 Codex

从 GitHub 添加本仓库作为插件目录：

```powershell
codex plugin marketplace add sunupupup/codex-user-bridge
codex plugin add codex-user-bridge@codex-user-bridge
```

也可先克隆本仓库，在本地运行 5555 服务：

```powershell
git clone https://github.com/sunupupup/codex-user-bridge.git
cd codex-user-bridge
uv sync --frozen
python scripts/serve.py
```


保持 5555 服务运行，在本项目根目录执行：

```powershell
codex plugin marketplace add .
codex plugin add codex-user-bridge@codex-user-bridge
```

重新打开 Codex 聊天，必要时重启 App。MCP 配置中的相对 `cwd` 由 Codex 解析为插件安装目录；`uv run` 在该副本准备依赖并启动工具，不引用原开发目录。

测试提示：

> 使用 codex-user-bridge 的 external-user-chat 技能发送一个本地测试问题。发送后先解释通信流程，再查询回答；只展示模拟结果，不执行真实付费操作。仍待回复时保留问题编号。

查看或卸载：

```powershell
codex plugin list --marketplace codex-user-bridge --json
codex plugin remove codex-user-bridge@codex-user-bridge
```

安装和日常工具调用本身不需要模型 Key；运行真实 Codex 模型任务使用你的账号额度。首次从缓存副本启动可能需要下载 Python 依赖，需能访问 Python 包源。

## 调用链

```text
Codex → bridge_send_question → LocalHttpAdapter.send → POST /chat
                                                   → SQLite 保存 pending

5555 服务定时线程 → 扫描到期问题 → 保存模拟回答 answered

Codex → bridge_get_reply → LocalHttpAdapter.get_reply → GET /chat/{id}?task_id=...
                                                     → 工具结果返回回答
```

| 模块 | 职责 |
|---|---|
| [Skill](./skills/external-user-chat/SKILL.md) | 何时发问、何时查询；不提供后台定时调度 |
| [mcp_server.py](./src/codex_user_bridge/mcp_server.py) | 两个 MCP 工具的参数、说明和结构化结果 |
| [adapters.py](./src/codex_user_bridge/adapters.py) | 统一 send/get_reply，隔离渠道协议 |
| [service.py](./src/codex_user_bridge/service.py) | HTTP 接口、模拟回复定时器 |
| [store.py](./src/codex_user_bridge/store.py) | 问题保存、幂等与状态推进 |

发送参数：

```json
{"task_id":"demo-001","request_id":"question-001","message":"本地测试收到吗？","options":["收到","稍后"]}
```

查询使用相同 `task_id` 和返回的 `question_id`。task_id 由调用者选定，不是自动读取的 Codex 会话 ID。

- 同一 task_id + request_id 重试不会重复创建问题；同键改内容返回 409。
- 查询不消费回答；任务标识不匹配或问题不存在返回 404。此机制防止串答，不是身份认证。
- 服务不可用时明确报告结果未知；重试沿用原 request_id。
- 回答是工具结果，不自动插入 user message，也不自动唤醒 Goal。
- 模拟回复永远不能授权真实花费或其他需用户决定的操作。
- Telegram 返回 queued 只代表排队，sent 只代表平台接受；unknown 不会自动重发。真人回答也仅适用于原问题。
- 仅供本机单用户实验，无账号认证；不要对局域网或公网开放，不发送密钥。

## 验证与开发

```powershell
uv run --frozen python -m unittest discover -s tests -v
python scripts/verify_plugin.py
# 需要先启动 5555 服务；从独立安装缓存验证收发
python scripts/verify_plugin.py --roundtrip
```

42 项自动检查通过（含三个渠道的模拟 API 及 MCP 端到端）；此前真实 Codex CLI 样本验证的是本地模拟渠道，详见[验收记录](./docs/validation.md)。安装验证使用独立临时配置，不改日常 Codex 配置、不调用模型。外部渠道真实手机收发待配置账号后验证。

[设计](./docs/plans/2026-10-20-local-bridge-design.md) · [更新记录](./CHANGELOG.md) · [MIT 许可证](./LICENSE)

术语：MCP 是宿主调用工具的协议；stdio 是子进程标准输入输出通信；Adapter 是渠道适配器；Polling 是主动查询；幂等表示重试同一个请求不重复创建问题。

[官方插件打包说明](https://developers.openai.com/plugins/build/plugins) · [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
