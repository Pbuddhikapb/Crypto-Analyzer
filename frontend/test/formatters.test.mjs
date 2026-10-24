import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { getPricePrecision, formatPrice, getMinMove, formatUTCClock } from './formatters.compiled.js';

describe('Price Formatter', () => {
  test('derives price precision from symbol price according to institutional rules', () => {
    // >= 1000: 1 decimal
    assert.equal(getPricePrecision(1000), 1);
    assert.equal(getPricePrecision(81671.7), 1);
    assert.equal(getPricePrecision(2397.45), 1);

    // >= 1: 2 decimals
    assert.equal(getPricePrecision(999.99), 2);
    assert.equal(getPricePrecision(184.24), 2);
    assert.equal(getPricePrecision(1.32), 2);
    assert.equal(getPricePrecision(1.0), 2);

    // >= 0.1: 4 decimals
    assert.equal(getPricePrecision(0.999), 4);
    assert.equal(getPricePrecision(0.2161), 4);
    assert.equal(getPricePrecision(0.1), 4);

    // < 0.1: 6 decimals
    assert.equal(getPricePrecision(0.0999), 6);
    assert.equal(getPricePrecision(0.081234), 6);
    assert.equal(getPricePrecision(0.00012), 6);
  });

  test('formats prices with derived precision based on reference price', () => {
    // For BTC (symbol price >= 1000 -> 1 decimal everywhere)
    const btcRef = 81671.7;
    assert.equal(formatPrice(81671.72, btcRef), '81671.7');
    assert.equal(formatPrice(81600.25, btcRef), '81600.3');
    assert.equal(formatPrice(82000, btcRef), '82000.0');

    // For SOL (symbol price >= 1 -> 2 decimals everywhere)
    const solRef = 184.24;
    assert.equal(formatPrice(184.246, solRef), '184.25');
    assert.equal(formatPrice(180, solRef), '180.00');

    // For ADA (symbol price >= 0.1 -> 4 decimals everywhere)
    const adaRef = 0.2161;
    assert.equal(formatPrice(0.21618, adaRef), '0.2162');
    assert.equal(formatPrice(0.22, adaRef), '0.2200');

    // For sub-0.1 coin (symbol price < 0.1 -> 6 decimals everywhere)
    const dogeRef = 0.0812;
    assert.equal(formatPrice(0.0812345, dogeRef), '0.081235');
    assert.equal(formatPrice(0.08, dogeRef), '0.080000');
  });

  test('computes correct minMove for lightweight-charts', () => {
    assert.equal(getMinMove(81671.7), 0.1);
    assert.equal(getMinMove(184.2), 0.01);
    assert.equal(getMinMove(0.216), 0.0001);
    assert.equal(getMinMove(0.08), 0.000001);
  });
});

describe('Header UTC Clock', () => {
  test('formats real UTC time and labels it with UTC', () => {
    // 2026-10-23T15:40:06.000Z
    const fixedDate = new Date(Date.UTC(2026, 9, 23, 15, 40, 6));
    assert.equal(formatUTCClock(fixedDate), '15:40:06 UTC');

    // Midnight UTC
    const midnight = new Date(Date.UTC(2026, 0, 1, 0, 0, 0));
    assert.equal(formatUTCClock(midnight), '00:00:00 UTC');

    // Single digit padding
    const singleDigits = new Date(Date.UTC(2026, 4, 5, 9, 7, 3));
    assert.equal(formatUTCClock(singleDigits), '09:07:03 UTC');
  });

  test('uses getUTC methods and does not rely on local timezone', () => {
    const timestamp = Date.UTC(2026, 9, 23, 22, 15, 30);
    const result = formatUTCClock(timestamp);
    assert.equal(result, '22:15:30 UTC');
    assert.ok(result.endsWith(' UTC'));
  });
});
