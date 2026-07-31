#!/usr/bin/env python3
"""Plot equity curve from /tmp/equity_curve.json as HTML."""
import json, math

with open('/tmp/equity_curve.json') as f:
    data = json.load(f)

# Parse timestamps and values
times = [d[0] for d in data]
vals = [d[1] for d in data]

# Find max drawdown
peak = vals[0]
mdd_val = 0
mdd_from = 0
mdd_to = 0
for i, v in enumerate(vals):
    if v > peak:
        peak = v
    dd = (peak - v) / peak * 100
    if dd > mdd_val:
        mdd_val = dd
        mdd_to = i
        # find peak index for this dd
        for j in range(i, -1, -1):
            if vals[j] == peak:
                mdd_from = j
                break

first_val = vals[0]
last_val = vals[-1]
roi = (last_val / first_val - 1) * 100

# Simplify: take every Nth point for plotting if > 2000
step = max(1, len(vals) // 1500)
plot_times = [times[i] for i in range(0, len(times), step)]
plot_vals = [vals[i] for i in range(0, len(vals), step)]

# Percent labels on x-axis: show month labels
months = []
for t in plot_times:
    m = t[5:7] + '.' + t[2:4]
    if not months or months[-1] != m:
        months.append(m)
    else:
        months.append('')

# Normalize values for plotting
min_v = min(vals)
max_v = max(vals)
rng = max_v - min_v if max_v > min_v else 1

html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Equity Curve</title>
<style>
  body {{ background:#1a1a2e; color:#eee; font-family:monospace; margin:20px }}
  .stats {{ display:flex; gap:20px; margin:20px 0; flex-wrap:wrap }}
  .stat {{ background:#16213e; padding:10px 20px; border-radius:8px; border-left:3px solid #0f3460 }}
  .stat .val {{ font-size:1.4em; font-weight:bold }}
  .stat .lbl {{ font-size:0.8em; color:#888 }}
  canvas {{ background:#0a0a1a; border-radius:8px; width:100%; height:500px }}
  .mdd-marker {{ color:#ff6b6b }}
  .roi-marker {{ color:#51cf66 }}
</style></head><body>
<h2>Equity Curve — Aggressive</h2>
<div class="stats">
  <div class="stat"><div class="lbl">Start</div><div class="val">{{:,.0f}}</div></div>
  <div class="stat"><div class="lbl">End</div><div class="val roi-marker">{{:,.0f}}</div></div>
  <div class="stat"><div class="lbl">ROI</div><div class="val roi-marker">+{roi:.1f}%</div></div>
  <div class="stat"><div class="lbl">Max DD</div><div class="val mdd-marker">{mdd_val:.2f}%</div></div>
  <div class="stat"><div class="lbl">Points</div><div class="val">{len(vals):,}</div></div>
</div>
<canvas id="c"></canvas>
<script>
const ctx = document.getElementById('c').getContext('2d');
const W = ctx.canvas.width = window.innerWidth - 40;
const H = ctx.canvas.height = 500;
const data = {json.dumps(plot_vals)};
const labels = {json.dumps(plot_times)};
const mddIdx = {mdd_to // step};
const startVal = {first_val};
const minV = {min_v};
const maxV = {max_v};
const pad = 0.05;
const yMin = minV * (1 - pad);
const yMax = maxV * (1 + pad);

function y(v) {{ return H - (v - yMin) / (yMax - yMin) * H; }}
function x(i) {{ return i / (data.length - 1) * W; }}

// Grid
ctx.strokeStyle = '#2a2a4a';
ctx.lineWidth = 0.5;
ctx.font = '11px monospace';
ctx.fillStyle = '#666';
for (let i = 0; i <= 4; i++) {{
    const yy = yMin + (yMax - yMin) * i / 4;
    ctx.beginPath(); ctx.moveTo(0, y(yy)); ctx.lineTo(W, y(yy)); ctx.stroke();
    ctx.fillText((yy/1000).toFixed(0) + 'K', 5, y(yy) - 3);
}}

// MDD marker area
ctx.fillStyle = 'rgba(255,50,50,0.08)';
const mddX = x(mddIdx);
ctx.fillRect(mddX, 0, W - mddX, H);

// Curve
ctx.beginPath();
ctx.strokeStyle = '#51cf66';
ctx.lineWidth = 1.5;
for (let i = 0; i < data.length; i++) {{
    const px = x(i), py = y(data[i]);
    i === 0 ? ctx.moveTo(px, py) : ctx.lineTo(px, py);
}}
ctx.stroke();

// Start/end dots
ctx.fillStyle = '#51cf66';
ctx.beginPath(); ctx.arc(x(0), y(data[0]), 3, 0, Math.PI*2); ctx.fill();
ctx.beginPath(); ctx.arc(x(data.length-1), y(data[data.length-1]), 3, 0, Math.PI*2); ctx.fill();

// MDD annotation
ctx.fillStyle = '#ff6b6b';
ctx.font = '12px monospace';
ctx.fillText('Max DD ' + {mdd_val:.2f} + '%', mddX + 10, 30);

// Month labels
ctx.fillStyle = '#444';
ctx.font = '10px monospace';
const labelStep = Math.max(1, Math.floor(data.length / 12));
for (let i = 0; i < labels.length; i += labelStep) {{
    if (labels[i]) {{
        ctx.fillText(labels[i], x(i) - 15, H - 5);
    }}
}}
</script>
</body></html>"""

with open('/tmp/equity_curve.html', 'w') as f:
    f.write(html)
print(f'HTML saved: /tmp/equity_curve.html ({len(html)} bytes)')
print(f'MDD peak→trough: index {mdd_from} → {mdd_to}')
