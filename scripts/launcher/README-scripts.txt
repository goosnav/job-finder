JOB FINDER / GOOSNAV LABS
Script preview

OPEN
----
Extract the complete ZIP first. Keep all launch files beside the app folder.

Mac:      Double-click Open Job Finder.command.
Windows:  Double-click Open Job Finder.cmd.
Linux:    Run ./Open\ Job\ Finder.sh in a terminal.

Windows users can also run the included Open Job Finder.ps1 from PowerShell.
Do not change the computer's execution policy. The .cmd file starts that script.
Keep the terminal window open while you use the app.

A setup page opens in your default browser. It downloads pinned Python and
locked dependencies, then opens Job Finder. You do not need to install Python,
uv, Node.js, Go, or a compiler. First setup needs internet.

Upload your resume or enter targets. Set location, salary, and work arrangement.
Choose Build search plan, review Plan, then Find jobs. Download CSV or Excel
from Jobs. Save search saves your inputs without an agent call.
Search plans and research need an installed, signed-in Codex or Claude Code CLI.

The macOS Apple Silicon command launch has been tested. Windows, Linux, and
macOS Intel supervisors are built but remain untested on their native systems.
This preview does not claim full M1a acceptance. Launchers are unsigned.

OS PROMPTS AND RECOVERY
----------------------
macOS or Windows can show a trust prompt for downloaded, unsigned programs.
Follow your operating system's trust controls. Do not run a file you do not trust.
If a Unix extractor loses execute permissions, use the file's Permissions panel
or restore them with chmod +x for the .command, .sh, and matching launcher file.

The setup page shows an error code, log location, and Retry when available.
For LAUNCH-ROOT or BOOT-INTEGRITY, extract a complete clean copy of the ZIP.
A browser-open failure also writes logs/OPEN_THIS_URL.txt below.

DATA
----
Mac:     ~/Library/Application Support/JobFinder/
Windows: %LOCALAPPDATA%\JobFinder\
Linux:   ~/.local/share/JobFinder/ (or XDG_DATA_HOME/JobFinder/)

Reports, documents, runtimes, and logs stay outside the extracted program folder.
Back up this location before deleting it. Existing Job Finder data is preserved.
Use Quit Job Finder in the app or Setup & help to stop its local server.
Closing the browser tab leaves the app running. Open the launcher to return.
