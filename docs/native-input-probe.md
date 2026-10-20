# 原生提问 → 本地网页答案实验

这是独立 App Server 客户端实验，**没有接管 Codex 桌面版现有提问框**。
不需要重载插件；复用已经运行在 http://127.0.0.1:5555 的 local_chat 服务。
运行会使用当前 Codex 登录账号进行真实模型调用。

## 手动测试

在仓库目录运行：

```powershell
uv run --frozen python scripts/native_input_probe.py --timeout 180
```

打开本地页面，等待出现带 `[原生 App Server 实验]` 的新问题，
填写回复并发送。终端会输出原生请求 id、bridge 问题 id，以及完成结果。
最多等待指定秒数；模型连接或网络较慢时可能超时。

## 自动验收

```powershell
uv run --frozen python scripts/native_input_probe.py --auto-test-reply --timeout 180
```

自动验收通过 `/ui/reply` 提交明确标注的测试文本。它不代表真人操作，
也没有验证浏览器点击。证据保存在忽略目录 `.local/native-input-probe.json`，
每次运行覆盖；失败运行也保留结果，不能只凭文件存在判定成功。

## 数据流

1. 启动隔离的 App Server 临时会话，让模型调用原生 request_user_input。
2. 接收 item/tool/requestUserInput，保存原 RPC id 和每个 question id。
3. 通过原有 `/chat` 创建页面问题，保存 bridge id 映射。
4. 后台轮询页面回复；全部问题回答后，沿原连接返回 `{id, result: {answers}}`。
5. 观察 serverRequest/resolved 和 turn/completed，确认模型复述测试答案。

不是插入 user message，不通过 bridge_get_reply 让模型自己查询。
当前仅实验一个外部答案入口，未实现桌面原生 UI 与网页并发回答。

## 已验证（2026-10-21，CLI 0.163.0-alpha.10.2）

- 原生请求 id：`0`（协议允许数字，不一定是 UUID）。
- question id：`local_probe`。
- bridge id：`q_7f37970f8abd43949e46e4e26f24fdbb`。
- 原请求提交后收到 resolved，回合 completed，模型逐字复述中文测试答案。
- 脚本退出码 0；现有 46 项回归测试通过。
- 初次运行核心链路成功，但临时目录清理失败；修复为结束本实验进程树后，
  第二次运行完整成功。中文文件内容完整，首轮终端显示受编码影响。

## 桌面 Hook 实验（2026-10-21）

- 已验证真实桌面会话的 `request_user_input_async` 触发 `PreToolUse` 与 `PostToolUse`。
- `scripts/desktop_hook_probe.py` 只处理 `native_bridge_probe` 测试问题，
  将其镜像到 local_chat；测试命令标记 `native_bridge_hook_control` 仅记录事件。
- 已修复 Windows stdin 按本地编码读取导致中文出现代理字符的问题：
  明确按 UTF-8 读取，并在 HTTP 请求前记录事件，分清触发失败和转发失败。
- 重启服务后，真实桌面提问已显示在页面，用户也已在页面回复；**答案未回填到原问题**。
- 此脚本只有问题镜像和诊断日志，没有答案监听或原生答复传输，不会替用户授权。
- 实验曾手动在本机用户级 `~/.codex/hooks.json` 注册两个 Hook；
  该配置不在仓库中，安装插件不会自动启用它。停用时仅移除对应测试项，保留其他 Hook。

桌面本轮测的是异步工具：工具先返回 `accepted`，回答稍后另行提交。
源码核查发现异步原生答复使用 `send_user_message_question_reply` 结构，
通过 `questionItemId` 关联原问题；独立同步实验使用的是原 JSON-RPC 请求响应。
二者不能混同。格式已知不等于已经获得当前桌面会话的提交入口。

## 未完成任务：桌面外部答案回填

状态：**未完成，待验证宿主答复入口**。目前没有找到并跑通当前桌面版可供插件使用的
原生答案提交通路；不据此断言 Codex 完全不支持。不要把另发普通消息或独立实例成功
当成原问题回填成功。

- [ ] 查证 Hook 返回值是否支持完成原提问，以及桌面是否提供对应答复接口。
- [ ] 以一个最小答案验证原问题 ID 的回填，并验证原生卡片状态和模型收到的答案。
- [ ] 接入页面回复监听，保存会话 ID、工具调用 ID、问题索引和 bridge ID 映射。
- [ ] 验证桌面/外部任一端先答只生效一次，取消或已结束的问题拒绝迟到答案。
- [ ] 完成上述闭环后再增加超时通知、断线与重启恢复。

当前独立同步实验的限制：
原请求取消或实验退出后，本地页面历史卡片暂不标记失效；迟到回复不会再提交，
因为实验进程已停止，但页面仍可能显示可回复。此脚本不能作为常驻生产服务。

当前桌面 App Server 以 stdio 启动，不能因为知道请求 id 就从另一个
独立 App Server 回答它；需要接入管理那个等待的同一服务实例。
