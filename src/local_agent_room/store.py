"""Small transactional store. Tokens and sender identities are never supplied by messages."""
import hashlib
import json
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from .rounds import Rounds, SCHEMA, BODY, STOP, acknowledgment

SLUG = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
MAX_BODY = 12000


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class InputError(ValueError):
    pass


def slug(value):
    if not isinstance(value, str) or not SLUG.fullmatch(value):
        raise InputError("Use 1–40 lowercase letters, numbers, or hyphens; start with a letter.")
    return value


def integer(value, minimum=0, maximum=2**53 - 1):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise InputError("Integer out of range.")
    return value


def write_private(path, data):
    """Replace private configuration atomically in its already-private directory."""
    path = Path(path)
    temp = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    try:
        with temp.open("x", encoding="utf-8") as f:
            temp.chmod(0o600)
            f.write(json.dumps(data, indent=2) + "\n")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


class Store(Rounds):
    def __init__(self, directory):
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        self.path = self.directory / "room.sqlite3"
        with self.db() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS principals (
                    id TEXT PRIMARY KEY, label TEXT NOT NULL,
                    token_hash TEXT UNIQUE, revoked INTEGER NOT NULL DEFAULT 0,
                    last_seen TEXT
                );
                CREATE TABLE IF NOT EXISTS rooms (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    room TEXT NOT NULL REFERENCES rooms(id),
                    author TEXT NOT NULL REFERENCES principals(id),
                    body TEXT NOT NULL, revision TEXT NOT NULL DEFAULT '',
                    reply_to INTEGER REFERENCES messages(id),
                    client_id TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(author, client_id)
                );
                CREATE INDEX IF NOT EXISTS messages_room_id ON messages(room, id);
                CREATE TABLE IF NOT EXISTS automation (
                    id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL DEFAULT 0,
                    epoch INTEGER NOT NULL DEFAULT 0
                );
                INSERT OR IGNORE INTO automation(id) VALUES(1);
                CREATE TABLE IF NOT EXISTS jobs (
                    agent TEXT NOT NULL REFERENCES principals(id),
                    message_id INTEGER NOT NULL REFERENCES messages(id),
                    status TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(agent,message_id)
                );
            """)
            db.execute("INSERT OR IGNORE INTO principals(id,label) VALUES('human','You')")
            db.execute("INSERT OR IGNORE INTO rooms VALUES('general','General',?)", (now(),))
            db.executescript(SCHEMA)
        self.path.chmod(0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def add_agent(self, name, label):
        slug(name)
        if name in {"human", "all", "you", "system"}:
            raise InputError("That participant name is reserved.")
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 60:
            raise InputError("Label must contain 1–60 characters.")
        token = secrets.token_urlsafe(32)
        with self.db() as db:
            try:
                db.execute("INSERT INTO principals(id,label,token_hash) VALUES(?,?,?)",
                           (name, label.strip(), hashlib.sha256(token.encode()).hexdigest()))
            except sqlite3.IntegrityError:
                raise InputError("Participant already exists. Revoke it and use a new name.") from None
        return token

    def revoke(self, name):
        if name == "human":
            raise InputError("The browser credential rotates when the server restarts.")
        with self.db() as db:
            if db.execute("UPDATE principals SET revoked=1 WHERE id=?", (name,)).rowcount != 1:
                raise InputError("Unknown participant.")

    def authenticate(self, token):
        if not isinstance(token, str) or not 20 <= len(token) <= 200:
            return None
        with self.db() as db:
            row = db.execute("SELECT id FROM principals WHERE token_hash=? AND revoked=0",
                             (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
            if row:
                db.execute("UPDATE principals SET last_seen=? WHERE id=?", (now(), row['id']))
                return row['id']
        return None

    def participants(self):
        with self.db() as db:
            return [dict(r) for r in db.execute(
                "SELECT id,label,revoked,last_seen FROM principals ORDER BY id")]

    def is_active(self, actor):
        with self.db() as db:
            return bool(db.execute('SELECT id FROM principals WHERE id=? AND revoked=0', (actor,)).fetchone())

    def wait(self, actor, room, after, limit=100, timeout=25):
        """Long-poll without holding a transaction; direct worker writes are visible too."""
        integer(timeout, 1, 50)
        deadline = time.monotonic() + timeout
        first = True
        round_id = None
        while True:
            if not self.is_active(actor):
                raise InputError('Participant is unknown or revoked.')
            page = self.read(room, after, limit) if actor == 'human' else self.agent_read(
                actor, room, after, limit, charge=first, expected_round=round_id)
            if first and actor != 'human':
                round_id = page['round']['id']
            first = False
            if page.get('round_ended'):
                return {**page, 'timed_out': False}
            if page['messages']:
                return {**page, 'timed_out': False}
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {**page, 'timed_out': True}
            time.sleep(min(0.25, remaining))

    def rooms(self):
        with self.db() as db:
            return [dict(r) for r in db.execute("""SELECT r.*,
                (SELECT count(*) FROM messages m WHERE m.room=r.id) AS message_count
                FROM rooms r ORDER BY r.created_at,r.id""")]

    def create_room(self, name, title):
        slug(name)
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 80:
            raise InputError("Title must contain 1–80 characters.")
        with self.db() as db:
            try:
                db.execute("INSERT INTO rooms VALUES(?,?,?)", (name, title.strip(), now()))
            except sqlite3.IntegrityError:
                raise InputError("Room already exists.") from None
        return {"id": name, "title": title.strip()}

    @staticmethod
    def check_room(db, room):
        slug(room)
        if not db.execute("SELECT id FROM rooms WHERE id=?", (room,)).fetchone():
            raise InputError("Unknown room.")

    def read(self, room, after=0, limit=100, tail=False):
        integer(after)
        integer(limit, 1, 200)
        with self.db() as db:
            self.check_room(db, room)
            order = "DESC" if tail else "ASC"
            rows = [dict(r) for r in db.execute(f"""SELECT m.*, p.label AS author_label
                FROM messages m JOIN principals p ON p.id=m.author
                WHERE room=? AND m.id>? ORDER BY m.id {order} LIMIT ?""", (room, after, limit + 1))]
        more = len(rows) > limit
        rows = rows[:limit]
        if tail:
            rows.reverse()
        return {"messages": rows, "next_cursor": rows[-1]['id'] if rows else after,
                "has_more": more, "room": room}

    def post(self, author, data):
        if not isinstance(data, dict) or set(data) - {"room", "body", "revision", "reply_to", "client_id"}:
            raise InputError("Unknown message fields. Sender identity is assigned by the server.")
        room = data.get("room", "general")
        body = data.get("body")
        revision = data.get("revision", "")
        client_id = data.get("client_id")
        reply = data.get("reply_to")
        if not isinstance(body, str) or not 1 <= len(body.strip()) <= MAX_BODY:
            raise InputError(f"Message must contain 1–{MAX_BODY} characters.")
        if not isinstance(revision, str) or (revision and not re.fullmatch(r"[a-fA-F0-9]{40}|[a-fA-F0-9]{64}", revision)):
            raise InputError("Revision must be a full 40- or 64-character Git commit SHA, or empty.")
        if not isinstance(client_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", client_id):
            raise InputError("A stable client_id of 1–100 letters, numbers, underscores, or hyphens is required.")
        if reply is not None:
            integer(reply, 1)
        body = body.strip()
        revision = revision.lower()
        if author != 'human':
            state = self.charge_post(author, room, client_id)
            if not state['active']:
                raise InputError(state['reason'] + ': ' + STOP)
            if len(body) > BODY:
                raise InputError(f'Agent messages are limited to {BODY} characters.')
            if acknowledgment(body):
                raise InputError('Post a substantive response; acknowledgment-only messages are blocked.')
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute('SELECT id FROM principals WHERE id=? AND revoked=0', (author,)).fetchone():
                raise InputError('Participant is unknown or revoked.')
            self.check_room(db, room)
            if reply is not None and not db.execute("SELECT id FROM messages WHERE id=? AND room=?", (reply, room)).fetchone():
                raise InputError("Reply must reference a message in this room.")
            old = db.execute("SELECT * FROM messages WHERE author=? AND client_id=?", (author, client_id)).fetchone()
            if old:
                if any(old[k] != v for k, v in {"room":room, "body":body, "revision":revision}.items()) or (reply is not None and old['reply_to'] != reply):
                    raise InputError("client_id was already used for a different message.")
                return {"message": dict(old), "duplicate": True}
            if author != 'human':
                reply = self._turn(db, author, room, client_id, reply)
            cursor = db.execute("""INSERT INTO messages(room,author,body,revision,reply_to,client_id,created_at)
                VALUES(?,?,?,?,?,?,?)""", (room, author, body, revision, reply, client_id, now()))
            row = db.execute("SELECT * FROM messages WHERE id=?", (cursor.lastrowid,)).fetchone()
            if author != 'human':
                db.execute("UPDATE round_turns SET status='posted' WHERE agent=? AND key=?", (author,client_id))
            # Expire first: a late human message must not silently restart an idle round.
            active_round = self._round(db,room)
            if active_round and active_round['status'] in {'active','reply_limit'} and not acknowledgment(body):
                db.execute('UPDATE rounds SET activity=? WHERE id=?',(time.time(),active_round['id']))
        return {"message": dict(row), "duplicate": False}

    def automation(self, enabled=None):
        with self.db() as db:
            if enabled is not None:
                if not isinstance(enabled, bool):
                    raise InputError('enabled must be a boolean.')
                db.execute('UPDATE automation SET enabled=?,epoch=epoch+1 WHERE id=1', (int(enabled),))
            state = dict(db.execute('SELECT enabled,epoch FROM automation WHERE id=1').fetchone())
            state['enabled'] = bool(state['enabled'])
            state['jobs'] = [dict(r) for r in db.execute('SELECT * FROM jobs ORDER BY updated_at DESC LIMIT 30')]
            return state

    def claim(self, agent, message_id, epoch):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT id FROM automation WHERE enabled=1 AND epoch=?', (epoch,)).fetchone():
                return False
            if not db.execute('SELECT id FROM principals WHERE id=? AND revoked=0', (agent,)).fetchone():
                return False
            message = db.execute('SELECT room,author,body FROM messages WHERE id=?',(message_id,)).fetchone()
            if not message or message['author']==agent or acknowledgment(message['body']):
                return False
            if db.execute('SELECT 1 FROM jobs WHERE agent=? AND message_id=?',(agent,message_id)).fetchone():
                return False
            try:
                self._turn(db,agent,message['room'],f'wake-{agent}-{message_id}',message_id,reserve=True)
            except InputError:
                return False
            return db.execute('INSERT OR IGNORE INTO jobs VALUES(?,?,?,?)',
                              (agent, message_id, 'running', now())).rowcount == 1

    def finish(self, agent, message_id, status):
        with self.db() as db:
            db.execute('UPDATE jobs SET status=?,updated_at=? WHERE agent=? AND message_id=?',
                       (status, now(), agent, message_id))
            db.execute("UPDATE round_turns SET status=? WHERE agent=? AND key=? AND status='reserved'",
                       (status,agent,f'wake-{agent}-{message_id}'))
