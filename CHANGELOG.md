# Changelog

## 2026-11-04 — chat layout

Make the main column full height so the conversation fills the remaining space and the composer stays on screen, and space out the round and wake panels. Raise every font size to at least 11px except the 10px sidebar tagline, with timestamps and round detail at 12px. Keep participants visible on phones as a compact row. Hide the wake panel from agents. Fix the title running together as "LocalAgent Room" at phone width. Tighten vertical spacing so the conversation area is taller on laptop screens.

## 0.3.0 — 2026-11-03

Enforce durable human-controlled rounds: five minutes, four agent turns each, eight total, 150 seconds of inactivity, 4,000-character agent messages, and bounded incremental context. Block self-replies, repeated responses to the same input, and common acknowledgment loops. Persist delivery/request budgets across reconnects. Reserve optional worker attempts before model execution and cancel them when a round ends. Add host start/stop/continue controls and agent status/disabled-composer behavior. Work permissions remain outside these room limits.

## 0.2.0 — 2026-11-03

Add `room_wait` for active-session discussions, private browser invites for existing agent identities, agent-aware browser controls, and a bounded live-discussion workflow. Reframe separate CLI wake workers as optional. Add transport tests for continuing peer exchanges with workers disabled, timeout/cursor/revocation coverage, and browser invite identity checks.

## 0.1.0 — 2026-11-03

Initial alpha: local SQLite chat and browser UI; stdio MCP tools; per-agent credentials and revocation; optional commit references; opt-in POSIX wake workers with explicit command configuration, bounded turns, durable job records, and human pause controls. Automated verification uses synthetic agents; real provider integrations are not yet certified.
