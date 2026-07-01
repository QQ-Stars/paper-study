<claude-mem-context>
# Memory Context

# [study-app] recent context, 2026-07-01 7:04pm GMT+8

Legend: 🎯session 🔴bugfix 🟣feature 🔄refactor ✅change 🔵discovery ⚖️decision 🚨security_alert 🔐security_note
Format: ID TIME TYPE TITLE
Fetch details: get_observations([IDs]) | Search: mem-search skill

Stats: 46 obs (19,084t read) | 886,794t work | 98% savings

### Jul 1, 2026
439 1:47p ✅ New git branch v1.0 created for refactor
440 1:48p 🔵 Refactor will follow improve-codebase-architecture + brainstorming skill pipeline
441 1:49p 🔵 study-app project structure revealed: no CONTEXT.md, no ADRs, monolithic server.js
442 " 🔵 study-app is an academic paper management system with MCP agent layer
446 " 🔵 Architectural friction map: PDF resolution triplicated, LLM config duplicated, server.js is a monolithic route handler
443 " ✅ Git Branch v1.0 Created for Refactor
444 " 🔵 study-app Project Structure Surveyed
445 " ⚖️ Architecture Review Workflow Selected for Refactoring
447 1:50p 🔵 Project completion status: P1–P5 all shipped, P6 Docker in progress, 124/130 papers have local PDFs
448 " 🔵 db.js is a deep data access module exporting ~20 functions covering papers, jobs, cite graph, and schedules
452 " 🔵 Actual line counts: public/app.js (1893 lines) is 2.8× larger than server.js (675 lines)
453 " 🔵 CDN-free architecture review HTML written to TEMP; sandbox blocked Start-Process on the CDN version but safe version succeeded
449 1:53p 🔵 study-app Frontend Is a 1893-Line Untested Monolith
450 " 🟣 Architecture Review HTML Report Generated for v1.0 Refactor
451 " ⚖️ Top Refactor Recommendation: Extract server.js into Thin HTTP Adapter First
454 1:54p 🔵 CDN-free architecture review report successfully opened in browser (exit 0)
455 " 🔵 Git state confirmed: branch v1.0, only AGENTS.md modified and .codegraph/ untracked — working tree clean for refactor
456 " 🔵 Product Design plugin has no saved user context for this project
458 1:56p 🔵 server.js full source confirmed: resolvePdfById triplication, /api/ingest uses raw spawn not spawnAgent, cssVar theming helper at app.js:249
459 " 🔵 Third venue normalization copy: VENUE_CANON in app.js:49 — normVenue exists in db.js AND agent/llm.py AND now client-side app.js
462 " ⚖️ User chose frontend UI redesign as refactor direction, with minimalist academic aesthetic requirement
463 " ⚖️ Frontend UI redesign plan locked in: 4-step visual overhaul preserving existing DOM/flow
457 " ⚖️ Frontend Redesign Scope Added: Minimalist Academic Aesthetic
460 1:58p 🔵 public/style.css has TWO complete theme personalities: indigo default (lines 15-45) and red Raycast-inspired theme override (lines 706-733+)
461 " 🔵 index.html confirms full app structure: 5 views, inline ingest wizard, two ingest paths (network search + local PDF import), venue verification UI
466 2:07p 🔵 Raycast redesign override block fully decoded: complete token reset + 430 lines of component overrides starting at style.css line 700
464 " 🔵 CSS Token Architecture Has Dual-Layer Override: Base + Raycast Block
465 " ⚖️ Frontend Redesign Strategy: Replace Raycast CSS Override Block, Flip Default Theme to Light
467 2:08p ✅ Default theme changed from dark to light; theme button emoji replaced with text labels
468 " 🟣 Academic fresh redesign CSS block appended to style.css — third complete theme skin, teal/ocean accent, paper-first aesthetic
469 2:10p ✅ Academic Fresh Theme Applied: Three Frontend Files Modified on v1.0 Branch
470 " 🔵 Theme Persistence Uses localStorage; toggleTheme Triggers renderHome and renderInsights
473 2:43p ⚖️ Project Refactor with v1.0 Git Branch Initiated
476 2:44p 🔄 study-app Backend Modularized into lib/ Directory
477 " 🔵 study-app All Endpoints Smoke-Test Green at Port 5273
478 " ⚖️ Next Refactor Phase: Frontend Style Extraction Planned
474 6:49p 🔄 study-app Backend Decomposed into Focused lib/ Modules
475 " 🟣 Academic-Themed Frontend Redesign Applied to study-app
479 " 🔵 style.css Structure: Academic Theme Block Starts at Line 1233 of 1799
480 " 🔴 CSS Split Script Failed: Line-Ending Mismatch Prevented Marker Detection
481 " 🔄 Academic Theme CSS Extracted into Standalone public/academic.css
482 " 🟣 All 8 Backend Tests Pass and Syntax Checks Clean After CSS Refactor
483 " 🟣 Live Smoke Test Confirms Both CSS Files Served Correctly Post-Split
484 6:50p 🟣 Playwright Visual QA Confirms CSS Split Renders Correctly at Desktop and Mobile
485 6:53p 🔄 Academic CSS Extracted into Separate File; v1.0 Refactor Marked Complete
486 " 🟣 Three New Feature/Bug Items Queued: PDF Rename, Local Import Copy, Translation Popup Scroll Bug

Access 887k tokens of past work via get_observations([IDs]) or mem-search skill.
</claude-mem-context>

## UI Design Direction

- The app UI is being redesigned as a Raycast-inspired research workspace: dark by default, compact, command-forward, high contrast, and accented with red for primary actions and active states.
- Preserve the current workflows and IDs in `public/index.html` / `public/app.js`; prefer visual-system changes in `public/style.css` unless behavior needs to change.
