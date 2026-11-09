"""Local JSON storage and startup backup rotation for z-note."""
import json
import os
from datetime import datetime
import shutil


DATA_DIR = os.path.join(os.environ.get("APPDATA", "."), "z-note")
DATA_FILE = os.path.join(DATA_DIR, "notes.json")
BACKUP_DIR = os.path.join(DATA_DIR, "backup")

DEFAULT_SETTINGS = {"autostart": False, "showOnStartup": True,
                    "opacity": 1.0, "color": "白", "fontSize": 11,
                    "fontFamily": "classic", "sideDock": True, "sideDockY": 0.5}

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


def backup_before_update():
    """Strict, independent backups are never pruned by startup rotation."""
    folder = os.path.join(DATA_DIR, "update-backups")
    os.makedirs(folder, exist_ok=True)
    # Windows 时钟粒度可达 15.6ms，快速连续备份会出现相同时间戳，须避免互相覆盖。
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    target = os.path.join(folder, f"notes-before-update-{stamp}.json")
    serial = 1
    while os.path.lexists(target):
        serial += 1
        target = os.path.join(folder, f"notes-before-update-{stamp}-{serial}.json")
    temporary = target + ".tmp"
    try:
        with open(DATA_FILE, "rb") as source, open(temporary, "xb") as output:
            shutil.copyfileobj(source, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)
    return target

