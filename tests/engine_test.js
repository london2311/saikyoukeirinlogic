// 統合エンジン（v103/v104）の検証テスト:  node tests/engine_test.js
// index.html から較正テーブルとエンジン部分を取り出して、確率の整合性・ケリー・見送り判定・
// Python学習結果との一致を確認する。
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');

const cal = html.match(/const KEIRIN_V101_ODDS_CAL = (\{.*?\});\n/)[1];
const start = html.indexOf('// v104: 過去レース実績で学習した統計モデル');
const engineSrc = html.slice(html.lastIndexOf('<script>', start) + 8, html.indexOf('</script>', start));
const ctx = {};
new Function('ctx', `const KEIRIN_V101_ODDS_CAL = ${cal};\n${engineSrc}\nctx.K = KEIRIN_V103; ctx.V104 = V104; ctx.M = KEIRIN_V104_MODEL;`)(ctx);
const { K, V104, M } = ctx;

let passed = 0;
const test = (name, fn) => { fn(); passed++; console.log('✔', name); };

const nums = [1, 2, 3, 4, 5, 6, 7];
const outs = K.outcomes(nums);
const pl = (o, s) => { let rem = nums.reduce((t, n) => t + s[n], 0), p = 1; for (const n of o) { p *= s[n] / rem; rem -= s[n]; } return p; };
const truth = { 1: 5, 2: 3, 3: 2, 4: 1.5, 5: 1, 6: 0.8, 7: 0.6 };
const odds3t = outs.map(o => ({ type: '3連単', nums: o, odds: Math.max(1.0, +(0.75 / pl(o, truth)).toFixed(1)) }));

test('融合分布の総和=1、3着内率の総和=3', () => {
  const R = K.run({ nums, modelProb3t: (a, b, c) => pl([a, b, c], { ...truth, 3: 4 }), allOdds: odds3t });
  assert(R.ok);
  assert(Math.abs(R.riders.reduce((t, r) => t + r.win.fused, 0) - 1) < 1e-9);
  assert(Math.abs(R.riders.reduce((t, r) => t + r.top3, 0) - 3) < 1e-9);
});

test('券種整合: 2車単(a,b) = Σ 3連単(a,b,k)', () => {
  const extra = [{ type: '2車単', nums: [1, 2], odds: 5.0 }];
  const R = K.run({ nums, modelProb3t: (a, b, c) => pl([a, b, c], truth), allOdds: odds3t.concat(extra) });
  const t2 = R.tickets.find(t => t.type === '2車単');
  const s3 = R.tickets.filter(t => t.type === '3連単' && t.nums[0] === 1 && t.nums[1] === 2).reduce((t, x) => t + x.pFused, 0);
  assert(Math.abs(t2.pFused - s3) < 1e-12);
});

test('線形プール: 1着率が市場とモデルの間に収まる', () => {
  const R = K.run({ nums, modelProb3t: (a, b, c) => pl([a, b, c], { ...truth, 1: 2, 5: 4 }), allOdds: odds3t, settings: { alpha: 0.6, beta: 0.4, fusionType: 'linear' } });
  R.riders.forEach(r => {
    const lo = Math.min(r.win.model, r.win.market) - 1e-12, hi = Math.max(r.win.model, r.win.market) + 1e-12;
    assert(r.win.fused >= lo && r.win.fused <= hi, `rider ${r.num}`);
  });
});

test('モデル=市場なら見送り（偽の妙味を出さない）', () => {
  const R = K.run({ nums, modelProb3t: (a, b, c) => pl([a, b, c], truth), allOdds: odds3t });
  assert(R.ken && R.bets.length === 0);
});

test('Benter型: 3連単から見て割安な2車複を検出し、2車複だけを買う', () => {
  const pairP = outs.filter(o => o[0] === 1 && o[1] === 2 || o[0] === 2 && o[1] === 1).reduce((t, o) => t + pl(o, truth), 0);
  const cheap = { type: '2車複', nums: [1, 2], odds: +(0.75 / pairP * 1.8).toFixed(1) };   // 本来の1.8倍の配当
  const fair = { type: '2車単', nums: [2, 1], odds: +(0.75 / pl([2, 1, 3], truth) * 0.5).toFixed(1) };
  const R = K.run({ nums, modelProb3t: (a, b, c) => pl([a, b, c], truth), allOdds: odds3t.concat([cheap, fair]),
    settings: { fusionType: 'benter', alpha: 0.03, beta: 1.03 } });
  assert(R.bets.length === 1 && R.bets[0].type === '2車複', JSON.stringify(R.bets.map(b => b.type)));
  assert(R.hitPicks['2車複'][0].p > 0 && R.hitPicks['ワイド'].length === 3);
});

test('一括ケリー: 単一買い目は解析解 (b·p−q)/b と一致', () => {
  const s = K.multiKelly([{ win: [0], oddsEff: 4 }], [0.3, 0.7]);
  assert(Math.abs(s[0] - (3 * 0.3 - 0.7) / 3) < 1e-4);
});

test('v104モデル: Python学習結果（reference_race.json）と一致', () => {
  const ref = JSON.parse(fs.readFileSync(path.join(root, 'analysis', 'reference_race.json'), 'utf8'));
  const venuePref = html.match(new RegExp(`'${ref.venue}':'([^']+)'`));
  const d = V104.dist(M, ref.riders, venuePref ? venuePref[1] : null);
  assert(Math.abs(Object.values(d).reduce((t, v) => t + v, 0) - 1) < 1e-9);
  ref.top10.forEach(([k, p]) => assert(Math.abs(d[k] - p) < 1e-5, k));
});

console.log(`\n${passed} tests passed`);
