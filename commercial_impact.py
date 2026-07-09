"""
Illustrative commercial-impact estimate: ties this pipeline's measured
outputs (routing, expansion signals, retention-risk detection) to Net
New ARR - the standard, dollar-denominated SaaS revenue bridge (New
Logo + Expansion - Contraction - Churn), reported alongside GRR% and
NRR% as separate retention-health ratios, the way real SaaS finance/CS
orgs actually report them (see HOW_THE_AI_WORKS.md's "Commercial
impact" section for why GRR/NRR are kept as percentages here rather
than folded into the dollar bridge).

Every dollar figure in this report is illustrative: assumed deal
sizes, close rates, and expansion/contraction rates all live in
config.py and are placeholder assumptions, not measured data - there
is no real revenue anywhere in this repo. What IS real: which
messages triggered which signal, and which of those signals could be
tied to a known mock account with an actual arr_usd figure on file.

Usage: python commercial_impact.py           (prints to terminal)
       python commercial_impact.py --html     (also writes results/commercial_impact.html)
"""

import html as html_module
import sys
from pathlib import Path

from config import CONFIG
from pipeline import classify_account_tier
from dashboard import CSS, stat_card

DATA_DIR = Path(__file__).parent / "data"


def load_json(path):
    import json
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def account_for(ref, backend):
    if not ref:
        return None
    return backend.get("accounts", {}).get(ref)


def compute():
    reference_run = load_json(Path(__file__).parent / "results" / "reference_run.json")
    backend = load_json(DATA_DIR / "mock_backend.json")
    results = [r for r in reference_run["results"] if "extraction" in r]

    new_logo_arr = 0.0
    new_logo_count = 0
    expansion_arr = 0.0
    expansion_signals_total = 0
    expansion_signals_priced = 0
    contraction_arr = 0.0
    at_risk_signals_total = 0
    at_risk_signals_priced = 0
    churned_arr = 0.0
    churn_events_total = 0
    churn_events_priced = 0

    close_rate = CONFIG["assumed_new_logo_close_rate"]
    deal_sizes = CONFIG["assumed_new_logo_arr_by_team_size"]
    expansion_rate = CONFIG["assumed_expansion_rate"]
    contraction_rate = CONFIG["assumed_contraction_rate"]

    for r in results:
        extraction = r["extraction"]
        ref = extraction.get("account_reference")
        account = account_for(ref, backend)
        arr_usd = account.get("arr_usd") if account else None

        # New Logo ARR: Sales-category messages with no known account on
        # file - i.e. a net-new prospect, not an existing customer.
        if extraction["category"] == "Sales" and not account:
            new_logo_count += 1
            band = extraction.get("team_size_band", "unknown")
            new_logo_arr += deal_sizes.get(band, deal_sizes["unknown"]) * close_rate

        # Expansion ARR: the health_expansion_flag fired on this message.
        if r.get("health_expansion_flag"):
            expansion_signals_total += 1
            if arr_usd is not None:
                expansion_signals_priced += 1
                expansion_arr += arr_usd * expansion_rate

        # Contraction ARR: account health context flags risk, but this
        # specific message isn't a formal close/cancel (that's tracked
        # separately as churn, not contraction).
        guardrail_flags = r.get("guardrail_flags", [])
        is_formal_close = "formal_close_cancel_support_owned" in guardrail_flags
        # account_health_is_risk isn't re-derived here - reuse whatever
        # the confidence reasons already recorded, so this can never drift
        # from what the pipeline itself decided at run time.
        was_flagged_at_risk = any("health/VoC risk" in reason for reason in r["confidence"].get("reasons", []))
        if was_flagged_at_risk and not is_formal_close:
            at_risk_signals_total += 1
            if arr_usd is not None:
                at_risk_signals_priced += 1
                contraction_arr += arr_usd * contraction_rate

        # Churned ARR: a formal close/cancel request against a known account.
        if is_formal_close:
            churn_events_total += 1
            if arr_usd is not None:
                churn_events_priced += 1
                churned_arr += arr_usd

    nnaov = new_logo_arr + expansion_arr - contraction_arr - churned_arr

    known_accounts = [a for a in backend.get("accounts", {}).values() if "arr_usd" in a]
    total_known_arr = sum(a["arr_usd"] for a in known_accounts)
    grr_pct = (total_known_arr - contraction_arr - churned_arr) / total_known_arr * 100 if total_known_arr else None
    nrr_pct = (total_known_arr - contraction_arr - churned_arr + expansion_arr) / total_known_arr * 100 if total_known_arr else None

    tier_counts = {"self_serve": 0, "mid_market": 0, "enterprise": 0}
    tier_arr = {"self_serve": 0.0, "mid_market": 0.0, "enterprise": 0.0}
    for account in known_accounts:
        tier = classify_account_tier(account["arr_usd"], CONFIG)
        tier_counts[tier] += 1
        tier_arr[tier] += account["arr_usd"]

    return {
        "new_logo_arr": new_logo_arr, "new_logo_count": new_logo_count, "close_rate": close_rate,
        "expansion_arr": expansion_arr, "expansion_signals_total": expansion_signals_total, "expansion_signals_priced": expansion_signals_priced,
        "contraction_arr": contraction_arr, "at_risk_signals_total": at_risk_signals_total, "at_risk_signals_priced": at_risk_signals_priced,
        "churned_arr": churned_arr, "churn_events_total": churn_events_total, "churn_events_priced": churn_events_priced,
        "nnaov": nnaov, "total_known_arr": total_known_arr, "grr_pct": grr_pct, "nrr_pct": nrr_pct,
        "tier_counts": tier_counts, "tier_arr": tier_arr,
    }


def print_report(d):
    print("Commercial impact estimate - ILLUSTRATIVE, see module docstring\n")
    print(f"New Logo ARR:    ${d['new_logo_arr']:,.0f}  ({d['new_logo_count']} net-new Sales prospects, {d['close_rate']:.0%} assumed close rate)")
    print(f"Expansion ARR:   ${d['expansion_arr']:,.0f}  ({d['expansion_signals_priced']}/{d['expansion_signals_total']} expansion signals priced - rest had no known account on file)")
    print(f"Contraction ARR: -${d['contraction_arr']:,.0f}  ({d['at_risk_signals_priced']}/{d['at_risk_signals_total']} at-risk signals priced)")
    print(f"Churned ARR:     -${d['churned_arr']:,.0f}  ({d['churn_events_priced']}/{d['churn_events_total']} formal close/cancel requests priced)")
    print(f"\nNet New ARR (NNAOV-style bridge) = ${d['nnaov']:,.0f}")
    print(f"  = New Logo (${d['new_logo_arr']:,.0f}) + Expansion (${d['expansion_arr']:,.0f}) - Contraction (${d['contraction_arr']:,.0f}) - Churn (${d['churned_arr']:,.0f})")
    if d["nnaov"] < 0:
        print(
            "  NOTE: negative - driven by 2 of the 14 known mock accounts (both\n"
            "  large enterprise ARR) sending formal close/cancel requests in this\n"
            "  120-message synthetic set. With only 14 known accounts, 2 large\n"
            "  churns dominate the bridge - not a claim about real churn rates,\n"
            "  but a real illustration of why NNAOV matters: a metric like 'new\n"
            "  logo count' would look fine here and completely miss this."
        )

    print(f"\nRetention health (reported separately, not folded into the bridge above):")
    if d["grr_pct"] is not None:
        print(f"  GRR: {d['grr_pct']:.1f}%  (of ${d['total_known_arr']:,.0f} known-account ARR base, contraction+churn only)")
        print(f"  NRR: {d['nrr_pct']:.1f}%  (same base, contraction+churn netted against expansion)")
    else:
        print("  No known-account ARR base to compute against.")

    print(f"\nKnown account base by tier (thresholds are per-company config, not universal - see config.py):")
    for tier in ("self_serve", "mid_market", "enterprise"):
        print(f"  {tier:<11} {d['tier_counts'][tier]:>2} accounts, ${d['tier_arr'][tier]:,.0f} total ARR")


def render_html(d):
    esc = html_module.escape
    bridge_note = ""
    if d["nnaov"] < 0:
        bridge_note = (
            '<div class="quote" style="margin-top:14px">'
            "<b>Why this is negative:</b> 2 of the 14 known mock accounts (both large ARR) sent formal "
            "close/cancel requests in this 120-message synthetic set - with a base this small, 2 large "
            "churns dominate the bridge. Not a claim about real churn rates, but a real illustration of "
            "why a dollar-weighted metric matters: a vanity metric like new-logo count (23 this run) "
            "would look fine and completely miss it."
            "</div>"
        )

    tier_rows = "".join(
        f'<div class="dl"><span class="k">{esc(tier.replace("_", " ").title())}</span>'
        f'<span class="v">{d["tier_counts"][tier]} accounts &middot; ${d["tier_arr"][tier]:,.0f} total ARR</span></div>'
        for tier in ("self_serve", "mid_market", "enterprise")
    )

    grr_str = f"{d['grr_pct']:.1f}%" if d["grr_pct"] is not None else "n/a"
    nrr_str = f"{d['nrr_pct']:.1f}%" if d["nrr_pct"] is not None else "n/a"

    stat_cards = "".join([
        stat_card(f"${d['nnaov']:,.0f}", "Net New ARR (NNAOV-style bridge)", "#34d399" if d["nnaov"] >= 0 else "#f87171"),
        stat_card(f"${d['new_logo_arr']:,.0f}", "New Logo ARR", "#60a5fa"),
        stat_card(f"${d['expansion_arr']:,.0f}", "Expansion ARR", "#34d399"),
        stat_card(f"-${d['contraction_arr']:,.0f}", "Contraction ARR", "#fbbf24"),
        stat_card(f"-${d['churned_arr']:,.0f}", "Churned ARR", "#f87171"),
        stat_card(grr_str, "GRR (retention health)", "#93c5fd"),
        stat_card(nrr_str, "NRR (retention health)", "#93c5fd"),
    ])

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="robots" content="noindex, nofollow">
<title>Commercial impact - Net New ARR bridge</title>
<style>{CSS}</style>
</head>
<body>
<h1>Commercial impact: Net New ARR bridge</h1>
<p class="subtitle">ILLUSTRATIVE - ties this build's measured pipeline signals (Sales routing, expansion flags, retention-risk catches) to Net New ARR, the same real revenue bridge Salesforce reports internally as "NNAOV." Dollar assumptions (deal size, close rate, expansion/contraction rates) are placeholders in config.py, not measured data - see HOW_THE_AI_WORKS.md's "Commercial impact" section for full methodology.</p>

<div class="stats">{stat_cards}</div>

{bridge_note}

<div class="divider">The bridge</div>
<div class="quote">
  Net New ARR = New Logo (${d['new_logo_arr']:,.0f}) + Expansion (${d['expansion_arr']:,.0f}) &minus; Contraction (${d['contraction_arr']:,.0f}) &minus; Churn (${d['churned_arr']:,.0f}) = <b>${d['nnaov']:,.0f}</b>
</div>
<p class="reasoning" style="margin-top:10px">
  New Logo: {d['new_logo_count']} net-new Sales prospects, {d['close_rate']:.0%} assumed close rate.
  Expansion: {d['expansion_signals_priced']}/{d['expansion_signals_total']} expansion signals priced (rest had no known account on file).
  Contraction: {d['at_risk_signals_priced']}/{d['at_risk_signals_total']} at-risk signals priced.
  Churn: {d['churn_events_priced']}/{d['churn_events_total']} formal close/cancel requests priced.
</p>

<div class="divider">Known account base by tier</div>
<p class="reasoning" style="margin-bottom:10px">Thresholds (mid_market_arr_threshold, enterprise_arr_threshold in config.py) are per-company presets, not universal constants.</p>
{tier_rows}

</body>
</html>"""


def main():
    d = compute()
    print_report(d)
    if "--html" in sys.argv:
        out_path = Path(__file__).parent / "results" / "commercial_impact.html"
        out_path.write_text(render_html(d), encoding="utf-8")
        print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
