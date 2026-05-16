#!/usr/bin/env python3
"""
WP-2 — declarative Lago catalog reconciler for the Ameide Marketplace SaaS offer.

Idempotent: reconciles a running Lago instance to marketplace-config/catalog.yml.
Apply twice -> zero diff (T-L1). NO fork patches; pure vanilla Lago REST API.

Usage:
  LAGO_API_URL=http://localhost:3000 LAGO_API_KEY=... \
      python3 marketplace-config/apply.py apply       # reconcile
      python3 marketplace-config/apply.py plan         # dry-run: print diff, exit 2 if drift
      python3 marketplace-config/apply.py verify        # assert in-sync, exit 1 if drift
      python3 marketplace-config/apply.py wallet-spec --seats N
                                                        # print the verified
                                                        # WP-3 wallet-create
                                                        # payload (D3 shape)

Stdlib only (urllib + a tiny YAML subset loader). LAGO_API_URL defaults to
http://localhost:3000 (the repo's docker-compose convention).
"""
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.join(HERE, "catalog.yml")


# --- tiny YAML loader (catalog.yml uses only the subset we emit) ----------
def _load_yaml(path):
    try:
        import yaml  # use PyYAML if present (preferred)
        with open(path) as fh:
            return yaml.safe_load(fh)
    except ImportError:
        pass
    # Minimal fallback parser for the exact structure of catalog.yml.
    import re
    lines = []
    with open(path) as fh:
        for raw in fh:
            s = raw.split(" #")[0].rstrip() if not raw.lstrip().startswith("#") else ""
            if raw.lstrip().startswith("#") or raw.strip() == "":
                continue
            lines.append(raw.rstrip("\n"))

    def indent(l):
        return len(l) - len(l.lstrip(" "))

    def scalar(v):
        v = v.strip()
        if v == "" :
            return None
        if v.startswith('"') and v.endswith('"'):
            return v[1:-1]
        if v in ("true", "false"):
            return v == "true"
        if re.fullmatch(r"-?\d+", v):
            return int(v)
        if re.fullmatch(r"-?\d+\.\d+", v):
            return float(v)
        return v

    def parse(block, base):
        result = None
        i = 0
        while i < len(block):
            line = block[i]
            stripped = line.strip()
            ind = indent(line)
            if ind != base:
                i += 1
                continue
            if stripped.startswith("- "):
                if result is None:
                    result = []
                item = stripped[2:]
                # gather the item's sub-block
                sub = []
                j = i + 1
                while j < len(block) and (indent(block[j]) > base or block[j].strip().startswith("- ") and indent(block[j]) > base):
                    sub.append(block[j])
                    j += 1
                if ":" in item and not item.startswith(("'", '"')):
                    k, _, v = item.partition(":")
                    merged = [" " * (base + 2) + item] + sub
                    result.append(parse(merged, base + 2))
                else:
                    result.append(scalar(item))
                i = j
                continue
            if ":" in stripped:
                k, _, v = stripped.partition(":")
                k = k.strip()
                if result is None:
                    result = {}
                if v.strip() == "":
                    sub = []
                    j = i + 1
                    while j < len(block) and indent(block[j]) > base:
                        sub.append(block[j])
                        j += 1
                    result[k] = parse(sub, base + 2) if sub else None
                    i = j
                    continue
                result[k] = scalar(v)
            i += 1
        return result if result is not None else {}

    return parse(lines, 0)


# --- Lago REST client ------------------------------------------------------
class Lago:
    def __init__(self):
        self.base = os.environ.get("LAGO_API_URL", "http://localhost:3000").rstrip("/")
        self.key = os.environ.get("LAGO_API_KEY")
        if not self.key:
            sys.exit("ERROR: LAGO_API_KEY is required (organization API key).")

    def _req(self, method, path, body=None):
        url = f"{self.base}/api/v1{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.key}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                payload = json.loads(raw)
            except Exception:
                payload = {"raw": raw.decode(errors="replace")}
            return e.code, payload

    def get(self, path):
        return self._req("GET", path)

    def post(self, path, body):
        return self._req("POST", path, body)

    def put(self, path, body):
        return self._req("PUT", path, body)


# --- desired-state builders ------------------------------------------------
def metric_payload(m):
    return {
        "billable_metric": {
            "name": m["name"],
            "code": m["code"],
            "description": m.get("description", ""),
            "aggregation_type": m["aggregation_type"],
            "field_name": m.get("field_name"),
            "recurring": bool(m.get("recurring", False)),
        }
    }


def plan_payload(cat, p):
    defaults = cat["plan_defaults"]
    override = p.get("charges_amount_override")
    charges = []
    for c in defaults["charges"]:
        amount = override if override is not None else c["amount"]
        charges.append({
            "billable_metric_code": c["billable_metric_code"],
            "charge_model": c["charge_model"],
            "pay_in_advance": False,
            "invoiceable": True,
            # R1: prices from unit 0 — properties carries ONLY `amount`,
            # deliberately NO `free_units`.
            "properties": {"amount": str(amount)},
        })
    return {
        "plan": {
            "name": p["name"],
            "code": p["code"],
            "description": p.get("description", ""),
            "interval": defaults["interval"],
            "amount_cents": p["amount_cents"],
            "amount_currency": cat["currency"],
            "trial_period": defaults.get("trial_period", 0),
            "pay_in_advance": defaults.get("pay_in_advance", False),
            "charges": charges,
        }
    }


def wallet_spec(cat, seats):
    wt = cat["wallet_template"]
    per = float(wt["recurring_transaction_rule"]["target_ongoing_balance_per_editor"])
    target = round(per * seats, 5)
    rule = wt["recurring_transaction_rule"]
    return {
        "wallet": {
            "external_customer_id": "<set-by-WP-3>",
            "name": wt["name"],
            "currency": wt["currency"],
            "rate_amount": str(wt["rate_amount"]),
            "granted_credits": str(target),
            "applies_to": {
                "billable_metric_codes": wt["applies_to_billable_metric_codes"],
            },
            "recurring_transaction_rules": [{
                "trigger": rule["trigger"],
                "interval": rule["interval"],
                "method": rule["method"],
                "target_ongoing_balance": str(target),
            }],
        }
    }


# --- reconciliation --------------------------------------------------------
def _metric_drift(existing, m):
    bm = existing.get("billable_metric", existing)
    return (
        bm.get("name") != m["name"]
        or bm.get("aggregation_type") != m["aggregation_type"]
        or (bm.get("field_name") or None) != (m.get("field_name") or None)
        or bool(bm.get("recurring")) != bool(m.get("recurring", False))
    )


def _plan_drift(existing, want):
    pl = existing.get("plan", existing)
    w = want["plan"]
    if (pl.get("amount_cents") != w["amount_cents"]
            or pl.get("interval") != w["interval"]
            or (pl.get("trial_period") or 0) != w["trial_period"]):
        return True
    have = {c["lago_id"] if "lago_id" in c else c.get("billable_metric_code"): c
            for c in pl.get("charges", [])}
    want_codes = {c["billable_metric_code"] for c in w["charges"]}
    have_codes = {c.get("billable_metric_code") or c.get("billable_metric", {}).get("code")
                  for c in pl.get("charges", [])}
    if want_codes != have_codes:
        return True
    for c in pl.get("charges", []):
        if "free_units" in (c.get("properties") or {}):
            return True  # R1 violation must be reconciled away
    return False


def reconcile(lago, cat, dry_run):
    changes = []

    # D1 metrics
    for m in cat["metrics"]:
        st, body = lago.get(f"/billable_metrics/{m['code']}")
        if st == 404:
            changes.append(("CREATE metric", m["code"]))
            if not dry_run:
                s, b = lago.post("/billable_metrics", metric_payload(m))
                _check(s, b, f"create metric {m['code']}")
        elif st == 200:
            if _metric_drift(body, m):
                changes.append(("UPDATE metric", m["code"]))
                if not dry_run:
                    s, b = lago.put(f"/billable_metrics/{m['code']}", metric_payload(m))
                    _check(s, b, f"update metric {m['code']}")
        else:
            _check(st, body, f"get metric {m['code']}")

    # D2 plans (+ nested charges, priced from unit 0)
    for p in cat["plans"]:
        want = plan_payload(cat, p)
        st, body = lago.get(f"/plans/{p['code']}")
        if st == 404:
            changes.append(("CREATE plan", p["code"]))
            if not dry_run:
                s, b = lago.post("/plans", want)
                _check(s, b, f"create plan {p['code']}")
        elif st == 200:
            if _plan_drift(body, want):
                changes.append(("UPDATE plan", p["code"]))
                if not dry_run:
                    s, b = lago.put(f"/plans/{p['code']}", want)
                    _check(s, b, f"update plan {p['code']}")
        else:
            _check(st, body, f"get plan {p['code']}")

    return changes


def _check(status, body, what):
    if status not in (200, 201):
        sys.exit(f"ERROR: {what} failed (HTTP {status}): {json.dumps(body)}")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd = sys.argv[1]
    cat = _load_yaml(CATALOG)

    if cmd == "wallet-spec":
        seats = 1
        if "--seats" in sys.argv:
            seats = int(sys.argv[sys.argv.index("--seats") + 1])
        print(json.dumps(wallet_spec(cat, seats), indent=2))
        return

    lago = Lago()

    if cmd == "apply":
        changes = reconcile(lago, cat, dry_run=False)
        if changes:
            for kind, code in changes:
                print(f"  applied: {kind} {code}")
        else:
            print("  in sync — nothing to apply")
        # idempotency self-check: a second pass must report zero changes
        residual = reconcile(lago, cat, dry_run=True)
        if residual:
            sys.exit(f"ERROR: not idempotent — residual drift: {residual}")
        print("OK: catalog applied and idempotent (second pass = 0 changes).")

    elif cmd == "plan":
        changes = reconcile(lago, cat, dry_run=True)
        for kind, code in changes:
            print(f"  would: {kind} {code}")
        if changes:
            sys.exit(2)
        print("OK: no drift.")

    elif cmd == "verify":
        changes = reconcile(lago, cat, dry_run=True)
        if changes:
            for kind, code in changes:
                print(f"  DRIFT: {kind} {code}")
            sys.exit(1)
        print("OK: Lago matches catalog.yml.")

    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
