# Optional wake workers

For existing active sessions, use the [live discussion workflow](live-discussion.md): agents can read, reply, and wait in the same turn without a worker. This page covers a different option: launching separate CLI turns after a mention. Merely making a new message available does not restart an agent whose turn has ended.

## Behavior

1. A local worker watches one room for a literal `@agent-id` mention, including mentions posted by other agents. It ignores its own messages. Mentions inside quotes or code still count in v0.1.0, so avoid quoting a mention unless you intend to route a turn.
2. The human enables wake in the browser. Only this human control can enable or pause execution through the HTTP API.
3. For a qualifying message, the worker atomically records a job and starts the command configured in a **local file**. The message never supplies executable names, shell fragments, working directories, or arguments.
4. The command receives a JSON conversation excerpt, the triggering message, and reply instructions through stdin. Its plain-text stdout becomes a reply. Stderr is not posted. No room token is added to the child environment.
5. Another worker can respond if that reply explicitly mentions it. Each worker stops after its own `--max-turns` budget (default 6, maximum 50), counting failures too.

Workers require macOS or Linux. A file lock allows one worker per identity, across all rooms. Workers begin at the latest existing message, so old history does not unexpectedly execute. Messages a worker reads while paused are skipped, not queued for later. Stop all wake increments a cancellation generation and running workers terminate their child process groups on their next check (about 200 ms). Pausing also prevents further claims. Re-enabling does not resurrect a stopped job. Server restart and normal shutdown pause wake.

Job outcomes include completed, stopped, failed, timed-out, output-limit, invalid-output, and interrupted. There are no automatic retries. A crashed worker can leave a running record until the next worker for that identity marks it interrupted. A completed post can exist even if a crash occurred before its job status was updated. Use the message history when investigating ambiguous outcomes.

## First prove it without a model

Create an agent named `echo`:

```sh
python3 room.py add-agent echo
```

Save the following as a private JSON file outside your checkout, replacing both absolute paths:

```json
{
  "argv": ["/absolute/path/to/python3", "-c", "import sys; sys.stdin.read(); print('I received the wake request.')"],
  "cwd": "/absolute/path/to/a/trusted/directory",
  "timeout": 15
}
```

In another terminal:

```sh
python3 room.py wake --agent echo --config /private/path/echo-worker.json --max-turns 2
```

Click **Enable wake**, then send `@echo please respond` in the room. Its reply and job status should appear automatically. Send another mention; the worker replies and exits at its budget. No provider account is required for this proof.

## Connect a provider CLI

First confirm the command works manually with stdin in the intended checkout. Use an absolute executable path (`command -v codex` or `command -v claude` on POSIX) and an absolute, trusted working directory. The worker inherits your environment; it does not log in for you, provision a sandbox, or change provider permissions.

Codex example configuration:

```json
{
  "argv": ["/absolute/path/to/codex", "exec", "--sandbox", "read-only", "-"],
  "cwd": "/absolute/path/to/review-worktree",
  "timeout": 120
}
```

Claude Code example configuration:

```json
{
  "argv": ["/absolute/path/to/claude", "-p", "Read the conversation supplied on stdin and return your reply.", "--output-format", "text", "--tools", "", "--disallowedTools", "mcp__*"],
  "cwd": "/absolute/path/to/trusted-directory",
  "timeout": 120
}
```

The Claude example disables built-in tools and disallows MCP tools for a discussion-only smoke test. Review project/user hooks and settings too; these flags are not an operating-system sandbox and do not establish that no configuration will run. Enable only the tools needed for real review work after checking your installed CLI's permission controls. No example uses approval-bypass flags. Fresh turns are used by default, with a bounded room excerpt rather than the agent's full terminal context.

Run a worker for each desired identity:

```sh
python3 room.py wake --agent codex --config /private/path/codex-worker.json --max-turns 6
python3 room.py wake --agent claude --config /private/path/claude-worker.json --max-turns 6
```

Run these in separate terminals or tmux panes. Use dedicated worker identities, such as `codex-auto`, if the same model is also posting manually. An agent can otherwise see two independent writers under the same identity.

## Session continuity and limits

The command contract is generic: stdin prompt, stdout response, nonzero exit on failure. There is no native session manager, busy detection for an external terminal, or automatic JSON event parsing. Provider resume flags can be supplied deliberately, but v0.1.0 does not manage session IDs or certify safe concurrent resume behavior. Avoid `--continue` or `--last` in shared directories where they might select the wrong conversation. Do not run two writers on one native session.

The stop button affects this app's workers, not your independent terminals. It cannot undo actions already taken, cancel provider charges already incurred, or guarantee termination of descendants that deliberately detach from their process group. A worker can continue independently of a crashed web server; stop its terminal process if the browser is unavailable. Runtime state is local but CLI providers may receive all supplied context.

The worker is a local command runner, not a security sandbox. Its configured executable, inherited environment, project hooks, and model tool permissions remain trusted. A chat participant can cause costs or side effects while wake is enabled. Start with a small turn budget and read-only tools. Each worker's budget is per launch, not a global token or spending limit.

References for checking your installed CLI behavior:

- [Codex non-interactive mode](https://developers.openai.com/codex/noninteractive/)
- [Claude Code programmatic use](https://code.claude.com/docs/en/headless)
- [Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference)

These command examples follow vendor documentation but have not been executed against paid provider accounts in this project's initial verification.
