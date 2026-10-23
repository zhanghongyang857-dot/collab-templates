(() => {
  "use strict";

  const MAX_PREVIEW = 1400;
  const LABEL_ORDER = ["system", "user", "context", "assistant", "reasoning", "tool", "other"];
  const KIND_LABEL = {
    system: "系统",
    user: "用户",
    context: "上下文",
    assistant: "助手",
    reasoning: "思考",
    tool: "工具",
    other: "其他",
  };
  const PHASE_LABEL = {
    commentary: "过程",
    final_answer: "最终答复",
  };

  const state = {
    source: "all",
    q: "",
    archived: false,
    subagents: false,
    sessions: [],
    selectedId: null,
    detail: null,
    hiddenLabels: new Set(),
    listSeq: 0,
    detailSeq: 0,
  };

  const dom = {
    search: document.getElementById("search"),
    sourceChips: document.getElementById("source-chips"),
    showArchived: document.getElementById("show-archived"),
    showSubagents: document.getElementById("show-subagents"),
    refresh: document.getElementById("refresh"),
    listCount: document.getElementById("list-count"),
    sessionList: document.getElementById("session-list"),
    detail: document.getElementById("detail"),
    homeLabel: document.getElementById("home-label"),
  };

  let searchTimer = 0;

  // ------------------------------ 工具函数 ------------------------------

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== undefined && text !== null) {
      node.textContent = String(text);
    }
    return node;
  }

  function clear(node) {
    while (node.firstChild) {
      node.removeChild(node.firstChild);
    }
  }

  function formatNumber(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) {
      return "—";
    }
    return number.toLocaleString("en-US");
  }

  function pad2(value) {
    return String(value).padStart(2, "0");
  }

  function formatShortTime(ms) {
    const date = new Date(Number(ms));
    if (Number.isNaN(date.getTime())) {
      return "—";
    }
    return `${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ${pad2(date.getHours())}:${pad2(date.getMinutes())}`;
  }

  function trimInline(text, limit) {
    const single = String(text || "").replace(/\s+/g, " ").trim();
    return single.length > limit ? `${single.slice(0, limit)}…` : single;
  }

  function sourceClass(key) {
    if (key === "exec" || key === "vscode") {
      return key;
    }
    return "other";
  }

  async function api(path) {
    const response = await fetch(path, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    let data = {};
    try {
      data = await response.json();
    } catch (error) {
      data = {};
    }
    if (!response.ok) {
      throw new Error(data.error || `请求失败（HTTP ${response.status}）`);
    }
    return data;
  }

  function sourceBadge(session) {
    return el(
      "span",
      `source-badge ${sourceClass(session.source_key)}`,
      session.source_label || "其他"
    );
  }

  function banner(text, danger) {
    const node = el("div", danger ? "banner danger" : "banner", text);
    return node;
  }

  function emptyDetail(title, text) {
    const wrap = el("div", "empty-state");
    wrap.append(el("div", "empty-icon", "⌘"));
    wrap.append(el("h2", null, title));
    wrap.append(el("p", null, text));
    return wrap;
  }

  function loadingDetail(text) {
    const wrap = el("div", "empty-state");
    const line = el("p", null);
    line.append(el("span", "spinner"));
    line.append(document.createTextNode(text));
    wrap.append(line);
    return wrap;
  }

  // ------------------------------ 会话列表 ------------------------------

  async function loadHealth() {
    try {
      const health = await api("/api/health");
      if (health.database_exists) {
        dom.homeLabel.textContent = `${health.codex_home} · 只读`;
      } else {
        dom.homeLabel.textContent = "未找到 state_5.sqlite";
      }
      dom.homeLabel.title = health.codex_home || "";
    } catch (error) {
      dom.homeLabel.textContent = "只读看板";
    }
  }

  function renderList() {
    clear(dom.sessionList);
    dom.listCount.textContent = `${state.sessions.length} 个会话`;

    if (!state.sessions.length) {
      dom.sessionList.append(
        el("div", "list-error", "没有匹配的会话。可尝试清空搜索、切换来源，或勾选“已归档 / 子 agent”。")
      );
      return;
    }

    for (const session of state.sessions) {
      const item = el("button", "session-item");
      item.type = "button";
      item.dataset.id = session.id;
      item.title = `${session.title}\n${session.cwd}`;
      if (session.id === state.selectedId) {
        item.classList.add("active");
        item.setAttribute("aria-current", "true");
      }

      const top = el("div", "session-top");
      top.append(sourceBadge(session));
      if (session.archived) {
        top.append(el("span", "mini-badge archived", "已归档"));
      }
      if (session.is_subagent) {
        top.append(el("span", "mini-badge", "子 agent"));
      }
      top.append(el("span", "session-time", formatShortTime(session.updated_at)));
      item.append(top);

      item.append(el("div", "session-title", session.title));
      item.append(el("div", "session-cwd", session.cwd || "（无工作目录）"));

      const foot = el("div", "session-foot");
      foot.append(el("span", null, `${formatNumber(session.tokens_used)} tok`));
      if (session.model) {
        foot.append(el("span", "model", session.model));
      }
      if (session.git_branch) {
        foot.append(el("span", null, session.git_branch));
      }
      item.append(foot);

      dom.sessionList.append(item);
    }
  }

  async function loadSessions() {
    const seq = ++state.listSeq;
    dom.listCount.textContent = "载入中…";
    const params = new URLSearchParams({
      source: state.source,
      q: state.q,
      archived: state.archived ? "1" : "0",
      subagents: state.subagents ? "1" : "0",
    });

    try {
      const data = await api(`/api/sessions?${params.toString()}`);
      if (seq !== state.listSeq) {
        return;
      }
      state.sessions = Array.isArray(data.sessions) ? data.sessions : [];
      renderList();
    } catch (error) {
      if (seq !== state.listSeq) {
        return;
      }
      state.sessions = [];
      clear(dom.sessionList);
      dom.listCount.textContent = "读取失败";
      dom.sessionList.append(el("div", "list-error", `会话列表读取失败：${error.message}`));
    }
  }

  // ------------------------------ 会话详情 ------------------------------

  async function openSession(id) {
    state.selectedId = id;
    try {
      const url = new URL(window.location.href);
      url.searchParams.set("session", id);
      window.history.replaceState(null, "", url.toString());
    } catch (error) {
      // 地址栏更新失败不影响读取。
    }
    renderList();
    const seq = ++state.detailSeq;
    clear(dom.detail);
    dom.detail.append(loadingDetail("正在读取会话…"));

    try {
      const data = await api(`/api/sessions/${encodeURIComponent(id)}`);
      if (seq !== state.detailSeq) {
        return;
      }
      state.detail = data;
      renderDetail();
      dom.detail.scrollTop = 0;

      // 直接打开归档/子 agent 深链时，把对应筛选打开，避免列表里看不到当前会话。
      let filtersChanged = false;
      if (data.session && data.session.archived && !state.archived) {
        state.archived = true;
        dom.showArchived.checked = true;
        filtersChanged = true;
      }
      if (data.session && data.session.is_subagent && !state.subagents) {
        state.subagents = true;
        dom.showSubagents.checked = true;
        filtersChanged = true;
      }
      if (filtersChanged) {
        loadSessions();
      }
    } catch (error) {
      if (seq !== state.detailSeq) {
        return;
      }
      state.detail = null;
      clear(dom.detail);
      dom.detail.append(el("div", "detail-error", `读取会话失败：${error.message}`));
    }
  }

  function addMeta(grid, label, value, mono) {
    const item = el("div", "meta-item");
    item.append(el("span", "meta-label", label));
    const text = value === undefined || value === null || value === "" ? "—" : String(value);
    const node = el("span", mono ? "meta-value mono" : "meta-value", text);
    node.title = text;
    item.append(node);
    grid.append(item);
  }

  function renderHeader(detail) {
    const session = detail.session;
    const stats = detail.stats || {};
    const head = el("header", "detail-head");

    head.append(el("h2", "detail-title", session.title || "（无标题）"));

    const badges = el("div", "detail-badges");
    badges.append(sourceBadge(session));
    if (session.archived) {
      badges.append(el("span", "mini-badge archived", "已归档"));
    }
    if (session.is_subagent) {
      badges.append(el("span", "mini-badge", "子 agent"));
    }
    if (session.agent_role) {
      badges.append(el("span", "mini-badge", session.agent_role));
    }
    if (detail.source_file && detail.source_file.compressed) {
      badges.append(el("span", "mini-badge compressed", "zst 压缩"));
    }
    head.append(badges);

    const grid = el("div", "meta-grid");
    addMeta(grid, "工作目录", session.cwd, true);
    addMeta(grid, "模型", session.model || (detail.meta && detail.meta.model) || "—");
    addMeta(grid, "来源", session.source_label || "—");
    addMeta(grid, "CLI 版本", session.cli_version || (detail.meta && detail.meta.cli_version) || "—");
    addMeta(grid, "创建时间", session.created_at_display || "—");
    addMeta(grid, "更新时间", session.updated_at_display || "—");
    addMeta(grid, "Token", formatNumber(session.tokens_used));
    addMeta(grid, "轮次 / 行数", `${formatNumber(stats.turn_count)} 轮 · ${formatNumber(stats.row_count)} 行`);
    addMeta(grid, "会话 ID", session.id, true);
    if (detail.source_file && detail.source_file.path) {
      addMeta(
        grid,
        detail.source_file.compressed ? "压缩记录文件" : "记录文件",
        detail.source_file.path,
        true
      );
    }
    if (session.originator) {
      addMeta(grid, "Originator", session.originator, true);
    }
    head.append(grid);

    const warnings = Array.isArray(detail.warnings) ? detail.warnings : [];
    const noTurns = !Array.isArray(detail.turns) || detail.turns.length === 0;
    if (detail.source_file && detail.source_file.exists === false) {
      head.append(banner("会话记录文件不存在或无法读取，下面只能显示数据库中的会话信息。", true));
    } else if (warnings.length && noTurns) {
      // 解压工具缺失等致命问题直接显示原因，不藏在折叠区里。
      head.append(banner(`无法读取会话记录：${warnings[0]}`, true));
    }
    if (warnings.length) {
      const details = el("details", "warning-details");
      details.append(el("summary", null, `有 ${warnings.length} 条读取提示`));
      const list = el("ul");
      for (const warning of warnings) {
        list.append(el("li", null, warning));
      }
      details.append(list);
      head.append(details);
    }

    return head;
  }

  function labelCount(detail, kind) {
    const counts = (detail.stats && detail.stats.label_counts) || {};
    return Number(counts[kind] || 0);
  }

  function renderToolbar(detail) {
    const toolbar = el("div", "toolbar");
    const labelBox = el("div", "label-filter");
    labelBox.append(el("span", "toolbar-label", "标签"));

    const presentKinds = LABEL_ORDER.filter((kind) => labelCount(detail, kind) > 0);
    const allButton = el("button", "label-chip", "全部");
    allButton.type = "button";
    allButton.dataset.label = "all";
    allButton.classList.toggle(
      "active",
      presentKinds.every((kind) => !state.hiddenLabels.has(kind))
    );
    allButton.addEventListener("click", () => {
      state.hiddenLabels.clear();
      renderDetail();
    });
    labelBox.append(allButton);

    for (const kind of LABEL_ORDER) {
      const count = labelCount(detail, kind);
      if (!count) {
        continue;
      }
      const button = el("button", "label-chip", `${KIND_LABEL[kind]} ${count}`);
      button.type = "button";
      button.dataset.label = kind;
      button.classList.toggle("active", !state.hiddenLabels.has(kind));
      button.setAttribute("aria-pressed", String(!state.hiddenLabels.has(kind)));
      button.addEventListener("click", () => {
        if (state.hiddenLabels.has(kind)) {
          state.hiddenLabels.delete(kind);
        } else {
          state.hiddenLabels.add(kind);
        }
        renderDetail();
      });
      labelBox.append(button);
    }
    toolbar.append(labelBox);

    const actions = el("div", "toolbar-actions");
    const expand = el("button", "tool-btn", "全部展开");
    expand.type = "button";
    expand.addEventListener("click", () => setAllRowsCollapsed(false));
    const collapse = el("button", "tool-btn", "全部收起");
    collapse.type = "button";
    collapse.addEventListener("click", () => setAllRowsCollapsed(true));
    actions.append(expand, collapse);
    toolbar.append(actions);

    return toolbar;
  }

  function setAllRowsCollapsed(collapsed) {
    dom.detail.querySelectorAll(".row").forEach((row) => {
      row.classList.toggle("collapsed", collapsed);
      const toggle = row.querySelector(".row-toggle");
      if (toggle) {
        toggle.textContent = collapsed ? "展开" : "收起";
        toggle.setAttribute("aria-expanded", String(!collapsed));
      }
    });
  }

  function previewText(row) {
    if (row.kind === "tool" && row.tool) {
      return trimInline(`${row.tool.name || "tool"} ${row.tool.arguments || ""}`, 150);
    }
    return trimInline(row.text, 150);
  }

  function renderTextBlock(parent, text, label) {
    const full = text === undefined || text === null ? "" : String(text);
    const block = el("div", "text-block");
    if (label) {
      block.append(el("div", "block-label", label));
    }

    if (!full) {
      block.append(el("div", "muted", "（空）"));
      parent.append(block);
      return;
    }

    const pre = el("pre", "code-block");
    const shortText = full.length > MAX_PREVIEW ? `${full.slice(0, MAX_PREVIEW)}\n…` : full;
    pre.textContent = shortText;
    block.append(pre);

    if (full.length > MAX_PREVIEW) {
      let expanded = false;
      const toggle = el("button", "link-btn", `展开全文（共 ${formatNumber(full.length)} 字）`);
      toggle.type = "button";
      toggle.addEventListener("click", () => {
        expanded = !expanded;
        pre.textContent = expanded ? full : shortText;
        toggle.textContent = expanded ? "收起" : `展开全文（共 ${formatNumber(full.length)} 字）`;
      });
      block.append(toggle);
    }

    parent.append(block);
  }

  function renderToolBody(body, tool) {
    const meta = el("div", "tool-meta");
    if (tool.call_id) {
      meta.append(el("span", null, `call_id: ${tool.call_id}`));
    }
    if (tool.status) {
      meta.append(el("span", null, `状态: ${tool.status}`));
    }
    if (tool.orphan) {
      meta.append(el("span", null, "未找到对应调用"));
    }
    if (meta.childNodes.length) {
      body.append(meta);
    }
    renderTextBlock(body, tool.arguments || "", "参数");
    if (tool.output_missing) {
      renderTextBlock(body, "（没有输出）", "输出");
    } else {
      renderTextBlock(body, tool.output, "输出");
    }
  }

  function renderRow(row) {
    const wrap = el("div", `row kind-${row.kind}`);
    if (row.collapsed) {
      wrap.classList.add("collapsed");
    }

    const head = el("div", "row-head");
    head.append(el("span", `label label-${row.kind}`, row.label || KIND_LABEL[row.kind] || "其他"));
    if (row.time) {
      head.append(el("span", "row-time", row.time));
    }
    if (row.phase && PHASE_LABEL[row.phase]) {
      head.append(el("span", "row-phase", PHASE_LABEL[row.phase]));
    }
    if (row.kind === "tool" && row.tool && row.tool.name) {
      head.append(el("span", "tool-name", row.tool.name));
    }
    // 预览始终生成，折叠时由 CSS 显示；这样“全部收起”也能看到摘要。
    head.append(el("span", "row-preview", previewText(row)));

    const toggle = el("button", "row-toggle", row.collapsed ? "展开" : "收起");
    toggle.type = "button";
    toggle.setAttribute("aria-expanded", String(!row.collapsed));
    toggle.addEventListener("click", () => {
      const nowCollapsed = wrap.classList.toggle("collapsed");
      toggle.textContent = nowCollapsed ? "展开" : "收起";
      toggle.setAttribute("aria-expanded", String(!nowCollapsed));
    });
    head.append(toggle);

    const body = el("div", "row-body");
    if (row.kind === "tool" && row.tool) {
      renderToolBody(body, row.tool);
    } else {
      renderTextBlock(body, row.text);
    }

    wrap.append(head, body);
    return wrap;
  }

  function renderTurn(turn) {
    const section = el("section", "turn");
    const head = el("div", "turn-head");

    const title = turn.number === 0 ? turn.label || "会话前置" : `第 ${turn.number} 轮`;
    head.append(el("span", "turn-no", title));
    if (turn.started_time) {
      head.append(el("span", "turn-time", turn.started_time));
    }
    if (turn.duration_display) {
      head.append(el("span", "turn-stat", `耗时 ${turn.duration_display}`));
    } else if (turn.completed) {
      head.append(el("span", "turn-stat", "耗时未知"));
    }
    if (turn.tokens && turn.tokens.total_tokens !== undefined) {
      const stat = el("span", "turn-stat", `${formatNumber(turn.tokens.total_tokens)} tok`);
      const parts = [];
      if (turn.tokens.input_tokens !== undefined) {
        parts.push(`输入 ${formatNumber(turn.tokens.input_tokens)}`);
      }
      if (turn.tokens.cached_input_tokens !== undefined) {
        parts.push(`缓存 ${formatNumber(turn.tokens.cached_input_tokens)}`);
      }
      if (turn.tokens.output_tokens !== undefined) {
        parts.push(`输出 ${formatNumber(turn.tokens.output_tokens)}`);
      }
      if (turn.tokens.reasoning_output_tokens !== undefined) {
        parts.push(`思考 ${formatNumber(turn.tokens.reasoning_output_tokens)}`);
      }
      if (parts.length) {
        stat.title = parts.join(" / ");
      }
      head.append(stat);
    } else {
      head.append(el("span", "turn-stat", "token 未知"));
    }
    if (turn.incomplete) {
      head.append(el("span", "turn-flag", "未完成"));
    }
    if (turn.turn_id) {
      const id = el("span", "turn-id", turn.turn_id);
      id.title = turn.turn_id;
      head.append(id);
    }
    section.append(head);

    let shown = 0;
    for (const row of turn.rows || []) {
      if (state.hiddenLabels.has(row.kind)) {
        continue;
      }
      shown += 1;
      section.append(renderRow(row));
    }
    if (!shown && (turn.rows || []).length) {
      section.append(el("div", "turn-note", "本轮内容已按标签隐藏。"));
    }
    if (!(turn.rows || []).length) {
      section.append(el("div", "turn-note", "本轮没有可显示的内容。"));
    }
    return section;
  }

  function renderDetail() {
    clear(dom.detail);
    const detail = state.detail;
    if (!detail) {
      dom.detail.append(emptyDetail("选择一个会话", "从左侧打开会话，查看按轮分组的完整轨迹。"));
      return;
    }

    dom.detail.append(renderHeader(detail));

    const turns = Array.isArray(detail.turns) ? detail.turns : [];
    if (!turns.length) {
      const warnings = Array.isArray(detail.warnings) ? detail.warnings : [];
      let message = "会话文件中没有可解析的对话记录。";
      if (detail.source_file && detail.source_file.exists === false) {
        message = "会话记录文件缺失，无法读取内容。";
      } else if (warnings.length) {
        message = "读取记录失败，原因见上方提示。";
      }
      dom.detail.append(emptyDetail("没有可显示的轨迹", message));
      return;
    }

    dom.detail.append(renderToolbar(detail));
    const container = el("div", "turns");
    for (const turn of turns) {
      container.append(renderTurn(turn));
    }
    dom.detail.append(container);
  }

  // ------------------------------ 事件绑定 ------------------------------

  function init() {
    dom.sourceChips.addEventListener("click", (event) => {
      const button = event.target.closest(".chip");
      if (!button) {
        return;
      }
      state.source = button.dataset.source || "all";
      dom.sourceChips.querySelectorAll(".chip").forEach((chip) => {
        const active = chip === button;
        chip.classList.toggle("active", active);
        chip.setAttribute("aria-pressed", String(active));
      });
      loadSessions();
    });

    dom.search.addEventListener("input", () => {
      window.clearTimeout(searchTimer);
      searchTimer = window.setTimeout(() => {
        state.q = dom.search.value.trim();
        loadSessions();
      }, 180);
    });

    dom.showArchived.addEventListener("change", () => {
      state.archived = dom.showArchived.checked;
      loadSessions();
    });

    dom.showSubagents.addEventListener("change", () => {
      state.subagents = dom.showSubagents.checked;
      loadSessions();
    });

    dom.refresh.addEventListener("click", () => {
      loadSessions();
      if (state.selectedId) {
        openSession(state.selectedId);
      }
    });

    dom.sessionList.addEventListener("click", (event) => {
      const item = event.target.closest(".session-item");
      if (!item || !item.dataset.id) {
        return;
      }
      openSession(item.dataset.id);
    });

    document.addEventListener("keydown", (event) => {
      const active = document.activeElement;
      const typing = active && (active.tagName === "INPUT" || active.tagName === "TEXTAREA");
      if (event.key === "/" && !typing) {
        event.preventDefault();
        dom.search.focus();
        dom.search.select();
      }
      if (event.key === "Escape" && active === dom.search) {
        dom.search.value = "";
        state.q = "";
        dom.search.blur();
        loadSessions();
      }
    });

    const initialSession = new URLSearchParams(window.location.search).get("session");

    loadHealth();
    loadSessions().then(() => {
      if (initialSession && !state.selectedId) {
        openSession(initialSession);
      }
    });
  }

  init();
})();
