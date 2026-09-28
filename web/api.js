/* api.js · 接口层（TASK-004A）—— **所有** HTTP 请求都在这里，页面逻辑不许自己 fetch。
 *
 * 为什么单独一层：
 *   ① 契约集中：后端每条路由的路径/入参/出参只在这一个文件里出现一次，
 *      后端改字段时只改这里，app.js 一行不动（与 app/api.py 的"API 层很薄"对称）。
 *   ② 错误统一：后端 **所有** 4xx/5xx 都是 `{error:{code,message,detail}, detail}`（002D），
 *      这里统一解包成 ApiError(code, message) —— 页面拿到的永远是"可读的中文错误 + 错误码"，
 *      而不是 fetch 默认那套"Failed to fetch"。
 *
 * 本文件**不含任何业务数字**：金额、行数、Spec、task_id 一律原样透传后端响应。
 */
const API = (() => {
  "use strict";

  // 请求超时用的信号（浏览器原生能力，不需要自己起定时器）。
  // 老浏览器没这个 API 时返回 undefined —— 那就退回"不设上限"，
  // 宁可慢，也不要因为设不了上限就整个请求报错。
  const timeoutSignal = (ms) => (
    typeof AbortSignal !== "undefined" && typeof AbortSignal.timeout === "function"
      ? AbortSignal.timeout(ms)
      : undefined
  );

  class ApiError extends Error {
    constructor(status, code, message, payload) {
      super(message);
      this.name = "ApiError";
      this.status = status;      // HTTP 状态码
      this.code = code;          // 后端 error.code（拿不到就按状态码兜底）
      this.payload = payload;    // 原始响应体（排障用）
    }
  }

  // 本机会话编号顺便带在请求头上（**不是**用来自证权限的令牌）。
  // 带上它的唯一目的：后端有一条"临时密码登录的会话只准改密码与退出登录"的服务端闸门
  // （见 app/api.py 的 must_change_guard），它需要知道**这次请求是谁发的**才能拦。
  // 最终授权始终由后端决定 —— 前端带或不带这个头，都改不了"能不能做"。
  function sessionHeaders() {
    try {
      const who = (typeof Session !== "undefined" && Session.identity) ? Session.identity() : null;
      const id = who && who.sessionId;
      return id ? { "X-Session-Id": id } : {};
    } catch (err) {
      return {};
    }
  }

  async function request(path, options = {}) {
    let response;
    const withSession = {
      ...options,
      headers: { ...sessionHeaders(), ...((options && options.headers) || {}) },
    };
    try {
      response = await fetch(path, withSession);
    } catch (err) {
      // 「压根连不上」与「这次请求超时了」要分开说 —— 用户能采取的动作不一样
      // （前者得去起服务，后者只要等一等再试）
      if (err && err.name === "TimeoutError") {
        throw new ApiError(0, "timeout", "读取超时：服务响应太慢，请稍后重试。");
      }
      // 网络层就失败了（服务没起 / 被防火墙拦）—— 明确说清楚，别让上层猜
      throw new ApiError(0, "network_error", `连不上服务（网络不通或服务未启动），请稍后重试。`);
    }

    const text = await response.text();
    let payload = null;
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch (err) {
        payload = null;          // 非 JSON（比如 HTML 错误页）—— 下面按原文报错
      }
    }

    if (!response.ok) {
      const error = payload && payload.error ? payload.error : null;
      throw new ApiError(
        response.status,
        (error && error.code) || `http_${response.status}`,
        (error && error.message) || text.slice(0, 400) || `HTTP ${response.status}`,
        payload,
      );
    }
    if (payload === null) {
      throw new ApiError(response.status, "bad_json", `服务返回的内容无法识别，请稍后重试。`);
    }
    return payload;
  }

  // 有的端点回的是 text/plain（文档全文），不能按 JSON 解——但**错误体仍然是那套统一 JSON**，
  // 所以这里只有"成功分支"不同，出错分支与 request() 逐字一致。
  async function requestText(path) {
    let response;
    try {
      response = await fetch(path);
    } catch (err) {
      throw new ApiError(0, "network_error", `连不上服务（网络不通或服务未启动），请稍后重试。`);
    }
    const body = await response.text();
    if (!response.ok) {
      let payload = null;
      try {
        payload = JSON.parse(body);
      } catch (err) {
        payload = null;
      }
      const error = payload && payload.error ? payload.error : null;
      throw new ApiError(
        response.status,
        (error && error.code) || `http_${response.status}`,
        (error && error.message) || body.slice(0, 400) || `HTTP ${response.status}`,
        payload,
      );
    }
    return body;
  }

  const json = (body) => ({
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  const query = (params) => {
    const search = new URLSearchParams();
    Object.entries(params || {}).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== "") search.append(key, value);
    });
    const text = search.toString();
    return text ? `?${text}` : "";
  };

  // 从 Content-Disposition 里取文件名：优先 RFC 5987 的 `filename*=UTF-8''...`（中文名），
  // 取不到再用 `filename=`。两个都没有就让调用方兜底 —— 文件名的"权威来源"是后端。
  function filenameFrom(disposition) {
    const header = disposition || "";
    const extended = header.match(/filename\*=UTF-8''([^;]+)/i);
    if (extended) {
      try {
        return decodeURIComponent(extended[1]);
      } catch (err) {
        return extended[1];
      }
    }
    const plain = header.match(/filename="?([^";]+)"?/i);
    return plain ? plain[1] : "";
  }

  // ── 指标目录：**从后端读**（不是前端写死的清单）────────────────────────
  // 后端没有单独的"指标目录"端点，但 FastAPI 的 /openapi.json 里，
  // ExecuteRequest.metrics 的字段说明写着合法取值（"可选：['sales_amount', …]"），
  // 那就是后端白名单的**权威声明**（由 api.py 的 SUPPORTED_METRICS 生成）。
  // 读不到时退回一份兜底清单，并在页面上**显式标注**"用的是兜底清单"（不冒充后端目录）。
  const FALLBACK_METRICS = [
    "sales_amount",
    "rows_in_range",
    "rows_valid",
    "rows_excluded",
    "excluded_amount",
    "valid_qty_sum",
  ];

  async function metricCatalog() {
    try {
      const schema = await request("/openapi.json");
      const shape = schema && schema.components && schema.components.schemas
        ? schema.components.schemas.ExecuteRequest
        : null;
      const description = (shape && shape.properties && shape.properties.metrics
        && shape.properties.metrics.description) || "";
      const matched = description.match(/可选：\[(.*?)\]/);
      const names = matched
        ? matched[1].split(",").map((part) => part.trim().replace(/^['"]|['"]$/g, "")).filter(Boolean)
        : [];
      if (names.length) return { names, source: "backend" };
    } catch (err) {
      // 读不到就走兜底（下面统一标注），不把页面搞挂
    }
    return { names: FALLBACK_METRICS.slice(), source: "fallback" };
  }

  return {
    ApiError,
    metricCatalog,

    // 顶栏那一栏要**尽快给结论**：健康检查单独设 8 秒上限 ——
    // 超过就当"读不到"处理，界面给人话提示，不让它一直停在"读取中"。
    // （只有这一条请求设上限：别的地方可能是真的慢，掐掉会把"慢"误判成"坏"。）
    health: () => request("/api/health", { signal: timeoutSignal(8000) }),

    upload(file) {
      const form = new FormData();
      form.append("file", file, file.name);
      return request("/api/upload", { method: "POST", body: form });
    },

    schema: (fileId) => request(`/api/schema${query({ file_id: fileId })}`),

    createTask: (payload) => request("/api/tasks", json(payload)),
    listTasks: (params) => request(`/api/tasks${query(params)}`),
    getTask: (taskId) => request(`/api/tasks/${encodeURIComponent(taskId)}`),
    runTask: (taskId) => request(`/api/tasks/${encodeURIComponent(taskId)}/run`, { method: "POST" }),
    taskRuns: (taskId, params) => request(`/api/tasks/${encodeURIComponent(taskId)}/runs${query(params)}`),
    listExecutions: (params) => request(`/api/executions${query(params)}`),

    // ── 文档输入（TASK-003：Word / PDF）────────────────────────────────
    // 与表格上传是**两条平行管道**：/api/upload 只认表格，/api/documents 只认 .docx/.pdf。
    // 各自拒收对方的格式，谁也没放宽（两条管道并存，不是替换）。
    uploadDocument(file) {
      const form = new FormData();
      form.append("file", file, file.name);
      return request("/api/documents", { method: "POST", body: form });
    },
    listDocuments: (params) => request(`/api/documents${query(params)}`),
    // 列表/详情默认不带全文；要正文走下面两个（一个进 DOM，一个给人下载/另存）
    getDocument: (docId, includeText) => request(
      `/api/documents/${encodeURIComponent(docId)}`
      + query({ include_text: includeText ? "true" : "" }),
    ),
    documentText: (docId) => requestText(`/api/documents/${encodeURIComponent(docId)}/text`),
    documentSummary: (docId, params) => request(
      `/api/documents/${encodeURIComponent(docId)}/summary${query(params)}`,
      { method: "POST" },
    ),
    documentTextUrl: (docId) => `/api/documents/${encodeURIComponent(docId)}/text`,

    // ── 自然语言问答（TASK-004）────────────────────────────────────────
    // 后端返回的是**整条链路**（问题 / Intent / 工具 / 事实 / 回答），这里原样透传：
    // 页面上分区显示的东西，就是落进 state/conversations.json 的那条记录。
    // use_llm=false 用于"强制降级演示"（验证没接 LLM 时系统不编造）。
    chatCapabilities: () => request("/api/chat/capabilities"),
    chat: (question, useLlm) => request("/api/chat", json({
      question,
      use_llm: useLlm !== false,
    })),
    listConversations: (params) => request(`/api/conversations${query(params)}`),
    getConversation: (id) => request(`/api/conversations/${encodeURIComponent(id)}`),
    // 报告下载：文件由后端按格式**现渲染**（Word / Excel / Markdown，数字与页面同源）。
    // 这里只说"哪条记录 + 什么格式"，文件名与内容类型一律由后端给 —— 前端不拼文件名。
    reportExportUrl: (id, format) => (
      `/api/conversations/${encodeURIComponent(id)}/report/export${query({ format })}`
    ),

    // ── 数据源 / 业务表 / 导入 / 导出（STEP A）─────────────────────────
    // 分页、排序、筛选、聚合、导出**全部由后端算**：这里只把用户的查询条件原样传过去，
    // 前端不做任何排序/分页/统计（否则就会出现"页面 = B、接口 = A"的第二套计算路径）。
    listDatasets: (params) => request(`/api/datasets${query(params)}`),
    getDataset: (datasetId) => request(`/api/datasets/${encodeURIComponent(datasetId)}`),

    readTable: (table, params) => request(`/api/tables/${encodeURIComponent(table)}${query(params)}`),

    // 导入向导：① 上传 + 只读检查（文件类型 / 工作表 / 前 N 行 / 字段猜测）
    inspectDataset(file) {
      const form = new FormData();
      form.append("file", file, file.name);
      return request("/api/datasets/inspect", { method: "POST", body: form });
    },
    // ② 确认导入：登记成数据集（后端会如实标"分析未开通"，前端不美化这句话）
    importDataset: (payload) => request("/api/datasets/import", json(payload)),

    // 导出：**同一个查询条件**打后端的导出端点，拿到二进制再交给页面存盘。
    // 用 fetch + Blob（不是直接跳转）：后端明确拒绝时（如"超过单次导出上限"）
    // 错误体是统一 JSON，页面上要显示那句人话，而不是把 JSON 甩到新标签页里。
    async exportTable(table, params, format) {
      const url = `/api/tables/${encodeURIComponent(table)}/export${query({ ...params, format })}`;
      let response;
      try {
        response = await fetch(url);
      } catch (err) {
        throw new ApiError(0, "network_error", "连不上服务（网络不通或服务未启动），请稍后重试。");
      }
      if (!response.ok) {
        const body = await response.text();
        let payload = null;
        try {
          payload = JSON.parse(body);
        } catch (err) {
          payload = null;
        }
        const error = payload && payload.error ? payload.error : null;
        throw new ApiError(
          response.status,
          (error && error.code) || `http_${response.status}`,
          (error && error.message) || body.slice(0, 400) || `HTTP ${response.status}`,
          payload,
        );
      }
      const blob = await response.blob();
      return { blob, filename: filenameFrom(response.headers.get("content-disposition")) };
    },

    // ── 导出直达桌面（FR-002B）─────────────────────────────────────────
    // 「文件下载了但找不到/打不开」的真因是**文件掉在了别处**（下载目录被设成 D:\ 之类），
    // 所以这里让服务端把同一份文件**直接写到桌面**，并能在资源管理器里定位它。
    //
    // 请求体里**没有路径**：写到哪儿由服务端决定（桌面目录走系统机制解析，见 app/desktop.py），
    // 前端只说要哪一份导出（哪个表 / 哪次问答的报告 + 什么格式）。
    // 存到桌面的是**同一份字节**：后端那条出口与浏览器下载共用同一个渲染函数。
    saveExportToDesktop: (payload) => request("/api/exports/desktop", json(payload)),
    // 「在文件夹中打开」只认刚刚生成的那个文件：这里只能带它的编号做校验，带不了路径。
    revealExport: (exportId) => request("/api/exports/reveal", json({ export_id: exportId || null })),

    // ── 统一导入（FR-003：Excel / CSV / Markdown / PPT / Word / PDF）────
    // 六种格式汇入**同一个入口**：后端按扩展名分流，前端不分。
    // 「导入前预览」与「导入」用同一份文件：先看不写库，用户确认了才真的入库。
    //
    // 为什么文件用 FormData 的**同一个字段名重复 append**：FastAPI 的 `list[UploadFile]`
    // 就是这么收多文件的（`files: list[UploadFile] = File(...)`）—— 一个文件一个同名条目。
    previewImports(files) {
      const form = new FormData();
      Array.from(files || []).forEach((file) => form.append("files", file, file.name));
      return request("/api/imports/preview", { method: "POST", body: form });
    },
    importFiles(files) {
      const form = new FormData();
      Array.from(files || []).forEach((file) => form.append("files", file, file.name));
      return request("/api/imports", { method: "POST", body: form });
    },
    listImports: (params) => request(`/api/imports${query(params)}`),
    getImport: (importId) => request(`/api/imports/${encodeURIComponent(importId)}`),
    // 导入进来的文档正文（与 /api/documents/* 那条老管道是两份存储：这里是物化进库的那一份）
    importedDocument: (documentId) => request(
      `/api/imports/documents/${encodeURIComponent(documentId)}${query({ include_text: "true" })}`),

    // ── 已物化数据源 + 地区维度（FR-003）──────────────────────────────
    // 与 /api/datasets（已**登记**、分析未开通）是两份列表：这里是**已物化**（行已在库里）。
    // 两边的语义差别写在 app/api_imports.py 顶部，页面上的文案也照实说。
    listSources: (params) => request(`/api/sources${query(params)}`),
    getSource: (datasetId) => request(`/api/sources/${encodeURIComponent(datasetId)}`),
    // 这个数据源有没有地区字段（没有 → 页面**不显示**「按地区」，不是点了才说没有）
    sourceRegion: (datasetId) => request(`/api/sources/${encodeURIComponent(datasetId)}/region`),
    // 按地区的销售额：条件原样传给后端，聚合全部在服务端由确定性代码算（前端不参与计算）
    querySourceRegion: (datasetId, params) => request(
      `/api/sources/${encodeURIComponent(datasetId)}/region/query${query(params)}`),

    // ── 本地账号（注册 / 登录）─────────────────────────────────────────
    // 登录校验**在后端**做（密码的比对只发生在那里），这里只把账号与密码原样递过去。
    // 前端不存密码、不比密码、也不算任何摘要 —— session.js 拿到的只是"这次是谁"。
    // 错误走统一形状：409 = 这个账号已被注册；401 = 账号或密码不对（后端不区分这两种）。
    authRegister: (payload) => request("/api/auth/register", json(payload)),
    authLogin: (payload) => request("/api/auth/login", json(payload)),
    authLogout: (sessionId) => request("/api/auth/logout", json({ session_id: sessionId })),

    // ── 账号管理（**只有管理员能调**）──────────────────────────────────
    // session_id 不是令牌：它只回答"这次管理动作是谁按的"，服务重启即失效。
    // 后端三道守卫（会话 → 账号 → 角色）：游客没会话 → 401；非管理员 → 403。
    // 前端**不**拿它当权限依据 —— 这里只是如实把编号递过去，能不能做由后端说了算。
    listAccounts: (sessionId, status) => request(
      `/api/auth/accounts${query({ session_id: sessionId, status })}`),
    reviewAccount: (username, action, sessionId) => request(
      `/api/auth/accounts/${encodeURIComponent(username)}/review`,
      json({ action, session_id: sessionId })),
    // 删除是**不可逆**的（与"停用"不同）：后端明确拒绝删掉唯一的管理员账号。
    deleteAccount: (username, sessionId) => request(
      `/api/auth/accounts/${encodeURIComponent(username)}${query({ session_id: sessionId })}`,
      { method: "DELETE" }),
    // 注册页失焦查重：传账号名问"有没有被注册"；不传就只回账号总数
    // （一个账号都没有 = 首次使用，登录页据此给出"先注册一个"的引导）
    accountExists: (username) => request(`/api/auth/accounts/exists${query({ username })}`),
    // 图形验证码：拿一张新图（连同它的编号）。点一下图就再调一次这个 —— 换图不走缓存。
    // 恢复流程第①步用的是**同一张图**（后端把"答对过"记成一张通过的挑战，
    // 见 app/challenge.py）—— 页面上因此只有一套验证码逻辑，没有第二份实现。
    captcha: () => request("/api/auth/captcha"),

    // ── 密码恢复（FR-001A：忘记密码 → 真能重置）─────────────────────────
    // 三步都在后端：第①步只换一张 reset_token（不回答"账号在不在"），
    // 第②步用恢复码换一次性票据，第③步用票据改密码。
    // ★ 第③步的请求体里**只有** ticket 与 new_password —— 前端无从指定"改谁的密码"，
    //   那是票据内部的事（后端也不读任何多余的字段）。
    resetRequest: (payload) => request("/api/auth/reset/request", json(payload)),
    resetVerify: (payload) => request("/api/auth/reset/verify", json(payload)),
    resetCommit: (payload) => request("/api/auth/reset/commit", json(payload)),
    // 生成 / 轮换自己的恢复码：给谁生成由**会话**决定（入参里没有账号名）。
    // 返回里的明文只出现这一次，页面必须当场让用户抄走。
    makeRecoveryCode: (sessionId) => request("/api/auth/recovery-code", json({ session_id: sessionId })),
    // 管理员给普通账号发一次性临时密码（仅管理员；明文也只返回这一次）
    tempPassword: (username, sessionId) => request(
      `/api/auth/accounts/${encodeURIComponent(username)}/temp-password`,
      json({ session_id: sessionId })),
    // 改密码：登录用户改自己的；临时密码登录进来的那条会话**只能走这里**。
    // 成功后返回一条**新**会话编号（密码变了 = 认证状态重建），页面要换上它接着用。
    changePassword: (payload) => request("/api/auth/password/change", json(payload)),

    // 下载地址一律用**后端返回的相对 URL**（download_url）拼当前源，前端不自己拼路径
    downloadUrl: (relative) => new URL(relative, window.location.origin).href,

    // 唯一的例外：GET /api/executions 返回的是**落盘原始记录**，里面没有 download_url 字段
    // （那是 002C 冻结的存储形状）。只有这条路径需要按 002C 冻结的路由规则拼一次 ——
    // 拼法写在接口层里（页面逻辑不该知道 URL 长什么样）。
    downloadUrlForExecution: (executionId) => `/api/download/${encodeURIComponent(executionId)}`,
  };
})();
