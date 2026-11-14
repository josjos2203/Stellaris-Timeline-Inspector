# Stellaris Timeline Inspector

A high-performance, interactive local web dashboard built for **Colosseum** and Stellaris multiplayer tournaments. It parses hundreds of standard `.sav` files in seconds—or streams autosaves live during an active match—to generate a chronological timeline of empire progression, technology research, planetary build orders, and military fleet compositions.

## Key Features

* **🏆 Live Galactic Leaderboard:** Compare all players side-by-side at any point on the timeline. Sort dynamically by Fleet Power, Economy Power, Tech Power, Planet Count, Monthly Alloy Net, Total Research Output, or Unlocked Technologies, with an **Active Empires Only** filter that tracks eliminations and wartime occupations.
* **🏛️ Unified Empire & Planetary Timeline Feed:** Tracks exactly when districts, buildings, and zones were queued on every colony, alongside empire-wide milestones including **Traditions**, **Ascension Perks**, **Government & Civic Reforms**, **Ethic Shifts**, and **Council Agenda Selections & Launches**.
* **🔬 Technology Research Timeline:** Logs every researched technology chronologically with its research area (*Physics*, *Society*, *Engineering*), tier badge (*T1–T5*), and original `.dds` tech icon, with filters for research areas and starting technologies.
* **🚀 Detailed Military Fleet Inspector:** Breaks down every military fleet by hull class, ship design template, and section loadouts (*Bow*, *Core*, *Stern*, *Auxiliaries*, *Armor*, *Shields*, and *Core Systems*), complete with 48×48 native `.dds` component tiles, slot-size badges, and aggregate Hull/Armor/Shield condition percentages.
* **💎 18-Resource Economy Ledger:** Tracks current stockpiles and monthly net balances for Energy, Minerals, Food, Trade, Alloys, Consumer Goods, Physics/Society/Engineering Research, Unity, Influence, Minor Artifacts, Volatile Motes, Exotic Gases, Rare Crystals, Zro, Nanites, and Living Metal.
* **🟢 Live Colosseum Autosaver & Streamer:** Automatically backs up the host's rolling multiplayer autosaves into permanent date-stamped archives (`Stellaris_Backups/`), incrementally parses new saves in under a second at `BELOW_NORMAL` CPU priority (zero host lag), and hot-reloads the web dashboard without disrupting the viewer's scroll position or active tab.
* **⚡ Multi-Core Batch Engine:** Bypasses Python's GIL using `ProcessPoolExecutor` and C-speed bracket scanning to batch-parse 300+ save files across all CPU cores in under 2 minutes.

## Prerequisites

* **Windows OS** (Required for the `.cmd` launchers and Windows process priority management)
* **Python 3.x** installed and added to your system `PATH`
* A local installation of **Stellaris** (used to read English localization `.yml` files and `.dds` artwork)

## Installation & Setup

1. Click **Code > Download ZIP** and extract `Stellaris-Timeline-Inspector` anywhere on your computer (or clone the repository via GitHub Desktop).
2. Double-click **`setup.cmd`**.
3. A folder browser will appear. Select your main **Stellaris installation directory** (the folder containing `gfx`, `interface`, `common`, and `localisation`). This path is saved locally to `config.json` and can be updated anytime by re-running `setup.cmd`.

## Usage

### Option A: Live Match Tracking (For Tournament Hosts)
1. Double-click **`Start Live Autosaver.cmd`** before or during your multiplayer match.
   * The script monitors your `Documents\Paradox Interactive\Stellaris\save games` folder every 15 seconds.
   * Whenever Stellaris writes an autosave, it archives a permanent date-stamped copy into `Stellaris_Backups\<campaign_name>\` (so rolling autosaves never overwrite earlier years) and incrementally updates the timeline JSON in `output/`.
2. Double-click **`Launch Timeline Viewer.cmd`** to open the dashboard in your browser.
3. Select the campaign from the dropdown. Leave **Live Auto-Follow: ON** to automatically step forward as new autosaves arrive, or scrub backward to inspect earlier years (which automatically pauses Auto-Follow until you return to the latest date or click the pill).

### Option B: Batch Extracting an Existing Save Archive
1. Double-click **`Extract New Save Folder.cmd`**.
2. Select any folder containing chronological `.sav` files from a completed match.
3. The multi-core extractor will process all saves in parallel and write `<campaign_name>_timeline.json` into the `output/` folder.
4. Double-click **`Launch Timeline Viewer.cmd`** to inspect the timeline in your browser.

## Project Structure

* `setup.cmd` / `setup_config.py` — Configures and validates your local Stellaris installation path (`config.json`).
* `Extract New Save Folder.cmd` / `build_order_extractor.py` — Multi-core batch extractor for full save folders.
* `Start Live Autosaver.cmd` / `autosaver.py` — Background autosave archiver and incremental live parser.
* `Launch Timeline Viewer.cmd` / `server.py` — Multi-threaded local HTTP server that serves the dashboard and streams `.dds` game textures.
* `index.html` — Single-page interactive web application with native `.dds` decoding (DXT1/DXT3/DXT5/DX10).