import os
import sys
import time
import zipfile
import re
import json
import tkinter as tk
from tkinter import filedialog
from collections import defaultdict, Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from setup_config import get_stellaris_dir

# Pre-compiled global regexes for worker processes
TOP_LEVEL_PATTERN = re.compile(r'(?:^|\n)\s*(\d+)\s*=\s*\{')
ITEM_PATTERN = re.compile(
    r'(\d+)=\s*\{[^{}]*queue=(\d+)\s*paying_country=(\d+)(?:(?!queue=)[\s\S]){1,300}?(?:building|district|zone)="([^"]+)"\s*planet=(\d+)'
)
RES_KEYS = [
    'energy', 'minerals', 'food', 'trade', 'alloys', 'consumer_goods',
    'physics_research', 'society_research', 'engineering_research',
    'unity', 'influence', 'minor_artifacts', 'volatile_motes',
    'exotic_gases', 'rare_crystals', 'sr_zro', 'nanites', 'sr_living_metal'
]
NON_MILITARY_SIZES = {
    'constructor', 'science', 'colonizer', 'sponsored_colonizer', 'transport',
    'mining_station', 'research_station', 'observation_station',
    'military_station_small', 'military_station_medium', 'military_station_large'
}

# Worker-level globals (initialized once per CPU core)
WORKER_LOC_DICT = {}
WORKER_PLAYER_PIDS = set()
WORKER_SYSTEM_NAMES = {}


def load_stellaris_localization(game_folder):
    loc_dict = {}
    target_dirs = [
        os.path.join(game_folder, "localisation", "english"),
        os.path.join(game_folder, "localisation", "english", "name_lists"),
        os.path.join(game_folder, "localisation", "english", "random_names")
    ]
    loc_pattern = re.compile(r'^\s*([a-zA-Z0-9_\.\-]+)\s*:\d*\s*\"([^\"]*)\"', re.MULTILINE)

    print("[1/4] Loading Stellaris Localization files...")
    for d in target_dirs:
        if not os.path.exists(d):
            continue
        for root, dirs, files in os.walk(d):
            for file in files:
                if file.endswith('.yml'):
                    try:
                        with open(os.path.join(root, file), 'r', encoding='utf-8-sig', errors='ignore') as f:
                            for match in loc_pattern.finditer(f.read()):
                                loc_dict[match.group(1)] = match.group(2)
                    except Exception:
                        pass
    return loc_dict


def resolve_loc_references(text, loc_dict, depth=0):
    """Recursively replaces $KEY$ localization aliases with their actual English strings."""
    if not text or '$' not in text or depth > 5:
        return text

    def repl(match):
        ref_key = match.group(1)
        if ref_key in loc_dict:
            return resolve_loc_references(loc_dict[ref_key], loc_dict, depth + 1)
        for k in (ref_key.upper(), ref_key.lower()):
            if k in loc_dict:
                return resolve_loc_references(loc_dict[k], loc_dict, depth + 1)
        return ref_key.replace('_', ' ').title()

    return re.sub(r'\$([A-Za-z0-9_\.\-]+)\$', repl, text)


def clean_loc(key, loc_dict):
    if not key:
        return "None"
    raw = None
    if key in loc_dict:
        raw = loc_dict[key]
    elif f"{key}_name" in loc_dict:
        raw = loc_dict[f"{key}_name"]
    else:
        stripped = re.sub(
            r'^(?:SMALL|MEDIUM|LARGE|CORVETTE|DESTROYER|CRUISER|BATTLESHIP|TITAN|COLOSSUS|JUGGERNAUT|PLATFORM|STARBASE)_',
            '', key
        )
        if stripped in loc_dict:
            raw = loc_dict[stripped]

    if raw:
        resolved = resolve_loc_references(raw, loc_dict)
        return re.sub(r'§[A-Za-z!]', '', resolved).strip()

    cleaned = key
    for prefix in ('trait_', 'civic_', 'origin_', 'building_', 'district_', 'agenda_', 'tr_', 'tradition_', 'ap_', 'gov_', 'auth_'):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
            break
    return cleaned.replace('_', ' ').title()


def format_tradition_name(t_key, loc_dict):
    """Formats tradition unlocks clearly, e.g. 'Adopted Supremacy', 'Finished Supremacy', or specific node name."""
    if t_key.endswith('_adopt'):
        base = t_key[:-6]
        tree_key = base.replace('tr_', 'tradition_', 1)
        tree_name = clean_loc(tree_key, loc_dict) if tree_key in loc_dict else clean_loc(base, loc_dict)
        return f"Adopted {tree_name} Tree"
    if t_key.endswith('_finish'):
        base = t_key[:-7]
        tree_key = base.replace('tr_', 'tradition_', 1)
        tree_name = clean_loc(tree_key, loc_dict) if tree_key in loc_dict else clean_loc(base, loc_dict)
        return f"Finished {tree_name} Tree"
    return clean_loc(t_key, loc_dict)


def extract_gamestate_head(file_or_dir_path, num_bytes=2000):
    if zipfile.is_zipfile(file_or_dir_path):
        with zipfile.ZipFile(file_or_dir_path, 'r') as z:
            if 'gamestate' in z.namelist():
                with z.open('gamestate') as f:
                    return f.read(num_bytes).decode('utf-8', errors='ignore')
    with open(file_or_dir_path, 'r', encoding='utf-8', errors='ignore') as f:
        return f.read(num_bytes)


def extract_gamestate_content(file_or_dir_path):
    if zipfile.is_zipfile(file_or_dir_path):
        with zipfile.ZipFile(file_or_dir_path, 'r') as z:
            if 'gamestate' in z.namelist():
                return z.read('gamestate').decode('utf-8', errors='ignore')
    with open(file_or_dir_path, 'r', encoding='utf-8', errors='ignore') as f:
        return f.read()


def extract_block(text, start_index):
    """C-speed bracket scanner using str.find."""
    find = text.find
    open_b = find('{', start_index)
    if open_b == -1:
        return ""
    depth = 1
    pos = open_b + 1
    while depth > 0:
        next_open = find('{', pos)
        next_close = find('}', pos)
        if next_close == -1:
            return ""
        if next_open != -1 and next_open < next_close:
            depth += 1
            pos = next_open + 1
        else:
            depth -= 1
            pos = next_close + 1
    return text[start_index:pos]


def get_top_level_blocks(container_text):
    blocks = {}
    pos = 0
    search = TOP_LEVEL_PATTERN.search
    while True:
        m = search(container_text, pos)
        if not m:
            break
        key = m.group(1)
        start_idx = m.end() - 1
        block_content = extract_block(container_text, start_idx)
        if block_content:
            blocks[key] = block_content
            pos = start_idx + len(block_content)
        else:
            pos = m.end()
    return blocks


def resolve_saved_name(block_text, loc_dict, default="Unknown"):
    name_m = re.search(r'\bname\s*=\s*\{', block_text)
    if name_m:
        name_blk = extract_block(block_text, name_m.end() - 1)
        lit_m = re.search(r'\bkey="([^"]+)"\s+literal=yes', name_blk)
        if lit_m:
            return lit_m.group(1)
        key_m = re.search(r'\bkey="([^"]+)"', name_blk)
        if key_m:
            raw_key = key_m.group(1)
            template = loc_dict.get(raw_key, raw_key)
            var_m = re.search(r'\bvariables\s*=\s*\{', name_blk)
            if var_m:
                var_blk = extract_block(name_blk, var_m.end() - 1)
                for vm in re.finditer(r'\{\s*key="([^"]+)"\s+value=\s*\{\s*key="([^"]+)"', var_blk):
                    k, v = vm.group(1), vm.group(2)
                    val_str = loc_dict.get(v, v)
                    template = template.replace(f"${k}$", val_str).replace(f"%{k}%", val_str)
            template = resolve_loc_references(template, loc_dict)
            template = re.sub(r'\$[A-Za-z0-9_.]+\$|%[A-Za-z0-9_.]+%', '', template).strip()
            return re.sub(r'§[A-Za-z!]', '', template) if template else raw_key

    str_m = re.search(r'\bname="([^"]+)"', block_text)
    if str_m:
        return clean_loc(str_m.group(1), loc_dict)
    return default


def classify_slot_size(slot_name, comp_key):
    s = slot_name.upper()
    if s.startswith("SMALL_GUN") or s.startswith("S_"): return "S"
    if s.startswith("MEDIUM_GUN") or s.startswith("M_"): return "M"
    if s.startswith("LARGE_GUN") or s.startswith("L_"): return "L"
    if s.startswith("PD") or "POINT_DEFENCE" in comp_key.upper() or "FLAK" in comp_key.upper(): return "P"
    if s.startswith("STRIKE_CRAFT") or "HANGAR" in comp_key.upper(): return "H"
    if s.startswith("TORPEDO") or s.startswith("G_") or "MISSILE" in comp_key.upper(): return "G"
    if s.startswith("EXTRA_LARGE") or s.startswith("XL"): return "X"
    if s.startswith("TITAN") or s.startswith("T_"): return "T"
    if s.startswith("PLANET_KILLER") or s.startswith("W_"): return "W"
    return "W"


def format_defense_name(comp_key, loc_dict):
    stripped = re.sub(r'^(?:SMALL|MEDIUM|LARGE)_', '', comp_key)
    tier_m = re.search(r'_([1-5])$', stripped)
    roman = {'1': 'I', '2': 'II', '3': 'III', '4': 'IV', '5': 'V'}.get(tier_m.group(1), '') if tier_m else ''
    label = clean_loc(stripped, loc_dict)
    if not label or label == stripped.replace('_', ' ').title():
        label = clean_loc(comp_key, loc_dict)
        label = re.sub(r'^(?:Small|Medium|Large)\s+', '', label)
    if roman and not label.endswith(f" {roman}"):
        label = f"{label} {roman}"
    return label


def parse_design_details(d_blk, loc_dict):
    target_blk = d_blk
    if not re.search(r'\bsection\s*=\s*\{', d_blk):
        gs_m = re.search(r'\bgrowth_stages\s*=\s*\{', d_blk)
        if gs_m:
            gs_blk = extract_block(d_blk, gs_m.end() - 1)
            first_stage = re.search(r'\{', gs_blk[1:])
            if first_stage:
                target_blk = extract_block(gs_blk, first_stage.start() + 1)

    s_m = re.search(r'\bship_size="([^"]+)"', target_blk) or re.search(r'\bship_size="([^"]+)"', d_blk)
    raw_size = s_m.group(1) if s_m else "unknown"
    size_label = clean_loc(raw_size, loc_dict)

    sections = []
    aux_counts = Counter()
    armor_counts = Counter()
    shield_counts = Counter()

    for sec_m in re.finditer(r'\bsection\s*=\s*\{', target_blk):
        sec_blk = extract_block(target_blk, sec_m.end() - 1)
        sec_tmpl_m = re.search(r'\btemplate="([^"]+)"', sec_blk)
        sec_slot_m = re.search(r'\bslot="([^"]+)"', sec_blk)
        sec_tmpl = sec_tmpl_m.group(1) if sec_tmpl_m else "Section"
        sec_slot = sec_slot_m.group(1) if sec_slot_m else ""
        sec_name = clean_loc(sec_tmpl, loc_dict)

        weapons = Counter()
        for comp_m in re.finditer(r'\bcomponent\s*=\s*\{', sec_blk):
            c_blk = extract_block(sec_blk, comp_m.end() - 1)
            c_slot_m = re.search(r'\bslot="([^"]+)"', c_blk)
            c_tmpl_m = re.search(r'\btemplate="([^"]+)"', c_blk)
            if not c_tmpl_m:
                continue
            c_slot = c_slot_m.group(1) if c_slot_m else ""
            c_tmpl = c_tmpl_m.group(1)
            c_upper = c_tmpl.upper()

            if c_slot.startswith("AUX_UTILITY") or "AUX" in c_slot:
                aux_counts[clean_loc(c_tmpl, loc_dict)] += 1
            elif "UTILITY" in c_slot:
                if "ARMOR" in c_upper or "PLATING" in c_upper:
                    armor_counts[format_defense_name(c_tmpl, loc_dict)] += 1
                elif "SHIELD" in c_upper or "DEFLECTOR" in c_upper or "BARRIER" in c_upper:
                    shield_counts[format_defense_name(c_tmpl, loc_dict)] += 1
                else:
                    aux_counts[clean_loc(c_tmpl, loc_dict)] += 1
            else:
                w_size = classify_slot_size(c_slot, c_tmpl)
                w_name = clean_loc(c_tmpl, loc_dict)
                weapons[(w_size, w_name)] += 1

        sections.append({
            "name": sec_name,
            "slot": sec_slot,
            "weapons": [{"size": k[0], "name": k[1], "count": v} for k, v in weapons.items()]
        })

    order = {"bow": 0, "mid": 1, "core": 1, "ship": 1, "stern": 2}
    sections.sort(key=lambda s: order.get(s["slot"].lower(), 3))

    req_comps = []
    for req_m in re.finditer(r'\brequired_component="([^"]+)"', target_blk):
        rc = req_m.group(1)
        if "EMPTY" not in rc.upper():
            req_comps.append(clean_loc(rc, loc_dict))

    return {
        "name": resolve_saved_name(d_blk, loc_dict, "Unknown Design"),
        "size": size_label,
        "raw_size": raw_size,
        "sections": sections,
        "auxiliaries": [{"name": k, "count": v} for k, v in aux_counts.items()],
        "armor": [{"name": k, "count": v} for k, v in armor_counts.items()],
        "shields": [{"name": k, "count": v} for k, v in shield_counts.items()],
        "required": req_comps
    }


def extract_player_names(gamestate_string):
    player_map = {}
    for name, country_id in re.compile(r'name="([^"]+)"\s*country=(\d+)').findall(gamestate_string):
        player_map[country_id] = name
    country_pattern = re.compile(
        r'^(\d+)=\s*\{\s*(?:save_on_death=\d+\s*)?(?:custom_name=(?:yes|no)\s*)?(?:has_advisor=(?:yes|no)\s*)?(?:initialized=yes\s*)?(?:flag=\s*\{[\s\S]*?\}\s*)?name=\s*\{\s*key="([^"]+)"',
        re.MULTILINE
    )
    for match in country_pattern.finditer(gamestate_string):
        if match.group(1) not in player_map:
            player_map[match.group(1)] = (
                match.group(2)
                .replace('NAME_', '')
                .replace('STARBASE_STATION_NAME_FORMAT_NON_PRIMARY', 'AI Empire')
                .replace('_', ' ')
                .title()
            )
    dead_country_start = gamestate_string.find('dead_country = {')
    if dead_country_start != -1:
        dead_countries = gamestate_string[dead_country_start:gamestate_string.find('}', dead_country_start)]
        for line in dead_countries.splitlines():
            match = re.match(r'^\s*(\d+)=', line)
            if match:
                player_map[match.group(1)] = f"Defeated Player {match.group(1)}"
    return player_map


def get_major_block(gamestate, block_name):
    match = re.search(rf'\n{block_name}\s*=\s*\{{', gamestate)
    if match:
        return extract_block(gamestate, match.end() - 1)
    return ""


def extract_country_resources(p_data, res_keys):
    resources = {rk: {"stockpile": 0.0, "balance": 0.0} for rk in res_keys}
    eco_m = re.search(r'\bstandard_economy_module\s*=\s*\{', p_data)
    if eco_m:
        eco_block = extract_block(p_data, eco_m.end() - 1)
        res_m = re.search(r'\bresources\s*=\s*\{', eco_block)
        if res_m:
            res_block = extract_block(eco_block, res_m.end() - 1)
            for rk in res_keys:
                val_m = re.search(rf'\b{rk}\s*=\s*"?([+-]?[\d\.]+)"?', res_block)
                if val_m:
                    resources[rk]["stockpile"] = float(val_m.group(1))

    budget_m = re.search(r'\bbudget\s*=\s*\{', p_data)
    if budget_m:
        budget_block = extract_block(p_data, budget_m.end() - 1)
        month_m = re.search(r'\blast_month\s*=\s*\{', budget_block) or re.search(r'\bcurrent_month\s*=\s*\{', budget_block)
        if month_m:
            month_block = extract_block(budget_block, month_m.end() - 1)
            bal_m = re.search(r'\bbalance\s*=\s*\{', month_block)
            if bal_m:
                bal_block = extract_block(month_block, bal_m.end() - 1)
                for rk in res_keys:
                    matches = re.findall(rf'\b{rk}\s*=\s*"?([+-]?[\d\.]+)"?', bal_block)
                    if matches:
                        resources[rk]["balance"] = sum(float(val) for val in matches)
                return resources

            inc_m = re.search(r'\bincome\s*=\s*\{', month_block)
            exp_m = re.search(r'\bexpenses?\s*=\s*\{', month_block)
            inc_block = extract_block(month_block, inc_m.end() - 1) if inc_m else ""
            exp_block = extract_block(month_block, exp_m.end() - 1) if exp_m else ""
            for rk in res_keys:
                inc_vals = re.findall(rf'\b{rk}\s*=\s*"?([+-]?[\d\.]+)"?', inc_block)
                exp_vals = re.findall(rf'\b{rk}\s*=\s*"?([+-]?[\d\.]+)"?', exp_block)
                resources[rk]["balance"] = sum(float(v) for v in inc_vals) - sum(float(v) for v in exp_vals)

    return resources


def extract_empire_progression(p_data):
    """Extracts government, civics, active agenda, cooldowns, finish modifiers, traditions, and ascension perks."""
    gov_type = ""
    authority = ""
    origin = ""
    civics = []
    council_agenda = ""
    council_agenda_progress = 0.0
    agenda_cooldowns = []

    gov_m = re.search(r'\bgovernment\s*=\s*\{', p_data)
    if gov_m:
        gov_blk = extract_block(p_data, gov_m.end() - 1)
        gt_m = re.search(r'\btype\s*=\s*"?([^"\s\}]+)"?', gov_blk)
        au_m = re.search(r'\bauthority\s*=\s*"?([^"\s\}]+)"?', gov_blk)
        or_m = re.search(r'\borigin\s*=\s*"?([^"\s\}]+)"?', gov_blk)
        if gt_m: gov_type = gt_m.group(1)
        if au_m: authority = au_m.group(1)
        if or_m: origin = or_m.group(1)

        civ_m = re.search(r'\bcivics\s*=\s*\{([^\}]*)\}', gov_blk)
        if civ_m:
            civics = re.findall(r'"([^"]+)"', civ_m.group(1))

        ca_m = re.search(r'\bcouncil_agenda\s*=\s*"([^"]+)"', gov_blk)
        if ca_m:
            council_agenda = ca_m.group(1)
        cap_m = re.search(r'\bcouncil_agenda_progress\s*=\s*([\d\.]+)', gov_blk)
        if cap_m:
            council_agenda_progress = float(cap_m.group(1))

        for cd_m in re.finditer(r'\bcouncil_agenda_cooldowns\s*=\s*\{', gov_blk):
            cd_blk = extract_block(gov_blk, cd_m.end() - 1)
            ag_m = re.search(r'\bcouncil_agenda\s*=\s*"([^"]+)"', cd_blk)
            sd_m = re.search(r'\bstart_date\s*=\s*"([\d\.]+)"', cd_blk)
            if ag_m:
                agenda_cooldowns.append((ag_m.group(1), sd_m.group(1) if sd_m else ""))

    finish_modifiers = []
    tm_m = re.search(r'\btimed_modifier\s*=\s*\{', p_data)
    if tm_m:
        tm_blk = extract_block(p_data, tm_m.end() - 1)
        finish_modifiers = re.findall(r'\bmodifier\s*=\s*"(agenda_[^"]+_finish)"', tm_blk)

    traditions = []
    trad_m = re.search(r'\btraditions\s*=\s*\{([^\}]*)\}', p_data)
    if trad_m:
        traditions = re.findall(r'"([^"]+)"', trad_m.group(1))

    ascension_perks = []
    ap_m = re.search(r'\bascension_perks\s*=\s*\{([^\}]*)\}', p_data)
    if ap_m:
        ascension_perks = re.findall(r'"([^"]+)"', ap_m.group(1))

    return {
        "gov_type": gov_type,
        "authority": authority,
        "origin": origin,
        "civics": civics,
        "council_agenda": council_agenda,
        "council_agenda_progress": council_agenda_progress,
        "agenda_cooldowns": agenda_cooldowns,
        "finish_modifiers": finish_modifiers,
        "traditions": traditions,
        "ascension_perks": ascension_perks
    }


def init_worker(loc_dict, player_pids, system_names):
    global WORKER_LOC_DICT, WORKER_PLAYER_PIDS, WORKER_SYSTEM_NAMES
    WORKER_LOC_DICT = loc_dict
    WORKER_PLAYER_PIDS = set(player_pids)
    WORKER_SYSTEM_NAMES = system_names


def process_single_save(task):
    index, total_files, date, filepath = task
    loc_dict = WORKER_LOC_DICT
    player_pids = WORKER_PLAYER_PIDS
    system_names = WORKER_SYSTEM_NAMES

    gamestate = extract_gamestate_content(filepath)

    # 1. Extract Country Blocks FIRST
    country_block = get_major_block(gamestate, "country")
    country_top_blocks = get_top_level_blocks(country_block)

    player_country_blocks = {}
    needed_species_ids = set()
    needed_fleet_ids = set()
    player_fleet_lists = defaultdict(list)

    for pid in player_pids:
        p_data = country_top_blocks.get(pid)
        if not p_data:
            continue
        player_country_blocks[pid] = p_data

        sp_id_m = re.search(r'founder_species_ref\s*=\s*(\d+)', p_data)
        if sp_id_m:
            needed_species_ids.add(sp_id_m.group(1))

        fm_match = re.search(r'\bfleets_manager\s*=\s*\{', p_data)
        if fm_match:
            fm_block = extract_block(p_data, fm_match.end() - 1)
            of_match = re.search(r'\bowned_fleets\s*=\s*\{', fm_block)
            if of_match:
                of_block = extract_block(fm_block, of_match.end() - 1)
                for fid_m in re.finditer(r'fleet\s*=\s*(\d+)', of_block):
                    fid = fid_m.group(1)
                    needed_fleet_ids.add(fid)
                    player_fleet_lists[pid].append(fid)

    # 2. Extract needed Species
    species_db_block = get_major_block(gamestate, "species_db")
    species_dict = {}
    for sp_id, sp_block in get_top_level_blocks(species_db_block).items():
        if sp_id not in needed_species_ids:
            continue
        tr_match = re.search(r'\btraits\s*=\s*\{', sp_block)
        if tr_match:
            tr_block = extract_block(sp_block, tr_match.end() - 1)
            species_dict[sp_id] = [clean_loc(t, loc_dict) for t in re.findall(r'trait="([^"]+)"', tr_block)]

    # 3. Scan Fleets
    fleet_block = get_major_block(gamestate, "fleet")
    raw_player_fleets = {}
    needed_ship_ids = set()

    for f_id, f_blk in get_top_level_blocks(fleet_block).items():
        if f_id not in needed_fleet_ids:
            continue
        if re.search(r'\bcivilian\s*=\s*yes\b', f_blk) or re.search(r'\bstation\s*=\s*yes\b', f_blk):
            continue
        sh_match = re.search(r'\bships\s*=\s*\{', f_blk)
        if not sh_match:
            continue
        sh_blk = extract_block(f_blk, sh_match.end() - 1)
        f_ship_ids = re.findall(r'\b(\d+)\b', sh_blk)
        if not f_ship_ids:
            continue
        raw_player_fleets[f_id] = (f_blk, f_ship_ids)
        needed_ship_ids.update(f_ship_ids)

    # 4. Scan Ships
    ships_block = get_major_block(gamestate, "ships")
    raw_ships = {}
    needed_design_ids = set()

    if needed_ship_ids:
        for s_id, s_blk in get_top_level_blocks(ships_block).items():
            if s_id not in needed_ship_ids:
                continue
            des_id = None
            impl = re.search(r'ship_design_implementation=\s*\{', s_blk)
            if impl:
                impl_blk = extract_block(s_blk, impl.end() - 1)
                d_m = re.search(r'design\s*=\s*(\d+)', impl_blk)
                if d_m:
                    des_id = d_m.group(1)
            else:
                d_m = re.search(r'ship_design\s*=\s*(\d+)', s_blk)
                if d_m:
                    des_id = d_m.group(1)
            if des_id:
                needed_design_ids.add(des_id)
                raw_ships[s_id] = (des_id, s_blk)

    # 5. Parse Ship Designs
    design_block = get_major_block(gamestate, "ship_design")
    designs = {}
    if needed_design_ids:
        for d_id, d_blk in get_top_level_blocks(design_block).items():
            if d_id in needed_design_ids:
                designs[d_id] = parse_design_details(d_blk, loc_dict)

    # 6. Finalize Ships & Fleets
    ships = {}
    for s_id, (des_id, s_blk) in raw_ships.items():
        if des_id not in designs:
            continue
        d_info = designs[des_id]
        if d_info["raw_size"] in NON_MILITARY_SIZES or d_info["raw_size"].startswith("starbase_"):
            continue
        hp_m = re.search(r'\bhitpoints\s*=\s*([\d\.]+)', s_blk)
        max_hp_m = re.search(r'\bmax_hitpoints\s*=\s*([\d\.]+)', s_blk)
        arm_m = re.search(r'\barmor_hitpoints\s*=\s*([\d\.]+)', s_blk)
        max_arm_m = re.search(r'\bmax_armor_hitpoints\s*=\s*([\d\.]+)', s_blk)
        shd_m = re.search(r'\bshield_hitpoints\s*=\s*([\d\.]+)', s_blk)
        max_shd_m = re.search(r'\bmax_shield_hitpoints\s*=\s*([\d\.]+)', s_blk)

        ships[s_id] = {
            "design_id": des_id,
            "ship_name": resolve_saved_name(s_blk, loc_dict, f"Ship {s_id}"),
            "hp": float(hp_m.group(1)) if hp_m else 0.0,
            "max_hp": float(max_hp_m.group(1)) if max_hp_m else 0.0,
            "armor": float(arm_m.group(1)) if arm_m else 0.0,
            "max_armor": float(max_arm_m.group(1)) if max_arm_m else 0.0,
            "shield": float(shd_m.group(1)) if shd_m else 0.0,
            "max_shield": float(max_shd_m.group(1)) if max_shd_m else 0.0
        }

    all_fleets = {}
    for f_id, (f_blk, f_ship_ids) in raw_player_fleets.items():
        ship_ids = [sid for sid in f_ship_ids if sid in ships]
        if not ship_ids:
            continue

        f_pow = re.search(r'military_power\s*=\s*([+-]?[\d\.\-]+)', f_blk)
        sys_name = "Unknown System"
        coord_m = re.search(r'\bcoordinate\s*=\s*\{', f_blk)
        if coord_m:
            coord_blk = extract_block(f_blk, coord_m.end() - 1)
            orig_m = re.search(r'\borigin\s*=\s*(\d+)', coord_blk)
            if orig_m and orig_m.group(1) in system_names:
                sys_name = system_names[orig_m.group(1)]

        tot_hp = sum(ships[sid]["hp"] for sid in ship_ids)
        tot_max_hp = sum(ships[sid]["max_hp"] for sid in ship_ids)
        tot_arm = sum(ships[sid]["armor"] for sid in ship_ids)
        tot_max_arm = sum(ships[sid]["max_armor"] for sid in ship_ids)
        tot_shd = sum(ships[sid]["shield"] for sid in ship_ids)
        tot_max_shd = sum(ships[sid]["max_shield"] for sid in ship_ids)

        design_groups = {}
        for sid in ship_ids:
            s_obj = ships[sid]
            did = s_obj["design_id"]
            if did not in design_groups:
                d_meta = designs[did]
                design_groups[did] = {
                    "count": 0,
                    "size": d_meta["size"],
                    "design": d_meta["name"],
                    "sections": d_meta["sections"],
                    "auxiliaries": d_meta["auxiliaries"],
                    "armor": d_meta["armor"],
                    "shields": d_meta["shields"],
                    "required": d_meta["required"],
                    "ship_names": []
                }
            design_groups[did]["count"] += 1
            if len(design_groups[did]["ship_names"]) < 50:
                design_groups[did]["ship_names"].append(s_obj["ship_name"])

        all_fleets[f_id] = {
            "name": resolve_saved_name(f_blk, loc_dict, f"Fleet {f_id}"),
            "system": sys_name,
            "power": float(f_pow.group(1)) if f_pow else 0.0,
            "ship_count": len(ship_ids),
            "condition": {
                "hull_pct": round(tot_hp / tot_max_hp * 100) if tot_max_hp > 0 else 100,
                "armor_pct": round(tot_arm / tot_max_arm * 100) if tot_max_arm > 0 else 100,
                "shield_pct": round(tot_shd / tot_max_shd * 100) if tot_max_shd > 0 else 100
            },
            "templates": list(design_groups.values())
        }

    # 7. Assemble Player Stats & Progression Snapshots
    player_stats = {}
    player_progression = {}
    empire_names = {}

    for pid, p_data in player_country_blocks.items():
        if index == total_files - 1:
            empire_names[pid] = resolve_saved_name(p_data, loc_dict, None)

        eco = re.search(r'\beconomy_power\s*=\s*"?([+-]?[\d\.\-]+)"?', p_data)
        tech = re.search(r'\btech_power\s*=\s*"?([+-]?[\d\.\-]+)"?', p_data)
        planets_match = re.search(r'\bowned_planets\s*=\s*\{\s*([^{}]*)\s*\}', p_data)

        prog = extract_empire_progression(p_data)
        player_progression[pid] = prog

        origin_clean = clean_loc(prog["origin"], loc_dict) if prog["origin"] else "None"
        civics_clean = [clean_loc(c, loc_dict) for c in prog["civics"]]
        gov_clean = clean_loc(prog["gov_type"], loc_dict) if prog["gov_type"] else ""
        auth_clean = clean_loc(prog["authority"], loc_dict) if prog["authority"] else ""
        active_agenda_clean = clean_loc(prog["council_agenda"], loc_dict) if prog["council_agenda"] else "None"

        traits = []
        sp_id_m = re.search(r'founder_species_ref\s*=\s*(\d+)', p_data)
        if sp_id_m and sp_id_m.group(1) in species_dict:
            traits = species_dict[sp_id_m.group(1)]

        resources = extract_country_resources(p_data, RES_KEYS)

        owned_fleets_data = []
        total_fleet_power = 0.0
        for fid in player_fleet_lists.get(pid, []):
            if fid in all_fleets:
                owned_fleets_data.append(all_fleets[fid])
                total_fleet_power += all_fleets[fid]["power"]

        player_stats[pid] = {
            "date": date,
            "economy_power": float(eco.group(1)) if eco else 0.0,
            "tech_power": float(tech.group(1)) if tech else 0.0,
            "military_power": total_fleet_power,
            "owned_planets": len(re.findall(r'\d+', planets_match.group(1))) if planets_match else 0,
            "government": gov_clean,
            "authority": auth_clean,
            "origin": origin_clean,
            "civics": civics_clean,
            "active_agenda": active_agenda_clean,
            "agenda_progress": prog["council_agenda_progress"],
            "traits": traits,
            "resources": resources,
            "fleets": owned_fleets_data
        }

    # 8. Extract Planets & Raw Queue Items
    colony_map = {}
    new_owners = {}
    planet_names = {}
    planets_block = get_major_block(gamestate, "planets")
    if planets_block:
        for p_id, p_block_str in get_top_level_blocks(planets_block).items():
            colony_match = re.search(r'\bcolony\s*=\s*(\d+)', p_block_str)
            owner_match = re.search(r'\bowner\s*=\s*(\d+)', p_block_str)
            if colony_match:
                colony_map[colony_match.group(1)] = p_id
            if owner_match:
                new_owners[p_id] = owner_match.group(1)

            name_val = resolve_saved_name(p_block_str, loc_dict, "Unknown Planet")
            if name_val != "Unknown Planet":
                planet_names[p_id] = name_val

    raw_queue_items = []
    for match in ITEM_PATTERN.finditer(gamestate):
        item_id, queue_id, player_id, build_item, internal_colony_id = match.groups()
        raw_queue_items.append((item_id, player_id, build_item, internal_colony_id))

    return {
        "index": index,
        "date": date,
        "player_stats": player_stats,
        "player_progression": player_progression,
        "empire_names": empire_names,
        "colony_map": colony_map,
        "new_owners": new_owners,
        "planet_names": planet_names,
        "raw_queue_items": raw_queue_items
    }


def main():
    print("--- Stellaris Timeline Extractor (Multi-Core Edition) ---")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    game_folder = get_stellaris_dir(auto_prompt=True)
    if not game_folder:
        print("\n[!] No Stellaris installation folder configured. Run setup.cmd first.")
        os.system('pause')
        return
    print(f"Using Stellaris folder: {game_folder}")

    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    root.focus_force()
    save_folder = filedialog.askdirectory(title="Select Folder with Stellaris .sav files")
    root.destroy()

    if not save_folder:
        print("\n[!] Extraction canceled. No folder selected.")
        os.system('pause')
        return

    sav_files = [os.path.join(save_folder, f) for f in os.listdir(save_folder) if f.endswith('.sav')]
    if not sav_files:
        print("\n[!] No .sav files found in the selected folder.")
        os.system('pause')
        return

    start_time = time.time()
    print(f"\nFound {len(sav_files)} save files. Processing...")
    loc_dict = load_stellaris_localization(game_folder)

    print("\n[2/4] Extracting dates and identifying campaign...")
    saves_metadata = []
    for filepath in sav_files:
        try:
            head = extract_gamestate_head(filepath, 2000)
            date_match = re.search(r'\bdate="([\d\.]+)"', head)
            name_match = re.search(r'^name="([^"]+)"', head, re.MULTILINE)
            if date_match:
                saves_metadata.append({
                    "filepath": filepath,
                    "date": date_match.group(1),
                    "name": name_match.group(1) if name_match else "Stellaris"
                })
        except Exception:
            pass

    saves_metadata.sort(key=lambda x: x['date'])
    campaign_name = re.sub(r'[\\/*?:"<>|]', "", saves_metadata[-1]['name'])

    latest_gamestate = extract_gamestate_content(saves_metadata[-1]['filepath'])
    player_map = extract_player_names(latest_gamestate)

    system_names = {}
    gal_obj_block = get_major_block(latest_gamestate, "galactic_object")
    if gal_obj_block:
        for sys_id, sys_blk in get_top_level_blocks(gal_obj_block).items():
            system_names[sys_id] = resolve_saved_name(sys_blk, loc_dict, f"System {sys_id}")

    timeline_data = {"campaign_name": campaign_name, "players": {}}
    for pid, pname in player_map.items():
        timeline_data["players"][pid] = {
            "name": pname,
            "empire_name": pname,
            "stats_history": [],
            "empire_events": [],
            "planets": {}
        }

    total_files = len(saves_metadata)
    max_workers = max(1, (os.cpu_count() or 4) - 1)
    print(f"\n[3/4] Parsing {total_files} saves in parallel across {max_workers} CPU cores...")

    tasks = [
        (idx, total_files, save['date'], save['filepath'])
        for idx, save in enumerate(saves_metadata)
    ]

    results = [None] * total_files
    completed = 0

    with ProcessPoolExecutor(
        max_workers=max_workers,
        initializer=init_worker,
        initargs=(loc_dict, list(player_map.keys()), system_names)
    ) as executor:
        futures = {executor.submit(process_single_save, t): t[0] for t in tasks}
        for future in as_completed(futures):
            res = future.result()
            results[res["index"]] = res
            completed += 1
            elapsed = time.time() - start_time
            rate = completed / elapsed if elapsed > 0 else 0
            eta = (total_files - completed) / rate if rate > 0 else 0
            print(
                f"      -> Completed {completed}/{total_files} saves | "
                f"Latest: {res['date']} | ETA: {eta:.0f}s   ",
                end='\r'
            )

    # Step 4: Chronological Reconciliation Pass
    print("\n\n[4/4] Reconciling build orders, traditions, civics, governments, and agendas...")
    build_orders = defaultdict(lambda: defaultdict(list))
    seen_construction_ids = set()
    planet_current_owners = {}
    master_colony_map = {}
    master_planet_names = {}

    # State trackers per player for Empire Events
    player_state = {}
    empire_events = defaultdict(list)

    for res in results:
        if not res:
            continue
        date = res["date"]

        for pid, stat_obj in res["player_stats"].items():
            timeline_data["players"][pid]["stats_history"].append(stat_obj)

        for pid, emp_name in res["empire_names"].items():
            if emp_name:
                timeline_data["players"][pid]["empire_name"] = emp_name

        # Reconcile Empire Progression (Traditions, Perks, Civics, Government, Agendas)
        for pid, prog in res["player_progression"].items():
            if pid not in timeline_data["players"]:
                continue

            if pid not in player_state:
                # Baseline initialization on first save the player appears
                player_state[pid] = {
                    "gov_type": prog["gov_type"],
                    "authority": prog["authority"],
                    "civics": set(prog["civics"]),
                    "traditions": set(prog["traditions"]),
                    "ascension_perks": set(prog["ascension_perks"]),
                    "council_agenda": prog["council_agenda"],
                    "launched_cooldowns": set(),
                    "finish_modifiers": set(prog["finish_modifiers"])
                }
                if prog["council_agenda"]:
                    empire_events[pid].append({
                        "date": date,
                        "type": "AGENDA_SELECTED",
                        "item": clean_loc(prog["council_agenda"], loc_dict)
                    })
                for t in prog["traditions"]:
                    empire_events[pid].append({
                        "date": date,
                        "type": "TRADITION",
                        "item": format_tradition_name(t, loc_dict)
                    })
                for ap in prog["ascension_perks"]:
                    empire_events[pid].append({
                        "date": date,
                        "type": "ASCENSION_PERK",
                        "item": clean_loc(ap, loc_dict)
                    })
                for ag, start_dt in prog["agenda_cooldowns"]:
                    if start_dt and start_dt != "1.01.01":
                        player_state[pid]["launched_cooldowns"].add((ag, start_dt))
                        empire_events[pid].append({
                            "date": start_dt,
                            "type": "AGENDA_LAUNCHED",
                            "item": clean_loc(ag, loc_dict)
                        })
            else:
                st = player_state[pid]

                # 1. Traditions unlocked
                for t in prog["traditions"]:
                    if t not in st["traditions"]:
                        st["traditions"].add(t)
                        empire_events[pid].append({
                            "date": date,
                            "type": "TRADITION",
                            "item": format_tradition_name(t, loc_dict)
                        })

                # 2. Ascension Perks unlocked
                for ap in prog["ascension_perks"]:
                    if ap not in st["ascension_perks"]:
                        st["ascension_perks"].add(ap)
                        empire_events[pid].append({
                            "date": date,
                            "type": "ASCENSION_PERK",
                            "item": clean_loc(ap, loc_dict)
                        })

                # 3. Government or Authority changed
                new_gov, new_auth = prog["gov_type"], prog["authority"]
                if (new_gov and new_gov != st["gov_type"]) or (new_auth and new_auth != st["authority"]):
                    old_desc = clean_loc(st["gov_type"] or st["authority"], loc_dict)
                    new_desc = clean_loc(new_gov or new_auth, loc_dict)
                    if new_auth != st["authority"] and new_auth:
                        new_desc = f"{new_desc} ({clean_loc(new_auth, loc_dict)})"
                    empire_events[pid].append({
                        "date": date,
                        "type": "GOV_CHANGED",
                        "item": f"{old_desc} → {new_desc}"
                    })
                    st["gov_type"] = new_gov
                    st["authority"] = new_auth

                # 4. Civics reformed (added / removed)
                curr_civics = set(prog["civics"])
                if curr_civics != st["civics"]:
                    added = [clean_loc(c, loc_dict) for c in (curr_civics - st["civics"])]
                    removed = [clean_loc(c, loc_dict) for c in (st["civics"] - curr_civics)]
                    parts = []
                    if added: parts.append(f"+{', '.join(added)}")
                    if removed: parts.append(f"-{', '.join(removed)}")
                    empire_events[pid].append({
                        "date": date,
                        "type": "CIVICS_CHANGED",
                        "item": " / ".join(parts)
                    })
                    st["civics"] = curr_civics

                # 5. Agenda Launched (via exact start_date in council_agenda_cooldowns or new _finish modifier)
                newly_launched_agendas = set()
                for ag, start_dt in prog["agenda_cooldowns"]:
                    if not start_dt or start_dt == "1.01.01":
                        continue
                    if (ag, start_dt) not in st["launched_cooldowns"]:
                        st["launched_cooldowns"].add((ag, start_dt))
                        newly_launched_agendas.add(ag)
                        empire_events[pid].append({
                            "date": start_dt,
                            "type": "AGENDA_LAUNCHED",
                            "item": clean_loc(ag, loc_dict)
                        })

                curr_mods = set(prog["finish_modifiers"])
                for mod in (curr_mods - st["finish_modifiers"]):
                    ag_base = mod[:-7] if mod.endswith("_finish") else mod
                    if ag_base not in newly_launched_agendas:
                        empire_events[pid].append({
                            "date": date,
                            "type": "AGENDA_LAUNCHED",
                            "item": clean_loc(ag_base, loc_dict)
                        })
                st["finish_modifiers"] = curr_mods

                # 6. Agenda Selected
                new_ag = prog["council_agenda"]
                if new_ag != st["council_agenda"]:
                    if new_ag:
                        empire_events[pid].append({
                            "date": date,
                            "type": "AGENDA_SELECTED",
                            "item": clean_loc(new_ag, loc_dict)
                        })
                    st["council_agenda"] = new_ag

        # Reconcile Planetary Builds
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

    for pid, planets in build_orders.items():
        if pid not in timeline_data["players"]:
            continue
        for planet_id, builds in planets.items():
            raw_planet_name = master_planet_names.get(planet_id, f"Planet ID {planet_id}")
            clean_builds = []
            for event in sorted(builds, key=lambda x: x[0]):
                date, evt_type, evt_data = event
                if evt_type == "BUILD":
                    clean_item = clean_loc(evt_data, loc_dict)
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
                "name": clean_loc(raw_planet_name, loc_dict),
                "builds": clean_builds
            }

    output_dir = os.path.join(script_dir, "output")
    os.makedirs(output_dir, exist_ok=True)
    filename = f"{campaign_name}_timeline.json"

    with open(os.path.join(output_dir, filename), "w", encoding="utf-8") as f:
        json.dump(timeline_data, f, indent=4, ensure_ascii=False)

    total_elapsed = time.time() - start_time
    print(f"\n[SUCCESS] Timeline saved to output/{filename} in {total_elapsed:.1f} seconds!")
    os.system('pause')


if __name__ == "__main__":
    main()