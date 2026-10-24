/**
 * Standard institutional definitions and tooltips for key trading concepts.
 */

export const GLOSSARY_TOOLTIPS = {
  PF: 'Profit Factor: Gross winning trades sum divided by gross losing trades sum. Values < 1.0 indicate negative expectation under friction.',
  R: 'R (Risk Multiple): Standardized risk unit based on initial stop distance (1.0R = initial risk budget).',
  OTE: 'Optimal Trade Entry: Fibonacci retracement levels 0.618, 0.705, and 0.786 used for high-probability discount/premium entries.',
  CE: 'Consequent Encroachment: The 50% midpoint of a Fair Value Gap (FVG) or wick imbalance acting as a magnet and institutional support/resistance.',
  OB: 'Order Block: The last opposing candle before displacement that represents institutional orders awaiting mitigation.',
  FVG: 'Fair Value Gap: A 3-candle price imbalance where candle 1 and candle 3 wicks do not overlap.',
  RR: 'Risk-to-Reward Ratio: Potential reward divided by initial stop risk (e.g. 1.5 RR = 1.5R potential reward per 1.0R risk).',
} as const;

export type GlossaryTerm = keyof typeof GLOSSARY_TOOLTIPS;
