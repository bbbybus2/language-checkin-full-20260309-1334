#!/usr/bin/env python3
import json
import hashlib
import sqlite3
from datetime import date, timedelta, datetime
import app


def make_signature(listening: dict, cards: list, speaking: dict):
    raw = json.dumps(
        {
            "listening_url": listening.get("url", ""),
            "cards": cards,
            "speaking": speaking,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def read_today_signature(db_path: str):
    day = date.today().isoformat()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT listening_url, sentence_cards_material, forced_speaking_material FROM daily_status WHERE day=?",
            (day,),
        ).fetchone()
        if not row:
            return None
        cards = json.loads(row["sentence_cards_material"] or "[]")
        speaking = json.loads(row["forced_speaking_material"] or "{}")
        listening = {"url": row["listening_url"] or ""}
        return make_signature(listening, cards, speaking)
    finally:
        conn.close()


def build_distinct_payload(day_str: str, today_sig: str | None):
    # 先用标准 seed，若与今日素材签名重复则自动换 seed 再生成，确保“预拉取有变化”。
    candidates = [
        f"{day_str}-0010",
        f"{day_str}-0010-v2",
        f"{day_str}-0010-v3",
        f"{day_str}-0010-v4",
        f"{day_str}-0010-v5",
    ]

    chosen = None
    for seed in candidates:
        listening, cards, speaking = app.build_daily_materials(seed)
        sig = make_signature(listening, cards, speaking)
        if today_sig and sig == today_sig:
            continue
        chosen = (seed, listening, cards, speaking, sig)
        break

    if not chosen:
        seed = candidates[-1]
        listening, cards, speaking = app.build_daily_materials(seed)
        sig = make_signature(listening, cards, speaking)
        chosen = (seed, listening, cards, speaking, sig)

    seed, listening, cards, speaking, sig = chosen
    return {
        "day": day_str,
        "listening": listening,
        "cards": cards,
        "speaking": speaking,
        "prefetched_at": datetime.now().isoformat(timespec="seconds"),
        "seed": seed,
        "signature": sig,
    }


def save_to_db(payload: dict):
    conn = sqlite3.connect(app.DB_PATH)
    conn.row_factory = sqlite3.Row
    now = datetime.now().isoformat(timespec="seconds")
    try:
        listening = payload["listening"]
        conn.execute(
            """
            INSERT INTO material_prefetch(
                day, listening_title, listening_url, listening_thumb, listening_source, listening_desc,
                sentence_cards_material, forced_speaking_material,
                seed, signature, status, prefetched_at, applied_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'prefetched', ?, NULL, ?, ?)
            ON CONFLICT(day) DO UPDATE SET
                listening_title=excluded.listening_title,
                listening_url=excluded.listening_url,
                listening_thumb=excluded.listening_thumb,
                listening_source=excluded.listening_source,
                listening_desc=excluded.listening_desc,
                sentence_cards_material=excluded.sentence_cards_material,
                forced_speaking_material=excluded.forced_speaking_material,
                seed=excluded.seed,
                signature=excluded.signature,
                status='prefetched',
                prefetched_at=excluded.prefetched_at,
                applied_at=NULL,
                updated_at=excluded.updated_at
            """,
            (
                payload["day"],
                listening.get("title", "今日听力素材"),
                listening.get("url", ""),
                listening.get("thumb", ""),
                listening.get("source", "prefetch"),
                listening.get("desc", ""),
                json.dumps(payload["cards"], ensure_ascii=False),
                json.dumps(payload["speaking"], ensure_ascii=False),
                payload.get("seed", ""),
                payload.get("signature", ""),
                payload["prefetched_at"],
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def main():
    target_day = date.today() + timedelta(days=1)
    day_str = target_day.isoformat()

    today_sig = read_today_signature(app.DB_PATH)
    payload = build_distinct_payload(day_str, today_sig)
    save_to_db(payload)

    print(
        f"[prefetch_0010] ok day={day_str} seed={payload['seed']} sig={payload['signature']} db=material_prefetch"
    )


if __name__ == "__main__":
    main()
