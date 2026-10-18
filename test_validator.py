"""
test_validator.py — Unit tests for the Backtest Validator Agent.
"""

import os
import tempfile
import unittest

from validator.output_parser import parse_backtest_output, parse_trade_rows
from validator import db_manager
from validator.validation import (
    run_all_checks,
    compute_config_hashes,
    check_config_drift,
)
from validator.report_generator import generate_report, save_report

SAMPLE_SINGLE_OUTPUT = """
=============================================================================================================================
 [BACKTEST REPORT] XRPUSDT (15m) | MODE: BAR-LEVEL | EVALUATED BARS: 2250 | TOTAL TRADES: 4
=============================================================================================================================
 TOTAL TRADES:         4      | WIN RATE:            75.0% (3W / 1L)
 PROFIT FACTOR:        3.50   | CUMULATIVE RETURN:   +8.50% (After 0.15% fee drag)
 AVG R-MULTIPLE:       +0.85R | CLOSED MAX DRAWDOWN:  1.20%
 M2M MAX DRAWDOWN:     2.10% | WORST INTRA-BAR DD:    2.50%
 LONG PERFORMANCE:     2 trades (Win Rate: 100.0%)
 SHORT PERFORMANCE:    2 trades (Win Rate: 50.0%)
-----------------------------------------------------------------------------------------------------------------------------
 [WHALE & ABNORMAL VOLUME ANALYSIS]
 Total Losses:         1
  - Losses from Abnormal Whale Volume (>2.5x Vol): 0 (0.0% of all losses)
  - Losses from Normal Technical Invalidation:      1 (100.0% of all losses)
  -> Technical Strategy Baseline Win Rate (Excluding Whale Blowouts): 75.0%
-----------------------------------------------------------------------------------------------------------------------------
ENTRY TIME       | SIDE  | ENTRY      | EXIT       | REASON       | NET %    | R-MULT  | VOL RATIO | CLASSIFICATION        
-----------------------------------------------------------------------------------------------------------------------------
2026-03-01 10:00 | LONG  | 0.5000     | 0.5200     | TAKE_PROFIT  |   +3.85% |  +1.20R |      1.2x | [+] TARGET HIT        
2026-03-01 14:00 | SHORT | 0.5250     | 0.5100     | TAKE_PROFIT  |   +2.70% |  +0.95R |      0.8x | [+] TARGET HIT        
2026-03-02 09:00 | LONG  | 0.5150     | 0.5050     | STOP_LOSS    |   -2.09% |  -1.00R |      1.0x | [-] NORMAL STOP       
2026-03-02 18:00 | SHORT | 0.5000     | 0.4850     | TAKE_PROFIT  |   +2.85% |  +1.00R |      1.5x | [+] TARGET HIT        
=============================================================================================================================
"""


class TestOutputParser(unittest.TestCase):
    def test_parse_single_report(self):
        result = parse_backtest_output(SAMPLE_SINGLE_OUTPUT)
        self.assertEqual(result["mode"], "single")
        self.assertEqual(result["symbol"], "XRPUSDT")
        self.assertEqual(result["timeframe"], "15m")
        self.assertEqual(result["total_trades"], 4)
        self.assertEqual(result["win_rate"], 75.0)
        self.assertEqual(result["profit_factor"], 3.50)
        self.assertEqual(result["cumulative_return_pct"], 8.50)
        self.assertEqual(result["avg_r_multiple"], 0.85)
        self.assertEqual(result["closed_max_drawdown_pct"], 1.20)
        self.assertEqual(result["m2m_max_drawdown_pct"], 2.10)
        self.assertEqual(result["worst_intra_bar_dd_pct"], 2.50)

    def test_parse_trade_rows(self):
        trades = parse_trade_rows(SAMPLE_SINGLE_OUTPUT)
        self.assertEqual(len(trades), 4)
        self.assertEqual(trades[0]["side"], "LONG")
        self.assertEqual(trades[0]["reason"], "TAKE_PROFIT")
        self.assertAlmostEqual(trades[0]["net_return_pct"], 3.85)


class TestDBManagerAndValidation(unittest.TestCase):
    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        self.db_path = self.temp_db.name
        db_manager.init_db(self.db_path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_db_flow(self):
        parsed = parse_backtest_output(SAMPLE_SINGLE_OUTPUT)
        trades = parse_trade_rows(SAMPLE_SINGLE_OUTPUT)

        run_id = db_manager.save_run(
            parsed_report=parsed,
            cli_args={"symbol": "XRPUSDT"},
            stderr="",
            exit_code=0,
            duration_s=1.5,
            db_path=self.db_path,
        )
        self.assertIsInstance(run_id, int)
        self.assertGreater(run_id, 0)

        db_manager.save_trades(run_id, trades, db_path=self.db_path)
        runs = db_manager.get_last_n_runs(5, db_path=self.db_path)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["win_rate"], 75.0)

    def test_report_generation(self):
        parsed = parse_backtest_output(SAMPLE_SINGLE_OUTPUT)
        trades = parse_trade_rows(SAMPLE_SINGLE_OUTPUT)
        report_md = generate_report(
            run_id=1,
            parsed_report=parsed,
            alerts=[],
            trade_rows=trades,
            research_notes="Test note",
        )
        self.assertIn("Backtest Validation Report", report_md)
        self.assertIn("XRPUSDT", report_md)
        self.assertIn("Summary Metrics", report_md)


if __name__ == "__main__":
    unittest.main()
