"""Authenticated loopback client; never follows redirects or uses HTTP proxies."""
import json
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import urlencode

from .store import InputError, integer


class Client:
    def __init__(self, credential):
        data = json.loads(Path(credential).expanduser().read_text())
        self.token = data['token']
        self.config_path = Path(data['server_config'])

    def request(self, method, path, data=None):
        config = json.loads(self.config_path.read_text())
        port = integer(config['port'], 1, 65535)
        conn = HTTPConnection('127.0.0.1', port, timeout=15)
        try:
            conn.request(method, path, body=None if data is None else json.dumps(data, ensure_ascii=False).encode(),
                         headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
            response = conn.getresponse()
            raw = response.read(16_000_001)
            if len(raw) > 16_000_000:
                raise InputError('Server response exceeds limit.')
            result = json.loads(raw)
            if response.status != 200:
                raise InputError(result.get('error', 'Request failed.'))
            return result
        finally:
            conn.close()

    def read(self, room='general', after=0, limit=100, tail=False):
        return self.request('GET', '/api/messages?' + urlencode(
            {'room': room, 'after': after, 'limit': limit, 'tail': int(tail)}))
