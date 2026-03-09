#!/usr/bin/env python3
"""Generate a redacted public demo bundle from the live project data.

Outputs:
- public_demo/checkin.public.db
- public_demo/manifest.json
- recordings/public/*.wav
- static/public-avatars/*.svg
"""

from __future__ import annotations

import json
import math
import shutil
import sqlite3
import struct
import wave
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SRC_DB = BASE_DIR / "checkin.db"
OUT_DIR = BASE_DIR / "public_demo"
OUT_DB = OUT_DIR / "checkin.public.db"
MANIFEST = OUT_DIR / "manifest.json"
PUBLIC_RECORDINGS_DIR = BASE_DIR / "recordings" / "public"
PUBLIC_AVATARS_DIR = BASE_DIR / "static" / "public-avatars"

PALETTE = [
    ("#6f8dff", "#eaf0ff"),
    ("#31d08c", "#e8fff5"),
    ("#ff8f6b", "#fff1eb"),
    ("#9b7bff", "#f2edff"),
    ("#f0b429", "#fff7df"),
]


def ensure_dirs() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    PUBLIC_RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    PUBLIC_AVATARS_DIR.mkdir(parents=True, exist_ok=True)


def reset_public_dirs() -> None:
    for path in [PUBLIC_RECORDINGS_DIR, PUBLIC_AVATARS_DIR]:
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)


def write_avatar(path: Path, label: str, primary: str, background: str) -> None:
    svg = f"""<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 128 128\" role=\"img\" aria-label=\"{label}\">\n  <rect width=\"128\" height=\"128\" rx=\"28\" fill=\"{background}\"/>\n  <circle cx=\"64\" cy=\"46\" r=\"24\" fill=\"{primary}\" opacity=\"0.88\"/>\n  <path d=\"M28 104c4-22 20-34 36-34s32 12 36 34\" fill=\"{primary}\" opacity=\"0.88\"/>\n  <text x=\"64\" y=\"118\" text-anchor=\"middle\" font-family=\"Arial, sans-serif\" font-size=\"12\" fill=\"#384152\">{label}</text>\n</svg>\n"""
    path.write_text(svg, encoding="utf-8")


def write_demo_wav(path: Path, duration_sec: float = 2.0, freq: float = 440.0) -> None:
    sample_rate = 16000
    amplitude = 0.18
    total_frames = max(1, int(sample_rate * duration_sec))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = bytearray()
        for i in range(total_frames):
            fade = min(1.0, i / 800.0, (total_frames - i) / 800.0)
            val = int(32767 * amplitude * fade * math.sin(2 * math.pi * freq * i / sample_rate))
            frames.extend(struct.pack("<h", val))
        wf.writeframes(bytes(frames))


def copy_db(src: Path, dst: Path) -> None:
    if dst.exists():
        dst.unlink()
    src_conn = sqlite3.connect(src)
    try:
        dst_conn = sqlite3.connect(dst)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()


def sanitize_db() -> dict:
    conn = sqlite3.connect(OUT_DB)
    conn.row_factory = sqlite3.Row
    manifest: dict = {"users": [], "recordings": [], "notes": []}
    try:
        users = conn.execute("SELECT rowid, * FROM users ORDER BY phone").fetchall()
        phone_map: dict[str, str] = {}

        for idx, user in enumerate(users, start=1):
            old_phone = user["phone"]
            anon_phone = f"user_{idx:03d}"
            phone_map[old_phone] = anon_phone
            primary, background = PALETTE[(idx - 1) % len(PALETTE)]
            avatar_rel = f"/static/public-avatars/{anon_phone}.svg"
            nickname = f"Demo User {idx}"
            slogan = f"Public demo member {idx}"
            bio = "Sanitized public demo profile."
            write_avatar(PUBLIC_AVATARS_DIR / f"{anon_phone}.svg", f"U{idx}", primary, background)
            conn.execute(
                """
                UPDATE users
                SET phone=?, nickname=?, pin_hash=?, avatar_url=?, slogan=?, bio=?, theme_color=?
                WHERE rowid=?
                """,
                (anon_phone, nickname, "public-demo-redacted", avatar_rel, slogan, bio, primary, user["rowid"]),
            )
            manifest["users"].append(
                {
                    "phone": anon_phone,
                    "nickname": nickname,
                    "avatar_url": avatar_rel,
                    "theme_color": primary,
                }
            )

        for old_phone, anon_phone in phone_map.items():
            conn.execute("UPDATE member_progress SET phone=? WHERE phone=?", (anon_phone, old_phone))

        recordings = conn.execute("SELECT rowid, * FROM recordings ORDER BY created_at, id").fetchall()
        for idx, rec in enumerate(recordings, start=1):
            anon_phone = phone_map.get(rec["phone"], f"user_{idx:03d}")
            file_rel = f"public/{anon_phone}-recording-{idx:03d}.wav"
            rec_id = f"public-rec-{idx:03d}"
            duration = 2.0 + ((idx - 1) % 2) * 0.6
            write_demo_wav(BASE_DIR / "recordings" / file_rel, duration_sec=duration, freq=440.0 + idx * 35)
            conn.execute(
                "UPDATE recordings SET id=?, phone=?, file_rel=?, duration_sec=? WHERE rowid=?",
                (rec_id, anon_phone, file_rel, duration, rec["rowid"]),
            )
            manifest["recordings"].append(
                {
                    "id": rec_id,
                    "phone": anon_phone,
                    "file_rel": file_rel,
                    "duration_sec": duration,
                }
            )

        conn.commit()
        conn.execute("VACUUM")
        manifest["notes"].append("phones, nicknames, avatars, recordings, and pin hashes were redacted")
        return manifest
    finally:
        conn.close()


def main() -> int:
    if not SRC_DB.exists():
        raise SystemExit(f"source db not found: {SRC_DB}")

    ensure_dirs()
    reset_public_dirs()
    copy_db(SRC_DB, OUT_DB)
    manifest = sanitize_db()
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_DB}")
    print(f"Wrote {MANIFEST}")
    print(f"Prepared {PUBLIC_RECORDINGS_DIR}")
    print(f"Prepared {PUBLIC_AVATARS_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
