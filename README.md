# Supabase Backup

Automated PostgreSQL backup service for Supabase. Runs on a schedule, compresses and AES-256 encrypts backups, stores them to any mounted directory, and sends Discord notifications on success or failure.

## What gets backed up

- All schemas (public, auth, storage, cron, realtime, etc.)
- All table data
- Indexes, views, functions, triggers, sequences, constraints
- RLS policies and privileges (GRANT/REVOKE)
- Global roles and permissions (`pg_dumpall --globals-only`)

## Stack

- Python 3.12
- `pg_dump` / `pg_dumpall` (PostgreSQL 17 client)
- GPG (AES-256 symmetric encryption)
- `schedule` library for in-process cron

## Retention

| Setting | Default | Description |
|---|---|---|
| `KEEP_DAILY` | 7 | One backup per day for 7 days |
| `KEEP_WEEKLY` | 4 | One backup per week for 4 weeks |
| `KEEP_MONTHLY` | 3 | One backup per month for 3 months |

## Setup

### 1. Clone and configure

```bash
git clone https://github.com/streamwizard/supabase-backup.git
cd supabase-backup
cp .env.example .env
nano .env
```

### 2. Configure `.env`

```env
# Supabase connection — password separate to avoid URL encoding issues with special characters
SUPABASE_URL=postgresql://postgres.YOUR_PROJECT_REF@aws-0-eu-central-1.pooler.supabase.com:5432/postgres
PGPASSWORD=your_supabase_database_password

# Discord webhook for success/failure notifications
DISCORD_WEBHOOK=https://discord.com/api/webhooks/...

# AES-256 encryption passphrase — store this in your password manager
# Without this passphrase backups cannot be decrypted
BACKUP_PASSPHRASE=your_strong_passphrase
```

### 3. Configure backup destination

By default backups are written to `/backups` inside the container. Mount any local directory or network share in `docker-compose.yml`:

```yaml
volumes:
  - /your/backup/path:/backups
```

### 4. Start

```bash
docker compose up -d
```

### 5. Trigger a manual test run

```bash
docker compose exec supabase-backup python -c "from backup import run_backup; run_backup()"
```

## Configuration

| Variable | Default | Description |
|---|---|---|
| `SUPABASE_URL` | required | Postgres connection string (without password) |
| `PGPASSWORD` | required | Supabase database password |
| `DISCORD_WEBHOOK` | required | Discord webhook URL |
| `BACKUP_PASSPHRASE` | required | GPG encryption passphrase |
| `SCHEDULE_TIME` | `01:00` | Daily run time (24h format) |
| `KEEP_DAILY` | `7` | Daily backups to keep |
| `KEEP_WEEKLY` | `4` | Weekly backups to keep |
| `KEEP_MONTHLY` | `3` | Monthly backups to keep |
| `BACKUP_DIR` | `/backups` | Backup destination inside container |

## File format

Backups are saved as `streamwizard-YYYY-MM-DD_HH-MM.sql.gz.gpg` — gzip compressed then AES-256 encrypted.

## Restoring a backup

```bash
# Decrypt and decompress
gpg --decrypt streamwizard-2026-06-01_01-00.sql.gz.gpg | zcat > restore.sql

# Restore to a temp Postgres container
docker run --name temp-db -e POSTGRES_PASSWORD=test -p 5432:5432 -d postgres:17
docker exec -i temp-db psql -U postgres < restore.sql

# Clean up
docker rm -f temp-db
rm restore.sql
```

## GDPR notes

- Backups are AES-256 encrypted at rest
- Passphrase is passed via file descriptor, never exposed in process arguments
- Retention is capped at 3 months by default
