# Stellaris Timeline Inspector

A high-performance, interactive local web dashboard designed for Stellaris multiplayer tournaments. It parses hundreds of standard `.sav` files in seconds to generate a chronological timeline of empire progression, planetary build orders, and military fleet compositions.

## Features

* **Chronological Build Feed:** Tracks exactly when districts, buildings, and mega-structures were built, and logs when planets are colonized, conquered, or lost.
* **Deep Empire Analytics:** Tracks Tech Power, Economy Power, and Military Power over time, alongside Civics, Origins, Traditions, Ascension Perks, and Council Agendas.
* **Comprehensive Resource Tracking:** Extracts stockpile and monthly net income for all 18 primary and strategic resources.
* **Detailed Fleet Inspector:** Breaks down every military fleet by hull size, ship design, equipped weapon/utility components, and current hull/armor/shield condition percentages.
* **Native Game Assets:** Dynamically hooks into your local Stellaris installation to translate internal game IDs into clean English and render native `.dds` game icons directly in the browser.
* **Multi-Core Parsing Engine:** Bypasses Python's GIL using `ProcessPoolExecutor` to parse hundreds of 50MB save files simultaneously across all CPU cores, reducing extraction time from 30 minutes to under 2 minutes.

## Prerequisites

* **Windows OS** (Required for the local `.cmd` launchers)
* **Python 3.x** installed and added to your system PATH
* A local installation of **Stellaris** (to pull localization and `.dds` icon assets)

## Installation & Setup

1. Click **Code > Download ZIP** and extract the folder anywhere on your computer (or clone the repository via GitHub Desktop).
2. Double-click `setup.cmd`. 
3. A folder browser will appear. Select your main Stellaris installation directory (the folder containing the `gfx` and `localisation` folders). This path will be saved locally to a `config.json` file.

## Usage

**To extract data from a new match:**
1. Double-click `Extract New Save Folder.cmd`.
2. Select the folder containing your chronological `.sav` files for the match.
3. The multi-core parser will process the saves, display a live ETA, and generate a `.json` timeline file in the `output/` directory.

**To view the dashboard:**
1. Double-click `Launch Timeline Viewer.cmd`.
2. A lightweight local Python server will start in the background to handle browser security restrictions regarding local `.dds` textures.
3. Your default web browser will open the dashboard. Select your generated timeline from the dropdown and use the scrubber to analyze the match frame-by-frame.