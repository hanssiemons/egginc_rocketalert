#!/usr/bin/env python3
import sys
import base64
import configparser
import requests
from datetime import datetime, timedelta
from pathlib import Path
import json
import ei_pb2

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "egginc_config.ini"
STATE_FILE = BASE_DIR / "rockets_state.json"

API_ROOT = "https://www.auxbrain.com"
CLIENT_VERSION = 70
PLATFORM_STRING = "IOS"
API_INTERVAL_HOURS = 1

HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 13; Pixel)",
}

SHIP_NAMES = {
    "CHICKEN_ONE": "Chicken One",
    "CHICKEN_NINE": "Chicken Nine",
    "CHICKEN_HEAVY": "Chicken Heavy",
    "BCR": "BCR",
    "MILLENIUM_CHICKEN": "Millenium Chicken",
    "CORELLIHEN_CORVETTE": "Corellihen Corvette",
    "GALEGGTICA": "Galeggtica",
    "CHICKFIANT": "Chickfiant",
    "VOYEGGER": "Voyegger",
    "HENERPRISE": "Henerprise",
    "ATREGGIES": "Atreggies",
}


def load_config():
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG_FILE)
    return cfg


def send_telegram(cfg, message):
    token = cfg.get("telegram", "bot_token", fallback="").strip()
    chat_id = cfg.get("telegram", "chat_id", fallback="").strip()
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        requests.post(url, data={"chat_id": chat_id, "text": message}, timeout=10)
    except Exception as e:
        print(f"[WARN] Telegram failed: {e}", file=sys.stderr)


def fetch_current_missions(player_id):
    rinfo = ei_pb2.BasicRequestInfo()
    rinfo.ei_user_id = ""
    rinfo.client_version = CLIENT_VERSION
    rinfo.platform = PLATFORM_STRING

    req = ei_pb2.EggIncFirstContactRequest()
    req.rinfo.CopyFrom(rinfo)
    req.ei_user_id = player_id
    req.device_id = "egginc_py"
    req.client_version = CLIENT_VERSION
    req.platform = ei_pb2.Platform.Value("IOS")

    payload = base64.b64encode(req.SerializeToString()).decode("ascii")
    resp = requests.post(API_ROOT + "/ei/bot_first_contact", data={"data": payload}, headers=HEADERS, timeout=30)
    resp.raise_for_status()

    raw = base64.b64decode(resp.text.replace(" ", "+"))
    contact = ei_pb2.EggIncFirstContactResponse()
    contact.ParseFromString(raw)

    if contact.error_code != 0:
        print(f"[ERROR] API error {contact.error_code}: {contact.error_message}", file=sys.stderr)
        return []

    if not contact.HasField("backup") or not contact.backup.HasField("artifacts_db"):
        print("[ERROR] No artifacts_db in response", file=sys.stderr)
        return []

    now = datetime.now()
    missions = []
    for m in contact.backup.artifacts_db.mission_infos:
        if m.status != ei_pb2.MissionInfo.Status.Value("EXPLORING"):
            continue
        eta = now + timedelta(seconds=m.seconds_remaining)
        missions.append({
            "ship": ei_pb2.MissionInfo.Spaceship.Name(m.ship),
            "identifier": m.identifier,
            "seconds_remaining": m.seconds_remaining,
            "eta": eta.isoformat(),
            "reported": False,
        })
    return missions


def load_state():
    if not STATE_FILE.exists():
        return {"missions": [], "last_api_call": None}
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {"missions": [], "last_api_call": None}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def main():
    cfg = load_config()
    player_id = cfg.get("egginc", "player_id").strip()
    max_missions = cfg.getint("egginc", "max_missions", fallback=3)

    now = datetime.now()
    state = load_state()
    old_missions = state.get("missions", [])
    new_state_missions = []
    landed = []

    # 1. Check which old missions have landed (ETA passed)
    for m in old_missions:
        eta = datetime.fromisoformat(m["eta"])
        if eta <= now and not m.get("reported", False):
            landed.append(m)
            m["reported"] = True
            continue
        if eta > now:
            new_state_missions.append(m)

    # 2. Decide whether to call the API
    last_api_call = state.get("last_api_call")
    api_cooled_down = (
        last_api_call is None
        or datetime.fromisoformat(last_api_call) < now - timedelta(hours=API_INTERVAL_HOURS)
    )
    slots_free = len(new_state_missions) < max_missions
    need_api = (slots_free or bool(landed)) and api_cooled_down

    if need_api:
        print("[INFO] Fetching missions from API...", file=sys.stderr)
        fresh = fetch_current_missions(player_id)
        print(f"[INFO] {len(fresh)} EXPLORING mission(s) found", file=sys.stderr)
        state["last_api_call"] = now.isoformat()

        existing_ids = {m.get("identifier") for m in new_state_missions if m.get("identifier")}
        for m in fresh:
            if m["identifier"] not in existing_ids:
                new_state_missions.append(m)
            else:
                for old in new_state_missions:
                    if old.get("identifier") == m["identifier"]:
                        old["seconds_remaining"] = m["seconds_remaining"]
                        old["eta"] = m["eta"]
    else:
        if new_state_missions:
            next_eta = min(datetime.fromisoformat(m["eta"]) for m in new_state_missions)
            print(f"[INFO] Skipping API. Next landing: {next_eta.strftime('%Y-%m-%d %H:%M')}", file=sys.stderr)
        else:
            print("[INFO] Skipping API (no missions tracked)", file=sys.stderr)

    # 3. Notify for landed missions
    for m in landed:
        ship = SHIP_NAMES.get(m.get("ship", ""), m.get("ship", "?"))
        eta = datetime.fromisoformat(m["eta"])
        msg = f"Raket geland: {ship} ({eta.strftime('%H:%M')})"
        print(msg)
        send_telegram(cfg, msg)

    state["missions"] = new_state_missions
    save_state(state)


if __name__ == "__main__":
    main()
