import os
import re
import json
import time
import ctypes
import shutil
import zipfile
from datetime import datetime
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed

from setup_config import get_stellaris_dir
import build_order_extractor as boe

# --- CONFIGURATION ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DESTINATION_FOLDER = os.path.join(SCRIPT_DIR, "Stellaris_Backups")
OUTPUT_FOLDER = os.path.join(SCRIPT_DIR, "output")

# How often to check for new autosaves (in seconds)
CHECK_INTERVAL_SECONDS = 15


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


class LiveCampaignIncrementalBuilder:
    """Caches file headers and parsed save results so save #338 is just as fast as save #1."""
    def __init__(self, game_folder):
        self.game_folder = game_folder
        self.loc_dict = boe.load_stellaris_localization(game_folder)
        self.comp_icons, self.entity_icons, self.tech_catalog = boe.build_game_catalog(game_folder)

        self.ui_icon_map = {}
        for raw_key, dds_path in self.entity_icons.items():
            self.ui_icon_map[raw_key] = dds_path
            clean_name = boe.clean_loc(raw_key, self.loc_dict)
            if clean_name and clean_name not in self.ui_icon_map:
                self.ui_icon_map[clean_name] = dds_path

        # campaign_folder_name -> {
        #   "parsed_by_date": {date: res},
        #   "file_header_cache": {filepath: (mtime, date_str, camp_name)},
        #   "player_map": {...},
        #   "campaign_name": str
        # }
        self.campaigns = {}

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

            # Skip copying if the file in Stellaris_Backups is already identical in timestamp and size
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
        """Incrementally parses only new save dates and writes compact JSON atomically."""
        if folder_name not in self.campaigns:
            self.campaigns[folder_name] = {
                "parsed_by_date": {},
                "file_header_cache": {},
                "player_map": {},
                "campaign_name": folder_name
            }

        camp = self.campaigns[folder_name]
        header_cache = camp["file_header_cache"]

        saves_by_date = {}
        campaign_raw_name = camp["campaign_name"]

        # O(1) header scan: only opens .sav files whose mtime has changed
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

        if not saves_by_date:
            return

        sorted_dates = sorted(saves_by_date.keys())
        campaign_name = re.sub(r'[\\/*?:"<>|]', "", campaign_raw_name)
        camp["campaign_name"] = campaign_name

        # Initialize player_map ONCE on the first save of the campaign (no double-decompression on every autosave)
        if not camp["player_map"]:
            first_gs = boe.extract_gamestate_content(saves_by_date[sorted_dates[0]])
            camp["player_map"] = boe.extract_player_names(first_gs)

        player_pids = list(camp["player_map"].keys())
        unparsed_dates = [d for d in sorted_dates if d not in camp["parsed_by_date"]]

        if not unparsed_dates:
            return

        t0 = time.time()
        # Single-core in-process parse for 1-2 live saves (zero process-spawn overhead)
        if len(unparsed_dates) <= 2:
            boe.init_worker(self.loc_dict, player_pids, self.comp_icons)
            for d_str in unparsed_dates:
                task = (0, 1, d_str, saves_by_date[d_str])
                camp["parsed_by_date"][d_str] = boe.process_single_save(task)
        else:
            # Multi-core catch-up if starting autosaver mid-campaign with dozens of existing saves
            max_workers = max(1, min(4, (os.cpu_count() or 4) - 1))
            tasks = [(i, len(unparsed_dates), d_str, saves_by_date[d_str]) for i, d_str in enumerate(unparsed_dates)]
            with ProcessPoolExecutor(
                max_workers=max_workers,
                initializer=boe.init_worker,
                initargs=(self.loc_dict, player_pids, self.comp_icons)
            ) as executor:
                futures = {executor.submit(boe.process_single_save, t): t[2] for t in tasks}
                for future in as_completed(futures):
                    d_str = futures[future]
                    camp["parsed_by_date"][d_str] = future.result()

        ordered_results = [camp["parsed_by_date"][d] for d in sorted_dates if d in camp["parsed_by_date"]]
        timeline_data = self._reconcile_results(campaign_name, camp["player_map"], ordered_results)

        os.makedirs(OUTPUT_FOLDER, exist_ok=True)
        out_filename = f"{campaign_name}_timeline.json"
        final_path = os.path.join(OUTPUT_FOLDER, out_filename)
        temp_path = os.path.join(OUTPUT_FOLDER, f".{out_filename}.tmp")

        # Compact separators=(',', ':') cuts file size & write time by ~50% at 300+ saves
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(timeline_data, f, separators=(',', ':'), ensure_ascii=False)

        os.replace(temp_path, final_path)
        elapsed = time.time() - t0
        print(
            f"[{datetime.now().strftime('%H:%M:%S')}] [LIVE UPDATE] Added {len(unparsed_dates)} save(s) "
            f"(Total: {len(ordered_results)} | Latest: {sorted_dates[-1]}) in {elapsed:.2f}s"
        )

    def _reconcile_results(self, campaign_name, player_map, results):
        loc_dict = self.loc_dict
        tech_catalog = self.tech_catalog

        timeline_data = {
            "campaign_name": campaign_name,
            "last_updated": time.time(),
            "icons": self.ui_icon_map,
            "players": {}
        }
        for pid, pname in player_map.items():
            timeline_data["players"][pid] = {
                "name": pname,
                "empire_name": pname,
                "stats_history": [],
                "empire_events": [],
                "tech_events": [],
                "planets": {}
            }

        build_orders = defaultdict(lambda: defaultdict(list))
        seen_construction_ids = set()
        planet_current_owners = {}
        master_colony_map = {}
        master_planet_names = {}
        player_state = {}
        empire_events = defaultdict(list)
        tech_events = defaultdict(list)

        def make_tech_event(date_str, t_key, level, is_starting):
            t_meta = tech_catalog.get(t_key, {
                "area": "physics",
                "tier": 1,
                "icon": "/gfx/interface/icons/research_icon.dds"
            })
            base_name = boe.clean_loc(t_key, loc_dict)
            if level > 1:
                base_name = f"{base_name} (Level {level})"
            return {
                "date": date_str,
                "key": t_key,
                "name": base_name,
                "area": t_meta["area"],
                "tier": t_meta["tier"],
                "icon": t_meta["icon"],
                "starting": is_starting
            }

        for res in results:
            if not res:
                continue
            date = res["date"]

            for pid, stat_obj in res["player_stats"].items():
                if pid in timeline_data["players"]:
                    timeline_data["players"][pid]["stats_history"].append(stat_obj)

            for pid, emp_name in res["empire_names"].items():
                if emp_name and pid in timeline_data["players"]:
                    timeline_data["players"][pid]["empire_name"] = emp_name

            for pid, prog in res["player_progression"].items():
                if pid not in timeline_data["players"]:
                    continue

                if pid not in player_state:
                    player_state[pid] = {
                        "gov_type": prog["gov_type"],
                        "authority": prog["authority"],
                        "ethics": set(prog["ethics"]),
                        "civics": set(prog["civics"]),
                        "traditions": set(prog["traditions"]),
                        "ascension_perks": set(prog["ascension_perks"]),
                        "council_agenda": prog["council_agenda"],
                        "cooldown_agendas": set(prog["cooldown_agendas"]),
                        "finish_modifiers": set(prog["finish_modifiers"]),
                        "techs": set(prog["techs"])
                    }
                    if prog["council_agenda"]:
                        empire_events[pid].append({
                            "date": date,
                            "type": "AGENDA_SELECTED",
                            "item": boe.clean_loc(prog["council_agenda"], loc_dict)
                        })
                    for ag in prog["cooldown_agendas"]:
                        empire_events[pid].append({
                            "date": date,
                            "type": "AGENDA_LAUNCHED",
                            "item": boe.clean_loc(ag, loc_dict)
                        })
                    for t in prog["traditions"]:
                        empire_events[pid].append({
                            "date": date,
                            "type": "TRADITION",
                            "item": boe.format_tradition_name(t, loc_dict)
                        })
                    for ap in prog["ascension_perks"]:
                        empire_events[pid].append({
                            "date": date,
                            "type": "ASCENSION_PERK",
                            "item": boe.clean_loc(ap, loc_dict)
                        })
                    for t_key, lvl in prog["techs"]:
                        tech_events[pid].append(make_tech_event(date, t_key, lvl, True))
                else:
                    st = player_state[pid]
                    for t_tuple in prog["techs"]:
                        if t_tuple not in st["techs"]:
                            st["techs"].add(t_tuple)
                            tech_events[pid].append(make_tech_event(date, t_tuple[0], t_tuple[1], False))

                    for t in prog["traditions"]:
                        if t not in st["traditions"]:
                            st["traditions"].add(t)
                            empire_events[pid].append({
                                "date": date,
                                "type": "TRADITION",
                                "item": boe.format_tradition_name(t, loc_dict)
                            })

                    for ap in prog["ascension_perks"]:
                        if ap not in st["ascension_perks"]:
                            st["ascension_perks"].add(ap)
                            empire_events[pid].append({
                                "date": date,
                                "type": "ASCENSION_PERK",
                                "item": boe.clean_loc(ap, loc_dict)
                            })

                    new_gov, new_auth = prog["gov_type"], prog["authority"]
                    if (new_gov and new_gov != st["gov_type"]) or (new_auth and new_auth != st["authority"]):
                        old_desc = boe.clean_loc(st["gov_type"] or st["authority"], loc_dict)
                        new_desc = boe.clean_loc(new_gov or new_auth, loc_dict)
                        if new_auth != st["authority"] and new_auth:
                            new_desc = f"{new_desc} ({boe.clean_loc(new_auth, loc_dict)})"
                        empire_events[pid].append({
                            "date": date,
                            "type": "GOV_CHANGED",
                            "item": f"{old_desc} → {new_desc}"
                        })
                        st["gov_type"] = new_gov
                        st["authority"] = new_auth

                    curr_ethics = set(prog["ethics"])
                    if curr_ethics and curr_ethics != st["ethics"]:
                        added = [boe.clean_loc(e, loc_dict) for e in (curr_ethics - st["ethics"])]
                        removed = [boe.clean_loc(e, loc_dict) for e in (st["ethics"] - curr_ethics)]
                        parts = []
                        if added: parts.append(f"+{', '.join(added)}")
                        if removed: parts.append(f"-{', '.join(removed)}")
                        empire_events[pid].append({
                            "date": date,
                            "type": "ETHICS_CHANGED",
                            "item": " / ".join(parts)
                        })
                        st["ethics"] = curr_ethics

                    curr_civics = set(prog["civics"])
                    if curr_civics != st["civics"]:
                        added = [boe.clean_loc(c, loc_dict) for c in (curr_civics - st["civics"])]
                        removed = [boe.clean_loc(c, loc_dict) for c in (st["civics"] - curr_civics)]
                        parts = []
                        if added: parts.append(f"+{', '.join(added)}")
                        if removed: parts.append(f"-{', '.join(removed)}")
                        empire_events[pid].append({
                            "date": date,
                            "type": "CIVICS_CHANGED",
                            "item": " / ".join(parts)
                        })
                        st["civics"] = curr_civics

                    curr_cd_agendas = set(prog["cooldown_agendas"])
                    newly_launched = set()
                    for ag in (curr_cd_agendas - st["cooldown_agendas"]):
                        newly_launched.add(ag)
                        empire_events[pid].append({
                            "date": date,
                            "type": "AGENDA_LAUNCHED",
                            "item": boe.clean_loc(ag, loc_dict)
                        })
                    st["cooldown_agendas"] = curr_cd_agendas

                    curr_mods = set(prog["finish_modifiers"])
                    for mod in (curr_mods - st["finish_modifiers"]):
                        ag_base = mod[:-7] if mod.endswith("_finish") else mod
                        if ag_base not in newly_launched:
                            empire_events[pid].append({
                                "date": date,
                                "type": "AGENDA_LAUNCHED",
                                "item": boe.clean_loc(ag_base, loc_dict)
                            })
                    st["finish_modifiers"] = curr_mods

                    new_ag = prog["council_agenda"]
                    if new_ag != st["council_agenda"]:
                        if new_ag:
                            empire_events[pid].append({
                                "date": date,
                                "type": "AGENDA_SELECTED",
                                "item": boe.clean_loc(new_ag, loc_dict)
                            })
                        st["council_agenda"] = new_ag

            colony_map = res["colony_map"]
            master_colony_map.update(colony_map)
            master_planet_names.update(res["planet_names"])

            for item_id, player_id, build_item, internal_colony_id in res["raw_queue_items"]:
                true_planet_id = colony_map.get(
                    internal_colony_id,
                    master_colony_map.get(internal_colony_id, internal_colony_id)
                )
                if true_planet_id and item_id not in seen_construction_ids:
                    seen_construction_ids.add(item_id)
                    build_orders[player_id][true_planet_id].append((date, "BUILD", build_item))

            for pid, new_owner in res["new_owners"].items():
                old_owner = planet_current_owners.get(pid)
                if old_owner != new_owner:
                    if old_owner and old_owner != '4294967295' and new_owner != '4294967295':
                        build_orders[old_owner][pid].append((date, "LOST", new_owner))
                        build_orders[new_owner][pid].append((date, "TAKEN", old_owner))
                    elif (not old_owner or old_owner == '4294967295') and new_owner != '4294967295':
                        build_orders[new_owner][pid].append((date, "COLONIZED", None))
                    planet_current_owners[pid] = new_owner

        for pid, evts in empire_events.items():
            if pid in timeline_data["players"]:
                timeline_data["players"][pid]["empire_events"] = sorted(evts, key=lambda x: x["date"])

        for pid, tevts in tech_events.items():
            if pid in timeline_data["players"]:
                timeline_data["players"][pid]["tech_events"] = sorted(tevts, key=lambda x: x["date"])

        for pid, planets in build_orders.items():
            if pid not in timeline_data["players"]:
                continue
            for planet_id, builds in planets.items():
                raw_planet_name = master_planet_names.get(planet_id, f"Planet ID {planet_id}")
                clean_builds = []
                for event in sorted(builds, key=lambda x: x[0]):
                    date, evt_type, evt_data = event
                    if evt_type == "BUILD":
                        clean_item = boe.clean_loc(evt_data, loc_dict)
                        clean_builds.append({"date": date, "type": "BUILD", "item": clean_item})
                    elif evt_type in ["TAKEN", "LOST"]:
                        clean_builds.append({
                            "date": date,
                            "type": evt_type,
                            "item": player_map.get(evt_data, f"AI/Entity {evt_data}")
                        })
                    elif evt_type == "COLONIZED":
                        clean_builds.append({"date": date, "type": "COLONIZED", "item": None})
                timeline_data["players"][pid]["planets"][planet_id] = {
                    "name": boe.clean_loc(raw_planet_name, loc_dict),
                    "builds": clean_builds
                }

        return timeline_data


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
                        # Wait 2 seconds after Stellaris finishes writing so MP unpause network ticks settle first
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