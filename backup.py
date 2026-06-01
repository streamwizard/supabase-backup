import os
import subprocess
import gzip
import json
from datetime import datetime, timedelta
from pathlib import Path

import requests
import schedule
import time

SUPABASE_URL = os.environ["SUPABASE_URL"]
PGPASSWORD = os.environ["PGPASSWORD"]
DISCORD_WEBHOOK = os.environ["DISCORD_WEBHOOK"]
BACKUP_PASSPHRASE = os.environ["BACKUP_PASSPHRASE"]
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", "/backups"))
SCHEDULE_TIME = os.environ.get("SCHEDULE_TIME", "01:00")
STATUS_FILE = BACKUP_DIR / ".backup_status.json"

KEEP_DAILY = int(os.environ.get("KEEP_DAILY", "7"))
KEEP_WEEKLY = int(os.environ.get("KEEP_WEEKLY", "4"))
KEEP_MONTHLY = int(os.environ.get("KEEP_MONTHLY", "3"))


def write_status(success: bool, message: str, size_mb: float = 0):
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(json.dumps({
        "success": success,
        "message": message,
        "size_mb": size_mb,
        "timestamp": datetime.now().isoformat(),
    }))


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
    now = datetime.now()
    all_backups = sorted(BACKUP_DIR.glob("streamwizard-*.sql.gz.gpg"), reverse=True)

    keep = set()

    seen_days = set()
    for f in all_backups:
        try:
            dt = datetime.strptime(f.name.replace("streamwizard-", "").replace(".sql.gz.gpg", ""), "%Y-%m-%d_%H-%M")
        except ValueError:
            continue
        day_key = dt.date()
        if (now.date() - day_key).days <= KEEP_DAILY and day_key not in seen_days:
            keep.add(f)
            seen_days.add(day_key)

    seen_weeks = set()
    for f in all_backups:
        try:
            dt = datetime.strptime(f.name.replace("streamwizard-", "").replace(".sql.gz.gpg", ""), "%Y-%m-%d_%H-%M")
        except ValueError:
            continue
        week_key = dt.isocalendar()[:2]
        cutoff = now - timedelta(weeks=KEEP_WEEKLY)
        if dt >= cutoff and week_key not in seen_weeks:
            keep.add(f)
            seen_weeks.add(week_key)

    seen_months = set()
    for f in all_backups:
        try:
            dt = datetime.strptime(f.name.replace("streamwizard-", "").replace(".sql.gz.gpg", ""), "%Y-%m-%d_%H-%M")
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
    gz_file = BACKUP_DIR / f"streamwizard-{date}.sql.gz"
    output_file = BACKUP_DIR / f"streamwizard-{date}.sql.gz.gpg"

    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)

        # full schema + data + RLS policies + privileges
        result = subprocess.run(
            ["pg_dump", "--no-password", SUPABASE_URL],
            capture_output=True,
            env={**os.environ, "PGPASSWORD": PGPASSWORD},
        )

        if result.returncode != 0:
            raise RuntimeError(result.stderr.decode())

        # global roles and permissions
        roles_result = subprocess.run(
            ["pg_dumpall", "--globals-only", "--no-password", "-d", SUPABASE_URL],
            capture_output=True,
            env={**os.environ, "PGPASSWORD": PGPASSWORD},
        )

        raw_data = b""
        if roles_result.returncode == 0:
            raw_data += roles_result.stdout
        raw_data += result.stdout

        raw_size_mb = len(raw_data) / 1024 / 1024

        # compress
        gz_data = gzip.compress(raw_data)

        # encrypt with AES-256 — passphrase passed via fd to keep it out of argv
        pp_read_fd, pp_write_fd = os.pipe()
        os.write(pp_write_fd, BACKUP_PASSPHRASE.encode() + b"\n")
        os.close(pp_write_fd)

        gpg_result = subprocess.run(
            [
                "gpg", "--batch", "--yes",
                "--passphrase-fd", str(pp_read_fd),
                "--pinentry-mode", "loopback",
                "--symmetric", "--cipher-algo", "AES256",
                "--output", str(output_file),
            ],
            input=gz_data,
            capture_output=True,
            pass_fds=(pp_read_fd,),
        )
        os.close(pp_read_fd)

        if gpg_result.returncode != 0:
            raise RuntimeError(f"GPG encryption failed: {gpg_result.stderr.decode()}")

        size_mb = output_file.stat().st_size / 1024 / 1024

        if size_mb < 0.01:
            raise RuntimeError("Backup is suspiciously small (< 10KB) — possible empty backup")

        removed = prune_backups()
        remaining = len(list(BACKUP_DIR.glob("*.sql.gz.gpg")))

        write_status(True, f"Backup succeeded — {size_mb:.2f} MB encrypted, {remaining} total kept", size_mb)

        send_discord(
            "✅ Supabase Backup Succeeded",
            f"**File:** `{output_file.name}`\n"
            f"**Size (raw):** {raw_size_mb:.2f} MB\n"
            f"**Size (compressed+encrypted):** {size_mb:.2f} MB\n"
            f"**Encrypted:** AES-256\n"
            f"**Retention:** {KEEP_DAILY}d daily / {KEEP_WEEKLY}w weekly / {KEEP_MONTHLY}m monthly\n"
            f"**Pruned:** {len(removed)} backup(s) — {remaining} total kept",
            True,
        )
        print(f"[{datetime.now()}] Backup completed: {output_file} ({size_mb:.2f} MB), pruned {len(removed)}")

    except Exception as e:
        for f in [gz_file, output_file]:
            if f.exists():
                f.unlink()
        write_status(False, str(e))
        send_discord("❌ Supabase Backup Failed", f"**Error:** {e}", False)
        print(f"[{datetime.now()}] Backup failed: {e}")


schedule.every().day.at(SCHEDULE_TIME).do(run_backup)

if __name__ == "__main__":
    print(f"Backup scheduler started — running daily at {SCHEDULE_TIME}")
    print(f"Retention: {KEEP_DAILY} daily / {KEEP_WEEKLY} weekly / {KEEP_MONTHLY} monthly")
    while True:
        schedule.run_pending()
        time.sleep(60)
