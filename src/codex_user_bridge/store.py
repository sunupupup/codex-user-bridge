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
            # 建表与升级也串行化，避免两个启动进程同时添加同一个字段。
            db.execute('BEGIN IMMEDIATE')
            db.execute('''CREATE TABLE IF NOT EXISTS questions (
                question_id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
                request_id TEXT NOT NULL, message TEXT NOT NULL,
                options TEXT NOT NULL, created_at REAL NOT NULL,
                reply TEXT, answered_at REAL,
                UNIQUE(task_id, request_id))''')
            # 旧版数据库原位升级；缺省 local_mock，旧回答不会被改成真人回复。
            columns = {row[1] for row in db.execute('PRAGMA table_info(questions)')}
            for name, definition in {
                'channel': "TEXT NOT NULL DEFAULT 'local_mock'",
                'delivery_status': "TEXT NOT NULL DEFAULT 'sent'",
                'delivery_error': 'TEXT', 'telegram_message_id': 'INTEGER',
                'sender_id': 'INTEGER', 'external_message_id': 'TEXT',
            }.items():
                if name not in columns:
                    db.execute(f'ALTER TABLE questions ADD COLUMN {name} {definition}')
            db.execute('CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS receipts (channel TEXT, event_id TEXT, PRIMARY KEY(channel,event_id))')

    @contextmanager
    def connect(self):
        # 每次操作独立连接，HTTP 线程和模拟回复线程不共享连接对象。
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def send(self, task_id: str, request_id: str, message: str, options: list[str], channel='local_mock'):
        encoded = json.dumps(options, ensure_ascii=False)
        with self.connect() as db:
            # 同一事务内检查和写入，避免并发重试生成两个问题。
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT question_id, message, options, channel FROM questions '
                             'WHERE task_id=? AND request_id=?', (task_id, request_id)).fetchone()
            if row:
                if row[1:] != (message, encoded, channel):
                    raise ValueError('同一个 request_id 已用于不同问题，请使用新的编号。')
                question_id = row[0]
            else:
                question_id = 'q_' + uuid.uuid4().hex
                db.execute('INSERT INTO questions '
                           '(question_id,task_id,request_id,message,options,created_at,channel,delivery_status) '
                           'VALUES (?,?,?,?,?,?,?,?)',
                           (question_id, task_id, request_id, message, encoded, time.time(), channel,
                            'sent' if channel == 'local_mock' else 'queued'))
        return self.get(task_id, question_id)

    def get(self, task_id: str, question_id: str):
        with self.connect() as db:
            row = db.execute('SELECT message, options, created_at, reply, answered_at, channel, '
                             'delivery_status, delivery_error, sender_id '
                             'FROM questions WHERE task_id=? AND question_id=?',
                             (task_id, question_id)).fetchone()
        if row is None:
            return None
        return {'question_id': question_id, 'task_id': task_id,
                'message': row[0], 'options': json.loads(row[1]), 'created_at': row[2],
                'status': 'answered' if row[3] is not None else 'pending',
                'reply': None if row[3] is None else {
                    'text': row[3], 'source': row[5], 'sender_id': row[8],
                    'is_simulated': row[5] == 'local_mock', 'answered_at': row[4]},
                'channel': row[5], 'delivery_status': row[6], 'delivery_error': row[7],
                'is_simulated': row[5] == 'local_mock', 'poll_after_seconds': 3}

    def auto_reply(self, delay: float, text: str):
        with self.connect() as db:
            # 只推进 pending → answered；重复扫描不覆盖已有回答。
            result = db.execute('UPDATE questions SET reply=?, answered_at=? '
                                "WHERE channel='local_mock' AND reply IS NULL AND created_at<=?",
                                (text, time.time(), time.time() - delay))
            return result.rowcount

    def bind_telegram(self, identity):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT value FROM state WHERE key='telegram_identity'").fetchone()
            if row and row[0] != identity:
                raise ValueError('此数据库已绑定另一个 Telegram Bot/接收者，请使用不同的 --db。')
            db.execute("INSERT OR IGNORE INTO state VALUES ('telegram_identity',?)", (identity,))

    def lease(self, owner, release=False, channel='telegram'):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT value FROM state WHERE key=?", (channel + '_lease',)).fetchone()
            previous = json.loads(row[0]) if row else {}
            if previous.get('owner') != owner and previous.get('until', 0) > time.time():
                return False
            value = json.dumps({'owner': owner, 'until': 0 if release else time.time() + 45})
            db.execute("INSERT OR REPLACE INTO state VALUES (?,?)", (channel + '_lease', value))
            return True

    def recover_sending(self, channel='telegram'):
        with self.connect() as db:
            db.execute("UPDATE questions SET delivery_status='unknown', delivery_error=? "
                       "WHERE channel=? AND delivery_status='sending'",
                       ('服务曾在发送过程中退出，结果未知；不会自动重发。', channel))

    def claim_outgoing(self, channel='telegram'):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            cooldown = db.execute("SELECT value FROM state WHERE key=?", (channel + '_send_after',)).fetchone()
            if cooldown and float(cooldown[0]) > time.time():
                return None
            row = db.execute("SELECT question_id,task_id FROM questions WHERE channel=? "
                             "AND delivery_status='queued' AND reply IS NULL ORDER BY created_at LIMIT 1", (channel,)).fetchone()
            if not row:
                return None
            db.execute("UPDATE questions SET delivery_status='sending' WHERE question_id=?", (row[0],))
        return self.get(row[1], row[0])

    def delivered(self, question_id, status, message_id=None, error=None, retry_after=0):
        with self.connect() as db:
            db.execute('UPDATE questions SET delivery_status=?,telegram_message_id=?,delivery_error=? '
                       'WHERE question_id=?', (status, message_id, error, question_id))
            if retry_after:
                db.execute("INSERT OR REPLACE INTO state VALUES ('telegram_send_after',?)",
                           (str(time.time() + retry_after),))

    def offset(self):
        with self.connect() as db:
            row = db.execute("SELECT value FROM state WHERE key='telegram_offset'").fetchone()
        return int(row[0]) if row else 0

    def accept_update(self, update_id, reply=None):
        # 回答与游标同事务提交：崩溃后可重收，绝不先确认消息再丢失回答。
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT value FROM state WHERE key='telegram_offset'").fetchone()
            if row and update_id < int(row[0]):
                return
            if reply:
                qid, message_id, answer, sender_id = reply
                if qid:
                    predicate, value = 'question_id=?', qid
                else:
                    predicate, value = 'telegram_message_id=?', message_id
                db.execute(f"UPDATE questions SET reply=?,answered_at=?,sender_id=? WHERE {predicate} "
                           "AND channel='telegram' AND reply IS NULL",
                           (answer, time.time(), sender_id, value))
            db.execute("INSERT OR REPLACE INTO state VALUES ('telegram_offset',?)", (str(update_id + 1),))

    def bind_channel(self, channel, identity):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            key = channel + '_identity'
            row = db.execute('SELECT value FROM state WHERE key=?', (key,)).fetchone()
            if row and row[0] != identity:
                raise ValueError('数据库已绑定不同账号或接收者，请使用单独的 --db。')
            db.execute('INSERT OR IGNORE INTO state VALUES (?,?)', (key, identity))

    def state_get(self, key, default=''):
        with self.connect() as db:
            row = db.execute('SELECT value FROM state WHERE key=?', (key,)).fetchone()
        return row[0] if row else default

    def external_delivery(self, channel, question_id, status, message_id=None, error=None, retry_after=0):
        with self.connect() as db:
            db.execute('UPDATE questions SET delivery_status=?,external_message_id=?,delivery_error=? '
                       'WHERE question_id=? AND channel=?', (status, message_id, error, question_id, channel))
            if retry_after:
                db.execute('INSERT OR REPLACE INTO state VALUES (?,?)',
                           (channel + '_send_after', str(time.time() + retry_after)))

    def external_events(self, channel, events, states=None):
        # 同一平台消息只消费一次；回答、事件去重和游标一起提交，避免 ACK 后丢失回答。
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for event_id, qid, parent_id, answer, sender_id in events:
                inserted = db.execute('INSERT OR IGNORE INTO receipts VALUES (?,?)', (channel, event_id)).rowcount
                if not inserted or not answer:
                    continue
                predicate, value = ('question_id=?', qid) if qid else ('external_message_id=?', parent_id)
                db.execute(f'UPDATE questions SET reply=?,answered_at=?,sender_id=? WHERE {predicate} '
                           'AND channel=? AND reply IS NULL', (answer, time.time(), sender_id, value, channel))
            for key, value in (states or {}).items():
                db.execute('INSERT OR REPLACE INTO state VALUES (?,?)', (key, value))
