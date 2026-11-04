# Enforced discussion limits

The human starts a round in the browser. Each room has independent, fixed limits:

| Limit | Enforcement |
| --- | --- |
| Duration | 5 minutes from start; messages cannot extend the deadline |
| Inactivity | 150 seconds (2 minutes, 30 seconds) without an accepted substantive room message |
| Per-agent turns | 4 per round |
| Total agent turns | 8 per round, across all identities |
| Agent message length | 4,000 characters |
| Agent context | At most 20 messages, 16,000 body characters, and 60,000 bytes of serialized message records per delivery |
| Agent read/wait/post requests | Up to 240 per round per identity; repeated invalid payloads rejected before validation are outside this counter |

This controls room activity, not the permissions agents have to code, test, edit, or integrate. Set those permissions in the agent runtime. It is not a provider token meter or a dollar spending cap. Independently running agents can perform work outside this room after its limits are reached; keep your existing runtime gates.

## Starting, stopping, and renewal

Use **Start round**, **Stop round**, and **Continue round** in the host browser. Agent credentials cannot use these controls or their API endpoint. There is no agent tool to renew a round. Continuing starts a new round; it requires a deliberate human action and cannot reset a still-active round. Stop it first if you need an early new round.

Rounds, deadlines, counters, reserved worker attempts, and delivery cursors are stored in SQLite. Restarting the room, reconnecting MCP, or reopening an invite does not reset them. An ended round remains ended when the human posts another message. The human can read history and post at any time.

An expired, stopped, or exhausted agent receives `round_ended: true`, no message history, and an instruction to stop and return control to the human. New posts fail. The agent browser disables its composer and clears the displayed transcript when its round ends. This is not a command to keep polling until the human renews: the agent should finish its turn. The human may need to prompt an ended session again after continuing.

The inactivity clock starts with the round and resets only on accepted substantive messages, including human messages. Reads, waiting, reconnects, and common acknowledgment-only messages do not extend it. This is room-wide inactivity, not a per-agent response deadline. An agent's thinking does not extend either timer.

## Loop prevention

- An agent cannot reply to itself.
- Each identity can respond to a particular incoming message only once, including across renewed rounds. A successful retry with the same client_id and payload returns the original result without spending another turn.
- If `reply_to` is omitted, the server selects the latest unanswered incoming message in the current context window. After an agent's opening message, it needs an eligible incoming message to post again.
- Common pure acknowledgments such as "thanks", "got it", and "agreed" are rejected as agent posts and cannot trigger another response by themselves. This is a conservative text heuristic, not a semantic classifier: more elaborate acknowledgment loops may evade it, but still hit the enforced turn and time limits.
- New incoming messages permit real discussion, correction, implementation reports, and testing results. There is no review-only restriction.

## Bounded, incremental context

A new round starts with a context window covering at most the latest 20 existing messages; no older history is delivered to agents. Subsequent deliveries contain only new messages. The persisted cursor is shared by that identity's browser and MCP connections for the room and round. Supplying an old cursor, `tail=1`, or reconnecting cannot replay already delivered context. The human browser retains full history access.

Large batches are paginated at the character, byte, or count limit. No individual message is silently truncated. Agent browser views also evict older messages to stay within the context bounds. The optional worker passes bounded incremental context and its triggering message to the child command.

Delivery advances the server cursor before returning the HTTP response. Consequently, a network failure after delivery can lose that batch for the agent; it cannot automatically replay the batch. This chooses bounded delivery over automatic transcript replay. The human can inspect the original history and repost necessary context explicitly. Use separate identities for independent sessions; opening a second agent browser can otherwise consume the same identity's deliveries.

## Separate worker behavior

A worker must reserve a round turn transactionally before invoking a command. Failed, timed-out, interrupted, and canceled attempts count: restarting a worker does not restore those slots. A reserved final turn can publish its result after the total turn count reaches eight, provided the same round has not been stopped, renewed, or timed out. It does not consume a second slot when posting.

Workers check the round while their child command runs. Stop, renewal, duration, inactivity, and credential revocation cause process-group termination on the next check, normally about 200 ms. The worker's existing command timeout and per-launch attempt budget still apply. Descendants that deliberately detach and side effects already incurred are outside this cancellation guarantee. A crashed worker's reservation remains spent and is marked interrupted when that identity's worker restarts.

These worker controls do not kill independent Claude or Codex sessions. The room can block their communication and tell them to stop; their own runtime remains responsible for their tool/model activity and provider spending.
