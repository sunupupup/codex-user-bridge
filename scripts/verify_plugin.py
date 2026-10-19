"""在隔离配置中安装插件，检查 Codex 实际发现 Skill 和两个 MCP 工具。"""
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import tempfile
import argparse
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--roundtrip', action='store_true', help='另需先启动 5555 服务，从安装缓存验证收发')
    args = parser.parse_args()
    cli = shutil.which('codex')
    if cli is None:
        raise RuntimeError('未找到 codex CLI，请先安装或加入 PATH。')
    # 缓存在源码目录之外，防止安装复制源码时递归复制自己的缓存。
    temp = tempfile.TemporaryDirectory(prefix='codex-user-bridge-validation-')
    workspace = Path(temp.name)
    source = workspace / 'source'
    shutil.copytree(ROOT, source, ignore=shutil.ignore_patterns(
        '.local', '.venv', '.git', '.env', '.env.*', 'plugin', '__pycache__', '*.egg-info', 'build', 'dist'))
    validation_home = workspace / 'codex-home'
    validation_home.mkdir(parents=True, exist_ok=True)
    # 所有安装状态只写隔离目录，不修改用户真实配置，也不使用登录凭据。
    env = {**os.environ, 'CODEX_HOME': str(validation_home)}

    def command(*args):
        result = subprocess.run([cli, *args], env=env, capture_output=True,
                                text=True, encoding='utf-8', timeout=40, check=True)
        return json.loads(result.stdout)

    command('plugin', 'marketplace', 'add', str(source), '--json')
    installation = command('plugin', 'add', 'codex-user-bridge@codex-user-bridge', '--json')
    output = queue.Queue()
    (ROOT / '.local').mkdir(exist_ok=True)
    log = (ROOT / '.local/plugin-validation.stderr.log').open('w', encoding='utf-8')
    process = subprocess.Popen([cli, 'app-server', '--stdio'], env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True, encoding='utf-8')

    def read_output():
        for line in process.stdout:
            output.put(json.loads(line))
    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    sequence = 0

    def call(method, params):
        nonlocal sequence
        sequence += 1
        process.stdin.write(json.dumps({'id': sequence, 'method': method, 'params': params}) + '\n')
        process.stdin.flush()
        while True:
            message = output.get(timeout=30)
            if message.get('id') == sequence:
                if 'error' in message:
                    raise RuntimeError(message['error'])
                return message['result']

    try:
        call('initialize', {'clientInfo': {'name': 'bridge-validation', 'version': '0.1.0'},
                            'capabilities': {'experimentalApi': True}})
        process.stdin.write('{"method":"initialized"}\n')
        process.stdin.flush()
        skills = call('skills/list', {'cwds': [str(source)], 'forceReload': True})
        found = [s['name'] for item in skills['data'] for s in item['skills']
                 if s.get('pluginId') == 'codex-user-bridge@codex-user-bridge']
        assert found == ['codex-user-bridge:external-user-chat'], found
        # 创建空会话只用于启动 MCP，不发送 turn/start，不调用模型。
        thread = call('thread/start', {'cwd': str(source), 'approvalPolicy': 'never', 'sandbox': 'read-only'})
        status = call('mcpServerStatus/list', {'threadId': thread['thread']['id'],
                                              'detail': 'toolsAndAuthOnly', 'limit': 100})
        server = next(s for s in status['data'] if s['name'] == 'user_bridge')
        tools = sorted(server['tools'])
        assert tools == ['bridge_get_reply', 'bridge_send_question'], server
        assert not server.get('toolsError'), server
        evidence = {'installation': installation, 'skills': found, 'tools': tools,
                    'server_info': server['serverInfo'], 'model_called': False}
        if args.roundtrip:
            # 真正从缓存副本运行，不引用原始源码的虚拟环境或 Python 路径。
            cached = Path(installation['installedPath'])
            subprocess.run([shutil.which('uv'), 'run', '--frozen', '--directory', str(cached),
                            'python', 'scripts/demo.py'], check=True, timeout=45,
                           stdout=subprocess.DEVNULL)
            messages = json.loads((cached / '.local/demo-evidence.json').read_text(encoding='utf-8'))
            assert messages[0]['result']['status'] == 'pending'
            assert messages[-1]['result']['status'] == 'answered'
            evidence['installed_cache_roundtrip'] = 'passed'
        (ROOT / '.local/plugin-evidence.json').write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print('Codex 插件安装、Skill 发现、两个 MCP 工具加载：通过；未调用模型。')
    finally:
        # Windows 的 codex 可能是包装进程；先以 EOF 正常关闭宿主，让它回收 MCP 子进程。
        # 直接 terminate 包装进程会遗留子进程继续写入临时环境，导致清理竞态。
        process.stdin.close()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            else:
                process.terminate()
            process.wait(timeout=10)
        reader.join(timeout=3)
        process.stdout.close()
        log.close()
        # 官方 SDK 的深层文件名可能超过 Windows 传统 260 字符限制；只清理本次专属目录。
        cleanup_path = workspace.resolve()
        assert cleanup_path.parent == Path(tempfile.gettempdir()).resolve()
        assert cleanup_path.name.startswith('codex-user-bridge-validation-')
        deletion_path = ('\\\\?\\' + str(cleanup_path)) if os.name == 'nt' else str(cleanup_path)
        # 杀毒软件或延迟关闭的句柄可能短暂占用此脚本专属临时目录，有限重试，不隐藏失败。
        for attempt in range(5):
            try:
                if cleanup_path.exists():
                    shutil.rmtree(deletion_path)
                temp.cleanup()
                break
            except OSError:
                if attempt == 4:
                    raise
                time.sleep(0.5)


if __name__ == '__main__':
    main()
