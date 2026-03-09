#!/usr/bin/env python3
import os
import sqlite3
import random
import subprocess
from datetime import datetime, date

DB_PATH = os.path.join(os.path.dirname(__file__), "checkin.db")
CHAT_ID = os.getenv("CHECKIN_CHAT_ID", "")
PRIMARY_PHONE = os.getenv("CHECKIN_PRIMARY_PHONE", "user_001")
SITE_URL = os.getenv("LANGUAGE_CHECKIN_SITE_URL", "http://127.0.0.1:8099")
TASKS = ["listening_input", "sentence_cards", "forced_speaking", "dialog_practice", "review_three_errors"]


def should_send_now():
    if os.getenv("FORCE_REMINDER") == "1":
        return True
    now = datetime.now()
    if now.hour < 9 or now.hour > 22:
        return False
    return random.random() < 0.45


def today_incomplete():
    if not os.path.exists(DB_PATH):
        return True
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    row = db.execute(
        "SELECT * FROM member_progress WHERE day=? AND phone=?",
        (date.today().isoformat(), PRIMARY_PHONE),
    ).fetchone()
    db.close()
    if not row:
        return True
    done_all = all(int(row[k]) == 1 for k in TASKS)
    checked = int(row["checked_in"]) == 1
    return not (done_all and checked)


def send_reminder():
    if not CHAT_ID:
        return
    msg = f"提醒你一下：今天的语言训练还没完成打卡。先完成5个任务，再点打卡 ✅\n网站： {SITE_URL}"
    subprocess.run([
        "openclaw", "message", "send",
        "--channel", "telegram",
        "--target", CHAT_ID,
        "--message", msg,
    ], check=False)


if __name__ == "__main__":
    if should_send_now() and today_incomplete():
        send_reminder()
