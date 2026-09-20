// k6 load models for the scalability demonstration (report sections 4.1 and 6).
//
// Whole platform, stepped:
//   for R in 25 50 100; do k6 run loadtest/order_flow.js -e RATE=$R \
//     -e API_URL=... -e ID_TOKEN=... ; done
//
// Single services, to demonstrate INDEPENDENT scalability (microservices evidence):
//   k6 run loadtest/order_flow.js -e SERVICE=catalog  -e RATE=100 ...
//   k6 run loadtest/order_flow.js -e SERVICE=ordering -e RATE=40  ...
//
// With SERVICE=catalog only the Catalog function should scale; Ordering, Notification
// and Analytics stay flat at zero concurrency. That divergence is the evidence that
// services scale independently rather than as one block.
import http from "k6/http";
import { check } from "k6";
import { uuidv4 } from "https://jslib.k6.io/k6-utils/1.4.0/index.js";

const BASE = __ENV.API_URL;
const TOKEN = __ENV.ID_TOKEN;
const STALL = __ENV.STALL_ID || "ahhock-cr";
const ITEM = __ENV.ITEM_ID || "cr";
const CENTRE = __ENV.CENTRE_ID || "maxwell";
const RATE = Number(__ENV.RATE || 100);
const SERVICE = (__ENV.SERVICE || "all").toLowerCase();   // all | catalog | ordering
const DURATION = __ENV.DURATION || "10m";

function scenario(exec, rate) {
  return {
    executor: "constant-arrival-rate",
    exec,
    rate: Math.max(1, Math.round(rate)),
    timeUnit: "1s",
    duration: DURATION,
    preAllocatedVUs: Math.max(10, Math.round(rate)),
    maxVUs: 300,
  };
}

const scenarios = {};
if (SERVICE === "all" || SERVICE === "catalog") {
  scenarios.browse = scenario("browse", SERVICE === "catalog" ? RATE : RATE * 0.7);
}
if (SERVICE === "all" || SERVICE === "ordering") {
  scenarios.order = scenario("order", SERVICE === "ordering" ? RATE : RATE * 0.3);
}

export const options = {
  scenarios,
  thresholds: {
    "http_req_duration{scenario:browse}": ["p(95)<300"],
    "http_req_duration{scenario:order}": ["p(95)<500"],
    http_req_failed: ["rate<0.01"],
  },
};

export function browse() {
  const stalls = http.get(`${BASE}/v1/centres/${CENTRE}/stalls`, { tags: { scenario: "browse" } });
  const menu = http.get(`${BASE}/v1/stalls/${STALL}/menu`, { tags: { scenario: "browse" } });
  check(stalls, { "stalls 200": (r) => r.status === 200 });
  check(menu, { "menu 200": (r) => r.status === 200 });
}

export function order() {
  const res = http.post(
    `${BASE}/v1/orders`,
    JSON.stringify({ stallId: STALL, items: [{ itemId: ITEM, qty: 1 }] }),
    {
      headers: {
        "content-type": "application/json",
        authorization: TOKEN,
        "idempotency-key": uuidv4(),
      },
      tags: { scenario: "order" },
    },
  );
  check(res, { "order 201": (r) => r.status === 201 });
}
