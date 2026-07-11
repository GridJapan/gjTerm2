# gjTerm2 — GridJapan Terminal

gjTerm2 は macOS 版 iTerm2（`gnachman/iTerm2`）を GridJapan がフォークした公開ターミナルです。GridJapan の YouTube チャンネルで配布します。

gjTerm2 is GridJapan’s public fork of macOS iTerm2 (`gnachman/iTerm2`), distributed through the GridJapan YouTube channel. Origin: `GridJapan/gjTerm2`; upstream: `gnachman/iTerm2`.

## ⚠️ This is a PUBLIC repository

Everything committed here is visible to strangers, including GridJapan YouTube viewers. Before you commit:

- Never add secrets, tokens, API keys, passwords, private filesystem paths, personal account names, internal ticket/URL references, or machine-specific setup.
- Personal or per-machine instructions belong in your own uncommitted `~/.claude/CLAUDE.md`, **not** in this file.
- Treat every line of this repo as published. When in doubt, leave it out and ask.

## Project goal: MCP communication between tabs

The headline feature we are building for gjTerm2 is **inter-tab communication over MCP** — letting an AI agent running in one tab/session discover, message, drive, and read the output of agents/sessions in other tabs, so tabs can converse with each other.

This is modeled on GridWorld’s `gj-terminal` (https://github.com/GridWorldOrganization/gj-terminal), a Windows Terminal fork that adds:

- an embedded control server (line-based JSON-RPC over TCP, default port 9551) that lets external agents inject input into and read the screen buffer of any tab — even when it is unfocused or backgrounded — targeting tabs by index or name;
- a small `wt-api` CLI wrapper over that server;
- an MCP surface (`.mcp.json`) that exposes those controls to AI agents.

For gjTerm2 the equivalent is a macOS/iTerm2-native implementation: expose each session/tab through an MCP server so tabs can find one another and exchange messages. Prefer reusing iTerm2’s existing automation surface (the Python API, session/tab model, and scripting hooks) over inventing a raw socket layer where the built-in mechanisms suffice.

---

## Rebrand: iTerm2 → gjTerm2 (READ THIS BEFORE MERGING UPSTREAM)

このリポジトリは iTerm2 を **gjTerm2** にリブランドした版です。upstream（`gnachman/iTerm2`）を取り込むときは、以下の「変えた／あえて変えていない」の境界を必ず守ってください。境界を破ると、ビルドが壊れるか、以後のマージが全ファイル衝突で破綻します。

**Design rule — rebrand the product, keep the internals.** We changed only the user-visible/product identity. We deliberately did NOT rename internal symbols, filenames, or the boilerplate file-header comments, so upstream diffs still apply cleanly.

### What WAS changed (re-apply these after an upstream merge)
- **App identity** — `project.pbxproj` main-app target only: `PRODUCT_NAME = gjTerm2`, `PRODUCT_BUNDLE_IDENTIFIER = co.gridjapan.gjterm2`, plus `TEST_HOST`/`BUNDLE_LOADER` paths (`gjTerm2.app/Contents/MacOS/gjTerm2`).
- **Info.plists** — `plists/{dev,beta,nightly,release,preview}-iTerm2.plist`: `CFBundleExecutable` and `CFBundleName` = `gjTerm2`, `CFBundleIdentifier` = `co.gridjapan.gjterm2`. (`plists/iTerm2.plist` is generated from the `dev-` variant; do not hand-edit it.)
- **Prefs domains** — main domain follows `CFBundleIdentifier` automatically; the hardcoded private suite in `iTermUserDefaults.m` / `ImportExport.swift` is `co.gridjapan.gjterm2.private`. App-Support dir follows `CFBundleExecutable` → `~/Library/Application Support/gjTerm2`.
- **Config dotdir** — `baseName ?: @"gjterm2"` in `NSFileManager+iTerm.m` `_homeDirectoryDotDir`, so the config dir, its `sockets/secrets` handshake socket, dynamic profiles, and the session-restoration socket live under `~/.config/gjterm2` (verified — resolves the socket collision with stock iTerm2). App-side only; needs no submodule change.
- **Terminal identity** — `PTYSession.m`: `LC_TERMINAL=gjTerm2`, `TERM_PROGRAM=gjTerm2.app`; user-agent `@"gjTerm2"` in `ToolWebView.m` / `iTermWebViewWrapperViewController.m`.
- **Sparkle auto-update — DISABLED.** `SUFeedURL`/`SUPublicEDKey` were removed from every plist variant (we cannot ship upstream’s feed/EdDSA key; leaving them would auto-update gjTerm2 into stock iTerm2). Do not re-add them from upstream.
- **Signing** — ad-hoc; upstream’s `CODE_SIGN_IDENTITY = Developer ID … NACHMAN …` / team is not ours.
- **User-visible strings** — `iTerm2` → `gjTerm2` in UI/dialog/menu string literals and `.xib` files (menu titles + their matching `identifier=` and the code that looks them up change together).
- **Makefile** — app paths (`gjTerm2.app`, `MacOS/gjTerm2`), `ITERM_CONF_PLIST` (`co.gridjapan.gjterm2.plist`), `pgrep gjTerm2`. The Xcode **scheme is still named `iTerm2`** (do not rename it; `tools/build.sh` depends on it).
- **URL scheme** — `iterm2://` → `gjterm2://` (registration in `iTermLaunchServices.m`; scheme build/compare in `iTermApplicationDelegate.m`, `iTermURLActionHelper.m`, `PTYSession.swift`, `CommandURLBuilder.swift`, `CommandExplainer.swift`, `TriggerController.m`, `CommandInfoViewController.swift`; the whats-new URL in `iTermAboutWindowController.m`; and `CFBundleURLSchemes` in every plist variant).
- **Renamed files** — `iTerm2.sdef` → `gjTerm2.sdef`, `iTerm2.entitlements` → `gjTerm2.entitlements` (references updated in `project.pbxproj` and the plists’ `OSAScriptingDefinition`).
- **Keychain / misc identifiers** — password-manager keychain services rebranded to `gjTerm2` (`gjTerm2`, `gjTerm2 API Keys`, `gjTerm2-Browser`, `gjTerm2-Adapter-…`); the `iTerm2-tmux` error string and the `iTerm2-Screenshot-…` save-filename → `gjTerm2-…`.

### What was deliberately NOT changed (keep as upstream — do NOT rebrand)
- **Swift module name is pinned to `iTerm2`** via `PRODUCT_MODULE_NAME = iTerm2` on the main-app target. This is what lets the product be `gjTerm2` while every `#import "iTerm2SharedARC-Swift.h"`, the class `iTermApplication`, and all `iTerm*` symbols keep compiling. Never remove this pin.
- **Internal symbols / class names / selectors** — `iTermController`, `PTYSession`, the C++ namespace `iTerm2::` (MetalRenderer/Metrology), etc. Left as upstream.
- **Filenames** — source files keep their `iTerm*` names (they pair with the kept symbols).
- **File-header comments** — the `//  iTerm2` / `Project: iTerm2` boilerplate at the top of ~933 files is intentionally left as-is to avoid a merge conflict in every file.
- **Framework/target/helper names & XPC ids** — `iTerm2SharedARC`, `iTerm2Shared`, `com.iterm2.pidinfo`, `com.iterm2.sandboxed-worker`, the scripting-bridge classes `iTerm2Session/Window/Tab`. In-bundle and invisible; renaming would require coordinated multi-project edits.
- **Lowercase `iterm2` that the Python API depends on — DO NOT rebrand.** These are the wire contract the standard `iterm2` Python library speaks, and the MCP inter-tab feature is built on top of that API — renaming any of them breaks it: the API method **namespace** (`registerFunction:…namespace:@"iterm2"`), the pip **package name** `iterm2` (`iTermSetupCfgParser.m`), the **protobuf package** (`Api.pbobjc.m` `initWithPackage:@"iterm2"`), and the websocket **handshake header** `X-iTerm2-Protocol-Version` (`iTermWebSocketConnection.m`). Also intentionally kept: the internal `<iterm2:attachment>` AI tag, the `iterm`/`iterm2` entries in the recognized-`TERM` set (`VT100Output.m`), and internal functional identifiers (`iTerm2-no-otp` 1Password tag, `/usr/local/iTerm2-secure-settings`, `iTerm2-Adblock`, and debug-only temp-socket/frame-capture names).

### The mechanical sweep rule (for re-running on merged-in code)
Replace `iTerm2` → `gjTerm2` **only** where it is NOT followed by `[A-Za-z0-9_.:-]`, AND the line is not a comment, AND the line contains neither `iTerm2::` nor `namespace iTerm2`. This hits visible string literals while protecting compound identifiers (`iTerm2SharedARC`), headers (`…-Swift.h`), the C++ namespace, and file paths. Lowercase `iterm2` (bundle-id fragments, the `iterm2:` URL scheme, `~/.iterm2`) is handled case-by-case, not by this rule.

### Still TODO in the rebrand (not yet done) — with blockers
- **Shell-integration `~/.iterm2` convention (separate from the config dir above) — needs the submodule, low value, left shared.** The local config dir is already isolated (see “Config dotdir” above). What remains on the old `~/.iterm2` name is the *shell-integration* feature: the install target (`Conductor.swift` `~/.iterm2/shell-integration`, `iTermShellIntegrationWindowController.m` `/.iterm2/` aliases), the `it2run` channel dir (`ChannelClient.swift` default `~/.iterm2`), and the `ITERM2_*` env vars + `.iterm2` paths baked into the scripts in `submodules/iTerm2-shell-integration`. These are shared-by-convention with stock iTerm2 and harmless to share (same scripts). Rebranding them means editing the git submodule and the `it2run`/`it2check` utilities in lockstep — do it only if you fork shell-integration too.
- **Helper/plugin bundle ids** under `iTermAI/`, `iTermCompanion/`, `iTermBrowserPlugin/` (`com.googlecode.iterm2.*`) — these are separate Xcode projects, **not built by or embedded in the Development `gjTerm2.app`** (installed at runtime as downloadable plugins). No effect on the shipped app until we ship those plugins; rebrand app-side lookup + helper id together when we do.
- **Lower-value / higher-risk, deferred:** renaming the `*-iTerm2.plist` variant files (needs the `cp …-iTerm2.plist iTerm2.plist` build-phase + Makefile + `INFOPLIST_FILE` updated together — `plists/iTerm2.plist` is generated, untracked); the app-icon asset (`CFBundleIconName`); the App-Group entitlement `$(TeamIdentifierPrefix)iTerm` in `gjTerm2.entitlements` (provisioning-bound — drop or re-provision under our own team); the internal `iterm2whatsnew:` scheme; the `iTermMigrationHelper.m` text (intentionally still names the legacy `iTerm`/`iTerm2` support dirs); and the `gjTerm2.sdef` scripting-dictionary *contents* (AppleScript terminology still reads iTerm2).

---

## Code Best Practices

- Avoid writing javascript, html, or CSS that's more than one line long in Swift. Create a new file and use the existing template mechanism to load it.
- After creating a new file, `git add` it immediately
- To add a file to the Xcode project, use `tools/add_file_to_xcodeproj.rb <file_path> <target_name>` (e.g., `tools/add_file_to_xcodeproj.rb sources/Example.swift iTerm2SharedARC`)
- In Swift, use it_fatalError and it_assert instead of fatalError and assert, which do not create useful crash logs. In ObjC, assert is ok although ITAssertWithMessage is preferable. Asserts are enabled in release builds.
- Don't write more than one line of inline javascript, html, or css. Instead create a new file and load it using iTermBrowserTemplateLoader.swift
- Don't create dependency cycles. Use delegates or closures instead.
- To run unit tests in ModernTests, use tools/run_tests.expect. It takes an argument naming the test or tests, such as `tools/run_tests.expect ModernTests/iTermScriptFunctionCallTest/testSignature`
- After changes that affect AI chat (request builders, response parsers, AITermController, AIConversation, anything in sources/AITerm/, ChatAgent, ChatClient, etc.), run `tools/run_ai_live.sh` against real vendor APIs. This is a separate live harness from the regular ModernTests; it costs real money but exercises end-to-end round-trips (smoke, multi-turn, tool calls, both streaming and non-streaming) against OpenAI/Anthropic/Gemini/DeepSeek. The default ModernTests run skips the live harness, so unit tests passing alone is not sufficient evidence. Pass a filter to scope the run: `tools/run_ai_live.sh openai`, `tools/run_ai_live.sh smoke`, or an exact method name like `tools/run_ai_live.sh test_anthropic_toolCall_nonStreaming`.
- After changes that affect attachment serialization (per-vendor file/image/document content blocks, MIME allowlists in LLMProvider, anything in CompletionsAnthropic.swift / Gemini.swift / DeepSeek.swift / Llama.swift / LLMModernProtocol.swift / ResponsesAPIRequest.swift attachment paths), run the 96-cell attachment matrix: `tools/run_ai_live.sh attachmentMatrix`. It bypasses the LLMProvider.accepts gate and sends each of 16 MIME fixtures through each of 6 vendor lanes, asserting whether the vendor accepted-with-content, rejected at HTTP, or accepted-but-garbled. Drift in either direction fails loudly with a `MATRIX DRIFT:` message that tells you whether to widen the allowlist, fix the serializer, or update the matrix cell. Full sweep: ~95 sec, ~70 API calls, under $0.50. Scope with `attachmentMatrix_<lane>` (e.g. `attachmentMatrix_gemini` runs one column) or `attachmentMatrix_<kind>` (e.g. `attachmentMatrix_imagePNG` runs one row across all lanes), or run a single cell by exact method name (`test_attachmentMatrix_anthropic_imageWEBP`). Fixtures live in `ModernTests/Resources/AttachmentFixtures/`.
- When renaming a file tracked by git (and almost all of them are) use `git mv` instead of `mv`
- To make a debug build run `tools/build.sh` (or `tools/build.sh Development`). This saves logs to `tmp/build.log` and shows only errors/warnings on failure.
- Little scripts or text files that are used for manual testing of features go in tests/
- The deployment target for iTerm2 is macOS 12. You don't need to perform availability checks for older versions.
- Don't replace curly quotes with straight quotes. Same for apostrophes and single quotes. If you need help typing a curly quote, just ask. Here are some you can copy and paste: ‘’“”
- In user-visible strings do not use " except as a shorthand for inch. Prefer curly quotes like “ and ”. I know this goes against your nature, but fight hard here.
- Ask permission before using auto layout if it's not already in use in a given file. Debugging auto layout is the worst hell.
- The deployment target is macOS 12. Don't add availability checks for 12 and lower.
- Never `git add` submodules without express written permission.
- Don't include AI-generated markdown files (summaries, plans, etc.) in commits — only ship code.
- Avoid duplicate expressions; hoist shared computations into a named `const` before branching.
- Don't change defaults silently.
- Use [iTermUserDefaults userDefaults] instead of [NSUserDefaults standardUserDefaults]
- Use `make run` to build and run a debug build.
- Do not use associated objects (objc_getAssociatedObject or objc_setAssociatedObject) without express written permission.
- You should treat warnings as errors.
- If you get stuck, ask for help. It's better to ask me to look at something in the debugger than to flail around for a long time.
- If your changes introduce compiler warnings, fix them.
- After landing a feature or bugfix, update docs/notes-3.7.txt (the release notes). Max width of a line is 50 characters.
- For changes to the Companion iOS app (the `Companion/` directory, "iTerm2 Buddy"), put release notes in Companion/docs/notes.txt instead of docs/notes-3.7.txt.
- The sources directory is organized into folders. Before adding a new file, consider which directory it belongs in. Some are named after features while others are named after their role.
- User Defaults keys that should only be stored locally begin with the prefix NoSync. If a user chooses to load prefs from a custom location (e.g., Dropbox) they may be prompted to write settings when a non-NoSync key changes. To avoid disrupting them in this manner, user defaults that are not actual configuration settings (e.g., a list of recent items) get a NoSync prefix.
- Use DLog statements so we can debug problems in the field. These statements have no effect when debug logging is off (the default) and it's OK for them to do somewhat expensive operations like getting a stack trace.
- Use RLog statements to log debug messages to memory even when debug logging is not on. Creating a debug log later will pull in the last 10 megabytes of RLog statements. RLog runs always so don't do anything expensive (such as stack traces) and do not use them in hot paths that could burn a lot of CPU logging.
- When adding temporary code for debugging, use NSFuckingLog instead of NSLog because NSLog truncates long output. Logging code that is intended to remain long-term should use DLog.
- Do not use an SF Symbols name as a string literal. Get it using SFSymbolGetString in Objective C or the SFSymbol enum in Swift.
- Don't use sleep to solve concurrency problems.
- Tests should not be flaky. Don't write tests that will fail if the system is slower than usual.
- DONE (companion NSE wire structs, syncSince): the syncSince leaf structs now live once in the package (CompanionSyncItem.swift, with `author` as a String to avoid hoisting Participant); CompanionHostMessage.syncSince and the NSE both use them, so NSESyncSince is just a thin envelope and the item-level cross-check is gone along with the `_0`-nesting footgun. The legacy messagesSince mirror (NSEMessagesSince) was deliberately NOT refactored: it is part of the revision-1 path the cleanup TODO below deletes wholesale, so sharing its structs would be wasted work.
- TODO (companion protocol cleanup): when 3.7 beta 6 or a stable 3.7 ships, remove support for the revision-1 companion protocol. That means dropping the legacy per-chat collapse-token push path entirely: delete `CompanionPushSender.sendMutable` and the legacy branch in `CompanionPushSender.dispatchPush`, the `messagesSince` client/host messages and `handleMessagesSince` in CompanionHostBridge, the `messagesSince` path in the NSE (NSEFetcher.fetch / NotificationService.runLegacy / the sentinel-vs-token branch in didReceive, since every push will then be a wakeup), `NSEMessagesSince` and `PushFetchCoordinator` and their tests, and raise `CompanionProtocolVersion.minimumPeer` to 2 so revision-1 peers are told to upgrade. Keep only the contentless wakeup + `syncSince` path. At that point the HMAC per-chat key is no longer exposed off-device, so also simplify the watermark/thread keying to the raw chatID and drop `CompanionThreadKey` / `CompanionCollapseToken` from this path (a coordinated change with `CompanionClient.advancePushWatermark`).
