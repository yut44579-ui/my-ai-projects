/* app.js · 页面逻辑（TASK-002 Scope B · 深色企业级三栏骨架）
 *
 * 分工：**所有** HTTP 都在 api.js 里；本文件只管"把后端真实数据渲染成页面"。
 * 三条铁律（AC-03 / AC-07 / AC-08）：
 *   ① 页面上的每个数字都来自后端响应（/api/health、/api/tasks、/api/executions、run 响应），
 *      本文件里没有任何业务金额、行数、任务号；没数据就显示空状态，绝不用示例数据填空。
 *   ② 刷新恢复靠**重新请求后端**：init() 第一件事就是拉 health / tasks / executions，
 *      路由用 location.hash（刷新后 #/data 还停在数据管理，并重新读后端）。
 *   ③ 五种 UI 状态都有实际落点：loading（banner/按钮 spinner）、empty（.empty 块）、
 *      success（真实表格）、error（红色 banner + 真实错误码）、disabled（前置条件不满足的按钮）。
 */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const ROUTES = ["overview", "sales", "customers", "products", "anomaly", "weekly", "data", "settings"];
  const PAGE_TITLES = {
    overview: "首页", sales: "销售分析", customers: "客户分析", products: "产品分析",
    anomaly: "异常发现", weekly: "周报中心", data: "数据管理", settings: "系统设置",
  };

  // 指标名 → 中文标签（**只是标签**；指标清单与数值都来自后端）
  const METRIC_LABELS = {
    sales_amount: "销售额",
    rows_in_range: "区间行数",
    rows_valid: "有效行数",
    rows_excluded: "排除行数",
    excluded_amount: "排除金额",
    valid_qty_sum: "有效数量合计",
  };
  const metricLabel = (name) => METRIC_LABELS[name] || name;

  const state = {
    route: "overview",
    health: null,
    tasks: [],
    executions: [],
    upload: null,          // 本次会话的上传响应（后端返回的 file_id / 行数 / 哈希）
    selectedTaskId: "",
    selectedTask: null,    // GET /api/tasks/{id} 的完整任务（含冻结 Spec）
    lastRun: null,         // POST /api/tasks/{id}/run 的响应
    documents: [],         // GET /api/documents 的列表（TASK-003）
    docViewer: null,       // 当前在正文查看器里打开的那条文档记录
    chat: null,            // POST /api/chat 的响应 = 整条链路（TASK-004）
    chatLoading: false,    // 提交中（三态里的 loading）
    chatError: null,       // 提交失败（网络/HTTP 层），与"后端说自己答不了"分开
    capabilities: null,    // GET /api/chat/capabilities（能力与数据边界）
    conversations: [],     // GET /api/conversations 的历史列表
    search: "",
    metricsCatalog: { names: [], source: "loading" },
  };

  // ══════════════════════════════════════════════════════════════════════
  // 小工具
  // ══════════════════════════════════════════════════════════════════════
  const money = new Intl.NumberFormat("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const int = new Intl.NumberFormat("zh-CN");

  const fmtMoney = (value) => (typeof value === "number" && isFinite(value) ? money.format(value) : "—");
  const fmtInt = (value) => (typeof value === "number" && isFinite(value) ? int.format(value) : "—");
  const fmtBytes = (value) => {
    if (typeof value !== "number" || !isFinite(value)) return "—";
    if (value < 1024) return `${value} B`;
    if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
    return `${(value / 1024 / 1024).toFixed(2)} MB`;
  };
  const fmtTime = (iso) => (iso ? String(iso).replace("T", " ").slice(0, 19) : "—");
  const shortHash = (hash) => (hash ? `${String(hash).slice(0, 12)}…` : "—");
  const text = (value) => (value === null || value === undefined || value === "" ? "—" : String(value));

  function show(el) { if (el) el.hidden = false; }
  function hide(el) { if (el) el.hidden = true; }
  function setText(id, value) { const el = $(id); if (el) el.textContent = value; }

  function clear(node) { while (node && node.firstChild) node.removeChild(node.firstChild); }

  function cell(row, value, className) {
    const td = document.createElement("td");
    if (className) td.className = className;
    td.textContent = value;
    row.appendChild(td);
    return td;
  }

  function badge(row, kind, label) {
    const td = document.createElement("td");
    const span = document.createElement("span");
    span.className = `badge ${kind}`;
    span.textContent = label;
    td.appendChild(span);
    row.appendChild(td);
    return td;
  }

  const EMPTY_TEXT = "—";

  // 全局 banner：loading / error 两种全局态
  function banner(kind, html) {
    const el = $("banner");
    if (!el) return;
    if (!kind) { el.hidden = true; el.textContent = ""; return; }
    clear(el);
    el.className = `banner ${kind}`;
    if (kind === "loading") {
      const spin = document.createElement("span");
      spin.className = "spinner";
      el.appendChild(spin);
    }
    const span = document.createElement("span");
    span.innerHTML = html;
    el.appendChild(span);
    el.hidden = false;
  }

  const errorBanner = (err) => banner(
    "error",
    `请求后端失败：${escapeHtml(err && err.message ? err.message : String(err))}` +
    `（错误码 ${escapeHtml(err && err.code ? err.code : "unknown")}）`,
  );

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (ch) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
    ));
  }

  // 行内状态文字（按钮旁）：loading / success / error 三态
  function status(id, message, kind) {
    const el = $(id);
    if (!el) return;
    clear(el);
    if (kind === "loading") {
      const spin = document.createElement("span");
      spin.className = "spinner";
      el.appendChild(spin);
      el.appendChild(document.createTextNode(" "));
    }
    const span = document.createElement("span");
    span.className = kind === "error" ? "bad" : kind === "success" ? "ok" : "";
    span.textContent = message;
    el.appendChild(span);
  }

  function toggleEmpty(emptyId, hasRows) {
    const empty = $(emptyId);
    if (!empty) return;
    empty.hidden = hasRows;
  }

  // 客户端搜索过滤（只过滤**已从后端取回**的行，不伪造任何数据）
  function matchesSearch(...fields) {
    if (!state.search) return true;
    const needle = state.search.toLowerCase();
    return fields.some((field) => String(field === null || field === undefined ? "" : field)
      .toLowerCase().includes(needle));
  }

  const successfulExecutions = () => state.executions.filter((row) => row.status === "success");
  const latestSuccess = () => {
    const rows = successfulExecutions().filter((row) => typeof row.amount_display === "number");
    rows.sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
    return rows.length ? rows[0] : null;
  };
  const taskName = (taskId) => {
    const found = state.tasks.find((task) => task.task_id === taskId);
    return found ? found.name : "（任务已不在列表中）";
  };

  // ══════════════════════════════════════════════════════════════════════
  // 数据加载（全部走 api.js）
  // ══════════════════════════════════════════════════════════════════════
  async function loadHealth() {
    state.health = await API.health();
    renderHealth();
  }

  async function loadTasks() {
    const payload = await API.listTasks({ limit: 100 });
    state.tasks = payload.tasks || [];
    renderTaskSelect();
    renderFiles();
    renderWeeklyTasks();
  }

  async function loadExecutions() {
    const payload = await API.listExecutions({ limit: 100 });
    state.executions = payload.executions || [];
    renderExecutions();
  }

  async function loadDocuments() {
    const payload = await API.listDocuments({ limit: 100 });
    state.documents = payload.documents || [];
    renderDocuments();
  }

  async function loadConversations() {
    const payload = await API.listConversations({ limit: 20 });
    state.conversations = payload.conversations || [];

    // 刷新页面后内存态没了，但"最近一次问答"必须还能看 ——
    // 从后端把那条**完整记录**取回来（不是从列表摘要拼，摘要里没有事实表和三段回答）。
    if (!state.chat && state.conversations.length) {
      try {
        state.chat = await API.getConversation(state.conversations[0].conversation_id);
      } catch (err) {
        // 取不回来就保持空态（页面宁可什么都不显示，也不拿摘要拼一条"看起来完整"的链路）
        state.chat = null;
      }
    }
    renderChat();
    renderChatHistory();
    renderAiConclusion();
  }

  async function loadCapabilities() {
    // 能力清单与**数据边界**都来自后端（页面不写死"支持哪三类问题"这种话）
    state.capabilities = await API.chatCapabilities();
    renderInertControls();
  }

  async function refreshAll() {
    banner("loading", "正在读取后端状态（health / tasks / executions / documents / conversations）…");
    const results = await Promise.allSettled([
      loadHealth(), loadTasks(), loadExecutions(), loadDocuments(),
      loadConversations(), loadCapabilities(),
    ]);
    const failed = results.find((item) => item.status === "rejected");
    if (failed) {
      errorBanner(failed.reason);
    } else {
      banner(null);
    }
    renderDerived();
  }

  // ══════════════════════════════════════════════════════════════════════
  // 渲染：顶栏 / 侧栏 / 右侧面板（都用 /api/health 的真实字段）
  // ══════════════════════════════════════════════════════════════════════
  function renderHealth() {
    const health = state.health;
    if (!health) return;
    const snapshot = health.data_snapshot || {};
    const stateInfo = health.state || {};

    const label = snapshot.path
      ? `数据源：${String(snapshot.path).split(/[\\/]/).pop()} · ${snapshot.match ? "哈希一致" : "哈希不一致"}`
      : "数据源：未配置";
    setText("datasource-label", label);
    const dot = $("datasource-dot");
    if (dot) dot.className = `dot ${snapshot.match ? "ok" : "bad"}`;

    setText("fact-data", "");
    const factData = $("fact-data");
    if (factData) {
      factData.innerHTML = `数据源：<b>${escapeHtml(snapshot.path ? String(snapshot.path).split(/[\\/]/).pop() : "—")}</b>` +
        ` · 快照 ${snapshot.exists ? "存在" : "缺失"} · 哈希 ${snapshot.match ? "一致" : "不一致"} · ${fmtBytes(snapshot.size_bytes)}`;
    }
    const factTemplate = $("fact-template");
    if (factTemplate) {
      const template = health.template || {};
      factTemplate.innerHTML = `模板：<b>${escapeHtml(template.path ? String(template.path).split(/[\\/]/).pop() : "—")}</b>` +
        ` · ${template.exists ? "存在" : "缺失"}`;
    }
    const factState = $("fact-state");
    if (factState) {
      factState.innerHTML = `状态文件：<b>${stateInfo.readable ? "可读" : "不可读"}</b>` +
        ` · 上传 ${fmtInt(stateInfo.uploads)} / 执行 ${fmtInt(stateInfo.executions)} / 任务 ${fmtInt(stateInfo.tasks)}` +
        ` · schema v${text(stateInfo.schema_version)}`;
    }
    const factCode = $("fact-code");
    if (factCode) factCode.innerHTML = `代码版本：<b>${escapeHtml(text(health.code_version))}</b>`;

    setText("side-version", `版本：${text(health.api_version)} · ${text(health.code_version)}`);
    setText("side-perm", `权限与安全：未启用（TASK-011）`);
    // 单用户部署，未做鉴权 —— 这两行如实写"当前就是这样"，不暗示已有权限体系
    setText("side-user", "当前用户：本地（单用户模式）");
    setText("side-role", "角色：owner（尚未做鉴权）");

    setText("rp-service", `${text(health.service)} · ${text(health.status)}`);
    setText("rp-api-version", text(health.api_version));
    setText("rp-code-version", text(health.code_version));
    setText("rp-state-readable", stateInfo.readable ? "可读" : "不可读");
    setText("rp-counts", `${fmtInt(stateInfo.uploads)} / ${fmtInt(stateInfo.executions)} / ${fmtInt(stateInfo.tasks)}`);
    const rpSnapshot = $("rp-snapshot");
    if (rpSnapshot) {
      rpSnapshot.textContent = snapshot.match ? "通过（SHA256 一致）" : "未通过 / 缺失";
      rpSnapshot.className = snapshot.match ? "ok" : "bad";
    }

    renderSettings();
  }

  function renderSettings() {
    const health = state.health;
    const body = $("set-health-body");
    if (!health || !body) return;
    clear(body);
    const rows = [
      ["服务", text(health.service)],
      ["状态", text(health.status)],
      ["API 版本", text(health.api_version)],
      ["代码版本", text(health.code_version)],
      ["后端自报 task", text(health.task)],
      ["后端当前时间", fmtTime(health.time)],
      ["数据源哈希一致", (health.data_snapshot || {}).match ? "是" : "否"],
      ["模板存在", (health.template || {}).exists ? "是" : "否"],
    ];
    rows.forEach(([key, value]) => {
      const row = document.createElement("tr");
      cell(row, key);
      cell(row, value, "num");
      body.appendChild(row);
    });
    toggleEmpty("set-health-empty", rows.length > 0);

    const storage = $("set-storage-body");
    if (storage) {
      clear(storage);
      const info = health.state || {};
      [
        ["状态目录", text(info.state_dir)],
        ["schema_version", text(info.schema_version)],
        ["上传记录数", fmtInt(info.uploads)],
        ["执行记录数", fmtInt(info.executions)],
        ["任务数", fmtInt(info.tasks)],
        ["可读", info.readable ? "是" : "否"],
      ].forEach(([key, value]) => {
        const row = document.createElement("tr");
        cell(row, key);
        cell(row, value, "num");
        storage.appendChild(row);
      });
    }

    const problems = health.problems || [];
    setText("set-problems-wrap", "");
    if (problems.length) {
      const list = $("set-problems");
      clear(list);
      problems.forEach((item) => {
        const li = document.createElement("li");
        li.textContent = String(item);
        list.appendChild(li);
      });
      show($("set-problems-wrap"));
    } else {
      hide($("set-problems-wrap"));
    }
  }

  // ══════════════════════════════════════════════════════════════════════
  // 渲染：KPI / 趋势 / 结论 / 销售分析（都从 /api/executions 与 /api/health 派生）
  // ══════════════════════════════════════════════════════════════════════
  function renderDerived() {
    renderKpis();
    renderTrend();
    renderConclusions();
    renderSales();
  }

  function renderKpis() {
    const info = (state.health && state.health.state) || {};
    const latest = latestSuccess();

    setText("kpi-amount", latest ? fmtMoney(latest.amount_display) : EMPTY_TEXT);
    setText("kpi-amount-sub", latest
      ? `${taskName(latest.task_id)} · ${fmtTime(latest.created_at)}`
      : "尚无成功执行记录");

    setText("kpi-rows", latest ? fmtInt(latest.rows_in_range) : EMPTY_TEXT);
    setText("kpi-rows-sub", latest
      ? `有效 ${fmtInt(latest.rows_valid)} 行`
      : "尚无成功执行记录");

    setText("kpi-excluded", latest ? fmtInt(latest.rows_excluded) : EMPTY_TEXT);
    setText("kpi-excluded-sub", latest
      ? `排除金额 ${fmtMoney(latest.excluded_amount)}`
      : "尚无成功执行记录");

    setText("kpi-tasks", state.health ? fmtInt(info.tasks) : EMPTY_TEXT);
    setText("kpi-tasks-sub", state.health
      ? `上传 ${fmtInt(info.uploads)} · 执行 ${fmtInt(info.executions)}`
      : "来自后端状态统计");
  }

  function renderTrend() {
    const rows = successfulExecutions()
      .filter((row) => typeof row.amount_display === "number")
      .slice()
      .sort((a, b) => String(a.created_at || "").localeCompare(String(b.created_at || "")))
      .slice(-12);

    const svg = $("trend-chart");
    const empty = $("trend-empty");
    if (rows.length < 2) {
      hide(svg);
      show(empty);
      if (rows.length === 1) {
        setText("trend-caption", "只有 1 次成功执行，画不出走势；数据来源：GET /api/executions");
      }
      return;
    }
    show(svg);
    hide(empty);

    const values = rows.map((row) => row.amount_display);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || 1;
    const width = 560;
    const height = 150;
    const padY = 12;
    const step = rows.length > 1 ? width / (rows.length - 1) : width;

    const points = values.map((value, index) => {
      const x = index * step;
      const y = height - padY - ((value - min) / span) * (height - padY * 2);
      return [x, y];
    });

    const line = $("trend-line");
    const area = $("trend-area");
    const dots = $("trend-dots");
    if (line) line.setAttribute("points", points.map((p) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" "));
    if (area) {
      const head = points[0];
      const tail = points[points.length - 1];
      area.setAttribute("d", `M ${head[0].toFixed(1)},${height} L ` +
        points.map((p) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" L ") +
        ` L ${tail[0].toFixed(1)},${height} Z`);
    }
    if (dots) {
      clear(dots);
      points.forEach((point, index) => {
        const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        circle.setAttribute("cx", point[0].toFixed(1));
        circle.setAttribute("cy", point[1].toFixed(1));
        circle.setAttribute("r", "3");
        const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
        title.textContent = `${taskName(rows[index].task_id)} · ${fmtMoney(values[index])} · ${fmtTime(rows[index].created_at)}`;
        circle.appendChild(title);
        dots.appendChild(circle);
      });
    }
    setText("trend-caption", `最近 ${rows.length} 次成功执行 · 数据来源：GET /api/executions`);
  }

  function renderConclusions() {
    const list = $("conclusions-list");
    const latest = latestSuccess();
    if (!list) return;
    clear(list);
    if (!latest) {
      toggleEmpty("conclusion-empty", false);
      return;
    }
    toggleEmpty("conclusion-empty", true);

    const range = latest.range || {};
    const verification = latest.verification || {};
    const items = [
      `最近一次成功执行：<span class="k">${escapeHtml(taskName(latest.task_id))}</span>` +
        ` · 区间 ${escapeHtml(text(range.start))} ~ ${escapeHtml(text(range.end))}` +
        ` · 销售额 <b>¥${fmtMoney(latest.amount_display)}</b>`,
      `覆盖 ${fmtInt(latest.rows_in_range)} 行：有效 ${fmtInt(latest.rows_valid)} 行，排除 ${fmtInt(latest.rows_excluded)} 行` +
        `（排除金额 ${fmtMoney(latest.excluded_amount)}）`,
      `数据快照：${latest.data_snapshot_match ? "SHA256 一致" : "SHA256 不一致"}（${escapeHtml(shortHash(latest.data_sha256))}）`,
      `产出：${escapeHtml(text(latest.excel_rel_path))} · ${fmtBytes(latest.excel_size_bytes)}` +
        ` · 单元格回读校验 ${verification.passed ? "通过" : "未通过"}`,
      `执行耗时 ${text(latest.seconds)} 秒 · 代码版本 ${escapeHtml(text(latest.code_version))}`,
    ];
    items.forEach((html) => {
      const li = document.createElement("li");
      li.innerHTML = html;
      list.appendChild(li);
    });
  }

  function renderSales() {
    const body = $("sales-table-body");
    const latest = latestSuccess();
    if (!body) return;
    clear(body);
    const metrics = (latest && latest.metrics) || {};
    const names = Object.keys(metrics);
    if (!latest || !names.length) {
      toggleEmpty("sales-empty", false);
      setText("sales-caption", "数据来源：GET /api/executions");
      return;
    }
    toggleEmpty("sales-empty", true);
    names.forEach((name) => {
      const row = document.createElement("tr");
      cell(row, metricLabel(name));
      const value = metrics[name];
      cell(row, typeof value === "number" ? (name.endsWith("amount") ? fmtMoney(value) : fmtInt(value)) : text(value), "num");
      cell(row, `执行 ${latest.execution_id}`).className = "num";
      body.appendChild(row);
    });
    setText("sales-caption",
      `最近一次成功执行 ${latest.execution_id} · ${fmtTime(latest.created_at)} · 数据来源：GET /api/executions`);
  }

  // ══════════════════════════════════════════════════════════════════════
  // 渲染：数据管理（任务 / Spec / 执行 / 记录）
  // ══════════════════════════════════════════════════════════════════════
  function renderTaskSelect() {
    const select = $("task-select");
    if (!select) return;
    const previous = state.selectedTaskId;
    clear(select);
    if (!state.tasks.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "（还没有任务）";
      select.appendChild(option);
      state.selectedTaskId = "";
      state.selectedTask = null;
      renderSpec();
      return;
    }
    state.tasks.forEach((task) => {
      const option = document.createElement("option");
      option.value = task.task_id;
      option.textContent = `${task.name}（${task.task_id}）`;
      select.appendChild(option);
    });
    const stillThere = state.tasks.some((task) => task.task_id === previous);
    state.selectedTaskId = stillThere ? previous : state.tasks[0].task_id;
    select.value = state.selectedTaskId;
  }

  function renderFiles() {
    const body = $("files-table-body");
    const custBody = $("cust-files-body");
    if (body) clear(body);
    if (custBody) clear(custBody);

    // 上传记录本身没有列表端点（12 端点冻结），所以这里列的是**任务绑定的数据文件**（真实字段）
    const byFile = new Map();
    state.tasks.forEach((task) => {
      if (!task.file_id) return;
      const entry = byFile.get(task.file_id) || { file_id: task.file_id, filename: task.source_filename, sha256: task.data_sha256, tasks: [] };
      entry.tasks.push(task.name);
      byFile.set(task.file_id, entry);
    });
    const rows = [...byFile.values()].filter((entry) => matchesSearch(entry.filename, entry.file_id));
    rows.forEach((entry) => {
      if (body) {
        const row = document.createElement("tr");
        cell(row, text(entry.filename));
        cell(row, entry.file_id).className = "num";
        cell(row, shortHash(entry.sha256)).className = "num";
        cell(row, entry.tasks.join("、"));
        body.appendChild(row);
      }
      if (custBody) {
        const row = document.createElement("tr");
        cell(row, text(entry.filename));
        cell(row, entry.file_id).className = "num";
        cell(row, shortHash(entry.sha256)).className = "num";
        custBody.appendChild(row);
      }
    });
    toggleEmpty("files-empty", rows.length > 0);
    toggleEmpty("cust-files-empty", rows.length > 0);
  }

  // ── 文档资料（TASK-003）──────────────────────────────────────────────
  // 行数据一律来自 GET /api/documents 的响应；每条只带元信息（不塞整篇正文），
  // 正文要用户真的点「看正文」才去 GET /api/documents/{id}/text 取 —— 列表不预读全文。
  function renderDocuments() {
    const body = $("docs-table-body");
    if (!body) return;
    clear(body);

    const rows = state.documents.filter((doc) => matchesSearch(doc.filename, doc.doc_id, doc.title));
    rows.forEach((doc) => {
      const row = document.createElement("tr");
      cell(row, text(doc.filename));
      cell(row, doc.doc_id).className = "num";
      cell(row, fmtInt(doc.chars)).className = "num";
      cell(row, fmtInt(doc.blocks)).className = "num";
      cell(row, shortHash(doc.sha256)).className = "num";
      cell(row, fmtTime(doc.created_at));

      const action = document.createElement("td");
      const view = document.createElement("button");
      view.type = "button";
      view.className = "btn btn-sm";
      view.textContent = "看正文";
      view.dataset.docId = doc.doc_id;
      view.dataset.docAction = "view";
      action.appendChild(view);
      row.appendChild(action);
      body.appendChild(row);
    });

    // 空态要说清是"真的一条都没有"还是"被搜索过滤掉了"——否则用户会以为文档丢了
    const emptyTitle = $("docs-empty") ? $("docs-empty").querySelector(".empty-title") : null;
    if (emptyTitle) {
      emptyTitle.textContent = state.documents.length && !rows.length
        ? "没有匹配的文档"
        : "还没有文档资料";
    }
    toggleEmpty("docs-empty", rows.length > 0);
  }

  function renderDocResult(payload) {
    const body = $("doc-result");
    if (!body) return;
    clear(body);
    [
      ["doc_id", payload.doc_id],
      ["文件名", payload.filename],
      ["字符数", fmtInt(payload.chars)],
      ["块数", `${fmtInt(payload.blocks)} ${payload.block_unit || ""}`.trim()],
      ["标题", payload.title || "（未识别到标题）"],
      ["SHA256", payload.sha256],
      ["落盘大小", fmtBytes(payload.size_bytes)],
      ["落盘路径", payload.stored_path],
    ].forEach(([key, value]) => {
      const row = document.createElement("tr");
      cell(row, key);
      cell(row, text(value), "num");
      body.appendChild(row);
    });
    show($("doc-result-wrap"));
  }

  // 正文查看器：只有真的取回文本才显示（取不回就报错 + 收起，绝不显示半截内容）
  async function openDocument(docId) {
    const record = state.documents.find((doc) => doc.doc_id === docId) || null;
    setText("doc-viewer-title", record ? `正文 · ${record.filename}` : `正文 · ${docId}`);
    status("doc-viewer-state", "正在读取全文（GET /api/documents/{doc_id}/text）…", "loading");
    hide($("doc-summary"));
    clear($("doc-summary"));
    try {
      const text = await API.documentText(docId);
      state.docViewer = record;
      setText("doc-viewer-meta", record
        ? `${fmtInt(record.chars)} 字符 · ${fmtInt(record.blocks)} ${record.block_unit || "块"} · ${fmtTime(record.created_at)}`
        : "");
      $("doc-viewer-text").textContent = text;
      $("doc-viewer-link").href = API.documentTextUrl(docId);
      show($("doc-viewer"));
      status("doc-viewer-state", `已取回 ${text.length} 个字符（后端重新从原文提取，不是前端缓存的副本）。`, "success");
    } catch (err) {
      state.docViewer = null;
      hide($("doc-viewer"));
      status("doc-upload-state", `读取全文失败：${err.message}（${err.code}）`, "error");
    }
  }

  function renderDocSummary(summary) {
    const box = $("doc-summary");
    if (!box) return;
    clear(box);

    const sentences = (summary.sentences || []).map((sentence) => {
      const div = document.createElement("div");
      div.className = "doc-sentence";
      div.textContent = sentence;          // 原句摘录，一个字都不改写
      return div;
    });
    const keywords = (summary.keywords || []).map((item) => {
      const span = document.createElement("span");
      span.className = "kw";
      span.textContent = item.term;
      const count = document.createElement("b");
      count.textContent = `×${item.count}`;
      span.appendChild(count);
      return span;
    });
    const facts = (summary.key_facts || []).map((fact) => {
      const div = document.createElement("div");
      div.className = "fact";
      const type = document.createElement("span");
      type.className = "fact-type";
      type.textContent = fact.type;
      const value = document.createElement("span");
      value.className = "fact-value";
      value.textContent = fact.value;
      const context = document.createElement("div");
      context.className = "fact-context";
      context.textContent = fact.context || "";
      div.appendChild(type);
      div.appendChild(value);
      if (fact.context) div.appendChild(context);
      return div;
    });

    [
      ["摘要（原文原句摘录，未改写）", sentences],
      ["关键词（词频 ≥2）", keywords],
      ["关键事实（正则抽取，带原文上下文）", facts],
    ].forEach(([heading, nodes]) => {
      const section = document.createElement("div");
      const title = document.createElement("h4");
      title.textContent = `${heading} · ${nodes.length}`;
      section.appendChild(title);
      const holder = document.createElement("div");
      if (heading.startsWith("关键词")) holder.className = "kw-row";
      nodes.forEach((node) => holder.appendChild(node));
      section.appendChild(holder);
      box.appendChild(section);
    });

    // 摘要方法/说明也来自后端（前端不自己编）
    const note = document.createElement("p");
    note.className = "note";
    note.textContent = `${summary.method || ""} · ${summary.note || ""} · 生成于 ${fmtTime(summary.generated_at)} · 原文 ${shortHash(summary.input_sha256)}`;
    box.appendChild(note);
    show(box);
  }

  async function loadDocSummary(docId) {
    status("doc-viewer-state", "正在生成摘要（POST /api/documents/{doc_id}/summary）…", "loading");
    try {
      const summary = await API.documentSummary(docId);
      renderDocSummary(summary);
      status("doc-viewer-state", `摘要已生成（本次${summary.cached ? "取回已缓存的那份" : "重新抽取"}）。`, "success");
    } catch (err) {
      status("doc-viewer-state", `生成摘要失败：${err.message}（${err.code}）`, "error");
    }
  }

  function bindDocuments() {
    const input = $("doc-input");
    const button = $("btn-doc-upload");
    if (!input || !button) return;

    input.addEventListener("change", () => {
      const file = input.files[0];
      status("doc-upload-state", file
        ? `已选择：${file.name}（${fmtBytes(file.size)}）· 点「上传并提取」提交到 POST /api/documents`
        : "选择 .docx / .pdf 后点「上传并提取」。", "");
    });

    button.addEventListener("click", async () => {
      const file = input.files[0];
      if (!file) {
        status("doc-upload-state", "请先选择一个 .docx / .pdf 文件。", "error");
        return;
      }
      button.disabled = true;
      status("doc-upload-state", `正在上传并提取：${file.name}（提取不到文字会判失败）…`, "loading");
      try {
        const payload = await API.uploadDocument(file);
        renderDocResult(payload);
        status("doc-upload-state",
          `提取成功：${payload.filename} · ${fmtInt(payload.chars)} 字符 · ${fmtInt(payload.blocks)} ${payload.block_unit || "块"}`,
          "success");
        input.value = "";
        await loadDocuments();               // 列表以后端为准，不往本地数组里塞
      } catch (err) {
        hide($("doc-result-wrap"));
        status("doc-upload-state", `提取失败：${err.message}（${err.code}）`, "error");
      } finally {
        button.disabled = false;
      }
    });

    // 表格里的「看正文」用事件委托（行是渲染出来的，按钮上挂 data-doc-id）
    const body = $("docs-table-body");
    if (body) {
      body.addEventListener("click", (event) => {
        const target = event.target.closest("[data-doc-action]");
        if (target) openDocument(target.dataset.docId);
      });
    }

    const summaryButton = $("btn-doc-summary");
    if (summaryButton) {
      summaryButton.addEventListener("click", () => {
        if (!state.docViewer) {
          status("doc-viewer-state", "先点某条文档的「看正文」，再生成摘要。", "error");
          return;
        }
        loadDocSummary(state.docViewer.doc_id);
      });
    }

    const closeButton = $("btn-doc-close");
    if (closeButton) {
      closeButton.addEventListener("click", () => {
        hide($("doc-viewer"));
        state.docViewer = null;
      });
    }
  }

  function renderWeeklyTasks() {
    const body = $("wk-tasks-body");
    if (!body) return;
    clear(body);
    const rows = state.tasks.filter((task) => matchesSearch(task.name, task.task_id, task.source_filename));
    rows.forEach((task) => {
      const summary = task.spec_summary || {};
      const row = document.createElement("tr");
      cell(row, text(task.name));
      cell(row, `${text(summary.start)} ~ ${text(summary.end)}`).className = "num";
      badge(row, task.status === "has_run" ? "success" : "info", task.status === "has_run" ? "已执行过" : "已创建");
      cell(row, fmtInt(task.run_count || 0)).className = "num";
      body.appendChild(row);
    });
    toggleEmpty("wk-tasks-empty", rows.length > 0);
  }

  function renderExecutions() {
    const body = $("exec-table-body");
    if (!body) return;
    clear(body);
    const rows = state.executions.filter((row) => matchesSearch(
      row.execution_id, row.task_id, row.status, row.source_filename,
    ));
    rows.forEach((row) => {
      const tr = document.createElement("tr");
      cell(tr, text(row.execution_id)).className = "num";
      cell(tr, row.task_id ? taskName(row.task_id) : "（ad-hoc 执行）");
      const statusKind = row.status === "success" ? "success" : row.status === "failed" ? "failed" : "warn";
      badge(tr, statusKind, text(row.status));
      cell(tr, typeof row.amount_display === "number" ? fmtMoney(row.amount_display) : "—").className = "num";
      cell(tr, typeof row.rows_in_range === "number" ? fmtInt(row.rows_in_range) : "—").className = "num";
      cell(tr, text(row.code_version));
      cell(tr, fmtTime(row.created_at)).className = "num";
      body.appendChild(tr);
    });
    toggleEmpty("exec-empty", rows.length > 0);
  }

  function renderSpec() {
    const task = state.selectedTask;
    const wrap = $("spec-json-wrap");
    const pre = $("spec-json");
    const meta = $("spec-meta");
    if (!task) {
      hide(wrap);
      show($("spec-empty"));
      updateRunButtons();
      return;
    }
    show(wrap);
    hide($("spec-empty"));
    if (pre) pre.textContent = JSON.stringify(task.spec || {}, null, 2);
    if (meta) {
      clear(meta);
      const summary = task.spec_summary || {};
      const fields = [
        ["task_id", task.task_id],
        ["名称", task.name],
        ["状态", task.status],
        ["Spec 版本", `${text(task.spec_id)} v${text(task.spec_version)}`],
        ["数据文件", task.source_filename],
        ["file_id", task.file_id],
        ["数据哈希", shortHash(task.data_sha256)],
        ["快照一致", task.data_snapshot_match ? "是" : "否"],
        ["区间", `${text(summary.start)} ~ ${text(summary.end)}`],
        ["指标", (summary.metrics || []).map(metricLabel).join("、")],
        ["创建时间", fmtTime(task.created_at)],
      ];
      fields.forEach(([key, value]) => {
        const span = document.createElement("span");
        span.innerHTML = `${escapeHtml(key)}：<b>${escapeHtml(text(value))}</b>`;
        meta.appendChild(span);
      });
    }
    updateRunButtons();
  }

  function updateRunButtons() {
    const hasTask = Boolean(state.selectedTask);
    const runBtn = $("btn-run-task");
    if (runBtn) runBtn.disabled = !hasTask;
    const downloadBtn = $("btn-download");
    if (downloadBtn) downloadBtn.disabled = !(state.lastRun && state.lastRun.download_url);
    if (!hasTask) status("run-state", "先在上面选中一个任务。", "");
  }

  async function selectTask(taskId) {
    state.selectedTaskId = taskId;
    // 下拉必须跟着状态走：刚建完任务时 loadTasks() 重建过选项（值还停在上一个任务），
    // 不显式同步就会出现"下拉显示 A、Spec/执行却是 B"的错位。
    const select = $("task-select");
    if (select && select.value !== taskId) select.value = taskId;
    if (!taskId) {
      state.selectedTask = null;
      renderSpec();
      return;
    }
    status("run-state", "正在读取任务的冻结 Spec…", "loading");
    try {
      state.selectedTask = await API.getTask(taskId);
      renderSpec();
      status("run-state", `已读取任务 ${taskId} 的 Spec（GET /api/tasks/{task_id}）。`, "success");
      await loadRuns(taskId);
    } catch (err) {
      state.selectedTask = null;
      renderSpec();
      status("run-state", `读取任务失败：${err.message}（${err.code}）`, "error");
      errorBanner(err);
    }
  }

  // 刷新恢复：任务的历史执行记录从后端读回来（不依赖内存）
  async function loadRuns(taskId) {
    try {
      const payload = await API.taskRuns(taskId, { limit: 5 });
      const runs = payload.runs || [];
      if (!runs.length) {
        // 这个任务还没跑过：必须把上一次任务的执行结果清掉，
        // 否则"选中的是没跑过的任务，却显示着上一个任务的数字"（错位展示）。
        state.lastRun = null;
        renderRunResult();
        return;
      }
      const latest = runs[0];
      state.lastRun = {
        status: latest.status,
        amount: latest.amount,
        rows_in_range: latest.rows_in_range,
        rows_valid: latest.rows_valid,
        rows_excluded: latest.rows_excluded,
        download_url: latest.download_url || "",
        created_at: latest.created_at,
        source: "GET /api/tasks/{task_id}/runs",
      };
      renderRunResult();
      status("run-state", `已从后端恢复最近一次执行（${fmtTime(latest.created_at)}）。`, "success");
    } catch (err) {
      status("run-state", `读取执行记录失败：${err.message}（${err.code}）`, "error");
    }
  }

  function renderRunResult() {
    const wrap = $("run-result-wrap");
    const body = $("run-metrics-body");
    const run = state.lastRun;
    if (!run || !body) {
      if (body) clear(body);          // 清干净：不留在 DOM 里装作"这个任务跑出过结果"
      hide(wrap);
      show($("run-empty"));
      updateRunButtons();
      return;
    }
    clear(body);
    const rows = [
      ["status", run.status],
      ["销售额（展示口径）", typeof run.amount === "number" ? fmtMoney(run.amount) : "—"],
      ["区间行数", typeof run.rows_in_range === "number" ? fmtInt(run.rows_in_range) : "—"],
      ["有效行数", typeof run.rows_valid === "number" ? fmtInt(run.rows_valid) : "—"],
      ["排除行数", typeof run.rows_excluded === "number" ? fmtInt(run.rows_excluded) : "—"],
      ["执行时间", fmtTime(run.created_at)],
      ["数据来源", run.source],
    ];
    rows.forEach(([key, value]) => {
      const row = document.createElement("tr");
      cell(row, key);
      cell(row, text(value), "num");
      body.appendChild(row);
    });
    show(wrap);
    hide($("run-empty"));
    updateRunButtons();
  }

  // ══════════════════════════════════════════════════════════════════════
  // 路由（hash，刷新后停在同一页并重新读后端）
  // ══════════════════════════════════════════════════════════════════════
  function currentRoute() {
    const raw = (window.location.hash || "").replace(/^#\/?/, "").split("?")[0];
    return ROUTES.includes(raw) ? raw : "overview";
  }

  function renderRoute() {
    state.route = currentRoute();
    ROUTES.forEach((route) => {
      const page = $("page-" + route);
      if (page) page.hidden = route !== state.route;
      const link = $("nav-" + route);
      if (link) link.classList.toggle("active", route === state.route);
    });
    const workspace = $("workspace");
    if (workspace) workspace.scrollTop = 0;
    document.title = `${PAGE_TITLES[state.route]} · 销售报表 Agent`;
  }

  function goto(route) {
    if (window.location.hash === `#/${route}`) {
      renderRoute();
    } else {
      window.location.hash = `#/${route}`;
    }
  }

  // ══════════════════════════════════════════════════════════════════════
  // 交互绑定
  // ══════════════════════════════════════════════════════════════════════
  // ══════════════════════════════════════════════════════════════════════
  // 自然语言问答（TASK-004）
  //
  // 页面**不自己拼链路**：后端 POST /api/chat 回的就是那条落盘记录，
  // 这里只是把它的五个字段分别摆到五个折叠块里。所以页面显示的东西
  // === state/conversations.json 里存的东西 === 事后能核对的东西。
  //
  // 数字（销售额、行数、占比）全部直接取自响应，页面不做任何算术。
  // ══════════════════════════════════════════════════════════════════════
  const CHAT_STATUS_TEXT = {
    ok: "执行成功",
    degraded: "执行成功（LLM 未参与推断）",
    unsupported: "数据不支持这个问题",
    error: "未能回答",
  };

  const SECTION_SOURCE_LABEL = {
    code: "程序生成",
    llm: "模型生成",
  };

  // 复用既有的四种 badge 变体（success/warn/info/failed），不为聊天另造一套颜色
  const CHAT_BADGE_CLASS = {
    ok: "success",
    degraded: "warn",
    unsupported: "info",
    error: "failed",
  };

  function fmtByStyle(value, style) {
    if (typeof value !== "number" || !isFinite(value)) return text(value);
    if (style === "money") return fmtMoney(value);
    if (style === "int") return fmtInt(value);
    if (style === "qty") return fmtInt(Math.round(value));
    if (style === "pct") return value.toFixed(2);
    return Number.isInteger(value) ? fmtInt(value) : String(value);
  }

  function toolLabel(tool) {
    if (!tool) return "未调用工具";
    if (typeof tool === "string") return `工具 ${tool}`;
    return tool.title || tool.name || "未调用工具";
  }

  function setChatState(kind, message) {
    const el = $("chat-state");
    if (!el) return;
    if (!kind) {
      el.hidden = true;
      el.textContent = "";
      el.className = "chat-state";
      return;
    }
    el.hidden = false;
    el.className = `chat-state ${kind}`;
    el.textContent = message;
  }

  async function askQuestion(question) {
    const asked = String(question || "").trim();
    if (!asked) {
      setChatState("error", "请先输入一个问题。");
      return;
    }
    goto("overview");
    state.chatLoading = true;
    state.chatError = null;
    state.chat = null;
    renderChat();
    setChatState(
      "loading",
      `正在提问：「${asked}」—— 解析意图 → 确定性计算 → 组织回答（推理模型可能要十几秒）…`,
    );

    try {
      state.chat = await API.chat(asked);
    } catch (err) {
      // 这一支是**请求本身**失败（网络/HTTP），与"后端答不了这个问题"（记录里的 status）分开
      state.chatError = err;
    } finally {
      state.chatLoading = false;
      renderChat();
      await loadConversations().catch(() => {});
    }
  }

  function renderChat() {
    const body = $("chat-body");
    const empty = $("chat-empty");
    if (!body || !empty) return;

    if (state.chatLoading) {
      hide(body);
      hide(empty);
      return;
    }
    if (state.chatError) {
      hide(body);
      hide(empty);
      const err = state.chatError;
      setChatState("error", `提交失败（${err.code || err.name}）：${err.message}`);
      return;
    }
    if (!state.chat) {
      hide(body);
      show(empty);
      setChatState(null);
      return;
    }

    hide(empty);
    show(body);
    const record = state.chat;
    const status = record.status || "error";

    // 顶部状态条：把"这次答成什么样"和 LLM 有没有参与都摆出来（不藏着）
    setChatState(
      status === "ok" || status === "degraded" ? "ok" : (status === "unsupported" ? "warn" : "error"),
      record.notice || "",
    );

    const badge = $("chat-status-badge");
    if (badge) {
      badge.textContent = CHAT_STATUS_TEXT[status] || status;
      badge.className = `badge ${CHAT_BADGE_CLASS[status] || "warn"}`;
    }
    setText("chat-created", fmtTime(record.created_at));

    const llm = record.llm || {};
    setText(
      "chat-llm",
      llm.used
        ? `LLM：${llm.provider || ""} ${llm.model || ""}（负责解析意图与组织语言）`
        : `LLM 未参与（${(llm.error && llm.error.code) || "未配置"}）—— 数字仍由程序算`,
    );

    setText("chat-question", record.question || "—");

    const intentPre = $("chat-intent");
    if (intentPre) {
      intentPre.textContent = record.intent
        ? JSON.stringify(record.intent, null, 2)
        : `（没有解析出 Intent）\n${(record.error && record.error.message) || ""}`;
    }

    renderChatTool(record.tool);
    renderChatFacts(record);
    renderChatAnswer(record.answer);
    setText("chat-notice", record.notice || "");

    // 五个环节按"这一环到底有没有内容"决定展开 ——
    // 答不了的问题（数据不支持）不该一进来就摊开四个空块，那看着像有东西其实没有。
    const stepOpen = {
      "chat-step-question": true,
      "chat-step-intent": !!record.intent,
      "chat-step-tool": !!(record.tool && record.tool.name),
      "chat-step-facts": !!record.facts,
      "chat-step-answer": !!(record.answer && record.answer.text),
    };
    Object.entries(stepOpen).forEach(([id, open]) => {
      const el = $(id);
      if (el) el.open = open;
    });
  }

  function renderChatTool(tool) {
    const box = $("chat-tool");
    if (!box) return;
    clear(box);
    if (!tool || !tool.name) {
      box.appendChild(line("本次没有调用任何工具（没解析出意图，或问题涉及数据里没有的维度）。"));
      return;
    }
    box.appendChild(line(`${tool.title || ""} · 工具名 ${tool.name}（白名单里的三个之一）`));
    const params = document.createElement("pre");
    params.className = "code";
    params.textContent = JSON.stringify(tool.params || {}, null, 2);
    box.appendChild(params);
    (tool.notes || []).forEach((note) => box.appendChild(line(note, "note-line")));
  }

  function renderChatFacts(record) {
    const box = $("chat-facts");
    if (!box) return;
    clear(box);
    const tool = record.tool;
    if (!tool || !record.facts) {
      box.appendChild(line("本次没有计算结果。"));
      return;
    }
    const table = document.createElement("table");
    table.className = "table";
    const head = document.createElement("tr");
    ["指标", "数值", "单位", "说明"].forEach((label) => {
      const th = document.createElement("th");
      th.textContent = label;
      head.appendChild(th);
    });
    table.appendChild(head);
    (tool.display || []).forEach((item) => {
      const row = document.createElement("tr");
      cell(row, item.label);
      cell(row, fmtByStyle(item.value, item.format), "num");
      cell(row, item.unit || "—");
      cell(row, [item.derived ? "派生" : "", item.note || ""].filter(Boolean).join(" · ") || "—");
      table.appendChild(row);
    });
    const wrap = document.createElement("div");
    wrap.className = "table-wrap";
    wrap.appendChild(table);
    box.appendChild(wrap);

    const raw = document.createElement("pre");
    raw.className = "code";
    raw.textContent = JSON.stringify({ facts: record.facts, selfcheck: tool.selfcheck }, null, 2);
    box.appendChild(raw);
  }

  function renderChatAnswer(payload) {
    const box = $("chat-answer");
    if (!box) return;
    clear(box);
    if (!payload || !payload.sections) {
      box.appendChild(line("本次没有生成回答。"));
      return;
    }
    (payload.sections || []).forEach((section) => {
      const wrap = document.createElement("div");
      wrap.className = "answer-section";
      const head = document.createElement("div");
      head.className = "answer-head";
      const title = document.createElement("b");
      title.textContent = section.title || "";
      head.appendChild(title);
      const tag = document.createElement("span");
      tag.className = `src-tag src-${section.source}`;
      tag.textContent = SECTION_SOURCE_LABEL[section.source] || section.source || "";
      head.appendChild(tag);
      if (section.inferred) {
        const inferred = document.createElement("span");
        inferred.className = "src-tag src-inferred";
        inferred.textContent = "推断";
        head.appendChild(inferred);
      }
      wrap.appendChild(head);
      const body = document.createElement("div");
      body.className = "answer-text";
      body.textContent = section.text || "";
      wrap.appendChild(body);
      box.appendChild(wrap);
    });

    // 数字闸门的报告也显示出来：这是"LLM 没自己造数"的直接证据
    const guard = payload.guard;
    if (guard) {
      box.appendChild(
        line(
          guard.checked
            ? `数字闸门：已核对（合法数字 ${guard.allowed_count} 个）—— ${
              guard.passed ? "模型写的内容全部可追溯" : `拦下越界数字 ${guard.violations.join("、")}，相关段落已作废`
            }`
            : "数字闸门：本次 LLM 未参与，未启用核对",
          "note-line",
        ),
      );
    }
  }

  function line(content, className) {
    const div = document.createElement("div");
    if (className) div.className = className;
    div.textContent = content;
    return div;
  }

  function renderChatHistory() {
    const box = $("chat-history");
    const empty = $("chat-history-empty");
    if (!box || !empty) return;
    clear(box);
    const items = state.conversations || [];
    // 一次都没问过的时候把整张历史卡收起来 —— 首屏不该堆两张空卡
    const wrap = $("chat-history-wrap");
    if (wrap) wrap.hidden = !items.length;
    if (!items.length) {
      show(empty);
      return;
    }
    hide(empty);
    items.forEach((item) => {
      const row = document.createElement("div");
      row.className = "row chat-history-row";
      row.dataset.conversationId = item.conversation_id;

      const main = document.createElement("div");
      main.className = "row-main";
      const title = document.createElement("div");
      title.className = "row-title";
      title.textContent = item.question || "—";
      main.appendChild(title);
      const sub = document.createElement("div");
      sub.className = "row-sub";
      sub.textContent = [
        fmtTime(item.created_at),
        CHAT_STATUS_TEXT[item.status] || item.status,
        item.tool ? `工具 ${item.tool}` : "未调用工具",
        typeof item.sales_amount === "number" ? `销售额 ${fmtMoney(item.sales_amount)}` : "",
      ].filter(Boolean).join(" · ");
      main.appendChild(sub);
      row.appendChild(main);

      const button = document.createElement("button");
      button.className = "btn btn-sm";
      button.textContent = "看链路";
      button.dataset.conversationId = item.conversation_id;
      row.appendChild(button);
      box.appendChild(row);
    });
  }

  async function openConversation(conversationId) {
    if (!conversationId) return;
    setChatState("loading", "正在从后端读取这次问答的完整记录…");
    try {
      state.chatError = null;
      state.chat = await API.getConversation(conversationId);
    } catch (err) {
      state.chatError = err;
    }
    renderChat();
  }

  function renderAiConclusion() {
    const empty = $("ai-conclusion-empty");
    const body = $("ai-conclusion-body");
    if (!empty || !body) return;

    // 优先显示"刚刚问的那次"（内存态就是刚落盘的整条记录）；
    // 刷新后内存态没有了，loadConversations() 已经把最近一次从后端取回来填进 state.chat。
    const latest = state.chat;
    if (!latest) {
      show(empty);
      hide(body);
      return;
    }
    hide(empty);
    show(body);

    // 这张卡是"最近一次"的投影，所以标题上写清楚它到底是哪一次（时间 + 走的哪个工具）。
    // 注意 toolLabel()：完整记录里 tool 是对象，列表摘要里 tool 是名字符串 —— 两种都要能显示。
    setText(
      "ai-conclusion-source",
      `最近一次 · ${fmtTime(latest.created_at)} · ${toolLabel(latest.tool)}`,
    );
    setText("ai-conclusion-question", latest.question || "—");

    const factsBox = $("ai-conclusion-facts");
    if (factsBox) {
      clear(factsBox);
      if (latest.tool && latest.tool.display) {
        latest.tool.display.forEach((item) => {
          const div = document.createElement("div");
          div.className = "rp-fact";
          const label = document.createElement("span");
          label.textContent = `${item.label}：`;
          const value = document.createElement("b");
          value.textContent = `${fmtByStyle(item.value, item.format)}${item.unit || ""}`;
          div.appendChild(label);
          div.appendChild(value);
          factsBox.appendChild(div);
        });
      } else {
        // 后端说了话才写"没有事实"；否则就是记录还没取回来，别替它下结论
        factsBox.appendChild(line(
          latest.status === "unsupported" || latest.status === "error"
            ? "这次没有可展示的事实（问题涉及数据里没有的维度）。"
            : "这条记录里没有事实表。",
        ));
      }
    }

    const whyBox = $("ai-conclusion-why");
    if (whyBox) {
      clear(whyBox);
      const sections = (latest.answer && latest.answer.sections) || [];
      const why = sections.find((section) => section.key === "why");
      if (why) {
        const tag = document.createElement("span");
        tag.className = "src-tag src-inferred";
        tag.textContent = why.inferred ? "推断" : "程序生成";
        whyBox.appendChild(tag);
        whyBox.appendChild(line(why.text || ""));
      }
    }
  }

  function bindTopbar() {
    $("global-search").addEventListener("input", (event) => {
      state.search = event.target.value.trim();
      renderExecutions();
      renderFiles();
      renderDocuments();
      renderWeeklyTasks();
    });
    $("btn-history").addEventListener("click", () => goto("data"));
    $("btn-settings").addEventListener("click", () => goto("settings"));
    $("avatar").addEventListener("click", () => goto("settings"));
  }

  function bindRightPanel() {
    const tabs = ["rp-tab-perm", "rp-tab-security", "rp-tab-circuit", "rp-tab-status"];
    // 事件委托挂在 Tab 容器上（容器自己不承载语义，只负责转发点击）
    const container = $("rp-tabs");
    if (container) {
      container.addEventListener("click", (event) => {
        const tab = event.target.closest(".tab");
        if (!tab || !tab.dataset.pane) return;
        tabs.forEach((other) => {
          const otherTab = $(other);
          if (otherTab) {
            otherTab.classList.toggle("active", otherTab === tab);
            const pane = $(otherTab.dataset.pane);
            if (pane) pane.hidden = otherTab !== tab;
          }
        });
      });
    }
    const defaultTab = $("rp-tab-status");
    if (defaultTab) defaultTab.click();
  }

  function bindDataPage() {
    $("file-input").addEventListener("change", () => {
      const file = $("file-input").files[0];
      status("upload-state", file
        ? `已选择：${file.name}（${fmtBytes(file.size)}）· 点「上传」提交到 POST /api/upload`
        : "选择真实 Excel 文件（.xlsx / .xls）后点上传。", "");
    });

    $("btn-upload").addEventListener("click", async () => {
      const input = $("file-input");
      const file = input.files[0];
      if (!file) {
        status("upload-state", "请先选择一个 .xlsx / .xls 文件。", "error");
        return;
      }
      const button = $("btn-upload");
      button.disabled = true;
      status("upload-state", `正在上传 ${file.name}（大文件解析较慢，请稍候）…`, "loading");
      try {
        const payload = await API.upload(file);
        state.upload = payload;
        renderUploadResult(payload);
        $("task-file-id").value = payload.file_id || "";
        status("upload-state", `上传成功：${payload.filename} · ${fmtInt(payload.rows)} 行 · ${payload.columns} 列`, "success");
        updateCreatePrecondition();
      } catch (err) {
        state.upload = null;
        hide($("upload-result-wrap"));
        status("upload-state", `上传失败：${err.message}（${err.code}）`, "error");
      } finally {
        button.disabled = false;
      }
    });

    ["task-name", "task-file-id", "task-start", "task-end"].forEach((id) => {
      const el = $(id);
      if (el) el.addEventListener("input", updateCreatePrecondition);
    });

    $("btn-create-task").addEventListener("click", async () => {
      const metrics = [...document.querySelectorAll("#task-metrics input:checked")].map((el) => el.value);
      const payload = {
        name: $("task-name").value.trim(),
        file_id: $("task-file-id").value.trim(),
        start: $("task-start").value,
        end: $("task-end").value,
        metrics,
      };
      const button = $("btn-create-task");
      button.disabled = true;
      status("create-task-state", "正在建任务（POST /api/tasks）…", "loading");
      try {
        const created = await API.createTask(payload);
        status("create-task-state", `任务已创建：${created.task_id}`, "success");
        await loadTasks();
        await selectTask(created.task_id);
      } catch (err) {
        status("create-task-state", `建任务失败：${err.message}（${err.code}）`, "error");
      } finally {
        updateCreatePrecondition();
      }
    });

    $("task-select").addEventListener("change", (event) => {
      state.lastRun = null;
      renderRunResult();
      selectTask(event.target.value);
    });

    $("btn-reload-tasks").addEventListener("click", async () => {
      status("run-state", "正在重新读取任务列表…", "loading");
      try {
        await loadTasks();
        await selectTask(state.selectedTaskId);
      } catch (err) {
        status("run-state", `刷新任务失败：${err.message}（${err.code}）`, "error");
      }
    });

    $("btn-run-task").addEventListener("click", async () => {
      if (!state.selectedTask) return;
      const button = $("btn-run-task");
      button.disabled = true;
      status("run-state", "正在执行（真实计算 + 渲染 xlsx，可能要几分钟）…", "loading");
      try {
        const result = await API.runTask(state.selectedTask.task_id);
        state.lastRun = Object.assign({ source: "POST /api/tasks/{task_id}/run" }, result);
        renderRunResult();
        status("run-state", `执行成功：${result.execution_id} · 耗时 ${result.seconds} 秒`, "success");
        await loadExecutions();
        await loadTasks();
        renderDerived();
      } catch (err) {
        state.lastRun = null;
        renderRunResult();
        status("run-state", `执行失败：${err.message}（${err.code}）`, "error");
      } finally {
        updateRunButtons();
      }
    });

    $("btn-download").addEventListener("click", () => {
      if (!state.lastRun || !state.lastRun.download_url) return;
      // 下载地址一律用**后端返回的 download_url**（相对 URL 拼当前源），前端不自己拼路径
      window.location.href = API.downloadUrl(state.lastRun.download_url);
    });

    $("btn-refresh-exec").addEventListener("click", async () => {
      status("run-state", "正在刷新执行记录…", "loading");
      try {
        await loadExecutions();
        status("run-state", "执行记录已刷新（GET /api/executions）。", "success");
      } catch (err) {
        status("run-state", `刷新失败：${err.message}（${err.code}）`, "error");
      }
    });
  }

  function renderUploadResult(payload) {
    const body = $("upload-result");
    if (!body) return;
    clear(body);
    [
      ["file_id", payload.file_id],
      ["文件名", payload.filename],
      ["行数", fmtInt(payload.rows)],
      ["列数", fmtInt(payload.columns)],
      ["SHA256", payload.sha256],
      ["落盘大小", fmtBytes(payload.size_bytes)],
    ].forEach(([key, value]) => {
      const row = document.createElement("tr");
      cell(row, key);
      cell(row, text(value), "num");
      body.appendChild(row);
    });
    show($("upload-result-wrap"));
  }

  function updateCreatePrecondition() {
    const name = $("task-name").value.trim();
    const fileId = $("task-file-id").value.trim();
    const start = $("task-start").value;
    const end = $("task-end").value;
    const metrics = [...document.querySelectorAll("#task-metrics input:checked")];
    const reasons = [];
    if (!name) reasons.push("填任务名称");
    if (!fileId) reasons.push("先上传数据（或填 file_id）");
    if (!start || !end) reasons.push("选开始与结束日期");
    if (start && end && start > end) reasons.push("结束日期不能早于开始日期");
    if (!metrics.length) reasons.push("至少选一个指标");

    const button = $("btn-create-task");
    button.disabled = reasons.length > 0;
    setText("create-precondition", reasons.length
      ? `建任务按钮当前禁用，还缺：${reasons.join(" / ")}。`
      : "前置条件已满足，可以建任务（POST /api/tasks，Spec 会冻结在任务里）。");
  }

  async function renderMetricOptions() {
    const box = $("task-metrics");
    if (!box) return;
    clear(box);
    let catalog;
    try {
      catalog = await API.metricCatalog();
    } catch (err) {
      catalog = { names: [], source: "error" };
    }
    state.metricsCatalog = catalog;
    catalog.names.forEach((name) => {
      const label = document.createElement("label");
      const input = document.createElement("input");
      input.type = "checkbox";
      input.value = name;
      input.checked = true;
      input.addEventListener("change", updateCreatePrecondition);
      label.appendChild(input);
      label.appendChild(document.createTextNode(metricLabel(name)));
      box.appendChild(label);
    });
    setText("metrics-source", catalog.source === "openapi"
      ? "（来源：后端 /openapi.json）"
      : `（后端目录读取失败，用兜底清单：${catalog.source}）`);
    updateCreatePrecondition();
  }

  // ══════════════════════════════════════════════════════════════════════
  // 尚未实现的能力：把"为什么不可用、归哪个 TASK"写进 title，
  // 让禁用状态是**可解释的**，而不是一个不响应的死按钮。
  // ══════════════════════════════════════════════════════════════════════
  const INERT_HINT = "尚未实现：自然语言分析由 TASK-004 接入（NL → Intent → 白名单 Tool → 确定性计算）";

  function renderInertControls() {
    // 自然语言入口在 TASK-004 已经**真的接通**了，不再进"未实现"名单 ——
    // 它的可用性由后端 /api/chat/capabilities 说了算（见下面的 renderNlNote）。
    const nlAlive = !!(state.capabilities && state.capabilities.intents);
    ["nl-input", "hero-nl-input"].forEach((id) => {
      const el = $(id);
      if (el) el.title = nlAlive ? "自然语言提问：回车即提交" : INERT_HINT;
    });
    renderNlNote();
    // 三个企业化开关：禁用的原因是"能力还没做"，把归口 TASK 写在 title 上（不摆假的状态标签）
    const switches = [
      ["sec-rbac", "用户与权限（RBAC）尚未实现：TASK-011 接入"],
      ["sec-acl", "访问控制白 / 黑名单尚未实现：TASK-012 接入"],
      ["sec-circuit", "熔断机制尚未实现：TASK-013 接入"],
    ];
    switches.forEach(([id, hint]) => {
      const el = $(id);
      if (el) el.title = hint;
    });
    const notify = $("btn-notify");
    if (notify) notify.title = "通知：后端暂无通知能力（企业化能力在 TASK-014，未启用）";
    const facts = $("hero-facts");
    if (facts) facts.title = "以上四项全部来自 GET /api/health 的真实字段";
  }

  // ══════════════════════════════════════════════════════════════════════
  // 自然语言入口的交互（TASK-004）
  // ══════════════════════════════════════════════════════════════════════
  function renderNlNote() {
    const caps = state.capabilities;
    if (!caps || !caps.intents) return;   // 拿不到能力清单就不改文案（不编一句"支持三类问题"糊上去）

    const llm = caps.llm || {};
    const llmText = llm.configured ? `${llm.model || "LLM"} 已就绪` : "LLM 未配置（只能走关键词降级）";
    const names = caps.intents.map((item) => item.title).join(" / ");
    const profile = caps.data_profile || {};

    setText("nl-note", `自然语言入口：可问 ${names}（数字全部由程序算，LLM 只负责听懂问题与组织语言）· ${llmText}`);
    setText("chat-source", `POST /api/chat · 白名单工具 ${caps.intents.length} 个`);
    setText(
      "hero-nl-note",
      `本版支持 ${names}；数据范围 ${text(profile.first_day)} ~ ${text(profile.last_day)}，`
      + `问数据里没有的维度（如区域）会被明确拒绝。`,
    );
  }

  function scrollToChat() {
    const panel = $("chat-panel");
    if (panel && panel.scrollIntoView) panel.scrollIntoView({ block: "start" });
  }

  function bindChat() {
    // 两个输入框（顶栏 + hero）走**同一个**提交函数，行为完全一致
    const pairs = [["nl-input", "nl-ask"], ["hero-nl-input", "hero-nl-btn"]];
    pairs.forEach(([inputId, buttonId]) => {
      const input = $(inputId);
      const button = $(buttonId);
      if (button) {
        button.addEventListener("click", () => {
          askQuestion(input ? input.value : "").then(scrollToChat);
        });
      }
      if (input) {
        input.addEventListener("keydown", (event) => {
          if (event.key !== "Enter") return;
          event.preventDefault();
          askQuestion(input.value).then(scrollToChat);
        });
      }
    });

    // 示例 chips：问题原文写在 HTML 的 data-question 上（**不是** JS 里另抄一份）
    ["chip-summary", "chip-trend", "chip-products"].forEach((id) => {
      const chip = $(id);
      if (!chip) return;
      chip.addEventListener("click", () => {
        const question = chip.dataset.question || "";
        const input = $("hero-nl-input");
        if (input) input.value = question;
        askQuestion(question).then(scrollToChat);
      });
    });

    // 历史列表：事件委托（列表是动态重建的，不能逐个绑）
    const history = $("chat-history");
    if (history) {
      history.addEventListener("click", (event) => {
        const holder = event.target.closest("[data-conversation-id]");
        if (!holder) return;
        openConversation(holder.dataset.conversationId).then(scrollToChat);
      });
    }

    const jump = $("btn-ai-conclusion");
    if (jump) jump.addEventListener("click", scrollToChat);
  }

  // ══════════════════════════════════════════════════════════════════════
  // 启动
  // ══════════════════════════════════════════════════════════════════════
  async function init() {
    renderRoute();
    window.addEventListener("hashchange", renderRoute);
    bindTopbar();
    bindRightPanel();
    bindDataPage();
    bindDocuments();
    bindChat();
    renderInertControls();
    await renderMetricOptions();
    hide($("upload-result-wrap"));
    hide($("run-result-wrap"));
    hide($("doc-result-wrap"));
    hide($("chat-body"));
    renderSpec();
    renderRunResult();
    renderChat();
    renderChatHistory();
    renderAiConclusion();
    await refreshAll();
    if (state.selectedTaskId) await selectTask(state.selectedTaskId);
  }

  document.addEventListener("DOMContentLoaded", () => {
    init().catch((err) => {
      errorBanner(err);
      status("upload-state", `初始化失败：${err.message}`, "error");
    });
  });
})();
