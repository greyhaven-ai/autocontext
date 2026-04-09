/**
 * Simulation dashboard HTML (AC-449).
 *
 * Single-page vanilla HTML/CSS/JS dashboard. No framework, no build step.
 * Fetches data from /api/simulations endpoints and renders charts.
 *
 * NOTE: All data rendered comes from local simulation artifacts produced
 * by autoctx simulate — not user input. The innerHTML usage is intentional
 * for rendering trusted local data as HTML charts and tables.
 */

export function renderDashboardHtml(): string {
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>autocontext — simulation dashboard</title>
<style>
  :root { --bg: #0d1117; --fg: #c9d1d9; --accent: #58a6ff; --green: #3fb950; --red: #f85149; --border: #30363d; --card: #161b22; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; background: var(--bg); color: var(--fg); padding: 1.5rem; }
  h1 { font-size: 1.4rem; margin-bottom: 1rem; color: var(--accent); }
  h2 { font-size: 1.1rem; margin-bottom: 0.5rem; }
  .simulation-dashboard { max-width: 960px; margin: 0 auto; }
  .sim-list { display: grid; gap: 0.75rem; margin-bottom: 1.5rem; }
  .sim-card { background: var(--card); border: 1px solid var(--border); border-radius: 6px; padding: 1rem; cursor: pointer; transition: border-color 0.15s; }
  .sim-card:hover { border-color: var(--accent); }
  .sim-card .name { font-weight: 600; }
  .sim-card .score { float: right; font-size: 1.2rem; font-weight: 700; }
  .sim-card .meta { font-size: 0.85rem; color: #8b949e; margin-top: 0.25rem; }
  .detail { background: var(--card); border: 1px solid var(--border); border-radius: 6px; padding: 1.5rem; margin-bottom: 1.5rem; display: none; }
  .detail.active { display: block; }
  .back-btn { background: none; border: 1px solid var(--border); color: var(--accent); padding: 0.4rem 0.8rem; border-radius: 4px; cursor: pointer; margin-bottom: 1rem; font-size: 0.85rem; }
  .score-big { font-size: 2.5rem; font-weight: 700; }
  .score-big.good { color: var(--green); }
  .score-big.bad { color: var(--red); }
  .charts { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin: 1rem 0; }
  @media (max-width: 640px) { .charts { grid-template-columns: 1fr; } }
  .chart-box { background: var(--bg); border: 1px solid var(--border); border-radius: 4px; padding: 1rem; }
  .chart-box h3 { font-size: 0.9rem; margin-bottom: 0.75rem; color: #8b949e; }
  .bar-row { display: flex; align-items: center; margin-bottom: 0.4rem; font-size: 0.85rem; }
  .bar-label { width: 100px; text-align: right; padding-right: 0.5rem; color: #8b949e; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .bar-track { flex: 1; height: 18px; background: var(--border); border-radius: 3px; overflow: hidden; }
  .bar-fill { height: 100%; border-radius: 3px; }
  .bar-value { width: 50px; padding-left: 0.5rem; font-weight: 600; }
  table { width: 100%; border-collapse: collapse; font-size: 0.85rem; margin-top: 0.5rem; }
  th, td { padding: 0.4rem 0.6rem; text-align: left; border-bottom: 1px solid var(--border); }
  th { color: #8b949e; font-weight: 600; }
  .tag { display: inline-block; padding: 0.15rem 0.4rem; border-radius: 3px; font-size: 0.75rem; }
  .tag-warn { background: #f8514920; color: var(--red); }
  #loading { text-align: center; padding: 2rem; color: #8b949e; }
</style>
</head>
<body>
<div class="simulation-dashboard">
  <h1>simulation dashboard</h1>
  <div id="loading">Loading simulations...</div>
  <div id="sim-list" class="sim-list"></div>
  <div id="sim-detail" class="detail">
    <button class="back-btn" id="back-btn">&larr; back to list</button>
    <div id="detail-content"></div>
  </div>
  <div id="sweep-chart"></div>
  <div id="sensitivity-chart"></div>
</div>
<script>
// All data rendered is from local simulation artifacts (trusted).
// No user-supplied content is injected.
const API = window.location.origin;
const esc = (s) => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');

document.getElementById('back-btn').addEventListener('click', showList);

async function loadList() {
  try {
    const res = await fetch(API + '/api/simulations');
    if (!res.ok) { document.getElementById('loading').textContent = 'No simulations found.'; return; }
    const sims = await res.json();
    document.getElementById('loading').style.display = 'none';
    const list = document.getElementById('sim-list');
    if (!sims.length) { list.textContent = 'No simulations found. Run autoctx simulate first.'; return; }
    list.replaceChildren();
    for (const s of sims) {
      const card = document.createElement('div');
      card.className = 'sim-card';
      card.addEventListener('click', () => loadDetail(s.name));
      const scoreSpan = document.createElement('span');
      scoreSpan.className = 'score';
      scoreSpan.style.color = s.score >= 0.7 ? 'var(--green)' : s.score < 0.4 ? 'var(--red)' : 'var(--fg)';
      scoreSpan.textContent = s.score.toFixed(2);
      const nameDiv = document.createElement('div');
      nameDiv.className = 'name';
      nameDiv.textContent = s.name;
      const metaDiv = document.createElement('div');
      metaDiv.className = 'meta';
      metaDiv.textContent = s.family + ' \\u00b7 ' + s.status;
      card.append(scoreSpan, nameDiv, metaDiv);
      list.appendChild(card);
    }
  } catch (e) { document.getElementById('loading').textContent = 'Failed to load: ' + e.message; }
}

function bar(label, pct, color, value) {
  const row = document.createElement('div');
  row.className = 'bar-row';
  const lbl = document.createElement('span');
  lbl.className = 'bar-label';
  lbl.textContent = label;
  const track = document.createElement('div');
  track.className = 'bar-track';
  const fill = document.createElement('div');
  fill.className = 'bar-fill';
  fill.style.width = pct + '%';
  fill.style.background = color;
  track.appendChild(fill);
  const val = document.createElement('span');
  val.className = 'bar-value';
  val.textContent = value;
  row.append(lbl, track, val);
  return row;
}

async function loadDetail(name) {
  document.getElementById('sim-list').style.display = 'none';
  const detail = document.getElementById('sim-detail');
  detail.classList.add('active');
  const content = document.getElementById('detail-content');
  content.replaceChildren();
  try {
    const res = await fetch(API + '/api/simulations/' + encodeURIComponent(name) + '/dashboard');
    const d = await res.json();

    const h = document.createElement('h2');
    h.textContent = d.name;
    const scoreDiv = document.createElement('div');
    scoreDiv.className = 'score-big ' + (d.overallScore >= 0.7 ? 'good' : d.overallScore < 0.4 ? 'bad' : '');
    scoreDiv.textContent = d.overallScore.toFixed(2);
    const reasoning = document.createElement('p');
    reasoning.style.cssText = 'margin:0.5rem 0;color:#8b949e';
    reasoning.textContent = d.reasoning;
    content.append(h, scoreDiv, reasoning);

    const charts = document.createElement('div');
    charts.className = 'charts';

    if (d.sensitivityRanking && d.sensitivityRanking.length) {
      const box = document.createElement('div');
      box.className = 'chart-box';
      const title = document.createElement('h3');
      title.textContent = 'Variable Sensitivity';
      box.appendChild(title);
      const max = d.sensitivityRanking.length;
      d.sensitivityRanking.forEach((v, i) => {
        box.appendChild(bar(v, Math.round(((max - i) / max) * 100), 'var(--accent)', '#' + (i + 1)));
      });
      charts.appendChild(box);
    }

    if (d.dimensionScores && Object.keys(d.dimensionScores).length) {
      const box = document.createElement('div');
      box.className = 'chart-box';
      const title = document.createElement('h3');
      title.textContent = 'Dimension Scores';
      box.appendChild(title);
      for (const [dim, score] of Object.entries(d.dimensionScores)) {
        const s = Number(score);
        const color = s >= 0.7 ? 'var(--green)' : s < 0.4 ? 'var(--red)' : 'var(--accent)';
        box.appendChild(bar(dim, Math.round(s * 100), color, s.toFixed(2)));
      }
      charts.appendChild(box);
    }
    content.appendChild(charts);

    if (d.sweepChart && d.sweepChart.length) {
      const box = document.createElement('div');
      box.className = 'chart-box';
      box.style.marginTop = '1rem';
      const title = document.createElement('h3');
      title.textContent = 'Sweep Results';
      box.appendChild(title);
      const table = document.createElement('table');
      const thead = document.createElement('thead');
      const hr = document.createElement('tr');
      for (const th of ['Variables', 'Score', 'Reasoning']) {
        const cell = document.createElement('th');
        cell.textContent = th;
        hr.appendChild(cell);
      }
      thead.appendChild(hr);
      const tbody = document.createElement('tbody');
      for (const p of d.sweepChart) {
        const tr = document.createElement('tr');
        const varTd = document.createElement('td');
        varTd.textContent = Object.entries(p.variables).map(([k,v]) => k + '=' + v).join(', ');
        const scoreTd = document.createElement('td');
        scoreTd.style.fontWeight = '600';
        scoreTd.style.color = p.score >= 0.7 ? 'var(--green)' : p.score < 0.4 ? 'var(--red)' : 'var(--fg)';
        scoreTd.textContent = p.score.toFixed(2);
        const reasonTd = document.createElement('td');
        reasonTd.textContent = p.reasoning;
        tr.append(varTd, scoreTd, reasonTd);
        tbody.appendChild(tr);
      }
      table.append(thead, tbody);
      box.appendChild(table);
      content.appendChild(box);
    }

    if (d.warnings && d.warnings.length) {
      const warnDiv = document.createElement('div');
      warnDiv.style.marginTop = '1rem';
      for (const w of d.warnings) {
        const tag = document.createElement('span');
        tag.className = 'tag tag-warn';
        tag.textContent = w;
        warnDiv.appendChild(tag);
        warnDiv.append(' ');
      }
      content.appendChild(warnDiv);
    }
  } catch (e) { content.textContent = 'Error: ' + e.message; }
}

function showList() {
  document.getElementById('sim-detail').classList.remove('active');
  document.getElementById('sim-list').style.display = '';
}

loadList();
</script>
</body>
</html>`;
}
