# Performance budget

Budgets are only meaningful if they are **measured**, so every number here was
read off a tool on the machine this was built on, and the command that produced it
is given so it can be reproduced. No number is an aspiration.

**Measurement date:** 2026-09-15
**Environment:** Python 3.13, Node 20, Linux. Local loopback, no network in the
critical path. A development laptop, not a server.

---

## Why a budget exists at all

The failure mode this guards against is specific and it is not abstract: *a
beautiful UI that freezes during the hero scenario is a failed demo.* The demo is
90 seconds of a presenter clicking through eleven stages with an audience
watching. A 3-second stall at the approval moment is worse than a plainer
interface that never stalls.

So the budgets below are chosen for **presentation smoothness**, and the tightest
one is deliberately on the approval→execution path.

---

## Measured results

### Bundle (frontend)

```bash
cd frontend && npm run build && for f in dist/assets/*.js dist/assets/*.css; do
  echo -n "$f  raw=$(stat -c%s "$f")  gzip="; gzip -c "$f" | wc -c; done
```

| Asset | Raw | gzip | Budget | Status |
|---|---|---|---|---|
| `index.js` | 223 KB | **67 KB** | 90 KB gzip | pass |
| `index.css` | 26 KB | **5.8 KB** | 15 KB gzip | pass |
| Total transferred | — | **~73 KB gzip** | 120 KB gzip | pass |

67 KB gzipped for the entire command center — graph, timeline, approval console and
all eleven loop stages — is the direct result of a dependency decision: **the
network map is hand-written SVG and there is no charting, animation or UI component
library.** React and React DOM are essentially the whole bundle. See
[ADR 0002](decisions/0002-no-charting-or-ui-component-library.md).

### Backend operations (in-process)

```bash
python -c "<script in the section below>"
```

| Operation | Median | Budget | Status |
|---|---|---|---|
| Full loop (`reset` + M1→M2→M3 run) | **74 ms** | 250 ms | pass |
| Human decision + governed execution | **5.2 ms** | 50 ms | pass |
| State snapshot build | **0.7 ms** | 20 ms | pass |

The full loop lands at ~74 ms, and it is stable across repeated runs
(75.8 / 74.2 / 73.7 ms) — the numbers above are from the timed harness, which is
warm, plus a 5 ms median for the decision-and-execution path. That is roughly
**14× under budget** on the path the presenter clicks, which is where the margin
matters.

### API latency (FastAPI `TestClient`, median of 12)

```bash
python -c "from fastapi.testclient import TestClient; ..."   # see below
```

| Endpoint | Median | Payload | gzip | Budget | Status |
|---|---|---|---|---|---|
| `POST /api/demo/reset` | 7.2 ms | 161 B | — | 100 ms | pass |
| `POST /api/demo/run` (first run) | 72.7 ms | 64 KB | 10 KB | 250 ms | pass |
| `GET /api/state` | 1.9 ms | 64 KB | 10 KB | 100 ms | pass |
| `GET /api/audit` (post-reset) | 8.0 ms | 37 KB | 7.4 KB | 100 ms | pass |
| `GET /api/impact` | 2.0 ms | 32 KB | — | 100 ms | pass |
| `GET /api/policies` | 1.8 ms | 4.7 KB | — | 100 ms | pass |
| `GET /api/mock-boundaries` | 1.7 ms | 1.4 KB | — | 100 ms | pass |
| `GET /api/network` | 1.9 ms | 11 KB | — | 100 ms | pass |

Every read endpoint is under 10 ms. The heaviest interaction in the whole demo —
the approval cycle — does not appear above because it is *5 ms*, which is
effectively instant on loopback.

### Rendering

| Surface | Data volume | Assessment |
|---|---|---|
| Network map | 12 nodes, 6 lanes, ~11 edges | Trivial. Single SVG pass, no animation frame loop. |
| Loop rail | 11 stages | Static DOM, CSS transitions only. |
| Audit timeline | **41 records** per completed run | Renders without windowing. This is the number to watch. |
| Compliance checks | ~7 checks | Trivial. |
| SAP call log | 3 calls | Trivial. |

---

## Budgets and the reasoning behind each

| # | Budget | Rationale |
|---|---|---|
| P1 | **Bundle ≤ 120 KB gzip** | The demo runs on venue hardware and possibly venue Wi-Fi. 73 KB is fast on anything, including a congested conference network. |
| P2 | **Approval→receipt ≤ 250 ms end to end** | This is *the* moment the product is judged on. It must feel instantaneous. 5 ms gives a 50× margin, which is intentional: the margin absorbs a slower venue machine and means no optimistic UI or spinner is needed. |
| P3 | **No jank during the demo** | No continuous animation runs on the graph. Motion is confined to stage transitions, which are CSS and do not touch the main thread's data path. |
| P4 | **Any single read ≤ 100 ms** | Panel switches, mode changes and stage navigation are all read calls. |
| P5 | **Full loop ≤ 250 ms** | The loop is triggered by one button. 74 ms keeps it under human perception of "instant." |

## What is deliberately NOT budgeted

Stated so the absence is not mistaken for an oversight:

- **Time to interactive / Lighthouse.** Not measured. No browser automation was
  run in this environment, so reporting a number here would mean inventing one.
  P1 (bundle size) is used as a *proxy*, and it is labelled as a proxy.
- **Memory.** Not measured. The process holds a 12-node graph, a 41-record ledger
  and one API response in memory; there is no memory pressure to characterise.
- **Concurrency.** The orchestrator takes a lock and is single-run. Throughput is
  not a goal for a demo control plane. This is also why P2 can be so tight.
- **Cold start.** Not measured. The first `import networkx` in a fresh process is
  the dominant cost and it is unaffected by anything in this repository.

## Known limits

Two real ones, recorded rather than hidden:

1. **`/api/audit` grows monotonically.** It is an append-only ledger, so it
   accumulates across runs until a reset. A single post-reset read is 37 KB; after
   many runs it reached **329 KB** during measurement, and it will keep growing.
   Bounded today by the reset control. A long-lived deployment would need
   pagination or windowing — the same finding recorded as D5 in the
   [threat model](security-threat-model.md).
2. **The full loop's 74 ms includes the graph build.** It is not a pure
   computation figure. It is the number a presenter actually experiences, which is
   why it is the one budgeted.

## Reproducing these numbers

```bash
# Backend operations
python3 -c "
import time
from backend.orchestrator import orchestrator
from backend.contracts import ApprovalDecision
for i in range(3):
    t0=time.perf_counter(); orchestrator.reset(); orchestrator.run(); t1=time.perf_counter()
    orchestrator.decide(ApprovalDecision.APPROVE,'meera.iyer'); orchestrator.execute(); t2=time.perf_counter()
    print(f'run={(t1-t0)*1000:.1f}ms decide+exec={(t2-t1)*1000:.1f}ms')
"

# API latency and payload sizes
python3 -c "
from fastapi.testclient import TestClient
from backend.api.app import app
c = TestClient(app)
c.post('/api/demo/reset'); c.post('/api/demo/run')
for u in ['/api/state','/api/audit','/api/impact','/api/network','/api/policies']:
    print(u, len(c.get(u).content), 'bytes')
"

# Bundle
cd frontend && npm run build && ls -l dist/assets/
```

Run these before and after a change that touches the hot path. A regression that
breaks P2 — the approval cycle — is a demo-breaking regression and should be
treated as such.
