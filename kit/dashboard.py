"""看板渲染：把调研数据与生成数据渲染成一个离线 HTML（两个标签页：调研 / 生成）。

输入宽容：字段缺失显示"未提供"，不报错；不强制 JSON 结构，只要能取到对应信息。
数据由 kit/research.py（调研案例）与 kit/production.py（生成记录）生成，也可以手写 JSON 直接喂入。
"""
import html
import json

DIMS_RESEARCH = ["钩子", "人", "货", "场", "证明", "行动", "可复制"]  # 每项 0–2，合计 /14
GROUPS = ["A 模板", "B 点击强转化弱", "C 待验证", "D 边界"]


def _esc(s):
    return html.escape("" if s is None else str(s))


def _json_for_script(obj):
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


CSS = """
:root{--bg:#f6f7f7;--fg:#1d2420;--muted:#66736c;--card:#fff;--line:#e2e6e3;--accent:#147d61;--accent-bg:#d9f2e7;--warn:#b7791f;--bad:#c2410c;--ok:#15803d}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#101412;--fg:#e7ece9;--muted:#93a097;--card:#1a201d;--line:#2a332e;--accent:#4ade80;--accent-bg:#17352a;--warn:#fbbf24;--bad:#fb923c;--ok:#4ade80}}
:root[data-theme="dark"]{--bg:#101412;--fg:#e7ece9;--muted:#93a097;--card:#1a201d;--line:#2a332e;--accent:#4ade80;--accent-bg:#17352a;--warn:#fbbf24;--bad:#fb923c;--ok:#4ade80}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC","Helvetica Neue",sans-serif}
header{padding:16px 20px;border-bottom:1px solid var(--line);background:var(--card)}
header h1{margin:0;font-size:18px}
header .sub{color:var(--muted);font-size:13px}
nav.tabs{display:flex;gap:8px;padding:12px 20px;flex-wrap:wrap}
nav.tabs button{font:inherit;padding:8px 14px;border-radius:999px;border:1px solid var(--line);background:var(--card);color:var(--fg);cursor:pointer;min-height:40px}
nav.tabs button[aria-selected="true"]{background:var(--accent);color:#fff;border-color:var(--accent)}
main{padding:0 20px 40px;max-width:1200px;margin:0 auto}
.muted{color:var(--muted);font-size:13px}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0}
.chip{font-size:13px;padding:4px 10px;border-radius:999px;border:1px solid var(--line);background:var(--card)}
.chip.on{background:var(--accent-bg);border-color:var(--accent)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden;cursor:pointer;display:flex;flex-direction:column}
.card .thumb{aspect-ratio:9/16;max-height:300px;background:var(--line);position:relative;display:flex;align-items:center;justify-content:center}
.card .thumb img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.card .body{padding:10px;display:flex;flex-direction:column;gap:4px}
.card h3{font-size:14px;margin:0;line-height:1.4}
.kv{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;margin:12px 0}
.kv div{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px 10px}
.kv b{display:block;font-size:18px}
.tag{font-size:12px;padding:2px 8px;border-radius:999px;border:1px solid var(--line)}
.tag.pass{color:var(--ok);border-color:var(--ok)} .tag.repair{color:var(--warn);border-color:var(--warn)} .tag.escalate{color:var(--bad);border-color:var(--bad)}
.panel{position:fixed;inset:0;background:rgba(0,0,0,.35);display:none;z-index:50}
.panel.open{display:block}
.drawer{position:absolute;right:0;top:0;bottom:0;width:min(720px,100%);background:var(--bg);overflow:auto;padding:20px;box-shadow:-8px 0 24px rgba(0,0,0,.2)}
.drawer .close{position:sticky;top:0;float:right;font:inherit;min-width:44px;min-height:44px;border-radius:10px;border:1px solid var(--line);background:var(--card);color:var(--fg);cursor:pointer}
.sec{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;margin:12px 0}
.sec h4{margin:0 0 8px;font-size:15px}
dl.j{display:grid;grid-template-columns:auto 1fr;gap:6px 12px;margin:0;font-size:14px}
dl.j dt{color:var(--muted)} dl.j dd{margin:0}
.bar{height:10px;border-radius:999px;background:var(--line);overflow:hidden;margin:4px 0 10px}
.bar>span{display:block;height:100%;background:var(--accent)}
.bar.low>span{background:var(--bad)}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{color:var(--muted);font-weight:600}
.tablewrap{overflow-x:auto}
a{color:var(--accent)}
.hidden{display:none}
.pick{display:flex;gap:6px;align-items:center;min-height:40px;font-size:14px}
.btn2{font:inherit;min-height:40px;padding:6px 12px;border-radius:10px;border:1px solid var(--accent);background:var(--accent);color:#fff;cursor:pointer}
@media (max-width:600px){main{padding:0 14px 40px}.grid{grid-template-columns:repeat(2,1fr)}}
"""

JS = r"""
const DATA = JSON.parse(document.getElementById('data').textContent);
const $ = (s, r=document) => r.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = v => (v === null || v === undefined || v === '') ? '未提供' : (typeof v === 'number' ? v.toLocaleString('en-US') : esc(v));
const show = (v, suffix='') => (v === null || v === undefined || v === '') ? '未提供' : esc(v) + suffix;

function tabs(){
  const names = [['research','调研'],['production','生成']];
  const bar = $('#tabs');
  bar.innerHTML = names.map(([k,n]) => `<button role="tab" aria-selected="${k===state.tab}" data-tab="${k}">${n}</button>`).join('');
  bar.querySelectorAll('button').forEach(b => b.onclick = () => { state.tab = b.dataset.tab; render(); });
}
const state = {tab: DATA.research ? 'research' : 'production', group: '全部', panel: null, selected: new Set()};
try { (JSON.parse(localStorage.getItem('studio_sel')||'[]')).forEach(x => state.selected.add(x)); } catch(e) {}
function saveSel(){ try { localStorage.setItem('studio_sel', JSON.stringify([...state.selected])); } catch(e) {} }

function renderResearch(){
  const R = DATA.research; if(!R){ return '<p class="muted">还没有调研数据。</p>'; }
  const groups = ['全部', ...(R.groups || [])];
  const cases = (R.cases || []).filter(c => state.group === '全部' || c.group === state.group);
  const counts = {}; (R.cases||[]).forEach(c => counts[c.group] = (counts[c.group]||0)+1);
  const head = `<div class="kv">
    <div>硬筛通过<b>${fmt((R.cases||[]).length)}</b></div>
    <div>主题入选<b>${fmt((R.cases||[]).filter(c=>c.final_pass).length)}</b></div>
    <div>待验证<b>${fmt(counts['C 待验证']||0)}</b></div>
    <div>边界<b>${fmt(counts['D 边界']||0)}</b></div></div>
    <p class="muted">${esc(R.summary || '')}</p>`;
  const chips = `<div class="chips">${groups.map(g => `<span class="chip ${g===state.group?'on':''}" data-g="${esc(g)}">${esc(g)}${g==='全部'?'':' · '+(counts[g]||0)}</span>`).join('')}</div>`;
  const sel = state.selected;
  const cards = cases.map(c => {
    const ok = c.eligible;
    const checked = sel.has(c.id) ? 'checked' : '';
    const why = ok ? '' : `<span class="muted">不可进入制作：${esc((c.block_reasons||[]).join('；'))}</span>`;
    return `<article class="card" data-case="${esc(c.id)}">
      <div class="thumb">${c.cover ? `<img src="${esc(c.cover)}" referrerpolicy="no-referrer" loading="lazy" alt="" onerror="this.remove()">` : ''}<span class="muted">封面链接可能已过期</span></div>
      <div class="body"><span class="tag">${esc(c.group)}</span>${ok ? '<span class="tag pass">可进入制作</span>' : ''}<h3>${esc(c.title || c.id)}</h3>
      <span class="muted">@${esc(c.author||'未提供')} · 播放 ${fmt(c.metrics?.plays)} · 赞 ${fmt(c.metrics?.likes)}</span>
      <label class="pick" data-stop><input type="checkbox" data-sel="${esc(c.id)}" ${checked} ${ok ? '' : 'disabled'}> 选入制作</label>
      ${why}</div></article>`;
  }).join('');
  const selCount = [...sel].length;
  const exportBar = `<div class="sec"><b>已选 ${selCount} 条</b> <button class="btn2" id="exp">导出 selection.json</button>
     <span class="muted">把下载的文件放到项目目录的 selection.json，然后运行 loop.py select。</span></div>`;
  return head + exportBar + chips + `<div class="grid">${cards || '<p class="muted">这个分组没有案例。</p>'}</div>`;
}

function radar(dims){
  const names = Object.keys(dims || {});
  if(!names.length) return '<p class="muted">还没有七维拆解（需要 Gemini 拆解后显示）。</p>';
  const n = names.length, cx = 130, cy = 130, r = 95;
  const pt = (i, v) => { const a = -Math.PI/2 + i*2*Math.PI/n; const k = v/2*r; return [cx + k*Math.cos(a), cy + k*Math.sin(a)]; };
  const ring = [2,1].map(v => names.map((_,i)=>pt(i,v).join(',')).join(' ')).map(p=>`<polygon points="${p}" fill="none" stroke="currentColor" opacity=".15"/>`).join('');
  const poly = names.map((_,i)=>pt(i, dims[names[i]] ?? 0).join(',')).join(' ');
  const labels = names.map((k,i)=>{const [x,y]=pt(i,2.35); return `<text x="${x}" y="${y}" font-size="13" text-anchor="middle" fill="currentColor">${esc(k)} ${fmt(dims[k])}</text>`}).join('');
  const total = names.reduce((s,k)=>s+(dims[k]||0),0);
  return `<svg viewBox="0 0 260 260" width="100%" style="max-width:320px" role="img" aria-label="七维雷达">${ring}<polygon points="${poly}" fill="var(--accent-bg)" stroke="var(--accent)" stroke-width="2"/>${labels}</svg><p class="muted">合计 ${total} / ${2*n}</p>`;
}

function openCase(id){
  const c = (DATA.research.cases||[]).find(x => x.id === id); if(!c) return;
  const m = c.metrics || {};
  const j = c.judgement || {};
  const ev = (c.evidence || []).map(e => `<dt>${esc(e.dim)}</dt><dd>${esc(e.text)}${e.t?` <span class="muted">${esc(e.t)}</span>`:''}</dd>`).join('');
  const judge = ['怎么读','学到什么','我们的判断','对我们','边界'].map(k => j[k] ? `<dt>${k}</dt><dd>${esc(j[k])}</dd>` : '').join('');
  const v = c.verify || {};
  $('#drawer').innerHTML = `<button class="close" aria-label="关闭" data-close>✕</button>
    <h2 style="font-size:17px;margin:0 40px 6px 0">${esc(c.title || c.id)}</h2>
    <p class="muted">${esc(c.id)} · <span class="tag">${esc(c.group)}</span> · @${esc(c.author||'未提供')} · 发布 ${show(m.published)} · ${show(m.duration_s,' 秒')}</p>
    ${c.url?`<p><a href="${esc(c.url)}" target="_blank" rel="noopener">打开原视频 ↗</a></p>`:''}
    <div class="kv">
      <div>播放<b>${fmt(m.plays)}</b></div><div>点赞<b>${fmt(m.likes)}</b></div>
      <div>评论<b>${fmt(m.comments)}</b></div><div>收藏<b>${fmt(m.saves)}</b></div>
      <div>分享<b>${fmt(m.shares)}</b></div><div>粉丝<b>${fmt(m.followers)}</b></div>
      <div>转化 / CVR<b>未提供</b></div><div>ROI<b>未提供</b></div></div>
    <div class="sec"><h4>七维拆解（${esc(DIMS_NAME)}）</h4>${radar(c.dims)}</div>
    <div class="sec"><h4>我们的判断</h4>${judge ? `<dl class="j">${judge}</dl>` : '<p class="muted">还没有判断。</p>'}</div>
    <div class="sec"><h4>拆解证据</h4>${ev ? `<dl class="j">${ev}</dl>` : '<p class="muted">还没有带时间码的证据。</p>'}</div>
    <div class="sec"><h4>AI 验证（Gemini）</h4>${Object.keys(v).length ? `<dl class="j">${Object.entries(v).map(([k,val])=>`<dt>${esc(k)}</dt><dd>${esc(typeof val==='object'?JSON.stringify(val):val)}</dd>`).join('')}</dl>` : '<p class="muted">未验证。</p>'}</div>
    <div class="sec"><h4>风险</h4><p>${show(c.risk)}</p></div>`;
  $('#panel').classList.add('open');
}
const DIMS_NAME = '钩子 · 人 · 货 · 场 · 证明 · 行动 · 可复制，每项 0–2';

function renderProduction(){
  const P = DATA.production; if(!P){ return '<p class="muted">还没有生成数据。</p>'; }
  const rule = P.rule || {};
  const head = `<div class="sec"><h4>通过条件</h4><p class="muted">加权总分 ≥ ${show(rule.weighted_min)}；每个维度 ≥ ${show(rule.dimension_floor)}；没有否决项。先查一致性，再查穿帮，最后查交付。最多 ${show(rule.max_attempts)} 轮，超过交给人。</p></div>`;
  const briefs = (P.briefs || []).map(b => {
    const takes = (b.takes || []).map(t => {
      const sc = t.scores || {};
      const dims = Object.keys(sc).map(k => `<div><span class="muted">${esc(k)}</span> <b>${fmt(sc[k])}</b><div class="bar ${sc[k] < (rule.dimension_floor||6.5) ? 'low' : ''}"><span style="width:${Math.max(0,Math.min(10,sc[k]||0))*10}%"></span></div></div>`).join('');
      const vet = (t.vetoes || []).length ? `<p class="muted">否决：${esc(t.vetoes.join('、'))}</p>` : '';
      const dec = t.decision || '未提供';
      return `<article class="sec"><div><span class="tag ${esc(dec)}">${esc(dec)}</span> <b>${esc(t.id||'')}</b> <span class="muted">第 ${show(t.attempt)} 轮 · 加权 ${show(t.weighted)}${dec==='pass'?'':' · 阶段 '+show(t.stage)}</span>${t.needs_human_look?' <span class="tag repair">有无证据低分，请人看一眼</span>':''}</div>
        <p>${esc(t.note || '')}</p>${dims ? `<div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(160px,1fr))">${dims}</div>` : '<p class="muted">还没有评分。</p>'}${vet}
        ${t.reason ? `<p class="muted">${esc(t.reason)}</p>` : ''}
        ${t.video ? `<video controls preload="metadata" playsinline style="width:100%;max-width:280px;border-radius:8px;background:#000" src="${esc(t.video)}"></video><p><a href="${esc(t.video)}" target="_blank" rel="noopener">查看成片 ↗</a></p>` : '<p class="muted">没有成片文件。</p>'}</article>`;
    }).join('');
    return `<h3 style="font-size:15px;margin:18px 0 4px">${esc(b.title || b.id)}</h3><p class="muted">${esc(b.brief_note || '')}</p>${takes || '<p class="muted">没有成片。</p>'}`;
  }).join('');
  return head + (briefs || '<p class="muted">还没有生成记录。</p>');
}

function render(){
  tabs();
  const body = $('#body');
  body.innerHTML = state.tab === 'research' ? renderResearch() : renderProduction();
  body.querySelectorAll('.chip[data-g]').forEach(el => el.onclick = () => { state.group = el.dataset.g; render(); });
  body.querySelectorAll('[data-case]').forEach(el => el.onclick = e => { if (e.target.closest('[data-stop]')) return; openCase(el.dataset.case); });
  body.querySelectorAll('input[data-sel]').forEach(el => el.onchange = () => { el.checked ? state.selected.add(el.dataset.sel) : state.selected.delete(el.dataset.sel); saveSel(); render(); });
  const exp = body.querySelector('#exp');
  if (exp) exp.onclick = () => {
    const out = {selected: [...state.selected].sort(), exported_at: new Date().toISOString(), note: '由看板导出；loop.py select 会校验每条是否可进入制作'};
    const blob = new Blob([JSON.stringify(out, null, 2)], {type: 'application/json'});
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'selection.json'; a.click();
  };
}
document.addEventListener('click', e => {
  if (e.target.matches('[data-close]') || e.target.id === 'panel') $('#panel').classList.remove('open');
});
document.addEventListener('keydown', e => { if (e.key === 'Escape') $('#panel').classList.remove('open'); });
render();
"""


def render(data, title="Studio 看板"):
    research = data.get("research")
    production = data.get("production")
    payload = {"research": research, "production": production}
    parts = [
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">",
        f"<title>{_esc(title)}</title><style>{CSS}</style></head><body>",
        f"<header><h1>{_esc(title)}</h1><div class=\"sub\">{_esc(data.get('subtitle', ''))}</div></header>",
        "<nav class=\"tabs\" id=\"tabs\"></nav>",
        "<main id=\"body\"></main>",
        "<div class=\"panel\" id=\"panel\"><div class=\"drawer\" id=\"drawer\"></div></div>",
        f"<script type=\"application/json\" id=\"data\">{_json_for_script(payload)}</script>",
        f"<script>{JS}</script></body></html>",
    ]
    return "".join(parts)
