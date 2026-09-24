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

  class ApiError extends Error {
    constructor(status, code, message, payload) {
      super(message);
      this.name = "ApiError";
      this.status = status;      // HTTP 状态码
      this.code = code;          // 后端 error.code（拿不到就按状态码兜底）
      this.payload = payload;    // 原始响应体（排障用）
    }
  }

  async function request(path, options = {}) {
    let response;
    try {
      response = await fetch(path, options);
    } catch (err) {
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

    health: () => request("/api/health"),

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

    // 下载地址一律用**后端返回的相对 URL**（download_url）拼当前源，前端不自己拼路径
    downloadUrl: (relative) => new URL(relative, window.location.origin).href,

    // 唯一的例外：GET /api/executions 返回的是**落盘原始记录**，里面没有 download_url 字段
    // （那是 002C 冻结的存储形状）。只有这条路径需要按 002C 冻结的路由规则拼一次 ——
    // 拼法写在接口层里（页面逻辑不该知道 URL 长什么样）。
    downloadUrlForExecution: (executionId) => `/api/download/${encodeURIComponent(executionId)}`,
  };
})();
