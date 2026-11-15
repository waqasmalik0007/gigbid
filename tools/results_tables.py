"""Build the README/report results tables from evals/results/*.json (every number is traceable).

    python tools/results_tables.py > docs/RESULTS.md

Missing result files are reported as "not run" instead of being filled in.
"""
from __future__ import annotations

import json
from pathlib import Path

R = Path(__file__).resolve().parents[1] / "evals/results"


def load(name: str):
    p = R / name
    return json.loads(p.read_text()) if p.exists() else None


def f(x, nd=3):
    return "–" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def rag_table() -> str:
    main, prop_best, prop_v0 = load("rag_main.json"), load("proposals_best.json"), load("proposals_v0.json")
    if not main:
        return "_RAG evaluation not run._\n"
    lines = ["| Variant | Evidence Recall@5 | MRR | nDCG@10 | p95 retrieval latency (s) | n jobs |",
             "|---|---|---|---|---|---|"]
    for name, v in main["variants"].items():
        o = v["overall"]
        lines.append(f"| {name} | {f(o['recall@5'])} | {f(o['mrr'])} | {f(o['ndcg@10'])} | {f(o['p95_latency_s'], 4)} | {o['n']} |")
    out = "\n".join(lines) + "\n"
    if prop_best or prop_v0:
        out += ("\n| Pipeline | Recall@5 | nDCG@10 | Unsupported claims / proposal (before verification) | "
                "Unsupported rate after | Judge (1-5) | GigSim reply rate | p95 latency / proposal (s) | "
                "Cost / proposal (USD) | n |\n|---|---|---|---|---|---|---|---|---|---|\n")
        for label, key, p in (("V0", "v0_naive", prop_v0), ("Shipped (best)", "best", prop_best)):
            o = (p or {}).get("overall", {})
            m = main["variants"].get(key, {}).get("overall", {})
            out += (f"| {label} | {f(m.get('recall@5'))} | {f(m.get('ndcg@10'))} | "
                    f"{f(o.get('unsupported_claims_per_proposal_before'), 2)} | {f(o.get('unsupported_after'))} | "
                    f"{f(o.get('judge_overall_mean'), 2)} | {f(o.get('expected_reply_rate'))} | "
                    f"{f((o.get('latency_s_per_proposal') or {}).get('p95'), 1)} | {f(o.get('usd_per_proposal'), 4)} | "
                    f"{o.get('n', 0)} |\n")
    return out


def ablation_table() -> str:
    ab = load("rag_ablation.json")
    if not ab:
        return "_Ablation not run._\n"
    lines = ["| Config | Recall@5 | MRR | nDCG@10 |", "|---|---|---|---|"]
    for name, v in ab["variants"].items():
        o = v["overall"]
        lines.append(f"| {name} | {f(o['recall@5'])} | {f(o['mrr'])} | {f(o['ndcg@10'])} |")
    return "\n".join(lines) + "\n"


def persona_table() -> str:
    main = load("rag_main.json")
    if not main:
        return ""
    lines = ["| Variant | ai_ml_engineer | fullstack_web | data_analyst | real Kaggle posts | synthetic posts | >=4 requirements |",
             "|---|---|---|---|---|---|---|"]
    for name, v in main["variants"].items():
        bp, bs, br = v["by_persona"], v["by_source"], v["by_requirements"]
        lines.append(f"| {name} | {f(bp['ai_ml_engineer'].get('recall@5'))} | {f(bp['fullstack_web'].get('recall@5'))} | "
                     f"{f(bp['data_analyst'].get('recall@5'))} | {f(bs['real_kaggle'].get('recall@5'))} | "
                     f"{f(bs['synthetic'].get('recall@5'))} | {f(br['>=4'].get('recall@5'))} |")
    return "Recall@5 by persona and job type:\n\n" + "\n".join(lines) + "\n"


def decision_table() -> str:
    lines = ["| Profile | n (failed) | Bid F1 | 3-way acc. | Scam P / R | Injection detected | Price in fair band | p50 / p95 latency per job (s) | Tokens / job | USD / job |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    any_ = False
    for prof in ("open", "open_qwen"):
        d = load(f"decision_{prof}.json")
        if not d:
            continue
        any_ = True
        o = d["overall"]
        lines.append(f"| {prof} | {o['n']} ({o.get('errors', 0)}) | {f(o['bid_skip']['f1'])} | {f(o['bid_skip']['accuracy_3way'])} | "
                     f"{f(o['scam']['precision'])} / {f(o['scam']['recall'])} | {f(o['injection_detected']['rate'])} "
                     f"(n={o['injection_detected']['n']}) | {f(o['price_in_fair_band']['rate'])} "
                     f"(n={o['price_in_fair_band']['n']}) | {f(o['latency_s_per_job']['p50'], 1)} / "
                     f"{f(o['latency_s_per_job']['p95'], 1)} | {o['tokens_per_job']} | {f(o['usd_per_job'], 5)} |")
    return "\n".join(lines) + "\n" if any_ else "_Decision evaluation not run._\n"


def compare_table() -> str:
    c = load("multiagent_compare.json")
    if not c:
        return "_Multi-agent comparison not run._\n"
    lines = ["| System | n (failed) | Bid/skip accuracy | Scams bid on | Unsupported claims / proposal | Policy violations in output / proposal | GigSim reply prob. | Tokens / job | p50 latency (s) |",
             "|---|---|---|---|---|---|---|---|---|"]
    for k, v in c["systems"].items():
        lines.append(f"| {k} | {v['n']} ({v.get('errors', 0)}) | {f(v['bid_skip_accuracy'])} | {v['scams_bid_on']} | "
                     f"{f(v['unsupported_claims_per_proposal'], 2)} | {f(v['violations_in_output_per_proposal'], 2)} | "
                     f"{f(v['mean_p_reply'])} | {v['tokens_per_job']} | {f(v['latency_s']['p50'], 1)} |")
    return "\n".join(lines) + "\n"


def tool_table() -> str:
    t = load("tool_design.json")
    if not t:
        return "_Tool-design study not run._\n"
    lines = ["| Tool set | # tools | Tool-selection accuracy | Argument accuracy | Tokens / task |", "|---|---|---|---|---|"]
    for k, v in t["sets"].items():
        lines.append(f"| {k} | {v['n_tools']} | {f(v['selection_accuracy'])} | {f(v['argument_accuracy'])} | {v['tokens_per_task']} |")
    return "\n".join(lines) + f"\n\n{t['meta']['n_tasks']} tasks, model {next(iter(t['sets'].values()))['model']}.\n"


def injection_table() -> str:
    i = load("injection.json")
    if not i:
        return "_Injection evaluation not run._\n"
    return ("| Setting | Attack success rate |\n|---|---|\n"
            f"| Undefended (no delimiters, no rules, no guard) | {f(i['attack_success_undefended'])} |\n"
            f"| Delimiters + writer rules | {f(i['attack_success_delimiters_and_rules'])} |\n"
            f"| + claim verification + Compliance Guard (what reaches the client) | {f(i['attack_success_after_guard'])} |\n"
            f"\nn = {i['n']} planted-injection jobs.\n")


if __name__ == "__main__":
    print("# Results (generated by tools/results_tables.py from evals/results/)\n")
    for title, fn in [("Retrieval and proposals (B5)", rag_table), ("Ablation", ablation_table),
                      ("Breakdown", persona_table), ("Decisions and budgets", decision_table),
                      ("Multi-agent comparison (R&D C)", compare_table), ("Tool granularity (R&D A)", tool_table),
                      ("Prompt injection (E4)", injection_table)]:
        print(f"## {title}\n\n{fn()}")
