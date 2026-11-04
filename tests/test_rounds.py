"""Budget invariants, including concurrent writers and durable delivery cursors."""
import json
import os
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from local_agent_room.store import Store, InputError
from local_agent_room.rounds import IDLE, REQUESTS


class RoundTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.store.add_agent('claude','Claude')
        self.store.add_agent('codex','Codex')
        self.sequence = 0

    def post(self, actor='human', body='Please inspect the implementation.', **kwargs):
        self.sequence += 1
        return self.store.post(actor,{'body':body,'client_id':str(self.sequence),**kwargs})

    def start(self, id_='round-one'):
        return self.store.control_round('human','general','start',id_)

    def test_human_only_start_continue_and_stop(self):
        self.assertFalse(self.store.round_state('general')['active'])
        with self.assertRaises(InputError): self.post('claude')
        with self.assertRaises(InputError): self.store.control_round('claude','general','start','bad')
        r = self.start()
        self.assertEqual(self.start()['id'],r['id'])
        with self.assertRaises(InputError): self.start('reset-attempt')
        self.store.control_round('human','general','stop','stop-one')
        empty = self.store.agent_read('claude','general')
        self.assertTrue(empty['round_ended'])
        renewed = self.store.control_round('human','general','continue','round-two')
        self.assertGreater(renewed['id'],r['id'])
        self.assertEqual(renewed['used'],0)

    def test_four_per_agent_retry_and_restart(self):
        r = self.start()
        last = None
        for i in range(4):
            self.post(body=f'Question number {i}')
            last = self.post('claude',body=f'Substantive finding number {i}')['message']
        self.post(body='One more question')
        with self.assertRaises(InputError): self.post('claude',body='Fifth answer is not permitted')
        retry = self.store.post('claude',{k:last[k] for k in ['room','body','client_id','revision','reply_to']})
        self.assertTrue(retry['duplicate'])
        reloaded = Store(self.temp.name)
        self.assertEqual(reloaded.round_state('general')['id'],r['id'])
        self.assertEqual(reloaded.round_state('general')['used'],4)
        self.assertEqual(reloaded.round_state('general','claude')['reason'],'agent_reply_limit')
        self.assertEqual(len(reloaded.read('general')['messages']),9)

    def test_eight_total_concurrent_and_reservations(self):
        self.start()
        incoming = self.post()['message']['id']
        names = [f'agent-{i}' for i in range(12)]
        for n in names: self.store.add_agent(n,n)
        def post(n):
            try:
                self.store.post(n,{'body':'My independent assessment','client_id':n,'reply_to':incoming})
                return True
            except InputError: return False
        with ThreadPoolExecutor(max_workers=12) as pool:
            self.assertEqual(sum(pool.map(post,names)),8)
        self.assertEqual(self.store.round_state('general')['used'],8)
        self.assertEqual(self.store.round_state('general')['reason'],'reply_limit')

    def test_idle_150_seconds_deadline_and_human_messages_do_not_renew(self):
        self.assertEqual(IDLE,150)
        r = self.start()
        self.assertAlmostEqual(r['deadline']-time.time(),300,delta=2)
        with self.store.db() as db: db.execute('UPDATE rounds SET activity=?',(time.time()-151,))
        self.post(body='This must not renew an expired round')
        self.assertEqual(self.store.round_state('general')['reason'],'idle_limit')
        self.start('another-round')
        with self.store.db() as db: db.execute('UPDATE rounds SET deadline=? WHERE id=(SELECT max(id) FROM rounds)',(time.time()-1,))
        self.assertEqual(self.store.round_state('general')['reason'],'time_limit')
        with self.assertRaises(InputError): self.post('codex')

    def test_acknowledgments_self_replies_duplicates_and_length(self):
        self.start()
        incoming = self.post()['message']['id']
        for body in ['@codex thanks!', 'Got it.', 'Agreed', 'x'*4001]:
            with self.assertRaises(InputError): self.post('claude',body=body)
        answer = self.post('claude',body='There is a reproducible failure.',reply_to=incoming)['message']['id']
        with self.assertRaises(InputError): self.post('claude',body='Repeating my answer',reply_to=incoming)
        with self.assertRaises(InputError): self.post('claude',body='Answering myself',reply_to=answer)
        thanks = self.post(body='@codex thanks')['message']['id']
        with self.assertRaises(InputError): self.post('codex',body='This acknowledgment must not trigger me',reply_to=thanks)
        self.assertEqual(self.store.round_state('general')['used'],1)

    def test_delivery_bounds_replay_restart_and_initial_window(self):
        for i in range(30): self.post(body=str(i)+'x'*3000)
        self.start()
        page = self.store.agent_read('claude','general',0,200)
        self.assertLessEqual(len(page['messages']),20)
        self.assertLessEqual(sum(len(m['body']) for m in page['messages']),16000)
        self.assertTrue(page['has_more'])
        self.assertEqual(page['messages'][0]['id'],11)
        again = Store(self.temp.name).agent_read('claude','general',0,200)
        self.assertGreater(again['messages'][0]['id'],page['messages'][-1]['id'])
        while again['has_more']: again = self.store.agent_read('claude','general',0,200)
        self.assertEqual(self.store.agent_read('claude','general',0)['messages'],[])
        self.assertTrue(self.store.agent_read('codex','general',0)['messages'])

    def test_multibyte_payload_bounded(self):
        self.start()
        for _ in range(6): self.post(body='🌍'*4000)
        page = self.store.agent_read('claude','general')
        self.assertLess(len(json.dumps(page,ensure_ascii=False).encode()),65536)
        self.assertTrue(page['has_more'])

    def test_request_budget_and_wait_stops(self):
        self.start()
        for _ in range(REQUESTS): self.store.agent_read('claude','general')
        result = self.store.wait('claude','general',0,timeout=1)
        self.assertTrue(result['round_ended'])
        self.assertEqual(result['round']['reason'],'request_limit')
        with ThreadPoolExecutor(max_workers=1) as pool:
            waiting = pool.submit(self.store.wait,'codex','general',0,100,5)
            time.sleep(0.1)
            self.store.control_round('human','general','stop','manual-stop')
            result = waiting.result(timeout=2)
            self.assertTrue(result['round_ended'])
            self.assertEqual(result['round']['reason'],'stopped')

    def test_worker_reservations_count_failures_and_stop(self):
        self.start()
        epoch = self.store.automation(True)['epoch']
        for i in range(4):
            mid = self.post(body=f'@claude inspect request {i}')['message']['id']
            self.assertTrue(self.store.claim('claude',mid,epoch))
            self.assertTrue(self.store.worker_allowed('claude',mid))
            self.store.finish('claude',mid,'failed')
            self.assertFalse(self.store.claim('claude',mid,epoch))
        mid = self.post(body='@claude another request')['message']['id']
        self.assertFalse(self.store.claim('claude',mid,epoch))
        self.assertEqual(self.store.round_state('general')['used'],4)
        self.assertTrue(self.store.claim('codex',mid,epoch))
        self.store.control_round('human','general','stop','stop-workers')
        self.assertFalse(self.store.worker_allowed('codex',mid))

    def test_reserved_final_turn_posts_without_double_count(self):
        self.start()
        epoch = self.store.automation(True)['epoch']
        for i in range(8):
            name = f'worker-{i}'
            self.store.add_agent(name,name)
            mid = self.post(body=f'Please handle task {i}')['message']['id']
            self.assertTrue(self.store.claim(name,mid,epoch))
        self.assertTrue(self.store.worker_allowed('worker-7',mid))
        self.store.post('worker-7',{'body':'Final reserved result','client_id':f'wake-worker-7-{mid}','reply_to':mid})
        self.assertEqual(self.store.round_state('general')['used'],8)

    @unittest.skipUnless(os.name=='posix','Worker process groups require POSIX')
    def test_stop_terminates_running_worker_and_renewal_rejects_old_result(self):
        from local_agent_room.wake import execute
        self.start()
        epoch = self.store.automation(True)['epoch']
        mid = self.post()['message']['id']
        self.assertTrue(self.store.claim('claude',mid,epoch))
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(execute,self.store,'claude',epoch,[sys.executable,'-c','import time; time.sleep(20)'],self.temp.name,30,'Review this',mid)
            time.sleep(0.3)
            self.store.control_round('human','general','stop','stop-process')
            self.assertEqual(running.result(timeout=3)[0],'stopped')
        self.start('renew-process')
        with self.assertRaises(InputError):
            self.store.post('claude',{'body':'Old result must not enter the new round','client_id':f'wake-claude-{mid}','reply_to':mid})


if __name__ == '__main__': unittest.main()
