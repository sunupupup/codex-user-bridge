# 第一阶段验收

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
