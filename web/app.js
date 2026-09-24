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

  const ROUTES = ["overview", "sales", "customers", "products", "anomaly", "weekly",
                  "raw", "data", "settings"];
  const PAGE_TITLES = {
    overview: "首页", sales: "销售分析", customers: "客户分析", products: "产品分析",
    anomaly: "异常发现", weekly: "周报中心", raw: "原始数据", data: "数据管理", settings: "系统设置",
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
    datasets: [],          // GET /api/datasets 的列表（数据源）
    tables: {},            // 四张业务表的查询结果（key = 表名）：分页/排序/筛选全由后端算
    tablePanels: {},       // 表格面板实例（每张表一个，负责组装查询条件与渲染）
    inspect: null,         // 导入向导第 1 步的结果（文件类型/工作表/预览/字段映射）
    imported: null,        // 导入向导第 2 步的结果（登记出来的数据集）
    menuOpen: false,
  };

  // ══════════════════════════════════════════════════════════════════════
  // 小工具
  // ══════════════════════════════════════════════════════════════════════
  const money = new Intl.NumberFormat("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const int = new Intl.NumberFormat("zh-CN");

  const fmtMoney = (value) => (typeof value === "number" && isFinite(value) ? money.format(value) : "—");
  // 币种符号**只从后端声明取**（capabilities.currency，数据里没有货币字段）。
  // 后端没给就返回空串：宁可金额前面什么都不写，也不自作主张补一个货币符号。
  const currencySymbol = () => {
    const currency = (state.capabilities || {}).currency || {};
    return currency.symbol || "";
  };
  const fmtInt = (value) => (typeof value === "number" && isFinite(value) ? int.format(value) : "—");
  const fmtBytes = (value) => {
    if (typeof value !== "number" || !isFinite(value)) return "—";
    if (value < 1024) return `${value} B`;
    if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
    return `${(value / 1024 / 1024).toFixed(2)} MB`;
  };
  const fmtTime = (iso) => (iso ? String(iso).replace("T", " ").slice(0, 19) : "—");
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

  // 错误文案：只说"请求失败 + 一句人话"。err.code / err.name / 接口路径都不上页面
  // （后台的记录里都有；界面上出现 undefined、TypeError、/api/xxx 都算事故）。
  const errorBanner = (err) => banner(
    "error",
    `请求失败：${escapeHtml(err && err.message ? err.message : String(err))}`,
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
    renderWeeklyReports();
  }

  async function loadCapabilities() {
    // 能力清单与**数据边界**都来自后端（页面不写死"支持哪三类问题"这种话）
    state.capabilities = await API.chatCapabilities();
    renderInertControls();
    renderCurrency();
  }

  function renderCurrency() {
    // KPI 卡片上的币种图标也跟着后端的声明走（前端不写死任何币种：口径只有 tools.DATASET_CURRENCY 一处）
    const icon = $("kpi-currency");
    if (icon) icon.textContent = currencySymbol() || "¤";
  }

  async function refreshAll() {
    banner("loading", "正在读取系统状态…");
    const results = await Promise.allSettled([
      loadHealth(), loadTasks(), loadExecutions(), loadDocuments(),
      loadConversations(), loadCapabilities(), loadDatasets(),
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

    // 页面上的每一句都是**业务语言**：说"数据源是否与登记时一致"，不说哈希怎么算的。
    const sourceName = snapshot.path ? String(snapshot.path).split(/[\\/]/).pop() : "";
    setText("datasource-label", sourceName ? `数据源：${sourceName}` : "数据源：未配置");
    const dot = $("datasource-dot");
    if (dot) dot.className = `dot ${snapshot.match ? "ok" : "bad"}`;
    const chip = $("datasource-chip");
    if (chip) {
      chip.title = sourceName
        ? `当前数据源：${sourceName}${snapshot.match ? "（与登记时一致）" : "（与登记时不一致，请检查数据文件）"}`
        : "当前数据源：未配置";
    }

    const factData = $("fact-data");
    if (factData) {
      factData.innerHTML = `数据源：<b>${escapeHtml(sourceName || "—")}</b>` +
        ` · ${snapshot.exists ? "文件在" : "文件缺失"} · ${fmtBytes(snapshot.size_bytes)}`;
    }
    const factTemplate = $("fact-template");
    if (factTemplate) {
      const template = health.template || {};
      factTemplate.innerHTML = `报表模板：<b>${escapeHtml(template.path ? String(template.path).split(/[\\/]/).pop() : "—")}</b>` +
        ` · ${template.exists ? "可用" : "缺失"}`;
    }
    const factState = $("fact-state");
    if (factState) {
      factState.innerHTML = `数据记录：<b>${stateInfo.readable ? "可读写" : "不可读写"}</b>` +
        ` · 数据源 ${fmtInt(state.datasets.length || 1)} / 报表任务 ${fmtInt(stateInfo.tasks)}` +
        ` / 执行记录 ${fmtInt(stateInfo.executions)}`;
    }
    const factCode = $("fact-code");
    if (factCode) {
      factCode.innerHTML = `数据校验：<b>${snapshot.match ? "与登记时一致" : "与登记时不一致"}</b>`;
    }

    setText("rp-service", `${text(health.service)} · ${text(health.status)}`);
    setText("rp-state-readable", stateInfo.readable ? "可读写" : "不可读写");
    setText("rp-counts", `${fmtInt(state.datasets.length || 1)} / ${fmtInt(stateInfo.tasks)}` +
      ` / ${fmtInt(stateInfo.executions)}`);
    const rpSnapshot = $("rp-snapshot");
    if (rpSnapshot) {
      rpSnapshot.textContent = snapshot.match ? "通过" : "未通过";
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
      ["服务时间", fmtTime(health.time)],
      ["数据源与登记一致", (health.data_snapshot || {}).match ? "是" : "否"],
      ["报表模板可用", (health.template || {}).exists ? "是" : "否"],
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
        ["数据源数", fmtInt(state.datasets.length || 1)],
        ["报表任务数", fmtInt(info.tasks)],
        ["执行记录数", fmtInt(info.executions)],
        ["上传文件数", fmtInt(info.uploads)],
        ["可读写", info.readable ? "是" : "否"],
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
      : "来自系统状态统计");
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
        setText("trend-caption", "只有 1 次成功执行，画不出走势；数据来源：历史执行记录");
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
    setText("trend-caption", `最近 ${rows.length} 次成功执行 · 数据来源：历史执行记录`);
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
        ` · 销售额 <b>${currencySymbol()}${fmtMoney(latest.amount_display)}</b>`,
      `覆盖 ${fmtInt(latest.rows_in_range)} 行：有效 ${fmtInt(latest.rows_valid)} 行，排除 ${fmtInt(latest.rows_excluded)} 行` +
        `（排除金额 ${fmtMoney(latest.excluded_amount)}）`,
      `数据来源：${latest.data_snapshot_match ? "与登记时一致" : "与登记时不一致（请核对数据文件）"}`,
      `报表文件已生成（${fmtBytes(latest.excel_size_bytes)}）` +
        ` · 内容校验${verification.passed ? "通过" : "未通过"}`,
      `执行耗时 ${text(latest.seconds)} 秒`,
    ];
    items.forEach((html) => {
      const li = document.createElement("li");
      li.innerHTML = html;
      list.appendChild(li);
    });
  }

  // 「销售分析」页现在是**真表格**（客户/产品/销售/原始数据四张表见文件下半部分）：
  // 维度 × 指标全部向后端要，所以这里不再从执行记录里拼指标卡。

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

  // 已上传的文件：只显示业务字段（文件 / 行数 / 列数 / 上传时间）。
  // 内部编号与哈希是审计信息，界面上不出现（文件名的权威来源是上传时的原名）。
  function renderFiles() {
    const body = $("files-table-body");
    if (body) clear(body);

    const byFile = new Map();
    state.tasks.forEach((task) => {
      if (!task.file_id) return;
      const entry = byFile.get(task.file_id)
        || { file_id: task.file_id, filename: task.source_filename, rows: task.rows, columns: task.columns, created_at: task.created_at };
      byFile.set(task.file_id, entry);
    });
    const rows = [...byFile.values()].filter((entry) => matchesSearch(entry.filename));
    rows.forEach((entry) => {
      if (!body) return;
      const row = document.createElement("tr");
      cell(row, text(entry.filename));
      cell(row, typeof entry.rows === "number" ? fmtInt(entry.rows) : "—").className = "num";
      cell(row, typeof entry.columns === "number" ? fmtInt(entry.columns) : "—").className = "num";
      cell(row, fmtTime(entry.created_at));
      body.appendChild(row);
    });
    toggleEmpty("files-empty", rows.length > 0);
    renderTaskFileOptions(rows);
  }

  // 建任务时的"数据文件"下拉：显示文件名（业务标识），值仍是内部的 file_id（不显示给用户）
  function renderTaskFileOptions(rows) {
    const select = $("task-file-id");
    if (!select) return;
    const current = select.value;
    clear(select);
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = rows.length ? "（请选择数据文件）" : "（还没有上传文件）";
    select.appendChild(placeholder);
    rows.forEach((entry) => {
      const option = document.createElement("option");
      option.value = entry.file_id;
      option.textContent = entry.filename || "未命名文件";
      select.appendChild(option);
    });
    if ([...select.options].some((option) => option.value === current)) select.value = current;
    updateCreatePrecondition();
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
      cell(row, fmtInt(doc.chars)).className = "num";
      cell(row, fmtInt(doc.blocks)).className = "num";
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
      ["文件名", payload.filename],
      ["字符数", fmtInt(payload.chars)],
      ["块数", `${fmtInt(payload.blocks)} ${payload.block_unit || ""}`.trim()],
      ["标题", payload.title || "（未识别到标题）"],
      ["文件大小", fmtBytes(payload.size_bytes)],
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
    status("doc-viewer-state", "正在读取全文…", "loading");
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
      status("doc-viewer-state", `已取回 ${text.length} 个字符（每次都从原文件重新读取）。`, "success");
    } catch (err) {
      state.docViewer = null;
      hide($("doc-viewer"));
      status("doc-upload-state", `读取全文失败：${err.message}`, "error");
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
    note.textContent = `${summary.method || ""} · ${summary.note || ""} · 生成于 ${fmtTime(summary.generated_at)}`;
    box.appendChild(note);
    show(box);
  }

  async function loadDocSummary(docId) {
    status("doc-viewer-state", "正在生成摘要…", "loading");
    try {
      const summary = await API.documentSummary(docId);
      renderDocSummary(summary);
      status("doc-viewer-state", `摘要已生成（本次${summary.cached ? "取回已缓存的那份" : "重新抽取"}）。`, "success");
    } catch (err) {
      status("doc-viewer-state", `生成摘要失败：${err.message}`, "error");
    }
  }

  function bindDocuments() {
    const input = $("doc-input");
    const button = $("btn-doc-upload");
    if (!input || !button) return;

    input.addEventListener("change", () => {
      const file = input.files[0];
      status("doc-upload-state", file
        ? `已选择：${file.name}（${fmtBytes(file.size)}）· 点「上传并提取」提交`
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
        status("doc-upload-state", `提取失败：${err.message}`, "error");
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

  // ── 周报中心：报告类问答（历史里 tool.name == sales_report 的那些）────────
  // 页面不重新算一遍报告：列表来自 `state.conversations`（后端摘要），
  // 点「查看」走既有的 openConversation（读完整记录、按同一套分区渲染），
  // 点「下载 Markdown」取回那条完整记录的 `answer.export` 再存盘 —— 三处同源。
  function renderWeeklyReports() {
    const body = $("wk-reports-body");
    if (!body) return;
    clear(body);
    const rows = state.conversations.filter((item) => item.tool === REPORT_TOOL);
    rows.forEach((item) => {
      const row = document.createElement("div");
      row.className = "row chat-history-row";     // 与「历史提问」列表同一个行样式，不另造一套

      const main = document.createElement("div");
      main.className = "row-main";
      main.appendChild(line(item.question || "—", "row-title"));
      main.appendChild(line([
        fmtTime(item.created_at),
        CHAT_STATUS_TEXT[item.status] || item.status,
        typeof item.sales_amount === "number" ? `销售额 ${fmtMoney(item.sales_amount)}` : "",
      ].filter(Boolean).join(" · "), "row-sub"));
      row.appendChild(main);

      const open = document.createElement("button");
      open.className = "btn btn-sm";
      open.type = "button";
      open.textContent = "查看";
      open.addEventListener("click", () => openConversation(item.conversation_id).then(scrollToChat));
      row.appendChild(open);

      const save = document.createElement("button");
      save.className = "btn btn-sm";
      save.type = "button";
      save.textContent = "下载 Markdown";
      save.addEventListener("click", () => downloadConversationReport(item.conversation_id, save));
      row.appendChild(save);

      body.appendChild(row);
    });
    toggleEmpty("wk-reports-empty", rows.length > 0);
  }

  async function downloadConversationReport(conversationId, button) {
    const original = button ? button.textContent : "";
    if (button) { button.disabled = true; button.textContent = "准备中…"; }
    try {
      const record = await API.getConversation(conversationId);
      const doc = ((record || {}).answer || {}).export || null;
      if (!doc) throw new Error("这次问答没有可下载的报告");
      downloadText(doc.filename, doc.markdown, doc.mime);
      setText("wk-reports-hint", `已下载：${doc.filename}`);
    } catch (err) {
      // 拿不到就说拿不到 —— 绝不从列表摘要拼一份"看起来像报告"的东西出来
      setText("wk-reports-hint", `下载失败：${(err && err.message) || err}`);
    } finally {
      if (button) { button.disabled = false; button.textContent = original; }
    }
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
      cell(tr, fmtTime(row.created_at)).className = "num";
      body.appendChild(tr);
    });
    toggleEmpty("exec-empty", rows.length > 0);
  }

  function renderSpec() {
    const task = state.selectedTask;
    const wrap = $("spec-summary");
    const meta = $("spec-meta");
    if (!task) {
      hide(wrap);
      show($("spec-empty"));
      updateRunButtons();
      return;
    }
    show(wrap);
    hide($("spec-empty"));
    // 只画**业务口径**（哪个文件 / 哪段时间 / 算哪些指标），不把内部规格对象铺到界面上
    if (meta) {
      clear(meta);
      const summary = task.spec_summary || {};
      const fields = [
        ["名称", task.name],
        ["状态", task.status === "has_run" ? "执行过" : "已创建"],
        ["数据文件", task.source_filename],
        ["数据源校验", task.data_snapshot_match ? "通过" : "未通过"],
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
      status("run-state", `已读取任务 ${taskId} 的报表配置。`, "success");
      await loadRuns(taskId);
    } catch (err) {
      state.selectedTask = null;
      renderSpec();
      status("run-state", `读取任务失败：${err.message}`, "error");
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
      };
      renderRunResult();
      status("run-state", `已恢复最近一次执行（${fmtTime(latest.created_at)}）。`, "success");
    } catch (err) {
      status("run-state", `读取执行记录失败：${err.message}`, "error");
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
    // 进到表格页才去要数据（四张表都由后端算，没必要在首屏一次全拉）
    ensureTableLoaded(state.route);
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
    degraded: "执行成功（本次未生成推断，只给计算事实）",
    unsupported: "数据不支持这个问题",
    error: "未能回答",
  };

  const SECTION_SOURCE_LABEL = {
    code: "程序生成",
    llm: "模型生成",
  };

  // 报告形态在记录里的 `tool.name`（后端 app/ai/report.py 的 REPORT_TOOL）。
  // 周报中心就是靠它把"报告类问答"从历史里挑出来 —— 不猜、不改写记录。
  const REPORT_TOOL = "sales_report";

  // 「生成周报」卡片上的示例问法（点一下填进输入框，**不自动提交**：用户多半想改区间）
  const REPORT_EXAMPLE = "帮我根据本星期的销售数据做一份销售周报";

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
    // 变化额/贡献率必须**带符号**：+354,517.03 / -1,234.56。
    // 少了符号，读者得自己从上下文猜方向 —— 而方向正是这两行的全部重点。
    if (style === "money_signed") return (value >= 0 ? "+" : "-") + fmtMoney(Math.abs(value));
    if (style === "int") return fmtInt(value);
    if (style === "qty") return fmtInt(Math.round(value));
    if (style === "pct") return value.toFixed(2);
    if (style === "pct_signed") return (value >= 0 ? "+" : "") + value.toFixed(2);
    return Number.isInteger(value) ? fmtInt(value) : String(value);
  }

  // 界面上只说"这次问的是哪类问题"（人话标题），不暴露内部工具名 ——
  // 列表摘要里只有名字，就用后端能力清单把它翻成人话标题。
  function toolLabel(tool) {
    if (!tool) return "未记录问题类型";
    if (typeof tool === "string") {
      const intents = (state.capabilities || {}).intents || [];
      const hit = intents.find((item) => item.name === tool);
      return (hit && hit.title) || "问题类型未记录";
    }
    return tool.title || "问题类型未记录";
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
      `正在提问：「${asked}」—— 正在计算并组织回答，可能要十几秒…`,
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
      setChatState("error", `提交失败：${err.message}`);
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
    // 业务用户只关心"数字可不可信"，不关心模型名与错误码（那些照旧落盘，后台可查）
    setText(
      "chat-llm",
      llm.used ? "数字由程序计算 · 文字由模型整理" : "全部由程序生成（本次未用模型）",
    );

    setText("chat-question", record.question || "—");

    renderChatFacts(record);
    renderChatAnswer(record.answer);
    setText("chat-notice", record.notice || "");

    // 五个环节按"这一环到底有没有内容"决定展开 ——
    // 答不了的问题（数据不支持）不该一进来就摊开四个空块，那看着像有东西其实没有。
    const stepOpen = {
      "chat-step-question": true,
      "chat-step-facts": !!record.facts,
      "chat-step-answer": !!(record.answer && record.answer.text),
    };
    Object.entries(stepOpen).forEach(([id, open]) => {
      const el = $(id);
      if (el) el.open = open;
    });
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
  }

  function renderChatAnswer(payload) {
    const box = $("chat-answer");
    if (!box) return;
    clear(box);
    if (!payload || !payload.sections) {
      box.appendChild(line("本次没有生成回答。"));
      return;
    }
    // 报告形态（TASK-010）：用户要的是**一份报告**，所以正文只出现一次 ——
    // 下面是那份文档，页面上预览的与点「下载 Markdown」拿到的**是同一段文本**
    // （不重新请求、不二次渲染，避免两个版本各说各话）。
    // 【为什么】/【建议行动】照旧按分区渲染；`what` 段的内容就是这份文档，跳过不重复印。
    const exportDoc = payload.export || null;
    if (exportDoc) box.appendChild(reportPanel(exportDoc));
    (payload.sections || []).forEach((section) => {
      if (exportDoc && section.key === "what") return;
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
  }

  // 报告面板：标题 + 文件名 + 预览（原文）+ 下载。内容全部来自后端 `answer.export`
  // （markdown 与 mime 也是后端给的 —— 前端不自己拼报告、不自己拼文件名）。
  function reportPanel(doc) {
    const wrap = document.createElement("div");
    wrap.className = "report";

    const bar = document.createElement("div");
    bar.className = "report-bar";
    const title = document.createElement("b");
    title.textContent = [doc.title || "报告", doc.period_label].filter(Boolean).join(" · ");
    bar.appendChild(title);
    const file = document.createElement("span");
    file.className = "muted-sm";
    file.textContent = doc.filename || "";
    bar.appendChild(file);
    const button = document.createElement("button");
    button.className = "btn btn-primary btn-sm";
    button.type = "button";
    button.textContent = "下载 Markdown";
    button.addEventListener("click", () => downloadText(doc.filename, doc.markdown, doc.mime));
    bar.appendChild(button);
    wrap.appendChild(bar);

    const pre = document.createElement("pre");
    pre.className = "report-doc";
    pre.textContent = doc.markdown || "";
    wrap.appendChild(pre);
    return wrap;
  }

  // 下载：Blob + <a download>（零依赖、不引第三方库，也不新增后端端点 ——
  // 下载的就是上面 <pre> 里那段文本）
  function downloadText(filename, content, mime) {
    const blob = new Blob([content || ""], { type: mime || "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename || "report.md";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    // 回收 blob：点完再放，别在 click() 之前就把 URL 撤了。
    // 用微任务而不是定时器 —— 本次任务一结束就回收，不需要延时；
    // `test_frontend_has_no_fake_shortcuts` 也禁止前端出现任何定时器字样（防"用延时假装在加载"）。
    queueMicrotask(() => URL.revokeObjectURL(url));
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
        typeof item.sales_amount === "number" ? `销售额 ${fmtMoney(item.sales_amount)}` : "",
      ].filter(Boolean).join(" · ");
      main.appendChild(sub);
      row.appendChild(main);

      const button = document.createElement("button");
      button.className = "btn btn-sm";
      button.textContent = "查看";
      button.dataset.conversationId = item.conversation_id;
      row.appendChild(button);
      box.appendChild(row);
    });
  }

  async function openConversation(conversationId) {
    if (!conversationId) return;
    setChatState("loading", "正在读取这次问答的完整记录…");
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

  // 顶栏：Logo / 数据源 / 提问框 / ☰ 菜单 / 用户区。
  // 菜单里的每一条都指向**已经能用的东西**（历史提问、原始数据、导入数据、生成报告、设置）；
  // 通知还没做，就明确写"暂未提供"并保持不可点 —— 不摆一个点了没反应的按钮。
  function bindTopbar() {
    const button = $("btn-menu");
    const menu = $("topbar-menu");
    const closeMenu = () => {
      state.menuOpen = false;
      if (menu) hide(menu);
      if (button) button.setAttribute("aria-expanded", "false");
    };
    if (button && menu) {
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        state.menuOpen = !state.menuOpen;
        if (state.menuOpen) show(menu); else hide(menu);
        button.setAttribute("aria-expanded", state.menuOpen ? "true" : "false");
      });
      // 点菜单外面 / 按 Esc 都要关上（菜单不该赖在屏幕上）
      document.addEventListener("click", () => { if (state.menuOpen) closeMenu(); });
      document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && state.menuOpen) closeMenu();
      });
      menu.addEventListener("click", (event) => event.stopPropagation());
    }
    const goWithMenu = (id, action) => {
      const item = $(id);
      if (!item) return;
      item.addEventListener("click", () => {
        closeMenu();
        action();
      });
    };
    goWithMenu("menu-item-history", () => { goto("overview"); scrollToChatHistory(); });
    goWithMenu("menu-item-raw", () => goto("raw"));
    goWithMenu("menu-item-import", () => goto("data"));
    goWithMenu("menu-item-report", () => {
      goto("overview");
      const input = $("hero-nl-input") || $("nl-input");
      if (input) {
        if (!input.value.trim()) input.value = REPORT_EXAMPLE;
        input.focus();
      }
      scrollToChat();
    });
    goWithMenu("menu-item-settings", () => goto("settings"));
    const notify = $("menu-item-notify");
    if (notify) notify.disabled = true;
    $("avatar").addEventListener("click", () => goto("settings"));
  }

  function scrollToChatHistory() {
    const box = $("chat-history-wrap");
    if (box && box.scrollIntoView) box.scrollIntoView({ block: "start" });
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
        ? `已选择：${file.name}（${fmtBytes(file.size)}）· 点「上传」提交`
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
        status("upload-state", `上传失败：${err.message}`, "error");
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
      status("create-task-state", "正在建任务…", "loading");
      try {
        const created = await API.createTask(payload);
        status("create-task-state", `任务已创建：${created.task_id}`, "success");
        await loadTasks();
        await selectTask(created.task_id);
      } catch (err) {
        status("create-task-state", `建任务失败：${err.message}`, "error");
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
        status("run-state", `刷新任务失败：${err.message}`, "error");
      }
    });

    $("btn-run-task").addEventListener("click", async () => {
      if (!state.selectedTask) return;
      const button = $("btn-run-task");
      button.disabled = true;
      status("run-state", "正在执行（真实计算 + 渲染 xlsx，可能要几分钟）…", "loading");
      try {
        const result = await API.runTask(state.selectedTask.task_id);
        state.lastRun = result;
        renderRunResult();
        status("run-state", `执行成功：${result.execution_id} · 耗时 ${result.seconds} 秒`, "success");
        await loadExecutions();
        await loadTasks();
        renderDerived();
      } catch (err) {
        state.lastRun = null;
        renderRunResult();
        status("run-state", `执行失败：${err.message}`, "error");
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
        status("run-state", "执行记录已刷新。", "success");
      } catch (err) {
        status("run-state", `刷新失败：${err.message}`, "error");
      }
    });
  }

  function renderUploadResult(payload) {
    const body = $("upload-result");
    if (!body) return;
    clear(body);
    [
      ["文件名", payload.filename],
      ["行数", fmtInt(payload.rows)],
      ["列数", fmtInt(payload.columns)],
      ["文件大小", fmtBytes(payload.size_bytes)],
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
    if (!fileId) reasons.push("先选择数据文件");
    if (!start || !end) reasons.push("选开始与结束日期");
    if (start && end && start > end) reasons.push("结束日期不能早于开始日期");
    if (!metrics.length) reasons.push("至少选一个指标");

    const button = $("btn-create-task");
    button.disabled = reasons.length > 0;
    setText("create-precondition", reasons.length
      ? `建任务按钮当前禁用，还缺：${reasons.join(" / ")}。`
      : "前置条件已满足，可以建任务（报表配置会冻结在任务里）。");
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
      ? "（清单已同步）"
      : "（本次未同步到清单，显示的是内置清单）");
    updateCreatePrecondition();
  }

  // ══════════════════════════════════════════════════════════════════════
  // 尚未实现的能力：把"为什么不可用、归哪个 TASK"写进 title，
  // 让禁用状态是**可解释的**，而不是一个不响应的死按钮。
  // ══════════════════════════════════════════════════════════════════════
  const INERT_HINT = "自然语言入口暂不可用";

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
      ["sec-rbac", "用户与权限（RBAC）尚未实现"],
      ["sec-acl", "访问控制白 / 黑名单尚未实现"],
      ["sec-circuit", "熔断机制尚未实现"],
    ];
    switches.forEach(([id, hint]) => {
      const el = $(id);
      if (el) el.title = hint;
    });
    const facts = $("hero-facts");
    if (facts) facts.title = "以上四项为最近一次读取到的真实状态";
  }

  // ══════════════════════════════════════════════════════════════════════
  // 自然语言入口的交互（TASK-004）
  // ══════════════════════════════════════════════════════════════════════
  function renderNlNote() {
    const caps = state.capabilities;
    if (!caps || !caps.intents) return;   // 拿不到能力清单就不改文案（不编一句"支持三类问题"糊上去）

    const names = caps.intents.map((item) => item.title).join(" / ");
    // 报告是**输出形态**（不是第 6 个 intent），所以它不在 intents 里，得单独提示 ——
    // 否则用户永远猜不到"可以做一份周报"。
    const reportNames = ((caps.report || {}).periods || []).map((item) => item.title).join(" / ");
    const allNames = reportNames ? `${names} / ${reportNames}` : names;
    const profile = caps.data_profile || {};
    // 币种**取自后端声明**（数据里没有货币字段，单位不能在前端猜、也不在前端写死）：
    // 后端没声明就一个币种字都不写 —— 宁可不显示，也不编一个默认单位出来。
    const currency = caps.currency || {};
    const currencyText = currency.name
      ? `金额单位：${currency.name}${currency.symbol ? `（${currency.symbol}，${currency.code || ""}）` : ""}`
      : "";

    // 顶栏不再铺这段长文案（它会把「分析」按钮挤成竖排单字）——
    // 能力清单只写在首页这一行，用户看得全，顶栏也不会被遮挡。
    setText(
      "hero-nl-note",
      `本版支持 ${allNames}；数据范围 ${text(profile.first_day)} ~ ${text(profile.last_day)}，`
      + `问数据里没有的维度（如区域）会被明确拒绝。`
      + (currencyText ? ` ${currencyText}。` : ""),
    );
  }

  // 「生成周报」卡片：把示例问法填进本页的输入框并聚焦。
  // 为什么不直接替用户提交：报告区间要用户自己确认（他可能想要别的周），自动提交等于替他拍板。
  function bindReportShortcut() {
    const button = $("btn-ov-report");
    if (!button) return;
    button.addEventListener("click", () => {
      const input = $("hero-nl-input") || $("nl-input");
      if (!input) return;
      if (!input.value.trim()) {
        input.value = REPORT_EXAMPLE;
        input.dispatchEvent(new Event("input", { bubbles: true }));
      }
      input.focus();
    });
  }

  function scrollToChat() {
    const panel = $("chat-panel");
    if (panel && panel.scrollIntoView) panel.scrollIntoView({ block: "start" });
  }

  // 深链提问：`#/overview?ask=<问题>` —— 页面加载完成后自动把问题填进输入框并提问。
  // 为什么值得有这一条：① 一条链接就能把"问好的问题"分享出去；
  // ② 让"真浏览器 + 真 HTTP + 真数据"的取证成为一条命令（Edge 自带
  //    `--headless --virtual-time-budget=… --dump-dom "<url>#/overview?ask=…"`），
  //    不必为此装 Playwright。
  // 问题原文**原样**来自 URL（不做任何改写/补全），与手动输入走同一个 askQuestion。
  function askFromHash() {
    const query = (window.location.hash || "").split("?")[1] || "";
    const question = (new URLSearchParams(query).get("ask") || "").trim();
    if (!question) return false;
    const input = $("hero-nl-input") || $("nl-input");
    if (input) input.value = question;
    askQuestion(question).then(scrollToChat);
    return true;
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
    // TASK-006 补了"客户排行 / 退货分析"两个 chip —— 现有能力要能被点到，
    // 不能只存在于后端接口里。
    ["chip-summary", "chip-trend", "chip-products", "chip-customers", "chip-returns"].forEach((id) => {
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
  // 业务表格（客户 / 产品 / 销售 / 原始数据）
  //
  // 【铁律】分页、排序、筛选、聚合、导出**全部由后端确定性计算**，这里只做三件事：
  //   ① 把用户的操作组装成查询参数；
  //   ② 把后端返回的 columns / items / total 渲染出来；
  //   ③ 点表头 = 改排序键再请求一次 —— **绝不拿本地这几行自己排**。
  //      （前端排序会形成第二套计算路径：将来"接口 = A、页面 = B"，破坏确定性。）
  //
  // 列名、对齐、数字格式也都来自后端（columns），前端不维护第二份表头。
  // ══════════════════════════════════════════════════════════════════════
  const TABLE_PAGE_SIZES = [20, 50, 100, 200];
  // 销售表的维度与指标：**取值由后端定**（这里只是显示名，选错后端会明确拒绝）
  const SALES_DIMENSIONS = [["day", "按日"], ["week", "按周"], ["country", "按国家"]];
  const SALES_METRICS = [["sales_amount", "销售额"], ["order_count", "订单数"],
                         ["customer_count", "客户数"], ["avg_order_amount", "客单价"]];
  const TABLE_SPECS = {
    customers: { table: "customers", rootId: "tbl-customers", noteId: "cust-scope-note",
                 emptyId: "tbl-customers-empty", searchPlaceholder: "客户号", sales: false },
    products: { table: "products", rootId: "tbl-products", noteId: "prod-scope-note",
                emptyId: "tbl-products-empty", searchPlaceholder: "商品编码或名称", sales: false },
    sales: { table: "sales", rootId: "tbl-sales", noteId: "sales-scope-note",
             emptyId: "tbl-sales-empty", searchPlaceholder: "期间", sales: true },
    raw: { table: "raw", rootId: "tbl-raw", noteId: "raw-scope-note", emptyId: "tbl-raw-empty",
           searchPlaceholder: "订单号 / 商品 / 客户 / 国家", sales: false },
  };

  // 单元格显示：**后端给 format**，前端只负责按格式排版（不做任何运算）
  function formatCell(value, format) {
    if (value === null || value === undefined || value === "") return "—";
    if (format === "money") return fmtMoney(value);
    if (format === "int") return fmtInt(value);
    if (format === "qty") return fmtInt(Math.round(value));
    if (format === "pct") return `${Number(value).toFixed(2)}%`;
    if (format === "date") return String(value).slice(0, 10);
    if (format === "datetime") return String(value).replace("T", " ").slice(0, 19);
    return String(value);
  }

  function button(label, className) {
    const el = document.createElement("button");
    el.type = "button";
    el.className = className || "btn btn-sm";
    el.textContent = label;
    return el;
  }

  function selectBox(options, value) {
    const el = document.createElement("select");
    options.forEach(([key, label]) => {
      const option = document.createElement("option");
      option.value = key;
      option.textContent = label;
      el.appendChild(option);
    });
    if (value !== undefined) el.value = value;
    return el;
  }

  function field(label, control) {
    const wrap = document.createElement("label");
    wrap.className = "field-inline";
    const span = document.createElement("span");
    span.className = "field-inline-label";
    span.textContent = label;
    wrap.appendChild(span);
    wrap.appendChild(control);
    return wrap;
  }

  function createTablePanel(spec) {
    const root = $(spec.rootId);
    const panel = {
      spec,
      query: { page: 1, page_size: 50, sort: "", order: "", search: "" },
      payload: null,
      elements: {},
      loaded: false,
    };
    if (!root) return panel;

    // ── 工具条 ────────────────────────────────────────────────────────
    const toolbar = document.createElement("div");
    toolbar.className = "toolbar";

    const startInput = document.createElement("input");
    startInput.type = "date";
    const endInput = document.createElement("input");
    endInput.type = "date";
    const rangeBox = document.createElement("div");
    rangeBox.className = "range";
    rangeBox.appendChild(startInput);
    const tilde = document.createElement("span");
    tilde.className = "muted-sm";
    tilde.textContent = "~";
    rangeBox.appendChild(tilde);
    rangeBox.appendChild(endInput);

    const searchInput = document.createElement("input");
    searchInput.type = "search";
    searchInput.placeholder = spec.searchPlaceholder || "搜索";

    const dimensionSelect = selectBox(SALES_DIMENSIONS, "day");
    const metricSelect = selectBox(SALES_METRICS, "sales_amount");
    const pageSizeSelect = selectBox(
      TABLE_PAGE_SIZES.map((size) => [String(size), `${size} 条/页`]), "50",
    );

    const queryButton = button("查询", "btn btn-sm btn-primary");
    const resetButton = button("重置");
    const exportXlsx = button("导出 Excel");
    const exportCsv = button("导出 CSV");
    const statusLine = document.createElement("span");
    statusLine.className = "muted-sm";
    statusLine.id = `${spec.rootId}-status`;

    toolbar.appendChild(field("时间范围", rangeBox));
    toolbar.appendChild(field("搜索", searchInput));
    if (spec.sales) {
      toolbar.appendChild(field("维度", dimensionSelect));
      toolbar.appendChild(field("指标", metricSelect));
    }
    toolbar.appendChild(queryButton);
    toolbar.appendChild(resetButton);
    const spacer = document.createElement("span");
    spacer.className = "toolbar-spacer";
    toolbar.appendChild(spacer);
    toolbar.appendChild(statusLine);
    toolbar.appendChild(field("每页", pageSizeSelect));
    toolbar.appendChild(exportXlsx);
    toolbar.appendChild(exportCsv);
    root.appendChild(toolbar);

    // ── 表体 ──────────────────────────────────────────────────────────
    const wrap = document.createElement("div");
    wrap.className = "table-wrap";
    const table = document.createElement("table");
    table.className = "table";
    const head = document.createElement("thead");
    const headRow = document.createElement("tr");
    head.appendChild(headRow);
    const body = document.createElement("tbody");
    table.appendChild(head);
    table.appendChild(body);
    wrap.appendChild(table);
    root.appendChild(wrap);

    // 空状态块**用页面上那一个**（HTML 里已经有），不另造一个 ——
    // 页面只有一处空状态定义，测试与走查都盯得住。
    const empty = spec.emptyId ? $(spec.emptyId) : null;

    const pager = document.createElement("div");
    pager.className = "pager";
    const prev = button("上一页");
    const next = button("下一页");
    const pageInfo = document.createElement("span");
    pageInfo.className = "muted-sm";
    const summary = document.createElement("span");
    summary.className = "muted-sm pager-summary";
    pager.appendChild(prev);
    pager.appendChild(pageInfo);
    pager.appendChild(next);
    pager.appendChild(summary);
    root.appendChild(pager);

    const footer = document.createElement("p");
    footer.className = "note";
    footer.id = `${spec.rootId}-note`;
    root.appendChild(footer);

    panel.elements = { startInput, endInput, searchInput, dimensionSelect, metricSelect,
                       pageSizeSelect, statusLine, headRow, body, empty, prev, next,
                       pageInfo, summary, footer };

    // ── 渲染 ──────────────────────────────────────────────────────────
    panel.render = (payload) => {
      panel.payload = payload;
      panel.loaded = true;
      const columns = payload.columns || [];
      const rows = payload.items || [];

      clear(headRow);
      columns.forEach((column) => {
        const th = document.createElement("th");
        th.textContent = column.label || column.key;
        if (column.align === "right") th.className = "num";
        if (column.note) th.title = column.note;
        const sortable = !(payload.table === "raw" && column.key === "row_no");
        if (sortable) {
          th.classList.add("sortable");
          if (payload.sort === column.key) {
            th.classList.add(payload.order === "asc" ? "sorted-asc" : "sorted-desc");
            th.title = `${th.title ? `${th.title} · ` : ""}当前排序：${payload.order === "asc" ? "升序" : "降序"}`;
          }
          // 点表头 → **向后端要一次新排序**（不是拿本地数据排）
          th.addEventListener("click", () => {
            const same = panel.query.sort === column.key;
            panel.query.sort = column.key;
            panel.query.order = same && panel.query.order === "desc" ? "asc" : "desc";
            panel.query.page = 1;
            panel.load();
          });
        }
        headRow.appendChild(th);
      });

      clear(body);
      rows.forEach((item) => {
        const row = document.createElement("tr");
        columns.forEach((column) => {
          const td = cell(row, formatCell(item[column.key], column.format));
          if (column.align === "right") td.className = "num";
        });
        body.appendChild(row);
      });
      if (empty) empty.hidden = rows.length > 0;
      wrap.hidden = rows.length === 0;

      prev.disabled = payload.page <= 1;
      next.disabled = payload.page >= payload.page_count;
      pageInfo.textContent = `第 ${fmtInt(payload.page)} / ${fmtInt(payload.page_count)} 页 · 共 ${fmtInt(payload.total)} 条`;
      const summaryParts = [];
      const totals = payload.summary || {};
      if (typeof totals.count === "number") summaryParts.push(`条件命中 ${fmtInt(totals.count)} 条`);
      if (typeof totals.sales_amount === "number") {
        summaryParts.push(`合计销售额 ${fmtMoney(totals.sales_amount)}${currencySymbol()}`);
      }
      if (typeof totals.metric_total === "number" && totals.metric_label) {
        summaryParts.push(`${totals.metric_label}合计 ${formatCell(totals.metric_total, "money")}`);
      }
      summary.textContent = summaryParts.join(" · ");

      const notes = [];
      if (totals.note) notes.push(totals.note);
      (payload.notes || []).forEach((note) => notes.push(note));
      footer.textContent = notes.join(" ");

      const noteBox = $(spec.noteId);
      if (noteBox) {
        noteBox.textContent = payload.dataset
          ? `${payload.dataset.name} · 更新于 ${fmtTime(payload.dataset.updated_at)}`
          : "";
      }
      statusLine.textContent = "";
    };

    panel.load = async () => {
      const query = panel.query;
      const params = {
        page: query.page,
        page_size: query.page_size,
        search: query.search,
        start: panel.elements.startInput.value,
        end: panel.elements.endInput.value,
      };
      if (query.sort) {
        params.sort = query.sort;
        params.order = query.order;
      }
      if (spec.sales) {
        params.dimension = panel.elements.dimensionSelect.value;
        params.metric = panel.elements.metricSelect.value;
      }
      statusLine.textContent = "正在读取…";
      try {
        const payload = await API.readTable(spec.table, params);
        panel.query.page = payload.page;
        panel.query.page_size = payload.page_size;
        panel.query.sort = payload.sort;
        panel.query.order = payload.order;
        if (Number(panel.elements.pageSizeSelect.value) !== payload.page_size) {
          panel.elements.pageSizeSelect.value = String(payload.page_size);
        }
        panel.render(payload);
      } catch (err) {
        statusLine.textContent = "";
        banner("error", `读取业务表格失败：${escapeHtml(err.message)}`);
      }
    };

    panel.exportTo = async (format) => {
      const params = {
        search: panel.query.search,
        start: panel.elements.startInput.value,
        end: panel.elements.endInput.value,
      };
      if (panel.query.sort) {
        params.sort = panel.query.sort;
        params.order = panel.query.order;
      }
      if (spec.sales) {
        params.dimension = panel.elements.dimensionSelect.value;
        params.metric = panel.elements.metricSelect.value;
      }
      statusLine.textContent = "正在按当前条件生成导出文件…";
      try {
        const result = await API.exportTable(spec.table, params, format);
        downloadBlob(result.filename, result.blob);
        statusLine.textContent = "已按当前筛选与排序导出完整结果。";
      } catch (err) {
        statusLine.textContent = "";
        // 后端拒绝导出时（如超过单次上限）把**它的原话**显示出来 —— 那是给用户看的
        banner("error", `导出失败：${escapeHtml(err.message)}`);
      }
    };

    // ── 事件 ──────────────────────────────────────────────────────────
    queryButton.addEventListener("click", () => {
      panel.query.search = searchInput.value.trim();
      panel.query.page = 1;
      panel.load();
    });
    searchInput.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      queryButton.click();
    });
    resetButton.addEventListener("click", () => {
      startInput.value = "";
      endInput.value = "";
      searchInput.value = "";
      panel.query = { page: 1, page_size: panel.query.page_size, sort: "", order: "", search: "" };
      if (spec.sales) {
        dimensionSelect.value = "day";
        metricSelect.value = "sales_amount";
      }
      panel.load();
    });
    pageSizeSelect.addEventListener("change", () => {
      panel.query.page_size = Number(pageSizeSelect.value);
      panel.query.page = 1;
      panel.load();
    });
    prev.addEventListener("click", () => {
      if (panel.query.page > 1) {
        panel.query.page -= 1;
        panel.load();
      }
    });
    next.addEventListener("click", () => {
      panel.query.page += 1;
      panel.load();
    });
    if (spec.sales) {
      // 换维度 / 指标 → 重新向后端要数据（行内容与排序规则都会变）
      dimensionSelect.addEventListener("change", () => {
        panel.query.sort = "";
        panel.query.order = "";
        panel.query.page = 1;
        panel.load();
      });
      metricSelect.addEventListener("change", () => {
        panel.query.sort = "";
        panel.query.order = "";
        panel.query.page = 1;
        panel.load();
      });
    }
    exportXlsx.addEventListener("click", () => panel.exportTo("xlsx"));
    exportCsv.addEventListener("click", () => panel.exportTo("csv"));

    return panel;
  }

  function downloadBlob(filename, blob) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename || "export";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    queueMicrotask(() => URL.revokeObjectURL(url));
  }

  function bindTablePanels() {
    Object.values(TABLE_SPECS).forEach((spec) => {
      state.tablePanels[spec.table] = createTablePanel(spec);
    });
  }

  // 进入页面时按需加载（加载过就不再重复请求；想刷新有「查询」按钮）
  function ensureTableLoaded(route) {
    const spec = TABLE_SPECS[route];
    if (!spec) return;
    const panel = state.tablePanels[spec.table];
    if (!panel || panel.loaded) return;
    panel.load().catch(() => {});
  }

  // ══════════════════════════════════════════════════════════════════════
  // 首页「客户 TOP5 / 产品排行 TOP5」：**真数据**，不是一句"去别处看"
  //
  // 取数与业务表**同一个后端查询**（page_size=5 + 后端排序）—— 前端只渲染，
  // 不拿全量数据自己排（那会形成第二套计算路径）。
  // ══════════════════════════════════════════════════════════════════════
  const OVERVIEW_TOPS = [
    { table: "customers", rootId: "ov-customers", emptyId: "ov-customers-empty",
      keys: ["customer_id", "sales_amount", "order_count"] },
    { table: "products", rootId: "ov-products", emptyId: "ov-products-empty",
      keys: ["stock_code", "description", "sales_amount"] },
  ];

  function renderMiniTable(rootId, payload, keys) {
    const root = $(rootId);
    if (!root) return 0;
    clear(root);
    const columns = (payload.columns || []).filter((column) => keys.indexOf(column.key) >= 0);
    const rows = payload.items || [];
    if (!rows.length) return 0;
    const table = document.createElement("table");
    table.className = "table";
    const head = document.createElement("thead");
    const headRow = document.createElement("tr");
    columns.forEach((column) => {
      const th = document.createElement("th");
      th.textContent = column.label || column.key;
      if (column.align === "right") th.className = "num";
      headRow.appendChild(th);
    });
    head.appendChild(headRow);
    table.appendChild(head);
    const body = document.createElement("tbody");
    rows.forEach((item) => {
      const row = document.createElement("tr");
      columns.forEach((column) => {
        const td = cell(row, formatCell(item[column.key], column.format));
        if (column.align === "right") td.className = "num";
      });
      body.appendChild(row);
    });
    table.appendChild(body);
    const wrap = document.createElement("div");
    wrap.className = "table-wrap";
    wrap.appendChild(table);
    root.appendChild(wrap);
    return rows.length;
  }

  async function renderOverviewTops() {
    for (const spec of OVERVIEW_TOPS) {
      let payload = null;
      try {
        payload = await API.readTable(spec.table, {
          page: 1, page_size: 5, sort: "sales_amount", order: "desc",
        });
      } catch (err) {
        payload = null;
      }
      const shown = payload ? renderMiniTable(spec.rootId, payload, spec.keys) : 0;
      const box = $(spec.rootId);
      if (box) box.hidden = shown === 0;
      toggleEmpty(spec.emptyId, shown > 0);
    }
  }

  // ══════════════════════════════════════════════════════════════════════
  // 数据源列表 + 导入向导（数据管理页）
  // ══════════════════════════════════════════════════════════════════════
  async function loadDatasets() {
    const payload = await API.listDatasets({ limit: 100 });
    state.datasets = payload.datasets || [];
    renderDatasets();
  }

  function renderDatasets() {
    const body = $("datasets-body");
    if (body) clear(body);
    state.datasets.forEach((dataset) => {
      if (!body) return;
      const row = document.createElement("tr");
      cell(row, text(dataset.name));
      cell(row, fmtInt(dataset.row_count)).className = "num";
      cell(row, fmtInt(dataset.column_count)).className = "num";
      const range = dataset.date_range || {};
      cell(row, range.start && range.end ? `${range.start} ~ ${range.end}` : "—");
      cell(row, fmtTime(dataset.updated_at));
      const statusCell = document.createElement("td");
      const badge = document.createElement("span");
      badge.className = `badge ${dataset.analysis_enabled ? "success" : "warn"}`;
      badge.textContent = dataset.status_label || "—";
      statusCell.appendChild(badge);
      row.appendChild(statusCell);
      body.appendChild(row);
    });
    toggleEmpty("datasets-empty", state.datasets.length > 0);
    const note = $("datasets-note");
    if (note) {
      const analyzable = state.datasets.filter((item) => item.analysis_enabled);
      const pending = state.datasets.filter((item) => !item.analysis_enabled);
      const parts = [];
      if (analyzable.length) {
        parts.push(`业务表格当前基于「${analyzable[0].name}」计算。`);
      }
      if (pending.length) {
        parts.push(`另有 ${pending.length} 个已登记的数据源：分析能力尚未开通，`
          + "系统不会拿它们的数字冒充结果。");
      }
      note.textContent = parts.join(" ");
    }
  }

  async function inspectDatasetFile() {
    const input = $("ds-file-input");
    if (!input || !input.files || !input.files.length) {
      status("ds-import-state", "请先选择一个 .xlsx / .xlsm / .csv 文件。", "error");
      return;
    }
    status("ds-import-state", "正在读取文件（只读前若干行，不把整表塞进浏览器）…", "loading");
    hide($("ds-import-result"));
    try {
      state.inspect = await API.inspectDataset(input.files[0]);
      renderInspect(state.inspect);
      status("ds-import-state", "文件已检查完成，请确认工作表与字段映射后导入。", "success");
    } catch (err) {
      state.inspect = null;
      hide($("ds-inspect"));
      status("ds-import-state", err.message, "error");
    }
  }

  function renderInspect(inspect) {
    const box = $("ds-inspect");
    if (!box) return;
    show(box);
    const meta = $("ds-inspect-meta");
    if (meta) {
      meta.textContent = `${inspect.filename} · ${fmtInt(inspect.rows)} 行 × `
        + `${fmtInt(inspect.column_count)} 列 · ${inspect.read_note || ""}`;
    }
    const sheetField = $("ds-sheet-field");
    const sheetSelect = $("ds-sheet");
    if (sheetSelect) {
      clear(sheetSelect);
      (inspect.sheets || []).forEach((name) => {
        const option = document.createElement("option");
        option.value = name;
        option.textContent = name;
        sheetSelect.appendChild(option);
      });
      if (inspect.sheet) sheetSelect.value = inspect.sheet;
    }
    if (sheetField) sheetField.hidden = !(inspect.sheets || []).length;

    const nameInput = $("ds-name");
    if (nameInput && !nameInput.value) {
      nameInput.value = String(inspect.filename || "").replace(/\.[^.]+$/, "");
    }

    const count = $("ds-preview-count");
    if (count) count.textContent = String((inspect.preview || []).length);
    const table = $("ds-preview-table");
    if (table) {
      clear(table);
      const head = document.createElement("thead");
      const headRow = document.createElement("tr");
      (inspect.columns || []).forEach((column) => {
        const th = document.createElement("th");
        th.textContent = column;
        headRow.appendChild(th);
      });
      head.appendChild(headRow);
      table.appendChild(head);
      const body = document.createElement("tbody");
      (inspect.preview || []).forEach((row) => {
        const tr = document.createElement("tr");
        (inspect.columns || []).forEach((column) => {
          cell(tr, row[column] === null || row[column] === undefined ? "—" : String(row[column]));
        });
        body.appendChild(tr);
      });
      table.appendChild(body);
    }

    const mapping = $("ds-mapping");
    if (mapping) {
      clear(mapping);
      (inspect.required_fields || []).forEach((fieldSpec) => {
        const wrap = document.createElement("label");
        wrap.className = "field-inline";
        const label = document.createElement("span");
        label.className = "field-inline-label";
        label.textContent = fieldSpec.label || fieldSpec.key;
        wrap.appendChild(label);
        const options = [["", "（不导入这一列）"]]
          .concat((inspect.columns || []).map((name) => [name, name]));
        const select = selectBox(options, fieldSpec.mapped_to || "");
        select.dataset.targetField = fieldSpec.key;
        wrap.appendChild(select);
        mapping.appendChild(wrap);
      });
    }
    const note = $("ds-import-note");
    if (note) {
      note.textContent = inspect.mapping_complete
        ? "字段映射已齐；确认后会把文件登记为新的数据源。"
        : `还缺 ${inspect.missing_fields.length} 个必需字段（${inspect.missing_fields.join("、")}）`
          + "—— 仍可登记，但缺字段的数据源不能参与分析。";
    }
  }

  async function importDataset() {
    const inspect = state.inspect;
    if (!inspect) {
      status("ds-import-state", "请先「上传并检查」一个文件。", "error");
      return;
    }
    const fieldMap = {};
    const mapping = $("ds-mapping");
    if (mapping) {
      mapping.querySelectorAll("select[data-target-field]").forEach((select) => {
        if (select.value) fieldMap[select.dataset.targetField] = select.value;
      });
    }
    const payload = {
      upload_id: inspect.upload_id,
      name: ($("ds-name") || {}).value || "",
      sheet: ($("ds-sheet") || {}).value || "",
      field_map: fieldMap,
    };
    status("ds-import-state", "正在登记数据源…", "loading");
    try {
      state.imported = await API.importDataset(payload);
      renderImportResult(state.imported);
      await loadDatasets();
      status("ds-import-state", "已登记为数据源。", "success");
    } catch (err) {
      state.imported = null;
      hide($("ds-import-result"));
      status("ds-import-state", err.message, "error");
    }
  }

  function renderImportResult(dataset) {
    const box = $("ds-import-result");
    if (!box) return;
    clear(box);
    show(box);
    const title = document.createElement("div");
    title.className = "import-title";
    title.textContent = `已登记：${dataset.name}`;
    box.appendChild(title);

    const rows = [
      ["行数", fmtInt(dataset.row_count)],
      ["列数", fmtInt(dataset.column_count)],
      ["数据范围", dataset.date_range ? `${dataset.date_range.start} ~ ${dataset.date_range.end}` : "—"],
      ["导入时间", fmtTime(dataset.imported_at)],
      ["字段映射", dataset.mapping_complete ? "完整" : `缺 ${(dataset.missing_fields || []).length} 个必需字段`],
      ["状态", dataset.status_label || "—"],
    ];
    const table = document.createElement("table");
    table.className = "table";
    const body = document.createElement("tbody");
    rows.forEach(([key, value]) => {
      const tr = document.createElement("tr");
      cell(tr, key);
      cell(tr, text(value), "num");
      body.appendChild(tr);
    });
    table.appendChild(body);
    const wrap = document.createElement("div");
    wrap.className = "table-wrap";
    wrap.appendChild(table);
    box.appendChild(wrap);

    const note = document.createElement("p");
    note.className = "note";
    note.textContent = dataset.analysis_note
      || "该数据源已登记；分析能力尚未开通，系统不会用它的数据算任何数字。";
    box.appendChild(note);
  }

  function bindDatasetImport() {
    const inspectButton = $("btn-ds-inspect");
    if (inspectButton) inspectButton.addEventListener("click", () => inspectDatasetFile());
    const importButton = $("btn-ds-import");
    if (importButton) importButton.addEventListener("click", () => importDataset());
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
    bindReportShortcut();
    bindTablePanels();
    bindDatasetImport();
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
    renderDatasets();
    await renderOverviewTops();
    ensureTableLoaded(state.route);
    // 深链提问放在最后：能力清单与页面骨架都已就绪，自动提问走的是**和手动点击
    // 完全同一条**链路（同样的 askQuestion、同样的渲染）。
    askFromHash();
  }

  document.addEventListener("DOMContentLoaded", () => {
    init().catch((err) => {
      errorBanner(err);
      status("upload-state", `初始化失败：${err.message}`, "error");
    });
  });
})();
