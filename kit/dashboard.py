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
.take{display:flex;gap:14px;align-items:flex-start;flex-wrap:wrap;margin:10px 0}
.take .tk{width:200px;max-width:100%;aspect-ratio:9/16;border-radius:10px;background:#000;flex:none}
.take .tk.none{display:flex;align-items:center;justify-content:center;color:#aaa;font-size:13px}
.take .tinfo{flex:1;min-width:220px}
.take .tinfo p{margin:4px 0}
ul.iss{margin:6px 0;padding-left:18px;font-size:14px}
.dimgrid{grid-template-columns:repeat(auto-fill,minmax(150px,1fr))}
details.past{border-top:1px solid var(--line);margin-top:10px;padding-top:8px}
details.past summary{cursor:pointer;min-height:36px;display:flex;align-items:center;color:var(--accent);font-size:14px}
details.past .take .tk{width:140px}
.prompt{font-size:13px;white-space:pre-wrap;word-break:break-word;color:var(--muted)}
button.chip{font:inherit;cursor:pointer;color:var(--fg);min-height:36px}
.tag.tier-H{color:var(--ok);border-color:var(--ok);font-weight:600} .tag.tier-M{color:var(--accent);border-color:var(--accent);font-weight:600}
.tag.tier-L{color:var(--warn);border-color:var(--warn);font-weight:600} .tag.tier-X{color:var(--muted);font-weight:600}
.tag.g-good{color:var(--ok);border-color:var(--ok)} .tag.g-minor{color:var(--accent);border-color:var(--accent)}
.tag.g-low{color:var(--warn);border-color:var(--warn)} .tag.g-severe{color:var(--bad);border-color:var(--bad)}
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
  const names = [['research','调研'],['production','生成'],['guide','使用说明']];
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
  const TIER_LEGEND = [['H','可直接替换产品或人物'],['M','保留必选项，场景动作可换'],['L','只借钩子、证明、行动的逻辑'],['X','只作参考，不复刻']];
  const legend = `<div class="sec"><b>档位</b> ${TIER_LEGEND.map(([k,t]) => `<span class="tag tier-${k}">${k}</span> <span class="muted">${esc(t)}</span>`).join('　')}</div>`;
  const chips = `<div class="chips">${groups.map(g => `<span class="chip ${g===state.group?'on':''}" data-g="${esc(g)}">${esc(g)}${g==='全部'?'':' · '+(counts[g]||0)}</span>`).join('')}</div>`;
  const sel = state.selected;
  const cards = cases.map(c => {
    const ok = c.eligible;
    const checked = sel.has(c.id) ? 'checked' : '';
    const why = ok ? '' : `<span class="muted">不可进入制作：${esc((c.block_reasons||[]).join('；'))}</span>`;
    return `<article class="card" data-case="${esc(c.id)}">
      <div class="thumb">${c.cover ? `<img src="${esc(c.cover)}" referrerpolicy="no-referrer" loading="lazy" alt="" onerror="this.remove()">` : ''}${c.cover ? (c.cover_local ? '' : '<span class="muted">封面链接可能已过期，运行 covers.py backfill 补封面</span>') : '<span class="muted">没有封面</span>'}</div>
      <div class="body"><span class="tag tier-${esc(c.tier||'X')}">${esc(c.tier||'X')} 档</span> <span class="tag">${esc(c.group)}</span>${ok ? '<span class="tag pass">可进入制作</span>' : ''}<h3>${esc(c.title || c.id)}</h3>
      <span class="muted">@${esc(c.author||'未提供')} · 播放 ${fmt(c.metrics?.plays)} · 赞 ${fmt(c.metrics?.likes)}</span>
      <span class="muted">来源：${c.source === 'manual' ? (c.local_file ? '本地文件 ' + esc(c.local_file) : '手动链接 ' + esc(c.url||'')) : '调研（TikHub）' + (c.url ? ' · ' + esc(c.url) : '')}</span>
      ${c.source === 'manual' ? `<span class="muted">产品关系：${esc(c.relation || '未说明')}</span>` : ''}
      <label class="pick" data-stop><input type="checkbox" data-sel="${esc(c.id)}" ${checked} ${ok ? '' : 'disabled'}> 选入制作</label>
      <p class="muted">用法：${esc(c.use || '')}</p>
      ${why}</div></article>`;
  }).join('');
  const selCount = [...sel].length;
  const exportBar = `<div class="sec"><b>已勾选 ${selCount} 条（仅供浏览）</b>
     <span class="muted">请用 products.py select 记录对标，或用 intake.py plan --pick 分配生成范围。</span></div>`;
  return head + legend + exportBar + chips + `<div class="grid">${cards || '<p class="muted">这个分组没有案例。</p>'}</div>`;
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

function takeBody(t, rule, compact){
  const sc = t.scores || {};
  const floor = rule.dimension_floor || 6.5;
  const dims = Object.keys(sc).map(k => `<div><span class="muted">${esc(k)}</span> <b>${fmt(sc[k])}</b><div class="bar ${sc[k] < floor ? 'low' : ''}"><span style="width:${Math.max(0,Math.min(10,sc[k]||0))*10}%"></span></div></div>`).join('');
  const vet = (t.vetoes || []).length ? `<p class="muted">否决：${esc(t.vetoes.join('、'))}</p>` : '';
  const issues = (t.issues || []).slice(0, compact ? 3 : 8).map(i => `<li>${i.at ? `<span class="muted">${esc(i.at)}</span> ` : ''}${esc(i.detail)}</li>`).join('');
  const more = (t.issues || []).length > (compact ? 3 : 8) ? `<p class="muted">另有 ${(t.issues.length - (compact ? 3 : 8))} 条问题</p>` : '';
  const cost = `${fmt(t.cost_cny)} 元${t.cost_estimated ? '（估算）' : ''}${t.submits > 1 ? ` · 提交 ${t.submits} 次` : ''}`;
  const humanNames = {accept:'接受',iterate:'迭代',redo:'重做',hold:'暂缓',abandon:'放弃',release:'放行'};
  const human = t.human
    ? `<p><span class="tag">人工：${esc(humanNames[t.human.decision] || t.human.decision)}</span> ${esc(t.human.reason || '')} <span class="muted">${esc(t.human.decided_at || '')}</span></p>`
    : '<p class="muted">未人工判定</p>';
  const video = t.video
    ? `<video controls preload="metadata" playsinline class="tk" src="${esc(t.video)}"></video>`
    : '<div class="tk none">没有成片文件</div>';
  return `<div class="take">${video}
    <div class="tinfo"><div><span class="tag g-${esc(t.grade)}">${esc(t.grade_text || '')}</span> <b>${esc(t.id || '')}</b></div>
      <p class="muted">第 ${show(t.attempt)} 轮 · 加权 ${show(t.weighted)} · ${cost}</p>
      ${human}
      ${vet}${issues ? `<ul class="iss">${issues}</ul>${more}` : (t.grade === 'unreviewed' ? '<p class="muted">还没有评审结果。</p>' : (t.reason ? `<p class="muted">${esc(t.reason)}</p>` : ''))}
      ${compact ? '' : (dims ? `<div class="grid dimgrid">${dims}</div>` : '')}
      ${t.video ? `<p><a href="${esc(t.video)}" target="_blank" rel="noopener">在新窗口打开 ↗</a></p>` : ''}</div></div>`;
}

function statLine(st){
  if (!st) return '';
  const rate = st.scrap_rate == null ? '未评审' : Math.round(st.scrap_rate * 100) + '%';
  const per = st.cost_per_good == null ? '尚无通过' : st.cost_per_good + ' 元';
  return {rate, per};
}

function renderGuide(){
  const steps = [
    ['1 找对标', '调研搜到的，或你直接给的视频链接/本地文件。本地文件保存在项目目录外的位置时，请告诉我路径；看板只显示路径，不上传。'],
    ['2 拆解与定档', 'Agent 按七个维度拆解，给出 H/M/L/X。拆解是推断，同一条可能在相邻两档间浮动，重要对标请你确认档位。'],
    ['3 选范围', '你选：指定哪几条、指定哪几个档位，或全部。Agent 给出条数、预计金额和缺口，等你确认。'],
    ['4 起草提示词', 'Agent 判断对标产品与我方产品是否适配（同款 / 适配改写 / 不适配）。不适配的不生成，交给你决定。'],
    ['5 生成与审判', '按确认的条数生成视频，三位专家逐项判定。通过只表示可以交给你看。'],
    ['6 修订', '不达标，Agent 按具体问题改提示词，再生成、再审判。到上限或预算用完就停下来问你。'],
    ['7 你决定', '每一步的"继续、换方向、停止"由你决定。Agent 不会在预算之外付费，也不会降低通过线。'],
  ];
  const agentHuman = [
    ['Agent 会主动问你', '产品卡没确认、预算要超出、对标产品和我方产品是否同类、要不要为一条不达标的成片再花钱。'],
    ['Agent 不会擅自做', '超预算付费、改通过线或专家标准、同一个任务重复提交、把视频上传到公开地址。'],
    ['你需要提供', '产品卡与产品锚点、对标的产品关系、本地视频的位置、预算上限（如超过默认 5 元）、选哪些对标或档位。'],
  ];
  const stepHtml = steps.map(([t, d]) => `<div class="sec"><h4>${esc(t)}</h4><p>${esc(d)}</p></div>`).join('');
  const ahHtml = agentHuman.map(([t, d]) => `<div class="sec"><h4>${esc(t)}</h4><p>${esc(d)}</p></div>`).join('');
  const quote = `<div class="sec"><h4>开工前想问你的（三个）</h4><ul class="iss">
    <li>每条视频最多重做几轮？建议 2 轮（首次 + 1 次修订）。</li>
    <li>一个品的预算上限是多少？默认 5 元，约够 3–4 条 10 秒视频。</li>
    <li>你想要几条成品？需要多设几条时，我会先算出多出来的钱。</li></ul></div>`;
  return `<p class="muted">这一页说明怎么用这套流程，以及 Agent 与你之间的分工。</p>${stepHtml}<h3 style="font-size:15px">Agent 与你的分工</h3>${ahHtml}${quote}`;
}

function renderProduction(){
  const P = DATA.production; if(!P){ return '<p class="muted">还没有生成数据。</p>'; }
  const rule = P.rule || {};
  const st = P.stats || {};
  const sl = statLine(st) || {};
  const GT = P.grade_text || {};
  const humanRate = st.human_agreement_rate == null ? '样本不足' : `${st.human_agreed}/${st.human_compared}（${Math.round(st.human_agreement_rate * 100)}%）`;
  const textTokens = (st.prompt_tokens || 0) + (st.completion_tokens || 0);
  const kpi = `<div class="kv">
    <div>成片总数<b>${show(st.takes)}</b></div><div>通过<b>${show(st.good)}</b></div>
    <div>废片率<b>${esc(sl.rate || '未提供')}</b></div>
    <div>人工判定数<b>${show(st.human_count)}</b></div>
    <div>专家与人工一致率<b>${esc(humanRate)}</b></div>
    <div>视频（元）<b>${show(st.video_cny)} 元</b><span class="muted">${show(st.video_seconds)} 秒</span></div>
    <div>文本模型（次数 / token，价格未配置）<b>${show(st.text_calls)} 次 / ${fmt(textTokens)}</b><span class="muted">prompt ${fmt(st.prompt_tokens)} · completion ${fmt(st.completion_tokens)} · 失败 ${fmt(st.text_failures)}</span></div>
    <div>TikHub 次数<b>${show(st.tikhub_calls)}</b></div>
    <div>重复提交次数<b>${show(st.duplicate_submit_count)}</b></div>
    <div>每条成品单价<b>${esc(sl.per || '未提供')}</b></div></div>`;
  const head = `<p class="muted">通过条件：加权总分 ≥ ${show(rule.weighted_min)}；每个维度 ≥ ${show(rule.dimension_floor)}；没有否决项。废片率 = 未通过 ÷ 已评审。成品单价 = 这一组的全部花费 ÷ 通过条数。</p>`;
  const f = state.gfilter || '全部';
  const counts = (st.by_grade || {});
  const chips = ['全部', 'good', 'minor', 'low', 'severe', 'unreviewed'].filter(g => g === '全部' || counts[g]).map(g =>
    `<button class="chip ${f === g ? 'on' : ''}" data-gf="${g}">${g === '全部' ? '全部' : esc(GT[g] || g)}${g === '全部' ? '' : ' ' + counts[g]}</button>`).join('');
  const series = (P.series || []).filter(s => f === '全部' || s.takes.some(t => t.grade === f)).map(s => {
    const fin = s.takes.find(t => t.id === s.final) || s.takes[0];
    const ss = s.stats || {}; const l = statLine(ss);
    const past = (s.past || []);
    const pastCost = Math.round(past.reduce((a, t) => a + (t.cost_cny || 0), 0) * 100) / 100;
    const open = (fin.grade === 'severe' || f === 'severe' || f === 'low') && past.length ? ' open' : '';
    const pastHtml = past.length ? `<details class="past"${open}><summary>过往废片 ${past.length} 条 · 花费 ${pastCost} 元（点开查看）</summary>${past.map(t => takeBody(t, rule, true)).join('')}</details>` : '';
    const prompt = fin.prompt ? `<details class="past"><summary>这条成片的提示词</summary><p class="prompt">${esc(fin.prompt)}</p></details>` : '';
    return `<section class="sec series"><h4>${esc(s.title || s.id)}</h4>
      <p class="muted">系列 ${esc(s.id)} · 共 ${ss.takes} 条 · 通过 ${ss.good} · 废片率 ${esc(l.rate)} · 花费 ${show(ss.cost_cny)} 元 · 成品单价 ${esc(l.per)}</p>
      ${takeBody(fin, rule, false)}${prompt}${pastHtml}</section>`;
  }).join('');
  return kpi + head + `<div class="chips">${chips}</div>` + (series || '<p class="muted">没有符合条件的成片。</p>');
}

function render(){
  tabs();
  const body = $('#body');
  body.innerHTML = state.tab === 'research' ? renderResearch() : (state.tab === 'guide' ? renderGuide() : renderProduction());
  body.querySelectorAll('.chip[data-g]').forEach(el => el.onclick = () => { state.group = el.dataset.g; render(); });
  body.querySelectorAll('.chip[data-gf]').forEach(el => el.onclick = () => { state.gfilter = el.dataset.gf; render(); });
  body.querySelectorAll('[data-case]').forEach(el => el.onclick = e => { if (e.target.closest('[data-stop]')) return; openCase(el.dataset.case); });
  body.querySelectorAll('input[data-sel]').forEach(el => el.onchange = () => { el.checked ? state.selected.add(el.dataset.sel) : state.selected.delete(el.dataset.sel); saveSel(); render(); });
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
