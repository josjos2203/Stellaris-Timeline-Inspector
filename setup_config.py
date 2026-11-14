import os
import json
import tkinter as tk
from tkinter import filedialog, messagebox

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")

def is_valid_stellaris_dir(path):
    """Checks if the selected folder actually contains Stellaris game assets."""
    if not path or not os.path.isdir(path):
        return False
    has_gfx = os.path.isdir(os.path.join(path, "gfx"))
    has_loc = os.path.isdir(os.path.join(path, "localisation"))
    return has_gfx and has_loc

def load_saved_path():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("stellaris_dir", "")
        except Exception:
            pass
    return ""

def prompt_and_save_stellaris_dir():
    current_path = load_saved_path()
    print("--- Stellaris Folder Setup ---")
    if current_path:
        print(f"Current configured path: {current_path}")
    else:
        print("No Stellaris installation path configured yet.")

    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    root.focus_force()

    initial_dir = current_path if os.path.isdir(current_path) else None
    selected_dir = filedialog.askdirectory(
        title="Select your Stellaris Installation Folder (containing gfx and localisation)",
        initialdir=initial_dir
    )

    if not selected_dir:
        root.destroy()
        print("\n[!] Setup canceled. Path was not changed.")
        return None

    selected_dir = os.path.normpath(selected_dir)

    if not is_valid_stellaris_dir(selected_dir):
        messagebox.showwarning(
            "Missing Game Folders",
            f"The selected folder does not appear to contain 'gfx' and 'localisation' folders:\n\n{selected_dir}\n\nSaving it anyway, but icons or text localization may be missing.",
            parent=root
        )

    root.destroy()

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"stellaris_dir": selected_dir}, f, indent=4)

    print(f"\n[SUCCESS] Stellaris path saved to config.json:\n          {selected_dir}")
    return selected_dir

def get_stellaris_dir(auto_prompt=True):
    """Used by server.py and build_order_extractor.py to resolve the Stellaris path."""
    saved = load_saved_path()
    if is_valid_stellaris_dir(saved):
        return saved

    # Fallback: check if the folder is still sitting inside the Stellaris directory
    parent_dir = os.path.dirname(SCRIPT_DIR)
    if is_valid_stellaris_dir(parent_dir):
        return parent_dir

    if auto_prompt:
        print("[!] Valid Stellaris folder not found in config.json. Launching folder selector...")
        return prompt_and_save_stellaris_dir()

    return saved

if __name__ == "__main__":
    prompt_and_save_stellaris_dir()
    os.system("pause")