# Install and launch

## Windows checkout

Clone the repository, then double-click **Open Job Finder.cmd**. You can also run **Open Job Finder.ps1** from PowerShell. The opener downloads a pinned, verified startup tool, then manages Python and the locked app dependencies under `%LOCALAPPDATA%\JobFinder`. You do not need a ZIP, Python, uv, Go, or administrator access.

First setup needs internet and shows progress in the terminal. The browser opens when the app is ready. Keep the terminal open; use **Quit Job Finder** in the app to stop it. Opening the launcher again returns to the running app. After setup, cached launches work offline; job research still needs the network and a signed-in agent.

If a direct PowerShell launch fails, read the error in its console and use **Open Job Finder.cmd**. CMD sets an execution-policy bypass for its child PowerShell process only. It does not change the system execution policy. Do not change your system policy to launch this app.

## Command and script preview

Extract `Job-Finder-Script-Preview.zip`. On a Mac, double-click `Open Job Finder.command`. On Windows, double-click `Open Job Finder.cmd`; PowerShell users can run the `.ps1` file. On Linux, run `./Open\ Job\ Finder.sh`.

The scripts resolve their adjacent `app/` folder and invoke the existing Go supervisor. They do not install development tools or run a separate application workflow. The preview bundles all six supervisors and pinned uv executables. The supervisor opens setup in the browser, downloads managed Python and locked production dependencies, and opens the real app. Keep the terminal open until you quit the app.

The macOS Apple Silicon script launch passed native browser verification with a PATH containing no Python, uv, or Go. Windows, Linux, and macOS Intel supervisors are compiled but need native verification. The preview is unsigned and does not advance M1a acceptance.

Build and update on a development host:

```sh
GOTOOLCHAIN=go1.27.3 python3 scripts/build_release.py --script-package
GOTOOLCHAIN=go1.27.3 python3 scripts/build_release.py --script-package --reuse-images
```

The output is `dist/Job-Finder-Script-Preview.zip` and its SHA-256 file. Mac and Linux source clones need this build step before their root scripts work. The Windows script handles an unbuilt checkout automatically. The distributed ZIP includes all required binaries.

## Native ZIP edition

The release builder produces one ZIP with five launcher images and a shared `app/` directory. This repository's initial build is an **unsigned development build**. Cross-compilation does not establish native desktop acceptance. See the verification status in the main README before relying on a platform.

1. Extract the complete ZIP into a permanent folder.
2. Open the launcher matching your computer. Keep it beside `app/`.
3. A setup page opens while a pinned Python runtime and locked dependencies download.
4. Setup opens the workspace when the local server is ready.

| Computer | Launcher |
|---|---|
| Mac, Apple Silicon or Intel | `Open Job Finder — macOS.app` |
| Windows, Intel/AMD | `Open Job Finder — Windows x64.exe` |
| Windows, ARM | `Open Job Finder — Windows ARM64.exe` |
| Linux, Intel/AMD | `Open Job Finder — Linux x86_64.AppImage` |
| Linux, ARM | `Open Job Finder — Linux ARM64.AppImage` |

The macOS launcher requires macOS 13 or later, matching the [minimum supported by its pinned Go toolchain](https://go.dev/doc/go1.27#linker).

First setup requires internet access. Customers do not need to install Python, pip, Go, Node.js, or a compiler to run a built ZIP. Search plans and research require an installed, authenticated Codex or Claude Code CLI. Provider subscriptions and usage limits are separate from this application.

The app opens your saved searches or a blank **New search** form. Upload a résumé or enter targets and a description. Set location, salary, and work arrangement, then choose **Build search plan**. Review the tool-access notice and choose **Allow & run**. Review **Plan**, choose **Find jobs**, then download **CSV** or **Excel** from **Jobs**. **Save search** saves inputs without a model call.

These images are unsigned. macOS Gatekeeper and Windows SmartScreen can show trust prompts. Linux file managers may require the launcher executable permission to be enabled. If the AppImage cannot use FUSE, start it with `--appimage-extract-and-run`; the adjacent `app/` path remains unchanged.

Use **Quit** in the header to stop the local server. Its confirmation warns about unsaved inputs and edits; saved results remain. Closing a browser tab leaves the app running; opening the launcher again returns to the same instance.

## Connect your agent

Job Finder detects standard native and npm installations of Codex and Claude Code. It checks common installation folders when the desktop PATH is stale. Existing CLI sign-in is reused. An unavailable version probe does not block a task; the task checks authentication and access.

The first agent task asks for approval saved to that search. **Allow & run** permits commands, file reads and changes, internet access, and connected tools under your account. Documents and webpages can influence the agent's actions. Approved tasks inherit your CLI configuration and use full tool permissions; separate CLI hook-trust decisions still apply. Revoke approval under **Inputs → Agent settings**.

If sign-in is needed, choose **Sign in to Codex** or **Sign in to Claude Code**. Complete the installed CLI's sign-in in the terminal window, return to the app, and choose **Retry task**. If the window cannot open, run the displayed fixed sign-in command yourself. Job Finder does not collect your provider credentials.

After an installation change, choose **Check again**. A task failure shows a specific recovery step for sign-in, permissions, network, model access, or usage limits. Repair that issue and choose **Retry task**. Your résumé, preferences, and completed results remain saved. Profile retries return to **Plan**; report retries return to **Jobs**.

For `model_unavailable`, choose **Choose model**, enter a model available to your CLI account, save, and retry. You can also use the other installed agent. The app does not change your CLI's default model.

## Source checkout

Windows clones use the opener described above. The manual source workflow in the main README is for developers and CLI users; it is optional on Windows.

## Data and recovery

Data, documents, logs, managed Python, and cached dependencies live outside the extracted program folder:

| OS | Application data |
|---|---|
| macOS | `~/Library/Application Support/JobFinder/` |
| Windows | `%LOCALAPPDATA%\JobFinder\` |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/JobFinder/` |

The launcher uses `data/`, `config/`, `logs/`, `python/`, `runtimes/`, `runtime/`, and `cache/` within this location. Keep your résumé, search profile, contact records, and database private. Job Finder uses a separate data directory from Auto Outbound. Back up the data directory before deleting or moving it.

The setup page shows a short error code and log location. Network/TLS failures allow a bounded retry; missing or damaged release files require extracting a complete clean copy. The previous ready runtime is preserved when a dependency update fails. If the default browser cannot open, the launcher writes the exact local URL to `logs/OPEN_THIS_URL.txt`.

If an agent is not found, confirm that its command works in a terminal, then choose **Check again**. An npm installation also needs its Node.js runtime. Optional developer overrides `JOB_FINDER_CODEX_BIN` and `JOB_FINDER_CLAUDE_BIN` select a custom installation; standard installations need neither variable.

## Build the native ZIP

Build tools are used only on the developer/CI host. Linux can cross-compile the five images; each target still needs a native GUI smoke test.

```sh
# Go version and all tool pins: scripts/launcher/tool-versions.json
python scripts/build_release.py --prepare-tools --target all --package
```

The output is `dist/Job-Finder-M1a-unverified.zip`, its SHA-256 file, and `build/images/launcher-hashes.json`. The script rejects incomplete release roots, mismatched runtime tools, leaked development files, and missing lockfiles. No release is published automatically.

For source-only changes, retain the previously built images:

```sh
python scripts/build_release.py --reuse-images --package
```

The manual GitHub Actions workflow **Build native ZIP** performs the same build and stores it as a workflow artifact. It does not promote the build to an accepted release.

Before accepting a native release, exercise first and second launch, a source-only update, path names with spaces/Unicode, restart persistence, failed/offline setup, concurrent launch, port collision, browser-open failure, and graceful Quit on each of the six OS/architecture combinations. Cross-compilation cannot substitute for these checks.
