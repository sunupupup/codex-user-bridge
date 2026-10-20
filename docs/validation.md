# 第一阶段验收

最新补充：0.3.0 Telegram 文本私聊代码与模拟 API 验证见本文末尾和 [Telegram 使用说明](./telegram.md)。原有真实模型记录仅适用于 local_mock 渠道。

日期：2026-10-20。环境：Windows、Python 3.12.10、MCP SDK 1.30.0、Codex CLI 0.163.0-alpha.10.2。

## 已验证

| 验收项 | 证据 |
|---|---|
| 发送立即返回 pending，定时线程生成回答 | 默认 5555 服务进程 + demo.py；约 3.15 秒后回答入库，后续主动查询取得 answered |
| 真正通过 MCP 工具发送、查询 | 官方 SDK ClientSession 启动 stdio 子进程，调用两个工具；非直接调用工具函数 |
| 结构化工具结果与输入约束 | 工具有 outputSchema；空问题被拒绝，跨任务查询返回 isError |
| 并发发送同一请求不重复创建 | 12 次并发请求只产生一个 question_id |
| 相同编号改内容被拒绝 | HTTP 409 |
| 多任务不串答、未知问题明确失败 | HTTP 404 |
| 重开数据库后保留状态 | pending 与 answered 均可由新 Store 实例读取；重复扫描不覆盖回答 |
| 无效参数、跨域请求、错误 Content-Type | HTTP 400、403、415 |
| 服务不可用 | 工具侧明确报告结果未知，提示原 request_id 重试 |
| 本机插件可安装 | 隔离 CODEX_HOME 注册 repo marketplace、安装插件并显示 enabled |
| 宿主发现 Skill 与 MCP | App Server skills/list 发现插件技能；空 thread 启动后 mcpServerStatus/list 列出 user_bridge 和两个工具，无 toolsError |

自动检查：`uv run --frozen python -m unittest discover -s tests -v`，10 项通过。

演示证据：`.local/demo-evidence.json`；插件证据：`.local/plugin-evidence.json`。它们包含本机路径或运行标识，已忽略，不纳入 Git。中文消息与回答的 UTF-8 内容已核对无替换字符。

## 发现与修正

- 裸 dict 返回类型没有生成预期的 outputSchema；改用明确的 Pydantic Question/Reply 模型后，客户端验证通过。
- 此 Codex 版本识别 portable plugin.json 的技能，但没有从 mcp.json 载入工具；改用官方支持的 `.codex-plugin/plugin.json` + `.mcp.json` 兼容布局，宿主实际发现两个工具。
- `codex mcp list` 没列出插件工具，不能用它判定插件失效；已改为在加载插件的空 thread 上检查 MCP 状态。

## 尚未验证

长期 Goal 中自行挑选调用时机、桌面 UI 安装交互、当前聊天热加载、真实外部用户回复、进程硬中断时的持久化容错。数据库重新打开检查不等于完整故障恢复验证。

上述协议检查与 demo 无真实 Key、模型请求或费用；模拟回答不代表用户许可。未提交或 push。

## 补充：真实 Codex CLI 模型测试

用户随后明确要求使用 Codex 命令行做本地测试，已将插件安装到用户真实配置，并执行 `codex exec --json --ephemeral`，使用现有 ChatGPT 登录和配置中的 gpt-6.1-sol。此项使用 Codex 账号额度；没有调用其他付费 API。

- 发送 1 次，查询 2 次，实际 MCP 记录均为 completed，状态依次为 pending → pending → answered。
- task_id：`cli-live-58ca4e02addb42ed97879a811ddce524`。
- question_id：`q_80dcbe2b517a44d4a09f3cdc0cb9870c`。
- 默认回答约 4.325 秒入库；CLI 全程约 25.39 秒，退出码 0。
- 模型在发送与查询之间输出了独立说明；没有使用 shell/HTTP 脚本替代 MCP 工具。
- 最终回答 source=local_mock、is_simulated=true；数据库核对恰有一个匹配问题，回答文本一致。
- 本机证据：`.local/cli-live-events.jsonl`、`.local/cli-live-evidence.json`、`.local/cli-live-final.txt`，均已忽略。

前两次测试未成功，记录保存在 `.local/cli-live-attempt1-*` 与 `attempt2-*`：首次 CLI 点路径参数中的引号导致插件名解析错误；修正后又因使用 `--ignore-user-config` 未完整保留插件加载所需配置，仍未暴露工具。最终保留现有配置后成功。失败两次均发送 0 次；不能以 CLI 退出码 0 作为业务成功依据。

本次是明确测试提示下的一次真实模型闭环样本，不证明无人值守 Goal 会始终主动查询。

## 0.2.0：公开发布结构复验

- `.codex-plugin/plugin.json`、`.mcp.json`、skills 位于项目根目录；源码位于 src/codex_user_bridge，脚本位于 scripts。
- Python 包使用 pyproject.toml，依赖由 uv.lock 锁定。移除了 0.1.0 生成的本机绝对路径配置。
- 使用兼容 MCP 配置的 `cwd: "."`，由 Codex 解析为安装根目录；没有使用此兼容解析器不支持的参数占位符。
- 10 项自动检查在重排后再次通过。
- `python scripts/verify_plugin.py --roundtrip`：将干净源码复制到临时目录，从隔离 Codex 配置安装；宿主实际发现 Skill 和两个 MCP 工具，再从安装缓存副本执行发送、查询闭环，pending → answered。
- 缓存副本的 uv 环境独立创建，没有依赖原始源码目录；不调用模型。
- 发布扫描排除 .local、.venv、缓存与数据库；已检查源码无密钥特征或开发机绝对路径。LICENSE 保留原仓库 MIT 文本。
- 当前结构的实测平台为 Windows；未运行 Linux/macOS 实测。GitHub 公开源码不等于发布到官方插件商店。

## 0.3.0：Telegram 渠道

- 默认仍为 local_mock；--channel telegram 才启用真实通道，不会由模拟线程回答 Telegram 问题。
- 21 项自动检查通过；测试真实 MCP 子进程 → 本机 HTTP → 模拟 Telegram HTTP API → 模拟入站回答 → 查询同一问题。
- 校验引用回复、问题编号回复、多个问题不串答、陌生人/其他聊天/无关联文本被忽略、首条回答不可覆盖、重复更新与持久化游标。
- 检查旧版 SQLite 数据库升级后保留 local_mock 来源；发送中断恢复 unknown、不自动重发；429 冷却持久化；Webhook 冲突与重复消费者被拒绝。
- 超长问题明确失败，不能悄悄截掉影响决定的条件。带 Token 的错误描述不会进入工具结果。
- 没有真实 Bot Token 和手机联调；不能把模拟测试的 source=telegram 字段作为实际外部用户答复证明。没有调用付费模型。

## 0.4.0：飞书渠道

- 28 项自动检查通过，保留旧渠道回归，新增飞书 REST 模拟、官方 SDK 事件模型、父进程落盘 ACK 和真实 MCP 子进程闭环。
- 绑定应用与接收者，仅处理私聊文本；支持引用与问题编号回复，重复事件不覆盖首个回答，渠道之间不串答。
- 官方 WebSocket SDK 在独立进程运行；不需要公网回调地址，退出时终止接收进程。
- 未配置真实飞书应用凭据，没有验证手机端收发和企业后台权限发布流程；未调用付费模型。

## 0.5.0：个人微信渠道

- 42 项自动检查通过；新增微信模拟 HTTP API → 入站消息 → SQLite → 真实 MCP 查询闭环，旧渠道回归保留。
- 检查缺少上下文排队、普通入站消息激活、游标恢复、消息去重不覆盖新上下文、账号绑定、其他渠道与陌生消息隔离。
- 检查 -2 等待新上下文、-14 登录失效、HTTP 5xx 与缺少明确成功时标记 unknown、不盲目重发。
- 扫码登录使用模拟 HTTP，另检查配对验证码、微信域名跳转、失败不覆盖原凭据。没有使用真实微信扫码或账号。
- Codex CLI 实际从独立临时安装缓存发现 Skill 和两个 MCP 工具，未发送模型回合。验证脚本的 Windows 长路径清理失败经修复后重跑，退出码为 0。
- 微信会话上下文窗口与账号可用性仍需真实手机验收。此实现不等于企业微信、普通好友机器人或无限期主动通知。


## 0.6.0：本地聊天页面

- 46 项自动检查通过，覆盖手动回复、同源请求、渠道隔离、并发首答及持久化。
- 在真实浏览器显示测试问题、选择候选答案并发送，刷新仍保留回答；真实 MCP 子进程成功查询这条网页回答，来源为 local_chat。
- 0.6.0 已安装到本机，5555 已切换为 local_chat；Telegram 账号与数据库保留。
- 页面仅用于本机手动测试，不提供远端用户身份认证；旧自动模拟需显式 --channel local_mock。
