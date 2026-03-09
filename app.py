#!/usr/bin/env python3
import os
import sqlite3
import json
import random
import uuid
from datetime import datetime, date, timedelta
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from functools import wraps
from flask import Flask, render_template, jsonify, request, g, session, redirect, url_for, send_from_directory

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "checkin.db")
ENV_FILE = "/home/ubuntu/.openclaw/workspace/.env.youtube-api-skill"
RECORDINGS_DIR = os.path.join(BASE_DIR, "recordings")

TASK_KEYS = [
    "listening_input",
    "sentence_cards",
    "forced_speaking",
    "dialog_practice",
    "review_three_errors",
]

TASK_LABELS = {
    "listening_input": "10 分钟：听力输入（仅选一个主题）",
    "sentence_cards": "15 分钟：句型卡练习（9个高频句）",
    "forced_speaking": "15 分钟：强制开口（2–3分钟录音）",
    "dialog_practice": "10 分钟：对话实战（AI/真人）",
    "review_three_errors": "10 分钟：复盘（只修3个错误）",
}

LISTENING_TOPICS = [
    "English shadowing daily",
    "daily English listening practice",
    "business English conversation",
    "TED talk English subtitle",
    "English speaking practice",
    "travel English conversation",
]

THEMES = [
    ("Self-introduction", "自我介绍"),
    ("Work progress", "工作进展"),
    ("Daily routine", "日常安排"),
    ("Problem solving", "问题处理"),
    ("Travel and booking", "出行与预订"),
    ("Learning reflection", "学习复盘"),
]


def load_env_file(path: str):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and v and not os.getenv(k):
                os.environ[k] = v


def get_or_create_secret_key():
    env_key = os.getenv("CHECKIN_SECRET_KEY")
    if env_key:
        return env_key
    path = os.path.join(BASE_DIR, ".secret_key")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    key = os.urandom(32).hex()
    with open(path, "w", encoding="utf-8") as f:
        f.write(key)
    os.chmod(path, 0o600)
    return key


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


def ensure_column(db, table, column, col_type):
    cols = [r[1] for r in db.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


def init_db():
    os.makedirs(RECORDINGS_DIR, exist_ok=True)
    db = sqlite3.connect(DB_PATH)

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_status (
            day TEXT PRIMARY KEY,
            listening_title TEXT,
            listening_url TEXT,
            listening_thumb TEXT,
            listening_source TEXT,
            listening_desc TEXT,
            sentence_cards_material TEXT,
            forced_speaking_material TEXT,
            listening_input INTEGER DEFAULT 0,
            sentence_cards INTEGER DEFAULT 0,
            forced_speaking INTEGER DEFAULT 0,
            dialog_practice INTEGER DEFAULT 0,
            review_three_errors INTEGER DEFAULT 0,
            checked_in INTEGER DEFAULT 0,
            checkin_at TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    ensure_column(db, "daily_status", "sentence_cards_material", "TEXT")
    ensure_column(db, "daily_status", "forced_speaking_material", "TEXT")

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            phone TEXT PRIMARY KEY,
            nickname TEXT,
            pin_hash TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    ensure_column(db, "users", "nickname", "TEXT")

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS member_progress (
            day TEXT,
            phone TEXT,
            listening_input INTEGER DEFAULT 0,
            sentence_cards INTEGER DEFAULT 0,
            forced_speaking INTEGER DEFAULT 0,
            dialog_practice INTEGER DEFAULT 0,
            review_three_errors INTEGER DEFAULT 0,
            checked_in INTEGER DEFAULT 0,
            checkin_at TEXT,
            created_at TEXT,
            updated_at TEXT,
            PRIMARY KEY (day, phone)
        )
        """
    )

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS recordings (
            id TEXT PRIMARY KEY,
            day TEXT,
            phone TEXT,
            file_rel TEXT,
            duration_sec REAL DEFAULT 0,
            created_at TEXT
        )
        """
    )

    db.commit()
    db.close()


def fetch_listening_item(seed: str):
    key = os.getenv("MATON_API_KEY")
    conn = os.getenv("MATON_YOUTUBE_CONNECTION_ID")
    rnd = random.Random(seed + "-listening")

    if not key or not conn:
        return {
            "title": "推荐听力（未配置YouTube API）",
            "url": "https://www.youtube.com/results?search_query=english+listening+practice",
            "thumb": "",
            "source": "fallback",
            "desc": "请先配置 MATON_API_KEY 与连接。",
        }

    query = rnd.choice(LISTENING_TOPICS)
    api = (
        "https://gateway.maton.ai/youtube/youtube/v3/search?part=snippet"
        f"&q={quote_plus(query)}&type=video&maxResults=10&order=relevance"
    )
    req = Request(api)
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Maton-Connection", conn)

    try:
        with urlopen(req, timeout=25) as r:
            data = json.load(r)
        items = data.get("items", [])
        if not items:
            raise RuntimeError("no items")
        item = rnd.choice(items)
        vid = item.get("id", {}).get("videoId", "")
        sn = item.get("snippet", {})
        return {
            "title": sn.get("title", "今日听力素材"),
            "url": f"https://www.youtube.com/watch?v={vid}" if vid else "https://www.youtube.com",
            "thumb": (sn.get("thumbnails", {}).get("high", {}) or sn.get("thumbnails", {}).get("medium", {})).get("url", ""),
            "source": "youtube",
            "desc": sn.get("description", "")[:300],
        }
    except (HTTPError, URLError, Exception):
        return {
            "title": "推荐听力（备用）",
            "url": "https://www.youtube.com/results?search_query=english+listening+practice",
            "thumb": "",
            "source": "fallback",
            "desc": "主数据源暂不可用，已使用备用推荐链接。",
        }


def generate_sentence_cards(seed: str):
    rnd = random.Random(seed + "-cards")
    theme_en, theme_zh = rnd.choice(THEMES)
    templates = [
        ("I usually ___ before I ___.", "我通常在___之前___。"),
        ("I need to ___ because ___.", "我需要___，因为___。"),
        ("Could you please ___ for me?", "你可以帮我___吗？"),
        ("The main reason is that ___.", "主要原因是___。"),
        ("I plan to ___ by ___.", "我计划在___之前___。"),
        ("What I learned today is ___.", "我今天学到的是___。"),
        ("If I had more time, I would ___.", "如果有更多时间，我会___。"),
        ("I agree with ___ because ___.", "我同意___，因为___。"),
        ("The challenge is ___, so I ___.", "挑战是___，所以我___。"),
        ("My next step is to ___.", "我的下一步是___。"),
        ("From my perspective, ___.", "在我看来，___。"),
        ("I was surprised that ___.", "让我惊讶的是___。"),
        ("The best option might be ___.", "最好的选择可能是___。"),
        ("I would like to improve my ___.", "我想提升我的___。"),
        ("In this situation, I prefer to ___.", "在这个情境下，我更倾向于___。"),
    ]
    picked = rnd.sample(templates, 9)
    cards = []
    for i, (en, zh) in enumerate(picked, start=1):
        cards.append({
            "index": i,
            "theme": f"{theme_zh} / {theme_en}",
            "en": en,
            "zh": zh,
            "tip": "先替换空格，再连说3遍；最后脱稿复述1遍。",
        })
    return cards


def generate_speaking_module(seed: str):
    rnd = random.Random(seed + "-speak")
    topic_en, topic_zh = rnd.choice(THEMES)
    must_use = rnd.sample([
        "I think...", "In my case...", "The key point is...", "For example...", "My next step is...", "I realized that..."
    ], 3)
    return {
        "topic": f"{topic_zh}（{topic_en}）",
        "goal": "录制 2–3 分钟连续表达，不中断超过 5 秒。",
        "structure": [
            "00:00-00:30：背景与主题",
            "00:30-01:30：具体经历/事实",
            "01:30-02:30：观点与下一步",
        ],
        "must_use": must_use,
        "self_check": [
            "是否连续说满 2 分钟以上",
            "是否使用了 3 个连接词（because/so/however 等）",
            "是否给出了一个具体例子",
        ],
    }


def build_daily_materials(day_str: str):
    listening = fetch_listening_item(day_str)
    cards = generate_sentence_cards(day_str)
    speaking = generate_speaking_module(day_str)
    return listening, cards, speaking


def refresh_day_materials(db, day_str: str, seed_suffix: str = ""):
    listening, cards, speaking = build_daily_materials(day_str + seed_suffix)
    now = datetime.now().isoformat(timespec="seconds")
    db.execute(
        """
        UPDATE daily_status
        SET listening_title=?, listening_url=?, listening_thumb=?, listening_source=?, listening_desc=?,
            sentence_cards_material=?, forced_speaking_material=?, updated_at=?
        WHERE day=?
        """,
        (
            listening["title"], listening["url"], listening["thumb"], listening["source"], listening["desc"],
            json.dumps(cards, ensure_ascii=False), json.dumps(speaking, ensure_ascii=False), now, day_str,
        ),
    )
    db.commit()


def ensure_day_row(db, day: date):
    day_str = day.isoformat()
    row = db.execute("SELECT * FROM daily_status WHERE day=?", (day_str,)).fetchone()
    if row:
        if not row["sentence_cards_material"] or not row["forced_speaking_material"]:
            refresh_day_materials(db, day_str)
            row = db.execute("SELECT * FROM daily_status WHERE day=?", (day_str,)).fetchone()
        return row

    listening, cards, speaking = build_daily_materials(day_str)
    now = datetime.now().isoformat(timespec="seconds")
    db.execute(
        """
        INSERT INTO daily_status(
            day, listening_title, listening_url, listening_thumb, listening_source, listening_desc,
            sentence_cards_material, forced_speaking_material,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            day_str,
            listening["title"], listening["url"], listening["thumb"], listening["source"], listening["desc"],
            json.dumps(cards, ensure_ascii=False), json.dumps(speaking, ensure_ascii=False),
            now, now,
        ),
    )
    db.commit()
    return db.execute("SELECT * FROM daily_status WHERE day=?", (day_str,)).fetchone()


def ensure_member_row(db, day_str: str, phone: str):
    row = db.execute("SELECT * FROM member_progress WHERE day=? AND phone=?", (day_str, phone)).fetchone()
    if row:
        return row
    now = datetime.now().isoformat(timespec="seconds")
    db.execute(
        """
        INSERT INTO member_progress(day, phone, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        (day_str, phone, now, now),
    )
    db.commit()
    return db.execute("SELECT * FROM member_progress WHERE day=? AND phone=?", (day_str, phone)).fetchone()



def get_user_nickname(db, phone: str):
    row = db.execute("SELECT nickname FROM users WHERE phone=?", (phone,)).fetchone()
    if not row:
        return ""
    return (row["nickname"] or "").strip()


def get_user_display_name(db, phone: str):
    nickname = get_user_nickname(db, phone)
    return nickname if nickname else phone


def parse_json_safe(s, default):
    if not s:
        return default
    try:
        return json.loads(s)
    except Exception:
        return default


def get_recordings_by_day(db, day_str: str):
    rows = db.execute(
        """
        SELECT r.id, r.day, r.phone, r.file_rel, r.duration_sec, r.created_at, u.nickname
        FROM recordings r
        LEFT JOIN users u ON u.phone = r.phone
        WHERE r.day=?
        ORDER BY r.created_at DESC
        """,
        (day_str,),
    ).fetchall()
    out = []
    for r in rows:
        nickname = (r["nickname"] or "").strip() if "nickname" in r.keys() else ""
        out.append({
            "id": r["id"],
            "day": r["day"],
            "phone": r["phone"],
            "nickname": nickname,
            "display_name": nickname if nickname else r["phone"],
            "duration_sec": r["duration_sec"],
            "created_at": r["created_at"],
            "url": f"/recordings/{r['file_rel']}",
        })
    return out


def row_to_payload(material_row, member_row, recordings):
    db = get_db()
    tasks = [{"key": k, "label": TASK_LABELS[k], "done": bool(member_row[k])} for k in TASK_KEYS]
    done_all = all(t["done"] for t in tasks)
    current_phone = member_row["phone"]
    current_nickname = get_user_nickname(db, current_phone)
    return {
        "day": material_row["day"],
        "listening": {
            "title": material_row["listening_title"],
            "url": material_row["listening_url"],
            "thumb": material_row["listening_thumb"],
            "source": material_row["listening_source"],
            "desc": material_row["listening_desc"],
        },
        "sentence_cards_material": parse_json_safe(material_row["sentence_cards_material"], []),
        "forced_speaking_material": parse_json_safe(material_row["forced_speaking_material"], {}),
        "tasks": tasks,
        "done_all": done_all,
        "checked_in": bool(member_row["checked_in"]),
        "checkin_at": member_row["checkin_at"],
        "recordings": recordings,
        "current_user": {
            "phone": current_phone,
            "nickname": current_nickname,
            "display_name": current_nickname if current_nickname else current_phone,
        },
    }


def build_weekly_stats(db, phone: str):
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    days, heatmap = [], []
    checked_count = 0
    missed = 0

    for i in range(7):
        d = monday + timedelta(days=i)
        row = db.execute("SELECT * FROM member_progress WHERE day=? AND phone=?", (d.isoformat(), phone)).fetchone()
        done_cnt = sum(int(row[k]) for k in TASK_KEYS) if row else 0
        checked = int(row["checked_in"]) == 1 if row else False
        if checked:
            checked_count += 1
        else:
            missed += 1

        day_obj = {
            "day": d.isoformat(),
            "weekday": ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][i],
            "done_count": done_cnt,
            "checked_in": checked,
        }
        days.append(day_obj)
        heatmap.append({
            "day": d.isoformat(),
            "weekday": day_obj["weekday"],
            "cells": [{"task": TASK_LABELS[k], "done": bool(int(row[k]) if row else 0)} for k in TASK_KEYS],
        })

    return {
        "monday": monday.isoformat(),
        "sunday": (monday + timedelta(days=6)).isoformat(),
        "checkin_rate": round(checked_count / 7 * 100, 1),
        "missed_days": missed,
        "days": days,
        "heatmap": heatmap,
    }


def build_members_progress(db, day_str: str):
    users = db.execute("SELECT phone, nickname FROM users ORDER BY COALESCE(nickname, phone), phone").fetchall()
    out = []
    for u in users:
        phone = u["phone"]
        row = db.execute("SELECT * FROM member_progress WHERE day=? AND phone=?", (day_str, phone)).fetchone()
        if row:
            done_count = sum(int(row[k]) for k in TASK_KEYS)
            checked = bool(row["checked_in"])
            updated_at = row["updated_at"]
        else:
            done_count = 0
            checked = False
            updated_at = None
        nickname = (u["nickname"] or "").strip()
        out.append({
            "phone": phone,
            "nickname": nickname,
            "display_name": nickname if nickname else phone,
            "done_count": done_count,
            "checked_in": checked,
            "updated_at": updated_at,
        })
    return out


def auth_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if session.get("phone"):
            return func(*args, **kwargs)
        if request.path.startswith("/api/") or request.path.startswith("/recordings/"):
            return jsonify({"ok": False, "error": "unauthorized"}), 401
        return redirect(url_for("login"))
    return wrapper


app = Flask(__name__)
load_env_file(ENV_FILE)
init_db()
app.secret_key = get_or_create_secret_key()


@app.before_request
def _boot():
    load_env_file(ENV_FILE)


@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


@app.get("/login")
def login():
    if session.get("phone"):
        return redirect(url_for("index"))
    return render_template("login.html")


@app.post("/login")
def login_post():
    phone = (request.form.get("phone") or "").strip()
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE phone=?", (phone,)).fetchone()
    if not row:
        return render_template("login.html", error="手机号不在白名单")
    if row["nickname"] is None:
        now = datetime.now().isoformat(timespec="seconds")
        db.execute("UPDATE users SET nickname=?, updated_at=? WHERE phone=?", ("", now, phone))
        db.commit()
    session["phone"] = phone
    return redirect(url_for("index"))


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
@auth_required
def index():
    db = get_db()
    day = date.today()
    day_str = day.isoformat()
    material = ensure_day_row(db, day)
    member = ensure_member_row(db, day_str, session["phone"])
    recs = get_recordings_by_day(db, day_str)
    return render_template("index.html", data=row_to_payload(material, member, recs), phone=session.get("phone"), today_str=day_str)


@app.get("/weekly")
@auth_required
def weekly():
    db = get_db()
    stats = build_weekly_stats(db, session["phone"])
    return render_template("weekly.html", stats=stats, phone=session.get("phone"))


@app.get("/api/today")
@auth_required
def api_today():
    db = get_db()
    day = date.today()
    day_str = day.isoformat()
    material = ensure_day_row(db, day)
    member = ensure_member_row(db, day_str, session["phone"])
    recs = get_recordings_by_day(db, day_str)
    return jsonify(row_to_payload(material, member, recs))


@app.get("/api/weekly")
@auth_required
def api_weekly():
    db = get_db()
    return jsonify(build_weekly_stats(db, session["phone"]))


@app.get("/api/members/progress")
@auth_required
def api_members_progress():
    day_str = request.args.get("day", date.today().isoformat())
    db = get_db()
    return jsonify({"day": day_str, "members": build_members_progress(db, day_str)})


@app.post("/api/profile")
@auth_required
def api_profile_update():
    db = get_db()
    payload = request.get_json(silent=True) or {}
    nickname = (payload.get("nickname") or "").strip()
    if len(nickname) > 20:
        return jsonify({"ok": False, "error": "昵称最多 20 个字符"}), 400
    now = datetime.now().isoformat(timespec="seconds")
    db.execute(
        "UPDATE users SET nickname=?, updated_at=? WHERE phone=?",
        (nickname, now, session["phone"]),
    )
    db.commit()
    return jsonify({
        "ok": True,
        "profile": {
            "phone": session["phone"],
            "nickname": nickname,
            "display_name": nickname if nickname else session["phone"],
        },
    })


@app.get("/api/history")
@auth_required
def api_history():
    limit = int(request.args.get("limit", 30))
    limit = max(1, min(limit, 180))
    db = get_db()
    rows = db.execute(
        """
        SELECT day, checked_in, checkin_at, listening_input, sentence_cards, forced_speaking, dialog_practice, review_three_errors
        FROM member_progress
        WHERE phone=?
        ORDER BY day DESC LIMIT ?
        """,
        (session["phone"], limit),
    ).fetchall()
    out = []
    for r in rows:
        out.append({
            "day": r["day"],
            "checked_in": bool(r["checked_in"]),
            "checkin_at": r["checkin_at"],
            "done_count": sum(int(r[k]) for k in TASK_KEYS),
        })
    return jsonify({"days": out})


@app.get("/api/day/<day_str>")
@auth_required
def api_day(day_str):
    try:
        date.fromisoformat(day_str)
    except Exception:
        return jsonify({"ok": False, "error": "invalid day"}), 400
    db = get_db()
    material = db.execute("SELECT * FROM daily_status WHERE day=?", (day_str,)).fetchone()
    if not material:
        return jsonify({"ok": False, "error": "not found"}), 404
    member = ensure_member_row(db, day_str, session["phone"])
    recs = get_recordings_by_day(db, day_str)
    return jsonify({"ok": True, "data": row_to_payload(material, member, recs)})


@app.post("/api/task/<task_key>/toggle")
@auth_required
def api_toggle(task_key):
    if task_key not in TASK_KEYS:
        return jsonify({"ok": False, "error": "invalid task key"}), 400
    db = get_db()
    day_str = date.today().isoformat()
    ensure_day_row(db, date.today())
    member = ensure_member_row(db, day_str, session["phone"])
    current = int(member[task_key])
    now = datetime.now().isoformat(timespec="seconds")
    db.execute(
        f"UPDATE member_progress SET {task_key}=?, updated_at=? WHERE day=? AND phone=?",
        (0 if current else 1, now, day_str, session["phone"]),
    )
    db.commit()
    material2 = db.execute("SELECT * FROM daily_status WHERE day=?", (day_str,)).fetchone()
    member2 = db.execute("SELECT * FROM member_progress WHERE day=? AND phone=?", (day_str, session["phone"])).fetchone()
    recs = get_recordings_by_day(db, day_str)
    return jsonify({"ok": True, "data": row_to_payload(material2, member2, recs)})


@app.post("/api/checkin")
@auth_required
def api_checkin():
    db = get_db()
    day_str = date.today().isoformat()
    material = ensure_day_row(db, date.today())
    member = ensure_member_row(db, day_str, session["phone"])
    payload = row_to_payload(material, member, get_recordings_by_day(db, day_str))
    if not payload["done_all"]:
        return jsonify({"ok": False, "error": "请先完成全部任务再打卡。", "data": payload}), 400
    if not payload["checked_in"]:
        ts = datetime.now().isoformat(timespec="seconds")
        db.execute(
            "UPDATE member_progress SET checked_in=1, checkin_at=?, updated_at=? WHERE day=? AND phone=?",
            (ts, ts, day_str, session["phone"]),
        )
        db.commit()
    member2 = db.execute("SELECT * FROM member_progress WHERE day=? AND phone=?", (day_str, session["phone"])).fetchone()
    return jsonify({"ok": True, "message": "打卡成功，今天完成得很扎实。", "data": row_to_payload(material, member2, get_recordings_by_day(db, day_str))})


@app.post("/api/refresh-materials")
@auth_required
def api_refresh_materials():
    db = get_db()
    day_str = date.today().isoformat()
    ensure_day_row(db, date.today())
    refresh_day_materials(db, day_str, seed_suffix="-manual")
    material = db.execute("SELECT * FROM daily_status WHERE day=?", (day_str,)).fetchone()
    member = ensure_member_row(db, day_str, session["phone"])
    recs = get_recordings_by_day(db, day_str)
    return jsonify({"ok": True, "data": row_to_payload(material, member, recs)})


@app.post("/api/recordings/upload")
@auth_required
def api_recordings_upload():
    if "audio" not in request.files:
        return jsonify({"ok": False, "error": "missing audio file"}), 400
    day_str = (request.form.get("day") or date.today().isoformat()).strip()
    try:
        date.fromisoformat(day_str)
    except Exception:
        return jsonify({"ok": False, "error": "invalid day"}), 400

    audio = request.files["audio"]
    if not audio or audio.filename == "":
        return jsonify({"ok": False, "error": "empty file"}), 400

    ext = "webm"
    ctype = (audio.content_type or "").lower()
    if "ogg" in ctype:
        ext = "ogg"
    elif "mp4" in ctype or "mpeg" in ctype or "aac" in ctype:
        ext = "m4a"

    rec_id = uuid.uuid4().hex
    day_dir = os.path.join(RECORDINGS_DIR, day_str)
    os.makedirs(day_dir, exist_ok=True)
    filename = f"{session['phone']}-{datetime.now().strftime('%H%M%S')}-{rec_id[:6]}.{ext}"
    abs_path = os.path.join(day_dir, filename)
    audio.save(abs_path)

    rel = f"{day_str}/{filename}"
    duration = 0.0
    try:
        duration = float(request.form.get("duration", "0") or 0)
    except Exception:
        duration = 0.0

    db = get_db()
    db.execute(
        "INSERT INTO recordings(id, day, phone, file_rel, duration_sec, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (rec_id, day_str, session["phone"], rel, duration, datetime.now().isoformat(timespec="seconds")),
    )
    db.commit()
    recs = get_recordings_by_day(db, day_str)
    return jsonify({"ok": True, "recordings": recs})


@app.get("/recordings/<path:filename>")
@auth_required
def recordings_file(filename):
    return send_from_directory(RECORDINGS_DIR, filename)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8099)
