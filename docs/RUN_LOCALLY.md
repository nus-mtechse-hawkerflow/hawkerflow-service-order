# Run HawkerFlow Locally — Step by Step

Runs the **entire backend and both web apps on your machine**. No AWS account, no credentials,
no Docker. Takes about 5 minutes the first time.

Use this to develop, to rehearse the presentation demo, and to onboard a teammate before anyone
touches AWS.

---

## Step 1 — Install Python 3.12

```bash
python3 --version
```

You need **3.12 or newer**. If not:

- **macOS:** `brew install python@3.12`
- **Windows:** download from <https://www.python.org/downloads/> and tick *Add python.exe to PATH*
- **Ubuntu/WSL:** `sudo apt update && sudo apt install python3.12 python3.12-venv`

> Windows users: run every command below in **Git Bash** or **WSL**, not PowerShell — the
> Makefile targets assume a POSIX shell.

✅ **Checkpoint:** `python3 --version` prints 3.12.x or higher.

---

## Step 2 — Get the code and enter the folder

```bash
unzip hawkerflow-repo.zip     # or: git clone <your repo>
cd hawkerflow
```

✅ **Checkpoint:** `ls` shows `Makefile`, `infra`, `services`, `apps`, `scripts`, `tests`.

---

## Step 3 — Create a virtual environment (recommended)

Keeps the project's packages separate from your system Python.

```bash
python3 -m venv .venv
source .venv/bin/activate         # Windows Git Bash: source .venv/Scripts/activate
```

✅ **Checkpoint:** your prompt now starts with `(.venv)`.

---

## Step 4 — Install dependencies

```bash
make install
```

This installs `boto3`, `pytest`, `moto`, `ruff`, `cfn-lint` from `requirements-dev.txt`
(no `make`? run `pip install -r requirements-dev.txt` instead).

✅ **Checkpoint:** finishes without red errors.

---

## Step 5 — Run the tests (proves your setup is correct)

```bash
make test
```

✅ **Checkpoint:** `35 passed`. If this passes, every service's logic works on your machine —
before a single line of infrastructure exists.

Optional: `make lint` should print `All checks passed!`.

---

## Step 6 — Start the local server

```bash
make local
```

You should see:

```
seeded 3 stalls owned by owner@hawkerflow.demo

HawkerFlow running locally on http://localhost:8000

  Demo launcher http://localhost:8000/demo/    <- start here, one click per persona
  Diner app     http://localhost:8000/diner/
  Stall portal  http://localhost:8000/stall/

  API auth      send header 'authorization: local-diner'  (consumer)
                            'authorization: local-owner'  (producer)
  ...
  Dashboard     GET  /_local/metrics    request counts by service/status, order counts, DLQ depth

  In-memory only - restart resets to seed data. Ctrl-C to stop.
```

**Leave this terminal running.** Everything below happens in a browser or a second terminal.

> Port 8000 already in use? Start it on another port: `PORT=8080 python scripts/local_server.py`

✅ **Checkpoint:** the banner appears and the terminal does not return to a prompt.

---

## Step 7 — Open the apps

**Fastest way — the demo launcher:** open <http://localhost:8000/demo/>. It's a one-page
launcher: click **Customer 1** (opens the diner app in a new tab, already signed in — no
form) and **Open stall portal** (same idea, as the owner). Click **Customer 2**, **3**, **4**
too if you want several diners ordering at once, each in its own tab. The launcher also has a
**live dashboard** at the top — see Step 8b.

<details>
<summary>Manual sign-in instead (closer to how the real Cognito-backed apps work)</summary>

Open **two browser windows side by side**:

| Window | URL | Sign in as |
|---|---|---|
| Left | <http://localhost:8000/diner/> | Diner (consumer) |
| Right | <http://localhost:8000/stall/> | Stall owner (producer) |

Click **Sign in** on each. The email and password are pre-filled and **ignored in local mode** —
the server issues a stub token instead of calling Cognito, so any values work.
</details>

✅ **Checkpoint:** the diner app lists *Ah Hock Chicken Rice*, *Mei's Laksa Corner*, and
*Guo's Satay*. The stall portal shows all three in the **My stalls** dropdown (one owner runs
all of them here — pick whichever stall you're demoing from the dropdown).

---

## Step 8 — Walk the full order flow

Do these in order and watch both windows:

1. **Diner:** click **Order** on *Ah Hock Chicken Rice*.
2. **Diner:** press **+** on "Chicken rice" twice — the total updates to `$9.00`.
3. **Diner:** click **Checkout**. A payment screen appears with three fake methods
   (Card / PayNow QR / Cash on collection) — pick one and click **Pay $9.00**. A brief
   "Processing payment..." spinner runs (~900 ms, simulated — no real gateway), then a
   green success checkmark, then it returns you to the stall menu. The order now appears
   under **My orders** with the badge `PLACED`.
4. **Stall portal:** within 5 seconds the order appears in the **Order queue**
   (the queue polls automatically). Click **accept**.
5. **Diner:** within 5 seconds the badge changes to `ACCEPTED`, and a message appears under
   **Notifications**.
6. **Stall portal:** click **preparing**, then **ready**.
7. **Diner:** the badge reaches `READY` and the notification reads
   *"Your order at Ah Hock Chicken Rice is ready for collection!"*
8. **Stall portal:** click **collected**.
9. **Stall portal:** the **Analytics (last 7 days)** table now shows today with 1 order and
   `$9.00` revenue.

Meanwhile the server terminal prints each pipeline hop:

```
  POST /v1/orders -> 201
    pipeline -> OrderPlaced (3f9a21c8) delivered to ['notification', 'analytics']
  PATCH /v1/orders/3f9a... -> 200
    pipeline -> OrderAccepted (3f9a21c8) delivered to ['notification', 'analytics']
```

✅ **Checkpoint:** you completed `PLACED → ACCEPTED → PREPARING → READY → COLLECTED` and
analytics incremented. That is the whole platform working end to end on your laptop.

---

## Step 8b — Watch orders come in on the dashboard

Back on <http://localhost:8000/demo/>, the **Live dashboard** card polls the server every 2s:
total requests, 2xx count, DLQ depth, a service-health badge, a **requests-over-time chart**
(1-minute buckets, last 4 hours, x-axis in actual Singapore time — hover it for a crosshair
+ exact count per minute),
requests-by-service, orders-by-status, orders-by-stall, an "Orders coming in" feed (one line
per order — stall, items, amount, status), and a raw request log.

Click **▶ Simulate incoming orders** to generate traffic without clicking through the diner
app yourself: every 5s it fires a real randomized batch of **1–100 concurrent orders**
(random customer, random stall, random items) straight at `POST /v1/orders` — not simulated
client-side, an actual burst of requests the server has to handle. Click **■ Stop simulating**
when you're done — it runs indefinitely otherwise.

✅ **Checkpoint:** the "Orders coming in" feed and the per-service bars update within a
couple of seconds of clicking the button. The chart's rightmost (current) minute climbs on
every 2s poll as batches land in it; a clear step in the line shows up once that minute
closes and the next one starts accumulating.

---

## Step 9 — Try the API directly (second terminal)

```bash
# public - no authentication needed
curl localhost:8000/v1/centres/maxwell/stalls

# as a diner (consumer)
curl localhost:8000/v1/me/orders -H "authorization: local-diner"

# as a stall owner (producer)
curl localhost:8000/v1/stalls/ahhock-cr/orders -H "authorization: local-owner"

# protected route with no token -> 401
curl -i localhost:8000/v1/me/orders
```

**Prove idempotency** — the same key twice creates one order:

```bash
IDEM=$(uuidgen)
for i in 1 2; do
  curl -s -o /dev/null -w "attempt $i -> HTTP %{http_code}\n" \
    -X POST localhost:8000/v1/orders \
    -H "authorization: local-diner" -H "content-type: application/json" \
    -H "idempotency-key: $IDEM" \
    -d '{"stallId":"ahhock-cr","items":[{"itemId":"cr","qty":1}]}'
done
```

✅ **Checkpoint:** `attempt 1 -> HTTP 201` then `attempt 2 -> HTTP 200`. One order, not two.

---

## Step 10 — Rehearse the fault-isolation demo

This is the microservices moment of your presentation. Practise it here first.

```bash
# 1. break ONE services
curl -X POST "localhost:8000/_local/break?service=notification"

# 2. place 2-3 orders in the diner app - they still succeed
# 3. check what happened
curl localhost:8000/_local/status
```

Expected: `{"broken": ["notification"], "dlq_depth": {"notification": 3, "analytics": 0}}`

Orders still return 201, the stall queue still works, analytics still counts — only the broken
service's messages parked in its dead-letter queue. Then repair and recover:

```bash
curl -X POST "localhost:8000/_local/repair?service=notification"
curl -X POST "localhost:8000/_local/redrive?service=notification"
```

✅ **Checkpoint:** the diner's **Notifications** panel fills in with the messages that were
delayed. Nothing was lost.

---

## Step 11 — Stop and reset

Press **Ctrl-C** in the server terminal. All data is in memory, so restarting with `make local`
returns you to clean seed data — useful between demo rehearsals.

---

## What local mode does *not* prove

Local runs everything in **one process**, so it cannot demonstrate:

- reserved concurrency and the per-service bulkheads
- independent scaling (no per-function CloudWatch curves)
- real Cognito tokens, IAM permissions, API Gateway throttling
- genuine SQS visibility timeouts, real DLQ alarms, Streams shard parallelism, cold starts

Those need a real deployment — see `docs/SETUP.md` Parts 3–7, then `make scale-demo` and
`make fault-demo` for the evidence that goes in the report.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError: moto` | Virtual environment not activated, or `make install` not run |
| `Address already in use` | Something else holds port 8000 — `PORT=8080 python scripts/local_server.py` |
| Apps load but the stall list is empty | You opened `apps/diner/index.html` as a file. Use the **http://localhost:8000/diner/** URL so `CONFIG` is rewritten |
| Sign-in spins or errors | Same cause as above — the app is trying to reach real Cognito because it wasn't served by the local server |
| `401` on every API call | Add the header `authorization: local-diner` (or `local-owner`) |
| `403` when editing a menu | You're signed in as the diner; stall actions need `local-owner` |
| Orders vanish after restart | Expected — storage is in memory by design |
| Status doesn't update in the app | The apps poll every 5–7 s (AD-08). Wait a moment or refresh |
