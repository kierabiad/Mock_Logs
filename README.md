# Mock Logs

A Django logging and analytics project with Docker, PostgreSQL, Redis, Celery, and factory_boy test data generation.

## What Is Tracked In Git

- Source code
- Django app files
- Exported CSV log files
- README and project scripts

## What Stays Local

- `.envs/.local/.gmail`
- Other machine-specific secrets
- Virtual environments and build caches

## Step-by-Step Setup

### 1. Clone the repository

```bash
git clone https://github.com/developer/mock_logs.git
cd mock_logs
```

### 2. Create the local Gmail secret file

Create the local file and fill in real values on your machine only.

```bash
touch .envs/.local/.gmail
```

Edit `.envs/.local/.gmail` and set:

```bash
GMAIL_SENDER_EMAIL=your_real_gmail_address@gmail.com
GMAIL_APP_PASSWORD=your_16_character_google_app_password
```

### 3. Start Docker services

```bash
docker compose -f docker-compose.local.yml up --build
```

This starts Django, PostgreSQL, Redis, and Celery.

### 4. Run database migrations

In a second terminal:

```bash
docker compose -f docker-compose.local.yml exec django python manage.py migrate
```

### 5. Create a Django superuser

```bash
docker compose -f docker-compose.local.yml exec django python manage.py createsuperuser
```

### 6. Check service status

```bash
docker compose -f docker-compose.local.yml ps
```

## Open the App

- App: http://localhost:8000
- Admin: http://localhost:8000/admin

## Gmail SMTP Setup

Use Gmail App Passwords only.

1. Turn on 2-Step Verification on the Google account that will send mail.
2. Create a Google App Password.
3. Put the sender email and app password in `.envs/.local/.gmail`.
4. Do not commit `.envs/.local/.gmail`.

## Export Logs and Email Them

To export the current database rows and send them through Gmail:

```bash
docker compose -f docker-compose.local.yml exec django python export_consolidated_logs_and_send_gmail.py --send-email --rows-per-csv 32000 --db-chunk-size 2000
```

Explicit Gmail SMTP command:

```bash
docker compose -f docker-compose.local.yml exec django python export_consolidated_logs_and_send_gmail.py --send-email --rows-per-csv 32000 --db-chunk-size 2000 --smtp-host smtp.gmail.com --smtp-port 587
```

Notes:
- Logs are exported separately by source.
- CSV chunks default to 32,000 rows.
- The email body contains the summary.
- The script will use your local `.envs/.local/.gmail` file automatically.

If you only want to export files and not send email:

```bash
docker compose -f docker-compose.local.yml exec django python export_consolidated_logs_and_send_gmail.py --rows-per-csv 32000 --db-chunk-size 2000
```

## Populate Dummy Data (factory_boy + PostgreSQL)

This project uses PostgreSQL as the database and uses factory_boy to generate dummy logs.

```bash
docker compose -f docker-compose.local.yml exec django python manage.py populate_logs --Primary-count 9000 --Secondary-count 110000 --batch-size 1000
```

## Service Notes

- Django runs on port 8000
- PostgreSQL and Redis are started by Docker Compose
- Celery workers are included in the local compose file

## Project Scripts

- `scripts/export_consolidated_logs_and_send_gmail.py` exports logs, zips them, and sends email
- `export_consolidated_logs_and_send_gmail.py` is a root-level launcher for the same script

## Common Commands

```bash
docker compose -f docker-compose.local.yml ps
docker compose -f docker-compose.local.yml logs -f django
docker compose -f docker-compose.local.yml exec django python manage.py shell
```
