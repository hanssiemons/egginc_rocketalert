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

API_ROOT = "https://www.auxbrain.com"
CLIENT_VERSION = 70
PLATFORM_STRING = "IOS"
API_INTERVAL_HOURS = 1
REPORTED_ID_TTL_HOURS = 48

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


def get_accounts(cfg):
    accounts = []
    for section in cfg.sections():
        if section.startswith("account:"):
            name = section[8:].strip()
            accounts.append({
                "name": name,
                "player_id": cfg.get(section, "player_id").strip(),
                "max_missions": cfg.getint(section, "max_missions", fallback=3),
            })
    if not accounts and cfg.has_section("egginc"):
        accounts.append({
            "name": None,
            "player_id": cfg.get("egginc", "player_id").strip(),
            "max_missions": cfg.getint("egginc", "max_missions", fallback=3),
        })
    return accounts


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

    player_name = contact.backup.user_name or None

    missions = []
    for m in contact.backup.artifacts_db.mission_infos:
        if m.status != ei_pb2.MissionInfo.Status.Value("EXPLORING"):
            continue
        eta = datetime.fromtimestamp(m.start_time_derived + m.duration_seconds)
        missions.append({
            "ship": ei_pb2.MissionInfo.Spaceship.Name(m.ship),
            "identifier": m.identifier,
            "duration_seconds": m.duration_seconds,
            "eta": eta.isoformat(),
            "reported": False,
        })
    return player_name, missions


def state_file(player_id):
    return BASE_DIR / f"rockets_state_{player_id}.json"


def load_state(player_id):
    f = state_file(player_id)
    if not f.exists():
        # migrate from legacy single-account state file
        legacy = BASE_DIR / "rockets_state.json"
        if legacy.exists():
            try:
                return json.loads(legacy.read_text())
            except Exception:
                pass
        return {"missions": [], "last_api_call": None}
    try:
        return json.loads(f.read_text())
    except Exception:
        return {"missions": [], "last_api_call": None}


def save_state(player_id, state):
    state_file(player_id).write_text(json.dumps(state, indent=2))


def run_account(cfg, account, now):
    player_id    = account["player_id"]
    max_missions = account["max_missions"]
    name         = account["name"]  # may be overridden by API response or stored state
    label        = f"[{name or player_id}] "

    def notify(msg, resolved_name=None):
        display = resolved_name or name
        full = f"{display}: {msg}" if display else msg
        print(full)
        send_telegram(cfg, full)

    state = load_state(player_id)
    if state.get("player_name"):
        name = state["player_name"]
    old_missions = state.get("missions", [])
    new_state_missions = []
    landed = []

    # Prune reported_ids older than TTL so the dict doesn't grow forever
    reported_ids = state.get("reported_ids", {})
    cutoff = now - timedelta(hours=REPORTED_ID_TTL_HOURS)
    reported_ids = {k: v for k, v in reported_ids.items() if datetime.fromisoformat(v) > cutoff}

    # 1. Check which old missions have landed (ETA passed)
    for m in old_missions:
        eta = datetime.fromisoformat(m["eta"])
        if eta <= now and not m.get("reported", False):
            landed.append(m)
            m["reported"] = True
            if m.get("identifier"):
                reported_ids[m["identifier"]] = now.isoformat()
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
        print(f"[INFO] {label}Fetching missions from API...", file=sys.stderr)
        api_name, fresh = fetch_current_missions(player_id)
        if api_name:
            name = api_name
            state["player_name"] = api_name
        print(f"[INFO] {label}{len(fresh)} EXPLORING mission(s) found (player: {name or player_id})", file=sys.stderr)
        state["last_api_call"] = now.isoformat()

        existing_ids = {m.get("identifier") for m in new_state_missions if m.get("identifier")}
        existing_ids |= {m.get("identifier") for m in landed if m.get("identifier")}
        existing_ids |= set(reported_ids.keys())
        for m in fresh:
            if m["identifier"] not in existing_ids:
                print(f"[INFO] {label}New mission: {m['ship']} — ETA {m['eta']}", file=sys.stderr)
                new_state_missions.append(m)
            else:
                for old in new_state_missions:
                    if old.get("identifier") == m["identifier"]:
                        old["eta"] = m["eta"]
    else:
        if new_state_missions:
            next_eta = min(datetime.fromisoformat(m["eta"]) for m in new_state_missions)
            print(f"[INFO] {label}Skipping API. Next landing: {next_eta.strftime('%Y-%m-%d %H:%M')}", file=sys.stderr)
        else:
            print(f"[INFO] {label}Skipping API (no missions tracked)", file=sys.stderr)

    # 3. Remind if not all slots are in use (only on API runs, and not right after a landing)
    if need_api and not landed and len(new_state_missions) < max_missions:
        flying = len(new_state_missions)
        notify(f"Not all rockets are flying: {flying}/{max_missions} active", name)

    # 4. Notify for landed missions
    for m in landed:
        ship = SHIP_NAMES.get(m.get("ship", ""), m.get("ship", "?"))
        notify(f"Rocket landed: {ship}", name)

    state["reported_ids"] = reported_ids
    state["missions"] = new_state_missions
    save_state(player_id, state)


def main():
    cfg      = load_config()
    accounts = get_accounts(cfg)
    now      = datetime.now()
    print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] Run start ({len(accounts)} account(s))", file=sys.stderr)

    for account in accounts:
        run_account(cfg, account, now)


if __name__ == "__main__":
    main()
