# Contributing

Use Python 3.11+ and run `python3 -m unittest discover -s tests -v`. There are no runtime dependencies. The browser UI is plain HTML, CSS, and JavaScript; `node --check src/local_agent_room/static/app.js` checks its syntax if Node is installed.

For the optional Chromium smoke test, install the test-only dependency with `npm install --no-save --package-lock=false --ignore-scripts playwright@1.58.2`, run `npx playwright install chromium`, then `node tests/ui.cjs`. This test starts a temporary local server on port 18765 and removes its synthetic state afterward. CI also runs it. Use the `PYTHON` environment variable if your executable is named differently.

Keep changes small and include tests for protocol, state, or execution changes. Preserve loopback-only serving, authenticated identities, human control of automation, bounded input/output, and no shell interpolation of chat content. Do not add telemetry or hosted dependencies silently.

Tests must use temporary directories, fake agents, and synthetic conversation content. Never require provider credentials in CI. A provider adapter should document supported CLI versions, session ownership, permission handling, output framing, timeouts, cancellation, and how failed turns avoid replaying side effects.

Before opening a pull request, review every tracked file for private data. Do not attach your real database, credentials, logs, session transcripts, or personal worker configuration. For vulnerabilities, see SECURITY.md.
