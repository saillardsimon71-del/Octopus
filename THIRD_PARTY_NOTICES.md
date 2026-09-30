# Hermes-derived components

`agents/search_ddgs.py` adapts the search-only normalization and disposable worker
boundary from `plugins/web/ddgs/provider.py` and `_search_worker.py` in
NousResearch/hermes-agent, commit `59004a62356f3a4697ab0fe8ad5086d2b405e2a6`.
OCTOPUS supplies its own cancellation, permissions and result envelope. No Hermes
agent loop, plugin loader or runtime installation is imported.

`agents/agent_browser.py` adapts the agent-browser command layer of
`tools/browser_tool_session.py` (temp-file stdio, Windows spawn flags, `.cmd` shim
workarounds, JSON interpretation), the line-boundary snapshot truncation of
`tools/browser_tool_snapshot.py` and secret prefix patterns of `agent/redact.py`
from the same NousResearch/hermes-agent commit. OCTOPUS supplies its own permissions,
egress guard, ledger and resume logic in `octopus/browser_workspace.py`.

The browser backend binary itself is not vendored: `scripts/install_agent_browser.py`
downloads agent-browser 0.26.0 (vercel-labs/agent-browser, Apache License 2.0,
https://github.com/vercel-labs/agent-browser/blob/main/LICENSE) from the npm registry and
verifies the sha256 pinned by Hermes (`pm/lock.json`). Its license and notices apply to
that binary.

MIT License

Copyright (c) 2025 Nous Research

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
