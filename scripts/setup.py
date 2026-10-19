"""准备项目依赖；发布配置不再写入开发机绝对路径。"""
from pathlib import Path
import shutil
import subprocess


def main():
    root = Path(__file__).resolve().parents[1]
    uv = shutil.which('uv')
    if uv is None:
        raise SystemExit('请先安装 uv：https://docs.astral.sh/uv/getting-started/installation/')
    subprocess.run([uv, 'sync', '--project', str(root)], check=True)
    print('依赖已准备。运行 python scripts/serve.py 启动本地 5555 服务。')


if __name__ == '__main__':
    main()
