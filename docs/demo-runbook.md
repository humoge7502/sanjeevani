# Demo runbook

**Target: ten minutes. One human click. Zero network dependency.**

---

## 0. Five minutes before you present

```bash
python scripts/health.py     # all checks must read PASS
bash scripts/dev.sh          # backend :8787 + frontend :5173
```

Open <http://127.0.0.1:5173>. You should see the loop strip with every stage
`PENDING` and the "Load the hero scenario" panel.

**Verify the offline claim out loud before you start**: pull the network cable or
turn off Wi-Fi, then click Reset and Run. If it works — and it will — you have
demonstrated rather than asserted the single most important reliability property
of the system.

### Fallbacks, in order

1. **Frontend won't start** → run `python scripts/demo.py`. The whole loop runs
   in one terminal with no server and no browser, and prints the same numbers.
2. **Both fail** → `python -m pytest tests/e2e -v` walks the entire hero scenario
   and asserts every claim. It is a demo you can read.
3. **Nothing runs** → the static walkthrough in
   `docs/demo-runbook.md` § "Slide-free walkthrough" below, plus this document.

---

## 1. The ten-minute choreography

Times are cumulative. The presenter is one person throughout.

### 0:00 – 1:00 · Framing

> "Sensing supply-chain disruptions is a solved problem. The gap is *decision and
> recovery* — signals arrive in days, governed recovery takes weeks. SANJEEVANI
> is the missing half: it senses, it verifies, it sizes the damage, and then it
> **stops and asks a human** before it touches anything."

Point at the loop strip. Name the stages across it. Say clearly:

> "Simulate and Optimize belong to Member 2 and are not in this build — the
> recovery plan you're about to see comes from a labelled fixture. Everything
> else you're looking at is running."

Being first with that sentence buys credibility for the rest.

### 1:00 – 2:30 · Signal and verification

Click **Run hero scenario**.

Scroll to **01 Intelligence**.

> "Two things happened at once. A maritime corridor closed at Bab-el-Mandeb, and
> a 2–8 °C biologics consignment sat at the chokepoint long enough to breach its
> excursion limit."

Point at the two verified events: `EVT-001` (cold-chain excursion, confidence
0.9603) and `EVT-002` (port closure, confidence 0.8742).

> "Notice what it did *not* do. There's a watchlist on the right. A single
> unverified social post about a labour action at JNPT — rejected, no second
> source. A stale archived advisory — rejected, too old to count. Two news
> outlets reporting the same thing — **deduplicated**, because that's one report
> restated, not corroboration. A single credible wire report of a supplier
> outage — escalated, then **failed** the two-source rule. We see it. We don't
> believe it."

Open the evidence drawer on `EVT-001`.

> "The device said 11.4 °C. We re-derived the peak from the raw samples and
> required agreement within half a degree. A device claim with no trace does not
> verify."

### 2:30 – 4:00 · Scenario and impact

Scroll to **02 Scenario**. Click the three horizon tabs.

> "It doesn't guess a duration. It plans for three futures, weighted by
> confidence and by how closely each horizon matches the disruption's own
> estimated length — which is why the 10-day window dominates at 61 %."

Scroll to **03 Network impact**.

> "$3.9 million at risk at the ten-day horizon, a 66 % value-weighted stockout
> probability, and 63 % on the hero consignment specifically."

Open **Show the arithmetic for every row**.

> "Every number here has its inputs attached. Cover, delay, outage gap,
> shortfall, the exponential, the condemnation band. Nothing is asserted."

### 4:00 – 5:00 · Network map

Scroll to **04 Network**.

> "The Red Sea corridor is severed — the dashed red lane. Six nodes drop out of
> the blast radius, five consignments are exposed. The three generics on the air
> corridor are untouched and stay muted, which is why the affected-node count is
> 6 of 12 and not 'everything'."

### 5:00 – 6:00 · The M2 boundary

Scroll to **05 Recovery plan**.

> "This is the handoff point. Member 2 owns the twin and the optimizer; that is
> not in this repository. So M3 consumes an interface, and this build supplies a
> deterministic fixture behind it — the badge says so, and so does the API."

Point at the ranked strategy table.

> "Three strategies. WAIT fails cold-chain and service-level policy outright. The
> recommended plan is re-route via Mumbai plus air uplift: $184,000, 96.5 %
> service, 18 hours."

### 6:00 – 7:00 · Compliance

Scroll to **06 Compliance**.

> "Ten rules, evaluated before any human is asked. Nine pass, one warns — the
> Red Sea corridor is under a simulated trade restriction, so a documented review
> is required. And look at the labels: `configured policy`, `simulated rule`,
> `illustrative check`. The cold-chain rule is a GDP-*style* check, and it says
> in its own words that it is not a regulatory certification."

Point at the risk tier.

> "Cost bands land this at L3, which means it needs the Head of Supply Planning.
> You can watch that happen."

### 7:00 – 8:30 · THE WOW MOMENT

Scroll to **07 Human approval**.

> "One decision. And note what the console tells you: the recommendation, why,
> the cost, the exposure, the compliance result, the affected consignments — and
> *what will happen next*, system by system."

**First, demonstrate the gate.** Change "Acting as" to **Anil Deshpande
(planner)**. Point at the warning. Click **Approve**.

> "Refused. He's a planner; this plan needs the planning head."

The red toast reads `UNAUTHORIZED_APPROVER`. Also worth showing, if you have
thirty seconds: press **Execute approved plan** now and get
`409 EXECUTION_BLOCKED` — the backend refuses because no approval exists.

Switch back to **Meera Iyer**, type a rationale, click **Approve & authorize**.

> "Now watch what one click does."

Scroll to **08 Execution**. Click **Execute approved plan**.

> "IBP planning scenario. TM freight re-booked. Ariba supplier risk. Three
> systems, one correlation ID, one receipt. Her old process took eleven days.
> This took eleven seconds — and every one of those posts is idempotent, so a
> refresh cannot double-book the freight."

### 8:30 – 9:40 · Audit and learning

Scroll to **09 Audit**.

> "Nineteen stages, forty-three records, hash-chained. Every stage: who, what,
> what evidence, what IDs. Including the refusals — the denied approval is in
> there too."

Point at the chain head and the "chain intact" badge.

> "And it's honest about its own limits: this detects an edit to the ledger body.
> It does not stop someone with filesystem access who rewrites and re-hashes the
> file. That needs append-only storage and external anchoring, which is a
> production requirement, not something I'm going to imply with a hash column."

Scroll to **10 Learning**.

> "Predicted versus actual. Four metrics better, four worse, two calibration
> signals proposed. And — this matters — **no model was retrained**. We observed
> the outcome, computed the deviation and emitted a signal for human review.
> Saying otherwise would be false."

### 9:40 – 10:00 · Close

Scroll back to the top of the loop strip.

> "Sense, verify, understand, scenario, simulate, optimize, approve, execute,
> audit, learn — governed end to end. The system sensed the disruption,
> understood its consequences, received a recovery plan, validated it against
> policy, obtained human authority, executed two SAP-shaped actions, recorded
> what happened, and learned from the outcome. **Supply chains that don't just
> react — they recover.**"

---

## 2. Reset and replay

Two ways, both reliable:

```bash
python scripts/reset.py      # then re-run the demo script
```

or click **Reset** in the header, then **Run hero scenario**.

Because the scenario clock is frozen and every input is a file, **a replay
produces an identical ledger hash chain**. That is asserted in
`tests/e2e/test_hero_scenario.py::test_reset_then_rerun_is_bit_identical`. If a
judge asks whether the run is repeatable, you can rerun it live and compare the
chain head.

**An approval never survives a reset.** Deliberate: a stale approval is exactly
how a strict gate becomes a rubber stamp.

---

## 3. Five-minute cut

If your slot is halved, run: framing (0:30) → signal and verification (1:00) →
network impact headline (1:00) → compliance labels (0:45) → **the approval click**
(1:15) → audit chain (0:30). Skip the scenario tabs, the strategy table and the
learning detail. The loop still visibly closes.

---

## 4. Question-handling quick reference

Full answers: [`judge-defense.md`](judge-defense.md).

| Question | One-line answer |
|---|---|
| "Why is this agentic?" | Six disciplined processes, each independently testable, with a provenance and confidence contract between them — not sixteen agents performing autonomy. |
| "Can execution happen without approval?" | No. `EXECUTING` is reachable only from `APPROVED`, and there's an architectural test that fails if a second module gains that power. |
| "What if the AI is wrong?" | Same as when a planner is wrong: a documented decision, reviewed by a human, bounded by policy, reversible by design. The difference is speed and memory. |
| "How do you know the impact numbers are right?" | They're arithmetic over seeded data, reproducible to the cent, with the formula and inputs attached to every row. |
| "What's mocked?" | SAP IBP/TM/Ariba shapes and the recovery plan. `/api/mock-boundaries` is the live inventory. |
| "Would this work with real SAP?" | The mocks are shape-faithful and the interfaces are already connector-shaped. It's a transport swap, not a redesign. |
| "Does it work offline?" | Pull the cable and I'll rerun it right now. |
