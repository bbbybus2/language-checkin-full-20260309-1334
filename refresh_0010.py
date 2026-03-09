#!/usr/bin/env python3
import json
import sqlite3
from datetime import date, datetime
import app


def parse_json_safe(s: str, default):
    try:
        return json.loads(s or "")
    except Exception:
        return default


def validate_prefetch_row(day_str: str, row):
    if not row:
        return False, "no prefetch row"
    if row["day"] != day_str:
        return False, "day mismatch"
    cards = parse_json_safe(row["sentence_cards_material"] or "[]", [])
    speaking = parse_json_safe(row["forced_speaking_material"] or "{}", {})
    if not row["listening_url"]:
        return False, "listening_url empty"
    if not isinstance(cards, list) or len(cards) == 0:
        return False, "cards invalid"
    if not isinstance(speaking, dict) or len(speaking) == 0:
        return False, "speaking invalid"
    return True, "ok"


def apply_prefetch(db, day_str: str, row):
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
            row["sentence_cards_material"] or "[]",
            row["forced_speaking_material"] or "{}",
            now,
            now,
        ),
    )
    db.execute(
        "UPDATE material_prefetch SET status='applied', applied_at=?, updated_at=? WHERE day=?",
        (now, now, day_str),
    )
    db.commit()


def fallback_apply(db, day_str: str):
    listening, cards, speaking = app.build_daily_materials(day_str + "-0010-fallback")
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
            listening.get("title", "今日听力素材"),
            listening.get("url", ""),
            listening.get("thumb", ""),
            listening.get("source", "fallback"),
            listening.get("desc", ""),
            json.dumps(cards, ensure_ascii=False),
            json.dumps(speaking, ensure_ascii=False),
            now,
            now,
        ),
    )
    db.commit()


def main():
    day_str = date.today().isoformat()

    db = sqlite3.connect(app.DB_PATH)
    db.row_factory = sqlite3.Row
    try:
        row = db.execute("SELECT * FROM material_prefetch WHERE day=?", (day_str,)).fetchone()
        ok, msg = validate_prefetch_row(day_str, row)
        if ok:
            apply_prefetch(db, day_str, row)
            print(
                f"[refresh_0010] applied source=prefetch-db day={day_str} "
                f"seed={row['seed'] or '-'} sig={row['signature'] or '-'}"
            )
        else:
            fallback_apply(db, day_str)
            print(f"[refresh_0010] applied source=fallback day={day_str} reason={msg}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
