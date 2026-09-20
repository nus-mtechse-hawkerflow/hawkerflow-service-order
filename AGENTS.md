# AGENT.md — Developer & AI Agent Guide for `hawkerflow-service-order`

Welcome to **`hawkerflow-service-order`**, the order management microservice of the **HawkerFlow** food ordering platform. This document provides a complete technical map, architectural guide, and operational instructions for engineers and AI agents working on this codebase.

> [!IMPORTANT]
> ### 🚨 Mandatory Rule for AI Agents: Always Check `graphify-out/` First
> **BEFORE starting any task, making code changes, refactoring, or answering architectural questions:**
> 1. **Inspect `graphify-out/`**: Always check the knowledge graph in [`graphify-out/`](file:///Users/wenjiefang/Documents/Development/hawkerflow-service-order/graphify-out/):
>    - [`graphify-out/GRAPH_REPORT.md`](file:///Users/wenjiefang/Documents/Development/hawkerflow-service-order/graphify-out/GRAPH_REPORT.md): Review **God Nodes** (e.g., `OrderService`, `OrderRepo`, `SqsWorker`, `EventPublisher`), **Community Hubs**, and **Surprising Connections** to understand system topology and blast radius before altering any code.
>    - [`graphify-out/graph.json`](file:///Users/wenjiefang/Documents/Development/hawkerflow-service-order/graphify-out/graph.json): Raw multi-graph data containing all AST and semantic relationships.
> 2. **Query the Graph Before Assuming**: For any question regarding data flow, dependencies, or architectural interactions, query the existing graph first:
>    ```bash
>    /graphify query "<your question about relationships or data flow>"
>    ```
> 3. **Incremental Graph Updates**: If you introduce new files or modify architecture, update the graph using `/graphify . --update`.

---

## 1. System Overview & Architecture

`hawkerflow-service-order` is a standalone Python 3.12+ microservice built using **FastAPI**, **SQLModel / SQLAlchemy**, and **Boto3 (AWS SQS/SNS)**.

### Primary Responsibilities:
- **Diner Order Ingestion**: Accepts multi-stall food orders from web/mobile diners or inbound SQS checkout queues.
- **Multi-Store Hawker Isolation**: Partitions diner checkout baskets into stall-specific sub-orders (`StallOrder`), guaranteeing hawkers only view, prepare, and manage dishes for their own stall.
- **Event-Driven Messaging**:
  - **Inbound Poller**: Background worker (`SqsWorker`) long-polling AWS SQS for order placement and status update messages.
  - **Outbound Publisher**: Asynchronous event publisher (`EventPublisher`) emitting domain events (`OrderReady`, `OrderCollected`) to AWS SNS/SQS when a stall's order is ready for pickup.

---

## 2. Directory Structure

```text
hawkerflow-service-order/
├── Makefile                      # Make targets (lint, test, build, deploy)
├── README.md                     # Platform overview & deployment notes
├── AGENT.md                      # Engineering & AI Agent technical reference (this file)
├── pyproject.toml                # Project metadata & build tool configuration
├── requirements.txt              # Production runtime dependencies
├── requirements-dev.txt          # Development, linting & test dependencies
├── infra/
│   ├── template.yaml             # AWS SAM Infrastructure as Code (SQS, DLQs, DynamoDB, API Gateway)
│   ├── samconfig.toml            # SAM deployment configurations
│   └── SECURITY_BASELINE.md      # IaC security audit justifications
├── resources/
│   └── config.yml                # Main application YAML configuration
├── vault/                        # File-based secrets directory
│   ├── postgres.user             # Database username secret
│   └── postgres.password         # Database password secret
├── src/
│   ├── main.py                   # Application entrypoint & FastAPI server bootstrap
│   ├── worker.py                 # Standalone SQS worker CLI entrypoint (python -m src.worker)
│   ├── configurations/
│   │   └── app_config.py         # Pydantic Settings (Service, Datasource, SqsConfig, EventsConfig)
│   ├── dependencies/
│   │   └── auth.py               # Authentication & multi-tenant isolation dependencies (get_current_stall_id)
│   ├── drivers/                  # Pluggable database connection drivers
│   │   ├── driver.py             # Abstract DB driver interface
│   │   ├── postgres_driver.py    # PostgreSQL + psycopg2 driver
│   │   └── sqlite_driver.py      # SQLite driver for local dev & testing
│   ├── endpoints/
│   │   └── order_routes.py       # REST API endpoints (diner, stall-isolated, and SQS diagnostics)
│   ├── entities/                 # SQLModel / SQLAlchemy database tables
│   │   ├── order.py              # Order entity (parent diner checkout transaction)
│   │   ├── stall_order.py        # StallOrder entity (stall-specific partitioned sub-order)
│   │   └── order_item.py         # OrderItem entity (individual dish items)
│   ├── factory/                  # Factory patterns for database drivers
│   │   ├── database_factory.py
│   │   └── driver_factory.py
│   ├── lifecycle/
│   │   └── lifespan.py           # FastAPI lifespan context manager (DB init, worker start/stop)
│   ├── models/                   # Pydantic request/response schemas (DTOs)
│   │   ├── order_details.py      # OrderDetails, Order, Dish DTOs
│   │   ├── order_update.py       # Global OrderUpdate DTO
│   │   └── stall_order_update.py # StallOrderUpdate DTO
│   ├── repository/
│   │   └── order_repo.py         # Data access layer (order persistence & stall-scoped queries)
│   ├── services/
│   │   ├── order_service.py      # Core business logic orchestrator
│   │   └── event_publisher.py    # Asynchronous AWS SNS/SQS event publisher
│   ├── session/
│   │   └── db_session.py         # SQLAlchemy engine manager
│   └── workers/
│       └── sqs_worker.py         # Async SQS long-polling background worker
└── tests/
    ├── test_order_isolation.py   # Multi-store isolation, access control & status tests
    ├── test_sqs_worker.py        # SQS poller parsing, retry, and deletion tests
    └── test_event_publisher.py   # OrderReady event publishing tests (SNS and SQS)
```

---

## 3. Core Domain Model & Multi-Store Isolation

### Entity Relationships

```
┌────────────────────────────────────────────────────────┐
│                        Order                           │
│   f_id (PK), f_total_price, f_status, f_created_at     │
└───────────────────────────┬────────────────────────────┘
                            │ 1:N
           ┌────────────────┴────────────────┐
           ▼                                 ▼
┌───────────────────────┐         ┌───────────────────────┐
│      StallOrder       │         │      StallOrder       │
│ f_id, f_stall_id: 101 │         │ f_id, f_stall_id: 202 │
│ f_status, f_subtotal  │         │ f_status, f_subtotal  │
└──────────┬────────────┘         └──────────┬────────────┘
           │ 1:N                             │ 1:N
           ▼                                 ▼
┌───────────────────────┐         ┌───────────────────────┐
│       OrderItem       │         │       OrderItem       │
│ f_dish_id: 1, qty: 2  │         │ f_dish_id: 3, qty: 1  │
│ f_price: 5.50         │         │ f_price: 8.00         │
└───────────────────────┘         └───────────────────────┘
```

1. **`Order`** (`orders` table): Represents the customer's entire checkout transaction across all stalls. Tracks total price, global created timestamp, and high-level status (`PENDING`, `IN_PROGRESS`, `READY`, `COMPLETED`).
2. **`StallOrder`** (`stall_orders` table): Partitioned sub-order belonging strictly to one `f_stall_id`. Contains that stall's independent preparation lifecycle (`PENDING` -> `PREPARING` -> `READY` -> `COMPLETED`) and subtotal.
3. **`OrderItem`** (`order_items` table): Stores individual dishes with quantities and unit prices, linked to both the parent `Order` and the specific `StallOrder`.

### Multi-Tenant Query Scoping:
- **Never query without `f_stall_id` for hawker operations**:
  ```python
  # Correct: Strictly scoped by stall
  statement = select(StallOrder).where(StallOrder.f_stall_id == stall_id)
  ```
- Hawkers only retrieve items and subtotals intended for their stall.

---

## 4. Authentication & Security Guardrails

Tenant isolation is enforced in [`src/dependencies/auth.py`](file:///Users/wenjiefang/Documents/Development/hawkerflow-service-order/src/dependencies/auth.py):

1. **`get_current_stall_id`**: Extracts stall identity from:
   - Header: `X-Stall-ID: <integer>` (useful for microservice calls and local testing).
   - Header: `Authorization: Bearer <jwt>` (reads claims `custom:stall_id` or `stall_id`).
   - Missing or invalid credentials return `401 Unauthorized`.
2. **`verify_stall_access(requested_stall_id, authenticated_stall_id)`**:
   - Ensures `requested_stall_id == authenticated_stall_id`.
   - Any cross-tenant access attempt returns:
     ```json
     {
       "detail": "Forbidden: You are not authorized to view or manage orders for stall <id>"
     }
     ```
     *(HTTP 403 Forbidden)*.

---

## 5. AWS Messaging Architecture

### A. Inbound Consumer (`SqsWorker`)
- Located in [`src/workers/sqs_worker.py`](file:///Users/wenjiefang/Documents/Development/hawkerflow-service-order/src/workers/sqs_worker.py).
- **Long Polling**: Uses `WaitTimeSeconds=20` (or configured value) to eliminate empty receives and reduce AWS costs.
- **Non-blocking Execution**: Sync `boto3` network calls run in thread pools via `asyncio.to_thread`.
- **Fault Tolerance**:
  - Successfully processed messages are deleted via `delete_message`.
  - Failed messages are **never deleted**, allowing AWS SQS to retry them and eventually transfer them to the Dead-Letter Queue (DLQ).
- **Dual Deployment Options**:
  - **In-process**: Runs automatically inside FastAPI's event loop via [`src/lifecycle/lifespan.py`](file:///Users/wenjiefang/Documents/Development/hawkerflow-service-order/src/lifecycle/lifespan.py) when `sqs.enabled: true`.
  - **Standalone**: Can run as an independent worker container/process:
    ```bash
    python -m src.worker
    ```

### B. Outbound Publisher (`EventPublisher`)
- Located in [`src/services/event_publisher.py`](file:///Users/wenjiefang/Documents/Development/hawkerflow-service-order/src/services/event_publisher.py).
- When a stall updates preparation status to `READY`:
  ```bash
  PATCH /v1/order/stalls/{stall_id}/orders/{order_id} {"status": "READY"}
  ```
  `OrderService.update_stall_order_status` triggers `EventPublisher.publish_order_ready(...)`.
- Publishes an `OrderReady` event to an AWS SNS topic (fan-out) or AWS SQS notification queue:
  ```json
  {
    "event_type": "OrderReady",
    "event_id": "uuid4",
    "timestamp": "ISO-8601-UTC",
    "data": {
      "stall_order_id": 1,
      "order_id": 10,
      "stall_id": 101,
      "status": "READY",
      "subtotal": 12.50
    }
  }
  ```

### C. LocalStack Auto-Detection
If `queue_url` or `endpoint_url` contains `localhost`, `127.0.0.1`, or `localstack`:
- Boto3 clients automatically configure `endpoint_url="https://localhost.localstack.cloud:4566"`.
- Default dummy credentials (`"test"` / `"test"`) are injected if no AWS environment variables exist, preventing `NoCredentialsError`.
- SSL verification is bypassed (`verify=False`) to support local self-signed certificates.

---

## 6. REST API Endpoints

All routes are prefixed by `/v1/order` (and mounted under `service.root_path = /hawkerflow` in `config.yml`).

| Method | Path | Auth Required | Description |
| :--- | :--- | :--- | :--- |
| `POST` | `/v1/order/orders` | None (Diner) | Submit a new order (can span multiple stalls). |
| `GET` | `/v1/order/orders/{order_id}` | None (Diner) | Retrieve full order details and all items. |
| `PUT` | `/v1/order/orders/update` | None / Internal | Update global order status. |
| `GET` | `/v1/order/stalls/me/orders` | `X-Stall-ID` / Bearer | Fetch orders strictly for the authenticated hawker. |
| `GET` | `/v1/order/stalls/{stall_id}/orders` | `X-Stall-ID` / Bearer | Fetch orders for `stall_id` (verified against token). |
| `PATCH`| `/v1/order/stalls/{stall_id}/orders/{order_id}` | `X-Stall-ID` / Bearer | Update stall sub-order status (triggers `OrderReady` event). |
| `GET` | `/v1/order/sqs/status` | None | Live diagnostic status of the SQS background worker. |
| `POST` | `/v1/order/sqs/simulate` | None | Directly process an SQS event payload without AWS. |

---

## 7. Configuration Reference (`resources/config.yml`)

The application loads configuration using Pydantic Settings in [`src/configurations/app_config.py`](file:///Users/wenjiefang/Documents/Development/hawkerflow-service-order/src/configurations/app_config.py):

```yaml
service:
  title: HawkerFlow Service Order
  root_path: '/hawkerflow'
  port: 8082
  host: '127.0.0.1'
  allow_origins: ['*']
  methods: ['*']
  headers: ['*']
  docs_url: '/docs'
  redoc_url: '/redoc'
  reload: true
  scheme: 'http'
  credentials: true

datasource:
  driver:
    package: "drivers.postgres_driver"
    driver_class: "PostgresDriver"
  database:
    connection_url: 'sqlite:///database.db'
    driver_name: 'postgresql+psycopg2'
    name: 'hawkerflow_order_db'
    host: 'localhost'
    port: 5432
  options:
    echo: false

sqs:
  enabled: true
  queue_url: "https://localhost.localstack.cloud:4566/000000000000/order_queue"
  region_name: "us-east-1"
  endpoint_url: "https://localhost.localstack.cloud:4566"
  wait_time_seconds: 5
  max_number_of_messages: 10
  visibility_timeout: 60

events:
  enabled: true
  notification_queue_url: "https://localhost.localstack.cloud:4566/000000000000/notifications_queue"
  region_name: "us-east-1"
  endpoint_url: "https://localhost.localstack.cloud:4566"
```

> [!NOTE]
> Database credentials for PostgreSQL are read from the `vault/` directory:
> - `vault/postgres.user`
> - `vault/postgres.password`

---

## 8. Development & Testing Runbook

### Prerequisites
- Python 3.12+ (or 3.14)
- Virtual environment in `.venv/`

### Run Unit Tests
Always run tests before committing code:
```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

### Start the FastAPI Application
```bash
PYTHONPATH=src .venv/bin/python src/main.py
```
- Swagger UI will be available at: `http://127.0.0.1:8082/hawkerflow/docs`
- Diagnostics endpoint: `http://127.0.0.1:8082/hawkerflow/v1/order/sqs/status`

### Start the Standalone SQS Worker
If deploying the poller independently:
```bash
PYTHONPATH=src .venv/bin/python -m src.worker
```

---

## 9. Common Troubleshooting

| Issue | Root Cause | Solution |
| :--- | :--- | :--- |
| **`403 Forbidden: You are not authorized...`** | Mismatch between `stall_id` in URL and credentials in `X-Stall-ID` / Bearer token. | Match `X-Stall-ID` to the URL `stall_id`, or use `/v1/order/stalls/me/orders`. |
| **`401 Unauthorized`** | Missing `X-Stall-ID` header or `Authorization: Bearer <jwt>`. | Provide stall credentials in request headers. |
| **`NoCredentialsError`** | Boto3 cannot find AWS credentials when connecting to AWS. | Set `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`, or run `aws configure`. (For LocalStack, dummy credentials `"test"` are injected automatically). |
| **`EndpointConnectionError`** | LocalStack is not running or port 4566 is unreachable. | Start LocalStack (`localstack start`) or verify the container is active. |
| **PostgreSQL Connection Error** | PostgreSQL server on `localhost:5432` is offline. | Start PostgreSQL, or switch `datasource.driver` to `drivers.sqlite_driver` / `SqliteDriver` in `config.yml` for local development. |
