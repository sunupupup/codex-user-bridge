"""独立 App Server 实验：本地网页回答原生提问；不接管桌面版连接。"""

import argparse
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import tomllib
from urllib.parse import urlencode
from urllib.request import Request, ProxyHandler, build_opener


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:5555')
    parser.add_argument('--timeout', type=float, default=180)
    parser.add_argument('--auto-test-reply', action='store_true',
                        help='自动提交标注的测试文本；不是真人回复或授权')
    args = parser.parse_args()
    if not 0 < args.timeout <= 600:
        parser.error('timeout 必须在 0～600 秒之间')
    if args.base_url not in ('http://127.0.0.1:5555', 'http://localhost:5555'):
        parser.error('本实验仅连接本机 5555')
    opener = build_opener(ProxyHandler({}))

    def http(path, data=None):
        req = Request(args.base_url + path,
                      data=None if data is None else json.dumps(data).encode(),
                      headers={'Content-Type': 'application/json'})
        with opener.open(req, timeout=5) as response:
            return json.load(response)

    if http('/health')['channel'] != 'local_chat':
        raise RuntimeError('请先启动 local_chat，避免测试问题发往真实外部渠道')
    executable = shutil.which('codex')
    if not executable:
        raise RuntimeError('未找到 codex CLI')
    inbox = queue.Queue()
    pending = {}
    submitted = []
    resolved = []
    root = Path(__file__).resolve().parents[1]
    log_dir = root / '.local'
    log_dir.mkdir(exist_ok=True)
    evidence = {'kind': 'independent_app_server', 'automatic_test': args.auto_test_reply}
    command = [executable]
    config_path = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'config.toml'
    if config_path.exists():
        config = tomllib.loads(config_path.read_text(encoding='utf-8'))
        # 只获取配置名称并关闭实验不需要的连接，密钥不复制到命令行或日志。
        for section in ('mcp_servers', 'plugins'):
            for name in config.get(section, {}):
                if '.' not in name:
                    command.extend(['-c', f'{section}.{name}.enabled=false'])
    command.extend(['app-server'])

    # 独立临时工作目录避免加载学习仓库指令；不读取或修改真实项目。
    with tempfile.TemporaryDirectory(prefix='bridge-native-') as cwd, \
            (log_dir / 'native-input-probe.stderr.log').open('w', encoding='utf-8') as err:
        proc = subprocess.Popen(command, cwd=cwd,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=err, text=True, encoding='utf-8',
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)

        def read():
            for line in proc.stdout:
                try:
                    inbox.put(json.loads(line))
                except ValueError:
                    continue
            inbox.put({'transport_closed': True})

        threading.Thread(target=read, daemon=True).start()

        def send(payload):
            proc.stdin.write(json.dumps(payload) + '\n')
            proc.stdin.flush()

        def call(identifier, method, params):
            send({'id': identifier, 'method': method, 'params': params})

        deadline = time.monotonic() + args.timeout
        try:
            call('init', 'initialize', {'clientInfo': {'name': 'bridge_probe', 'version': '0.1'},
                                      'capabilities': {'experimentalApi': True}})
            while time.monotonic() < deadline:
                try:
                    event = inbox.get(timeout=.3)
                except queue.Empty:
                    event = {}
                if event.get('transport_closed'):
                    raise RuntimeError('App Server 连接关闭；查看 .local/native-input-probe.stderr.log')
                if 'error' in event:
                    raise RuntimeError(str(event['error']))
                identifier = event.get('id')
                if identifier == 'init':
                    send({'method': 'initialized'})
                    call('thread', 'thread/start', {'cwd': cwd, 'ephemeral': True,
                                                   'approvalPolicy': 'never', 'sandbox': 'read-only'})
                elif identifier == 'thread':
                    result = event['result']
                    evidence['thread_id'] = result['thread']['id']
                    call('turn', 'turn/start', {
                        'threadId': evidence['thread_id'],
                        'collaborationMode': {'mode': 'plan', 'settings': {
                            'model': result['model'], 'reasoning_effort': 'low',
                            'developer_instructions': None}},
                        'input': [{'type': 'text', 'text':
                            'This is a user-authorized input transport test. Do not read files or run commands. '
                            'Call request_user_input exactly once to ask: 本地页面能否回答原生问题？ '
                            'Use question id local_probe, header 本地测试, two options 可以 and 不可以 '
                            'with short descriptions. After receiving the answer, echo it verbatim and stop.',
                                   'text_elements': []}]})
                method = event.get('method')
                if method == 'item/tool/requestUserInput':
                    params = event['params']
                    mappings = []
                    # RPC 请求编号与每个问题编号不同；多问题必须全部收齐再响应。
                    for question in params['questions']:
                        record = http('/chat', {
                            'task_id': params['threadId'],
                            'request_id': f"native:{identifier}:{question['id']}",
                            'message': '[原生 App Server 实验] ' + question['question'],
                            'options': [option['label'] for option in question.get('options') or []]})
                        mappings.append((question['id'], record['question_id']))
                    pending[identifier] = {'params': params, 'mappings': mappings,
                                           'created': time.monotonic()}
                    print(json.dumps({'native_request_id': identifier, 'questions': mappings},
                                     ensure_ascii=False), flush=True)
                elif method == 'serverRequest/resolved':
                    request_id = event['params']['requestId']
                    pending.pop(request_id, None)
                    resolved.append(request_id)
                elif method == 'item/completed':
                    item = event['params']['item']
                    if item.get('type') == 'agentMessage':
                        evidence['final_text'] = item.get('text')
                elif method == 'turn/completed':
                    evidence['turn'] = event['params']['turn']
                    break
                elif identifier is not None and method:
                    # 不替用户批准其他请求；本实验只实现普通提问的响应。
                    send({'id': identifier, 'error': {'code': -32601,
                                                     'message': 'Unsupported by input probe'}})
                for request_id, state in list(pending.items()):
                    if request_id in submitted:
                        continue
                    answers = {}
                    for question_id, bridge_id in state['mappings']:
                        task = state['params']['threadId']
                        record = http('/chat/' + bridge_id + '?' + urlencode({'task_id': task}))
                        if args.auto_test_reply and record['status'] == 'pending' \
                                and time.monotonic() - state['created'] > 2:
                            record = http('/ui/reply', {'task_id': task, 'question_id': bridge_id,
                                                      'text': '自动测试文本：网页答案已返回；不是用户授权。'})
                        if record['status'] == 'answered':
                            answers[question_id] = {'answers': [record['reply']['text']]}
                    if len(answers) == len(state['mappings']):
                        # 使用原 JSON-RPC id 返回结果，不创建新 user message 或新 turn。
                        send({'id': request_id, 'result': {'answers': answers}})
                        submitted.append(request_id)
                        evidence['answers'] = answers
            else:
                raise TimeoutError('实验超时；停止本进程，未回填的页面问题此后不会再被处理')
            evidence.update(submitted=submitted, resolved=resolved)
            if not submitted or not all(item in resolved for item in submitted):
                raise RuntimeError('未观察到原生提问提交并 resolved，不能判定测试成功')
            if evidence.get('turn', {}).get('status') != 'completed':
                raise RuntimeError('答案提交后回合未正常完成')
            if args.auto_test_reply and '自动测试文本' not in evidence.get('final_text', ''):
                raise RuntimeError('模型未复述预期测试答案')
            print(json.dumps(evidence, ensure_ascii=False), flush=True)
        finally:
            # 退出即切断实验会话；不让旧页面的迟到回答进入其他任务。
            if os.name == 'nt' and proc.poll() is None:
                # 仅结束本脚本创建的进程树，避免 MCP 子进程占用临时目录。
                subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                               capture_output=True, timeout=10)
            elif proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            evidence.update(submitted=submitted, resolved=resolved)
            (log_dir / 'native-input-probe.json').write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
