> **Superseded design.** This was the repository README when HawkerFlow was one serverless
> platform (Lambda + DynamoDB + SAM). The team has since split it into per-service FastAPI +
> PostgreSQL repositories; this repo is now only the order service. Kept unchanged for the
> project report's design history. For the current service, see the [README](../README.md).

# HawkerFlow

Hawker centre food ordering **platform** — the working system behind the SWE5001 practice-module
report. Serverless, pay-per-request, all Python 3.12: idle cost is **$0**, and the lunch-hour spike
is absorbed by Lambda/DynamoDB autoscaling.

**Seed:** one hawker centre's order-ahead app · **Producers:** stall owners (portal) · **Consumers:** diners (web app)

## Architecture (one paragraph)

Static web apps on **S3 + CloudFront** → **Cognito** issues JWTs → **API Gateway (HTTP API)**
validates them and routes to per-service **Lambda** functions → **DynamoDB** single table
(on-demand) is the store → **DynamoDB Streams** feed a dispatcher that publishes domain events to
**SQS** queues (with DLQs) consumed by the notification and analytics functions. Everything is
defined in **AWS SAM** and deployed by **GitHub Actions** via OIDC. Full rationale and trade-offs:
see the project report (AD-01 … AD-10).

## Repository layout

| Path | What lives here |
|---|---|
| `infra/template.yaml` | The entire stack (table, queues, Cognito, API, 6 functions, CloudFront) |
| `services/<name>/src/` | One Lambda service each: `handler.py` (transport) / `domain.py` (pure logic) / `repo.py` (data access) |
| `services/shared/` | Lambda layer with the small shared HTTP helper library |
| `apps/diner`, `apps/stall` | Single-file web apps (static, no build step) |
| `apps/demo` | Local-only demo launcher + live dashboard (see below) — not part of the deploy pipeline |
| `tests/` | pytest: pure domain tests + repository tests against moto-mocked DynamoDB |
| `.github/workflows/` | `ci.yml` (ruff, tests, pip-audit, bandit, gitleaks, sam-validate) — `deploy.yml` (dev → approval → prod) is a planned follow-up |
| `loadtest/order_flow.js` | k6 model: 70 RPS browse + 30 RPS orders for 10 min |
| `scripts/` | `seed_data.py` (demo users, stalls, menus), `smoke.py` (post-deploy check) |
| `docs/openapi.yaml` | The platform API contract |
| `docs/RUN_LOCALLY.md` | Step-by-step local run with checkpoints |
| `docs/LOCAL_DEV_NOTES.md` | Engineering reference for `local_server.py` and the `/demo/` launcher/dashboard internals |
| `docs/SETUP.md` | AWS account → deploy → CI/CD → load-test evidence → teardown |
| `docs/QA_PREP.md` | Presentation Q&A prep, glossary, ownership map |
| `infra/SECURITY_BASELINE.md` | Every accepted IaC security finding, with justification |

## Quickstart (from zero to running system)

> **Full walkthrough — start here if any step below is unfamiliar:** [docs/SETUP.md](docs/SETUP.md)
> covers AWS account creation, the OIDC pipeline, load-test evidence capture and troubleshooting.

Prerequisites: an AWS account (new accounts get **US$100–200 credits**), AWS CLI configured,
[SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html),
Python 3.12.

### Run it locally first (no AWS account, no Docker)

```bash
make install
make local        # http://localhost:8000/demo/  <- start here
```

The demo launcher (`/demo/`) gives one-click, no-login access to 4 diner personas and the
stall owner, plus a live dashboard (request counts, orders-by-stall, a "simulate incoming
orders" button). Or go straight to `/diner/` and `/stall/` for the normal sign-in flow.

> **Full walkthrough:** [docs/RUN_LOCALLY.md](docs/RUN_LOCALLY.md) — step-by-step with
> checkpoints, the demo script, and troubleshooting.

The whole backend runs in one process against an in-memory DynamoDB (moto): real handler,
domain and repository code, real dispatcher and consumers driven synchronously in place of
Streams + SQS, and a stub authorizer standing in for Cognito. State resets on restart.
Auth for direct API calls: `authorization: local-diner` or `authorization: local-owner`.
Fault-injection endpoints (`/_local/break`, `/_local/status`, `/_local/repair`, `/_local/redrive`)
let you rehearse the fault-isolation demo before running it on AWS.

### Deploy to AWS

```bash
# 0. Guardrail FIRST - S$5 budget alarm (email-alert version: docs/SETUP.md Part 1)
aws budgets create-budget --account-id <ACCOUNT_ID> --budget \
  '{"BudgetName":"hawkerflow","BudgetLimit":{"Amount":"5","Unit":"USD"},"TimeUnit":"MONTHLY","BudgetType":"COST"}'

# 1. Install dev tooling, run the checks the pipeline runs
make install && make lint && make test

# 2. Deploy the dev stack (~3 min)
make deploy-dev

# 3. Seed demo users + two stalls with menus
make seed        # prints ApiUrl / ClientId and the demo logins

# 4. Wire the apps: paste ApiUrl + ClientId into the CONFIG block of
#    apps/diner/index.html and apps/stall/index.html, then either
#    open them locally...
python -m http.server 8000 --directory apps
#    ...or publish them behind CloudFront:
aws s3 sync apps/ s3://<WebBucketName from outputs>/

# 5. Verify
make smoke
```

Demo logins (created by the seed script): `diner@hawkerflow.demo` and `owner@hawkerflow.demo`,
password `HawkerDemo1!`.

## CI/CD setup (once per repo)

1. Create an IAM role for GitHub OIDC (trust `token.actions.githubusercontent.com`, condition on
   your `org/repo`) with permissions to deploy the stack. No access keys are ever stored.
2. In GitHub: add repo **variable** `AWS_DEPLOY_ROLE_ARN`, and create environments `dev` and
   `production` — add a **required reviewer** on `production` (this is the manual approval gate).
3. Push to `main`: CI runs lint/tests/scans → deploy to dev → smoke + **integration test**
   (full order lifecycle; requires the one-time `make seed` on dev) + **OWASP ZAP baseline** →
   wait for approval → deploy to prod → smoke test → **alarm-gated bake window**.

## Load test (the scalability demonstration)

```bash
# Get a diner IdToken (sign in via the app and copy it, or use scripts in README-notes)
k6 run loadtest/order_flow.js \
  -e API_URL=<ApiUrl> -e ID_TOKEN=<IdToken> -e STALL_ID=ahhock-cr -e ITEM_ID=cr
```

Watch Lambda `ConcurrentExecutions` and API latency in CloudWatch while it runs: scaling is
automatic, thresholds assert p95 ≤ 300 ms (reads) / 500 ms (orders), error rate < 1%.
A 10-minute run at 100 RPS costs roughly **US$3–5** (the only above-free-tier spend in the project).

## Cost guardrails (why this stays ~$0/month)

No always-on resources exist — no NAT gateway, no load balancer, no Kubernetes, no RDS.
Lambda (1M req), DynamoDB (25 GB), SQS (1M req) sit inside AWS **always-free** monthly tiers;
CloudFront within the 1 TB free transfer tier. Log retention is pinned to **14 days in the
template**, and five CloudWatch alarms per stage (API 5xx, both DLQs, ordering and dispatcher
errors) deploy with the stack - within the 10 always-free alarms. Keep the S$5 budget alarm on.

## Tear down

```bash
sam delete --stack-name hawkerflow-dev --region ap-southeast-1
sam delete --stack-name hawkerflow-prod --region ap-southeast-1
```

(Empty the web bucket first if you synced the apps to it.)
