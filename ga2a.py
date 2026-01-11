# a2a.py
import json
from pathlib import Path
from datetime import datetime

DATA_FILE = Path("data.json")

def load_data():
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text())
    return {"entities": {}, "zones": {}, "interactions": [], "logs": []}

def save_data(data):
    DATA_FILE.write_text(json.dumps(data, indent=2))

def spawn(name):
    data = load_data()
    if name in data["entities"]:
        return False
    data["entities"][name] = {
        "name": name,
        "created_at": datetime.now().isoformat(),
        "zone": None
    }
    save_data(data)
    return True

def create_zone(name):
    data = load_data()
    if name in data["zones"]:
        return False
    data["zones"][name] = {
        "name": name,
        "created_at": datetime.now().isoformat(),
        "entities": []
    }
    save_data(data)
    return True

def interact(e1, e2, e3):
    data = load_data()
    for e in [e1, e2, e3]:
        if e not in data["entities"]:
            return False
    data["interactions"].append({
        "timestamp": datetime.now().isoformat(),
        "entities": [e1, e2, e3]
    })
    save_data(data)
    return True
