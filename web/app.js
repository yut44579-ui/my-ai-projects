/* app.js · 页面逻辑（TASK-004A，原生 JS，无框架）
 *
 * 五区块的真实数据流（每一步都用**上一步的 HTTP 响应**里的值，前端不造数）：
 *   ① 数据        选文件 → POST /api/upload                → state.file  = 响应
 *   ② 建任务      POST /api/tasks {file_id: state.file.file_id, …}
 *                 → state.task = 响应（含 task_id + **后端冻结的完整 Spec**）
 *   ③ Report Spec 直接渲染 state.task.spec（或 GET /api/tasks/{id} 重读的 spec）
 *   ④ 执行结果    POST /api/tasks/{state.task.task_id}/run   → state.run = 响应（含 download_url）
 *   ⑤ 执行记录    GET  /api/tasks/{state.task.task_id}/runs  → 后端拉取
 *
 * "刷新页面后仍读得到" 是**从后端重读**实现的（不是 localStorage）：
 *   页面加载 → GET /api/tasks → 取 URL hash 里的 task_id（没有就取最新一条）
 *   → GET /api/tasks/{id}（拿回 Spec）+ GET …/runs（拿回执行记录，④也据此恢复到"上次结果"）。
 *
 * 空状态 / 禁用规则（AC）：没上传 → 建任务灰；没任务 → 执行灰；没成功的执行 → 下载灰；
 * 任何请求进行中 → 相关按钮全灰（防重复提交）。
 */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const state = {
    metrics: { names: [], source: "" },   // 指标目录（来自后端 /openapi.json）
    file: null,        // ① POST /api/upload 的响应
    task: null,        // ②③ 任务详情（含 spec）—— 后端返回
    run: null,         // ④ run 响应 或 执行记录里最新的一条
    runLabel: "",      // ④ 的数据来源说明
    runs: [],          // ⑤ 当前任务的执行记录
    tasks: [],         // ② 任务列表
    busy: false,       // 是否有请求在飞（true 时按钮全灰）
  };

  // ════════════════════════════════════════════════════════════════════
  // 渲染小工具
  // ════════════════════════════════════════════════════════════════════
  const text = (node, value) => { node.textContent = value === null || value === undefined ? "" : String(value); };

  function status(node, message, kind) {
    node.className = `status status--${kind || "muted"}`;
    node.textContent = message;
  }

  function kvTable(node, pairs) {
    node.innerHTML = "";
    pairs.forEach(([label, value]) => {
      if (value === undefined) return;
      const tr = document.createElement("tr");
      const th = document.createElement("th");
      const td = document.createElement("td");
      text(th, label);
      text(td, value === null ? "—" : value);
      if (value === null) td.className = "is-null";
      tr.append(th, td);
      node.append(tr);
    });
  }

  function grid(node, header, rows, emptyText) {
    node.innerHTML = "";
    if (!rows.length) {
      const caption = document.createElement("caption");
      caption.className = "grid__empty";
      text(caption, emptyText);
      node.append(caption);
      return;
    }
    const thead = document.createElement("thead");
    const headRow = document.createElement("tr");
    header.forEach((label) => {
      const th = document.createElement("th");
      text(th, label);
      headRow.append(th);
    });
    thead.append(headRow);

    const tbody = document.createElement("tbody");
    rows.forEach((cells) => {
      const tr = document.createElement("tr");
      cells.forEach((cell) => {
        const td = document.createElement("td");
        if (cell instanceof Node) {
          td.append(cell);
        } else {
          text(td, cell === null || cell === undefined ? "—" : cell);
        }
        tr.append(td);
      });
      tbody.append(tr);
    });
    node.append(thead, tbody);
  }

  const errorText = (err) => (err && err.code ? `[${err.code}] ${err.message}` : (err && err.message) || String(err));

  const shortHash = (value) => (value ? `${value.slice(0, 12)}…` : "—");

  function downloadLink(url, label) {
    if (!url) {
      const span = document.createElement("span");
      span.className = "is-null";
      span.textContent = "无产出（失败不产出文件）";
      return span;
    }
    const a = document.createElement("a");
    a.href = API.downloadUrl(url);
    a.target = "_blank";
    a.rel = "noreferrer";
    text(a, label || "下载");
    return a;
  }

  // ════════════════════════════════════════════════════════════════════
  // 统一渲染（**按钮禁用状态只在这里算**，避免各处各写一套）
  // ════════════════════════════════════════════════════════════════════
  function render() {
    const busy = state.busy;
    $("upload-btn").disabled = busy || !selectedFile();
    $("create-task-btn").disabled = busy || !state.file;
    $("run-btn").disabled = busy || !state.task;
    $("history-refresh-btn").disabled = busy;
    $("reload-spec-btn").disabled = busy || !state.task;

    const link = $("download-link");
    const ready = !busy && state.run && state.run.status === "success" && state.run.download_url;
    if (ready) {
      link.href = API.downloadUrl(state.run.download_url);
      link.setAttribute("aria-disabled", "false");
      link.classList.remove("is-disabled");
      text(link, `下载报表 xlsx（${state.run.download_url}）`);
    } else {
      link.removeAttribute("href");
      link.setAttribute("aria-disabled", "true");
      link.classList.add("is-disabled");
      text(link, "下载报表 xlsx");
    }
  }

  const selectedFile = () => ($("file-input").files || [])[0] || null;

  // ════════════════════════════════════════════════════════════════════
  // ① 数据：真上传
  // ════════════════════════════════════════════════════════════════════
  function renderData() {
    const info = state.file;
    if (!info) {
      kvTable($("data-info"), []);
      $("data-columns-wrap").hidden = true;
      return;
    }
    kvTable($("data-info"), [
      ["file_id（后端返回）", info.file_id],
      ["文件名", info.filename],
      ["行数", Number(info.rows).toLocaleString("zh-CN")],
      ["列数", info.columns],
      ["大小（字节）", Number(info.size_bytes).toLocaleString("zh-CN")],
      ["SHA256", info.sha256],
      ["上传时间", info.created_at],
      ["落盘路径（后端记录）", info.stored_path],
    ]);
    $("data-columns-wrap").hidden = false;
    text($("data-columns-count"), info.columns);
    const box = $("data-columns");
    box.innerHTML = "";
    (info.column_names || []).forEach((name) => {
      const chip = document.createElement("span");
      chip.className = "chip";
      text(chip, name);
      box.append(chip);
    });
  }

  async function doUpload() {
    const file = selectedFile();
    if (!file) return;
    state.busy = true;
    state.file = null;          // 新上传没回来之前，②③④⑤ 一律作废（不许拿旧 file_id 建任务）
    state.task = null;
    state.run = null;
    state.runs = [];
    renderData();
    status($("data-status"), `正在上传 ${file.name}（${Number(file.size).toLocaleString("zh-CN")} 字节）……大文件后端要读一会儿，页面不会假装成功`, "busy");
    render();
    try {
      const info = await API.upload(file);
      state.file = info;
      status($("data-status"), `上传成功：后端返回 file_id = ${info.file_id}`, "ok");
      renderData();
      render();
    } catch (err) {
      status($("data-status"), `上传失败：${errorText(err)}`, "error");
      renderData();
      render();
    } finally {
      state.busy = false;
      render();
    }
  }

  // ════════════════════════════════════════════════════════════════════
  // ② 建任务：真调 POST /api/tasks
  // ════════════════════════════════════════════════════════════════════
  function checkedMetrics() {
    return Array.from(document.querySelectorAll("#task-metrics input[type=checkbox]"))
      .filter((box) => box.checked)
      .map((box) => box.value);
  }

  async function doCreateTask(event) {
    event.preventDefault();
    if (!state.file) return;
    const payload = {
      file_id: state.file.file_id,          // ← ① 的响应
      start: $("task-start").value,
      end: $("task-end").value,
      metrics: checkedMetrics(),
    };
    const name = $("task-name").value.trim();
    if (name) payload.name = name;

    state.busy = true;
    state.task = null;
    state.run = null;
    state.runs = [];
    renderSpec();
    renderRun();
    renderHistory();
    status($("task-status"), "正在把口径固化成任务……", "busy");
    render();
    try {
      const detail = await API.createTask(payload);
      state.task = detail;
      const warnings = (detail.warnings || []).map((line) => `  ⚠️ ${line}`).join("\n");
      status(
        $("task-status"),
        `创建成功：task_id = ${detail.task_id}（status=${detail.status}，spec_id=${detail.spec_id}）${warnings ? `\n${warnings}` : ""}`,
        detail.warnings && detail.warnings.length ? "warn" : "ok",
      );
      window.location.hash = detail.task_id;   // 刷新页面后据此重读（URL 里的真 task_id）
      renderTaskInfo();
      renderSpec();
      renderRun();
      await Promise.all([loadRuns(), loadTaskList()]);
    } catch (err) {
      status($("task-status"), `建任务失败：${errorText(err)}`, "error");
    } finally {
      state.busy = false;
      render();
    }
  }

  function renderTaskInfo() {
    const task = state.task;
    if (!task) {
      kvTable($("task-info"), []);
      return;
    }
    kvTable($("task-info"), [
      ["task_id（后端返回）", task.task_id],
      ["任务名", task.name],
      ["状态", task.status],
      ["绑定的 file_id", task.file_id],
      ["源文件名", task.source_filename],
      ["数据 SHA256", task.data_sha256],
      ["与冻结快照同源", task.data_snapshot_match ? "是（执行可出数）" : "否（执行会被拒绝并记录失败）"],
      ["spec_id / version", `${task.spec_id} / v${task.spec_version}`],
      ["创建时间", task.created_at],
      ["更新时间", task.updated_at],
      ["执行次数", task.run_count],
      ["最近一次执行", task.last_run_at ? `${task.last_run_at}（${task.last_run_status}）` : "从未执行"],
    ]);
  }

  async function loadTaskList() {
    try {
      const payload = await API.listTasks({ limit: 20 });
      state.tasks = payload.tasks || [];
      grid(
        $("task-list"),
        ["task_id", "任务名", "状态", "区间", "指标", "执行次数", "最近执行", "创建时间", "操作"],
        state.tasks.map((task) => {
          const pick = document.createElement("button");
          pick.type = "button";
          pick.textContent = "选中";
          pick.addEventListener("click", () => selectTask(task.task_id));
          const summary = task.spec_summary || {};
          return [
            task.task_id,
            task.name,
            task.status,
            `${summary.start || "?"} ~ ${summary.end || "?"}`,
            (summary.metrics || []).join(", "),
            task.run_count,
            task.last_run_at ? `${task.last_run_at}（${task.last_run_status}）` : "—",
            task.created_at,
            pick,
          ];
        }),
        "还没有任务（先在 ② 里建一个）",
      );
      status($("task-list-status"), `后端共 ${payload.total} 个任务，本页 ${payload.count} 个`, "muted");
    } catch (err) {
      status($("task-list-status"), `读取任务列表失败：${errorText(err)}`, "error");
    }
  }

  // ════════════════════════════════════════════════════════════════════
  // ③ Report Spec：只渲染后端给的 spec
  // ════════════════════════════════════════════════════════════════════
  function renderSpec() {
    const task = state.task;
    const spec = task && task.spec ? task.spec : null;
    if (!spec) {
      $("spec-json").textContent = "";
      kvTable($("spec-info"), []);
      grid($("spec-metrics"), [], [], "");
      status($("spec-status"), "尚未选择任务 —— 建任务（或从任务列表选中）后，Spec 由后端返回", "muted");
      return;
    }
    const range = spec.time_range || {};
    const output = spec.output || {};
    const source = spec.data_source || {};
    status($("spec-status"), `以下 Spec 原样来自后端响应（task_id=${task.task_id}，spec_id=${spec.spec_id}，version=${spec.version}）`, "ok");
    kvTable($("spec-info"), [
      ["spec_id", spec.spec_id],
      ["name", spec.name],
      ["version", spec.version],
      ["data_source.kind", source.kind],
      ["data_source.path", source.path || source.conn],
      ["data_source.sheet", source.sheet],
      ["time_range.mode", range.mode],
      ["time_range.start ~ end", `${range.start} ~ ${range.end}`],
      ["time_range.tz", range.tz],
      ["time_range.time_field", range.time_field],
      ["time_range.semantics", range.semantics],
      ["output.template", output.template],
      ["output.sheet", output.sheet],
      ["created_at", spec.created_at],
    ]);
    const cells = output.cells || {};
    grid(
      $("spec-metrics"),
      ["指标名", "算子 op", "字段 field", "单元格", "排除口径 exclude"],
      (spec.metrics || []).map((metric) => {
        const dsl = metric.dsl || {};
        return [
          metric.name,
          dsl.op,
          dsl.field || dsl.of_metric || "—",
          cells[metric.name] || "—",
          (dsl.exclude || []).join(", ") || "—",
        ];
      }),
      "该 Spec 没有指标（不该发生：后端会拒绝无指标的 Spec）",
    );
    $("spec-json").textContent = JSON.stringify(spec, null, 2);
  }

  // ════════════════════════════════════════════════════════════════════
  // ④ 执行结果
  // ════════════════════════════════════════════════════════════════════
  function renderRun() {
    const run = state.run;
    if (!run) {
      kvTable($("run-info"), []);
      status($("run-status"), "尚未执行 —— 点 ④ 的「执行这个任务」才会有结果", "muted");
      return;
    }
    const verification = run.verification || {};
    const checks = verification.checks
      ? Object.entries(verification.checks).map(([key, ok]) => `${key}=${ok ? "✓" : "✗"}`).join("，")
      : "";
    kvTable($("run-info"), [
      ["数据来源", state.runLabel],
      ["执行状态", run.status],
      ["execution_id（后端返回）", run.execution_id || run.run_id],
      ["task_id", run.task_id],
      ["金额 amount", run.amount],
      ["金额全精度 amount_full", run.amount_full],
      ["区间行数 rows_in_range", run.rows_in_range !== null && run.rows_in_range !== undefined ? Number(run.rows_in_range).toLocaleString("zh-CN") : "—"],
      ["有效行 rows_valid", run.rows_valid === null || run.rows_valid === undefined ? "—" : Number(run.rows_valid).toLocaleString("zh-CN")],
      ["排除行 rows_excluded", run.rows_excluded === null || run.rows_excluded === undefined ? "—" : Number(run.rows_excluded).toLocaleString("zh-CN")],
      ["实际计算区间 resolved_range", run.resolved_range ? `${run.resolved_range.start} ~ ${run.resolved_range.end}` : "—"],
      ["产出相对路径", run.excel_rel_path],
      ["产出大小（字节）", run.excel_size_bytes],
      ["download_url（后端返回）", run.download_url],
      ["读回自检", checks || (run.verification ? JSON.stringify(run.verification) : "—")],
      ["代码版本", run.code_version],
      ["耗时（秒）", run.seconds],
      ["错误", run.error],
    ]);
    if (run.status === "success") {
      status($("run-status"), `执行成功：execution_id = ${run.execution_id || run.run_id}，金额 ${run.amount}`, "ok");
    } else {
      status($("run-status"), `执行失败（后端已落一条 failed 记录，不产出文件）：${run.error || "无错误信息"}`, "error");
    }
  }

  async function doRun() {
    if (!state.task) return;
    state.busy = true;
    state.run = null;                 // 先清空 → 下载按钮立刻灰回去（不许拿上次的文件冒充这次）
    state.runLabel = "";
    renderRun();
    status($("run-status"), "正在执行（后端真算数据 + 用 openpyxl 渲染，可能要几十秒）……", "busy");
    render();
    try {
      const result = await API.runTask(state.task.task_id);
      state.run = result;
      state.runLabel = `POST /api/tasks/${state.task.task_id}/run 的响应`;
      renderRun();
    } catch (err) {
      // 失败也要让用户看到失败（并且 ⑤ 里会出现后端落的 failed 记录）
      state.run = { status: "failed", task_id: state.task.task_id, error: errorText(err), download_url: null };
      state.runLabel = "后端拒绝执行（响应里的统一错误体）";
      renderRun();
    } finally {
      state.busy = false;
      render();
      await refreshTaskDetail();       // 任务状态 created → has_run，从后端重读
      await loadRuns();
      await loadTaskList();
    }
  }

  // ════════════════════════════════════════════════════════════════════
  // ⑤ 执行记录
  // ════════════════════════════════════════════════════════════════════
  function renderHistory() {
    const rows = state.runs.map((run) => [
      run.created_at,
      run.execution_id || run.run_id,
      run.task_id,
      run.status,
      run.amount === null || run.amount === undefined ? "—" : run.amount,
      run.rows_in_range === null || run.rows_in_range === undefined ? "—" : Number(run.rows_in_range).toLocaleString("zh-CN"),
      run.error || "",
      downloadLink(run.download_url, "下载"),
    ]);
    grid(
      $("history-table"),
      ["时间", "execution_id", "task_id", "状态", "金额", "区间行数", "错误", "产出"],
      rows,
      state.task ? `任务 ${state.task.task_id} 还没有执行记录（点 ④ 的"执行这个任务"）` : "选中一个任务后显示它的执行记录",
    );
  }

  async function loadRuns() {
    if (!state.task) {
      state.runs = [];
      renderHistory();
      status($("history-status"), "尚未选择任务", "muted");
      return;
    }
    try {
      const payload = await API.taskRuns(state.task.task_id, { limit: 20 });
      state.runs = payload.runs || [];
      renderHistory();
      status($("history-status"), `GET ${payload.task_id ? `/api/tasks/${payload.task_id}/runs` : ""} → 共 ${payload.total} 条（成功与失败都在）`, "ok");
    } catch (err) {
      state.runs = [];
      renderHistory();
      status($("history-status"), `读取执行记录失败：${errorText(err)}`, "error");
    }
  }

  async function loadAllExecutions() {
    try {
      const payload = await API.listExecutions({ limit: 50 });
      state.runs = (payload.executions || []).map((record) => ({
        execution_id: record.execution_id,
        task_id: record.task_id || "（无任务，ad-hoc）",
        status: record.status,
        created_at: record.created_at,
        amount: record.amount_display !== undefined && record.amount_display !== null ? record.amount_display : record.amount,
        rows_in_range: record.rows_in_range,
        error: record.error,
        // 落盘记录里没有 download_url（002C 的存储形状）→ 由接口层按冻结路由拼（见 api.js）
        download_url: record.status === "success" && record.excel_rel_path
          ? API.downloadUrlForExecution(record.execution_id)
          : null,
      }));
      renderHistory();
      status($("history-status"), `GET /api/executions → 共 ${payload.total} 条（全部任务）`, "ok");
    } catch (err) {
      state.runs = [];
      renderHistory();
      status($("history-status"), `读取全局执行记录失败：${errorText(err)}`, "error");
    }
  }

  async function loadHistory() {
    if ($("history-all").checked) await loadAllExecutions();
    else await loadRuns();
  }

  // ════════════════════════════════════════════════════════════════════
  // 选中任务（刷新页面后也走这条路，从后端重读，不靠内存）
  // ════════════════════════════════════════════════════════════════════
  async function refreshTaskDetail() {
    if (!state.task) return;
    try {
      state.task = await API.getTask(state.task.task_id);
      renderTaskInfo();
      renderSpec();
    } catch (err) {
      status($("task-status"), `重读任务失败：${errorText(err)}`, "error");
    }
  }

  async function selectTask(taskId) {
    state.busy = true;
    render();
    status($("task-status"), `正在从后端读取任务 ${taskId} ……`, "busy");
    try {
      state.task = await API.getTask(taskId);       // ← ① 后端重读，不靠内存变量
      window.location.hash = taskId;
      status($("task-status"), `已从后端读取任务：task_id = ${state.task.task_id}`, "ok");
      renderTaskInfo();
      renderSpec();
      await loadRuns();                             // ⑤
      const latest = state.runs[0];
      if (latest) {
        state.run = latest;                         // ④ 恢复到"上次执行结果"（同样来自后端）
        state.runLabel = `GET /api/tasks/${taskId}/runs 里最新的一条`;
      } else {
        state.run = null;
        state.runLabel = "";
      }
      renderRun();
    } catch (err) {
      status($("task-status"), `读取任务失败：${errorText(err)}`, "error");
    } finally {
      state.busy = false;
      render();
    }
  }

  // ════════════════════════════════════════════════════════════════════
  // 指标目录（后端 /openapi.json）+ 健康检查 + 初始化
  // ════════════════════════════════════════════════════════════════════
  function renderMetricChoices() {
    const box = $("task-metrics");
    box.innerHTML = "";
    state.metrics.names.forEach((name) => {
      const label = document.createElement("label");
      label.className = "chip chip--check";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = name;
      checkbox.checked = true;
      label.append(checkbox, document.createTextNode(name));
      box.append(label);
    });
    text(
      $("metrics-source"),
      state.metrics.source === "openapi"
        ? `来自后端 /openapi.json（${state.metrics.names.length} 个白名单指标）`
        : `⚠️ 读不到后端指标目录，用兜底清单（${state.metrics.names.length} 个）—— 真实合法性以后端校验为准`,
    );
  }

  async function initHealth() {
    try {
      const health = await API.health();
      const snapshot = health.data_snapshot || {};
      status(
        $("health"),
        `${health.service} v${health.api_version}（${health.task}）· 状态 ${health.status} · `
        + `数据快照 ${snapshot.match ? "与冻结快照一致 ✓" : "与冻结快照不一致 ✗"} · ${health.time}`,
        health.status === "ok" ? "ok" : "warn",
      );
      text($("code-version"), `代码版本：${health.code_version}`);
    } catch (err) {
      status($("health"), `后端不可用：${errorText(err)}`, "error");
    }
  }

  async function init() {
    // 事件绑定
    $("file-input").addEventListener("change", () => {
      state.file = null;          // 换了文件 → ① 的上传结果作废（② 的按钮跟着灰回去）
      renderData();
      const file = selectedFile();
      status($("data-status"), file ? `已选择：${file.name}（还没上传，点"上传到后端"）` : "尚未选择文件", file ? "muted" : "muted");
      render();
    });
    $("upload-btn").addEventListener("click", doUpload);
    $("task-form").addEventListener("submit", doCreateTask);
    $("run-btn").addEventListener("click", doRun);
    $("reload-spec-btn").addEventListener("click", refreshTaskDetail);
    $("history-refresh-btn").addEventListener("click", loadHistory);
    $("history-all").addEventListener("change", loadHistory);
    $("download-link").addEventListener("click", (event) => {
      if ($("download-link").getAttribute("aria-disabled") === "true") event.preventDefault();
    });

    render();
    renderData();
    renderSpec();
    renderRun();
    renderHistory();

    await initHealth();
    state.metrics = await API.metricCatalog();
    renderMetricChoices();
    await loadTaskList();

    // 刷新页面后恢复：URL hash 里的 task_id 优先，否则取后端返回的最新一条任务
    const wanted = window.location.hash.replace(/^#/, "");
    const target = state.tasks.find((task) => task.task_id === wanted) || state.tasks[0];
    if (target) {
      await selectTask(target.task_id);
    } else {
      status($("history-status"), "后端还没有任何任务", "muted");
    }
  }

  window.addEventListener("DOMContentLoaded", init);
})();
