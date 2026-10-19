"""可从任意工作目录启动；数据库始终归属于本项目。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from codex_user_bridge.service import main

if __name__ == '__main__':
    # 显式 --db 仍可覆盖，默认路径不随调用者工作目录变化。
    if '--db' not in sys.argv:
        sys.argv.extend(['--db', str(ROOT / '.local/messages.sqlite3')])
    main()
