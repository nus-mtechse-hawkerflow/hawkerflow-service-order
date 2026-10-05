# hawkerflow-service-order

The HawkerFlow **order service**: takes diner and counter orders, splits each order into one
sub-order per stall, tracks each stall's preparation status, and publishes an event every time an
order is placed or its status changes.

FastAPI + SQLModel on PostgreSQL, with Amazon SQS and SNS for messaging (LocalStack when run
locally). The other HawkerFlow services (hawker, customer, analytics) and the two web apps
(`hawker-ui`, `diner-ui`) live in their own repositories.

## How orders flow

```
diner-ui ──POST /orders/queue──▶ order_queue (SQS) ──▶ SQS worker ──┐
                                                                    ├─▶ PostgreSQL ──▶ hawker-ui (polls)
hawker-ui (counter) ──POST /orders──────────────────────────────────┘        │
                                                                              ▼
hawker-ui ──PATCH status──▶ order service ──▶ order_status (SNS) ──▶ notifications_queue (SQS)
```

- **Queued intake (diner app).** `POST /v1/order/orders/queue` puts an `ORDER_PLACED` message on
  `order_queue` and answers **202** with an `order_ref`. The background worker creates the order.
  The diner polls `GET /v1/order/orders/queue/{order_ref}`: **202 PENDING** until the order exists,
  then **200** with its `order_id`. SQS delivers at least once, so a redelivered message with a
  known `order_ref` returns the existing order instead of creating a second one.
- **Direct intake (hawker counter).** `POST /v1/order/orders` creates the order immediately.
- **Status changes.** Hawkers move each stall sub-order through
  `PENDING → PREPARING → READY → COMPLETED` (or `CANCELLED`); the parent order's status follows.

## Events

Published to the `order_status` SNS topic, which delivers to `notifications_queue`. One event per
stall sub-order:

| Event | When |
|---|---|
| `OrderPlaced` | an order is created (either intake path) |
| `OrderPreparing`, `OrderReady`, `OrderCompleted`, `OrderCancelled` | a stall changes its sub-order's status |
| `OrderCancelled` with `"reason": "NOT_ACCEPTED_IN_TIME"` | the expiry worker cancels a sub-order no stall accepted in time |
| `OrderCompleted` with `"reason": "NOT_COLLECTED_BY_DAY_END"` | the expiry worker completes a ready sub-order left from an earlier day |

```json
{
  "event_type": "OrderReady",
  "event_id": "…uuid…",
  "timestamp": "2026-09-27T09:37:56.381+00:00",
  "data": { "stall_order_id": 131, "order_id": 110, "stall_id": 1, "status": "READY", "subtotal": 9.5 }
}
```

- **Nothing consumes `notifications_queue` yet.** The diner app learns about status changes by
  polling `GET /v1/order/orders/{order_id}` every 10 seconds. The queue is where a future
  notification consumer (web push, SMS) would read from.
- **Order events by `timestamp`, not arrival.** A standard SQS queue does not guarantee order.

## API

All paths are served under `service.root_path` (`/hawkerflow`), e.g.
`http://localhost:8082/hawkerflow/v1/order/orders`. Interactive docs: `/hawkerflow/docs`.

| Method | Path | Used by | Purpose |
|---|---|---|---|
| `POST` | `/v1/order/orders/queue` | diner-ui | Queue an order; **202** with `order_ref` (**503** if SQS is disabled) |
| `GET` | `/v1/order/orders/queue/{order_ref}` | diner-ui | **202 PENDING**, then **200** with `order_id` |
| `POST` | `/v1/order/orders` | hawker-ui counter | Create an order immediately |
| `GET` | `/v1/order/orders/{order_id}` | diner-ui tracker | Order with items, status, `dining_option`, `takeaway_fee` |
| `PUT` | `/v1/order/orders/update` | hawker-ui | Set the parent order's status |
| `GET` | `/v1/order/stalls/me/orders` | hawker-ui | The signed-in stall's orders (`?status=pending` to filter) |
| `GET` | `/v1/order/stalls/{stall_id}/orders` | hawker-ui | Same, for a given stall (must match the caller's stall) |
| `PATCH` | `/v1/order/stalls/{stall_id}/orders/{order_id}` | hawker-ui | Change a stall's sub-order status; publishes the event |
| `GET` | `/v1/order/sqs/status` | diagnostics | SQS worker health and message count |

**Order body** (both intake paths):

```json
{
  "orders": [{ "stall_id": 1, "dishes": [{ "dish_id": 1, "dish_name": "Steamed Chicken Rice", "quantity": 1, "price": 4.5 }] }],
  "total_price": 4.8,
  "dining_option": "takeaway",
  "takeaway_fee": 0.3
}
```

`dining_option` (`dine_in` | `takeaway`, both self-collect) and `takeaway_fee` are optional and
default to dine-in with no fee.

## Data model

| Table | Holds |
|---|---|
| `orders` | One row per checkout: total, status, created time |
| `stall_orders` | One row per stall in the order: that stall's status and subtotal |
| `order_items` | The dishes, linked to the order and its stall sub-order |
| `order_requests` | `order_ref` → `order_id` for queued orders (also detects redelivered messages) |
| `order_options` | `dining_option` and `takeaway_fee` per order |

Tables are created by `SQLModel.metadata.create_all` at startup; there are no migrations. That is
why new data goes into new tables: `create_all` adds a table to an existing database but never
adds a column to an existing table.

## Configuration

Settings are read from `<PROJECT_ROOT>/resources/config.yml`, and secrets from
`<PROJECT_ROOT>/vault/` (`postgres.user`, `postgres.password`). `PROJECT_ROOT` defaults to the
current directory. Point it at a copy outside the repository to keep local settings out of git.

| Section | Key settings |
|---|---|
| `service` | host, port (8082), `root_path`, CORS origins |
| `datasource` | PostgreSQL host, port, database `hawkerflow_order_db` |
| `sqs` | `enabled`, `queue_url` of `order_queue`, `region_name`, `endpoint_url` (LocalStack) |
| `events` | `enabled`, `topic_arn` of `order_status`, `region_name`, `endpoint_url` |

Setting `sqs.enabled: false` turns off both the queue worker and `POST /orders/queue` (which then
answers 503).

## Run locally

Prerequisites: Python 3.12, PostgreSQL with a `hawkerflow_order_db` database, and LocalStack.

1. Create the messaging resources in LocalStack (once; match the region to your `config.yml`):
   ```bash
   aws --endpoint-url http://localhost:4566 sqs create-queue --queue-name order_queue
   aws --endpoint-url http://localhost:4566 sqs create-queue --queue-name notifications_queue
   aws --endpoint-url http://localhost:4566 sns create-topic --name order_status
   aws --endpoint-url http://localhost:4566 sns subscribe --topic-arn <topic ARN> \
     --protocol sqs --notification-endpoint <notifications_queue ARN>
   ```
2. Install and start:
   ```bash
   python -m venv .venv && .venv/Scripts/activate      # Windows; source .venv/bin/activate elsewhere
   pip install -r requirements.txt -r requirements-dev.txt
   PYTHONPATH=src python src/main.py
   ```
   The service creates its tables, starts the SQS worker, and listens on
   `http://127.0.0.1:8082/hawkerflow`.

## Tests

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest -q
```

Tests run against in-memory SQLite with SQS and SNS clients mocked; no database or LocalStack
needed.

## Known gaps

Fine for local development; to fix before any public deployment:

- **Authentication.** Stall endpoints accept an `X-Stall-ID` header from any caller, and the
  bearer token's claims are read without verifying its signature.
- **CORS** allows every origin (`allow_origins: '*'`).
- `POST /v1/order/sqs/simulate` calls an async handler without `await`, so it returns before
  processing the message.
