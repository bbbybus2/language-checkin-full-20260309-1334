#!/usr/bin/env python3
import os
import re
import sqlite3
import subprocess
from datetime import date, datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "checkin.db"
DEFAULT_EMAIL = "demo@example.com"
DEFAULT_NICKNAME = "lulu"
DEFAULT_GOG_ACCOUNT = ""
DEFAULT_GOG_BIN = "/home/ubuntu/.local/bin/gog"
DEFAULT_OPENCLAW_BIN = "/home/ubuntu/.nvm/versions/node/v25.8.0/bin/openclaw"
DEFAULT_OWNER_TELEGRAM_ID = ""
DEFAULT_SITE_URL = "http://127.0.0.1:8099/"
SERVICE_ENV_FILE = Path.home() / ".config/systemd/user/openclaw-gateway.service.d/10-env.conf"


def now_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_gog_env() -> None:
    """Cron 场景下补齐 GOG_KEYRING_PASSWORD（若 shell 未注入）。"""
    if os.getenv("GOG_KEYRING_PASSWORD"):
        return

    if not SERVICE_ENV_FILE.exists():
        return

    try:
        text = SERVICE_ENV_FILE.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return

    m = re.search(r"^Environment=GOG_KEYRING_PASSWORD=(.+)$", text, re.M)
    if m:
        os.environ["GOG_KEYRING_PASSWORD"] = m.group(1).strip()


def fetch_member_status(day_str: str, nickname: str):
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT u.phone, u.nickname,
                   COALESCE(mp.checked_in, 0) AS checked_in,
                   mp.updated_at
            FROM users u
            LEFT JOIN member_progress mp
              ON mp.phone = u.phone AND mp.day = ?
            WHERE LOWER(COALESCE(u.nickname, '')) = LOWER(?)
            LIMIT 1
            """,
            (day_str, nickname),
        ).fetchone()
        return row
    finally:
        conn.close()


def send_email(to_email: str, subject: str, body: str) -> tuple[int, str]:
    gog_bin = os.getenv("GOG_BIN", DEFAULT_GOG_BIN)
    if not Path(gog_bin).exists():
        gog_bin = "gog"

    account = os.getenv("GOG_ACCOUNT", DEFAULT_GOG_ACCOUNT)
    cmd = [
        gog_bin,
        "gmail",
        "send",
        "--no-input",
        "--to",
        to_email,
        "--subject",
        subject,
        "--body",
        body,
    ]
    if account:
        cmd.extend(["--account", account])

    dry_run = os.getenv("DRY_RUN", "0") in {"1", "true", "TRUE", "yes", "YES"}
    if dry_run:
        cmd.append("--dry-run")

    proc = subprocess.run(cmd, capture_output=True, text=True)
    output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    return proc.returncode, output.strip()


def send_owner_notice(text: str) -> None:
    notify_on = os.getenv("NOTIFY_OWNER", "1") in {"1", "true", "TRUE", "yes", "YES"}
    if not notify_on:
        return

    openclaw_bin = os.getenv("OPENCLAW_BIN", DEFAULT_OPENCLAW_BIN)
    if not Path(openclaw_bin).exists():
        openclaw_bin = "openclaw"

    owner_target = os.getenv("OWNER_TELEGRAM_ID", DEFAULT_OWNER_TELEGRAM_ID).strip()
    if not owner_target:
        return

    cmd = [
        openclaw_bin,
        "message",
        "send",
        "--channel",
        "telegram",
        "--target",
        owner_target,
        "--message",
        text,
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        print(f"[{now_ts()}] [lulu-reminder] warn: notify owner failed")
        if err:
            print(err)


def main() -> int:
    ensure_gog_env()

    day_str = date.today().isoformat()
    nickname = os.getenv("LULU_NICKNAME", DEFAULT_NICKNAME)
    to_email = os.getenv("LULU_EMAIL", DEFAULT_EMAIL)

    row = fetch_member_status(day_str, nickname)
    if not row:
        msg = f"[{day_str}] Lulu打卡提醒任务完成：未找到昵称 {nickname}，本次已跳过。"
        print(f"[{now_ts()}] [lulu-reminder] skip: user nickname '{nickname}' not found")
        send_owner_notice(msg)
        return 0

    checked_in = int(row["checked_in"] or 0)
    phone = row["phone"] or ""

    if checked_in == 1:
        msg = f"[{day_str}] Lulu打卡提醒任务完成：lulu({phone}) 已完成打卡，本次未发送邮件。"
        print(f"[{now_ts()}] [lulu-reminder] ok: {nickname}({phone}) already checked in on {day_str}")
        send_owner_notice(msg)
        return 0

    subject = f"[语言打卡提醒] {day_str} 今天还没完成打卡"
    body = (
        f"Hi {nickname},\n\n"
        f"现在是 {day_str}，你今天还没有完成语言打卡。\n"
        "请抽 30-50 分钟完成今日任务：\n"
        "1) 听力输入 10 分钟\n"
        "2) 句型卡练习 15 分钟\n"
        "3) 强制开口 15 分钟（建议录音）\n\n"
        "打卡页面：\n"
        f"{os.getenv('LANGUAGE_CHECKIN_SITE_URL', DEFAULT_SITE_URL)}\n\n"
        "— Openclaw 自动提醒"
    )

    code, out = send_email(to_email, subject, body)
    if code == 0:
        msg = f"[{day_str}] Lulu打卡提醒任务完成：lulu({phone}) 未打卡，已发送提醒邮件到 {to_email}。"
        print(f"[{now_ts()}] [lulu-reminder] sent: {nickname}({phone}) -> {to_email}")
        send_owner_notice(msg)
        return 0

    msg = f"[{day_str}] Lulu打卡提醒任务失败：lulu({phone}) 未打卡，但提醒邮件发送失败（{to_email}）。"
    print(f"[{now_ts()}] [lulu-reminder] error: failed to send email to {to_email}")
    if out:
        print(out)
    send_owner_notice(msg)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
