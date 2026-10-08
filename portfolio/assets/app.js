/* ============================================================
   app.js — 交互层（无依赖、离线可用）
   渲染 / 滚动动效 / 数字动画 / 卡片光晕 / 预置问答 / 服务状态探测
   ============================================================ */
(function () {
  "use strict";
  var $  = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  /* ---------- 顶栏：滚动状态 + 进度条 ---------- */
  function initNav() {
    var nav = $(".nav"), bar = $(".progress");
    var links = $$(".nav a.lnk");
    function onScroll() {
      var y = window.scrollY || document.documentElement.scrollTop;
      if (nav) nav.classList.toggle("solid", y > 24);
      var h = document.documentElement.scrollHeight - window.innerHeight;
      if (bar) bar.style.width = (h > 0 ? (y / h) * 100 : 0) + "%";
      // 当前区块高亮
      var secs = $$("section[id]");
      var cur = "";
      secs.forEach(function (s) { if (s.offsetTop - 120 <= y) cur = s.id; });
      links.forEach(function (a) {
        a.classList.toggle("active", a.getAttribute("href") === "#" + cur);
      });
    }
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }

  /* ---------- 入场动效 ---------- */
  function initReveal() {
    var els = $$(".reveal");
    if (!("IntersectionObserver" in window)) { els.forEach(function (e) { e.classList.add("in"); }); return; }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); }
      });
    }, { threshold: 0.12, rootMargin: "0px 0px -8% 0px" });
    els.forEach(function (e, i) { e.style.transitionDelay = Math.min(i % 6, 5) * 60 + "ms"; io.observe(e); });
  }

  /* ---------- 数字滚动 ---------- */
  function initCounters() {
    var nodes = $$("[data-count]");
    if (!nodes.length) return;
    var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    function run(el) {
      var to = parseFloat(el.getAttribute("data-count")) || 0;
      if (reduce) { el.textContent = to.toLocaleString("en-US"); return; }   // 降级动效：直接给终值
      var dur = 700, t0 = null;
      function step(ts) {
        if (!t0) t0 = ts;
        var p = Math.min((ts - t0) / dur, 1);
        var eased = 1 - Math.pow(1 - p, 3);
        el.textContent = Math.round(to * eased).toLocaleString("en-US");
        if (p < 1) requestAnimationFrame(step);
      }
      requestAnimationFrame(step);
    }
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) { if (e.isIntersecting) { run(e.target); io.unobserve(e.target); } });
    }, { threshold: 0.4 });
    nodes.forEach(function (n) {
      var to = parseFloat(n.getAttribute("data-count")) || 0;
      n.textContent = reduce ? to.toLocaleString("en-US") : "0";   // 不让"0"在截图/静态抓取里冒充真值
      io.observe(n);
    });
  }

  /* ---------- 卡片：跟随鼠标的光晕 ---------- */
  function initCardGlow() {
    $$(".pcard").forEach(function (c) {
      c.addEventListener("mousemove", function (e) {
        var r = c.getBoundingClientRect();
        c.style.setProperty("--mx", (e.clientX - r.left) + "px");
        c.style.setProperty("--my", (e.clientY - r.top) + "px");
      });
    });
  }

  /* ---------- 渲染：项目卡 ---------- */
  function projectCard(p, i) {
    var metrics = (p.metrics || []).slice(0, 3).map(function (m) {
      return '<div class="m"><div class="v">' + m.v + '</div><div class="t">' + m.t + '</div></div>';
    }).join("");
    var tags = (p.tags || []).slice(0, 4).map(function (t) { return '<span class="chip">' + t + "</span>"; }).join("");
    var url = p.demo && p.demo.url ? p.demo.url : "";
    var live = (window.SITE_MODE === "public")
      ? '<span class="status"><i class="led"></i><span class="st">可现场演示</span></span>'
      : (url ? '<span class="status" data-port="' + p.demo.port + '" data-url="' + url + '"><i class="led"></i><span class="st">检测中…</span></span>'
             : '<span class="status"><i class="led"></i><span class="st">单文件看板</span></span>');
    return '' +
      '<article class="card pcard reveal" data-href="projects/' + p.slug + '.html" tabindex="0" role="link" ' +
        'aria-label="查看 ' + p.title + ' 项目详情" style="animation-delay:' + (i * 70) + 'ms">' +
        '<div class="glow"></div>' +
        '<div class="top"><div class="badge">' + p.badge + '</div>' +
          '<div><h3>' + p.title + '</h3><div class="meta">' + tags + '</div></div></div>' +
        '<p>' + p.summary + '</p>' +
        '<div class="metrics">' + metrics + '</div>' +
        '<div class="foot">' + live +
          '<a class="link detail-btn" style="margin-left:auto" href="projects/' + p.slug + '.html">查看项目详情 →</a>' +
          // ★ 线上不渲染指向 127.0.0.1 的链接（访客点了必然打不开）；
          //   本地才给真链接。线上靠卡片上的「可现场演示」+ 详情页截图说明。
          ((url && window.SITE_MODE !== "public")
            ? '<a class="link" href="' + url + '" target="_blank" rel="noopener">打开演示 ↗</a>'
            : '') +
        '</div>' +
      '</article>';
  }

  function bindCardClicks() {
    $$(".pcard[data-href]").forEach(function (c) {
      var href = c.getAttribute("data-href");
      c.style.cursor = "pointer";
      c.addEventListener("click", function (e) {
        if (e.target.closest("a,button")) return;      // 卡内已有链接/按钮，别抢
        window.location.href = href;
      });
      c.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); window.location.href = href; }
      });
    });
  }

  function renderIndex() {
    var host = $("#pgrid");
    if (host && window.PROJECTS) { host.innerHTML = window.PROJECTS.map(projectCard).join(""); bindCardClicks(); }
    // 首屏统计
    var hs = $("#heroStats");
    if (hs && window.PROFILE) {
      hs.innerHTML = window.PROFILE.heroStats.map(function (s) {
        return '<div class="st"><div class="n"><span data-count="' + s.n + '">0</span>' + (s.suffix || "") +
               '</div><div class="l">' + s.label + "</div></div>";
      }).join("");
    }
    var hp = $("#profileLine");
    if (hp && window.PROFILE) hp.textContent = window.PROFILE.intro;
  }

  /* ---------- 渲染：详情页 ---------- */
  function renderDetail() {
    var slug = document.body.getAttribute("data-slug");
    if (!slug || !window.PROJECTS) return;
    var p = window.PROJECTS.filter(function (x) { return x.slug === slug; })[0];
    if (!p) return;
    document.title = p.title + " · 唐宇作品集";
    var set = function (id, html) { var el = document.getElementById(id); if (el) el.innerHTML = html; };
    set("dTitle", p.title);
    set("dSub", p.subtitle);
    set("dTags", (p.tags || []).map(function (t) { return '<span class="chip">' + t + "</span>"; }).join(""));
    set("dSummary", p.summary);
    set("dRole", p.role);
    set("dAiRole", p.aiRole);
    set("dMetrics", '<table class="tbl"><thead><tr><th>指标</th><th>数值</th><th>说明</th><th>来源</th></tr></thead><tbody>' +
      (p.metrics || []).map(function (m) {
        return "<tr><td><b>" + m.v + "</b></td><td>" + m.t + "</td><td>" + (m.note || "—") + '</td><td><code>' + m.src + "</code></td></tr>";
      }).join("") + "</tbody></table>");
    set("dTradeoffs", (p.tradeoffs || []).map(function (h) { return "<li>" + h + "</li>"; }).join(""));
    set("dHighlights", (p.highlights || []).map(function (h) { return "<li>" + h + "</li>"; }).join(""));
    set("dLimits", (p.limits || []).map(function (h) { return "<li>" + h + "</li>"; }).join(""));
    set("dStack", (p.stack || []).map(function (t) { return '<span class="chip">' + t + "</span>"; }).join(" "));
    var demo = document.getElementById("dDemo");
    if (demo) {
      var isPublic = window.SITE_MODE === "public";
      // ★ publicUrl = 线上**真能点进去用**的地址（已部署到服务器）。
      //   有它 → 公网版给真按钮、手机也能操作；
      //   没有 → 只给说明，绝不渲染 127.0.0.1 那种死链接。
      var online = (p.demo && p.demo.publicUrl) ? p.demo.publicUrl : "";
      var linksHtml = (p.demo && p.demo.links)
        ? '<div style="margin-top:18px"><div style="color:var(--fg-dim);font-size:13.5px;margin-bottom:9px">' +
          ((isPublic && !online)
            ? '本机可进入的模块（在线版不开放实时操作，面试现场可逐个打开）：'
            : '直接进具体模块：') +
          '</div><div style="display:flex;gap:8px;flex-wrap:wrap">' +
          p.demo.links.map(function (l) {
            if (isPublic && online) {
              // 在线版：模块链接也指到公网地址（把 127.0.0.1:端口 前缀换掉、保留路径）
              var path = l.u.replace(/^https?:\/\/(127\.0\.0\.1|localhost):\d+/, "");
              return '<a class="chip" style="text-decoration:none" href="' + online.replace(/\/$/, "") + path +
                '" target="_blank" rel="noopener">' + l.t + '</a>';
            }
            if (isPublic) {
              // 没有在线地址：只给标签，避免死链接
              return '<span class="chip" style="cursor:default">' + l.t + '</span>';
            }
            return '<a class="chip" style="text-decoration:none" href="' + l.u + '" target="_blank" rel="noopener">' + l.t + '</a>';
          }).join(" ") + '</div></div>'
        : '';
      if (isPublic && online) {
        // 演示账号：有的系统登录页自己会写（如 AI供应链），
        // 有的不会（如销售 Agent 带图形验证码），所以这里按需补充说明。
        var acct = (p.demo && p.demo.publicAccount)
          ? '演示账号：<b>' + p.demo.publicAccount + '</b>'
          : '演示账号写在登录页上。';
        demo.innerHTML =
          '<a class="btn primary" href="' + online + '" target="_blank" rel="noopener">进入在线系统 ↗</a>' +
          '<div class="note"><b>这是真正能用的在线系统，不是截图</b> —— 手机也能操作。' +
          acct + '<br>' +
          '如实说明：数据是演示数据；AI 接口做了限流，避免额度被刷。</div>' + linksHtml;
      } else if (isPublic) {
        demo.innerHTML =
          '<div class="note">这个项目是在<b>本机真实运行</b>的：上面与下方的界面全部是真实运行截图，不是设计稿、不是效果图。' +
          '<b>在线版不开放实时操作</b> —— 它要连本机数据库与模型 API，放开等于把演示环境和额度放到公网。' +
          '<b>面试时可现场启动</b>，你点哪我开哪。</div>' + linksHtml;
      } else if (p.demo && p.demo.url) {
        demo.innerHTML = '<a class="btn primary" href="' + p.demo.url + '" target="_blank" rel="noopener">打开本地演示 ↗</a>' +
          '<div class="note">演示地址 <code>' + p.demo.url + '</code> ——需先双击桌面快捷方式「' + p.demo.launcher +
          '」启动服务；未启动时页面会打不开，属正常现象（不是假成功）。</div>' + linksHtml;
      } else {
        demo.innerHTML = '<div class="note">该项目以单文件看板 + 命令行演示为主：看板文件在 <code>self-healing-agent/dashboard.html</code>，先跑 <code>python -m selfheal.cli dashboard-data</code> 生成数据再打开。</div>';
      }
    }
  }

  /* ---------- 预置问答 ---------- */
  function initGuide() {
    var body = $("#chatBody"), qs = $("#chatQs");
    if (!body || !qs || !window.PRESET_QA) return;

    function push(cls, html) {
      var d = document.createElement("div");
      d.className = "msg " + cls;
      d.innerHTML = html;
      body.appendChild(d);
      body.scrollTop = body.scrollHeight;
      return d;
    }
    function type(el, text, done) {
      var i = 0;
      var t = setInterval(function () {
        el.textContent = text.slice(0, ++i);
        body.scrollTop = body.scrollHeight;
        if (i >= text.length) { clearInterval(t); if (done) done(); }
      }, 12);
    }
    function ask(item) {
      push("me", item.q);
      var target = push("ai", "…");
      var html = item.a + (item.jump ? '<a class="jump" href="' + item.jump + '">' + (item.jumpText || "查看 →") + "</a>" : "");
      setTimeout(function () { type(target, item.a, function () {
        if (item.jump) {
          var a = document.createElement("a");
          a.className = "jump"; a.href = item.jump; a.textContent = item.jumpText || "查看 →";
          target.appendChild(a); body.scrollTop = body.scrollHeight;
        }
      }); }, 260);
    }
    function renderChips() {
      qs.innerHTML = window.PRESET_QA.map(function (it, i) {
        return '<button type="button" data-i="' + i + '">' + it.q + "</button>";
      }).join("");
      $$("button", qs).forEach(function (b) {
        b.addEventListener("click", function () { ask(window.PRESET_QA[+b.getAttribute("data-i")]); });
      });
    }
    renderChips();
    push("ai", "我是这份作品集的<b>离线问答</b>（预置答案，不是实时模型）。点下面的问题，我会回答并带你跳到对应页面。" +
      '<div class="note" style="margin-top:10px">面试后可接入本地模型做实时导览。</div>');
  }

  /* ---------- 本地服务状态探测 ---------- */
  function initStatus() {
    $$("[data-port]").forEach(function (el) {
      var url = el.getAttribute("data-url");
      var label = $(".st", el);
      var done = false;
      function mark(ok) {
        if (done) return; done = true;
        el.classList.add(ok ? "on" : "off");
        if (label) label.textContent = ok ? "本机运行中" : "未启动（双击桌面快捷方式）";
      }
      // 用图片探测（不依赖跨域），随后再尝试 fetch 兜底
      var img = new Image();
      var t = setTimeout(function () { mark(false); }, 1600);
      img.onload = function () { clearTimeout(t); mark(true); };
      img.onerror = function () {
        clearTimeout(t);
        if (!window.fetch) { mark(false); return; }
        var ctl = ("AbortController" in window) ? new AbortController() : null;
        var tt = setTimeout(function () { if (ctl) ctl.abort(); mark(false); }, 1500);
        fetch(url, { mode: "no-cors", signal: ctl ? ctl.signal : undefined })
          .then(function () { clearTimeout(tt); mark(true); })
          .catch(function () { clearTimeout(tt); mark(false); });
      };
      img.src = url + "/favicon.ico?_=" + Date.now();
    });
  }


  /* ---------- 首页：真实界面预览带 ---------- */
  function renderStrip() {
    var host = $("#shotsStrip");
    if (!host || !window.PROJECTS) return;
    var pick = ["sales-report-agent", "ai-supply-chain"];
    host.innerHTML = pick.map(function (slug) {
      var p = window.PROJECTS.filter(function (x) { return x.slug === slug; })[0];
      if (!p || !p.shots || !p.shots.length) return "";
      var s = p.shots[0];
      return '<a class="shot" href="projects/' + slug + '.html" style="text-decoration:none">' +
               '<img src="' + s.src + '" alt="' + p.title + ' 界面截图" loading="lazy">' +
               '<figcaption>' + p.title + " · " + s.cap + "</figcaption></a>";
    }).join("");
  }

  /* ---------- 界面实拍 ---------- */
  function renderShots() {
    var host = $("#dShots");
    if (!host) return;
    var slug = document.body.getAttribute("data-slug");
    var p = (window.PROJECTS || []).filter(function (x) { return x.slug === slug; })[0];
    if (!p || !p.shots || !p.shots.length) { host.parentNode.style.display = "none"; return; }
    var prefix = "../";
    host.innerHTML = p.shots.map(function (s) {
      return '<figure class="shot" data-src="' + prefix + s.src + '">' +
               '<img src="' + prefix + s.src + '" alt="' + p.title + ' 界面截图" loading="lazy">' +
               '<figcaption>' + s.cap + "</figcaption></figure>";
    }).join("");
    $$(".shot", host).forEach(function (f) {
      f.addEventListener("click", function () {
        var src = f.getAttribute("data-src");
        var lb = document.createElement("div");
        lb.className = "lightbox";
        lb.innerHTML = '<img src="' + src + '" alt="放大查看"><span class="lb-hint">点击任意处关闭</span>';
        lb.addEventListener("click", function () { lb.remove(); });
        document.body.appendChild(lb);
      });
    });
  }

  /* ---------- 真实案例回放（数据全部来自真实运行记录） ---------- */
  function rpSteps(steps) {
    var h = "", t = 0;
    steps.forEach(function (s, i) {
      t += (s.ms || 0);
      var inner = "";
      (s.items || []).forEach(function (it) {
        if (it.k === "call") {
          inner += '<div class="rp-call">调用工具 ' + it.name + "(" + JSON.stringify(it.args || {}) + ")</div>";
        } else if (it.k === "result") {
          inner += '<div class="txt">工具返回：' + (it.text || "").replace(/</g, "&lt;") + "</div>";
        } else if (it.k === "answer") {
          inner += '<div class="txt">' + (it.text || "").replace(/</g, "&lt;") + "</div>";
        }
      });
      h += '<div class="rp-step" style="animation-delay:' + (i * 180) + 'ms">' +
             '<span class="dot"></span><span class="nd">' + (s.node || "step") + "</span>" +
             '<span class="ms">' + (s.ms || 0) + " ms</span>" + inner + "</div>";
    });
    return { html: h, total: t };
  }

  function rpOrch(idx) {
    var t = window.REPLAYS.orch[idx];
    var s = rpSteps(t.steps);
    return '<div class="rp-head"><b>' + t.label + '</b><span style="margin-left:auto" class="ms">真实总耗时 ' +
      t.sec + " s</span></div>" +
      '<div class="rp-body">' + s.html + "</div>" +
      '<div class="rp-final">' + (t.answer || "").replace(/</g, "&lt;") + "</div>" +
      '<div class="rp-note">真实案例回放 · 非实时 AI —— 来自本机真实调用（POST /api/chat）的真实记录，耗时用逐段计时实测（包住大模型调用点量 LLM 耗时，其余为纯代码）；首次取数含聚合预热，同一条问题第二次会明显更快</div>';
  }

  function rpRag() {
    return window.REPLAYS.rag.map(function (c) {
      var kw = (c.kw_ok || []).map(function (k) { return '<span class="chip">命中 ' + k + "</span>"; }).join("") +
               (c.kw_miss || []).map(function (k) { return '<span class="chip" style="opacity:.6">未命中 ' + k + "</span>"; }).join("");
      return '<div class="rp-trace" style="margin-top:12px"><div class="rp-head"><b>' + c.id + " · " + c.q +
        '</b><span style="margin-left:auto" class="ms">' + (c.sec || "-") + " s</span></div>" +
        '<div class="rp-body"><div class="txt">检索命中：' + (c.hit ? "是" : "否") +
        "　回答正确：" + (c.acc ? "是" : "否") + "　来源正确：" + (c.src_ok ? "是" : "否") + "</div>" +
        '<div style="margin-top:8px">' + kw + "</div>" +
        (c.chunks && c.chunks.length ? '<div class="txt">检索到的片段：' + c.chunks.map(function (x) {
            return (x.s || "?") + "（score " + (x.score == null ? "-" : Number(x.score).toFixed(3)) + "）"; }).join("、") + "</div>" : "") +
        "</div></div>";
    }).join("");
  }

  function rpCs() {
    var h = "";
    var live = window.REPLAYS.cs.live;
    if (live) {
      h += '<div class="rp-trace"><div class="rp-head"><b>真实工单调用</b><span style="margin-left:auto" class="ms">' +
        live.sec + " s</span></div>" +
        '<div class="rp-body"><div class="txt">提问：' + (live.q || "") + "</div>" +
        '<div class="rp-call">调用工具 ' + ((live.tools || []).join("、")) + "</div></div>" +
        '<div class="rp-final">' + (live.answer || "").replace(/</g, "&lt;") + "</div>" +
        '<div class="rp-note">真实案例回放 · 非实时 AI（已脱敏：收货人/地址做了掩码处理）</div></div>';
    }
    (window.REPLAYS.cs.failed || []).forEach(function (c) {
      h += '<div class="rp-trace" style="margin-top:12px"><div class="rp-head"><b>真实未通过样本 ' + c.id + "</b>" +
        '<span style="margin-left:auto" class="ms">评测集记录</span></div>' +
        '<div class="rp-body"><div class="txt">期望分类「' + c.cat_exp + "」· 实际「" + c.cat_act + "」　" +
        "期望动作「" + c.act_exp + "」· 实际「" + c.act_act + "」</div>" +
        '<div class="txt">期望工具：' + ((c.tools_exp || []).join("、") || "-") + "　实际工具：" + ((c.tools_act || []).join("、") || "-") + "</div>" +
        '<div class="rp-note" style="margin:10px 0 0">动作正确、分类判偏 —— 这类样本我逐条留在评测集里，没有删掉</div></div></div>';
    });
    return h;
  }

  function rpSelfheal() {
    var i = window.REPLAYS.selfheal;
    if (!i) return "";
    return '<div class="rp-trace"><div class="rp-head"><b>真实 incident</b><span style="margin-left:auto" class="ms">' +
      (i.started || "").slice(0, 19) + "</span></div>" +
      '<div class="rp-body"><div class="txt">服务：' + i.service + "　编号：" + i.id + "</div>" +
      '<div class="rp-call">执行动作 ' + i.action + "　结果：" + (i.ok ? "成功" : "失败") + "</div>" + "</div>" +
      '<div class="rp-note">真实案例回放 · 非实时 AI（来自 dashboard_data.js 的真实 incident 记录，共 782 条同类记录）</div></div>';
  }

  function renderReplays() {
    var host = $("#replayBody"), qs = $("#replayQs");
    if (host && qs && window.REPLAYS) {
      function show(i) {
        host.innerHTML = rpOrch(i);
        $$("button", qs).forEach(function (b, j) { b.classList.toggle("on", i === j); });
      }
      qs.innerHTML = window.REPLAYS.orch.map(function (t, i) {
        return '<button type="button" data-i="' + i + '">' + t.label + "</button>";
      }).join("");
      $$("button", qs).forEach(function (b) {
        b.addEventListener("click", function () { show(+b.getAttribute("data-i")); });
      });
      show(0);
    }
    // 详情页
    var box = $("#dReplay");
    if (box && window.REPLAYS) {
      var slug = document.body.getAttribute("data-slug");
      if (slug === "sales-report-agent" || slug === "orchestrator-agent") {
        box.innerHTML = window.REPLAYS.orch.map(function (t, i) { return '<div style="margin-top:12px">' + (function () { return rpOrch(i); })() + "</div>"; }).join("");
      } else if (slug === "rag-agent") {
        box.innerHTML = rpRag();
      } else if (slug === "customer-service-agent") {
        box.innerHTML = rpCs();
      } else if (slug === "self-healing-agent") {
        box.innerHTML = rpSelfheal();
      } else {
        box.innerHTML = '<div class="rp-note">这个项目以真实页面验收与回归记录为主，回放见「真实案例回放」区与验收表。</div>';
      }
    }
  }

  /* ---------- 启动 ---------- */
  function boot() {
    renderIndex();
    renderDetail();
    renderStrip();
    renderShots();
    renderReplays();
    initNav(); initReveal(); initCounters(); initCardGlow(); initGuide(); initStatus();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
