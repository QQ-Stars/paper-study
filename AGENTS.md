<claude-mem-context>
# Memory Context

# [study-app] recent context, 2026-07-27 1:16pm GMT+8

Legend: 🎯session 🔴bugfix 🟣feature 🔄refactor ✅change 🔵discovery ⚖️decision 🚨security_alert 🔐security_note
Format: ID TIME TYPE TITLE
Fetch details: get_observations([IDs]) | Search: mem-search skill

Stats: 50 obs (21,457t read) | 2,540,154t work | 99% savings

### Jul 1, 2026
502 6:51p 🟣 Ebbinghaus Review Implementation Plan Created and Committed
503 " 🟣 Task 1: lib/reviews.js and test/reviews.test.js Implemented
504 " 🔵 Code Quality Review Found Edge Case Gaps in Task 1
485 6:53p 🔄 Academic CSS Extracted into Separate File; v1.0 Refactor Marked Complete
486 " 🟣 Three New Feature/Bug Items Queued: PDF Rename, Local Import Copy, Translation Popup Scroll Bug
505 7:34p 🔵 Quality Review Identified Gaps in lib/reviews.js Task 1 Implementation
506 7:35p 🔵 TDD Red Phase Confirmed Two Specific Bugs in lib/reviews.js
507 " 🟣 Added 5 Edge-Case Tests to test/reviews.test.js for Quality Review Fixes
500 11:55p 🟣 Ebbinghaus Review Scheduler and Store Implemented in study-app
501 " 🔴 Quality Review Identified 6 Issues in reviews.js Requiring Fixes
509 11:57p 🟣 Ebbinghaus Spaced-Repetition Review System — Core Scheduler Module
510 " ⚖️ Subagent-Driven TDD Workflow for Ebbinghaus Feature
514 " 🔴 dateOnly Patched to Reject Invalid Datetime Suffixes
508 11:58p 🔴 lib/reviews.js Quality Review Fixes Completed — All 7 Tests Green
513 " 🔵 Ebbinghaus Review Feature Code-Quality Review — lib/reviews.js & test/reviews.test.js
### Jul 2, 2026
511 12:00a 🔴 Task 1 Quality Fixes Applied — lib/reviews.js and test/reviews.test.js Now Pass 7 Tests
512 " 🔵 db.js setStatus Has No Review Plan Hook — Task 3 Must Add It
515 12:01a 🔵 Ebbinghaus Spaced-Repetition Review Feature Spec for study-app
517 " 🟣 Task 1 Committed — Review Scheduling Core Merged to v1.0 Branch
518 " 🔵 Task 2 Spec: completeReviewStep, listReviewItems, dueState Logic
522 " 🔵 server.js Route Architecture and db.js Integration Points for Review API
516 12:02a 🔵 lib/reviews.js and test/reviews.test.js are untracked in git
519 12:07a 🟣 Ebbinghaus Review Step Completion & Grouped List — TDD Implementation
521 12:08a 🔵 lib/reviews.js Pre-Implementation State: ensureReviewPlan Only, No completeReviewStep/listReviewItems
520 " 🟣 Task 2 Implementer Subagent (Euclid) Dispatched for Review Completion and Grouping
523 " 🔵 test/reviews.test.js Full Content & Git Working Tree State Before Task 2 Changes
### Jul 20, 2026
955 7:48a ⚖️ Install MCP server into Claude Code and Claude Desktop without modifying project files
956 " 🔵 study-app (Paper-Study) MCP server is a Python module at agent/mcp_server.py
957 7:49a 🔵 Paper-Study MCP server internals: FastMCP stdio server with 9 read-only tools
958 " 🔵 README documents MCP setup in section 七 and supports both Codex and Claude clients
959 " 🔵 Official Paper-Study MCP registration commands for Claude Code, Claude Desktop, and Codex
960 7:50a 🔵 Verified: main study-app repo has MCP server, live database, and venv with mcp package installed
961 " 🔵 Claude Code MCP baseline: codegraph and claude-mem connected; paper-study not yet registered
963 " 🟣 Paper-Study MCP server registered in Claude Code user scope and smoke-tested (9 tools)
964 " 🔵 Claude Code mcp list in current session does not reflect newly added user-scope server until restart
962 " 🔵 Paper-Study MCP server already registered in Claude Desktop config
S130 Fix "Claude Desktop still doesn't show paper-study" — root-caused to two desktop deployments with different config paths, then registered paper-study in the 3p build's config (Jul 20, 7:52 AM)
S128 Install the study-app (Paper-Study) MCP server into Claude Code and Claude Desktop without modifying any project files — completed with Claude Code user-scope registration and confirmation that Claude Desktop already had it configured (Jul 20, 7:52 AM)
965 7:55a 🔵 User reports Claude Desktop still does not show paper-study MCP server despite existing config entry
966 " 🔵 MCP config landscape: .claude.json user-scope entry confirmed; Claude Code also reads mcpServers from ~/.claude/settings.json
967 " 🔐 Plaintext API keys stored in C:\Users\HP\.claude\settings.json
968 7:56a 🔵 Claude Desktop build 1.22209.0 uses DXT extensions model — filesystem MCP is a DXT package, not a config entry
969 " 🔵 Desktop app DID spawn paper-study MCP server — 742KB server log exists
970 " 🔵 Desktop logs prove paper-study MCP server works: clean initialize handshake and tools/list response
971 7:57a 🔵 Claude Desktop app is currently running (9 claude.exe processes + cowork-svc.exe) — config reload requires restart
972 " 🔵 Main repo .venv Python launches mcp_server.py cleanly in 2.9s and answers MCP initialize over stdio
973 " 🔵 Root cause found: running Claude Desktop build uses AppData\Local\Claude-3p, not Roaming\Claude — and Local\Claude has a 28-byte empty config
974 7:59a 🔵 Claude-3p desktop build stores config.json under AppData\Local\Claude-3p — separate from both Roaming\Claude and Local\Claude
975 " 🔵 Diagnosis confirmed: desktop runs in "3p" deployment mode with a different account and no mcpServers in its active configs
977 " 🔵 study-app docs have no guidance for Claude Desktop 3p deployment mode config location
979 " ✅ Paper-Study MCP registered in Claude Desktop 3p-mode config — fixes desktop invisibility
980 " ✅ Claude Desktop config file edited with paper-study MCP server entry
S132 Fix "Claude Desktop still doesn't show paper-study" — root-caused to two coexisting desktop deployments with different config paths; paper-study registered in the 3p build's config and validated (Jul 20, 8:00 AM)
S133 Explain what Claude Desktop "deploymentMode: 3p" means — concluded it marks a third-party-model-backend deployment mode, backed by the machine's proxy gateway evidence (Jul 20, 8:01 AM)
**Investigated**: Follow-up question after the MCP fix: what "3p" in {"deploymentMode": "3p"} actually means. Evidence examined: the split data directories (%LOCALAPPDATA%\Claude-3p for the 3p build vs %APPDATA%\Roaming\Claude for the classic build), the Claude Code env in ~/.claude/settings.json (ANTHROPIC_BASE_URL=http://127.0.0.1:15721, ANTHROPIC_AUTH_TOKEN=PROXY_MANAGED, Opus/Sonnet/Haiku/Fable model slots mapped to qwen3.8-max-preview), and credential artifacts in the 3p dir (host-creds-*.json, ccd-session-secrets). A web search for official documentation of "deploymentMode 3p" found nothing public (WebSearch unavailable in this environment).

**Learned**: "3p" = third-party (vs 1p = first-party) — an internally-written deployment-mode flag, not a publicly documented product name. The 3p Claude Desktop build is functionally the same app (Cowork/code-session capable, adjacent version numbers 1.22209.0 classic vs 1.22209.3 3p) but differs in two ways: (1) model source — instead of a direct claude.ai account connection, traffic routes through a local proxy gateway (127.0.0.1:15721) forwarding to third-party models (here, Qwen/qwen3.8-max-preview); (2) config paths — the 3p build reads %LOCALAPPDATA%\Claude\claude_desktop_config.json and stores runtime data under %LOCALAPPDATA%\Claude-3p, while the classic build reads %APPDATA%\Claude\claude_desktop_config.json. This config-path split was the root cause of the earlier "paper-study not showing" problem.

**Completed**: Answered the user's 3p question with the third-party-deployment-mode explanation and machine-specific evidence; reiterated that both desktop builds now have paper-study configured (classic build pre-existing, 3p build newly written and JSON-validated) and that a full app restart + new conversation activates the tools. The overall task (MCP in Claude Code + Claude Desktop, no project files touched) remains complete: Claude Code user-scope registration ✔ Connected, 3p desktop config merged preserving deploymentMode.

**Next Steps**: Session idle, awaiting user feedback — most likely confirmation after the desktop restart that paper-study's 9 tools appear in a new conversation's connector menu, or further questions about the 3p deployment setup. If the desktop still shows nothing post-restart, the planned path is inspecting fresh mcp.log entries and the Settings → Connectors UI.


Access 2540k tokens of past work via get_observations([IDs]) or mem-search skill.
</claude-mem-context>

## UI Design Direction

- The app UI is being redesigned as a Raycast-inspired research workspace: dark by default, compact, command-forward, high contrast, and accented with red for primary actions and active states.
- Preserve the current workflows and IDs in `public/index.html` / `public/app.js`; prefer visual-system changes in `public/style.css` unless behavior needs to change.
