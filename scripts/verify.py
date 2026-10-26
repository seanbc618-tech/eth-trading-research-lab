"""Run public-only regression, integration and export-boundary checks."""
from __future__ import annotations
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
sys.dont_write_bytecode = True


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def public_files():
    for path in sorted(ROOT.rglob("*")):
        relative = path.relative_to(ROOT)
        if any(part in {".git", "__pycache__", "outputs", ".venv"} for part in relative.parts):
            continue
        require(not path.is_symlink(), f"symlink in public export: {relative}")
        if path.is_file():
            yield path


def audit_export():
    manifest = json.loads((ROOT / "docs/EXTRACTION_MANIFEST.json").read_text())
    for item in manifest["files"]:
        path = ROOT / item["export"]
        require(hashlib.sha256(path.read_bytes()).hexdigest() == item["export_sha256"], f"extracted file changed: {item['export']}")
    credential_patterns = [
        r"-----BEGIN " + r"(?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        r"gh[pousr]_" + r"[A-Za-z0-9]{30,}",
        r"github_pat_" + r"[A-Za-z0-9_]{30,}",
        r"AKIA" + r"[A-Z0-9]{16}",
        r"sk-" + r"[A-Za-z0-9_-]{24,}",
        r"\b\d{7,12}:" + r"[A-Za-z0-9_-]{30,}\b",
    ]
    private_path = "/" + r"(?:Users|Volumes|home)/[^\s\"']+"
    checked = 0
    for path in public_files():
        relative = path.relative_to(ROOT)
        require(path.suffix in {".py", ".md", ".json", ".csv", ".html", ".txt", ""} or path.name in {".gitignore", "RELEASE_FILES.sha256"}, f"unexpected file: {relative}")
        require(not any(part.startswith(".env") or part in {".vps", "private"+"_alpha"} for part in relative.parts), f"private file: {relative}")
        content = path.read_text(encoding="utf-8")
        for pattern in credential_patterns + [private_path]:
            require(re.search(pattern, content) is None, f"sensitive pattern in {relative}")
        if path.suffix == ".py":
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules = [alias.name.split('.')[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    modules = [(node.module or '').split('.')[0]]
                else:
                    continue
                require(all(m in sys.stdlib_module_names or m in {"quantlab", "tests"} for m in modules), f"unexpected dependency in {relative}: {modules}")
        checked += 1
    return checked


def integrations():
    from decimal import Decimal as D
    from quantlab.demo import load_candles, run, canonical_hash
    from quantlab.market_data.replay import DeterministicReplayEngine, ReplayConfig, SignalAction, SignalProposal
    from quantlab.tutorial import TeachingMeanStrategy
    bars = load_candles(ROOT / "examples/synthetic_hourly.csv")
    with patch("socket.socket.connect", side_effect=AssertionError("network forbidden")), patch("socket.create_connection", side_effect=AssertionError("network forbidden")):
        report = run(bars)
        require(report == run(bars), "repeat run differs")
        reference = json.loads((ROOT / "sample_output/report.json").read_text())
        require(report == reference, "reference report does not reproduce")
        payload = dict(report); digest = payload.pop("result_sha256")
        require(canonical_hash(payload) == digest, "result hash mismatch")
        require(report["metrics"]["fill_count"] > 0, "demo generated no fills")
        metrics = report["metrics"]
        require(D(metrics["ending_equity"]) - D(metrics["starting_equity"]) == D(metrics["net_pnl"]), "equity identity failed")
        require(D(metrics["realized_pnl_gross"]) + D(metrics["unrealized_pnl_gross"]) - D(metrics["fees"]) == D(metrics["net_pnl"]), "PnL identity failed")
        blocked = run(bars, max_order_notional=D("100"))
        require(blocked["metrics"]["fill_count"] == 0 and blocked["metrics"]["risk_rejections"] > 0, "risk gate failed")
        require(D(blocked["metrics"]["net_pnl"]) == 0, "rejection invented PnL")
        require(all("order_notional_limit" in item["reasons"] for item in blocked["risk_decisions"]), "rejection reason missing")
        prefix = run(bars[:48])
        require(prefix["equity_curve"] == report["equity_curve"][:48], "future candles affected prior equity")
        replay = DeterministicReplayEngine(config=ReplayConfig("fixture", "teaching"), strategy_factory=TeachingMeanStrategy).run(bars)
        require(all(o.execution_candle_key.open_time > o.source_candle_key.open_time and o.priced_at >= o.eligible_at for o in replay.execution_opportunities), "same-bar execution")
        try:
            run(bars[:12] + bars[13:])
        except ValueError as error:
            require("INVALID_DECLARED_GAP" in str(error), "wrong gap error")
        else:
            raise AssertionError("data gap accepted")
        class NoTrades:
            def on_confirmed_candle(self, context):
                return None
        neutral = run(bars, strategy_factory=NoTrades, strategy_id="teaching-no-trades")
        require(neutral["metrics"]["fill_count"] == 0 and D(neutral["metrics"]["ending_equity"]) == 10000, "replacement plugin failed")
        class BuyOnce:
            def on_confirmed_candle(self, context):
                if len(context.history) == 1:
                    return SignalProposal(SignalAction.LONG, "teaching-buy-once")
                return None
        holding = run(bars, strategy_factory=BuyOnce, strategy_id="teaching-buy-once")
        require(holding["metrics"]["fill_count"] == 1 and D(holding["metrics"]["open_base_quantity"]) > 0, "open position not retained")
        free = run(bars, fee_rate=D(0), slippage_bps=D(0))
        require(D(free["metrics"]["fees"]) == 0 and D(free["metrics"]["ending_equity"]) > D(metrics["ending_equity"]), "cost stress comparison failed")
    spec = importlib.util.spec_from_file_location("fixture_generator", ROOT / "examples/generate_data.py")
    generator = importlib.util.module_from_spec(spec); spec.loader.exec_module(generator)
    with tempfile.TemporaryDirectory() as tmp:
        regenerated = Path(tmp)/"fixture.csv"
        generator.generate(regenerated)
        require(regenerated.read_bytes() == (ROOT/"examples/synthetic_hourly.csv").read_bytes(), "fixture regeneration differs")
    return {"repeatability": True, "reference_match": True, "accounting_identity": True,
            "risk_rejections": blocked["metrics"]["risk_rejections"], "prefix_causality": True,
            "next_bar_only": True, "gap_rejection": True, "plugin_replacement": True,
            "terminal_marking": True, "cost_comparison": True, "network_blocked_demo": True,
            "fixture_regeneration": True, "result_sha256": digest}


def main():
    checked = audit_export()
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    require(result.wasSuccessful(), "unit tests failed")
    checks = integrations()
    print(json.dumps({"unit_tests": result.testsRun, "scanned_files": checked, "integration": checks}, indent=2))


if __name__ == "__main__":
    main()
