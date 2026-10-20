"""仅镜像含 native_bridge_probe 标记的原生提问，不回填或替代用户答案。"""

import json
from pathlib import Path
import sys
import time
from urllib.request import ProxyHandler, Request, build_opener
from urllib.error import HTTPError


def main():
    # Codex 写入 UTF-8；Windows 文本 stdin 可能按本地编码解码并产生代理字符。
    event = json.loads(sys.stdin.buffer.read().decode('utf-8'))
    questions = event.get('tool_input', {}).get('questions', [])
    # Hook 可在其他会话被发现，但只处理显式测试标记，避免转发正常会话。
    selected = [q for q in questions if q.get('id') == 'native_bridge_probe'
                or 'native_bridge_probe' in q.get('title', '')]
    control = 'native_bridge_hook_control' in event.get('tool_input', {}).get('command', '')
    if not selected and not control:
        return
    root = Path(__file__).resolve().parents[1]
    out = root / '.local' / 'desktop-hook-events.jsonl'
    out.parent.mkdir(exist_ok=True)
    record = {key: event.get(key) for key in
              ('hook_event_name', 'session_id', 'turn_id', 'tool_use_id', 'tool_name')}
    record['time'] = time.time()
    # 先落盘再访问页面服务，网络错误或字段缺失不能掩盖事件已经触发的事实。
    with out.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({**record, 'stage': 'received'}, ensure_ascii=False) + '\n')
    if selected and event.get('hook_event_name') == 'PreToolUse':
        opener = build_opener(ProxyHandler({}))
        with opener.open('http://127.0.0.1:5555/health', timeout=3) as response:
            if json.load(response).get('channel') != 'local_chat':
                raise RuntimeError('实验只允许 local_chat')
        record['questions'] = []
        for index, question in enumerate(selected):
            data = {'task_id': event['session_id'],
                    'request_id': 'desktop:' + event['tool_use_id'] + ':' + question.get('id', str(index)),
                    'message': '[桌面 Hook 实验；回填尚未接通] ' + question.get('question', question.get('title', '')),
                    'options': [o['label'] if isinstance(o, dict) else o
                                for o in question.get('options') or []]}
            request = Request('http://127.0.0.1:5555/chat',
                              data=json.dumps(data).encode('utf-8'),
                              headers={'Content-Type': 'application/json'})
            with opener.open(request, timeout=3) as response:
                record['questions'].append(json.load(response)['question_id'])
    # 仅记录测试调用的元信息，不记录其他会话文本，也不输出 Hook 决策。
    with out.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + '\n')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # 只记录异常类型和文本，不输出用户问题或其他会话内容。
        path = Path(__file__).resolve().parents[1] / '.local' / 'desktop-hook-errors.jsonl'
        path.parent.mkdir(exist_ok=True)
        with path.open('a', encoding='utf-8') as stream:
            detail = exc.read(2048).decode('utf-8', errors='replace') if isinstance(exc, HTTPError) else ''
            stream.write(json.dumps({'time': time.time(), 'error': str(exc), 'detail': detail,
                                     'type': type(exc).__name__}) + '\n')
        raise
