# Skills registry

Skills are reusable, self-contained instruction files. This is the record of what
was evaluated, what was selected, and — more importantly — **what was rejected and
why**.

**Decision as of 2026-09-15: zero community skills installed.**

That is a considered outcome, not an oversight. The reasoning is in
[§ Decision](#decision) below.

---

## Method

Each candidate was inspected with the skills CLI's dry-run listing, which reports
the actual skill inventory and descriptions **without installing**:

```bash
npx skills add <owner/repo> --list
```

Licence, maintenance date and description were then read from the GitHub API
(`https://api.github.com/repos/<owner>/<repo>`). Nothing below is recalled from
memory; every row is a value that was read back from a tool at the time of writing.

Community skills are unvetted third-party instructions, so the bar for
installation is: **it must solve a problem this repository actually has, and its
cost must be lower than solving it directly.**

---

## Evaluated candidates

Repository metadata read from the GitHub API on **2026-09-15**.

| Skill / repo | Licence | Last push | Inventory | Verdict |
|---|---|---|---|---|
| `pbakaus/impeccable` | Apache-2.0 | 2026-09-15 | `impeccable` — frontend design, redesign, critique, audit, polish, design tokens | **Not installed** |
| `Leonxlnx/taste-skill` | MIT | 2026-08-24 | `design-taste-frontend`, `design-taste-frontend-v1`, `redesign-existing-projects`, `high-end-visual-design`, `stitch-design-taste`, `no-truncation` | **Not installed** |
| `emilkowalski/skills` | MIT | 2026-09-15 | `animate`, `animate-expo`, `mobile-native`, `pick-ui-library`, `prototype`, `review-animations`, `write-swift` | **Not installed** |
| `nutlope/hallmark` | MIT | 2026-08-06 | `hallmark` — anti-slop design for greenfield pages, audits, redesigns | **Not installed** |
| `VoltAgent/awesome-agent-skills` | MIT | 2026-09-15 | **no installable skills** — a curated index, no `SKILL.md` entries | **Not applicable** |

### Notes on each

**`pbakaus/impeccable`** — the most directly relevant candidate. Its stated scope
(visual hierarchy, information architecture, cognitive load, accessibility, design
tokens, anti-patterns, error states) overlaps almost exactly with the UX quality
bar this project was already held to. Rejected for a specific reason rather than a
general one: the frontend here is **already built, typed, building clean and
tested**, and a general-purpose redesign skill's natural output is a rework of
presentation layers. Running it would have produced churn in `src/components/*`
without adding a capability the codebase lacks. Apache-2.0 makes it low-risk to
adopt *later*; see [Adopting a skill](#adopting-a-skill).

**`Leonxlnx/taste-skill`** — targets greenfield landing pages and marketing sites.
SANJEEVANI's frontend is an internal command center with no marketing surface, no
hero section and no conversion goal. `no-truncation` is the one interesting
transferable idea, and it is a prompting technique rather than a repository
capability — it does not belong in a dependency list.

**`emilkowalski/skills`** — the best-fitting of the four for this specific
codebase because motion is a real part of the design brief. `review-animations`
and `animate` would have been genuinely useful **at the point the motion layer was
being written**. By the time this registry was compiled that work was complete and
verified, so the honest recommendation is: adopt `emilkowalski/skills@review-animations`
if the motion layer is ever substantially revised. `write-swift` and
`mobile-native` are irrelevant — there is no Swift in this repository and no
mobile target.

**`nutlope/hallmark`** — overlaps `impeccable` and `taste-skill`. Same rejection
reason. It is also the least recently pushed of the four (2026-08-06).

**`VoltAgent/awesome-agent-skills`** — discovered to contain **no installable
skills**; the CLI reports `No valid skills found. Skills require a SKILL.md with
name and description.` It is an index of other repositories. Useful as a discovery
source, not as a dependency.

---

## Decision

**Nothing is installed.** The reasoning, stated plainly:

1. **The gap is not in design capability.** The frontend is built, `tsc --noEmit`
   is clean, `vite build` succeeds, and the loop is rendered end to end. A design
   skill's value is highest when there is no interface yet.
2. **Installation has a real cost here.** Community skills are unvetted
   instructions that would be placed in the repository's agent context. Adding
   them changes how future contributors' agents behave, and that is a
   maintainability decision, not a free enhancement.
3. **A skill is not evidence.** A judge asking "how do you know this UI is good?"
   is not answered by "a skill was installed." It is answered by a design system
   with stated rules and a measured bundle. Both exist:
   [`design-system.md`](design-system.md), [`performance-budget.md`](performance-budget.md).
4. **The prompt's own discipline applies.** "Do not blindly install everything"
   and "do not add dependencies simply because they exist" point at exactly this
   situation.

## Adopting a skill

If a future workstream does want one — most plausibly a substantial motion or
redesign pass — the low-risk path is:

```bash
# Inspect first. Never install blind.
npx skills add emilkowalski/skills --list
npx skills add emilkowalski/skills --skill review-animations --yes

# Installed into .agents/skills/ — review the diff before committing.
git status .agents/skills
```

Bookkeeping for any adoption:

| Field | Requirement |
|---|---|
| Licence | Recorded above; all four candidates are MIT or Apache-2.0, so redistribution is not a blocker |
| Scope | Must be scoped to the frontend. A skill must not be given authority over `backend/` — governance code is the wrong place for generative instruction |
| Review | `.agents/skills/` must be committed and read by a human, so the instructions are visible in review rather than implicit |
| Reversibility | Deleting the directory must fully undo the installation; if it does not, that is a reason to refuse |

## Security note

Skills are third-party instruction payloads. Two standing constraints apply
regardless of which skill is ever adopted:

1. **No skill may be granted authority over `backend/governance/` or
   `backend/sap/`.** The approval state machine and the execution path are the
   product's security boundary. Generative instruction does not get a vote there.
2. **No skill may introduce a network dependency into the demo path.** Offline
   operation is non-negotiable, and a skill that assumes fetched assets or remote
   services cannot be allowed into the critical path.

Both constraints are also enforced mechanically: `test_a08c_approved_state_is_only_entered_from_the_decide_path`
scans the backend for unauthorised approval paths, and the offline guarantee is
asserted by `test_offline_flag_is_on_and_no_network_is_required`.

---

## Sources

| What | Where | Accessed |
|---|---|---|
| Impeccable | `github.com/pbakaus/impeccable` (Apache-2.0) | 2026-09-15 |
| Taste Skill | `github.com/Leonxlnx/taste-skill` (MIT) | 2026-09-15 |
| Emil Kowalski — Skills | `github.com/emilkowalski/skills` (MIT) | 2026-09-15 |
| Hallmark | `github.com/nutlope/hallmark` (MIT) | 2026-09-15 |
| Awesome Agent Skills | `github.com/VoltAgent/awesome-agent-skills` (MIT) | 2026-09-15 |
