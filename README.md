# Stellaris Timeline Inspector

A high-performance, interactive local web dashboard built for **Colosseum** and Stellaris multiplayer tournaments. It parses hundreds of standard `.sav` files in seconds—or streams autosaves live during an active match—to give players and casters a real-time view of galactic alerts, empire progression, diplomacy, build orders, technology research, military fleets, and spawn fairness. Created with the assistance of AI.

## Key Features

* **🔔 Live Notifications (Caster Alert Feed):** Real-time, color-coded event stream synced to the timeline scrubber and live autosaver. Includes an unread counter on the tab, clickable player jump-buttons, search, **★ Firsts Only** and **Major (Red)** toggles, and customizable category filtering:
  * **Click** any category (`Diplomacy`, `Military`, `Ascension`, `Techs`, `Milestones`, `Gov / Ethics`, `Deficits`) to hide/show it (preferences persist automatically across refreshes).
  * **Right-Click** (or **Shift+Click**) any category to solo it.
* **🏛️ Empire Inspector, Diplomacy, Edicts & Policies:** Inspect any empire at any point in the match. The profile panel displays Origin, Government, Ethics, Civics, Species Traits, Active Council Agenda, the **18-Resource Economy Ledger**, **Diplomacy & Active Wars** (ongoing wars, Federation membership/leadership, Defensive Pacts, Non-Aggression Pacts, Research/Commercial/Migration treaties), **Active Edicts**, and a collapsible **Active Policies** grid.
* **📜 Unified Empire & Planetary Timeline Feed:** Chronologically logs every colony's districts, buildings, zones, **cleared tile blockers**, colonizations, and conquests/losses alongside empire-wide events (**Traditions**, **Ascension Perks**, **Government/Civic/Ethic Reforms**, **Council Agendas**, **Edict activations/deactivations**, **Policy changes**, and **Diplomacy** shifts), complete with category filters (`All`, `Empire`, `Diplomacy`, `Edicts & Policies`, `Planets`).
* **📊 Visual Build Timeline (Swimlane View):** Interactive zoomable timeline displaying staggered planetary build tracks, an optional **Empire Milestones** row, and three dedicated **Physics / Society / Engineering** research rows with native `.dds` icons, hover tooltips, and click-to-pin event details.
* **🏆 Live Galactic Leaderboard:** Compare all players side-by-side at any selected date. Sort dynamically by Fleet Power, Economy Power, Tech Power, Planet Count, Monthly Alloy Net, Total Research Output, or Unlocked Technologies, with an **Active Empires Only** filter that tracks eliminations and wartime occupations.
* **🌌 Interactive Galaxy & Spawn Map:** Pan-and-zoom star map generated from the initial save featuring **5-jump BFS spawn reach** and automatic rerouted **illegal spawn collision detection**, homeworld player labels, T1/T2 guaranteed neighbors, Fallen Empires, Marauders, Enclaves, Wanderlust POIs, a clickable star system inspector, and one-click **PNG** export.
* **🔬 Technology Research Timeline:** Logs every researched technology chronologically with its research area (*Physics*, *Society*, *Engineering*), tier badge (*T1–T5*), and native `.dds` tech icon, with filters for research areas and starting technologies.
* **🚀 Detailed Military Fleet Inspector:** Breaks down every military fleet by hull class, ship design template, and section loadouts (*Bow*, *Core*, *Stern*, *Auxiliaries*, *Armor*, *Shields*, and *Core Systems*), complete with 48×48 native `.dds` component tiles, slot-size badges, and aggregate Hull/Armor/Shield condition percentages.
* **💾 Offline HTML, PNG & Print/PDF Export:** Package any active tab (Empire Inspector, Build Timeline, Leaderboard, Notifications, or Galaxy Map) into a standalone offline `.html` file with embedded base64 icons and interactive tooltips, save the Galaxy Map as a `.png`, or print/export cleanly to PDF.
* **🟢 Live Colosseum Autosaver & Crash-Resilient Cache:** Archives rolling multiplayer autosaves into permanent date-stamped backups (`Stellaris_Backups/`), runs at `BELOW_NORMAL` CPU priority for zero host lag, shares the exact same parsing and notification engine as the batch extractor, and persists parsed snapshots to a compressed `.live_state_cache.json.gz` so restarting mid-match preserves all galaxy-first records and fleet-loss baselines without re-parsing older saves.

## Live Notification Color Legend

| Color | Tracked Events |
| :--- | :--- |
| **Red** | Wars started / ended, Federations formed / disbanded, Major Fleet Loss (`>20%` loss on fleets `≥4k` power), Capital captured / occupied |
| **Purple** | Planet captured / occupied, Defensive Pacts formed / broken, Non-Aggression Pacts formed / broken |
| **Orange** | Opening & finishing Ascension Tradition trees (*Cybernetics, Synthetics, Psionics, Cloning, Mutation, Purity, Modularity, Nanotech, Virtuality*) |
| **Light Green** | **Galaxy First** to research a watched tournament technology |
| **Green** | Subsequent players researching a watched tournament technology (*Battleships, Titans, Colossi, Proton/Neutron/Zro Launchers, Kinetic Battery/Artillery, Mega-Engineering, Arc Furnace, Deep Space Citadel, Galactic Administration, Ascension Theory, Psi Jump Drives, Arkship Mastery, Stellar Engine, etc.*) |
| **Blue** | **Galaxy First** to hit monthly income milestones: Total Research (`+200` to `+3,000`), Alloys (`+100` to `+2,000`), Unity (`+100` to `+2,000`), Trade (`+500` to `+20,000`) |
| **Light Blue** | Subsequent players hitting those same monthly income milestones |
| **Yellow** | Government, Civic, or Ethic changes |
| **Amber** | Resource Deficits (`0` stockpile with negative monthly net) |

## Prerequisites

* **Windows OS** (Required for the `.cmd` launchers and Windows process priority management)
* **Python 3.x** installed and added to your system `PATH`
* A local installation of **Stellaris** (used to read English localization `.yml` files, game definitions, and `.dds` artwork)

## Installation & Setup

1. Click **Code > Download ZIP** and extract `Stellaris-Timeline-Inspector` anywhere on your computer (or clone the repository via GitHub Desktop).
2. Double-click **`setup.cmd`**.
3. A folder browser will appear. Select your main **Stellaris installation directory** (the folder containing `gfx`, `interface`, `common`, and `localisation`). This path is saved locally to `config.json` and can be updated anytime by re-running `setup.cmd`.

## Usage

### Option A: Live Match Tracking & Casting (For Tournament Hosts)
1. Double-click **`Start Live Autosaver.cmd`** before or during your multiplayer match.
   * Monitors your `Documents\Paradox Interactive\Stellaris\save games` folder every 15 seconds.
   * Archives a permanent date-stamped copy into `Stellaris_Backups\<campaign_name>\` whenever Stellaris writes an autosave, updates the compressed state cache, and incrementally writes the latest timeline JSON into `output/`.
2. Double-click **`Launch Timeline Viewer.cmd`** to open the dashboard in your browser.
3. Select the campaign from the dropdown. Leave **Live Auto-Follow: ON** to automatically step forward as new autosaves arrive, or scrub backward to inspect earlier years (which pauses Auto-Follow until you return to the latest date or click the pill).

### Option B: Batch Extracting an Existing Save Archive
1. Double-click **`Extract New Save Folder.cmd`**.
2. Select any folder containing chronological `.sav` files from a completed match.
3. The multi-core extractor processes all saves in parallel and writes `<campaign_name>_timeline.json` into the `output/` folder.
4. Double-click **`Launch Timeline Viewer.cmd`** to inspect the timeline in your browser.

## Project Structure

* `setup.cmd` / `setup_config.py` — Configures and validates your local Stellaris installation path (`config.json`).
* `Extract New Save Folder.cmd` / `build_order_extractor.py` — Multi-core batch extractor, shared save parser, galaxy spawn checker, and unified timeline & notification reconciliation engine.
* `Start Live Autosaver.cmd` / `autosaver.py` — Background autosave archiver, persistent compressed state cache, and incremental live updater.
* `Launch Timeline Viewer.cmd` / `server.py` — Multi-threaded local HTTP server that serves the dashboard, live polling APIs (`/api/list`, `/api/meta`), and `.dds` game textures.
* `index.html` — Single-page interactive web application with native browser-side `.dds` decoding (DXT1/DXT3/DXT5/DX10), caster alert filtering, interactive galaxy map, and offline export tools.