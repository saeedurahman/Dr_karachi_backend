# Karachi Lab, Pharmacy & Clinics Backend

FastAPI backend providing patient appointments, pharmacy orders, lab reports, and WhatsApp Cloud API integrations.

---

## Running the Services

### 1. Web API Server
In development:
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
In production (as configured in Dockerfile):
```bash
alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 4
```

---

### 2. WhatsApp Appointment Reminder Worker

The appointment reminder worker runs as an independent, decoupled background worker process:
```bash
python -m app.worker
```

#### Why a Separate Worker Process?
In production, the web API runs 4 Uvicorn worker processes (`--workers 4`). Running recurring polling loops inside FastAPI's lifespan events would spawn 4 concurrent polling loops inside each web server process. To prevent redundant polling overhead and keep background processing decoupled from web request lifecycles, the reminder worker runs as a dedicated worker process.

#### Deploying on Railway as a Second Service
To run the reminder worker alongside the web API in Railway:
1. In the Railway dashboard, navigate to your project and click **New Service** -> **GitHub Repo** (select this repository).
2. Set the **Root Directory** to `backend`.
3. In the service **Settings** -> **Deploy**, set the custom **Start Command**:
   ```bash
   python -m app.worker
   ```
4. Copy/link the exact same environment variables from the web service (e.g. `DATABASE_URL`, `WHATSAPP_*`, etc.).
5. Railway will run this service as a continuous worker (no HTTP exposed).

#### Worker Configuration Settings
The worker behavior can be tuned via the following environment variables:

| Variable | Default | Description |
|---|---|---|
| `WHATSAPP_REMINDER_POLL_SECONDS` | `60` | Loop polling interval in seconds |
| `WHATSAPP_REMINDER_WINDOW_START_MINUTES` | `55` | Minutes ahead of appointment slot for window start |
| `WHATSAPP_REMINDER_WINDOW_END_MINUTES` | `65` | Minutes ahead of appointment slot for window end |
| `WHATSAPP_REMINDER_BATCH_SIZE` | `50` | Maximum appointments locked & processed per tick |

#### Reliability & Concurrency Guarantees
- **Row-level locking**: Uses `SELECT ... FOR UPDATE SKIP LOCKED` inside a transaction so multiple worker instances never process or conflict on the same appointment.
- **Idempotency**: Every outbound message is logged with a unique deduplication key: `appointment:{id}:reminder_1h`. If a message log with this key already exists, sending is skipped silently.
- **Pre-send validation**: Before sending, the worker verifies that the appointment status is still `confirmed` and the slot datetime is strictly in the future. Rescheduled or cancelled appointments never receive a reminder.
- **Fault-tolerant loop**: Transient errors, WhatsApp API failures, or consent blocks on an appointment are recorded via `mark_failed` without crashing the polling loop.
