#!/usr/bin/env python3
import os
import sqlite3
import json
import random
import uuid
import re
import subprocess
import shutil
from datetime import datetime, date, timedelta
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from functools import wraps
from flask import Flask, render_template, jsonify, request, g, session, redirect, url_for, send_from_directory
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "checkin.db")
ENV_FILE = "/home/ubuntu/.openclaw/workspace/.env.youtube-api-skill"
RECORDINGS_DIR = os.path.join(BASE_DIR, "recordings")
AVATARS_DIR = os.path.join(BASE_DIR, "static", "avatars")
OPENCLAW_NODE_BIN = os.getenv("OPENCLAW_NODE_BIN", "/home/ubuntu/.nvm/versions/node/v25.8.0/bin/node")
OPENCLAW_CLI_BIN = os.getenv("OPENCLAW_CLI_BIN", "/home/ubuntu/.nvm/versions/node/v25.8.0/bin/openclaw")

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

# 备用可嵌入视频（必须是具体 video id，不能是搜索页）
FALLBACK_VIDEO_IDS = [
    "M7lc1UVf-VE",  # YouTube IFrame API Demo (最稳定可嵌入)
    "9ifQ3xRz4hM",  # BBC Learning English
    "YAsDeXcYyTg",  # BBC Learning English
    "QBddNulwhKo",  # BBC Learning English
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
    os.makedirs(AVATARS_DIR, exist_ok=True)
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
    ensure_column(db, "daily_status", "refresh_0010_done", "INTEGER DEFAULT 0")

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
    ensure_column(db, "users", "avatar_url", "TEXT")
    ensure_column(db, "users", "slogan", "TEXT")
    ensure_column(db, "users", "bio", "TEXT")
    ensure_column(db, "users", "theme_color", "TEXT")

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

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS material_prefetch (
            day TEXT PRIMARY KEY,
            listening_title TEXT,
            listening_url TEXT,
            listening_thumb TEXT,
            listening_source TEXT,
            listening_desc TEXT,
            sentence_cards_material TEXT,
            forced_speaking_material TEXT,
            seed TEXT,
            signature TEXT,
            status TEXT DEFAULT 'prefetched',
            prefetched_at TEXT,
            applied_at TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )

    db.commit()
    db.close()


def is_youtube_video_embeddable(video_id: str):
    if not video_id:
        return False
    oembed = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
    req = Request(oembed)
    try:
        with urlopen(req, timeout=8) as r:
            return int(getattr(r, "status", 200)) == 200
    except Exception:
        return False


def build_fallback_video(seed: str, title: str, desc: str):
    # 优先按顺序选择最稳定可嵌入视频，避免随机到地区/版权受限素材
    vid = FALLBACK_VIDEO_IDS[0]
    for x in FALLBACK_VIDEO_IDS:
        if is_youtube_video_embeddable(x):
            vid = x
            break
    return {
        "title": title,
        "url": f"https://www.youtube.com/watch?v={vid}",
        "thumb": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
        "source": "fallback",
        "desc": desc,
    }


def fetch_listening_item_via_web(query: str, seed: str):
    """
    当 API 配额/认证失败时，走网页搜索兜底（仍属于“从网上拉取”）。
    通过解析 YouTube 搜索页拿到 videoId + 标题，再筛可嵌入视频。
    """
    search_url = f"https://www.youtube.com/results?search_query={quote_plus(query)}"
    req = Request(search_url)
    req.add_header("User-Agent", "Mozilla/5.0")

    try:
        with urlopen(req, timeout=20) as r:
            html = r.read().decode("utf-8", errors="ignore")

        # 从页面 JSON 片段中提取 videoId + title
        pattern = re.compile(
            r'"videoId":"([A-Za-z0-9_-]{11})".*?"title":\{"runs":\[\{"text":"(.*?)"\}\]',
            re.S,
        )
        matches = pattern.findall(html)
        if not matches:
            return None

        # 去重
        items = []
        seen = set()
        for vid, title in matches:
            if vid in seen:
                continue
            seen.add(vid)
            if "\\u" in title:
                try:
                    title = title.encode("utf-8", "ignore").decode("unicode_escape", "ignore")
                except Exception:
                    pass
            items.append((vid, title))

        if not items:
            return None

        rnd = random.Random(seed + "-web")
        rnd.shuffle(items)

        # 优先选可嵌入视频
        for vid, title in items[:12]:
            if is_youtube_video_embeddable(vid):
                return {
                    "title": title or "今日听力素材（网页）",
                    "url": f"https://www.youtube.com/watch?v={vid}",
                    "thumb": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                    "source": "youtube-web",
                    "desc": "主 API 不可用，已自动切换网页检索源。",
                }

        return None
    except Exception:
        return None


def fetch_listening_item(seed: str):
    key = os.getenv("MATON_API_KEY")
    conn = os.getenv("MATON_YOUTUBE_CONNECTION_ID")
    rnd = random.Random(seed + "-listening")
    query = rnd.choice(LISTENING_TOPICS)

    # 主源未配置：尝试网页检索源
    if not key or not conn:
        web_item = fetch_listening_item_via_web(query, seed)
        if web_item:
            return web_item
        return build_fallback_video(
            seed,
            "推荐听力（备用）",
            "主数据源未配置且网页检索失败，已切换到可直接播放的备用视频。",
        )

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

        rnd.shuffle(items)
        for item in items:
            vid = item.get("id", {}).get("videoId", "")
            sn = item.get("snippet", {})
            if vid and is_youtube_video_embeddable(vid):
                return {
                    "title": sn.get("title", "今日听力素材"),
                    "url": f"https://www.youtube.com/watch?v={vid}",
                    "thumb": (sn.get("thumbnails", {}).get("high", {}) or sn.get("thumbnails", {}).get("medium", {})).get("url", f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"),
                    "source": "youtube",
                    "desc": sn.get("description", "")[:300],
                }

        # API 有结果但都不可嵌入，转网页检索源
        web_item = fetch_listening_item_via_web(query, seed)
        if web_item:
            return web_item
        return build_fallback_video(seed, "推荐听力（备用）", "API 结果不可嵌入且网页检索失败，已切换备用视频。")

    except (HTTPError, URLError, Exception):
        # 配额超限/网络错误：转网页检索源
        web_item = fetch_listening_item_via_web(query, seed)
        if web_item:
            return web_item
        return build_fallback_video(
            seed,
            "推荐听力（备用）",
            "主数据源暂不可用且网页检索失败，已切换到可直接播放的备用视频。",
        )


def parse_first_json_object(raw: str):
    if not raw:
        return None
    s = raw.strip()

    # 去掉 markdown 代码块包裹
    fence = re.search(r"```(?:json)?\s*(.*?)```", s, re.S | re.I)
    if fence:
        s = fence.group(1).strip()

    # 先尝试整体解析
    try:
        return json.loads(s)
    except Exception:
        pass

    # 再尝试抽取第一个 JSON 对象
    start = s.find("{")
    end = s.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(s[start : end + 1])
        except Exception:
            return None
    return None


def run_openclaw_agent_json(message: str, timeout_seconds: int = 120):
    node_bin = OPENCLAW_NODE_BIN if os.path.exists(OPENCLAW_NODE_BIN) else (shutil.which("node") or "node")
    cli_bin = OPENCLAW_CLI_BIN if os.path.exists(OPENCLAW_CLI_BIN) else (shutil.which("openclaw") or "openclaw")

    cmd = [
        node_bin,
        cli_bin,
        "agent",
        "--agent",
        "main",
        "--timeout",
        str(timeout_seconds),
        "--message",
        message,
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            timeout=timeout_seconds + 10,
        )
    except Exception:
        return None

    if proc.returncode != 0:
        return None

    return parse_first_json_object(proc.stdout or "")


def normalize_ai_cards(items, theme_en: str, theme_zh: str):
    if not isinstance(items, list):
        return []

    out = []
    seen = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        en = str(it.get("en", "")).strip()
        zh = str(it.get("zh", "")).strip()
        tip = str(it.get("tip", "")).strip()

        if not en or not zh or not tip:
            continue

        if "___" not in en:
            # 尝试把 ... / ____ 统一为 ___
            en = en.replace("....", "___").replace("...", "___").replace("____", "___")
            if "___" not in en:
                continue

        key = en.lower()
        if key in seen:
            continue
        seen.add(key)

        out.append({
            "index": len(out) + 1,
            "theme": f"{theme_zh} / {theme_en}",
            "en": en,
            "zh": zh,
            "tip": tip,
        })
        if len(out) >= 9:
            break

    return out


def generate_sentence_cards_ai(seed: str, theme_en: str, theme_zh: str):
    prompt = f"""
你是资深英语口语教练。请围绕主题“{theme_zh} / {theme_en}”，生成 9 条口语句型卡。

要求：
1) 每条必须包含字段 en / zh / tip；
2) en 是英文句型，必须包含至少一个 ___ 作为填空；
3) zh 是对应自然中文；
4) tip 是中文实操提示（1-2 句，简洁可执行）；
5) 9 条不要重复，偏高频口语场景；
6) 不要输出任何解释文字或 Markdown。

只输出严格 JSON，格式如下：
{{
  "cards": [
    {{"en": "...___...", "zh": "...", "tip": "..."}}
  ]
}}

生成种子：{seed}
""".strip()

    data = run_openclaw_agent_json(prompt, timeout_seconds=120)
    if not data:
        return []

    cards = normalize_ai_cards(data.get("cards"), theme_en, theme_zh)
    return cards if len(cards) >= 9 else []


def generate_sentence_cards_fallback(seed: str, theme_en: str, theme_zh: str):
    # 极端情况下的保底（AI 不可用时），避免任务中断
    rnd = random.Random(seed + "-cards-fallback")
    starters = ["In this topic", "For daily use", "In my situation", "At work", "When I practice"]
    actions = [
        "I usually ___ before I ___",
        "I need to ___ because ___",
        "I plan to ___ by ___",
        "I prefer to ___ when ___",
        "The key point is ___, so I ___",
        "I would like to ___ this week",
        "If possible, I will ___ after ___",
        "My next step is to ___",
        "The challenge is ___, but I ___",
        "I was surprised that ___",
        "The best option is to ___",
    ]
    tips = [
        "先填一个具体动作，再补原因。",
        "把空格换成你今天真实场景。",
        "先慢速读三遍，再连读两遍。",
        "录音复述一次，检查是否流畅。",
        "把句子改成过去时再说一遍。",
        "补一个细节例子让表达更具体。",
    ]

    rnd.shuffle(actions)
    cards = []
    for i, en_core in enumerate(actions[:9], start=1):
        en = f"{rnd.choice(starters)}, {en_core}."
        cards.append(
            {
                "index": i,
                "theme": f"{theme_zh} / {theme_en}",
                "en": en,
                "zh": "围绕当前主题补全句子并说完整理由。",
                "tip": rnd.choice(tips),
            }
        )
    return cards


def generate_sentence_cards(seed: str):
    rnd = random.Random(seed + "-cards")
    theme_en, theme_zh = rnd.choice(THEMES)

    cards = generate_sentence_cards_ai(seed, theme_en, theme_zh)
    if cards:
        return cards

    return generate_sentence_cards_fallback(seed, theme_en, theme_zh)


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



def passed_0010_now():
    now = datetime.now()
    return now.hour > 0 or (now.hour == 0 and now.minute >= 10)


def should_auto_refresh_0010(day: date, row):
    if day != date.today():
        return False
    if not passed_0010_now():
        return False
    return int(row["refresh_0010_done"] or 0) == 0


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


def apply_prefetched_materials(db, day_str: str):
    row = db.execute("SELECT * FROM material_prefetch WHERE day=?", (day_str,)).fetchone()
    if not row:
        return False

    cards = row["sentence_cards_material"] or "[]"
    speaking = row["forced_speaking_material"] or "{}"
    if not row["listening_url"] or cards == "[]" or speaking == "{}":
        return False

    now = datetime.now().isoformat(timespec="seconds")
    db.execute(
        """
        INSERT INTO daily_status(
            day, listening_title, listening_url, listening_thumb, listening_source, listening_desc,
            sentence_cards_material, forced_speaking_material, refresh_0010_done,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT(day) DO UPDATE SET
            listening_title=excluded.listening_title,
            listening_url=excluded.listening_url,
            listening_thumb=excluded.listening_thumb,
            listening_source=excluded.listening_source,
            listening_desc=excluded.listening_desc,
            sentence_cards_material=excluded.sentence_cards_material,
            forced_speaking_material=excluded.forced_speaking_material,
            refresh_0010_done=1,
            updated_at=excluded.updated_at
        """,
        (
            day_str,
            row["listening_title"] or "今日听力素材",
            row["listening_url"] or "",
            row["listening_thumb"] or "",
            row["listening_source"] or "prefetch",
            row["listening_desc"] or "",
            cards,
            speaking,
            now,
            now,
        ),
    )
    db.execute(
        "UPDATE material_prefetch SET status='applied', applied_at=?, updated_at=? WHERE day=?",
        (now, now, day_str),
    )
    db.commit()
    return True


def auto_refresh_0010_from_db(db, day_str: str):
    if apply_prefetched_materials(db, day_str):
        return
    # 兜底：如果 23:50 预拉取失败，00:10 仍保证有新素材可切换
    refresh_day_materials(db, day_str, seed_suffix="-0010-fallback")
    db.execute("UPDATE daily_status SET refresh_0010_done=1 WHERE day=?", (day_str,))
    db.commit()


def ensure_day_row(db, day: date):
    day_str = day.isoformat()
    row = db.execute("SELECT * FROM daily_status WHERE day=?", (day_str,)).fetchone()
    if row:
        if not row["sentence_cards_material"] or not row["forced_speaking_material"]:
            refresh_day_materials(db, day_str)
            row = db.execute("SELECT * FROM daily_status WHERE day=?", (day_str,)).fetchone()

        # 每天 00:10 自动从“预拉取库”切换当日素材（仅触发一次）
        if should_auto_refresh_0010(day, row):
            auto_refresh_0010_from_db(db, day_str)
            row = db.execute("SELECT * FROM daily_status WHERE day=?", (day_str,)).fetchone()
        return row

    now = datetime.now()
    listening, cards, speaking = build_daily_materials(day_str)
    now_str = now.isoformat(timespec="seconds")
    db.execute(
        """
        INSERT INTO daily_status(
            day, listening_title, listening_url, listening_thumb, listening_source, listening_desc,
            sentence_cards_material, forced_speaking_material, refresh_0010_done,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
        """,
        (
            day_str,
            listening["title"], listening["url"], listening["thumb"], listening["source"], listening["desc"],
            json.dumps(cards, ensure_ascii=False), json.dumps(speaking, ensure_ascii=False),
            now_str, now_str,
        ),
    )
    db.commit()

    if day == date.today() and passed_0010_now():
        auto_refresh_0010_from_db(db, day_str)

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



def get_user_profile(db, phone: str):
    row = db.execute(
        "SELECT nickname, avatar_url, slogan, bio, theme_color FROM users WHERE phone=?",
        (phone,),
    ).fetchone()
    if not row:
        return {
            "nickname": "",
            "avatar_url": "",
            "slogan": "",
            "bio": "",
            "theme_color": "",
            "display_name": phone,
        }
    nickname = (row["nickname"] or "").strip()
    avatar_url = (row["avatar_url"] or "").strip()
    slogan = (row["slogan"] or "").strip()
    bio = (row["bio"] or "").strip()
    theme_color = (row["theme_color"] or "").strip()
    return {
        "nickname": nickname,
        "avatar_url": avatar_url,
        "slogan": slogan,
        "bio": bio,
        "theme_color": theme_color,
        "display_name": nickname if nickname else phone,
    }


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
    current_profile = get_user_profile(db, current_phone)
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
        "material_updated_at": material_row["updated_at"],
        "current_user": {
            "phone": current_phone,
            "nickname": current_profile["nickname"],
            "avatar_url": current_profile["avatar_url"],
            "slogan": current_profile["slogan"],
            "bio": current_profile["bio"],
            "theme_color": current_profile["theme_color"],
            "display_name": current_profile["display_name"],
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
    users = db.execute("SELECT phone, nickname, avatar_url, slogan, bio, theme_color FROM users ORDER BY COALESCE(nickname, phone), phone").fetchall()
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
        avatar_url = (u["avatar_url"] or "").strip()
        slogan = (u["slogan"] or "").strip()
        bio = (u["bio"] or "").strip()
        theme_color = (u["theme_color"] or "").strip()
        out.append({
            "phone": phone,
            "nickname": nickname,
            "avatar_url": avatar_url,
            "slogan": slogan,
            "bio": bio,
            "theme_color": theme_color,
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
    avatar_url = (payload.get("avatar_url") or "").strip()
    slogan = (payload.get("slogan") or "").strip()
    bio = (payload.get("bio") or "").strip()
    theme_color = (payload.get("theme_color") or "").strip()

    if len(nickname) > 20:
        return jsonify({"ok": False, "error": "昵称最多 20 个字符"}), 400
    if len(slogan) > 60:
        return jsonify({"ok": False, "error": "Slogan 最多 60 个字符"}), 400
    if len(bio) > 160:
        return jsonify({"ok": False, "error": "简介最多 160 个字符"}), 400
    if avatar_url and not (
        avatar_url.startswith("http://")
        or avatar_url.startswith("https://")
        or avatar_url.startswith("/static/")
    ):
        return jsonify({"ok": False, "error": "头像链接需为 http(s) 或站内 /static/ 路径"}), 400
    if theme_color and (len(theme_color) != 7 or not theme_color.startswith("#") or any(c not in "0123456789abcdefABCDEF" for c in theme_color[1:])):
        return jsonify({"ok": False, "error": "主题色格式需为 #RRGGBB"}), 400

    now = datetime.now().isoformat(timespec="seconds")
    db.execute(
        "UPDATE users SET nickname=?, avatar_url=?, slogan=?, bio=?, theme_color=?, updated_at=? WHERE phone=?",
        (nickname, avatar_url, slogan, bio, theme_color, now, session["phone"]),
    )
    db.commit()

    profile = get_user_profile(db, session["phone"])
    return jsonify({
        "ok": True,
        "profile": {
            "phone": session["phone"],
            "nickname": profile["nickname"],
            "avatar_url": profile["avatar_url"],
            "slogan": profile["slogan"],
            "bio": profile["bio"],
            "theme_color": profile["theme_color"],
            "display_name": profile["display_name"],
        },
    })


@app.post("/api/profile/avatar")
@auth_required
def api_profile_avatar_upload():
    if "avatar" not in request.files:
        return jsonify({"ok": False, "error": "missing avatar file"}), 400

    f = request.files["avatar"]
    if not f or f.filename == "":
        return jsonify({"ok": False, "error": "empty file"}), 400

    ctype = (f.content_type or "").lower()
    if not ctype.startswith("image/"):
        return jsonify({"ok": False, "error": "仅支持图片文件"}), 400

    ext = os.path.splitext(secure_filename(f.filename))[1].lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        ext = ".jpg"

    f.stream.seek(0, os.SEEK_END)
    size = f.stream.tell()
    f.stream.seek(0)
    if size > 5 * 1024 * 1024:
        return jsonify({"ok": False, "error": "头像文件不能超过 5MB"}), 400

    filename = f"{session['phone']}-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}{ext}"
    abs_path = os.path.join(AVATARS_DIR, filename)
    f.save(abs_path)

    avatar_url = f"/static/avatars/{filename}"
    now = datetime.now().isoformat(timespec="seconds")
    db = get_db()
    db.execute("UPDATE users SET avatar_url=?, updated_at=? WHERE phone=?", (avatar_url, now, session["phone"]))
    db.commit()

    return jsonify({"ok": True, "avatar_url": avatar_url})


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
