import os
import subprocess
import gzip
from datetime import datetime
from pathlib import Path

import requests
import schedule
import time

SUPABASE_URL = os.environ["SUPABASE_URL"]
DISCORD_WEBHOOK = os.environ["DISCORD_WEBHOOK"]
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", "/backups"))
KEEP_DAYS = int(os.environ.get("KEEP_DAYS", "30"))
SCHEDULE_TIME = os.environ.get("SCHEDULE_TIME", "01:00")


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

        # Cleanup old backups
        cutoff = datetime.now().timestamp() - (KEEP_DAYS * 86400)
        removed = [f for f in BACKUP_DIR.glob("*.sql.gz") if f.stat().st_mtime < cutoff]
        for f in removed:
            f.unlink()

        send_discord(
            "✅ Supabase Backup Succeeded",
            f"**File:** `{output_file.name}`\n**Size:** {size_mb:.2f} MB\n**Pruned:** {len(removed)} old backup(s)",
            True,
        )
        print(f"[{datetime.now()}] Backup completed: {output_file} ({size_mb:.2f} MB)")

    except Exception as e:
        if output_file.exists():
            output_file.unlink()
        send_discord("❌ Supabase Backup Failed", f"**Error:** {e}", False)
        print(f"[{datetime.now()}] Backup failed: {e}")


schedule.every().day.at(SCHEDULE_TIME).do(run_backup)

if __name__ == "__main__":
    print(f"Backup scheduler started — running daily at {SCHEDULE_TIME}")
    while True:
        schedule.run_pending()
        time.sleep(60)
