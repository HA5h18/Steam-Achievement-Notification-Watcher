import os
import sys
import json
import time
import requests
import threading
import queue
import sqlite3
import platform
import tkinter as tk
from tkinter import font as tkfont
from tkinter import scrolledtext, filedialog, messagebox
from tkinter import ttk

try:
    from PIL import Image, ImageTk
    import io
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

# ==============================================================================
# CONFIG & CACHE PATHS — portable, next to executable
# ==============================================================================
def get_app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))

APP_DIR = get_app_dir()
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
CACHE_PATH = os.path.join(APP_DIR, "achievements_cache.json")
IMAGE_CACHE_PATH = os.path.join(APP_DIR, "image_cache.db")
VIEWER_REFRESH_CALLBACK = None

# ==============================================================================
# DEFAULTS
# ==============================================================================
DEFAULT_CONFIG = {
    "api_key": "",
    "steam_id": "",
    "check_interval": 5,
    "sound_filename": "unlock.mp3",
    "discord_webhook_url": "",
    "paused": False,
    "toast": {
        "width": 360,
        "height": 90,
        "x_offset": 20,
        "y_offset": 50,
        "duration_ms": 10000,
        "bg_color": "#1b2838",
        "border_color": "#3d4450",
        "title_color": "#66c0f4",
        "game_color": "#ffffff",
        "ach_color": "#9099a1",
        "icon_bg": "#2a475e",
        "ach_title": "🏆 Achievement Unlocked!",
        "test_title": "🚀 Notifier Test Mode",
        "font_family": "Helvetica",
        "title_size": 10,
        "game_size": 12,
        "ach_size": 11,
        "icon_size": 64,
        "stack_spacing": 100
    },
    "discord": {
        "enabled": True,
        "title_template": "🏆 {title}",
        "game_field_name": "🎮 Game Title",
        "ach_field_name": "🎖️ Unlocked Achievement",
        "footer_text": "Steam Web API Real-Time Notifier Framework",
        "standard_color": "6734068",
        "test_color": "15548997"
    }
}

CONFIG = {}
toast_queue = queue.Queue()
log_queue = queue.Queue()
active_toasts = []
LAST_UNLOCKED = set()
IS_BASELINE_COMPLETE = False
PAUSED = False


def load_config():
    global CONFIG
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                loaded = json.load(f)
            CONFIG = json.loads(json.dumps(DEFAULT_CONFIG))
            for k, v in loaded.items():
                if isinstance(v, dict) and k in CONFIG and isinstance(CONFIG[k], dict):
                    CONFIG[k].update(v)
                else:
                    CONFIG[k] = v
            return
        except Exception as e:
            print(f"⚠️ Corrupt config, resetting: {e}")
    CONFIG = json.loads(json.dumps(DEFAULT_CONFIG))


def save_config():
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(CONFIG, f, indent=2)
    except Exception as e:
        log(f"❌ Failed to save config: {e}")


def load_cache():
    if not os.path.exists(CACHE_PATH):
        return None
    try:
        with open(CACHE_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return None


def save_cache(data):
    try:
        with open(CACHE_PATH, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        log(f"❌ Cache save failed: {e}")


def init_image_cache():
    try:
        conn = sqlite3.connect(IMAGE_CACHE_PATH)
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS images (
            url TEXT PRIMARY KEY,
            data BLOB,
            timestamp INTEGER
        )""")
        conn.commit()
        conn.close()
    except Exception as e:
        log(f"❌ Failed to init image cache: {e}")


def get_cached_image(url):
    try:
        conn = sqlite3.connect(IMAGE_CACHE_PATH)
        c = conn.cursor()
        c.execute("SELECT data FROM images WHERE url = ?", (url,))
        row = c.fetchone()
        conn.close()
        if row:
            return row[0]
    except Exception:
        pass
    return None


def save_image_to_cache(url, data):
    try:
        conn = sqlite3.connect(IMAGE_CACHE_PATH)
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO images (url, data, timestamp) VALUES (?, ?, ?)",
                  (url, data, int(time.time())))
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


def log(msg):
    timestamp = time.strftime("%H:%M:%S")
    log_queue.put(f"[{timestamp}] {msg}")
    print(msg)


def get_recent_games():
    url = "https://api.steampowered.com/IPlayerService/GetRecentlyPlayedGames/v1/"
    params = {"key": CONFIG["api_key"], "steamid": CONFIG["steam_id"], "format": "json"}
    try:
        response = requests.get(url, params=params, timeout=10).json()
        games = [g["appid"] for g in response.get("response", {}).get("games", [])]
        log(f"Recent games: {len(games)} found")
        return games
    except Exception as e:
        log(f"❌ Failed to get recent games: {e}")
        return []


def get_all_owned_games():
    url = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
    params = {
        "key": CONFIG["api_key"],
        "steamid": CONFIG["steam_id"],
        "format": "json",
        "include_appinfo": "1",
        "include_played_free_games": "1"
    }
    try:
        response = requests.get(url, params=params, timeout=10).json()
        games = [g["appid"] for g in response.get("response", {}).get("games", [])]
        log(f"Owned games: {len(games)} found")
        return games
    except Exception as e:
        log(f"❌ Failed to get owned games: {e}")
        return []


def get_achievement_icon(app_id, api_name):
    url = "https://api.steampowered.com/ISteamUserStats/GetSchemaForGame/v2/"
    params = {"key": CONFIG["api_key"], "appid": app_id, "format": "json"}
    try:
        res = requests.get(url, params=params, timeout=10).json()
        achievements = res.get("game", {}).get("availableGameStats", {}).get("achievements", [])
        for ach in achievements:
            if str(ach.get("name")).strip().lower() == str(api_name).strip().lower():
                return ach.get("icon")
    except Exception:
        pass
    return None


def send_discord_webhook(title, subtitle, display_name, icon_url):
    if not CONFIG["discord"]["enabled"]:
        return
    if not icon_url:
        icon_url = "https://steamstatic.com"

    d = CONFIG["discord"]
    try:
        standard_color = int(d["standard_color"])
        test_color = int(d["test_color"])
    except ValueError:
        standard_color = 6734068
        test_color = 15548997

    embed_color = test_color if "Test" in title or "Backup" in title else standard_color
    title_text = d["title_template"].format(title=title)

    payload = {
        "embeds": [{
            "title": title_text,
            "color": embed_color,
            "thumbnail": {"url": icon_url},
            "fields": [
                {
                    "name": d["game_field_name"],
                    "value": subtitle or "Unknown",
                    "inline": True
                },
                {
                    "name": d["ach_field_name"],
                    "value": display_name or "Hidden",
                    "inline": True
                }
            ],
            "footer": {
                "text": d["footer_text"],
                "icon_url": "https://steamstatic.com"
            },
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }]
    }
    try:
        requests.post(CONFIG["discord_webhook_url"], json=payload, timeout=10)
    except Exception:
        pass


def show_toast(title, subtitle, display_name, icon_url=None):
    toast_queue.put((title, subtitle, display_name, icon_url))


def create_toast_window(root, title, subtitle, display_name, icon_url):
    t = CONFIG["toast"]
    sound_path = get_resource_path(CONFIG["sound_filename"])
    if os.path.exists(sound_path):
        os.system(f"afplay '{sound_path}' &")

    threading.Thread(target=send_discord_webhook,
                     args=(title, subtitle, display_name, icon_url),
                     daemon=True).start()

    toast = tk.Toplevel(root)
    toast.overrideredirect(True)
    toast.configure(bg=t["bg_color"])
    toast.attributes("-topmost", True)

    screen_width = toast.winfo_screenwidth()
    x = screen_width - t["width"] - t["x_offset"]
    y = t["y_offset"] + len(active_toasts) * t["stack_spacing"]
    toast.geometry(f"{t['width']}x{t['height']}+{x}+{y}")

    frame = tk.Frame(toast, bg=t["bg_color"], padx=12, pady=12)
    frame.pack(fill="both", expand=True)

    icon_label = None
    icon_size = t["icon_size"]
    if HAS_PILLOW and icon_url:
        try:
            data = get_cached_image(icon_url)
            if not data:
                response = requests.get(icon_url, timeout=5)
                data = response.content
                save_image_to_cache(icon_url, data)
            img = Image.open(io.BytesIO(data))
            img = img.resize((icon_size, icon_size), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            icon_label = tk.Label(frame, image=photo, bg=t["bg_color"])
            icon_label.image = photo
            icon_label.pack(side="left", padx=(0, 12))
        except Exception:
            pass

    if icon_label is None:
        placeholder = tk.Frame(frame, width=icon_size, height=icon_size, bg=t["icon_bg"])
        placeholder.pack(side="left", padx=(0, 12))
        placeholder.pack_propagate(False)

    text_frame = tk.Frame(frame, bg=t["bg_color"])
    text_frame.pack(side="left", fill="both", expand=True)

    ff = t["font_family"]
    title_font = tkfont.Font(family=ff, size=t["title_size"], weight="bold")
    body_font = tkfont.Font(family=ff, size=t["game_size"], weight="bold")
    sub_font = tkfont.Font(family=ff, size=t["ach_size"])

    tk.Label(text_frame, text=title, font=title_font,
             bg=t["bg_color"], fg=t["title_color"], anchor="w").pack(fill="x")
    tk.Label(text_frame, text=subtitle, font=body_font,
             bg=t["bg_color"], fg=t["game_color"], anchor="w").pack(fill="x")
    tk.Label(text_frame, text=display_name, font=sub_font,
             bg=t["bg_color"], fg=t["ach_color"], anchor="w").pack(fill="x")

    def remove_and_destroy():
        if toast in active_toasts:
            active_toasts.remove(toast)
        try:
            toast.destroy()
        except Exception:
            pass

    toast.after(t["duration_ms"], remove_and_destroy)
    active_toasts.append(toast)


def process_queues(root, log_widget):
    try:
        while True:
            title, subtitle, display_name, icon_url = toast_queue.get_nowait()
            create_toast_window(root, title, subtitle, display_name, icon_url)
    except queue.Empty:
        pass

    try:
        while True:
            msg = log_queue.get_nowait()
            log_widget.insert(tk.END, msg + "\n")
            log_widget.see(tk.END)
    except queue.Empty:
        pass

    root.after(100, process_queues, root, log_widget)


def check_achievements(app_id):
    global IS_BASELINE_COMPLETE
    url = "https://api.steampowered.com/ISteamUserStats/GetPlayerAchievements/v1/"
    params = {
        "key": CONFIG["api_key"],
        "steamid": CONFIG["steam_id"],
        "appid": app_id,
        "l": "english",
        "format": "json"
    }

    unlocked_list = []
    try:
        res = requests.get(url, params=params, timeout=10).json()
        player_stats = res.get("playerstats", {})
        if not player_stats.get("success", False):
            log(f"  App {app_id}: no achievement data")
            return []

        game_name = player_stats.get("gameName", f"App {app_id}")
        achievements = player_stats.get("achievements", [])
        log(f"  Checking {game_name} ({app_id}): {len(achievements)} achievements")

        new_unlocks = 0
        for ach in achievements:
            ach_id = f"{app_id}_{ach['apiname']}"
            if ach["achieved"] == 1:
                clean_name = ach.get('name', ach['apiname'])
                unlocked_list.append({
                    "app_id": app_id,
                    "api_name": ach['apiname'],
                    "display_name": clean_name,
                    "game_name": game_name,
                    "unlock_time": int(ach.get("unlocktime", 0))
                })

                if IS_BASELINE_COMPLETE and ach_id not in LAST_UNLOCKED:
                    icon_url = get_achievement_icon(app_id, ach['apiname'])
                    log(f"  🏆 NEW: {clean_name} in {game_name}")
                    show_toast(CONFIG["toast"]["ach_title"], game_name, clean_name, icon_url)
                    new_unlocks += 1
                    # Trigger viewer refresh
                    if VIEWER_REFRESH_CALLBACK:
                        VIEWER_REFRESH_CALLBACK()

                LAST_UNLOCKED.add(ach_id)

        if new_unlocks == 0 and IS_BASELINE_COMPLETE:
            log(f"  No new unlocks in {game_name}")
        return unlocked_list
    except Exception as e:
        log(f"  ❌ Error checking app {app_id}: {e}")
        return []


def watcher_loop():
    global IS_BASELINE_COMPLETE
    log("🚀 Steam Notifier Started...")

    apps_to_check = get_recent_games()
    if not apps_to_check:
        log("No recent games, falling back to owned games...")
        apps_to_check = get_all_owned_games()

    log(f"Checking {len(apps_to_check)} apps for achievements...")

    all_unlocked = []
    for app_id in apps_to_check:
        found = check_achievements(app_id)
        if found:
            all_unlocked.extend(found)

    IS_BASELINE_COMPLETE = True
    log("✅ Baseline set. Monitoring actively...")

    if all_unlocked:
        all_unlocked.sort(key=lambda x: x["unlock_time"])
        latest = all_unlocked[-1]
        log(f"🎉 Last unlock: {latest['display_name']} ({latest['game_name']})")
        icon_url = get_achievement_icon(latest['app_id'], latest['api_name'])
        show_toast(CONFIG["toast"]["test_title"], latest['game_name'],
                   f"Last Unlocked: {latest['display_name']}", icon_url)
    else:
        log("❌ No history found. Showing backup test toast...")
        show_toast(CONFIG["toast"]["test_title"], "Team Fortress 2",
                   "Last Unlocked: Head of the Class", "https://steamstatic.com")

    while True:
        global PAUSED
        if PAUSED:
            time.sleep(0.5)
            continue

        time.sleep(CONFIG["check_interval"])
        if PAUSED:
            continue

        log("--- Polling cycle ---")
        recent = get_recent_games()
        if not recent:
            recent = apps_to_check
        for app_id in recent:
            if PAUSED:
                break
            check_achievements(app_id)


# ==============================================================================
# ACHIEVEMENT VIEWER
# ==============================================================================
def open_achievements_viewer(root):
    global VIEWER_REFRESH_CALLBACK

    win = tk.Toplevel(root)
    win.title("Achievement Viewer")
    win.configure(bg="#1b2838")
    win.geometry("800x760")
    win.minsize(640, 400)

    # Header
    header = tk.Frame(win, bg="#1b2838", padx=12, pady=12)
    header.pack(fill="x")
    tk.Label(header, text="📋 Achievement Viewer", font=("Helvetica", 16, "bold"),
             bg="#1b2838", fg="#66c0f4").pack(side="left")

    # Search + Resync bar
    control = tk.Frame(win, bg="#1b2838", padx=12, pady=8)
    control.pack(fill="x")

    tk.Label(control, text="🔍", bg="#1b2838", fg="#66c0f4",
             font=("Helvetica", 12, "bold")).pack(side="left")

    search_var = tk.StringVar()
    search_entry = tk.Entry(control, textvariable=search_var, bg="#16202d", fg="#c7d5e0",
                            insertbackground="#ffffff", font=("Helvetica", 11, "bold"),
                            relief="flat", highlightthickness=1,
                            highlightcolor="#66c0f4", highlightbackground="#3d4450")
    search_entry.pack(side="left", fill="x", expand=True, padx=(6, 0))

    resync_btn = tk.Button(control, text="🔄 Resync Library",
                           bg="#66c0f4", fg="#000000", activebackground="#4a9fd4",
                           font=("Helvetica", 10, "bold"), relief="flat", padx=12, pady=4)
    resync_btn.pack(side="right", padx=(8, 0))

    # Progress label
    progress = tk.Label(win, text="Loading...", font=("Helvetica", 11, "bold"),
                        bg="#1b2838", fg="#66c0f4")
    progress.pack(pady=4)

    # Scrollable container — tk.Text handles scrolling natively
    outer = tk.Frame(win, bg="#1b2838")
    outer.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    scrollbar = tk.Scrollbar(outer, orient="vertical")
    scrollbar.pack(side="right", fill="y")

    text_widget = tk.Text(outer, bg="#1b2838", fg="#c7d5e0",
                          font=("Helvetica", 10), wrap="word",
                          state="disabled", padx=0, pady=0,
                          highlightthickness=0, borderwidth=0,
                          yscrollcommand=scrollbar.set)
    text_widget.pack(side="left", fill="both", expand=True)
    scrollbar.config(command=text_widget.yview)

    # Mouse wheel
    def on_mousewheel(event):
        if platform.system() == "Darwin":
            text_widget.yview_scroll(int(-1 * event.delta), "units")
        else:
            text_widget.yview_scroll(int(-1 * (event.delta / 120)), "units")

    text_widget.bind("<Enter>", lambda e: text_widget.bind_all("<MouseWheel>", on_mousewheel))
    text_widget.bind("<Leave>", lambda e: text_widget.unbind_all("<MouseWheel>"))

    # Arrow key scrolling
    def scroll_up(event):
        text_widget.yview_scroll(-1, "units")
        return "break"

    def scroll_down(event):
        text_widget.yview_scroll(1, "units")
        return "break"

    def scroll_page_up(event):
        text_widget.yview_scroll(-1, "pages")
        return "break"

    def scroll_page_down(event):
        text_widget.yview_scroll(1, "pages")
        return "break"

    text_widget.bind("<Up>", scroll_up)
    text_widget.bind("<Down>", scroll_down)
    text_widget.bind("<Prior>", scroll_page_up)
    text_widget.bind("<Next>", scroll_page_down)
    text_widget.focus_set()

    # Data structures
    viewer_queue = queue.Queue()
    game_data = []          # list of game dicts
    banner_photos = {}      # {app_id: PhotoImage}
    expanded_state = {}     # {app_id: bool}
    _search_timer = [None]

    def build_game_ui(game):
        """Create a game frame and return it."""
        app_id = game["app_id"]
        game_name = game["name"]
        achievements = game["achievements"]
        unlocked = game["unlocked_count"]
        total = game["total_count"]

        frame = tk.Frame(text_widget, bg="#1b2838")

        # Banner (clickable)
        banner_label = tk.Label(frame, bg="#16202d", cursor="hand2",
                                text="Loading banner...", fg="#9099a1",
                                font=("Helvetica", 10, "bold"))
        banner_label._app_id = app_id
        banner_label.pack(fill="x")

        # Apply cached banner if already loaded
        if app_id in banner_photos:
            banner_label.config(image=banner_photos[app_id], text="", bg="#1b2838")
            banner_label.image = banner_photos[app_id]

        # Info bar
        info = tk.Frame(frame, bg="#2a475e", padx=10, pady=5)
        info.pack(fill="x")

        tk.Label(info, text=game_name, bg="#2a475e", fg="#ffffff",
                 font=("Helvetica", 12, "bold")).pack(side="left")

        pct = (unlocked / total * 100) if total > 0 else 0
        tk.Label(info, text=f"{unlocked}/{total}  ({pct:.0f}%)",
                 bg="#2a475e", fg="#66c0f4",
                 font=("Helvetica", 10, "bold")).pack(side="right")

        ind = tk.Label(info, text="▶", bg="#2a475e", fg="#66c0f4",
                       font=("Helvetica", 10, "bold"), cursor="hand2")
        ind.pack(side="right", padx=(0, 8))

        # Expandable content
        content = tk.Frame(frame, bg="#1b2838")
        populated = [False]

        def populate():
            if populated[0]:
                return
            populated[0] = True

            achievements.sort(key=lambda a: (
                0 if a.get("achieved") == 1 else 1,
                -a.get("unlocktime", 0)
            ))

            for ach in achievements:
                row = tk.Frame(content, bg="#1b2838", padx=4, pady=3)
                row.pack(fill="x")

                is_unlocked = ach.get("achieved") == 1
                status_icon = "✅" if is_unlocked else "🔒"
                name_color = "#ffffff" if is_unlocked else "#5c6370"
                desc_color = "#c7d5e0" if is_unlocked else "#3d4450"

                # Achievement icon
                icon_lbl = None
                if HAS_PILLOW:
                    icon_url = ach.get("icon") if is_unlocked else (ach.get("icongray") or ach.get("icon"))
                    if icon_url:
                        try:
                            data = get_cached_image(icon_url)
                            if not data:
                                r = requests.get(icon_url, timeout=5)
                                data = r.content
                                save_image_to_cache(icon_url, data)
                            img = Image.open(io.BytesIO(data))
                            img = img.resize((32, 32), Image.LANCZOS)
                            p = ImageTk.PhotoImage(img)
                            icon_lbl = tk.Label(row, image=p, bg="#1b2838")
                            icon_lbl.image = p
                            icon_lbl.pack(side="left", padx=(4, 10))
                        except Exception:
                            pass

                if icon_lbl is None:
                    ph = tk.Frame(row, width=32, height=32, bg="#16202d",
                                  highlightbackground="#3d4450", highlightthickness=1)
                    ph.pack(side="left", padx=(4, 10))
                    ph.pack_propagate(False)

                txt = tk.Frame(row, bg="#1b2838")
                txt.pack(side="left", fill="both", expand=True)

                unlock_str = ""
                if is_unlocked and ach.get("unlocktime", 0) > 0:
                    unlock_str = time.strftime("  •  %Y-%m-%d %H:%M", time.localtime(ach["unlocktime"]))

                tk.Label(txt, text=f"{status_icon}  {ach.get('display_name', 'Unknown')}",
                         font=("Helvetica", 10, "bold"), bg="#1b2838",
                         fg=name_color, anchor="w").pack(fill="x")

                desc = ach.get("description", "No description available.")
                tk.Label(txt, text=f"{desc}{unlock_str}",
                         font=("Helvetica", 9), bg="#1b2838",
                         fg=desc_color, anchor="w", wraplength=560,
                         justify="left").pack(fill="x")

                tk.Frame(content, height=1, bg="#3d4450").pack(fill="x", padx=4)

        def toggle():
            is_expanded = expanded_state.get(app_id, False)
            is_expanded = not is_expanded
            expanded_state[app_id] = is_expanded

            if is_expanded:
                populate()
                content.pack(fill="x", padx=8, pady=(0, 10))
                ind.config(text="▼")
                info.config(bg="#66c0f4")
                for w in info.winfo_children():
                    w.config(bg="#66c0f4", fg="#1b2838")
            else:
                content.pack_forget()
                ind.config(text="▶")
                info.config(bg="#2a475e")
                for w in info.winfo_children():
                    if isinstance(w, tk.Label) and w.cget("text") == game_name:
                        w.config(bg="#2a475e", fg="#ffffff")
                    else:
                        w.config(bg="#2a475e", fg="#66c0f4")

        banner_label.bind("<Button-1>", lambda e: toggle())
        info.bind("<Button-1>", lambda e: toggle())
        ind.bind("<Button-1>", lambda e: toggle())

        # Restore expanded state if previously expanded
        if expanded_state.get(app_id, False):
            toggle()

        return frame

    def render_games(games_list):
        """Rebuild the text widget with the given games."""
        text_widget.config(state="normal")
        # Destroy old embedded frames to prevent leaks
        for child in list(text_widget.winfo_children()):
            child.destroy()
        text_widget.delete("1.0", "end")

        for game in games_list:
            frame = build_game_ui(game)
            text_widget.window_create("end", window=frame)
            text_widget.insert("end", "\n")

        text_widget.config(state="disabled")

    def filter_games(*args):
        if _search_timer[0]:
            win.after_cancel(_search_timer[0])

        def _do():
            _search_timer[0] = None
            text = search_var.get().lower().strip()
            if not text:
                render_games(game_data)
                return
            filtered = [g for g in game_data if text in g["name"].lower()]
            render_games(filtered)

        _search_timer[0] = win.after(150, _do)

    search_var.trace_add("write", filter_games)

    def update_banner(app_id, bytes_data):
        if not HAS_PILLOW:
            return
        try:
            img = Image.open(io.BytesIO(bytes_data))
            w, h = img.size
            target_w = 520
            target_h = int(h * (target_w / w))
            img = img.resize((target_w, target_h), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            banner_photos[app_id] = photo

            # Find all banner labels with this app_id and update them
            for child in text_widget.winfo_children():
                for sub in child.winfo_children():
                    if isinstance(sub, tk.Label) and getattr(sub, "_app_id", None) == app_id:
                        sub.config(image=photo, text="", bg="#1b2838")
                        sub.image = photo
                        break
        except Exception:
            pass

    def load_games(games):
        nonlocal game_data
        game_data = games
        render_games(game_data)

        # Fetch banners in background
        threading.Thread(target=fetch_banners_background,
                         args=(games, viewer_queue), daemon=True).start()

    def fetch_banners_background(games, q):
        for g in games:
            try:
                app_id = g["app_id"]
                url = f"https://cdn.akamai.steamstatic.com/steam/apps/{app_id}/header.jpg"
                data = get_cached_image(url)
                if data:
                    q.put(("banner", app_id, data))
                    continue
                r = requests.get(url, timeout=10)
                if r.status_code == 200:
                    save_image_to_cache(url, r.content)
                    q.put(("banner", app_id, r.content))
            except Exception:
                pass
        q.put(("banners_done",))

    def full_resync():
        nonlocal game_data
        game_data = []
        expanded_state.clear()
        banner_photos.clear()

        text_widget.config(state="normal")
        for child in list(text_widget.winfo_children()):
            child.destroy()
        text_widget.delete("1.0", "end")
        text_widget.config(state="disabled")

        progress.config(text="Resyncing full library from Steam...")
        resync_btn.config(state="disabled")
        win.update_idletasks()

        def background_fetch():
            try:
                owned = get_all_owned_games()
                total = len(owned)
                games = []

                for idx, app_id in enumerate(owned, 1):
                    viewer_queue.put(("progress", idx, total))
                    try:
                        s_url = "https://api.steampowered.com/ISteamUserStats/GetSchemaForGame/v2/"
                        s_res = requests.get(s_url, params={
                            "key": CONFIG["api_key"], "appid": app_id, "format": "json"
                        }, timeout=10).json()

                        available = s_res.get("game", {}).get("availableGameStats", {})
                        schema_achs = available.get("achievements", [])
                        if not schema_achs:
                            continue

                        p_url = "https://api.steampowered.com/ISteamUserStats/GetPlayerAchievements/v1/"
                        p_res = requests.get(p_url, params={
                            "key": CONFIG["api_key"], "steamid": CONFIG["steam_id"],
                            "appid": app_id, "l": "english", "format": "json"
                        }, timeout=10).json()

                        p_stats = p_res.get("playerstats", {})
                        if not p_stats.get("success"):
                            continue

                        g_name = p_stats.get("gameName", f"App {app_id}")
                        p_achs = {a["apiname"]: a for a in p_stats.get("achievements", [])}

                        merged = []
                        for s in schema_achs:
                            name = s["name"]
                            pa = p_achs.get(name, {})
                            merged.append({
                                "display_name": s.get("displayName", name),
                                "description": s.get("description", "No description available."),
                                "icon": s.get("icon"),
                                "icongray": s.get("icongray"),
                                "achieved": pa.get("achieved", 0),
                                "unlocktime": pa.get("unlocktime", 0)
                            })

                        games.append({
                            "app_id": app_id,
                            "name": g_name,
                            "achievements": merged,
                            "unlocked_count": sum(1 for a in merged if a["achieved"] == 1),
                            "total_count": len(merged)
                        })
                    except Exception:
                        continue

                games.sort(key=lambda x: x["name"].lower())
                save_cache({"last_sync": int(time.time()), "games": games})
                viewer_queue.put(("complete", games))
            except Exception as e:
                viewer_queue.put(("error", str(e)))

        threading.Thread(target=background_fetch, daemon=True).start()

    def poll_viewer_queue():
        try:
            while True:
                msg = viewer_queue.get_nowait()
                cmd = msg[0]

                if cmd == "banner":
                    _, app_id, data = msg
                    update_banner(app_id, data)

                elif cmd == "banners_done":
                    progress.config(text="✅ Ready. Click a banner to expand.")

                elif cmd == "progress":
                    _, current, total = msg
                    progress.config(text=f"Fetching... {current}/{total} games")

                elif cmd == "complete":
                    _, games = msg
                    progress.config(text=f"✅ Synced {len(games)} games. Cached.")
                    resync_btn.config(state="normal")
                    load_games(games)

                elif cmd == "error":
                    _, err = msg
                    progress.config(text=f"❌ Error: {err}")
                    resync_btn.config(state="normal")

        except queue.Empty:
            pass
        win.after(200, poll_viewer_queue)

    def refresh_viewer():
        """Called when a new achievement is unlocked. Reloads from cache."""
        cache = load_cache()
        if cache and cache.get("games"):
            nonlocal game_data
            game_data = cache["games"]
            # Preserve expanded state, just re-render
            render_games(game_data)
            progress.config(text="🔄 Viewer refreshed — new achievement detected!")
            win.after(3000, lambda: progress.config(text="✅ Ready. Click a banner to expand."))

    # Register refresh callback
    VIEWER_REFRESH_CALLBACK = refresh_viewer

    def on_close():
        global VIEWER_REFRESH_CALLBACK
        VIEWER_REFRESH_CALLBACK = None
        win.destroy()

    win.protocol("WM_DELETE_WINDOW", on_close)

    resync_btn.config(command=full_resync)

    # Start
    cache = load_cache()
    if cache and cache.get("games"):
        progress.config(text=f"Loaded {len(cache['games'])} games from cache.")
        load_games(cache["games"])
    else:
        full_resync()

    poll_viewer_queue()
# ==============================================================================
# FIRST-RUN SETUP
# ==============================================================================
def run_first_time_setup():
    setup = tk.Tk()
    setup.title("First-Time Setup")
    setup.configure(bg="#1b2838")
    setup.geometry("420x320")
    setup.resizable(False, False)

    tk.Label(setup, text="🔑 Welcome to Steam Watcher",
             font=("Helvetica", 16, "bold"), bg="#1b2838", fg="#66c0f4").pack(pady=(16, 4))
    tk.Label(setup, text="Enter your credentials to get started.",
             font=("Helvetica", 11), bg="#1b2838", fg="#9099a1").pack(pady=(0, 12))

    fields = {}
    for label, key, show in [
        ("Steam API Key", "api_key", "*"),
        ("Steam ID", "steam_id", None),
        ("Discord Webhook URL (optional)", "discord_webhook_url", None),
    ]:
        frame = tk.Frame(setup, bg="#1b2838")
        frame.pack(fill="x", padx=20, pady=6)
        tk.Label(frame, text=label, font=("Helvetica", 10, "bold"),
                 bg="#1b2838", fg="#66c0f4", anchor="w").pack(fill="x")
        var = tk.StringVar(value=CONFIG.get(key, ""))
        ent = tk.Entry(frame, textvariable=var, bg="#16202d", fg="#c7d5e0",
                       insertbackground="#ffffff", font=("Helvetica", 11, "bold"),
                       relief="flat", highlightthickness=1,
                       highlightcolor="#66c0f4", highlightbackground="#3d4450",
                       show=show or "")
        ent.pack(fill="x", pady=(4, 0))
        fields[key] = var

    status = tk.Label(setup, text="", bg="#1b2838", fg="#ff6b6b",
                      font=("Helvetica", 10, "bold"))
    status.pack(pady=(8, 0))

    def on_save():
        api = fields["api_key"].get().strip()
        sid = fields["steam_id"].get().strip()
        hook = fields["discord_webhook_url"].get().strip()

        if not api:
            status.config(text="❌ Steam API Key is required")
            return
        if not sid:
            status.config(text="❌ Steam ID is required")
            return

        CONFIG["api_key"] = api
        CONFIG["steam_id"] = sid
        CONFIG["discord_webhook_url"] = hook
        save_config()
        setup.destroy()

    tk.Button(setup, text="💾 Save & Start", command=on_save,
              bg="#66c0f4", fg="#000000", activebackground="#4a9fd4",
              font=("Helvetica", 12, "bold"), relief="flat",
              padx=20, pady=6).pack(pady=16)

    setup.mainloop()


# ==============================================================================
# SETTINGS UI
# ==============================================================================
def open_settings(root):
    settings_win = tk.Toplevel(root)
    settings_win.title("Settings")
    settings_win.configure(bg="#1b2838")
    settings_win.geometry("620x820")
    settings_win.resizable(True, True)
    settings_win.transient(root)
    settings_win.grab_set()

    style = ttk.Style()
    style.theme_use("default")
    style.configure("TNotebook", background="#1b2838", borderwidth=0)
    style.configure("TNotebook.Tab", background="#2a475e", foreground="#ffffff",
                    padding=(10, 4), font=("Helvetica", 10, "bold"))
    style.map("TNotebook.Tab", background=[("selected", "#66c0f4")],
              foreground=[("selected", "#000000")])

    notebook = ttk.Notebook(settings_win)
    notebook.pack(fill="both", expand=True, padx=8, pady=8)

    # --- General Tab ---
    gen_frame = tk.Frame(notebook, bg="#1b2838")
    notebook.add(gen_frame, text="General")

    def make_entry(parent, row, label, key, show=None, is_file=False):
        tk.Label(parent, text=label, bg="#1b2838", fg="#66c0f4",
                 font=("Helvetica", 10, "bold"), anchor="w").grid(
            row=row, column=0, sticky="w", padx=12, pady=6)
        var = tk.StringVar(value=str(CONFIG.get(key, "")))
        ent = tk.Entry(parent, textvariable=var, bg="#16202d", fg="#c7d5e0",
                       insertbackground="#ffffff", font=("Helvetica", 10, "bold"),
                       relief="flat", highlightthickness=1,
                       highlightcolor="#66c0f4", highlightbackground="#3d4450",
                       show=show or "")
        ent.grid(row=row, column=1, sticky="ew", padx=6, pady=6)
        if is_file:
            def browse():
                path = filedialog.askopenfilename(
                    title="Select Sound File",
                    filetypes=[("Audio", "*.mp3 *.wav *.aac"), ("All Files", "*.*")]
                )
                if path:
                    var.set(path)
            tk.Button(parent, text="Browse", command=browse,
                      bg="#3d4450", fg="#000000", font=("Helvetica", 9, "bold"),
                      relief="flat", padx=6).grid(row=row, column=2, padx=6)
        return var

    gen_frame.grid_columnconfigure(1, weight=1)
    api_var = make_entry(gen_frame, 0, "Steam API Key", "api_key", show="*")
    sid_var = make_entry(gen_frame, 1, "Steam ID", "steam_id")
    int_var = make_entry(gen_frame, 2, "Check Interval (sec)", "check_interval")
    snd_var = make_entry(gen_frame, 3, "Sound File", "sound_filename", is_file=True)
    hook_var = make_entry(gen_frame, 4, "Discord Webhook", "discord_webhook_url")

    # --- Toast Tab ---
    toast_frame = tk.Frame(notebook, bg="#1b2838")
    notebook.add(toast_frame, text="Toast Style")

    t = CONFIG["toast"]
    toast_frame.grid_columnconfigure(1, weight=1)

    def make_toast_entry(row, label, key, numeric=False):
        tk.Label(toast_frame, text=label, bg="#1b2838", fg="#66c0f4",
                 font=("Helvetica", 10, "bold"), anchor="w").grid(
            row=row, column=0, sticky="w", padx=12, pady=4)
        var = tk.StringVar(value=str(t.get(key, "")))
        ent = tk.Entry(toast_frame, textvariable=var, bg="#16202d", fg="#c7d5e0",
                       insertbackground="#ffffff", font=("Helvetica", 10, "bold"),
                       relief="flat", highlightthickness=1,
                       highlightcolor="#66c0f4", highlightbackground="#3d4450")
        ent.grid(row=row, column=1, sticky="ew", padx=6, pady=4)
        return var

    tw_var = make_toast_entry(0, "Width (px)", "width", True)
    th_var = make_toast_entry(1, "Height (px)", "height", True)
    tx_var = make_toast_entry(2, "X Offset from right (px)", "x_offset", True)
    ty_var = make_toast_entry(3, "Y Offset from top (px)", "y_offset", True)
    td_var = make_toast_entry(4, "Duration (ms)", "duration_ms", True)
    sp_var = make_toast_entry(5, "Stack Spacing (px)", "stack_spacing", True)
    bg_var = make_toast_entry(6, "Background Color", "bg_color")
    bc_var = make_toast_entry(7, "Border Color", "border_color")
    tc_var = make_toast_entry(8, "Title Color", "title_color")
    gc_var = make_toast_entry(9, "Game Color", "game_color")
    ac_var = make_toast_entry(10, "Achievement Color", "ach_color")
    ic_var = make_toast_entry(11, "Icon Placeholder BG", "icon_bg")
    at_var = make_toast_entry(12, "Achievement Title Text", "ach_title")
    tt_var = make_toast_entry(13, "Test Title Text", "test_title")
    ff_var = make_toast_entry(14, "Font Family", "font_family")
    ts_var = make_toast_entry(15, "Title Font Size", "title_size", True)
    gs_var = make_toast_entry(16, "Game Font Size", "game_size", True)
    als_var = make_toast_entry(17, "Achievement Font Size", "ach_size", True)
    isz_var = make_toast_entry(18, "Icon Size (px)", "icon_size", True)

    # --- Discord Tab ---
    disc_frame = tk.Frame(notebook, bg="#1b2838")
    notebook.add(disc_frame, text="Discord")

    d = CONFIG["discord"]
    disc_frame.grid_columnconfigure(1, weight=1)

    def make_disc_entry(row, label, key):
        tk.Label(disc_frame, text=label, bg="#1b2838", fg="#66c0f4",
                 font=("Helvetica", 10, "bold"), anchor="w").grid(
            row=row, column=0, sticky="w", padx=12, pady=4)
        var = tk.StringVar(value=str(d.get(key, "")))
        ent = tk.Entry(disc_frame, textvariable=var, bg="#16202d", fg="#c7d5e0",
                       insertbackground="#ffffff", font=("Helvetica", 10, "bold"),
                       relief="flat", highlightthickness=1,
                       highlightcolor="#66c0f4", highlightbackground="#3d4450")
        ent.grid(row=row, column=1, sticky="ew", padx=6, pady=4)
        return var

    en_var = tk.BooleanVar(value=d.get("enabled", True))
    tk.Checkbutton(disc_frame, text="Enabled", variable=en_var,
                   bg="#1b2838", fg="#000000", selectcolor="#2a475e",
                   activebackground="#1b2838", activeforeground="#000000",
                   font=("Helvetica", 10, "bold")).grid(row=0, column=0, columnspan=2,
                                                          sticky="w", padx=12, pady=6)

    dt_var = make_disc_entry(1, "Title Template ({title})", "title_template")
    dg_var = make_disc_entry(2, "Game Field Name", "game_field_name")
    da_var = make_disc_entry(3, "Achievement Field Name", "ach_field_name")
    df_var = make_disc_entry(4, "Footer Text", "footer_text")
    dsc_var = make_disc_entry(5, "Standard Color (int)", "standard_color")
    dtc_var = make_disc_entry(6, "Test Color (int)", "test_color")

    # Status + Buttons
    status = tk.Label(settings_win, text="", bg="#1b2838", fg="#66c0f4",
                      font=("Helvetica", 10, "bold"))
    status.pack(pady=(4, 0))

    btn_frame = tk.Frame(settings_win, bg="#1b2838")
    btn_frame.pack(pady=12)

    def do_save():
        try:
            interval = int(int_var.get().strip())
            if interval < 1:
                raise ValueError
        except ValueError:
            status.config(text="❌ Interval must be a positive integer", fg="#ff6b6b")
            return

        CONFIG["api_key"] = api_var.get().strip()
        CONFIG["steam_id"] = sid_var.get().strip()
        CONFIG["check_interval"] = interval
        CONFIG["sound_filename"] = snd_var.get().strip()
        CONFIG["discord_webhook_url"] = hook_var.get().strip()

        def safe_int(val, fallback):
            try:
                return int(val.strip())
            except ValueError:
                return fallback

        CONFIG["toast"].update({
            "width": safe_int(tw_var.get(), 360),
            "height": safe_int(th_var.get(), 90),
            "x_offset": safe_int(tx_var.get(), 20),
            "y_offset": safe_int(ty_var.get(), 50),
            "duration_ms": safe_int(td_var.get(), 10000),
            "stack_spacing": safe_int(sp_var.get(), 100),
            "bg_color": bg_var.get().strip(),
            "border_color": bc_var.get().strip(),
            "title_color": tc_var.get().strip(),
            "game_color": gc_var.get().strip(),
            "ach_color": ac_var.get().strip(),
            "icon_bg": ic_var.get().strip(),
            "ach_title": at_var.get().strip(),
            "test_title": tt_var.get().strip(),
            "font_family": ff_var.get().strip(),
            "title_size": safe_int(ts_var.get(), 10),
            "game_size": safe_int(gs_var.get(), 12),
            "ach_size": safe_int(als_var.get(), 11),
            "icon_size": safe_int(isz_var.get(), 64),
        })

        CONFIG["discord"].update({
            "enabled": en_var.get(),
            "title_template": dt_var.get().strip(),
            "game_field_name": dg_var.get().strip(),
            "ach_field_name": da_var.get().strip(),
            "footer_text": df_var.get().strip(),
            "standard_color": dsc_var.get().strip(),
            "test_color": dtc_var.get().strip(),
        })

        save_config()
        status.config(text="✅ Saved! Changes apply immediately.", fg="#66c0f4")
        log("⚙️ Settings updated")

    def do_reset():
        if messagebox.askyesno("Reset", "Restore all defaults?"):
            global CONFIG
            CONFIG = json.loads(json.dumps(DEFAULT_CONFIG))
            save_config()
            status.config(text="✅ Defaults restored. Re-open settings to see changes.", fg="#66c0f4")
            log("⚙️ Settings reset to defaults")

    tk.Button(btn_frame, text="💾 Save", command=do_save,
              bg="#66c0f4", fg="#000000", activebackground="#4a9fd4",
              font=("Helvetica", 11, "bold"), relief="flat", padx=16, pady=4).pack(side="left", padx=6)

    tk.Button(btn_frame, text="🔄 Reset", command=do_reset,
              bg="#ff9f43", fg="#000000", activebackground="#e58e3a",
              font=("Helvetica", 11, "bold"), relief="flat", padx=16, pady=4).pack(side="left", padx=6)

    tk.Button(btn_frame, text="Close", command=settings_win.destroy,
              bg="#3d4450", fg="#000000", activebackground="#9099a1",
              font=("Helvetica", 11, "bold"), relief="flat", padx=16, pady=4).pack(side="left", padx=6)


# ==============================================================================
# MAIN
# ==============================================================================
if __name__ == "__main__":
    load_config()
    init_image_cache()

    # First-time setup if credentials are missing
    if not CONFIG.get("api_key") or not CONFIG.get("steam_id"):
        run_first_time_setup()

    root = tk.Tk()
    root.title("Steam Watcher")
    root.configure(bg="#1b2838")
    root.geometry("560x480+100+100")
    root.minsize(400, 300)

    header = tk.Frame(root, bg="#1b2838", padx=12, pady=8)
    header.pack(fill="x")
    tk.Label(header, text="🚀 Steam Watcher", font=("Helvetica", 16, "bold"),
             bg="#1b2838", fg="#66c0f4").pack(side="left")
    tk.Label(header, text="Live Log", font=("Helvetica", 10, "bold"),
             bg="#1b2838", fg="#9099a1").pack(side="right")

    log_frame = tk.Frame(root, bg="#1b2838", padx=8, pady=4)
    log_frame.pack(fill="both", expand=True)

    log_widget = scrolledtext.ScrolledText(
        log_frame,
        bg="#16202d",
        fg="#c7d5e0",
        font=("Consolas", 10, "bold"),
        state="normal",
        wrap="word",
        padx=8,
        pady=6
    )
    log_widget.pack(fill="both", expand=True)

    btn_frame = tk.Frame(root, bg="#1b2838", padx=8, pady=8)
    btn_frame.pack(fill="x")

    def on_test():
        log("🧪 Manual test toast triggered")
        show_toast(CONFIG["toast"]["test_title"], "Team Fortress 2",
                   "Test Achievement", "https://steamstatic.com")

    def toggle_pause():
        global PAUSED
        PAUSED = not PAUSED
        if PAUSED:
            pause_btn.config(text="▶️ Resume", bg="#2ecc71")
            log("⏸️ Paused")
        else:
            pause_btn.config(text="⏸️ Pause", bg="#66c0f4")
            log("▶️ Resumed")

    def on_quit():
        root.destroy()
        os._exit(0)

    tk.Button(btn_frame, text="🧪 Test Toast", command=on_test,
              bg="#66c0f4", fg="#000000", activebackground="#4a9fd4",
              font=("Helvetica", 11, "bold"), relief="flat", padx=10, pady=4).pack(side="left", padx=2)

    pause_btn = tk.Button(btn_frame, text="⏸️ Pause", command=toggle_pause,
                          bg="#66c0f4", fg="#000000", activebackground="#4a9fd4",
                          font=("Helvetica", 11, "bold"), relief="flat", padx=10, pady=4)
    pause_btn.pack(side="left", padx=2)

    tk.Button(btn_frame, text="📋 Achievements", command=lambda: open_achievements_viewer(root),
              bg="#66c0f4", fg="#000000", activebackground="#4a9fd4",
              font=("Helvetica", 11, "bold"), relief="flat", padx=10, pady=4).pack(side="left", padx=2)

    tk.Button(btn_frame, text="⚙️ Settings", command=lambda: open_settings(root),
              bg="#66c0f4", fg="#000000", activebackground="#4a9fd4",
              font=("Helvetica", 11, "bold"), relief="flat", padx=10, pady=4).pack(side="left", padx=2)

    tk.Button(btn_frame, text="❌ Quit", command=on_quit,
              bg="#ff6b6b", fg="#000000", activebackground="#e05555",
              font=("Helvetica", 11, "bold"), relief="flat", padx=10, pady=4).pack(side="left", padx=2)

    root.after(100, process_queues, root, log_widget)
    threading.Thread(target=watcher_loop, daemon=True).start()

    root.mainloop()
