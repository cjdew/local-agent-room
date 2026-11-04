"""Local CLI. Runtime state belongs outside the source checkout."""
import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .store import InputError, Store, integer, slug, write_private


def main(argv=None):
    parser = argparse.ArgumentParser(description='Local web chat and MCP bridge for your agents.')
    parser.add_argument('--version', action='version', version=__version__)
    parser.add_argument('--data-dir', type=Path, default=Path.home() / '.local-agent-room')
    sub = parser.add_subparsers(dest='action', required=True)
    server = sub.add_parser('serve', help='Start the loopback web app')
    server.add_argument('--port', type=int, default=8765)
    add = sub.add_parser('add-agent', help='Create a private MCP credential')
    add.add_argument('name')
    add.add_argument('--label')
    invite = sub.add_parser('invite', help='Print a private browser link for an existing agent')
    invite.add_argument('name')
    invite.add_argument('--room', default='general')
    revoke = sub.add_parser('revoke-agent')
    revoke.add_argument('name')
    mcp = sub.add_parser('mcp', help='Run the stdio MCP bridge')
    mcp.add_argument('--credential', required=True, type=Path)
    export = sub.add_parser('export', help='Export all local room data to stdout (contains private chat)')
    export.add_argument('--room', default='general')
    wake = sub.add_parser('wake', help='Opt-in local command worker; see docs/wake.md')
    wake.add_argument('--agent', required=True)
    wake.add_argument('--room', default='general')
    wake.add_argument('--config', required=True, type=Path)
    wake.add_argument('--max-turns', type=int, default=6)
    args = parser.parse_args(argv)
    try:
        if args.action == 'mcp':
            from .bridge import Client
            from .mcp import serve
            serve(Client(args.credential))
            return 0
        store = Store(args.data_dir)
        if args.action == 'add-agent':
            token = store.add_agent(args.name, args.label or args.name.capitalize())
            credentials = store.directory / 'credentials'
            credentials.mkdir(mode=0o700, exist_ok=True)
            path = credentials / (args.name + '.json')
            write_private(path, {'token': token, 'server_config': str(store.directory / 'server.json')})
            print(f'Credential written: {path}\nKeep this file private. It grants access to all rooms.')
        elif args.action == 'invite':
            slug(args.name)
            store.read(args.room, limit=1)
            credential = store.directory / 'credentials' / (args.name + '.json')
            data = json.loads(credential.read_text())
            if store.authenticate(data['token']) != args.name:
                raise InputError('Credential is invalid or revoked.')
            config = json.loads((store.directory / 'server.json').read_text())
            port = integer(config['port'], 1, 65535)
            print(f'Private browser invite for @{args.name} (all-room read/post access):\n'
                  f'http://127.0.0.1:{port}/#token={data["token"]}&room={args.room}\n'
                  'Give this only to the intended agent. Its browser must run on this machine.\n'
                  'Ask it to stay active, read/reply/check until your agreed deadline or stop message.\n'
                  'No MCP or wake worker is required for browser participation.')
        elif args.action == 'revoke-agent':
            store.revoke(args.name)
            print('Credential revoked.')
        elif args.action == 'export':
            cursor = 0
            while True:
                page = store.read(args.room, cursor, 200)
                for message in page['messages']:
                    print(json.dumps(message))
                if not page['has_more']:
                    break
                cursor = page['next_cursor']
        elif args.action == 'wake':
            from .wake import run
            run(store, args.agent, args.room, args.config, args.max_turns)
        else:
            from .server import RoomServer
            if not 1 <= args.port <= 65535:
                raise InputError('Port must be between 1 and 65535.')
            with RoomServer(store, args.port) as server:
                store.automation(False)
                write_private(store.directory / 'server.json', {'port': server.server_port})
                print(f'Local Agent Room {__version__}\nOpen this private link in your browser:\n'
                      f'http://127.0.0.1:{server.server_port}/#token={server.browser_token}\n'
                      'Keep this terminal open. Ctrl+C stops the server.', flush=True)
                try:
                    server.serve_forever()
                finally:
                    store.automation(False)
    except KeyboardInterrupt:
        return 0
    except (InputError, OSError, ValueError, KeyError) as e:
        # Never emit raw config contents or credential values in errors.
        print(str(e) if isinstance(e, InputError) else 'Operation failed. Check paths, port, and configuration.', file=sys.stderr)
        return 1
    return 0
