import os
import subprocess
import gzip
from datetime import datetime, timedelta
from pathlib import Path

import requests
import schedule
import time

SUPABASE_URL = os.environ["SUPABASE_URL"]
DISCORD_WEBHOOK = os.environ["DISCORD_WEBHOOK"]
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", "/backups"))
SCHEDULE_TIME = os.environ.get("SCHEDULE_TIME", "01:00")

KEEP_DAILY = int(os.environ.get("KEEP_DAILY", "7"))
KEEP_WEEKLY = int(os.environ.get("KEEP_WEEKLY", "4"))
KEEP_MONTHLY = int(os.environ.get("KEEP_MONTHLY", "3"))


def send_discord(title: str, message: str, success: bool):
    color = 0x57F287 if success else 0xED4245
    requests.post(DISCORD_WEBHOOK, json={
        "embeds": [{
            "title": title,
            "description": message,
            "color": color,
            "footer": {"text": "Supabase Backup"},
            "timestamp": datetime.utcnow().isoformat(),
        }]
    }, timeout=10)


def prune_backups():
    """
    Retain:
      - one backup per day for the last KEEP_DAILY days
      - one backup per week for the last KEEP_WEEKLY weeks
      - one backup per month for the last KEEP_MONTHLY months
    Everything else is deleted.
    """
    now = datetime.now()
    all_backups = sorted(BACKUP_DIR.glob("streamwizard-*.sql.gz"), reverse=True)

    keep = set()

    # daily — one per day for last N days
    seen_days = set()
    for f in all_backups:
        try:
            dt = datetime.strptime(f.stem.replace("streamwizard-", "").replace(".sql", ""), "%Y-%m-%d_%H-%M")
        except ValueError:
            continue
        day_key = dt.date()
        if (now.date() - day_key).days <= KEEP_DAILY and day_key not in seen_days:
            keep.add(f)
            seen_days.add(day_key)

    # weekly — one per ISO week for last N weeks
    seen_weeks = set()
    for f in all_backups:
        try:
            dt = datetime.strptime(f.stem.replace("streamwizard-", "").replace(".sql", ""), "%Y-%m-%d_%H-%M")
        except ValueError:
            continue
        week_key = dt.isocalendar()[:2]
        cutoff = now - timedelta(weeks=KEEP_WEEKLY)
        if dt >= cutoff and week_key not in seen_weeks:
            keep.add(f)
            seen_weeks.add(week_key)

    # monthly — one per month for last N months
    seen_months = set()
    for f in all_backups:
        try:
            dt = datetime.strptime(f.stem.replace("streamwizard-", "").replace(".sql", ""), "%Y-%m-%d_%H-%M")
        except ValueError:
            continue
        month_key = (dt.year, dt.month)
        cutoff = now - timedelta(days=KEEP_MONTHLY * 30)
        if dt >= cutoff and month_key not in seen_months:
            keep.add(f)
            seen_months.add(month_key)

    removed = []
    for f in all_backups:
        if f not in keep:
            f.unlink()
            removed.append(f.name)

    return removed


def run_backup():
    print(f"[{datetime.now()}] Starting backup...")
    date = datetime.now().strftime("%Y-%m-%d_%H-%M")
    output_file = BACKUP_DIR / f"streamwizard-{date}.sql.gz"

    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)

        result = subprocess.run(
            ["pg_dump", SUPABASE_URL],
            capture_output=True,
        )

        if result.returncode != 0:
            raise RuntimeError(result.stderr.decode())

        with gzip.open(output_file, "wb") as f:
            f.write(result.stdout)

        size_mb = output_file.stat().st_size / 1024 / 1024

        if size_mb < 0.01:
            raise RuntimeError("Dump is suspiciously small (< 10KB) — possible empty backup")

        removed = prune_backups()
        remaining = len(list(BACKUP_DIR.glob("*.sql.gz")))

        send_discord(
            "✅ Supabase Backup Succeeded",
            f"**File:** `{output_file.name}`\n"
            f"**Size:** {size_mb:.2f} MB\n"
            f"**Retention:** {KEEP_DAILY}d daily / {KEEP_WEEKLY}w weekly / {KEEP_MONTHLY}m monthly\n"
            f"**Pruned:** {len(removed)} backup(s) — {remaining} total kept",
            True,
        )
        print(f"[{datetime.now()}] Backup completed: {output_file} ({size_mb:.2f} MB), pruned {len(removed)}")

    except Exception as e:
        if output_file.exists():
            output_file.unlink()
        send_discord("❌ Supabase Backup Failed", f"**Error:** {e}", False)
        print(f"[{datetime.now()}] Backup failed: {e}")


schedule.every().day.at(SCHEDULE_TIME).do(run_backup)

if __name__ == "__main__":
    print(f"Backup scheduler started — running daily at {SCHEDULE_TIME}")
    print(f"Retention: {KEEP_DAILY} daily / {KEEP_WEEKLY} weekly / {KEEP_MONTHLY} monthly")
    while True:
        schedule.run_pending()
        time.sleep(60)
