# HawkerFlow — Complete Setup Guide

Zero to a running, load-tested platform with a working CI/CD pipeline.
Follow the parts **in order** — later parts assume earlier ones are done.

| Part | What you get | Time |
|---|---|---|
| 1 | Secured AWS account with credits + budget alarm | ~20 min |
| 2 | Local tools installed, tests passing | ~15 min |
| 3 | `dev` stack deployed and seeded | ~10 min |
| 4 | Both web apps live behind CloudFront | ~15 min |
| 5 | GitHub CI/CD pipeline (OIDC, no stored keys) | ~25 min |
| 6 | `prod` deployed through the pipeline | ~10 min |
| 7 | Load test run + evidence captured for the report | ~30 min |
| 8 | Teardown (when the module ends) | ~5 min |

Commands are written for **bash** (macOS/Linux). On Windows, use **Git Bash** or **WSL**.

---

## Part 1 — AWS account (one per team)

Use **one AWS account for the whole team** — one free-credit pool, one bill to watch.

1. **Create the account** at <https://aws.amazon.com> → *Create an AWS account*.
   Choose the **Free plan** when asked. New accounts receive **US$100 in credits**
   at sign-up and can earn **up to US$100 more** by completing the console's
   onboarding activities (setting a budget is one of them — you'll do it in step 5).
   > The Free plan auto-closes after 6 months or when credits run out (with a 90-day
   > grace period) — signing up in early August covers you through the 16 Nov report.

2. **Secure the root user.** Console → *IAM* → *Add MFA* on the root account.
   Then stop using root day-to-day.

3. **Create your working identity.** Console → *IAM* → *Users* → *Create user*
   (e.g. `teamadmin`) → attach **AdministratorAccess** → enable MFA → create an
   **access key** (choose *Command Line Interface*). Save the key ID + secret once —
   they are shown only at creation. Repeat per team member, or share one admin user
   for simplicity (acceptable for a course project).

4. **Install and configure the AWS CLI** (v2): <https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html>

   ```bash
   aws configure
   # AWS Access Key ID:      <from step 3>
   # AWS Secret Access Key:  <from step 3>
   # Default region name:    ap-southeast-1
   # Default output format:  json
   aws sts get-caller-identity   # ✅ Checkpoint: prints your account ID
   ```

5. **Budget alarm FIRST** (also earns an activity credit). Replace the email:

   ```bash
   ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
   aws budgets create-budget --account-id "$ACCOUNT_ID" \
     --budget '{"BudgetName":"hawkerflow","BudgetLimit":{"Amount":"5","Unit":"USD"},"TimeUnit":"MONTHLY","BudgetType":"COST"}' \
     --notifications-with-subscribers '[{"Notification":{"NotificationType":"ACTUAL","ComparisonOperator":"GREATER_THAN","Threshold":80,"ThresholdType":"PERCENTAGE"},"Subscribers":[{"SubscriptionType":"EMAIL","Address":"you@example.com"}]}]'
   ```

   ✅ Checkpoint: *Billing → Budgets* shows `hawkerflow` with an email alert at 80% of S$5.

---

## Part 2 — Local environment

1. **Install the tools:**
   - Python **3.12** — <https://www.python.org/downloads/>
   - Git — <https://git-scm.com>
   - **AWS SAM CLI** — <https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html>
   - **k6** (for Part 7): macOS `brew install k6` · Windows `winget install k6 --source winget` · Linux: see <https://k6.io/docs/get-started/installation/>

2. **Get the code** (unzip `hawkerflow-repo.zip`, or clone once Part 5 is done):

   ```bash
   cd hawkerflow
   make install        # dev dependencies (pytest, moto, ruff, cfn-lint, pip-audit)
   make lint           # ✅ "All checks passed!"
   make test           # ✅ "35 passed"
   cfn-lint infra/template.yaml   # ✅ no output = template valid
   ```

   If all three are green, your machine matches CI exactly.

---

## Part 2b — Run it locally (optional, but do this first)

> Detailed version with checkpoints and troubleshooting: [RUN_LOCALLY.md](RUN_LOCALLY.md)

Before touching AWS, run the whole backend on your machine:

```bash
make local
```

Then open <http://localhost:8000/demo/> — a one-click launcher (4 diner personas + the stall
owner, no sign-in form) with a live dashboard, or go straight to <http://localhost:8000/diner/>
and <http://localhost:8000/stall/> and sign in on either (the local server issues stub tokens;
the Cognito call is skipped because `CLIENT_ID` is `LOCAL`).

✅ Checkpoint: place an order in the diner app, accept it in the stall portal, and watch the
diner's status flip to READY within ~5 s. The server log prints each `pipeline -> OrderAccepted`
step as the real dispatcher and consumers run.

For direct API calls, replace the Cognito JWT with a stub token:

```bash
curl localhost:8000/v1/centres/maxwell/stalls                      # public, no auth
curl localhost:8000/v1/me/orders -H "authorization: local-diner"   # consumer
curl localhost:8000/v1/stalls/ahhock-cr/orders -H "authorization: local-owner"   # producer
```

### Rehearse the fault-isolation demo locally

```bash
curl -X POST "localhost:8000/_local/break?service=notification"     # break one services
# place a few orders in the diner app - they still succeed (HTTP 201)
curl localhost:8000/_local/status                                    # DLQ depth rises, analytics unaffected
curl -X POST "localhost:8000/_local/repair?service=notification"
curl -X POST "localhost:8000/_local/redrive?service=notification"    # nothing was lost
```

The local server retries three times before parking a message, mirroring the redrive policy in
`infra/template.yaml`. Rehearse here, then capture the real evidence on AWS with
`make fault-demo`, where the DLQ and its CloudWatch alarm are genuine.

**What local mode does not cover:** real Cognito tokens and password policy, IAM permissions,
API Gateway throttling, **reserved concurrency and the per-service bulkheads**, genuine SQS
visibility timeouts and CloudWatch DLQ alarms, DynamoDB Streams shard parallelism, and cold
starts. Local runs everything in one process with no concurrency limits, so it can demonstrate
fault *containment* but not concurrency *isolation*. Those are only exercised on a real deployment — which is what
`scripts/integration_test.py` verifies.

## Part 3 — Deploy and seed `dev`

1. **Deploy** (~3–4 min; CloudFront inside the stack can take up to 10 min the first time):

   ```bash
   make deploy-dev
   ```

   ✅ Checkpoint: `Successfully created/updated stack - hawkerflow-dev`, followed by
   the **Outputs** table. You will reuse four values: `ApiUrl`, `UserPoolClientId`,
   `WebBucketName`, `WebUrl`. Re-print them any time:

   ```bash
   aws cloudformation describe-stacks --stack-name hawkerflow-dev \
     --query "Stacks[0].Outputs" --output table
   ```

2. **Seed demo users, stalls and menus:**

   ```bash
   make seed
   ```

   ✅ Checkpoint: prints the two demo logins
   (`diner@hawkerflow.demo` / `owner@hawkerflow.demo`, password `HawkerDemo1!`)
   and the config values for Part 4.

3. **Smoke-test the API:**

   ```bash
   make smoke     # ✅ "SMOKE OK ... (2 stall(s))"
   ```

---

## Part 4 — Publish the web apps

1. **Wire the config.** Open `apps/diner/index.html` **and** `apps/stall/index.html`,
   find the `CONFIG` block near the top, and paste your values:

   ```js
   const CONFIG = {
     API_URL:  "<ApiUrl from Part 3>",          // no trailing slash
     CLIENT_ID: "<UserPoolClientId>",
     REGION:   "ap-southeast-1",
     CENTRE_ID: "maxwell",                       // diner app only
   };
   ```

2. **Try locally first:**

   ```bash
   python -m http.server 8000 --directory apps
   # → http://localhost:8000/diner/   and   http://localhost:8000/stall/
   ```

   Sign in with the seeded accounts; place an order as the diner, accept it in the
   stall portal, watch the diner's status change within ~5 s.

3. **Publish behind CloudFront:**

   ```bash
   aws s3 sync apps/ s3://<WebBucketName>/
   # after any later re-sync, flush the CDN cache:
   DIST_ID=$(aws cloudfront list-distributions \
     --query "DistributionList.Items[?Comment=='HawkerFlow dev web apps'].Id" --output text)
   aws cloudfront create-invalidation --distribution-id "$DIST_ID" --paths "/*"
   ```

   ✅ Checkpoint: `<WebUrl>/diner/index.html` and `<WebUrl>/stall/index.html`
   load and sign in over HTTPS. (The bucket is private — only CloudFront can read it.)

---

## Part 5 — GitHub CI/CD (OIDC, no stored keys)

1. **Create the repo and push:**

   ```bash
   git init && git add -A && git commit -m "feat: initial platform"
   git branch -M main
   git remote add origin git@github.com:<ORG>/hawkerflow.git
   git push -u origin main
   ```

   CI (`.github/workflows/ci.yml`) runs immediately: lint, 35 tests, pip-audit,
   gitleaks, `sam validate` + build. ✅ Checkpoint: all CI jobs green.
   (Deploy will fail until steps 2–4 are done — that's expected.)

2. **Create the GitHub OIDC provider in AWS** (once per account):

   ```bash
   aws iam create-open-id-connect-provider \
     --url https://token.actions.githubusercontent.com \
     --client-id-list sts.amazonaws.com \
     --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1
   ```

   (The thumbprint is legacy-required by the CLI; AWS validates GitHub's cert itself.)

3. **Create the deploy role.** Save as `trust.json` — replace `<ACCOUNT_ID>` and `<ORG>`:

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Effect": "Allow",
       "Principal": {"Federated": "arn:aws:iam::<ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com"},
       "Action": "sts:AssumeRoleWithWebIdentity",
       "Condition": {
         "StringEquals": {"token.actions.githubusercontent.com:aud": "sts.amazonaws.com"},
         "StringLike":  {"token.actions.githubusercontent.com:sub": "repo:<ORG>/hawkerflow:*"}
       }
     }]
   }
   ```

   ```bash
   aws iam create-role --role-name hawkerflow-deploy \
     --assume-role-policy-document file://trust.json
   aws iam attach-role-policy --role-name hawkerflow-deploy \
     --policy-arn arn:aws:iam::aws:policy/AdministratorAccess
   aws iam get-role --role-name hawkerflow-deploy --query Role.Arn --output text  # copy this ARN
   ```

   > Admin on the role is pragmatic for a course project because the **trust policy
   > restricts it to your repo only**. Note it in the report as an accepted risk;
   > production would scope the permissions to the stack's services.

4. **Configure GitHub.** Repo → *Settings*:
   - *Secrets and variables → Actions → Variables* → new **variable**
     `AWS_DEPLOY_ROLE_ARN` = the ARN from step 3.
   - *Environments* → create `dev` (no protection) and `production` →
     on `production`, add **Required reviewers** = your team lead.
     **This is the manual approval gate** the report describes.
   - *Collaborators* → add all team members (Write role).
   - *Branches → Add branch protection rule* for `main`: tick **Require a pull request
     before merging** (1 approval) and **Require status checks to pass** (select the three
     CI jobs). This makes the report's "protected main + required review" claim true.
   > **If the repo lives under a GitHub organisation:** gitleaks-action needs a (free for
   > education) `GITLEAKS_LICENSE` secret — on personal accounts it runs without one.

5. **First pipeline run.** Dev is already seeded (Part 3), which the integration
   test needs. Push any commit to `main`:

   ```bash
   git commit --allow-empty -m "chore: trigger pipeline" && git push
   ```

   ✅ Checkpoint (Actions tab): `deploy-dev` runs build → deploy → smoke →
   **integration test** (`INTEGRATION PASS`) → **ZAP baseline** — then
   `deploy-prod` sits at *Waiting for review*.

---

## Part 6 — Production

1. In the Actions run, click **Review deployments → Approve** on `production`.
   The same artifact deploys to `hawkerflow-prod`, smoke-tests, then holds a
   60-second **alarm-gated bake** — any firing CloudWatch alarm fails the release.

2. **Seed prod** (needed for the live demo, not for the pipeline):

   ```bash
   python scripts/seed_data.py --stack hawkerflow-prod
   ```

3. Repeat Part 4 for prod (its own `ApiUrl`/`ClientId`/bucket from
   `hawkerflow-prod` outputs). Keep the dev-configured copies of the apps in a
   separate folder if you want both live at once.

---

## Part 7 — Load test + evidence for the report

1. **Get a diner ID token** (valid ~1 hour):

   ```bash
   TOKEN=$(aws cognito-idp initiate-auth \
     --auth-flow USER_PASSWORD_AUTH \
     --client-id <UserPoolClientId> \
     --auth-parameters USERNAME=diner@hawkerflow.demo,PASSWORD='HawkerDemo1!' \
     --query 'AuthenticationResult.IdToken' --output text)
   ```

2. **Run the stepped tests against DEV** — 25, then 50, then 100 RPS (change `-e RATE=`).
   Three points let you fit the USL scalability model in report §6. Keep **prod pristine**:
   a 100 RPS run creates ~18,000 orders that would flood the demo stall's queue
   (they expire via TTL, but not before your presentation).

   ```bash
   for RATE in 25 50 100; do
     k6 run loadtest/order_flow.js -e RATE=$RATE \
       -e API_URL=<dev ApiUrl> -e ID_TOKEN=$TOKEN -e STALL_ID=ahhock-cr -e ITEM_ID=cr
   done
   ```

   Cost of the run ≈ US$3–5. ✅ Checkpoint: k6 exits with thresholds green
   (`p(95)<300` reads, `p(95)<500` orders, `http_req_failed rate<0.01`).

3. **Capture the evidence — six artefacts:**

   | # | What | Where | Goes into |
   |---|---|---|---|
   | 1 | k6 terminal summary (p95s, error rate) | your terminal | report §4.1 + slide 8 |
   | 2 | Lambda `ConcurrentExecutions` graph during the run | CloudWatch → Metrics → Lambda → Across all functions | report §4.1 + slide 8 |
   | 3 | API latency, **p95 statistic** | CloudWatch → Metrics → ApiGateway → `Latency` for your ApiId | report §4.1 |
   | 4 | DynamoDB throttles = **0** | CloudWatch → Metrics → DynamoDB → `ThrottledRequests` | report §4.1 |
   | 5 | X-Ray service map + one order trace | CloudWatch → X-Ray traces → Service map | report §4.1 / appendix |
   | 6 | Month-to-date bill | Billing → Cost Explorer | report §6 + slide 13 |

4. **Fill the placeholders** (yellow in the report, `[ ___ ]` on slide 8):
   report §2.3 dates, §4.1 results, §6 headroom sentence; slide 8 green card.

---

## Part 8 — Teardown (after the module)

```bash
aws s3 rm s3://<dev WebBucketName> --recursive
aws s3 rm s3://<prod WebBucketName> --recursive
sam delete --stack-name hawkerflow-dev  --region ap-southeast-1
sam delete --stack-name hawkerflow-prod --region ap-southeast-1
```

Then check *Billing → Cost Explorer* a day later: it should read S$0.
(Buckets must be emptied first or stack deletion fails.)

---

## IaC checks and the security baseline

The template is validated four ways in CI: `cfn-lint` (including informational rules),
`sam validate --lint`, `checkov` for security posture, and a custom guard that checks the
reserved-concurrency budget across **all stages** fits the account pool — CloudFormation only
discovers that conflict at deploy time, and only on the second stack.

Checkov reports findings that are deliberate rather than accidental (no VPC, AWS-managed keys
instead of CMKs, WAF deferred). Each one is classified and justified in
[`infra/SECURITY_BASELINE.md`](../infra/SECURITY_BASELINE.md) — read it before answering any
security question in the presentation.

## Troubleshooting

| Symptom | Cause → fix |
|---|---|
| `make test` fails on a fresh machine | Wrong Python. `python3 --version` must be 3.12; re-run `make install` inside a 3.12 venv. |
| `sam deploy` → `...web-<account>` bucket error | Stack half-created earlier. `sam delete --stack-name hawkerflow-dev`, redeploy. |
| Pipeline: `Not authorized to perform sts:AssumeRoleWithWebIdentity` | Trust policy `sub` doesn't match `repo:<ORG>/hawkerflow:*` exactly (org and repo are case-sensitive), or the variable holds the wrong ARN. |
| Integration test: sign-in fails | Dev was never seeded — run `make seed` once, re-run the workflow. |
| Integration test times out on notification/analytics | First cold run can exceed the poll window — re-run the job; if persistent, check the DLQ alarms and the dispatcher's CloudWatch logs. |
| ZAP step fails the build | Open the ZAP report artifact in the run. A genuine FAIL alert (rare on this API) → fix; a flaky network error → re-run the job. |
| Apps: sign-in returns `USER_PASSWORD_AUTH flow not enabled` | You edited the Cognito client. Redeploy — the flow is enabled in `infra/template.yaml`. |
| Apps: API calls blocked by CORS | `API_URL` in `CONFIG` has a trailing slash or the wrong stage — copy the output exactly. |
| Browser shows stale app after re-sync | CloudFront cache — run the invalidation command in Part 4.3. |
| k6: many 401s | Token expired (1 h) — re-run the Part 7.1 command. |
| Charges appearing outside the model | Cost Explorer → group by service. Usual suspect: CloudWatch logs from a chatty debug loop — retention is 14 days, but ingestion still counts. |

## Command quick-reference

```bash
# local (no AWS) - see RUN_LOCALLY.md
make install                # dev dependencies
make local                  # whole backend + both apps on localhost:8000

# checks (what CI runs)
make lint test              # ruff + 35 pytest cases
make audit                  # pip-audit dependency scan
cfn-lint infra/template.yaml --include-checks I     # IaC lint, informational included
checkov -f infra/template.yaml --framework cloudformation   # IaC security scan
python scripts/check_concurrency_budget.py          # reserved concurrency fits the account
sam validate -t infra/template.yaml --lint --region ap-southeast-1

# deploy
make deploy-dev             # build + deploy dev
make seed                   # demo users + stalls
make smoke                  # public endpoint check
python scripts/integration_test.py --stack hawkerflow-dev
make deploy-prod            # manual prod deploy (pipeline is preferred)

# evidence for the report
make loadtest               # k6 whole platform (needs -e vars, see Part 7)
make scale-demo             # drive ONE services - independent scalability graph
make fault-demo             # break one services - fault isolation + DLQ + recovery
```
