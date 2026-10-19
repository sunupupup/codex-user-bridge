"""SQLite 保存问题与模拟回答，服务重启后仍可查询。"""

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS questions (
                question_id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
                request_id TEXT NOT NULL, message TEXT NOT NULL,
                options TEXT NOT NULL, created_at REAL NOT NULL,
                reply TEXT, answered_at REAL,
                UNIQUE(task_id, request_id))''')

    @contextmanager
    def connect(self):
        # 每次操作独立连接，HTTP 线程和模拟回复线程不共享连接对象。
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def send(self, task_id: str, request_id: str, message: str, options: list[str]):
        encoded = json.dumps(options, ensure_ascii=False)
        with self.connect() as db:
            # 同一事务内检查和写入，避免并发重试生成两个问题。
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT question_id, message, options FROM questions '
                             'WHERE task_id=? AND request_id=?', (task_id, request_id)).fetchone()
            if row:
                if row[1:] != (message, encoded):
                    raise ValueError('同一个 request_id 已用于不同问题，请使用新的编号。')
                question_id = row[0]
            else:
                question_id = 'q_' + uuid.uuid4().hex
                db.execute('INSERT INTO questions VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)',
                           (question_id, task_id, request_id, message, encoded, time.time()))
        return self.get(task_id, question_id)

    def get(self, task_id: str, question_id: str):
        with self.connect() as db:
            row = db.execute('SELECT message, options, created_at, reply, answered_at '
                             'FROM questions WHERE task_id=? AND question_id=?',
                             (task_id, question_id)).fetchone()
        if row is None:
            return None
        return {'question_id': question_id, 'task_id': task_id,
                'message': row[0], 'options': json.loads(row[1]), 'created_at': row[2],
                'status': 'answered' if row[3] is not None else 'pending',
                'reply': None if row[3] is None else {
                    'text': row[3], 'source': 'local_mock',
                    'is_simulated': True, 'answered_at': row[4]},
                'is_simulated': True, 'poll_after_seconds': 3}

    def auto_reply(self, delay: float, text: str):
        with self.connect() as db:
            # 只推进 pending → answered；重复扫描不覆盖已有回答。
            result = db.execute('UPDATE questions SET reply=?, answered_at=? '
                                'WHERE reply IS NULL AND created_at<=?',
                                (text, time.time(), time.time() - delay))
            return result.rowcount
