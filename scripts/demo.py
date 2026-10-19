"""连接正在运行的 5555 服务，用真实 MCP 客户端演示主动查询。"""
import asyncio
import json
from pathlib import Path
import time
import uuid

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


async def main():
    config = json.loads((ROOT / '.mcp.json').read_text(encoding='utf-8'))
    server = config['mcpServers']['user_bridge']
    # Codex 会把插件配置中的相对 cwd 解析为安装目录；普通客户端需显式解析。
    server['cwd'] = str(ROOT)
    params = StdioServerParameters(**server)
    events = []

    def record(event, result):
        if result.isError:
            raise RuntimeError(result.content)
        events.append({'event': event, 'result': result.structuredContent})
        print(json.dumps(events[-1], ensure_ascii=False), flush=True)
        return result.structuredContent

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            task_id = 'demo-' + uuid.uuid4().hex
            question = record('sent', await client.call_tool('bridge_send_question', {
                'task_id': task_id, 'request_id': 'question-1',
                'message': '这是一条本地通信测试，请生成默认模拟回复。'}))
            args = {'task_id': task_id, 'question_id': question['question_id']}
            assert question['status'] == 'pending'
            print('发送已返回；这里可以继续其他独立工作。', flush=True)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                # 演示程序的定时器主动查询；这不证明模型会自行定时查询。
                reply = record('polled', await client.call_tool('bridge_get_reply', args))
                if reply['status'] == 'answered':
                    assert reply['reply']['is_simulated'] is True
                    break
                await asyncio.sleep(1)
            else:
                raise TimeoutError('15 秒内未收到模拟回复，请检查服务日志。')
    evidence = ROOT / '.local/demo-evidence.json'
    evidence.parent.mkdir(exist_ok=True)
    evidence.write_text(json.dumps(events, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('闭环通过，记录已保存到 .local/demo-evidence.json')


if __name__ == '__main__':
    asyncio.run(main())
