"""Local JSON storage and startup backup rotation for z-note."""
import json
import os


DATA_DIR = os.path.join(os.environ.get("APPDATA", "."), "z-note")
DATA_FILE = os.path.join(DATA_DIR, "notes.json")
BACKUP_DIR = os.path.join(DATA_DIR, "backup")

DEFAULT_SETTINGS = {"autostart": False, "showOnStartup": True,
                    "opacity": 1.0, "color": "白", "fontSize": 11}

def load_data():
    if not os.path.exists(DATA_FILE):
        return {"schemaVersion": 2, "settings": dict(DEFAULT_SETTINGS), "notes": []}
    _rotate_backups()
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"schemaVersion": 2, "settings": dict(DEFAULT_SETTINGS), "notes": []}

def _rotate_backups():
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        import time
        stamp = time.strftime("%Y%m%d-%H%M%S")
        with open(DATA_FILE, "r", encoding="utf-8") as f, \
             open(os.path.join(BACKUP_DIR, f"notes.{stamp}.json"), "w", encoding="utf-8") as g:
            g.write(f.read())
        backups = sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith("notes."))
        for old in backups[:-5]:
            os.remove(os.path.join(BACKUP_DIR, old))
    except Exception:
        pass

def save_data(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, DATA_FILE)

