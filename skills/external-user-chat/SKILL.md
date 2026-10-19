---
name: external-user-chat
description: 当用户要求通过本地模拟渠道、Telegram 或飞书 发送问题、主动查询回答时使用；按结果区分真实回答与模拟数据。
---

# 异步用户问答

1. 为本次任务选一个稳定且唯一的 task_id，并为每个问题选 request_id。它们由你生成，不是假定可自动读取的宿主 ID。
2. 调用 bridge_send_question，保存 task_id、request_id、返回的 question_id；原问题重试必须复用 request_id。
3. 工具返回后继续独立工作，不等待回答。queued/sending 表示尚未确认平台接收；仅 sent 可称“平台已接受”，也不等于用户已读。unknown/failed 要明确报告，不能宣称通知成功，不自动换 request_id 重发。
4. 在独立阶段结束或需要回答前，调用 bridge_get_reply。pending 就保留待确认，不当成拒绝或同意。
5. 如果无其他工作，查询一次并报告待回复；本插件不保证后续定时唤醒。不要高频循环耗费模型调用。
6. 回答带 is_simulated=true、source=local_mock；只用于验证通信。绝对不能把它当成人类回答、真实费用许可或系统权限授权。
7. 服务失败必须说明未送达或结果未知；禁止宣称已经通知了真实用户。
8. 回答以 tool result 返回，不是 user message，不自动控制其他聊天。不要发送密钥或凭据。
9. source=telegram/feishu、is_simulated=false 时，是已绑定私聊用户对该问题的首条有效文本回答。严格按原问题范围理解，不扩大为其他操作或系统权限审批；不要自动把一句“同意”用于新问题。
