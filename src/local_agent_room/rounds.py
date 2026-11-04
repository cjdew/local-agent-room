"""Durable room budgets. These limit room activity, not external provider spending."""
import json
import re
import time

DURATION = 300
IDLE = 150
PER_AGENT = 4
TOTAL = 8
BODY = 4000
CONTEXT_CHARS = 16000
CONTEXT_BYTES = 60000
REQUESTS = 240
STOP = 'Round ended. Stop room activity and return control to the human; do not retry until they continue the round.'

SCHEMA = '''
CREATE TABLE IF NOT EXISTS rounds (
 id INTEGER PRIMARY KEY AUTOINCREMENT, room TEXT NOT NULL REFERENCES rooms(id),
 started REAL NOT NULL, deadline REAL NOT NULL, activity REAL NOT NULL,
 status TEXT NOT NULL DEFAULT 'active', floor INTEGER NOT NULL,
 request_id TEXT UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS round_turns (
 round_id INTEGER NOT NULL REFERENCES rounds(id), agent TEXT NOT NULL,
 key TEXT NOT NULL, status TEXT NOT NULL, reply_to INTEGER,
 PRIMARY KEY(agent,key)
);
CREATE TABLE IF NOT EXISTS round_readers (
 round_id INTEGER NOT NULL REFERENCES rounds(id), agent TEXT NOT NULL,
 cursor INTEGER NOT NULL, requests INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(round_id,agent)
);
CREATE UNIQUE INDEX IF NOT EXISTS once_per_incoming ON round_turns(agent,reply_to)
 WHERE reply_to IS NOT NULL;
'''


def acknowledgment(body):
    text = re.sub(r'(?<!\w)@[a-z][a-z0-9-]*', '', body.lower())
    text = re.sub(r'[^\w\s]', '', text)
    return ' '.join(text.split()) in {'ok', 'okay', 'thanks', 'thank you', 'got it',
        'ack', 'acknowledged', 'understood', 'agreed', 'sounds good', 'will do', 'yes', 'no', 'done', ''}


def bounded(rows, limit=20):
    result, chars, size = [], 0, 0
    for row in rows:
        item = dict(row)
        length = len(item['body'])
        encoded = len(json.dumps(item, ensure_ascii=False).encode())
        if len(result) >= min(limit,20) or chars + length > CONTEXT_CHARS or size + encoded > CONTEXT_BYTES:
            break
        result.append(item)
        chars += length
        size += encoded
    return result


class Rounds:
    def _round(self, db, room):
        row = db.execute('SELECT * FROM rounds WHERE room=? ORDER BY id DESC LIMIT 1', (room,)).fetchone()
        if not row:
            return None
        r = dict(row)
        used = db.execute('SELECT count(*) FROM round_turns WHERE round_id=?', (r['id'],)).fetchone()[0]
        stamp = time.time()
        reason = r['status']
        if reason == 'active':
            if stamp >= r['deadline']:
                reason = 'time_limit'
            elif stamp >= r['activity'] + IDLE:
                reason = 'idle_limit'
            elif used >= TOTAL:
                reason = 'reply_limit'
            if reason != 'active':
                db.execute('UPDATE rounds SET status=? WHERE id=?', (reason,r['id']))
        # Even final reserved worker turns cannot run past time/idle limits.
        if reason == 'reply_limit' and (stamp >= r['deadline'] or stamp >= r['activity'] + IDLE):
            reason = 'time_limit' if stamp >= r['deadline'] else 'idle_limit'
            db.execute('UPDATE rounds SET status=? WHERE id=?', (reason,r['id']))
        r.update(status=reason, used=used)
        return r

    def _state(self, db, room, actor='human'):
        r = self._round(db,room)
        counts = {} if not r else {x['agent']:x['n'] for x in db.execute(
            'SELECT agent,count(*) AS n FROM round_turns WHERE round_id=? GROUP BY agent',(r['id'],))}
        requests = 0
        if r and actor != 'human':
            reader = db.execute('SELECT requests FROM round_readers WHERE round_id=? AND agent=?',(r['id'],actor)).fetchone()
            requests = reader['requests'] if reader else 0
        reason = r['status'] if r else 'not_started'
        if actor != 'human' and reason == 'active':
            if counts.get(actor,0) >= PER_AGENT:
                reason = 'agent_reply_limit'
            elif requests >= REQUESTS:
                reason = 'request_limit'
        return {'id':r['id'] if r else None, 'room':room, 'active':reason=='active', 'reason':reason,
                'deadline':r['deadline'] if r else None, 'idle_deadline':r['activity']+IDLE if r else None,
                'used':r['used'] if r else 0, 'per_agent':counts, 'requests':requests,
                'limits':{'seconds':DURATION,'idle_seconds':IDLE,'per_agent':PER_AGENT,'total':TOTAL,
                          'message_chars':BODY,'context_chars':CONTEXT_CHARS,'context_bytes':CONTEXT_BYTES,
                          'context_messages':20,'requests_per_agent':REQUESTS},
                'instruction': 'Continue within the round limits.' if reason=='active' else STOP}

    def round_state(self, room, actor='human'):
        with self.db() as db:
            self.check_room(db,room)
            return self._state(db,room,actor)

    def control_round(self, actor, room, action, request_id):
        from .store import InputError
        if actor != 'human':
            raise InputError('Only the human can start, continue, or stop a round.')
        if action not in {'start','continue','stop'} or not isinstance(request_id,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}',request_id):
            raise InputError('Invalid round action or client_id.')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.check_room(db,room)
            r = self._round(db,room)
            if action == 'stop':
                if r:
                    db.execute("UPDATE rounds SET status='stopped' WHERE id=?",(r['id'],))
            else:
                old = db.execute('SELECT room FROM rounds WHERE request_id=?',(request_id,)).fetchone()
                if old:
                    if old['room'] != room:
                        raise InputError('client_id already used for a different room.')
                    return self._state(db,room)
                if r and r['status']=='active':
                    raise InputError('Stop or finish the current round before continuing.')
                # Only the latest 20 historical messages can be included as initial context.
                floor = db.execute('SELECT id FROM messages WHERE room=? ORDER BY id DESC LIMIT 1 OFFSET 20',(room,)).fetchone()
                stamp = time.time()
                db.execute('INSERT INTO rounds(room,started,deadline,activity,floor,request_id) VALUES(?,?,?,?,?,?)',
                           (room,stamp,stamp+DURATION,stamp,floor['id'] if floor else 0,request_id))
            return self._state(db,room)

    def _request(self, db, r, actor):
        db.execute('INSERT OR IGNORE INTO round_readers(round_id,agent,cursor) VALUES(?,?,?)',(r['id'],actor,r['floor']))
        db.execute('UPDATE round_readers SET requests=requests+1 WHERE round_id=? AND agent=?',(r['id'],actor))

    def charge_post(self, actor, room, key):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.check_room(db,room)
            r = self._round(db,room)
            state = self._state(db,room,actor)
            # Retries and an already-reserved worker response do not need a new turn slot.
            turn = db.execute('SELECT * FROM round_turns WHERE agent=? AND key=?',(actor,key)).fetchone()
            if r and turn and turn['round_id']==r['id'] and turn['status'] in {'reserved','posted'} and r['status'] in {'active','reply_limit'} and state['requests'] < REQUESTS:
                state.update(active=True)
            if state['active']:
                self._request(db,r,actor)
            return state

    def agent_read(self, actor, room, after=0, limit=100, charge=True, expected_round=None):
        from .store import InputError, integer
        integer(after)
        integer(limit,1,200)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.check_room(db,room)
            if not db.execute('SELECT id FROM principals WHERE id=? AND revoked=0',(actor,)).fetchone():
                raise InputError('Participant is unknown or revoked.')
            state = self._state(db,room,actor)
            if expected_round is not None and state['id'] != expected_round:
                state.update(active=False,reason='round_changed',instruction=STOP)
            empty = {'messages':[],'next_cursor':after,'has_more':False,'room':room,'round':state,
                     'round_ended':not state['active']}
            if not state['active']:
                return empty
            r = self._round(db,room)
            if charge:
                self._request(db,r,actor)
            reader = db.execute('SELECT cursor FROM round_readers WHERE round_id=? AND agent=?',(r['id'],actor)).fetchone()
            cursor = max(after, r['floor'], reader['cursor'] if reader else 0)
            rows = list(db.execute('''SELECT m.*,p.label AS author_label FROM messages m JOIN principals p ON p.id=m.author
                WHERE m.room=? AND m.id>? ORDER BY m.id LIMIT 21''',(room,cursor)))
            messages = bounded(rows,limit)
            if messages:
                cursor = messages[-1]['id']
                db.execute('UPDATE round_readers SET cursor=? WHERE round_id=? AND agent=?',(cursor,r['id'],actor))
            return {'messages':messages,'next_cursor':cursor,'has_more':len(rows)>len(messages),'room':room,
                    'round':state,'round_ended':False}

    def _turn(self, db, actor, room, key, reply, reserve=False):
        from .store import InputError
        r = self._round(db,room)
        old = db.execute('SELECT * FROM round_turns WHERE agent=? AND key=?',(actor,key)).fetchone()
        if old:
            if not r or old['round_id'] != r['id'] or old['status'] != 'reserved' or r['status'] not in {'active','reply_limit'}:
                raise InputError(STOP)
            if reply is not None and reply != old['reply_to']:
                raise InputError('Reserved turn belongs to a different incoming message.')
            return old['reply_to']
        state = self._state(db,room,actor)
        if not state['active']:
            raise InputError(state['reason'] + ': ' + STOP)
        if reserve:
            self._request(db,r,actor)
        if reply is None:
            incoming = db.execute('''SELECT id FROM messages WHERE room=? AND author!=? AND id>?
                AND NOT EXISTS(SELECT 1 FROM round_turns t WHERE t.agent=? AND t.reply_to=messages.id)
                ORDER BY id DESC LIMIT 1''',(room,actor,r['floor'],actor)).fetchone()
            reply = incoming['id'] if incoming else None
        if reply is not None:
            target = db.execute('SELECT author,body FROM messages WHERE id=? AND room=? AND id>?',(reply,room,r['floor'])).fetchone()
            if not target or target['author']==actor:
                raise InputError('Reply must reference another participant in the current context window.')
            if acknowledgment(target['body']):
                raise InputError('An acknowledgment alone cannot trigger another agent turn.')
            if db.execute('SELECT 1 FROM round_turns WHERE agent=? AND reply_to=?',(actor,reply)).fetchone():
                raise InputError('You have already responded to this incoming message.')
        elif state['per_agent'].get(actor,0):
            raise InputError('Wait for a new incoming message before posting again.')
        db.execute('INSERT INTO round_turns VALUES(?,?,?,?,?)',(r['id'],actor,key,'reserved' if reserve else 'posted',reply))
        return reply

    def worker_allowed(self, agent, message_id):
        key = f'wake-{agent}-{message_id}'
        with self.db() as db:
            turn = db.execute('''SELECT t.*,r.room FROM round_turns t JOIN rounds r ON r.id=t.round_id
                WHERE t.agent=? AND t.key=?''',(agent,key)).fetchone()
            if not turn or turn['status']!='reserved':
                return False
            r = self._round(db,turn['room'])
            return r['id']==turn['round_id'] and r['status'] in {'active','reply_limit'}
