H1a: REJECTED on dev data (net EV < 0, CI includes 0); matched baseline bug found after the run, corrected p = 0.9703; no tuning performed
H1b: REJECTED on dev data (net EV < 0, CI includes 0); matched baseline bug found after the run, corrected p = 0.6535; no tuning performed
H1a/H1b: originally REJECTED; simulator silently dropped trades open > 120 bars (found after seeing results); re-evaluated with spec unchanged: H1a REJECTED (1,055 trades, mean net R +0.0495, 95% CI [-0.0162, +0.1187], net PF 1.14, matched p = 0.0669, dropped fraction 0.0), H1b PASSED DEV GATE (see robustness) (832 trades, mean net R +0.0788, 95% CI [+0.0021, +0.1590], net PF 1.22, matched p = 0.0240, dropped fraction 0.0). Holdout not yet used.
H1b passed the dev gate by a narrow margin; not validated; holdout pending.
H1b (Donchian4hN55): 10,000 matched baseline passes completed. matched p = 0.0195, Monte Carlo SE = 0.0014, null median = +0.0003R, null std = 0.0372R. p + 2*SE = 0.0223 (below 0.025: True). Verdict: PASSED DEV GATE (see robustness) (p < 0.025, threshold=0.025). Holdout pending.
Universes U4/U5 defined on 2026-10-28 before any results for them were viewed, for operational monitoring and future hypotheses. H1 primary evidence stays on U10.

H1b (Donchian4hN55) Holdout Run Preregistration:
- Strategy: Donchian4hN55 (source blob hash: 991dd2aff1df5eb14e06214ecf7597078e947cf4)
- Commit Hash: c72980905c24d4eacf2c6a8b6eccdc880fe1e0f2
- Dependency Blob Hashes: indicators.py (1c403c972155b1dc722619b789f12d31d123ac17), engine/exits.py (1f8a8882f88039a44a762dc1286d1f7d680ac0e0)
- Effective Execution Config: Identical to dev run (ExitConfig: trailing_1r, trail_dist_r=1.0; Friction: taker 0.05%, maker 0.02%, slippage 0.03%, funding 0.01%/8h; Sizing: fixed_risk 1.0%, max 3 open positions, max 2 directional, max leverage 3.0x, cooldown 3 bars).
- Out-of-Sample Holdout Window: post-2026-04-18 00:00:00 UTC through 2026-10-19 23:00:00 UTC (185.0 days, 4,439 1h bars, 1,109 4h bars across 10 symbols in U10; 0 gaps).
- Expected Sample Size & Statistical Power: At dev trade rate (832 trades / 5.29 yrs = 157.28 trades/yr), expected holdout trade count is ~80 trades (79.66). Standard error is ~0.1276R. Statistical power at dev mean (+0.0788R) and SD (1.1415R) is only ~8.97% at alpha=0.025 and ~15.21% at alpha=0.050. Achieving 80% power at alpha=0.025 would require ~1,648 trades (~10.5 years).
- Holdout Decision Rule: [USER TO DECIDE]
  * Option 1 (Strict Preregistered Gate): Net Expectancy R > 0 AND matched rank p < 0.025 (note: high statistical hurdle; ~91% Type II false negative risk due to ~80-trade sample size).
  * Option 2 (Standard Alpha Threshold): Net Expectancy R > 0 AND matched rank p < 0.050 (standard single-tailed hurdle; power remains low at ~15.2%).
  * Option 3 (Directional Capital Preservation & Expectancy): Total Net Realized R > 0 (or Mean Net R > 0) AND Net Profit Factor > 1.0 AND Max Drawdown < 10R (evaluates whether edge remains net positive and survives friction out-of-sample without requiring unattainable p-values on short holdout).
