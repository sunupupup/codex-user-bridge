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

## 尚未实现

桌面版附加连接、Hook 运行验证、双端抢答、超时手机通知、重启恢复。
原请求取消或实验退出后，本地页面历史卡片暂不标记失效；迟到回复不会再提交，
因为实验进程已停止，但页面仍可能显示可回复。此脚本不能作为常驻生产服务。

当前桌面 App Server 以 stdio 启动，不能因为知道请求 id 就从另一个
独立 App Server 回答它；需要接入管理那个等待的同一服务实例。
