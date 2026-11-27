import os
import re
import gzip
import json
import time
import ctypes
import shutil
import zipfile
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed

from setup_config import get_stellaris_dir
import build_order_extractor as boe

# --- CONFIGURATION ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DESTINATION_FOLDER = os.path.join(SCRIPT_DIR, "Stellaris_Backups")
OUTPUT_FOLDER = os.path.join(SCRIPT_DIR, "output")

# How often to check for new autosaves (in seconds)
CHECK_INTERVAL_SECONDS = 15

# Bump this if the parsed save snapshot structure in build_order_extractor.py changes
CACHE_VERSION = 2
CACHE_FILENAME = ".live_state_cache.json.gz"


def set_low_cpu_priority():
    """Sets this Python process to BELOW_NORMAL priority on Windows so it never competes with Stellaris."""
    if os.name == 'nt':
        try:
            BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            ctypes.windll.kernel32.SetPriorityClass(handle, BELOW_NORMAL_PRIORITY_CLASS)
            print("[Host Protection] Process priority set to BELOW_NORMAL (Stellaris always gets CPU priority).")
        except Exception:
            pass


def get_stellaris_save_directory():
    """Attempts to find the Stellaris save games directory automatically."""
    user_profile = os.environ.get('USERPROFILE', os.path.expanduser("~"))
    possible_paths = [
        os.path.join(user_profile, "Documents", "Paradox Interactive", "Stellaris", "save games"),
        os.path.join(user_profile, "OneDrive", "Documents", "Paradox Interactive", "Stellaris", "save games")
    ]
    for path in possible_paths:
        if os.path.exists(path):
            return path
    return None


def wait_until_save_ready(filepath, timeout=5.0):
    """Ensures Stellaris has finished flushing the .sav zip archive to disk before copying/reading."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if os.path.exists(filepath) and os.path.getsize(filepath) > 0 and zipfile.is_zipfile(filepath):
                with zipfile.ZipFile(filepath, 'r') as z:
                    if 'gamestate' in z.namelist():
                        return True
        except Exception:
            pass
        time.sleep(0.4)
    return False


def get_folder_latest_mtime(folder_path):
    """Returns the latest modification time across the folder and all .sav files inside it."""
    latest = 0.0
    try:
        latest = os.stat(folder_path).st_mtime
        for entry in os.scandir(folder_path):
            if entry.is_file() and entry.name.endswith('.sav'):
                latest = max(latest, entry.stat().st_mtime)
    except Exception:
        pass
    return latest


def _restore_snapshot_types(snapshot):
    """Restores hashable tuples (e.g. tech (key, level) pairs) after loading a JSON cache from disk."""
    for prog in snapshot.get("player_progression", {}).values():
        if "techs" in prog:
            prog["techs"] = [tuple(t) for t in prog["techs"]]
    if "raw_queue_items" in snapshot:
        snapshot["raw_queue_items"] = [tuple(item) for item in snapshot["raw_queue_items"]]
    return snapshot


class LiveCampaignIncrementalBuilder:
    """
    Caches file headers, static galaxy map, and parsed per-date save snapshots (in RAM and on disk)
    so each incoming autosave is parsed once and reconciled with full history (firsts, fleet dips,
    diplomacy diffs, blockers, edicts, and policies) via build_order_extractor.reconcile_timeline_results.
    """
    def __init__(self, game_folder):
        self.game_folder = game_folder
        self.loc_dict = boe.load_stellaris_localization(game_folder)
        self.comp_icons, self.entity_icons, self.tech_catalog = boe.build_game_catalog(game_folder)
        self.blocker_types = boe.load_blocker_types(game_folder)
        self.ui_icon_map = boe.build_ui_icon_map(self.entity_icons, self.loc_dict)
        self.campaigns = {}

    def _load_disk_cache(self, folder_name, backup_dir):
        cache_path = os.path.join(backup_dir, CACHE_FILENAME)
        if not os.path.exists(cache_path):
            return None
        try:
            with gzip.open(cache_path, "rt", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("version") != CACHE_VERSION:
                return None

            parsed_by_date = {
                d_str: _restore_snapshot_types(snap)
                for d_str, snap in data.get("parsed_by_date", {}).items()
            }
            print(
                f"[{datetime.now().strftime('%H:%M:%S')}] [CACHE] Restored {len(parsed_by_date)} "
                f"parsed save(s) for '{folder_name}' from disk."
            )
            return {
                "parsed_by_date": parsed_by_date,
                "file_header_cache": {},
                "player_map": data.get("player_map", {}),
                "human_pids": set(data.get("human_pids", [])),
                "galaxy_map": data.get("galaxy_map"),
                "campaign_name": data.get("campaign_name", folder_name),
                "last_notif_count": int(data.get("last_notif_count", 0))
            }
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [CACHE] Ignoring unreadable cache for '{folder_name}': {e}")
            return None

    def _save_disk_cache(self, backup_dir, camp):
        cache_path = os.path.join(backup_dir, CACHE_FILENAME)
        temp_path = os.path.join(backup_dir, f"{CACHE_FILENAME}.tmp")
        payload = {
            "version": CACHE_VERSION,
            "campaign_name": camp["campaign_name"],
            "player_map": camp["player_map"],
            "human_pids": sorted(list(camp["human_pids"])),
            "galaxy_map": camp["galaxy_map"],
            "last_notif_count": camp["last_notif_count"],
            "parsed_by_date": camp["parsed_by_date"]
        }
        try:
            with gzip.open(temp_path, "wt", encoding="utf-8", compresslevel=3) as f:
                json.dump(payload, f, separators=(',', ':'), ensure_ascii=False)
            os.replace(temp_path, cache_path)
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [CACHE] Warning: failed to save state cache: {e}")

    def sync_and_archive_saves(self, src_dir, backup_dir):
        """
        Copies only modified .sav files from src_dir to backup_dir and creates date-stamped
        archive_<date>.sav copies so rolling autosaves never overwrite earlier match years.
        """
        os.makedirs(backup_dir, exist_ok=True)
        new_dates_found = []

        for entry in os.scandir(src_dir):
            if not entry.is_file() or not entry.name.endswith('.sav'):
                continue

            dest_raw = os.path.join(backup_dir, entry.name)
            src_mtime = entry.stat().st_mtime

            if os.path.exists(dest_raw):
                dest_stat = os.stat(dest_raw)
                if abs(src_mtime - dest_stat.st_mtime) < 0.1 and entry.stat().st_size == dest_stat.st_size:
                    continue

            if not wait_until_save_ready(entry.path):
                continue

            shutil.copy2(entry.path, dest_raw)

            try:
                head = boe.extract_gamestate_head(dest_raw, 2000)
                date_m = re.search(r'\bdate="([\d\.]+)"', head)
                if date_m:
                    save_date = date_m.group(1)
                    dated_dest = os.path.join(backup_dir, f"archive_{save_date}.sav")
                    if not os.path.exists(dated_dest):
                        shutil.copy2(dest_raw, dated_dest)
                        new_dates_found.append(save_date)
            except Exception:
                pass

        return new_dates_found

    def update_campaign_timeline(self, folder_name, backup_dir):
        """Incrementally parses only new save dates, updates persistent cache, and writes compact JSON atomically."""
        if folder_name not in self.campaigns:
            restored = self._load_disk_cache(folder_name, backup_dir)
            if restored is not None:
                self.campaigns[folder_name] = restored
            else:
                self.campaigns[folder_name] = {
                    "parsed_by_date": {},
                    "file_header_cache": {},
                    "player_map": {},
                    "human_pids": set(),
                    "galaxy_map": None,
                    "campaign_name": folder_name,
                    "last_notif_count": 0
                }

        camp = self.campaigns[folder_name]
        header_cache = camp["file_header_cache"]

        saves_by_date = {}
        campaign_raw_name = camp["campaign_name"]

        for entry in os.scandir(backup_dir):
            if not entry.is_file() or not entry.name.endswith('.sav'):
                continue
            filepath = entry.path
            mtime = entry.stat().st_mtime

            cached = header_cache.get(filepath)
            if cached and abs(cached[0] - mtime) < 0.1:
                _, d_str, c_name = cached
            else:
                try:
                    head = boe.extract_gamestate_head(filepath, 2000)
                    date_match = re.search(r'\bdate="([\d\.]+)"', head)
                    name_match = re.search(r'^name="([^"]+)"', head, re.MULTILINE)
                    if not date_match:
                        continue
                    d_str = date_match.group(1)
                    c_name = name_match.group(1) if name_match else folder_name
                    header_cache[filepath] = (mtime, d_str, c_name)
                except Exception:
                    continue

            campaign_raw_name = c_name
            if d_str not in saves_by_date or entry.name.startswith("archive_"):
                saves_by_date[d_str] = filepath

        if not saves_by_date and not camp["parsed_by_date"]:
            return

        disk_dates = sorted(saves_by_date.keys())
        campaign_name = re.sub(r'[\\/*?:"<>|]', "", campaign_raw_name)
        camp["campaign_name"] = campaign_name

        # Initialize player_map, human_pids, and static galaxy_map ONCE from the earliest save
        if not camp["player_map"] and disk_dates:
            first_gs = boe.extract_gamestate_content(saves_by_date[disk_dates[0]])
            camp["player_map"] = boe.extract_player_names(first_gs)
            camp["human_pids"] = boe.extract_human_player_ids(first_gs)
            camp["galaxy_map"] = boe.extract_galaxy_map_data(first_gs, self.loc_dict, camp["player_map"])

        player_pids = list(camp["player_map"].keys())
        unparsed_dates = [d for d in disk_dates if d not in camp["parsed_by_date"]]

        if not unparsed_dates:
            return

        t0 = time.time()
        if len(unparsed_dates) <= 2:
            boe.init_worker(self.loc_dict, player_pids, self.comp_icons, self.blocker_types)
            for d_str in unparsed_dates:
                task = (0, 1, d_str, saves_by_date[d_str])
                snap = boe.process_single_save(task)
                camp["parsed_by_date"][d_str] = snap
                camp["human_pids"].update(snap.get("save_human_pids", []))
        else:
            max_workers = max(1, min(4, (os.cpu_count() or 4) - 1))
            tasks = [(i, len(unparsed_dates), d_str, saves_by_date[d_str]) for i, d_str in enumerate(unparsed_dates)]
            with ProcessPoolExecutor(
                max_workers=max_workers,
                initializer=boe.init_worker,
                initargs=(self.loc_dict, player_pids, self.comp_icons, self.blocker_types)
            ) as executor:
                futures = {executor.submit(boe.process_single_save, t): t[2] for t in tasks}
                for future in as_completed(futures):
                    d_str = futures[future]
                    snap = future.result()
                    camp["parsed_by_date"][d_str] = snap
                    camp["human_pids"].update(snap.get("save_human_pids", []))

        # Use all cached dates (so prior saves & firsts survive even if old .sav files are removed)
        all_sorted_dates = sorted(camp["parsed_by_date"].keys())
        ordered_results = [camp["parsed_by_date"][d] for d in all_sorted_dates]

        timeline_data = boe.reconcile_timeline_results(
            campaign_name=campaign_name,
            player_map=camp["player_map"],
            results=ordered_results,
            loc_dict=self.loc_dict,
            tech_catalog=self.tech_catalog,
            ui_icon_map=self.ui_icon_map,
            galaxy_map=camp["galaxy_map"],
            human_pids=camp["human_pids"],
            include_last_updated=True
        )

        os.makedirs(OUTPUT_FOLDER, exist_ok=True)
        out_filename = f"{campaign_name}_timeline.json"
        final_path = os.path.join(OUTPUT_FOLDER, out_filename)
        temp_path = os.path.join(OUTPUT_FOLDER, f".{out_filename}.tmp")

        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(timeline_data, f, separators=(',', ':'), ensure_ascii=False)

        os.replace(temp_path, final_path)

        total_notifs = len(timeline_data.get("notifications", []))
        delta_notifs = max(0, total_notifs - camp["last_notif_count"])
        camp["last_notif_count"] = total_notifs
        self._save_disk_cache(backup_dir, camp)

        elapsed = time.time() - t0
        alert_str = f" | Alerts: {total_notifs} (+{delta_notifs} new)" if delta_notifs else f" | Alerts: {total_notifs}"
        print(
            f"[{datetime.now().strftime('%H:%M:%S')}] [LIVE UPDATE] Added {len(unparsed_dates)} save(s) "
            f"(Total: {len(ordered_results)} | Latest: {all_sorted_dates[-1]}{alert_str}) in {elapsed:.2f}s"
        )


def main():
    print("--- Stellaris Colosseum Live Autosaver & Incremental Extractor ---")
    set_low_cpu_priority()

    game_folder = get_stellaris_dir(auto_prompt=True)
    if not game_folder:
        print("[!] Cannot run live extractor without a valid Stellaris installation path. Run setup.cmd first.")
        return

    save_base_dir = get_stellaris_save_directory()
    if not save_base_dir:
        print("Error: Could not automatically find your Stellaris 'save games' directory.")
        return

    os.makedirs(DESTINATION_FOLDER, exist_ok=True)
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)

    live_builder = LiveCampaignIncrementalBuilder(game_folder)

    print(f"\nWatching Stellaris saves at: {save_base_dir}")
    print(f"Archiving backups into:      {DESTINATION_FOLDER}")
    print(f"Live JSON output directory:  {OUTPUT_FOLDER}")

    folder_mtimes = {}
    for entry in os.scandir(save_base_dir):
        if entry.is_dir():
            folder_mtimes[entry.name] = get_folder_latest_mtime(entry.path)

    print(f"\nMonitoring for new autosaves every {CHECK_INTERVAL_SECONDS} seconds.")
    print("Press Ctrl+C in this window to stop.\n")

    try:
        while True:
            for entry in os.scandir(save_base_dir):
                if not entry.is_dir():
                    continue

                current_mtime = get_folder_latest_mtime(entry.path)
                last_mtime = folder_mtimes.get(entry.name, 0.0)

                if current_mtime > last_mtime:
                    folder_mtimes[entry.name] = current_mtime
                    backup_path = os.path.join(DESTINATION_FOLDER, entry.name)

                    try:
                        time.sleep(2.0)
                        live_builder.sync_and_archive_saves(entry.path, backup_path)
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] Change detected! Synced folder: {entry.name}")
                        live_builder.update_campaign_timeline(entry.name, backup_path)
                    except Exception as e:
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] Error syncing {entry.name}: {e}")

            time.sleep(CHECK_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        print("\nLive Autosaver stopped manually. Good luck out there!")


if __name__ == "__main__":
    main()