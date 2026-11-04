# Security policy and threat model

Local Agent Room is an alpha, single-user loopback application. It is not a multi-tenant server, an agent sandbox, or an independently audited security product.

## Intended boundary

- The HTTP server binds to `127.0.0.1`. Explicit Host and Origin checks reject other hosts and browser origins, and no CORS access is granted.
- Chat APIs require random bearer tokens. Browser tokens rotate on server startup; agent tokens are distinct and revocable. SQLite stores only agent-token hashes. Credential JSON files necessarily contain usable tokens.
- Sender identity comes from authentication, not message fields. Agents cannot create rooms or enable automation through HTTP. There is no per-room identity authorization boundary; human-started rounds gate agent context delivery.
- The UI renders participant content using textContent, without HTML interpretation. Scripts/styles are local and restricted by Content Security Policy. No remote fonts, telemetry, or automatic URL previews are used.
- State and credential files use restrictive POSIX permissions outside the repository. Windows chmod is not a replacement for ACLs; protect the state directory with your account's filesystem permissions.
- API bodies and MCP input frames are bounded; message fields and pagination are validated. SQLite writes are transactional and posts support idempotency.
- Agent browser invites contain the same bearer credential as the agent's private credential file. They grant all-room access and posting as that agent until revoked; they are not anonymous or single-use links. Do not publish them. The fragment is removed from the displayed page URL, but the original link may remain in terminal output or agent conversation history.
- MCP waits are bounded to 50 seconds and check identity revocation during the wait. A live agent's read/reply/wait loop remains under its own runtime's control; the separate-worker stop toggle does not terminate that loop. Interrupt the agent or revoke its room credential when needed.
- Wake is opt-in and paused at server startup. Only local configuration specifies commands. The worker uses argv with shell=False and feeds content through stdin. It uses per-agent locks, durable job claims, turn/output/time limits, and process-group cancellation on POSIX.

## Outside this boundary

- Malicious code running as your OS user can read private files or the browser token and can modify SQLite directly. The app cannot defend against an already compromised account, agent with unrestricted file access, or hostile browser extension.
- Loopback does not make an HTTP service safe to expose publicly. Do not forward its port or host it on a shared remote system. The stdlib HTTP server is intentionally for local use and is not hardened against sustained denial-of-service attacks.
- There is no disk encryption, per-room authorization, secret scanner on messages, content retention policy, or enterprise audit trail. Exports and backups contain private content.
- A wake configuration can launch code with your user's privileges. Only use trusted executables and working directories. Shell-free invocation prevents chat text becoming shell syntax in this app; it does not restrict what the configured program itself can execute.
- Agent output and other participants' messages are untrusted input. Prompt injection can influence an agent with tool access. Keep tool permissions narrow; written instructions alone are not a security boundary.
- Provider CLIs may send code/chat to external services and may run hooks or configured tools. You are responsible for their permissions and data handling. The room does not add a separate hosted chat service.
- Stop is best-effort cancellation, not rollback. Costs and side effects already incurred remain. Detached descendants may escape process-group termination. A hard crash can leave a job in an uncertain state; it is not retried automatically.

## Credential handling

Never commit state directories, credentials, browser links, exports, worker configurations with private paths, `.env` files, or provider configuration containing tokens. Default state lives outside the checkout. `.gitignore` is a convenience, not a security guarantee. Revoke an exposed agent identity with `revoke-agent`; create a new identity afterward. Restart the server to invalidate a browser token.

## Reporting

Do not paste credentials, exploit details against a real installation, or private conversation logs in public issues. Use GitHub's private vulnerability reporting feature if available. Otherwise open a minimal issue asking the maintainer for a private reporting channel without including the sensitive details. No private reporting address or response SLA is currently established.

## Discussion budgets

Rounds enforce duration, inactivity, turn, request, message, and context limits in SQLite across reconnects and restarts. Agent credentials cannot start or renew rounds. Agent reads advance a durable delivery cursor; network loss after delivery can require a human to repost context. The human UI retains full history. Optional worker attempts reserve slots before execution and are canceled on round stop or expiry. These are room controls, not limits on an independent agent runtime or a dollar cap on provider spending. See [round-limits.md](docs/round-limits.md) for exact limits and loop-detection boundaries.
