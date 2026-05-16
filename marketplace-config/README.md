# Ameide Marketplace SaaS — Lago catalog config (WP-2)

Declarative, idempotent configuration that backs the Ameide Marketplace
SaaS offer on this **vanilla** Lago fork. **Config only — no fork
patches, no Rails-code changes, no manual UI.**

Implements WP-2 D1–D4 of
`io-ameide-marketplace-azure/docs/IMPLEMENTATION-PLAN.md`.

## What's here

| File | Role |
|---|---|
| `catalog.yml` | The declarative source of truth: 5 billable metrics, 4 plans (+ from-unit-0 charges), wallet/WalletTarget + recurring-refill template. Diffable. |
| `apply.py` | Idempotent reconciler (stdlib only). Addresses every resource by stable `code`; GET-then-POST/PUT. `apply` self-checks idempotency. |
| `test/run.sh` | Integration harness for matrix rows **T-L1..T-L5**. |
| `test/idempotency_test.sh` | **T-L1** in isolation (apply ×2 → 0 diff). |

## Declarative-mechanism decision (WP-2 Step 0)

Vanilla upstream Lago ships **no Terraform/OpenTofu provider** (verified —
no provider in the `getlago` org) and **no config-as-code/seed manifest**
(`api/db/seeds/*` are dev fixtures = Rails code, out of scope for a
config-only WP). Per the spec's explicit fallback we ship an
**idempotent, version-controlled, diffable REST-API seed**: `catalog.yml`
is the declarative state; `apply.py` reconciles a running instance to it.
Re-running yields zero diff (the reconciler GETs each resource by its
stable `code`/`external_id` before deciding create vs. update, and
`apply` runs a second dry-run pass that must report zero changes).

## Apply / verify

```bash
export LAGO_API_URL=http://localhost:3000   # repo docker-compose default
export LAGO_API_KEY=<organization API key>

python3 marketplace-config/apply.py plan     # dry-run: show drift (exit 2 if any)
python3 marketplace-config/apply.py apply     # reconcile + idempotency self-check
python3 marketplace-config/apply.py verify    # assert in-sync (exit 1 on drift)

# The per-subscription wallet payload WP-3 will POST (D3 shape), seat-scaled:
python3 marketplace-config/apply.py wallet-spec --seats 12
```

`apply.py` uses PyYAML if available, otherwise a built-in loader for the
exact subset `catalog.yml` uses (no install required).

## Tests

```bash
./marketplace-config/test/run.sh
```

T-L1..T-L5 are **integration** tests needing a running Lago. With no
reachable instance the harness prints every row as
`PENDING — needs a Lago instance` and exits 0 — it never fakes green.
Run it against a real instance to turn the rows green.

## How the deliverables map

- **D1 — 5 metrics**, keyed exactly by the `ameide.marketplace.azure.v1`
  canonical codes. Aggregation choices justified inline in `catalog.yml`:
  `editor` → `unique_count_agg` (seats are a distinct set, not a flow —
  re-emitting a seat must not inflate the count);
  `inference`/`api` → `sum_agg` (additive consumption flows);
  `db`/`blob` → `latest_agg` (provisioned-size **gauge**; the latest
  reading represents the period — `max_agg` would penalise a transient
  spike; the bridge derives GB-month from the gauge in WP-6).
- **D2 — plans** `public-preview`/`professional`/`business`/`enterprise`:
  monthly interval, `trial_period: 0` (a trial is incompatible with
  Marketplace Metering), `amount_cents` = the indicative per-Editor seat
  price ($0 / $19 / $29 / $49). Each plan carries the four consumption
  charges as `charge_model: standard` with a bare `properties.amount`
  — **priced from unit 0, no `free_units`** (Risk **R1**). `editor` is
  deliberately not charged on the plan (the seat price *is* the plan
  base; the `editor` metric exists only so the bridge can emit seat
  count as a dimension — charging it again would double-bill).
- **D3 — wallet + WalletTarget + recurring refill**: a verified
  *template* (not a per-customer wallet — WP-3 owns runtime creation).
  Vanilla Lago's `POST /api/v1/wallets` natively accepts
  `applies_to.billable_metric_codes` (→ `WalletTarget` rows, the
  metric-scoped grant) and `recurring_transaction_rules` with
  `trigger: interval` (= the spec's `source: interval`),
  `interval: monthly` (refill at the billing-cycle anchor) and
  `method: target` (`target_ongoing_balance` — the seat-scaled total
  WP-3 resizes). The wallet is customer/org-scoped, so the bundle pools
  org-wide across Editors. The included per-Editor bundle is **this
  wallet grant, never a plan `free_units`** — R1 honoured.
- **D4 — committed artifact**: `catalog.yml` + `apply.py`, reproducible
  across environments via the two env vars; this README documents apply
  & verify.

## Indicative-pricing notice

All money figures are **indicative** (finance sign-off pending). They
mirror `io-ameide-marketplace-azure/offers/public-cloud/*.yaml` and
`docs/architecture/README.md → Billing`. The meter **codes**
(`editor/inference/api/db/blob`) are a cross-repo contract
(`ameide.marketplace.azure.v1`); renaming one is a breaking change.
