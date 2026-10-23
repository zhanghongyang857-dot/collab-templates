// 御前看板：每 5 秒拉一次项目数据并重绘；展开状态保留。
// /p/<id>/archive 是归档页：只看归档的目标、任务、议题，打开时加载一次，不轮询。
// 服务端给的 *Html 字段（来自 Markdown，已转义）用 innerHTML，其余一律转义。
(function () {
  const [, id, archiveSeg] = location.pathname.match(/^\/p\/([0-9a-f]+)(\/archive)?/) || [];
  const ARCHIVE = !!archiveSeg;
  const app = document.getElementById("app");
  const COLS = [
    {name: "待开始", match: (s) => s.startsWith("已交接")},
    {name: "执行中", match: (s) => s.startsWith("执行中")},
    {name: "待验收", match: (s) => s.startsWith("待验收")},
    {name: "需处理", match: (s) => /^(需补充|未通过|受阻)/.test(s)},
    {name: "通过", match: (s) => s.startsWith("通过")},
  ];
  const STALE_MIN = 10;
  let openTask = ARCHIVE ? (location.hash.match(/^#((?:G\d+-)?T\d+)$/) || [])[1] || null : null;
  let openGoal = null;
  let openIssue = null;
  let recTab = "runs";
  // 选中的目标：只在「目标」总览里选，泳道、任务、派活记录都跟着它；不选时任务和记录看全部，泳道展开排第一的目标
  let selGoal = (location.hash.match(/^#(G\d+)$/) || [])[1] || null;
  const openSpeech = new Set();
  const CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩";
  const circ = (n) => CIRCLED[n - 1] || String(n);
  const openRuns = new Set();
  let data = null;
  let lastSig = "";

  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const strip = (h) => String(h || "").replace(/<[^>]+>/g, "");
  const secs = (iso) => (iso ? Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000) : null);
  function dur(s) {
    if (s == null) return "—";
    s = Math.round(s);
    if (s < 60) return s + " 秒";
    if (s < 3600) return Math.floor(s / 60) + " 分" + (s % 60 ? " " + (s % 60) + " 秒" : "");
    return Math.floor(s / 3600) + " 小时 " + Math.floor((s % 3600) / 60) + " 分";
  }
  function tok(t) {
    if (!t || !t.total) return "—";
    const n = t.total, s = n >= 10000 ? (n / 10000).toFixed(1).replace(/\.0$/, "") + "万" : String(n);
    return t.cached ? `${s} · 缓存${Math.round((100 * t.cached) / n)}%` : s;
  }
  function tokDetail(t) {
    if (!t || !t.total) return "token 未知";
    const parts = [`token ${tok(t)}`, `输入 ${t.input || 0}`, `缓存 ${t.cached || 0}`, `输出 ${t.output || 0}`];
    if (t.calls) parts.push(`调用 ${t.calls} 次`);
    if (t.context) parts.push(`最后上下文 ${t.context}`);
    if (t.cost_usd) parts.push(`约 ${t.cost_usd} 美元`);
    return parts.join(" · ");
  }
  function sumTok(runs) {
    const s = {total: 0, cached: 0};
    runs.forEach((r) => { if (r.tokens) { s.total += r.tokens.total || 0; s.cached += r.tokens.cached || 0; } });
    return s;
  }
  const hm = (iso) => (iso ? iso.slice(5, 16).replace("T", " ") : "—");
  function statusPill(s) {
    const cls = s === "运行中" ? "run" : s === "完成" || s.startsWith("通过") ? "ok"
      : /失败|异常|未通过/.test(s) ? "bad" : /中断|需补充|受阻/.test(s) ? "warn" : "";
    return `<span class="pill ${cls}">${s === "运行中" ? '<span class="dot"></span>' : ""}${esc(s)}</span>`;
  }

  function card(title, body, opts = {}) {
    return `<section class="card ${opts.cls || ""}"><div class="card-head"><h2>${title}</h2>${opts.meta ? `<span class="meta">${opts.meta}</span>` : ""}</div>${body}</section>`;
  }

  // 「待你决定」一条的约定格式：编号｜要决定什么｜可选方案与代价｜总管建议｜验收意见｜提出者；不合格式的原样显示
  function pendingCard(html) {
    const f = html.split("｜").map((x) => x.trim());
    if (f.length < 4) return `<div class="pend raw">${html}</div>`;
    let [id, what, opts, sug, chk, by] = f;
    const sample = /^（示例）/.test(strip(id));
    id = id.replace(/^（示例）\s*/, "");
    const pick = (strip(sug).match(/[A-Z]/) || [])[0];
    const list = opts.split(/[；;]\s*(?=[A-Z][\s　])/).map((o) => {
      const m = strip(o).match(/^([A-Z])[\s　]/);
      const on = m && m[1] === pick;
      return `<li class="${on ? "pick" : ""}">${o}${on ? ' <span class="sug">总管建议</span>' : ""}</li>`;
    }).join("");
    const plainId = strip(id).trim();
    const link = /^(G\d+-)?T\d+$/.test(plainId) && data.cards.some((t) => t.id === plainId) ? ` data-task="${esc(plainId)}"`
      : /^G\d+$/.test(plainId) ? ` data-goal="${esc(plainId)}"` : /^I\d+$/.test(plainId) ? ` data-issue="${esc(plainId)}"` : "";
    const extra = pick ? "" : `<div class="note">${sug}</div>`;
    const km = strip(what).match(/^(受阻|批准[^：:]*|裁决|返工|授权)[：:]\s*/);
    const kind = km ? km[1] : "";
    if (km) what = what.replace(/^[^：:]*[：:]\s*/, "");
    const kcls = /受阻/.test(kind) ? "bad" : /返工/.test(kind) ? "warn" : "run";
    const check = chk && !/^[—\-\s]*$/.test(strip(chk)) ? `<div class="note">验收意见：${chk}</div>` : "";
    return `<div class="pend ${kind ? "k-" + kcls : ""}"><div class="ph"><span class="tid ${link ? "tlink" : ""}"${link}>${id}</span>${kind ? `<span class="pill ${kcls}">${esc(kind)}</span>` : ""}${sample ? '<span class="pill">示例</span>' : ""}<span class="pw">${what}</span>${by ? `<span class="meta">提出：${by}</span>` : ""}</div>
      <ul class="popts">${list}</ul>${extra}${check}</div>`;
  }

  // 校验结果（tools/check.py）：错误会挡住派活和 Claude 的每轮结束，提醒只显示
  function renderCheck(d) {
    const items = d.check || [];
    if (!items.length) return "";
    const errs = items.filter((x) => x.level === "error"), warns = items.filter((x) => x.level !== "error");
    const li = (x) => `<li class="${x.level === "error" ? "e" : "w"}"><span class="loc">${esc(x.file)}${x.line ? ":" + x.line : ""}</span>${esc(x.msg)}</li>`;
    const title = errs.length ? `校验：${errs.length} 个错误${warns.length ? `，${warns.length} 个提醒` : ""}` : `校验：${warns.length} 个提醒`;
    const note = errs.length ? '<div class="note">有错误时派活脚本不派活，Claude 每轮结束会被要求先改好。</div>' : "";
    return card(title, `<ul class="checks">${errs.concat(warns).map(li).join("")}</ul>${note}`, {cls: errs.length ? "check-bad" : "alert"});
  }

  function renderPending(d) {
    const body = d.pending.length ? d.pending.map(pendingCard).join("") : `<div class="empty">没有需要你拍板的事</div>`;
    return card("待你决定", body, {cls: "pending", meta: d.pending.length ? d.pending.length + " 项" : ""});
  }

  // 「现在」：先说等谁做什么，再是下一步，现状只露 3 行
  function renderNow(d) {
    const goals = activeGoals(d);
    let b = goals.length ? goals.map((g) => `<div class="nowbig"><span class="tid">${esc(g.id)}</span>${nowLine(d, g)}</div>`).join("") : "";
    b += `<h4>▶ 下一步</h4>${d.next ? `<div>${d.next}</div>` : `<div class="empty">未填写</div>`}`;
    if (d.status.length) {
      const li = (x) => `<li>${x}</li>`;
      b += `<h4>现状</h4><ul class="plain">${d.status.slice(0, 3).map(li).join("")}</ul>`;
      if (d.status.length > 3) b += `<details class="more"><summary>其余 ${d.status.length - 3} 条</summary><ul class="plain">${d.status.slice(3).map(li).join("")}</ul></details>`;
    }
    return card("现在", b, {cls: "now"});
  }

  // 顶部摘要：目标、任务各状态数、在跑的派活、token 合计
  function renderStrip(d) {
    const goals = d.goals.filter((g) => !/^(完成|取消)/.test(g.state));
    const counts = COLS.map((c) => [c.name, d.cards.filter((t) => c.match(t.state || "")).length]).filter(([, n]) => n);
    const running = d.runs.filter((r) => r.status === "运行中").length;
    const total = sumTok(d.runs);
    const items = [
      `<span class="si"><b>${goals.length}</b> 个目标在推进</span>`,
      `<span class="si">任务 ${counts.length ? counts.map(([k, n]) => `${k} <b>${n}</b>`).join(" · ") : "<b>0</b>"}</span>`,
      running ? `<span class="si live"><span class="dot"></span><b>${running}</b> 个派活在跑</span>` : `<span class="si">没有派活在跑</span>`,
      `<span class="si">派活 <b>${d.runs.length}</b> 次 · token <b>${tok(total).split(" ·")[0]}</b></span>`,
    ];
    return `<div class="strip">${items.join("")}</div>`;
  }

  function daysLeft(due) {
    const m = String(due || "").match(/(\d{4}-)?(\d{1,2})-(\d{1,2})/);
    if (!m) return "";
    const now = new Date();
    const y = m[1] ? +m[1].slice(0, 4) : now.getFullYear();
    const d = new Date(y, +m[2] - 1, +m[3]);
    const n = Math.ceil((d - new Date(now.getFullYear(), now.getMonth(), now.getDate())) / 86400000);
    return n < 0 ? `已过 ${-n} 天` : n === 0 ? "今天截止" : `还剩 ${n} 天`;
  }


  function goalRuns(d, g) {
    const ids = new Set(g.stages.flatMap((s) => s.tasks));
    return d.runs.filter((r) => ids.has(r.task));
  }

  // ---------- 多目标：总览一行一个，按「待你决定 > 受阻 > 进行中 > 待批准 > 暂停」再按优先排；只展开一个目标的泳道 ----------
  const SORT = (g) => (g.alert.pend ? 0 : g.alert.block ? 1 : /^进行中/.test(g.state) ? 2 : /^待批准/.test(g.state) ? 3 : 4);
  function goalTasks(d, g) { return new Set(g.stages.flatMap((s) => s.tasks)); }
  function goalInfo(d, g) {
    const ids = goalTasks(d, g);
    const pend = d.pending.filter((h) => { const k = strip(h.split("｜")[0]).replace(/^（示例）\s*/, "").trim(); return k === g.id || ids.has(k); }).length;
    const block = g.stages.some((s) => s.status === "受阻");
    const dep = (g.head["依赖"] || "").match(/G\d+/g) || [];
    const waiting = dep.filter((x) => { const o = d.goals.find((q) => q.id === x); return o && !/^完成/.test(o.state); });
    const prio = parseInt(g.head["优先"], 10);
    return {...g, alert: {pend, block}, waiting, prio: isNaN(prio) ? 99 : prio, runs: goalRuns(d, g)};
  }
  function activeGoals(d) {
    return d.goals.filter((g) => !/^(完成|取消)/.test(g.state)).map((g) => goalInfo(d, g))
      .sort((a, b) => SORT(a) - SORT(b) || a.prio - b.prio || a.id.localeCompare(b.id));
  }
  function stageDots(g) {
    const cls = {"通过": "ok", "受阻": "bad", "返工": "warn", "待验收": "now", "进行中": "now"};
    const done = g.stages.filter((s) => s.status === "通过").length;
    return `<span class="dots">${g.stages.map((s) => `<i class="${cls[s.status] || ""}" title="阶段 ${s.no} ${esc(s.seat)}：${esc(s.status)}"></i>`).join("")}</span><span class="dn">${done}/${g.stages.length}</span>`;
  }

  function renderGoals(d) {
    const goals = activeGoals(d);
    const done = d.goals.filter((g) => /^完成/.test(g.state)).length;
    if (!goals.length) {
      if (done) return card("目标", `<div class="empty">已完成 ${done} 个目标，没有在推进的</div>`);
      return d.cards.length ? card("目标", '<div class="empty">还没有目标卡，所以没有流程图。流程图来自目标卡的「流程」表：先建 G00x 目标卡（待批准），总裁批准后再派 G00x-Tn。</div>') : "";
    }
    const focus = goals.find((g) => g.id === selGoal) || goals[0];
    const rows = goals.map((g) => {
      const due = g.head["截止"] && !/^[—\-\s]*$/.test(g.head["截止"]) ? g.head["截止"] : "";
      const alerts = (g.alert.pend ? `<span class="pill run">待你决定 ${g.alert.pend}</span>` : "") + (g.alert.block ? '<span class="pill bad">受阻</span>' : "")
        + (g.runs.some((r) => r.override) ? `<span class="pill warn">偏离 ${g.runs.filter((r) => r.override).length}</span>` : "");
      return `<div class="grow ${g.id === selGoal ? "on" : g.id === focus.id ? "auto" : ""}" data-goal="${esc(g.id)}">
        <div class="gname"><span class="tid">${esc(g.id)}</span><span class="tt">${esc(g.title)}</span>${statusPill(g.state || "—")}${g.prio < 99 ? `<span class="prio">优先 ${g.prio}</span>` : ""}</div>
        <div class="gprog">${stageDots(g)}</div>
        <div class="gnow">${nowLine(d, g)}</div>
        <div class="galert">${alerts}</div>
        <div class="gcost">${g.runs.length ? `派活 ${g.runs.length} · ${tok(sumTok(g.runs)).split(" ·")[0]}` : "未派活"}${due ? `<div>截止 ${esc(due)}${daysLeft(due) ? " · " + daysLeft(due) : ""}</div>` : ""}</div>
      </div>`;
    }).join("");
    let body = `<div class="glist">${rows}</div>`;
    body += `<div class="gfocus"><div class="gfh"><b>${esc(focus.id)} ${esc(focus.title)}</b><span class="meta">流程泳道</span>
      <span class="tlink gdet" data-gdet="${esc(focus.id)}">${openGoal === focus.id ? "收起框架" : "看框架和偏离"}</span></div>
      ${openGoal === focus.id ? renderGoalDetail(focus, focus.runs.filter((r) => r.override)) : ""}${renderLanes(d, focus)}</div>`;
    return card("目标", body, {meta: `${goals.length} 个在推进${done ? ` · 已完成 ${done}` : ""} · ${selGoal ? "再点一次取消选中" : "点一行选中，任务和记录跟着切换"}`});
  }

  // ---------- 泳道流程：按席位分道（总裁 / 总管 / 执行 / 验收），执行按并行任务分子道，从上到下是时间 ----------
  let openNode = null;
  const LEAD = /^(claude|总管)/i, BOSS = /^(总裁|用户|本人)/;
  function laneOf(row, verifier) {
    const who = strip(row["经办人"] || "").trim();
    if (BOSS.test(who)) return "boss";
    if (LEAD.test(who)) return "lead";
    return /^(通过|需补充|未通过)/.test(strip(row["状态"] || "")) && verifier ? "check" : "exec";
  }
  function nodeCls(state, last) {
    if (/^(受阻|需补充|未通过)/.test(state)) return "bad";
    if (last && !/^通过/.test(state)) return "now";
    return "ok";
  }
  function goalLanes(d, g) {
    const nodes = [], edges = [], used = new Set();
    const put = (lane, sub, row) => { while (used.has(lane + sub + ":" + row)) row++; used.add(lane + sub + ":" + row); return row; };
    const add = (n) => { n.i = nodes.length; nodes.push(n); return n.i; };
    const approved = !g.state.startsWith("待批准");
    let row = 0;
    const draft = add({lane: "lead", sub: 0, row: put("lead", 0, row++), label: "起草框架", cls: "ok", info: `总管起草 ${g.id} 的目标和流程`});
    const appr = add({lane: "boss", sub: 0, row: put("boss", 0, row++), label: approved ? "批准框架" : "待批准", cls: approved ? "ok" : "now",
      info: approved ? `总裁批准 ${g.id}：${strip(g.head["依据"] || "")}` : "等总裁批准框架，批准前不派发"});
    edges.push([draft, appr]);
    let ends = [appr], maxSub = 1, hasCheck = false;
    const runsOf = (id) => d.runs.filter((r) => r.task === id);
    g.stages.forEach((s) => {
      const tasks = s.taskInfo;
      const start = row, newEnds = [];
      if (!tasks.length) {
        const lane = /验收/.test(s.seat) && !LEAD.test(s.who) && !/总管/.test(s.who) ? "check" : /汇报|总管/.test(s.seat + s.who) ? "lead" : "exec";
        if (lane === "check") hasCheck = true;
        const prevDone = g.stages.filter((x) => x.no < s.no).every((x) => x.status === "通过");
        const n = add({lane, sub: 0, row: put(lane, 0, start), label: `${circ(s.no)} ${s.seat}`, cls: s.status === "通过" ? "ok" : prevDone && approved ? "now" : "todo", stage: s,
          info: `阶段 ${circ(s.no)}「${s.seat}」· ${s.who || "—"}：${strip(s.output)}（还没有任务）`});
        ends.forEach((e) => edges.push([e, n]));
        ends = [n]; row = nodes[n].row + 1; return;
      }
      maxSub = Math.max(maxSub, tasks.length);
      tasks.forEach((t, k) => {
        const flow = t.flow || [];
        const verifier = !!strip(flow.map((r) => r["经办人"]).join(""));
        let r0 = start, prev = null;
        const rows = flow.length ? flow : [{"状态": t.state, "经办人": t.exec, "说明": ""}];
        rows.forEach((fr, idx) => {
          const st = strip(fr["状态"] || "");
          const lane = laneOf(fr, verifier);
          if (lane === "check") hasCheck = true;
          const sub = lane === "exec" ? k : 0;
          const rr = put(lane, sub, r0);
          const descKey = Object.keys(fr).find((x) => x.startsWith("说明")) || "说明";
          const n = add({lane, sub, row: rr, label: `${t.id.replace(/^G\d+-/, "")} ${st.replace(/：.*/, "")}`.slice(0, 14), cls: nodeCls(st, idx === rows.length - 1), last: idx === rows.length - 1,
            task: t.id, holder: strip(fr["接手人"] || ""), state: st, info: `${strip(fr["日期"] || "")} ${t.id} · ${strip(fr["经办人"] || "")} → ${strip(fr["接手人"] || "")} · ${st}：${strip(fr[descKey] || "")}`});
          if (prev === null) ends.forEach((e) => edges.push([e, n])); else edges.push([prev, n]);
          prev = n; r0 = rr + 1;
        });
        newEnds.push(prev);
        row = Math.max(row, r0);
      });
      ends = newEnds;
    });
    return {nodes, edges, maxSub, hasCheck, rows: Math.max(...nodes.map((n) => n.row)) + 1, runsOf};
  }
  function nowLine(d, g) {
    const wait = ((g.head["依赖"] || "").match(/G\d+/g) || []).filter((x) => { const o = d.goals.find((q) => q.id === x); return o && !/^完成/.test(o.state); });
    if (g.state.startsWith("暂停")) return "已暂停";
    if (g.state.startsWith("待批准")) return `等<b>总裁</b>批准框架${wait.length ? `，且等 ${wait.map(esc).join("、")} 完成` : "（批准前不派发）"}`;
    if (wait.length) return `等 <b>${wait.map(esc).join("、")}</b> 完成后才能派发`;
    const cur = goalLanes(d, g).nodes.filter((n) => n.cls === "now" || (n.last && n.cls === "bad"));
    if (!cur.length) return "各阶段都已完成，等<b>总裁</b>批准目标完成";
    return cur.map((n) => {
      if (n.stage) return `等<b>${esc(n.stage.who || "总管")}</b>做阶段 ${circ(n.stage.no)}「${esc(n.stage.seat)}」（还没派任务）`;
      const who = esc(n.holder || "—"), id = esc(n.task);
      const act = /^受阻/.test(n.state) ? `处理 ${id} 的阻塞` : /^需补充/.test(n.state) ? `返工 ${id}`
        : /^待验收/.test(n.state) ? `验收 ${id}` : /^(已交接|执行中)/.test(n.state) ? `执行 ${id}` : `推进 ${id}`;
      const live = d.runs.filter((r) => r.task === n.task && r.status === "运行中").pop();
      return `等<b>${who}</b>${act}${live ? ` · <span class="dot"></span>${esc(live.member)} 已运行 ${dur(secs(live.started))}` : ""}`;
    }).join("；");
  }

  function renderLanes(d, g) {
    const L = goalLanes(d, g), RH = 34, TOP = 40, NH = 24, LEFT = 8;
    const cols = [{lane: "boss", name: "总裁", w: 120}, {lane: "lead", name: "总管", w: 130}];
    for (let k = 0; k < L.maxSub; k++) cols.push({lane: "exec", sub: k, name: k ? "" : "执行", w: 170});
    if (L.hasCheck) cols.push({lane: "check", name: "验收（第三方）", w: 150});
    // 泳道按页面宽度铺开（最多放大到 2 倍），节点宽度不跟着变
    const need = cols.reduce((a, c) => a + c.w, 0), avail = (app.clientWidth || 1000) - 56 - LEFT;
    const k = Math.min(2, Math.max(1, avail / need));
    cols.forEach((c) => { c.nw = c.w - 14; c.w = Math.floor(c.w * k); });
    let x = LEFT; cols.forEach((c) => { c.x = x; x += c.w; });
    const W = x + 8, H = TOP + RH * L.rows + 8;
    const col = (n) => cols.find((c) => c.lane === n.lane && (n.lane !== "exec" || c.sub === n.sub)) || cols[1];
    const cx = (n) => col(n).x + col(n).w / 2, cy = (n) => TOP + RH * n.row + RH / 2;
    let s = `<svg class="lanes" viewBox="0 0 ${W} ${H}" style="max-width:${W}px"><defs><marker id="la" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" stroke-width="1.5"/></marker></defs>`;
    const groups = [];
    cols.forEach((c) => { if (c.name || c.lane !== "exec") groups.push({...c, w2: c.lane === "exec" ? c.w * L.maxSub : c.w}); });
    groups.forEach((c, i) => { s += `<rect x="${c.x + 3}" y="4" width="${c.w2 - 6}" height="${H - 8}" rx="8" class="ln"/><text x="${c.x + c.w2 / 2}" y="22" class="lnh">${esc(c.name)}</text><line x1="${c.x + 8}" y1="36" x2="${c.x + c.w2 - 8}" y2="36" class="lhl"/>${i > 0 ? `<line x1="${c.x}" y1="8" x2="${c.x}" y2="${H - 8}" class="lsep"/>` : ""}`; });
    for (let k = 1; k < L.maxSub; k++) { const c = cols.find((q) => q.lane === "exec" && q.sub === k); s += `<line x1="${c.x}" y1="34" x2="${c.x}" y2="${H - 8}" class="lsub"/>`; }
    L.edges.forEach(([a, b], k) => {
      const A = L.nodes[a], B = L.nodes[b], x1 = cx(A), y1 = cy(A) + NH / 2, x2 = cx(B), y2 = cy(B) - NH / 2 - 2, ym = y2 - 6 - (k % 2) * 3;
      s += `<path d="${x1 === x2 ? `M${x1} ${y1}L${x2} ${y2}` : `M${x1} ${y1}L${x1} ${ym}L${x2} ${ym}L${x2} ${y2}`}" class="le ${B.cls === "todo" ? "todo" : ""}" marker-end="url(#la)"/>`;
    });
    L.nodes.forEach((n) => {
      const w = col(n).nw;
      s += `<g class="lnode ${n.cls} ${openNode === g.id + ":" + n.i ? "sel" : ""}" data-lnode="${esc(g.id + ":" + n.i)}"><rect x="${cx(n) - w / 2}" y="${cy(n) - NH / 2}" width="${w}" height="${NH}" rx="6"/><text x="${cx(n)}" y="${cy(n)}">${esc(n.label)}</text></g>`;
    });
    s += "</svg>";
    let det = `<div class="note">点格子看这一步的说明、用时和 token</div>`;
    if (openNode && openNode.startsWith(g.id + ":")) {
      const n = L.nodes[+openNode.split(":")[1]];
      if (n) {
        det = `<div class="ldet">${esc(n.info)}`;
        const rs = n.task ? L.runsOf(n.task) : [];
        if (rs.length) det += `<div class="meta" style="margin-top:6px">${n.task} 的派活：${rs.map((r) => `${esc(r.run)} ${esc(r.member)} ${dur(r.ended ? (new Date(r.ended) - new Date(r.started)) / 1000 : secs(r.started))} · ${tok(r.tokens)}`).join("；")}</div>`;
        det += "</div>";
      }
    }
    return `<div class="lanewrap">${s}</div>${det}`;
  }

  function renderGoalDetail(g, devs) {
    let b = `<div class="detail gdetail">`;
    const gtasks = new Set(g.stages.flatMap((s) => s.tasks));
    const gruns = (data.runs || []).filter((r) => gtasks.has(r.task));
    if (gruns.length) b += `<div class="meta">派活 ${gruns.length} 次 · 合计 ${tok(sumTok(gruns))}</div>`;
    const head = Object.entries(g.head).filter(([k]) => !["状态", "截止"].includes(k)).map(([k, v]) => ({k, v: esc(v)}));
    const fields = head.concat(g.fields);
    if (fields.length) b += `<div class="fields">${fields.map((f) => `<span class="k">${esc(f.k)}</span><span>${f.v}</span>`).join("")}</div>`;
    b += `<h4>流程</h4><div class="scroll"><table><thead><tr><th>阶段</th><th>席位</th><th>人选</th><th>产出</th><th>通过条件</th><th>子任务</th><th>状态</th></tr></thead><tbody>${g.stages.map((s) => `<tr>
      <td>${circ(s.no)}</td><td>${esc(s.seat)}</td><td>${esc(s.who)}</td><td>${s.output}</td><td>${s.cond}</td>
      <td>${s.taskInfo.map((t) => t.archived && !ARCHIVE ? `<a class="tlink" href="/p/${esc(id)}/archive#${esc(t.id)}">${esc(t.id)} ${statusPill(t.state || "—")}</a>`
        : `<span class="tlink" data-task="${esc(t.id)}">${esc(t.id)} ${statusPill(t.state || "—")}</span>`).join(" ") || "—"}${s.missing.length ? ` <span class="pill bad">找不到 ${esc(s.missing.join("、"))}</span>` : ""}</td>
      <td>${statusPill(s.status)}</td></tr>`).join("")}</tbody></table></div>`;
    if (devs.length) b += `<h4>偏离记录</h4><ul class="plain">${devs.map((r) => `<li><b>${esc(r.run)}</b> ${esc(r.member)} 坐「${esc(r.seat)}」：${esc(r.override)}<div class="note">${(r.deviations || []).map(esc).join("；")}</div></li>`).join("")}</ul>`;
    return b + "</div>";
  }

  const ISSUE_CLS = {"待裁决": "warn", "已裁决": "ok", "开放": ""};

  function renderIssues(d) {
    const list = d.issues.slice().sort((a, b) => (a.stage === "已裁决") - (b.stage === "已裁决"));
    if (!list.length) return "";
    const body = list.map((i) => {
      const live = d.runs.filter((r) => r.task === i.id && r.status === "运行中");
      const chips = Array.from({length: i.maxRounds}, (_, k) => {
        const n = k + 1;
        const done = n <= i.roundsDone;
        const running = !done && n === i.roundsDone + 1 && live.length;
        return `<span class="stage ${done ? "ok" : running ? "run cur" : ""}">第${n}轮 ${done ? "✓" : running ? "●" : ""}</span>`;
      }).join('<span class="arrow">→</span>') + `<span class="arrow">→</span><span class="stage ${i.summary.length ? "ok" : ""}">总结 ${i.summary.length ? "✓" : ""}</span><span class="arrow">→</span><span class="stage ${i.ruling.length ? "ok" : i.stage === "待裁决" ? "warn cur" : ""}">裁决 ${i.ruling.length ? "✓" : ""}</span>`;
      const who = i.attendees.map((a) => `${esc(a.member)}${a.view ? "（" + esc(a.view) + "）" : ""}`).join("、");
      const stage = live.length ? `第${i.roundsDone + 1}轮进行中` : i.stage;
      let h = `<div class="goal ${openIssue === i.id ? "open" : ""}" data-issue="${esc(i.id)}">
        <div class="ghead"><span class="tid">${esc(i.id)}</span><span class="tt">${esc(i.title)}</span> ${issuePill(stage)}
          ${stage === "待裁决" ? '<span class="meta">等你裁决</span>' : ""}</div>
        <div class="pipe">${chips}</div>
        <div class="note">与会：${who || "—"}${live.length ? ` · <span class="dot"></span>${live.length} 人发言中` : ""}</div></div>`;
      if (openIssue === i.id) h += renderIssueDetail(i);
      return h;
    }).join("");
    return card("议题（朝会）", body, {meta: "点议题看发言、总结和裁决"});
  }

  function issuePill(s) {
    const cls = /进行中/.test(s) ? "run" : ISSUE_CLS[s] ?? "";
    return `<span class="pill ${cls}">${/进行中/.test(s) ? '<span class="dot"></span>' : ""}${esc(s)}</span>`;
  }

  function fieldGrid(list) {
    return list.length ? `<div class="fields">${list.map((f) => `<span class="k">${esc(f.k)}</span><span>${f.v}</span>`).join("")}</div>` : `<div class="empty">未填写</div>`;
  }

  function renderIssueDetail(i) {
    let b = `<div class="detail gdetail"><h4>问题</h4>${fieldGrid(i.question)}`;
    i.rounds.filter((r) => r.speeches.length).forEach((r) => {
      b += `<h4>第 ${r.no} 轮</h4>${r.speeches.map((s, k) => `<details class="speech" data-sp="${esc(i.id + "-" + r.no + "-" + k)}" ${openSpeech.has(i.id + "-" + r.no + "-" + k) ? "open" : ""}><summary>${esc(s.who)}</summary><pre class="out">${esc(s.text)}</pre></details>`).join("")}`;
    });
    b += `<h4>总结（总管）</h4>${fieldGrid(i.summary)}<h4>裁决（总裁）</h4>${fieldGrid(i.ruling)}`;
    return b + "</div>";
  }

  function renderRunning(d) {
    const running = d.runs.filter((r) => r.status === "运行中");
    if (!running.length) return "";
    const rows = running.map((r) => {
      const idle = secs(r.last_at);
      const stale = idle != null && idle > STALE_MIN * 60;
      return `<tr><td class="num">${esc(r.run)}</td><td>${esc(r.seat)}</td><td>${esc(r.member)}</td>
        <td class="num">${dur(secs(r.started))}</td><td class="num">${esc(r.steps || 0)}</td>
        <td>${esc(r.last_action || "—")}</td>
        <td class="num ${stale ? "stale" : ""}">${idle == null ? "—" : dur(idle) + " 前"}${stale ? " · 疑似卡住" : ""}</td></tr>`;
    }).join("");
    return card("正在运行", `<div class="scroll"><table><thead><tr><th>派活</th><th>席位</th><th>成员</th><th>已运行</th><th>步数</th><th>最近动作</th><th>上次动作</th></tr></thead><tbody>${rows}</tbody></table></div>`,
      {meta: `${running.length} 个 · 超过 ${STALE_MIN} 分钟无动作标红`});
  }

  // 目标筛选：任务看卡头「目标」，派活看它所属任务的目标
  function goalOfTask(d, id) { const t = d.cards.find((x) => x.id === id); return t && t.goal ? t.goal : "none"; }
  const passGoal = (gid) => !selGoal || gid === selGoal;
  const scopeMeta = () => (selGoal ? `只看 ${esc(selGoal)} · <span class="tlink clear" data-clear="1">看全部</span>` : "");

  function who(t) {
    const parts = [];
    if (t.head["执行"] || t.head["验收"]) parts.push(`${esc(t.head["执行"] || "—")} → ${esc(t.head["验收"] || "—")}`);
    if (t.holder) parts.push(`现在在 ${esc(t.holder)}`);
    return parts.join(" · ");
  }

  function renderBoard(d) {
    const runsBy = {};
    d.runs.forEach((r) => { (runsBy[r.task] = runsBy[r.task] || []).push(r); });
    const cols = COLS.map((c) => ({...c, cards: []}));
    const other = {name: "其他", cards: []};
    d.cards.filter((t) => passGoal(t.goal || "none")).forEach((t) => { (cols.find((c) => c.match(t.state || "")) || other).cards.push(t); });
    if (other.cards.length) cols.push(other);
    // 只列有任务的状态组，一行一个任务；点开在该行下面看详情
    const html = cols.filter((c) => c.cards.length).map((c) => `<div class="tgroup"><h3>${c.name}<span class="n">${c.cards.length}</span></h3>${c.cards.map((t) => {
      const rs = runsBy[t.id] || [];
      const live = rs.some((r) => r.status === "运行中");
      const lastRow = t.flow[t.flow.length - 1] || {};
      const last = strip(lastRow[Object.keys(lastRow).find((k) => k.startsWith("说明"))] || "");
      const row = `<div class="trow task ${openTask === t.id ? "open" : ""}" data-task="${esc(t.id)}">
        <span class="tid">${esc(t.id)}</span><span class="tt">${esc(t.title)}</span>
        ${t.goal && !t.id.startsWith(t.goal + "-") ? `<span class="gtag">${esc(t.goal)}${t.stage ? " · " + circ(t.stage) : ""}</span>` : ""}${rs.some((r) => r.override) ? '<span class="pill warn">偏离</span>' : ""}
        <span class="tw">${who(t)}</span>
        ${statusPill(t.state || "—")}
        <span class="tn">${live ? '<span class="dot"></span>' : ""}${rs.length ? `派活 ${rs.length}` : ""}</span>
        ${last ? `<div class="note">${esc(last)}</div>` : ""}
      </div>`;
      return row + (openTask === t.id ? renderTaskDetail(t, rs) : "");
    }).join("")}</div>`).join("");
    const body = (!d.cards.length ? `<div class="empty">任务.md 里还没有任务卡</div>` : html ? `<div class="tlist">${html}</div>` : `<div class="empty">这个目标下没有任务</div>`);
    const meta = `${d.doneLine ? strip(d.doneLine) : ""}${d.archived ? ` · 归档 ${d.archived} 张卡` : ""}`;
    return card("任务", body, {meta: selGoal ? scopeMeta() : esc(meta)});
  }

  function renderTaskDetail(t, runs) {
    let b = `<div class="detail"><h4>${esc(t.id)} ${esc(t.title)} ${statusPill(t.state || "—")}</h4>`;
    const head = Object.entries(t.head).filter(([k]) => k !== "状态");
    const fields = head.map(([k, v]) => ({k, v: esc(v)})).concat(t.fields);
    if (fields.length) b += `<div class="fields">${fields.map((f) => `<span class="k">${esc(f.k)}</span><span>${f.v}</span>`).join("")}</div>`;
    if (t.flow.length) {
      const keys = Object.keys(t.flow[0]);
      b += `<h4>流转</h4><div class="scroll"><table><thead><tr>${keys.map((k) => `<th>${esc(k.replace(/（.*）/, ""))}</th>`).join("")}</tr></thead><tbody>${t.flow.map((r) => `<tr>${keys.map((k) => `<td>${r[k] || ""}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
    }
    if (runs.length) b += `<h4>派活记录</h4>${runsTable(runs.slice().reverse(), false)}`;
    return b + "</div>";
  }

  // 派活记录按开始日期分组（新的在上），组头写当天次数、合计用时和 token
  const WEEK = "日一二三四五六";
  function dayLabel(day) {
    const [y, m, dd] = day.split("-").map(Number), dt = new Date(y, m - 1, dd);
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const diff = Math.round((today - dt) / 86400000);
    return `${diff === 0 ? "今天 · " : diff === 1 ? "昨天 · " : ""}${day.slice(5)}（周${WEEK[dt.getDay()]}）`;
  }
  const tookOf = (r) => (r.ended ? (new Date(r.ended) - new Date(r.started)) / 1000 : secs(r.started));
  function runsTable(runs, showTask) {
    runs = runs.slice().sort((a, b) => String(b.started || "").localeCompare(String(a.started || "")));
    const cols = showTask ? 8 : 7;
    let lastDay = null;
    const rows = runs.map((r) => {
      const day = (r.started || "").slice(0, 10);
      let head = "";
      if (day !== lastDay) {
        lastDay = day;
        const same = runs.filter((x) => (x.started || "").slice(0, 10) === day);
        head = `<tr class="dayrow"><td colspan="${cols}"><b>${day ? dayLabel(day) : "时间未知"}</b><span>${same.length} 次 · 合计用时 ${dur(same.reduce((a, x) => a + (tookOf(x) || 0), 0))} · token ${tok(sumTok(same)).split(" ·")[0]}</span></td></tr>`;
      }
      return head + runRow(r, showTask, cols);
    }).join("");
    return `<div class="scroll"><table class="runs"><thead><tr><th>派活</th>${showTask ? "<th>任务</th>" : ""}<th>席位 · 成员</th><th>状态</th><th>开始</th><th>用时</th><th>token</th><th>结果</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }
  function runRow(r, showTask, cols) {
    {
      const took = tookOf(r);
      const summary = r.status === "完成" ? r.final : r.error || r.final;
      const open = openRuns.has(r.run);
      const plain = (summary || "").replace(/[*#`>|]+/g, " ").replace(/\[([^\]]*)\]\([^)]*\)/g, "$1").replace(/\s+/g, " ").trim();
      let tr = `<tr class="click" data-run="${esc(r.run)}"><td class="num">${esc(r.run)}</td>${showTask ? `<td>${esc(r.task)}</td>` : ""}
        <td>${esc(r.seat)} · ${esc(r.member)}<div class="sub2">${r.mode === "续接" ? "续接" : "新会话"}</div></td><td>${statusPill(r.status || "")}${r.override ? ' <span class="pill warn">偏离</span>' : ""}</td>
        <td class="num">${(r.started || "").slice(11, 16) || "—"}</td><td class="num">${dur(took)}<div class="sub2">${esc(r.steps || 0)} 步</div></td><td class="num">${tok(r.tokens)}</td>
        <td><div class="clip">${esc(plain)}</div></td></tr>`;
      if (open) {
        tr += `<tr><td colspan="${cols}">
          <div class="meta">会话 ${esc(r.session || "—")}${r.continues ? ` · 续接自 ${esc(r.continues)}` : ""}${r.ended ? ` · 结束于 ${hm(r.ended)}` : ""}</div>
          <div class="meta" style="margin-top:6px">${esc(tokDetail(r.tokens))}</div>
          ${r.override ? `<div class="meta" style="margin-top:6px">偏离理由：${esc(r.override)}；${(r.deviations || []).map(esc).join("；")}</div>` : ""}
          <div class="meta" style="margin-top:6px">启动语</div><pre class="out">${esc(r.prompt)}</pre>
          ${r.final ? `<div class="meta">最终回复</div><pre class="out">${esc(r.final)}</pre>` : ""}
          ${r.error ? `<div class="meta">错误</div><pre class="out">${esc(r.error)}</pre>` : ""}
        </td></tr>`;
      }
      return tr;
    }
  }

  // 记录区：派活和决议两个标签页
  function renderRecords(d) {
    const past = d.runs.filter((r) => r.status !== "运行中" && passGoal(goalOfTask(d, r.task)))
      .sort((a, b) => String(b.started || "").localeCompare(String(a.started || ""))).slice(0, 30);
    const tabs = [["runs", `派活记录 ${past.length}`], ["dec", `决议 ${d.decisions.length}`]]
      .map(([k, t]) => `<button class="tab ${recTab === k ? "on" : ""}" data-tab="${k}">${t}</button>`).join("");
    const body = recTab === "runs"
      ? (past.length ? runsTable(past, true) : `<div class="empty">没有派活记录</div>`)
      : (d.decisions.length ? `<ul class="plain">${d.decisions.map((x) => `<li>${x}</li>`).join("")}</ul>` : `<div class="empty">暂无</div>`);
    return card(`<span class="tabs">${tabs}</span>`, body, {meta: recTab === "runs" ? (selGoal ? scopeMeta() : past.length ? "点一行看启动语和回复" : "") : ""});
  }

  // ---------- 归档页 ----------
  function doneDate(x) {
    const row = (x.flow || []).filter((r) => /^(通过|完成)/.test(strip(r["状态"] || ""))).pop();
    return row ? strip(row["日期"] || "") : "";
  }
  function renderArchiveGoals(d) {
    if (!d.goals.length) return card("归档目标", '<div class="empty">没有归档的目标</div>');
    const goals = d.goals.slice().sort((a, b) => b.id.localeCompare(a.id, undefined, {numeric: true}));
    const focus = goals.find((g) => g.id === selGoal) || goals[0];
    const rows = goals.map((g) => {
      const runs = goalRuns(d, g);
      return `<div class="grow ${g.id === selGoal ? "on" : g.id === focus.id ? "auto" : ""}" data-goal="${esc(g.id)}">
        <div class="gname"><span class="tid">${esc(g.id)}</span><span class="tt">${esc(g.title)}</span>${statusPill(g.state || "—")}</div>
        <div class="gprog">${stageDots(g)}</div>
        <div class="gnow">${g.head["批准"] ? "批准：" + esc(g.head["批准"]) : ""}</div>
        <div class="galert"></div>
        <div class="gcost">${runs.length ? `派活 ${runs.length} · ${tok(sumTok(runs)).split(" ·")[0]}` : "未派活"}</div>
      </div>`;
    }).join("");
    const body = `<div class="glist">${rows}</div><div class="gfocus"><div class="gfh"><b>${esc(focus.id)} ${esc(focus.title)}</b><span class="meta">流程泳道 · 已归档</span>
      <span class="tlink gdet" data-gdet="${esc(focus.id)}">${openGoal === focus.id ? "收起框架" : "看框架和偏离"}</span></div>
      ${openGoal === focus.id ? renderGoalDetail(focus, goalRuns(d, focus).filter((r) => r.override)) : ""}${renderLanes(d, focus)}</div>`;
    return card("归档目标", body, {meta: `${goals.length} 个 · 点一行看它的流程`});
  }
  function renderArchiveTasks(d) {
    if (!d.cards.length) return card("归档任务", '<div class="empty">没有归档的任务</div>');
    const runsBy = {};
    d.runs.forEach((r) => { (runsBy[r.task] = runsBy[r.task] || []).push(r); });
    const groups = {};
    d.cards.forEach((t) => { const k = t.goal || ""; (groups[k] = groups[k] || []).push(t); });
    const keys = Object.keys(groups).sort((a, b) => (a === "") - (b === "") || b.localeCompare(a, undefined, {numeric: true}));
    const html = keys.map((k) => {
      const g = d.goals.find((x) => x.id === k);
      const title = k ? `${esc(k)}${g ? " " + esc(g.title) : ""}` : "零散任务";
      return `<div class="tgroup"><h3>${title}<span class="n">${groups[k].length}</span></h3>${groups[k].map((t) => {
        const rs = runsBy[t.id] || [];
        const dd = doneDate(t);
        return `<div class="trow task ${openTask === t.id ? "open" : ""}" data-task="${esc(t.id)}">
          <span class="tid">${esc(t.id)}</span><span class="tt">${esc(t.title)}</span>
          <span class="tw">${esc(t.head["执行"] || "—")} → ${esc(t.head["验收"] || "—")}${dd ? ` · ${esc(dd)} 通过` : ""}</span>${statusPill(t.state || "—")}
          <span class="tn">${rs.length ? `派活 ${rs.length}` : ""}</span></div>` + (openTask === t.id ? renderTaskDetail(t, rs) : "");
      }).join("")}</div>`;
    }).join("");
    return card("归档任务", `<div class="tlist">${html}</div>`, {meta: `${d.cards.length} 张 · 点一行看流转和派活记录`});
  }
  function renderArchive(d) {
    const n = d.goals.length + d.cards.length + d.issues.length;
    const body = n ? `${renderArchiveGoals(d)}${renderIssues(d)}${renderArchiveTasks(d)}` : '<div class="empty">这个项目还没有归档的目标、任务或议题</div>';
    return `<div class="strip"><span class="si">归档目标 <b>${d.goals.length}</b></span><span class="si">任务 <b>${d.cards.length}</b></span><span class="si">议题 <b>${d.issues.length}</b></span><span class="si">派活 <b>${d.runs.length}</b> 次 · token <b>${tok(sumTok(d.runs)).split(" ·")[0]}</b></span></div>${body}`;
  }

  function render() {
    const d = data;
    document.title = d.name + (ARCHIVE ? " · 归档" : " · 御前看板");
    document.getElementById("name").textContent = d.name + (ARCHIVE ? " · 归档" : "");
    const m = d.meta;
    const ac = d.archivedCount || {};
    const an = (ac.goals || 0) + (ac.tasks || 0) + (ac.issues || 0);
    const alink = !ARCHIVE && an ? `<a href="/p/${esc(id)}/archive">已归档 ${[ac.goals && ac.goals + " 个目标", ac.tasks && ac.tasks + " 张任务", ac.issues && ac.issues + " 个议题"].filter(Boolean).join("、")} →</a>` : "";
    document.getElementById("meta").innerHTML = [m["状态"], m["截止"] && "截止 " + m["截止"], `<a href="${esc(d.progressHref)}">PROGRESS.md</a>`, alink]
      .filter(Boolean).map((x) => (x.startsWith("<a") ? x : esc(x))).join(" · ");
    if (ARCHIVE) { app.classList.add("archive"); app.innerHTML = renderArchive(d); return; }
    app.innerHTML = `${renderStrip(d)}${renderCheck(d)}<div class="row">${renderPending(d)}${renderNow(d)}</div>${renderRunning(d)}${renderGoals(d)}${renderIssues(d)}${renderBoard(d)}${renderRecords(d)}`;
  }

  async function load() {
    try {
      const r = await fetch("/api/p/" + id + (ARCHIVE ? "/archive" : ""), {cache: "no-store"});
      const j = await r.json();
      if (j.error) throw new Error(j.error);
      const sig = JSON.stringify({...j, generated: 0});
      const live = j.runs.some((x) => x.status === "运行中");
      data = j;
      if (sig !== lastSig || live) render();
      lastSig = sig;
      const now = new Date().toLocaleTimeString("zh-CN", {hour12: false});
      document.getElementById("tick").textContent = ARCHIVE ? "打开时加载 · " + now + " · 刷新页面重新读取" : "每 5 秒刷新 · " + now;
    } catch (e) {
      document.getElementById("tick").textContent = "刷新失败：" + e.message;
    }
  }

  function selectGoal(id) {
    selGoal = id;
    history.replaceState(null, "", id ? "#" + id : location.pathname);
    render();
  }

  app.addEventListener("click", (e) => {
    const a = e.target.closest('a[href^="file://"]');
    if (a) {
      e.preventDefault();
      fetch("/api/open", {method: "POST", headers: {"Content-Type": "application/json", "X-Board": "1"},
        body: JSON.stringify({path: decodeURIComponent(a.getAttribute("href").slice(7))})});
      return;
    }
    if (e.target.closest("a")) return;
    const tb = e.target.closest("[data-tab]");
    if (tb) { recTab = tb.dataset.tab; render(); return; }
    if (e.target.closest("[data-clear]")) { selectGoal(null); return; }
    const gd = e.target.closest("[data-gdet]");
    if (gd) { openGoal = openGoal === gd.dataset.gdet ? null : gd.dataset.gdet; render(); return; }
    const t = e.target.closest("[data-task]");
    if (t) {
      if (!t.dataset.task) return;
      const fromGoal = t.classList.contains("tlink");
      openTask = fromGoal ? t.dataset.task : (openTask === t.dataset.task ? null : t.dataset.task);
      render();
      if (fromGoal) document.querySelector(".task.open")?.scrollIntoView({behavior: "smooth", block: "center"});
      return;
    }
    if (e.target.closest("details")) return;
    const ln = e.target.closest("[data-lnode]");
    if (ln) { openNode = openNode === ln.dataset.lnode ? null : ln.dataset.lnode; render(); return; }
    const iss = e.target.closest("[data-issue]");
    if (iss) { openIssue = openIssue === iss.dataset.issue ? null : iss.dataset.issue; render(); return; }
    const g = e.target.closest("[data-goal]");
    if (g) {
      // 总览行点击切换选中；「待你决定」里的目标编号只负责选中并跳到泳道
      const row = g.classList.contains("grow");
      selectGoal(row && selGoal === g.dataset.goal ? null : g.dataset.goal);
      if (!row) document.querySelector(".gfocus")?.scrollIntoView({behavior: "smooth", block: "start"});
      return;
    }
    const r = e.target.closest("[data-run]");
    if (r && !window.getSelection().toString()) {
      openRuns.has(r.dataset.run) ? openRuns.delete(r.dataset.run) : openRuns.add(r.dataset.run);
      render();
    }
  });
  document.getElementById("meta").addEventListener("click", (e) => {
    const a = e.target.closest('a[href^="file://"]');
    if (!a) return;
    e.preventDefault();
    fetch("/api/open", {method: "POST", headers: {"Content-Type": "application/json", "X-Board": "1"},
      body: JSON.stringify({path: decodeURIComponent(a.getAttribute("href").slice(7))})});
  });

  app.addEventListener("toggle", (e) => {
    const sp = e.target.dataset && e.target.dataset.sp;
    if (sp) e.target.open ? openSpeech.add(sp) : openSpeech.delete(sp);
  }, true);

  let rt = null;
  window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => data && render(), 200); });

  if (ARCHIVE) {
    const back = document.querySelector(".back");
    back.href = "/p/" + id;
    back.textContent = "← 项目看板";
  }
  load().then(() => { if (ARCHIVE && openTask) document.querySelector(".task.open")?.scrollIntoView({block: "center"}); });
  if (!ARCHIVE) setInterval(() => { if (document.visibilityState === "visible") load(); }, 5000);
})();
