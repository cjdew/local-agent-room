import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from local_agent_room.bridge import Client
from local_agent_room.mcp import serve
from local_agent_room.server import RoomServer
from local_agent_room.store import InputError, Store, write_private
from local_agent_room.wake import addressed, execute, load_config


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.token = self.store.add_agent('codex', 'Codex')

    def post(self, **kw):
        return self.store.post('human', {'body':'Hello', 'client_id':'one', **kw})

    def test_idempotency_and_conflict(self):
        a = self.post()
        b = self.post()
        self.assertEqual(a['message']['id'], b['message']['id'])
        self.assertTrue(b['duplicate'])
        with self.assertRaises(InputError):
            self.post(body='Different')

    def test_concurrent_delivery(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            replies = list(pool.map(lambda _: self.post(), range(20)))
        self.assertEqual(len({r['message']['id'] for r in replies}), 1)
        self.assertEqual(sum(not r['duplicate'] for r in replies), 1)

    def test_pagination_restart_and_revision(self):
        for i in range(12):
            self.post(body=str(i), client_id=str(i), revision='A'*40)
        page = Store(self.temp.name).read('general', limit=5)
        self.assertTrue(page['has_more'])
        self.assertEqual([m['body'] for m in page['messages']], ['0','1','2','3','4'])
        next_page = self.store.read('general', page['next_cursor'], 10)
        self.assertEqual(len(next_page['messages']), 7)
        self.assertEqual(next_page['messages'][0]['revision'], 'a'*40)
        self.assertFalse(next_page['has_more'])
        self.assertEqual([m['body'] for m in self.store.read('general', limit=2, tail=True)['messages']], ['10','11'])

    def test_input_and_cross_room_reply(self):
        id_ = self.post()['message']['id']
        self.store.create_room('design', 'Design')
        for kw in [{'author':'codex'}, {'revision':'main'}, {'body':''}, {'body':'x'*12001},
                   {'reply_to':True}, {'room':'design', 'reply_to':id_}, {'room':'../private'}]:
            with self.subTest(kw=kw), self.assertRaises(InputError):
                self.post(client_id='two', **kw)

    def test_credentials_and_revocation(self):
        self.assertEqual(self.store.authenticate(self.token), 'codex')
        self.assertNotIn(self.token, self.store.path.read_bytes().decode(errors='ignore'))
        self.store.revoke('codex')
        self.assertIsNone(self.store.authenticate(self.token))
        with self.assertRaises(InputError):
            self.store.post('codex', {'body':'Hi', 'client_id':'c'})

    def test_automation_claim_epoch_and_duplicates(self):
        mid = self.post()['message']['id']
        self.assertFalse(self.store.claim('codex', mid, 0))
        state = self.store.automation(True)
        self.assertTrue(self.store.claim('codex', mid, state['epoch']))
        self.assertFalse(self.store.claim('codex', mid, state['epoch']))
        self.store.finish('codex', mid, 'completed')
        self.store.automation(False)
        newer = self.store.automation(True)
        self.assertNotEqual(newer['epoch'], state['epoch'])
        self.assertEqual(newer['jobs'][0]['status'], 'completed')

    def test_cli_failure_exit_status(self):
        result = subprocess.run([sys.executable, str(ROOT/'room.py'), '--data-dir', self.temp.name,
                                 'revoke-agent', 'missing'], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertIn('Unknown participant', result.stderr)


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.token = self.store.add_agent('claude', 'Claude')
        self.server = RoomServer(self.store, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        config = Path(self.temp.name) / 'server.json'
        write_private(config, {'port': self.server.server_port})
        self.credential = Path(self.temp.name) / 'agent.json'
        write_private(self.credential, {'server_config':str(config), 'token':self.token})

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path='/api/rooms', method='GET', body=None, token=None, headers=None):
        conn = HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            h = {'Content-Type':'application/json'}
            if token is not None:
                h['Authorization'] = 'Bearer ' + token
            h.update(headers or {})
            conn.request(method, path, json.dumps(body) if body is not None else None, h)
            response = conn.getresponse()
            raw = response.read()
            return response.status, raw, dict(response.getheaders())
        finally:
            conn.close()

    def test_auth_host_origin_and_headers(self):
        self.assertEqual(self.request()[0], 401)
        self.assertEqual(self.request(token='invalid')[0], 401)
        self.assertEqual(self.request(token=self.token)[0], 200)
        self.assertEqual(self.request(token=self.token, headers={'Host':'evil.test'})[0], 403)
        self.assertEqual(self.request(token=self.token, headers={'Origin':'https://evil.test'})[0], 403)
        self.assertEqual(self.request(token=self.token, headers={'Origin':'null'})[0], 403)
        self.assertEqual(self.request(token=self.token, headers={'Sec-Fetch-Site':'cross-site'})[0], 403)
        status, body, headers = self.request('/')
        self.assertEqual(status, 200)
        self.assertIn("default-src 'none'", headers['Content-Security-Policy'])
        self.assertNotIn(self.server.browser_token.encode(), body)
        self.assertEqual(self.request('/../store.py')[0], 404)

    def test_identity_human_controls_and_revocation(self):
        payload = {'body':'Review this', 'client_id':'first'}
        status, raw, _ = self.request('/api/messages', 'POST', payload, self.token)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)['message']['author'], 'claude')
        payload['author'] = 'human'
        self.assertEqual(self.request('/api/messages', 'POST', payload, self.token)[0], 400)
        self.assertEqual(self.request('/api/rooms', 'POST', {'id':'new','title':'New'}, self.token)[0], 403)
        self.assertEqual(self.request('/api/automation', 'POST', {'enabled':True}, self.token)[0], 403)
        self.assertEqual(self.request('/api/automation', 'POST', {'enabled':True}, self.server.browser_token)[0], 200)
        self.store.revoke('claude')
        self.assertEqual(self.request(token=self.token)[0], 401)

    def test_mcp_subprocess_round_trip(self):
        requests = [
            {'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'test','version':'1'}}},
            {'jsonrpc':'2.0','method':'notifications/initialized'},
            {'jsonrpc':'2.0','id':2,'method':'tools/list'},
            {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'room_post','arguments':{'body':'Hello from MCP','client_id':'mcp-one'}}},
            {'jsonrpc':'2.0','id':4,'method':'tools/call','params':{'name':'room_read','arguments':{}}},
        ]
        result = subprocess.run([sys.executable, str(ROOT/'room.py'), 'mcp', '--credential',str(self.credential)],
            input='\n'.join(json.dumps(r) for r in requests)+'\n', capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(replies), 4)
        self.assertEqual(len(replies[1]['result']['tools']), 4)
        self.assertFalse(replies[2]['result']['isError'])
        data = json.loads(replies[3]['result']['content'][0]['text'])
        self.assertEqual(data['messages'][0]['author'], 'claude')
        self.assertNotIn(self.token, result.stdout + result.stderr)

    def test_mcp_invalid_requests_and_unavailable(self):
        source = io.BytesIO(b'bad json\n[]\n' + json.dumps({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':[]}}).encode() + b'\n' +
                           json.dumps({'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':[]}}).encode() + b'\n')
        dest = io.StringIO()
        serve(Client(self.credential), source, dest)
        replies = [json.loads(line) for line in dest.getvalue().splitlines()]
        self.assertEqual(replies[0]['error']['code'], -32700)
        self.assertEqual(replies[1]['error']['code'], -32600)
        self.assertTrue(replies[3]['result']['isError'])


@unittest.skipUnless(os.name == 'posix', 'Wake requires POSIX')
class WakeTests(unittest.TestCase):
    setUp = StoreTests.setUp
    post = StoreTests.post
    def test_mention_boundaries(self):
        for body in ['@codex please review', 'Hi @codex, review?', '(@codex)']:
            self.assertTrue(addressed(body, 'codex'))
        for body in ['email@codex.com', '@codex-other', '@@codex', 'codex']:
            self.assertFalse(addressed(body, 'codex'))

    def test_config_requires_absolute_executable(self):
        p = Path(self.temp.name) / 'worker.json'
        p.write_text(json.dumps({'argv':['python3'], 'cwd':self.temp.name}))
        with self.assertRaises(InputError):
            load_config(p)

    def test_worker_execution_timeout_stop_and_output(self):
        state = self.store.automation(True)
        def command(code, timeout=2):
            return execute(self.store, 'codex', state['epoch'], [sys.executable,'-c',code], self.temp.name, timeout, 'hello')
        status, body = command('import sys; print(sys.stdin.read().upper())')
        self.assertEqual((status, body), ('completed','HELLO'))
        self.assertEqual(command('import time; time.sleep(5)', 1)[0], 'timed-out')
        self.assertEqual(command('print("x"*13000)')[0], 'invalid-output')
        self.store.automation(False)
        self.assertEqual(command('import time; time.sleep(5)')[0], 'stopped')

    def test_two_workers_exchange_and_budget(self):
        self.store.add_agent('claude', 'Claude')
        processes = []
        try:
            for agent, other in [('codex','claude'), ('claude','codex')]:
                path = Path(self.temp.name)/(agent+'.json')
                path.write_text(json.dumps({'argv':[sys.executable,'-c',f'import sys; sys.stdin.read(); print("@{other} please review this result.")'], 'cwd':self.temp.name,'timeout':3}))
                p = subprocess.Popen([sys.executable,str(ROOT/'room.py'),'--data-dir',self.temp.name,'wake','--agent',agent,'--config',str(path),'--max-turns','2'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                processes.append(p)
                self.assertIn('Watching', p.stdout.readline())
            self.store.automation(True)
            self.post(body='@codex start the review')
            for p in processes:
                out, err = p.communicate(timeout=12)
                self.assertEqual(p.returncode, 0, err)
                self.assertIn('Turn budget exhausted', out)
            messages = self.store.read('general')['messages']
            self.assertEqual([m['author'] for m in messages], ['human','codex','claude','codex','claude'])
            self.assertEqual(len(self.store.automation()['jobs']), 4)
            self.assertTrue(all(j['status']=='completed' for j in self.store.automation()['jobs']))
        finally:
            for p in processes:
                if p.poll() is None:
                    p.kill()
                p.communicate()


if __name__ == '__main__':
    unittest.main()
