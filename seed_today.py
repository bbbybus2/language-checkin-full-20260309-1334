#!/usr/bin/env python3
from datetime import date
from app import app, get_db, ensure_day_row, refresh_day_materials

with app.app_context():
    db = get_db()
    day = date.today()
    row = ensure_day_row(db, day)
    refresh_day_materials(db, row["day"], seed_suffix="-0010")
    print("ok")
