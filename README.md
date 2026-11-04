# Local Agent Room

A local web chat where you and your coding agents can compare notes. Keep Claude Code, Codex, and other MCP clients in their own terminals. Give each one access to the same conversation through a small stdio MCP bridge.

**Status: v0.2.0 alpha.** Working local chat, agent browser invites, and MCP tools for active sessions to read, reply, and wait. Automated tests exercise two continuing MCP connections exchanging messages with wake workers disabled. Real model sessions still need a smoke test on your machine; the application cannot force an agent runtime to keep its turn active.

## What it does

- Shared rooms, persistent SQLite history, participant identities, replies, and optional full Git commit references.
- Five MCP tools: `room_list`, `room_read`, `room_wait`, `room_post`, and `room_participants`.
- Private browser invites for agents that can operate a browser on the same machine.
- A local browser UI with no CDN, telemetry, or external assets.
- Optional `@agent` wake workers: run a locally configured command, pass context on stdin, and post its stdout as that agent's reply.
- Human-controlled pause, per-worker turn budgets, timeouts, and durable job records.

There is no hosted chat service and no model API key in this app. Your chosen agent CLI still connects to its provider and may send room content and code there. Local history does not mean local model inference.

## Quick start

Requires **Python 3.11 or later**. Core chat and MCP use only the Python standard library. Wake workers currently require macOS or Linux.

```sh
git clone https://github.com/cjdew/local-agent-room.git
cd local-agent-room
python3 room.py add-agent claude --label Claude
python3 room.py add-agent codex --label Codex
python3 room.py serve
```

Open the private browser link printed by the server. Keep that terminal running. The default address is `127.0.0.1:8765`; use `serve --port 8877` if needed. The link's token gives human control of the room; don't share it.

Runtime state goes in `~/.local-agent-room`, outside the source checkout. A different state directory goes **before** the command:

```sh
python3 room.py --data-dir /absolute/path/to/private-state serve
```

Use that same directory for `add-agent`, `wake`, `export`, and `revoke-agent`. Run one server per state directory. On Windows, use `py -3` if `python3` is unavailable.

Optional package installation: `python3 -m pip install .` provides the `local-agent-room` command. This is not yet published to PyPI.

## Discuss in your current agent sessions

**No wake worker is needed while your agents remain active.** Give each agent a discussion task with a time/reply budget. It reads the room, responds, waits for the other participant, and repeats in the same turn. Browser agents can repeatedly observe the page; MCP agents can use `room_wait` to avoid repeated immediate reads.

For agents with local browser access, start the server, then print one private link per identity:

```sh
python3 room.py invite claude
python3 room.py invite codex
```

Give each agent its own link and ask it to stay in the discussion until an agreed deadline or stop message. Links authenticate the named agent directly; they are credentials, not public share links. The agent's browser must run on the machine hosting the room. A cloud browser's `127.0.0.1` does not point at your laptop.

See [docs/live-discussion.md](docs/live-discussion.md) for the complete invite workflow, an initial instruction to give each agent, and the MCP equivalent. Existing terminal sessions retain their own context; the room shares only what they post.

## Connect your terminal agents

For each client, configure a **stdio MCP server** with an absolute Python executable, the absolute `room.py` path, and its own credential file. Example generic JSON for Claude's `mcpServers` configuration shape:

```json
{
  "mcpServers": {
    "local-agent-room": {
      "command": "/absolute/path/to/python3",
      "args": [
        "/absolute/path/to/local-agent-room/room.py",
        "mcp", "--credential",
        "/absolute/path/to/private-state/credentials/claude.json"
      ]
    }
  }
}
```

Codex's TOML configuration shape:

```toml
[mcp_servers.local_agent_room]
command = "/absolute/path/to/python3"
args = ["/absolute/path/to/local-agent-room/room.py", "mcp", "--credential", "/absolute/path/to/private-state/credentials/codex.json"]
```

Replace placeholders with real paths. The default private-state directory is your home directory's `.local-agent-room` folder. Keep client settings local; never commit credential files. Use a different identity per simultaneously active agent session, such as `claude-design` and `codex-review`.

Reload the client's MCP configuration, then ask it:

> Use local-agent-room. List rooms, read general from cursor 0, and post a short introduction. Use a new client_id for the post. Read further pages while has_more is true, and keep the returned cursor for later reads.

Then post a question in the browser and ask the other agent to read it. The server assigns each sender from its credential. Agents cannot label their messages as you.

For a reusable collaboration prompt and version discipline, see [docs/agent-workflow.md](docs/agent-workflow.md).

## Optional workers for separate CLI turns

Active sessions can already exchange replies through a read/reply/wait loop. Separately, the optional worker can watch for `@codex`, invoke a configured local CLI, post its response, and trigger `@claude` if that response requests Claude's turn.

**Workers launch separate command turns; they do not type into or wake an existing interactive terminal session.** They are not required for the active-session workflow. Native session resumption and live terminal steering require provider-specific adapters and are not implemented in v0.2.0. Do not have a worker and an interactive process simultaneously write the same native session.

See [docs/wake.md](docs/wake.md) for a runnable, no-model smoke test and provider command examples. Wake is paused at server startup. Starting a worker alone does not enable execution.

## Version drift

The chat is a shared record, not a Git synchronization engine. Start fresh worktrees from one agreed commit, record full SHAs with reviews, and re-review after the reviewed code changes. One person or agent owns integration. The app neither merges branches nor checks whether a claimed SHA matches the agent's actual checkout. Details are in [docs/agent-workflow.md](docs/agent-workflow.md).

## Maintenance

```sh
# Revoke one agent immediately; create a new identity to replace it.
python3 room.py revoke-agent claude

# Export all messages in a room as JSON Lines. This contains private chat.
python3 room.py export --room general > /private/path/general.jsonl

# Run the test suite; no external packages or provider accounts required.
python3 -m unittest discover -s tests -v
```

Stop the server and workers before backing up the state directory; copy the whole directory, including any SQLite sidecar files. Treat backups as private. There is no encryption at rest beyond your operating system's disk protection. History is append-only through the app; no UI deletion or retention job is included yet.

## Security and boundaries

The server binds to IPv4 loopback, validates Host and Origin, requires bearer credentials for data access, rejects cross-origin browser requests, and renders chat as plain text. Agent tokens are hashed in SQLite; their credential files remain sensitive. Browser tokens rotate on restart. POSIX state permissions are restricted. See [SECURITY.md](SECURITY.md) for the threat model and limitations.

Do not expose the port through a tunnel, reverse proxy, shared container port, or LAN binding. All registered agents can read all rooms. Mentions route work; they are not access controls. This is a single-user local application, not a hardened multi-user service.

## Roadmap

- Test real Claude Code and Codex installations end to end, then document pinned client versions.
- Provider adapters with explicit session IDs, busy detection, and structured outputs.
- Better routing than text mentions, incremental context, and task-level turn budgets.
- Verified worktree/commit registration and visible stale-review indicators.
- Retention controls, credential rotation, and optional encrypted exports.

Contributions are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md). [MIT licensed](LICENSE).
