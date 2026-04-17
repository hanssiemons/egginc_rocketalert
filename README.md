# egginc_rocketalert

Monitors active rocket missions in [Egg Inc](https://egg-inc.com/) and sends a Telegram notification when a rocket lands.

Calls the Egg Inc API directly via protobuf — no browser or scraping involved.

## How it works

On each run the script:
1. Loads the saved mission state
2. Checks whether any ETAs have passed → sends a Telegram message per landed rocket
3. Skips the API call if all slots are in flight and nothing has landed yet
4. Otherwise fetches fresh mission data from the Egg Inc API and merges it into state

Intended to be run every minute via Task Scheduler or cron. The Egg Inc API is only called when needed (free slot or landed rocket) and at most once per hour.

## Setup

### 1. Install dependencies

```
pip install -r requirements.txt
```

### 2. ei_pb2.py

`ei_pb2.py` is included in this repo (generated from [carpetsage/egg](https://github.com/carpetsage/egg)'s `ei.proto`). No action needed.

If you ever need to regenerate it:

```
python -m grpc_tools.protoc -I<path_to_egg_repo>/protobuf --python_out=. ei.proto
```

### 3. Configure

Copy the sample config and fill in your details:

```
cp egginc_config.ini.sample egginc_config.ini
```

| Setting | Description |
|---------|-------------|
| `player_id` | Your Egg Inc ID (Settings → Privacy & Data in the app) |
| `max_missions` | Maximum simultaneous rocket slots (default: 3) |
| `bot_token` | Telegram bot token from [@BotFather](https://t.me/BotFather) |
| `chat_id` | Your Telegram chat ID (send a message to your bot, then check `https://api.telegram.org/bot<TOKEN>/getUpdates`) |

### 4. Schedule

**Windows Task Scheduler:**
```
python C:\sites\egginc\egginc.py
```
Trigger: every 1 minute.

**Linux/macOS cron:**
```
* * * * * /usr/bin/python3 /path/to/egginc/egginc.py
```

## Files

| File | Description |
|------|-------------|
| `egginc.py` | Main script |
| `egginc_config.ini` | Your local config (not in git) |
| `egginc_config.ini.sample` | Config template |
| `ei_pb2.py` | Generated protobuf module (included) |
| `rockets_state.json` | Persisted mission state (auto-generated, not in git) |

## Credits

- [carpetsage/egg](https://github.com/carpetsage/egg) — source of the `ei.proto` protobuf definitions and API documentation used in this project.
