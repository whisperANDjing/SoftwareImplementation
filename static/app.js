/* ==========================================================================
   APS 实施顾问 AI 工作台 · 第二阶段前端增量
   原生 JS，无构建工具。职责：
   1. 五阶段导航与状态展示
   2. 阶段一客户信息表单 / 其他阶段对话输入
   3. Agent 输出、来源标签、纠偏重生成、归档
   4. 右侧项目上下文与项目知识库（上传/切片/摘要）
   ========================================================================== */

(() => {
  "use strict";

  const STATUS_LABELS = {
    not_started: "未开始",
    in_progress: "进行中",
    archived: "已归档",
  };

  const STAGE_PLACEHOLDERS = {
    1: "补充访谈信息，或追问 Agent：例如关键设备有多少台、插单频次如何统计……",
    2: "描述已有数据、来源系统或缺口：例如 ERP 能导出订单和 BOM，但换型时间没有实测数据……",
    3: "补充方案约束或追问某条规则：例如插单要区分客户等级，设备故障优先转线还是外协……",
    4: "补充系统能力或接口限制：例如 MES 只提供设备状态，不提供故障预计恢复时间……",
    5: "补充上线范围或组织约束：例如先上一条产线，并行运行两周，回退决策人是计划经理……",
  };

  const state = {
    project: null,
    stages: [],
    activeStage: 1,
    latestReply: "",
    latestSource: "",
    latestSources: [],
    lastUserInput: "",
    loading: false,
    switching: false,
    contextCollapsed: false,
    editingStage: null,
    chatDrafts: {},
    correctionDrafts: {},
    answerModes: {},
    documents: [],
    uploading: false,
    uploadMessage: "",
    uploadMessageType: "info",
    streaming: false,
    streamingText: "",
    streamingSources: [],
    streamingStage: null,
    streamingUserInput: "",
    exportPendingFormat: "",
    exporting: false,
    view: "stage",
    postLaunchUnlocked: false,
    issues: [],
    issuesLoaded: false,
    issueLoading: false,
    issueAnalyzing: false,
    issueAnalysis: "",
    archiveGate: null,
    assistantOpen: false,
    assistantMessages: [],
    assistantLoadedProject: null,
    assistantLoading: false,
    assistantStreaming: false,
    assistantAbortController: null,
  };

  const el = (id) => document.getElementById(id);

  // 所有网络调用统一从 apiClient 进入；组件不直接调用 fetch。
  const apiClient = {
    request: (...args) => api(...args),
    stream: (...args) => streamSseRequest(...args),
    download: (...args) => downloadFile(...args),
  };

  function filenameFromDisposition(header) {
    if (!header) return "";
    const encoded = header.match(/filename\*=UTF-8''([^;]+)/i);
    if (encoded) {
      try {
        return decodeURIComponent(encoded[1]);
      } catch (error) {
        return encoded[1];
      }
    }
    const plain = header.match(/filename="?([^";]+)"?/i);
    return plain ? plain[1] : "";
  }

  async function downloadFile(path, payload, fallbackName) {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      let message = `导出失败（HTTP ${response.status}）`;
      try {
        const data = await response.json();
        if (data && data.message) message = data.message;
      } catch (error) {
        // 文件接口异常时可能返回非 JSON，保留 HTTP 状态提示。
      }
      throw new Error(message);
    }
    const blob = await response.blob();
    const filename =
      filenameFromDisposition(response.headers.get("Content-Disposition")) ||
      fallbackName;
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1500);
    return filename;
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function formatDate(iso) {
    if (!iso) return "";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    return date.toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function statusLabel(status) {
    return STATUS_LABELS[status] || "未开始";
  }

  /* ------------------------------------------------------------------------
     Markdown 渲染（轻量、无第三方依赖，先转义再处理标记）
     ------------------------------------------------------------------------ */

  function renderInline(text) {
    let html = escapeHtml(text);
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
    html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/__([^_]+)__/g, "<strong>$1</strong>");
    html = html.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
    return html;
  }

  function splitTableRow(line) {
    let value = String(line || "").trim();
    if (value.startsWith("|")) value = value.slice(1);
    if (value.endsWith("|")) value = value.slice(0, -1);
    return value.split("|").map((cell) => cell.trim());
  }

  function isTableSeparator(line) {
    const cells = splitTableRow(line);
    return (
      cells.length > 0 &&
      cells.every((cell) => /^:?-{3,}:?$/.test(cell.replace(/\s/g, "")))
    );
  }

  function renderMarkdown(markdown) {
    const lines = String(markdown || "")
      .replace(/\r\n/g, "\n")
      .replace(/\r/g, "\n")
      .split("\n");
    const html = [];
    let paragraph = [];
    let listType = null;
    let inCode = false;
    let codeLines = [];

    const flushParagraph = () => {
      if (!paragraph.length) return;
      html.push(`<p>${paragraph.map((line) => renderInline(line)).join("<br>")}</p>`);
      paragraph = [];
    };

    const closeList = () => {
      if (listType) {
        html.push(`</${listType}>`);
        listType = null;
      }
    };

    for (let index = 0; index < lines.length; index += 1) {
      const rawLine = lines[index];
      const line = rawLine.trim();

      if (line.startsWith("```")) {
        flushParagraph();
        closeList();
        if (inCode) {
          html.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
          codeLines = [];
          inCode = false;
        } else {
          inCode = true;
        }
        continue;
      }

      if (inCode) {
        codeLines.push(rawLine);
        continue;
      }

      if (!line) {
        flushParagraph();
        closeList();
        continue;
      }

      const headingMatch = line.match(/^(#{1,4})\s+(.+)$/);
      if (headingMatch) {
        flushParagraph();
        closeList();
        const level = headingMatch[1].length;
        html.push(`<h${level}>${renderInline(headingMatch[2])}</h${level}>`);
        continue;
      }

      if (/^(-{3,}|\*{3,}|_{3,})$/.test(line)) {
        flushParagraph();
        closeList();
        html.push("<hr>");
        continue;
      }

      if (
        line.includes("|") &&
        index + 1 < lines.length &&
        isTableSeparator(lines[index + 1])
      ) {
        flushParagraph();
        closeList();
        const headers = splitTableRow(line);
        index += 2;
        const rows = [];
        while (index < lines.length) {
          const candidate = lines[index].trim();
          if (!candidate || !candidate.includes("|")) break;
          rows.push(splitTableRow(candidate));
          index += 1;
        }
        index -= 1;
        const headHtml = headers
          .map((cell) => `<th>${renderInline(cell)}</th>`)
          .join("");
        const bodyHtml = rows
          .map(
            (row) =>
              `<tr>${headers
                .map((_, cellIndex) => `<td>${renderInline(row[cellIndex] || "")}</td>`)
                .join("")}</tr>`
          )
          .join("");
        html.push(
          `<div class="table-wrap"><table><thead><tr>${headHtml}</tr></thead><tbody>${bodyHtml}</tbody></table></div>`
        );
        continue;
      }

      if (line.startsWith(">")) {
        flushParagraph();
        closeList();
        const quoteLines = [];
        while (index < lines.length && lines[index].trim().startsWith(">")) {
          quoteLines.push(lines[index].trim().replace(/^>\s?/, ""));
          index += 1;
        }
        index -= 1;
        html.push(
          `<blockquote><p>${quoteLines
            .map((quote) => renderInline(quote))
            .join("<br>")}</p></blockquote>`
        );
        continue;
      }

      const unorderedMatch = line.match(/^[-*+]\s+(.+)$/);
      if (unorderedMatch) {
        flushParagraph();
        if (listType !== "ul") {
          closeList();
          html.push("<ul>");
          listType = "ul";
        }
        html.push(`<li>${renderInline(unorderedMatch[1])}</li>`);
        continue;
      }

      const orderedMatch = line.match(/^\d+[.)]\s+(.+)$/);
      if (orderedMatch) {
        flushParagraph();
        if (listType !== "ol") {
          closeList();
          html.push("<ol>");
          listType = "ol";
        }
        html.push(`<li>${renderInline(orderedMatch[1])}</li>`);
        continue;
      }

      closeList();
      paragraph.push(line);
    }

    if (inCode) {
      html.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
    }
    flushParagraph();
    closeList();
    return html.join("");
  }

  /* ------------------------------------------------------------------------
     请求封装
     ------------------------------------------------------------------------ */

  async function api(path, options = {}) {
    const { timeoutMs = 100_000, ...fetchOptions } = options;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
    const headers = { Accept: "application/json", ...(fetchOptions.headers || {}) };
    if (typeof fetchOptions.body === "string") {
      headers["Content-Type"] = "application/json";
    }

    try {
      const response = await fetch(path, {
        ...fetchOptions,
        headers,
        signal: controller.signal,
      });
      const text = await response.text();
      let data = null;
      try {
        data = text ? JSON.parse(text) : null;
      } catch {
        throw new Error("后端返回了无法解析的内容，请查看 Flask 日志。");
      }
      if (!response.ok || !data || data.ok === false) {
        throw new Error((data && (data.error || data.message)) || `请求失败（HTTP ${response.status}）`);
      }
      return data;
    } catch (error) {
      if (error.name === "AbortError") {
        const seconds = Math.round(timeoutMs / 1000);
        throw new Error(`请求超时（${seconds} 秒）。请检查网络或 API 状态后重试。`);
      }
      if (error instanceof TypeError) {
        throw new Error("无法连接后端，请确认 Flask 服务已启动。");
      }
      throw error;
    } finally {
      window.clearTimeout(timeout);
    }
  }

  /* ------------------------------------------------------------------------
     Toast
     ------------------------------------------------------------------------ */

  function showToast(message, type = "info", duration = 4200) {
    const container = el("toast-container");
    if (!container) return;
    const toast = document.createElement("div");
    toast.className = `toast toast--${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    window.setTimeout(() => {
      toast.classList.add("is-leaving");
      window.setTimeout(() => toast.remove(), 200);
    }, duration);
  }

  /* ------------------------------------------------------------------------
     项目加载与渲染
     ------------------------------------------------------------------------ */

  function currentStageState() {
    if (!state.project) return null;
    return state.project.stages?.[String(state.activeStage)] || null;
  }

  function getLatestOutput() {
    const stageState = currentStageState();
    if (!stageState) return { content: "", source: "", sources: [] };
    const messages = Array.isArray(stageState.conversation_history)
      ? stageState.conversation_history
      : [];
    const lastAssistant = [...messages]
      .reverse()
      .find((message) => message.role === "assistant" && message.content);
    if (lastAssistant) {
      return {
        content: lastAssistant.content,
        source: "assistant",
        sources: Array.isArray(lastAssistant.sources) ? lastAssistant.sources : [],
      };
    }
    if (stageState.archive?.content) {
      return { content: stageState.archive.content, source: "archive", sources: [] };
    }
    return { content: "", source: "", sources: [] };
  }

  function deriveLatest() {
    if (state.streaming && state.streamingStage === state.activeStage) {
      state.latestReply = state.streamingText;
      state.latestSource = "streaming";
      state.latestSources = state.streamingSources;
      if (state.streamingUserInput) {
        state.lastUserInput = state.streamingUserInput;
      }
      return;
    }

    const stageState = currentStageState();
    const latest = getLatestOutput();
    state.latestReply = latest.content;
    state.latestSource = latest.source;
    state.latestSources = latest.sources;

    const messages = Array.isArray(stageState?.conversation_history)
      ? stageState.conversation_history
      : [];
    const lastUser = [...messages].reverse().find((message) => message.role === "user");
    state.lastUserInput = lastUser ? String(lastUser.content || "") : "";
  }

  function hasIndexedDocuments() {
    return state.documents.some((document) => document.status === "indexed");
  }

  function effectiveAnswerMode() {
    if (state.answerModes[state.activeStage]) {
      return state.answerModes[state.activeStage];
    }
    return hasIndexedDocuments() ? "rag" : "stage";
  }

  function sourceFileName(source) {
    return source.file_name || source.file || "未知文件";
  }

  function sourceLocator(source) {
    const parts = [];
    if (source.page) parts.push(`第 ${source.page} 页`);
    if (source.sheet) parts.push(`工作表「${source.sheet}」`);
    const rowStart = source.row ?? source.row_start;
    if (rowStart) {
      const rowText =
        source.row_end && source.row_end !== rowStart
          ? `第 ${rowStart}–${source.row_end} 行`
          : `第 ${rowStart} 行`;
      parts.push(rowText);
    }
    const paragraphStart = source.paragraph ?? source.paragraph_start;
    if (paragraphStart) {
      const paragraphText =
        source.paragraph_end && source.paragraph_end !== paragraphStart
          ? `第 ${paragraphStart}–${source.paragraph_end} 段`
          : `第 ${paragraphStart} 段`;
      parts.push(paragraphText);
    }
    return parts.join(" · ");
  }

  function sourceLabel(source) {
    const locator = sourceLocator(source);
    return `《${sourceFileName(source)}》${locator ? ` ${locator}` : ""}`;
  }

  function sourceChunkId(source) {
    if (source.chunk_id) return source.chunk_id;
    if (source.doc_id !== undefined && source.chunk_index !== undefined) {
      return `${source.doc_id}:${String(source.chunk_index).padStart(4, "0")}`;
    }
    return "";
  }

  function renderShellState() {
    const collapsed = Boolean(state.contextCollapsed);
    el("app-shell")?.classList.toggle("context-collapsed", collapsed);
    document.body.classList.toggle("no-project", !state.project);
    const toggle = el("context-toggle");
    if (toggle) {
      toggle.hidden = !state.project;
      toggle.setAttribute("aria-expanded", String(!collapsed));
    }
    const uploadButton = el("upload-doc-btn");
    if (uploadButton) uploadButton.hidden = !state.project;
  }

  function renderTopbarProject() {
    const target = el("topbar-project");
    if (!target) return;
    if (!state.project) {
      target.innerHTML = "";
      return;
    }
    target.innerHTML = `
      <div class="project-pill" title="${escapeHtml(state.project.client_name || "")}">
        <span class="project-pill-dot"></span>
        <strong>${escapeHtml(state.project.client_name || "未命名项目")}</strong>
        <span>·</span>
        <span>${escapeHtml(state.project.industry || "行业待补充")}</span>
      </div>
    `;
  }

  function renderProjectCard() {
    const card = el("project-card");
    if (!card) return;
    if (!state.project) {
      card.className = "project-card project-card--empty";
      card.innerHTML = `
        <p>尚未选择项目</p>
        <button type="button" class="btn btn-primary btn-block" id="rail-new-project-btn">创建第一个项目</button>
      `;
      el("rail-new-project-btn")?.addEventListener("click", openProjectModal);
      return;
    }

    const stages = Object.values(state.project.stages || {});
    const archivedCount = stages.filter((stage) => stage.status === "archived").length;
    card.className = "project-card";
    card.innerHTML = `
      <p class="project-card-client">${escapeHtml(state.project.client_name || "未命名项目")}</p>
      <p class="project-card-meta">${escapeHtml(
        [state.project.industry, state.project.production_mode].filter(Boolean).join(" · ") ||
          "行业与生产模式待补充"
      )}</p>
      <div class="project-card-stats">
        <div><strong>${archivedCount}</strong>阶段已归档</div>
        <div><strong>${state.activeStage}</strong>当前阶段</div>
      </div>
    `;
  }

  function renderStageNav() {
    const list = el("stage-list");
    if (!list) return;
    if (!state.project) {
      list.innerHTML = "";
      return;
    }

    list.innerHTML = state.stages
      .map((stage) => {
        const stageState = state.project.stages[String(stage.id)] || {};
        const status = stageState.status || "not_started";
        const isActive = stage.id === state.activeStage;
        const exportLabel = "Word / Excel";
        return `
          <button
            type="button"
            class="stage-link ${isActive ? "is-active" : ""} is-${status}"
            data-stage="${stage.id}"
            ${isActive ? 'aria-current="step"' : ""}
            title="${escapeHtml(stage.description || "")}"
          >
            <span class="stage-number">0${stage.id}</span>
            <span class="stage-link-text">
              <span class="stage-link-name">${escapeHtml(stage.name)}</span>
              <span class="stage-link-meta">阶段 0${stage.id} · 交付 ${exportLabel}</span>
            </span>
            <span class="stage-state stage-state--${status}">${statusLabel(status)}</span>
          </button>
        `;
      })
      .join("");

    list.querySelectorAll(".stage-link").forEach((button) => {
      button.addEventListener("click", () => {
        switchStage(Number(button.dataset.stage));
      });
    });

    // 移动端阶段导航横向滚动：确保当前阶段始终可见。
    if (window.innerWidth <= 900) {
      const activeButton = list.querySelector(".stage-link.is-active");
      if (activeButton) {
        list.scrollLeft = Math.max(
          0,
          activeButton.offsetLeft - list.clientWidth / 2 + activeButton.offsetWidth / 2
        );
      }
    }
  }

  function renderTemplateContext() {
    const card = el("template-context-card");
    const body = el("template-context-body");
    if (!card || !body) return;
    const stageState = currentStageState() || {};
    const initial = stageState.initial_context;
    const content = String(initial?.content || "").trim();
    if (!content) {
      card.hidden = true;
      body.innerHTML = "";
      return;
    }
    const source =
      initial?.source_project_name || initial?.source_project_id || "历史项目";
    const stageName = state.stages.find((item) => item.id === initial?.source_stage)?.name;
    card.hidden = false;
    card.querySelector(".template-context-source").textContent =
      `${source}${stageName ? ` · ${stageName}` : ""}`;
    body.innerHTML = renderMarkdown(content);
  }

  function renderStageHeading() {
    const stage = state.stages.find((item) => item.id === state.activeStage);
    if (!stage) return;
    const stageState = currentStageState() || {};
    const status = stageState.status || "not_started";

    el("stage-kicker").textContent = `STAGE 0${stage.id} / 05`;
    el("stage-title").textContent = stage.name;
    el("stage-description").textContent = stage.description || "";
    el("stage-deliverable").textContent = stage.deliverable || "";
    const pill = el("stage-status-pill");
    pill.className = `status-pill status-pill--${status}`;
    pill.textContent = statusLabel(status);
  }

  function stageProductionOptions(selected) {
    const options = [
      "备货型 MTS",
      "订单型 MTO",
      "按订单装配 ATO",
      "按订单设计 ETO",
      "混合模式",
    ];
    return [
      '<option value="">请选择</option>',
      ...options.map(
        (option) =>
          `<option value="${escapeHtml(option)}" ${
            option === selected ? "selected" : ""
          }>${escapeHtml(option)}</option>`
      ),
    ].join("");
  }

  function chatComposerHtml({ secondary = false } = {}) {
    const draft = state.chatDrafts[state.activeStage] || "";
    const answerMode = effectiveAnswerMode();
    const hasDocs = hasIndexedDocuments();
    const ragMode = answerMode === "rag";
    const placeholder = ragMode
      ? "针对已上传文档提问，例如：这个文件里排产周期是多少？"
      : STAGE_PLACEHOLDERS[state.activeStage] || "输入补充信息或追问…";
    const label = secondary
      ? ragMode
        ? "补充信息 / 文档问答"
        : "补充信息 / 继续对话"
      : ragMode
        ? "文档问答（RAG）"
        : "与本阶段 Agent 对话";
    const hint = ragMode
      ? "已连接项目知识库，回答会检索文档并标注来源。"
      : "已归档内容会自动进入本轮上下文，不需要重复描述。";
    const modeToggle = hasDocs
      ? `<button type="button" class="chat-mode-toggle" id="chat-mode-toggle" title="点击切换文档问答 / 阶段生成">
           <span class="chat-mode-dot"></span>${ragMode ? "文档问答" : "阶段生成"}
         </button>`
      : "";
    return `
      <div class="chat-composer ${secondary ? "chat-composer--secondary" : ""}" data-secondary="${secondary ? "1" : "0"}">
        <div class="composer-head">
          <label for="chat-input">${label}</label>
          <div class="composer-meta">
            ${modeToggle}
            <span id="composer-hint">${hint}</span>
          </div>
        </div>
        <textarea
          id="chat-input"
          rows="3"
          maxlength="8000"
          placeholder="${escapeHtml(placeholder)}"
        >${escapeHtml(draft)}</textarea>
        <div class="composer-foot">
          <span class="keyboard-hint"><kbd>Ctrl</kbd> / <kbd>⌘</kbd> + <kbd>Enter</kbd> 发送</span>
          <button type="button" class="btn btn-primary btn-sm" id="chat-send-btn">发送给 Agent</button>
        </div>
      </div>
    `;
  }

  function toggleChatMode() {
    const next = effectiveAnswerMode() === "rag" ? "stage" : "rag";
    state.answerModes[state.activeStage] = next;
    const button = el("chat-mode-toggle");
    const hint = el("composer-hint");
    const label = document.querySelector('label[for="chat-input"]');
    const composer = document.querySelector(".chat-composer");
    const secondary = composer?.dataset.secondary === "1";
    if (button) {
      button.innerHTML = `<span class="chat-mode-dot"></span>${next === "rag" ? "文档问答" : "阶段生成"}`;
    }
    if (hint) {
      hint.textContent =
        next === "rag"
          ? "已连接项目知识库，回答会检索文档并标注来源。"
          : "已归档内容会自动进入本轮上下文，不需要重复描述。";
    }
    if (label) {
      label.textContent = secondary
        ? next === "rag"
          ? "补充信息 / 文档问答"
          : "补充信息 / 继续对话"
        : next === "rag"
          ? "文档问答（RAG）"
          : "与本阶段 Agent 对话";
    }
  }

  function renderInputZone() {
    const zone = el("input-zone");
    if (!zone) return;

    if (state.activeStage === 1) {
      const project = state.project;
      zone.innerHTML = `
        <div class="stage-form-card">
          <div class="card-head">
            <div>
              <span class="kicker">CLIENT INTAKE</span>
              <h3>客户现状信息</h3>
              <p>先给出已知事实，Agent 会据此生成调研纪要，并列出需要补充的问题。</p>
            </div>
          </div>
          <form id="stage-one-form">
            <div class="form-grid">
              <label class="field field--full">
                <span>客户名称 <em>*</em></span>
                <input type="text" id="stage-client-name" maxlength="120" value="${escapeHtml(
                  project.client_name || ""
                )}" placeholder="例如：华东精密制造有限公司" required>
              </label>
              <label class="field">
                <span>行业</span>
                <input type="text" id="stage-industry" list="industry-options" value="${escapeHtml(
                  project.industry || ""
                )}" placeholder="例如：汽车零部件">
              </label>
              <label class="field">
                <span>生产模式</span>
                <select id="stage-production-mode">
                  ${stageProductionOptions(project.production_mode || "")}
                </select>
              </label>
              <label class="field field--full">
                <span>核心痛点</span>
                <textarea id="stage-pain-points" rows="3" placeholder="例如：插单频繁、缺料发现晚、设备故障后计划无法快速恢复……">${escapeHtml(
                  project.pain_points || ""
                )}</textarea>
              </label>
            </div>
            <div class="form-actions">
              <span class="form-actions-note">保存后会更新项目基本信息，并生成阶段一诊断初稿。</span>
              <button type="submit" class="btn btn-primary" id="stage-one-submit">保存并生成诊断</button>
            </div>
          </form>
        </div>
        ${chatComposerHtml({ secondary: true })}
      `;
      el("stage-one-form")?.addEventListener("submit", handleStageFormSubmit);
    } else {
      zone.innerHTML = chatComposerHtml();
    }

    const chatInput = el("chat-input");
    if (chatInput) {
      chatInput.addEventListener("input", (event) => {
        state.chatDrafts[state.activeStage] = event.target.value;
      });
      chatInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
          event.preventDefault();
          submitChatInput();
        }
      });
    }
    el("chat-send-btn")?.addEventListener("click", submitChatInput);
    el("chat-mode-toggle")?.addEventListener("click", toggleChatMode);
  }

  function renderOutput() {
    deriveLatest();
    const output = el("agent-output");
    const sourceBadge = el("output-source-badge");
    const copyButton = el("copy-output-btn");
    const regenerateButton = el("regenerate-btn");
    const archiveButton = el("archive-btn");
    const correction = el("correction-input");
    const stageState = currentStageState() || {};

    if (correction) {
      correction.value = state.correctionDrafts[state.activeStage] || "";
      if (!correction.dataset.bound) {
        correction.dataset.bound = "1";
        correction.addEventListener("input", (event) => {
          state.correctionDrafts[state.activeStage] = event.target.value;
        });
      }
    }

    const isStreaming =
      state.streaming && state.streamingStage === state.activeStage;

    if (sourceBadge) {
      sourceBadge.hidden = !isStreaming && !state.latestReply;
      sourceBadge.classList.toggle(
        "source-badge--archive",
        !isStreaming && state.latestSource === "archive"
      );
      sourceBadge.textContent = isStreaming
        ? "Agent 生成中"
        : state.latestSource === "archive"
          ? "已归档版本"
          : state.latestSource === "assistant"
            ? "本次 Agent 输出"
            : "";
    }

    if (output) {
      if (isStreaming && !state.latestReply) {
        output.innerHTML = `
          <div class="thinking">
            <div class="spinner" aria-hidden="true"></div>
            <span>Agent 正在读取项目上下文并生成回答…</span>
          </div>
        `;
      } else if (state.latestReply) {
        output.innerHTML =
          renderMarkdown(state.latestReply) +
          (isStreaming
            ? '<span class="assistant-cursor" aria-hidden="true"></span>'
            : "");
      } else {
        output.innerHTML = `
          <div class="output-placeholder">
            <div class="placeholder-mark" aria-hidden="true">AI</div>
            <p>提交阶段信息后，Agent 会在这里给出结构化交付稿。</p>
            <span>所有输出均为 AI 初稿，需要顾问校验后再发送客户。</span>
          </div>
        `;
      }
    }

    renderSourceStrip();

    if (copyButton) copyButton.disabled = !state.latestReply || state.loading;
    if (regenerateButton) regenerateButton.disabled = !state.latestReply || state.loading;
    if (archiveButton) {
      archiveButton.disabled = !state.latestReply || state.loading;
      archiveButton.innerHTML =
        stageState.status === "archived"
          ? '<span aria-hidden="true">✓</span> 更新归档'
          : '<span aria-hidden="true">✓</span> 归档本阶段';
    }

    if (archiveButton && !archiveButton.dataset.bound) {
      archiveButton.dataset.bound = "1";
      archiveButton.addEventListener("click", archiveCurrentStage);
    }
    if (regenerateButton && !regenerateButton.dataset.bound) {
      regenerateButton.dataset.bound = "1";
      regenerateButton.addEventListener("click", regenerateCurrentStage);
    }
    if (copyButton && !copyButton.dataset.bound) {
      copyButton.dataset.bound = "1";
      copyButton.addEventListener("click", copyCurrentOutput);
    }
  }

  function renderSourceStrip() {
    const strip = el("source-strip");
    const list = el("source-list");
    if (!strip || !list) return;
    const sources = Array.isArray(state.latestSources) ? state.latestSources : [];
    const hasAnswer = Boolean(state.latestReply) || state.streaming;
    strip.hidden = !hasAnswer;
    if (!hasAnswer) {
      list.innerHTML = "";
      return;
    }
    if (!sources.length) {
      list.innerHTML =
        '<span class="source-empty">本次回答未引用知识库内容</span>';
      return;
    }
    list.innerHTML = sources
      .map((source, index) => {
        const label = sourceLabel(source);
        return `
          <button type="button" class="source-chip" data-source-index="${index}" title="${escapeHtml(label)}">
            <span class="source-chip-index">${index + 1}</span>
            <span class="source-chip-file">${escapeHtml(label)}</span>
          </button>
        `;
      })
      .join("");
    list.querySelectorAll(".source-chip").forEach((button) => {
      button.addEventListener("click", () => {
        openSourceModal(Number(button.dataset.sourceIndex));
      });
    });
  }

  function sourceHighlight(text, snippet) {
    const rawText = String(text || "");
    const rawSnippet = String(snippet || "").trim();
    if (!rawSnippet) return escapeHtml(rawText);
    const index = rawText.indexOf(rawSnippet);
    if (index < 0) return escapeHtml(rawText);
    return (
      escapeHtml(rawText.slice(0, index)) +
      "<mark>" +
      escapeHtml(rawText.slice(index, index + rawSnippet.length)) +
      "</mark>" +
      escapeHtml(rawText.slice(index + rawSnippet.length))
    );
  }

  function sourceContextBlock(label, chunk) {
    if (!chunk) return "";
    const locator = sourceLocator(chunk);
    return `
      <section class="source-context-block source-context-block--muted">
        <div class="source-context-head">
          <span>${escapeHtml(label)}</span>
          ${locator ? `<span>${escapeHtml(locator)}</span>` : ""}
        </div>
        <pre>${escapeHtml(chunk.text || "")}</pre>
      </section>
    `;
  }

  function renderSourceDetail(source, detail, errorMessage = "") {
    const body = el("source-modal-body");
    if (!body) return;
    const fileName = detail?.file_name || sourceFileName(source);
    const locator =
      sourceLocator(detail || {}) || sourceLocator(source);
    const score =
      typeof source.score === "number"
        ? `<span class="source-modal-score">相似度 ${(source.score * 100).toFixed(0)}%</span>`
        : "";
    const metaParts = [];
    if (detail?.sheet) metaParts.push(`工作表：${detail.sheet}`);
    if (detail?.page) metaParts.push(`页码：${detail.page}`);
    if (detail?.row) metaParts.push(`行号：${detail.row}`);
    if (detail?.paragraph) metaParts.push(`段落：${detail.paragraph}`);
    if (detail?.chunk_id || source.chunk_id) {
      metaParts.push(`切片：${detail?.chunk_id || source.chunk_id}`);
    }
    const content = detail?.text || source.snippet || "";
    const before = detail?.before
      ? sourceContextBlock("上一段", detail.before)
      : "";
    const after = detail?.after ? sourceContextBlock("下一段", detail.after) : "";
    body.innerHTML = `
      <div class="source-modal-head">
        <span class="source-modal-file">${escapeHtml(fileName)}</span>
        ${locator ? `<span class="source-modal-locator">${escapeHtml(locator)}</span>` : ""}
        ${score}
      </div>
      <div class="source-detail-meta">
        ${metaParts.map((part) => `<span>${escapeHtml(part)}</span>`).join("")}
      </div>
      ${errorMessage ? `<div class="source-detail-warning">${escapeHtml(errorMessage)}</div>` : ""}
      ${before}
      <section class="source-context-block source-context-block--current">
        <div class="source-context-head">
          <span>命中片段</span>
          <span>已高亮命中内容</span>
        </div>
        <pre>${sourceHighlight(content, source.snippet || detail?.snippet || "")}</pre>
      </section>
      ${after}
      <p class="source-detail-note">来源仅用于核实，不支持编辑原文。</p>
    `;
  }

  async function openSourceDetail(source) {
    if (!source) return;
    const body = el("source-modal-body");
    const modal = el("source-modal");
    if (!body || !modal) return;
    modal.hidden = false;
    body.innerHTML = `
      <div class="source-detail-loading">
        <div class="spinner" aria-hidden="true"></div>
        <span>正在读取来源上下文…</span>
      </div>
    `;

    const chunkId = sourceChunkId(source);
    if (!chunkId || !state.project) {
      renderSourceDetail(source, null, "该来源缺少切片 ID，只能展示命中片段。");
      return;
    }
    try {
      const data = await apiClient.request(
        `/api/source/${encodeURIComponent(state.project.id)}?chunk_id=${encodeURIComponent(chunkId)}`
      );
      renderSourceDetail(source, data.source || null);
    } catch (error) {
      renderSourceDetail(source, null, error.message || "来源上下文加载失败。");
    }
  }

  function openSourceModal(index) {
    openSourceDetail(state.latestSources[index]);
  }

  function closeSourceModal() {
    const modal = el("source-modal");
    if (modal) modal.hidden = true;
  }

  function renderHistory() {
    const stageState = currentStageState() || {};
    const messages = Array.isArray(stageState.conversation_history)
      ? stageState.conversation_history
      : [];
    const card = el("history-card");
    const count = el("history-count");
    const list = el("history-list");
    const clearButton = el("history-clear-btn");
    if (!card || !count || !list) return;

    card.hidden = false;
    count.textContent = `${messages.length} 条`;
    if (clearButton) clearButton.disabled = messages.length === 0 || state.loading;
    if (!messages.length) {
      list.innerHTML =
        '<p class="history-empty">本阶段暂无对话记录。发送消息后会在这里恢复显示。</p>';
      return;
    }
    list.innerHTML = messages
      .map((message, messageIndex) => {
        const isUser = message.role === "user";
        const content = isUser
          ? escapeHtml(message.content || "")
          : renderMarkdown(message.content || "");
        const sources = Array.isArray(message.sources) ? message.sources : [];
        const sourceHtml =
          !isUser && sources.length
            ? `<div class="history-sources">
                 ${sources
                   .map(
                     (source, sourceIndex) => `
                       <button type="button" class="history-source-chip"
                               data-message-index="${messageIndex}"
                               data-source-index="${sourceIndex}"
                               title="${escapeHtml(sourceLabel(source))}">
                         ${escapeHtml(sourceLabel(source))}
                       </button>
                     `
                   )
                   .join("")}
               </div>`
            : "";
        return `
          <div class="history-item ${isUser ? "history-item--user" : ""}">
            <div class="history-role">${isUser ? "顾问" : "Agent"}</div>
            <div class="history-content ${
              isUser ? "history-content--plain" : "markdown-body"
            }">${content}${sourceHtml}</div>
          </div>
        `;
      })
      .join("");
    list.querySelectorAll(".history-source-chip").forEach((button) => {
      button.addEventListener("click", () => {
        const message = messages[Number(button.dataset.messageIndex)];
        const source = (message?.sources || [])[Number(button.dataset.sourceIndex)];
        if (source) openSourceDetail(source);
      });
    });
  }

  function openHistoryClearConfirm() {
    if (!state.project) return;
    const stageState = currentStageState() || {};
    const history = stageState.conversation_history || [];
    if (!history.length) return;
    const card = el("history-card");
    if (card) card.open = true;
    const panel = el("history-clear-confirm");
    if (panel) panel.hidden = false;
  }

  function closeHistoryClearConfirm() {
    const panel = el("history-clear-confirm");
    if (panel) panel.hidden = true;
  }

  async function clearCurrentConversation() {
    if (!state.project || state.loading) return;
    const stage = state.activeStage;
    const button = el("history-clear-confirm-btn");
    if (button) {
      button.disabled = true;
      button.textContent = "清空中…";
    }
    try {
      const data = await apiClient.request("/api/conversation/clear", {
        method: "POST",
        body: JSON.stringify({ project_id: state.project.id, stage }),
      });
      state.project = data.project;
      state.chatDrafts[stage] = "";
      state.correctionDrafts[stage] = "";
      closeHistoryClearConfirm();
      renderAll();
      showToast("本阶段对话记录已清空，归档内容不受影响。", "success");
    } catch (error) {
      showToast(error.message, "error", 7000);
    } finally {
      if (button) {
        button.disabled = false;
        button.textContent = "清空记录";
      }
    }
  }

  function renderContextPanel() {
    const panel = el("context-panel");
    const summary = el("context-summary");
    const list = el("context-list");
    if (!panel || !summary || !list) return;

    const scrollTop = panel.scrollTop;
    if (!state.project) {
      summary.innerHTML = "";
      list.innerHTML = '<p class="context-empty">创建项目后，已归档内容会显示在这里。</p>';
      return;
    }

    summary.innerHTML = `
      <dl>
        <dt>客户</dt><dd>${escapeHtml(state.project.client_name || "未命名项目")}</dd>
        <dt>行业</dt><dd>${escapeHtml(state.project.industry || "待补充")}</dd>
        <dt>生产模式</dt><dd>${escapeHtml(state.project.production_mode || "待补充")}</dd>
        <dt>核心痛点</dt><dd>${escapeHtml(state.project.pain_points || "待补充")}</dd>
      </dl>
    `;

    list.innerHTML = state.stages
      .map((stage) => {
        const stageState = state.project.stages[String(stage.id)] || {};
        const archive = stageState.archive || {};
        const hasContent = Boolean(String(archive.content || "").trim());
        const status = stageState.status || "not_started";
        const isEditing = state.editingStage === stage.id;

        let body = "";
        if (isEditing) {
          body = `
            <textarea class="context-edit-area" data-edit-stage="${stage.id}" placeholder="编辑该阶段归档内容…">${escapeHtml(
              archive.content || ""
            )}</textarea>
            <div class="context-actions">
              <button type="button" class="btn btn-ghost context-cancel-btn" data-stage="${stage.id}">取消</button>
              <button type="button" class="btn btn-primary context-save-btn" data-stage="${stage.id}">保存归档</button>
            </div>
          `;
        } else if (hasContent) {
          body = `
            <div class="context-content markdown-body" title="归档于 ${escapeHtml(
              formatDate(archive.archived_at)
            )}">${renderMarkdown(archive.content)}</div>
            <div class="context-actions">
              <button type="button" class="btn btn-outline context-edit-btn" data-stage="${stage.id}">编辑</button>
            </div>
          `;
        } else {
          body = '<p class="context-empty">尚未归档。完成本阶段交付后点击“归档本阶段”。</p>';
        }

        const gates = Array.isArray(stageState.gate_confirmations)
          ? stageState.gate_confirmations
          : [];
        const latestGate = gates[gates.length - 1];
        let gateHtml = "";
        if (latestGate) {
          const payload = latestGate.payload || {};
          const detail =
            latestGate.gate === "constraint_priority"
              ? "排产约束优先级已确认"
              : `业务规则确认：${payload.confirmer_and_date || ""}`;
          gateHtml = `<div class="gate-record">✓ ${escapeHtml(
            detail
          )} · ${escapeHtml(formatDate(latestGate.confirmed_at))}</div>`;
        }

        return `
          <article class="context-block ${hasContent ? "" : "is-empty"}">
            <div class="context-block-head">
              <div class="context-block-title">
                <strong>阶段 0${stage.id} · ${escapeHtml(stage.name)}</strong>
                <span>${statusLabel(status)}${
                  archive.version ? ` · v${archive.version}` : ""
                }</span>
              </div>
              <span class="stage-state stage-state--${status}">${statusLabel(status)}</span>
            </div>
            <div class="context-block-body">${body}${gateHtml}</div>
          </article>
        `;
      })
      .join("");

    list.querySelectorAll(".context-edit-btn").forEach((button) => {
      button.addEventListener("click", () => {
        state.editingStage = Number(button.dataset.stage);
        renderContextPanel();
      });
    });
    list.querySelectorAll(".context-cancel-btn").forEach((button) => {
      button.addEventListener("click", () => {
        state.editingStage = null;
        renderContextPanel();
      });
    });
    list.querySelectorAll(".context-save-btn").forEach((button) => {
      button.addEventListener("click", () => {
        const stageId = Number(button.dataset.stage);
        const textarea = list.querySelector(`[data-edit-stage="${stageId}"]`);
        saveManualArchive(stageId, textarea ? textarea.value : "", button);
      });
    });

    panel.scrollTop = scrollTop;
  }

  /* ------------------------------------------------------------------------
     项目知识库
     ------------------------------------------------------------------------ */

  const DOC_STATUS_LABELS = {
    processing: "解析中",
    indexed: "已入库",
    failed: "失败",
  };

  function formatBytes(bytes) {
    const value = Number(bytes) || 0;
    if (value < 1024) return `${value} B`;
    if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
    return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  }

  async function fetchDocuments(projectId) {
    const data = await apiClient.request(`/api/documents/${encodeURIComponent(projectId)}`);
    return Array.isArray(data.documents) ? data.documents : [];
  }

  function renderKnowledgeBase() {
    const list = el("doc-list");
    const status = el("kb-upload-status");
    const button = el("kb-upload-btn");
    const topButton = el("upload-doc-btn");
    if (!list || !status || !button) return;

    if (button) {
      button.disabled = state.uploading || !state.project;
      button.textContent = state.uploading ? "上传中…" : "上传文档";
    }
    if (topButton) {
      topButton.disabled = state.uploading || !state.project;
    }

    if (state.uploading || state.uploadMessage) {
      status.hidden = false;
      status.className = `kb-upload-status ${
        state.uploadMessageType === "error" ? "is-error" : ""
      }`;
      status.innerHTML = state.uploading
        ? `<span class="mini-spinner"></span>${escapeHtml(state.uploadMessage || "正在解析并建立索引…")}`
        : escapeHtml(state.uploadMessage);
    } else {
      status.hidden = true;
      status.textContent = "";
    }

    if (!state.project) {
      list.innerHTML = "";
      return;
    }
    if (!state.documents.length) {
      list.innerHTML =
        '<p class="kb-empty">还没有文档。上传排产 Excel、调研 DOCX 或数字版 PDF 后，问答会自动检索并标注来源。</p>';
      return;
    }

    list.innerHTML = state.documents
      .map((doc) => {
        const docStatus = doc.status || "failed";
        const summary = String(doc.summary || "").trim();
        const locator = [
          `阶段 ${doc.stage || "-"}`,
          `${doc.chunks_count || 0} 个切片`,
          formatBytes(doc.size_bytes),
        ].join(" · ");
        return `
          <article class="doc-item">
            <div class="doc-item-head">
              <span class="doc-name" title="${escapeHtml(doc.file || "")}">${escapeHtml(
                doc.file || "未知文件"
              )}</span>
              <span class="doc-status doc-status--${docStatus}">${
                DOC_STATUS_LABELS[docStatus] || docStatus
              }</span>
            </div>
            <p class="doc-meta">${escapeHtml(locator)}</p>
            ${
              doc.error
                ? `<p class="doc-error">${escapeHtml(doc.error.message || "处理失败")}</p>`
                : ""
            }
            ${
              summary
                ? `<details class="doc-summary"><summary>查看解析摘要</summary><div class="markdown-body">${renderMarkdown(
                    summary
                  )}</div></details>`
                : ""
            }
          </article>
        `;
      })
      .join("");
  }

  function triggerDocumentUpload() {
    if (!state.project) {
      showToast("请先创建或选择项目。", "warning");
      return;
    }
    if (state.uploading) return;
    el("file-input")?.click();
  }

  async function uploadDocument(file) {
    if (!state.project || !file) return;
    const extension = `.${String(file.name).split(".").pop().toLowerCase()}`;
    if (![".docx", ".xlsx", ".pdf"].includes(extension)) {
      showToast("仅支持 DOCX、XLSX、PDF 文件。", "warning");
      return;
    }
    if (file.size > 20 * 1024 * 1024) {
      showToast("文件超过 20 MiB，请压缩或拆分后上传。", "warning");
      return;
    }

    state.uploading = true;
    state.uploadMessage = `正在解析《${file.name}》并建立索引，首次加载嵌入模型可能需要更久…`;
    state.uploadMessageType = "info";
    renderKnowledgeBase();

    const formData = new FormData();
    formData.append("file", file);
    formData.append("project_id", state.project.id);
    formData.append("stage", String(state.activeStage));

    try {
      const data = await apiClient.request("/api/upload", {
        method: "POST",
        body: formData,
        timeoutMs: 300_000,
      });
      state.uploading = false;
      state.uploadMessage = `《${file.name}》已入库：${data.chunks_count} 个切片。`;
      state.uploadMessageType = "info";
      showToast(`文档已入库，共 ${data.chunks_count} 个切片。`, "success", 5000);
      if (Array.isArray(data.warnings) && data.warnings.length) {
        showToast(data.warnings[0].message, "warning", 8000);
      }
      try {
        state.documents = await fetchDocuments(state.project.id);
      } catch {
        state.documents = [];
      }
      renderKnowledgeBase();
      // 上传后让对话区出现“文档问答 / 阶段生成”切换。
      renderInputZone();
    } catch (error) {
      state.uploading = false;
      state.uploadMessage = `上传失败：${error.message}`;
      state.uploadMessageType = "error";
      showToast(error.message, "error", 8000);
      try {
        state.documents = await fetchDocuments(state.project.id);
      } catch {
        state.documents = [];
      }
      renderKnowledgeBase();
    } finally {
      const input = el("file-input");
      if (input) input.value = "";
    }
  }

  function handleFileSelected(event) {
    const file = event.target.files?.[0];
    if (file) uploadDocument(file);
  }

  function postLaunchUnlocked() {
    if (!state.project) return false;
    const stage5 = state.project.stages?.["5"] || {};
    return (
      stage5.status === "archived" ||
      Boolean(state.project.post_launch?.unlocked_at)
    );
  }

  function updateViewUrl() {
    const url = new URL(window.location.href);
    if (state.view === "tracking") url.searchParams.set("view", "tracking");
    else url.searchParams.delete("view");
    window.history.replaceState({}, "", url.toString());
  }

  function renderPostLaunchAvailability() {
    state.postLaunchUnlocked = postLaunchUnlocked();
    const nav = el("post-launch-nav");
    if (nav) {
      nav.hidden = !state.project || !state.postLaunchUnlocked;
      nav.classList.toggle("is-active", state.view === "tracking");
    }
    if (state.view === "tracking" && !state.postLaunchUnlocked) {
      state.view = "stage";
    }
  }

  function setView(view) {
    if (view === "tracking" && !postLaunchUnlocked()) return;
    state.view = view;
    updateViewUrl();
    renderAll();
  }

  function renderView() {
    const stage = el("stage-workspace");
    const post = el("post-launch-view");
    const empty = el("empty-state");
    const tracking = Boolean(
      state.project && state.view === "tracking" && state.postLaunchUnlocked
    );
    if (!state.project) {
      if (empty) empty.hidden = false;
      if (stage) stage.hidden = true;
      if (post) post.hidden = true;
      return;
    }
    if (empty) empty.hidden = true;
    if (stage) stage.hidden = tracking;
    if (post) post.hidden = !tracking;
    if (tracking) renderPostLaunch();
  }

  function issueSeverityLabel(value) {
    return { high: "高", medium: "中", low: "低" }[value] || "中";
  }

  function issueStatusLabel(value) {
    return {
      pending: "待处理",
      in_progress: "处理中",
      resolved: "已解决",
    }[value] || "待处理";
  }

  function renderIssueStats() {
    const target = el("post-launch-stats");
    if (!target) return;
    const high = state.issues.filter((issue) => issue.severity === "high").length;
    const pending = state.issues.filter((issue) => issue.status === "pending").length;
    const resolved = state.issues.filter((issue) => issue.status === "resolved").length;
    const cards = [
      ["问题总数", state.issues.length],
      ["高严重", high],
      ["待处理", pending],
      ["已解决", resolved],
    ];
    target.innerHTML = cards
      .map(
        ([label, value]) =>
          `<div class="issue-stat"><span>${escapeHtml(label)}</span><strong>${value}</strong></div>`
      )
      .join("");
  }

  function renderIssueList() {
    const list = el("issue-list");
    const count = el("issue-count");
    if (!list) return;
    if (count) count.textContent = `${state.issues.length} 条`;
    if (!state.issues.length) {
      list.innerHTML = '<p class="issue-empty">暂无问题记录。</p>';
      return;
    }
    list.innerHTML = state.issues
      .map((issue) => {
        const options = ["pending", "in_progress", "resolved"]
          .map(
            (status) =>
              `<option value="${status}" ${
                issue.status === status ? "selected" : ""
              }>${issueStatusLabel(status)}</option>`
          )
          .join("");
        return `
          <div class="issue-item" data-issue-id="${escapeHtml(issue.issue_id)}">
            <div class="issue-item-main">
              <p class="issue-description">${escapeHtml(issue.description)}</p>
              <div class="issue-meta">
                <span>${escapeHtml(formatDate(issue.created_at))}</span>
              </div>
            </div>
            <span class="issue-severity issue-severity--${escapeHtml(issue.severity)}">
              ${escapeHtml(issueSeverityLabel(issue.severity))}
            </span>
            <select class="issue-status-select" data-issue-status="${escapeHtml(issue.issue_id)}"
                    data-previous="${escapeHtml(issue.status)}" aria-label="问题状态">
              ${options}
            </select>
          </div>
        `;
      })
      .join("");
    list.querySelectorAll("[data-issue-status]").forEach((select) => {
      select.addEventListener("change", () =>
        updateIssueStatus(select.dataset.issueStatus, select.value, select)
      );
    });
  }

  function renderPostLaunch() {
    renderIssueStats();
    renderIssueList();
    const reply = el("issue-agent-reply");
    if (reply) {
      if (state.issueAnalysis) {
        reply.hidden = false;
        reply.innerHTML = renderMarkdown(state.issueAnalysis);
      } else {
        reply.hidden = true;
        reply.innerHTML = "";
      }
    }
    if (!state.issuesLoaded && !state.issueLoading) {
      loadIssues();
    }
  }

  async function loadIssues() {
    if (!state.project || state.issueLoading) return;
    state.issueLoading = true;
    try {
      const data = await apiClient.request(
        `/api/issues/${encodeURIComponent(state.project.id)}`
      );
      state.issues = Array.isArray(data.issues) ? data.issues : [];
      state.issuesLoaded = true;
    } catch (error) {
      state.issuesLoaded = true;
      state.issues = [];
      showToast(error.message || "上线问题加载失败。", "error", 6000);
    } finally {
      state.issueLoading = false;
      if (state.view === "tracking") renderPostLaunch();
    }
  }

  function openIssueModal() {
    const modal = el("issue-modal");
    if (!modal) return;
    el("issue-form")?.reset();
    modal.hidden = false;
    document.body.style.overflow = "hidden";
    window.setTimeout(() => el("issue-description")?.focus(), 20);
  }

  function closeIssueModal() {
    const modal = el("issue-modal");
    if (modal) modal.hidden = true;
    document.body.style.overflow = "";
  }

  async function submitIssue(event) {
    event.preventDefault();
    if (!state.project) return;
    const description = (el("issue-description")?.value || "").trim();
    if (!description) {
      showToast("请填写问题描述。", "warning");
      el("issue-description")?.focus();
      return;
    }
    const button = el("issue-submit");
    if (button) {
      button.disabled = true;
      button.textContent = "保存中…";
    }
    try {
      const data = await apiClient.request(
        `/api/issues/${encodeURIComponent(state.project.id)}`,
        {
          method: "POST",
          body: JSON.stringify({
            description,
            severity: el("issue-severity")?.value || "medium",
            status: el("issue-status")?.value || "pending",
          }),
        }
      );
      state.issues.unshift(data.issue);
      state.issuesLoaded = true;
      closeIssueModal();
      renderPostLaunch();
      showToast("问题已记录。", "success");
    } catch (error) {
      showToast(error.message || "问题保存失败。", "error", 6000);
    } finally {
      if (button) {
        button.disabled = false;
        button.textContent = "保存问题";
      }
    }
  }

  async function updateIssueStatus(issueId, status, select) {
    if (!state.project) return;
    if (select) select.disabled = true;
    try {
      const data = await apiClient.request(
        `/api/issues/${encodeURIComponent(state.project.id)}/${encodeURIComponent(issueId)}`,
        {
          method: "PATCH",
          body: JSON.stringify({ status }),
        }
      );
      const index = state.issues.findIndex((issue) => issue.issue_id === issueId);
      if (index >= 0) state.issues[index] = data.issue;
      if (select) select.dataset.previous = status;
      renderPostLaunch();
      showToast("问题状态已更新。", "success");
    } catch (error) {
      showToast(error.message || "状态更新失败。", "error", 6000);
      if (select) select.value = select.dataset.previous || select.value;
    } finally {
      if (select && document.contains(select)) select.disabled = false;
    }
  }

  async function analyzeIssues(prompt) {
    if (!state.project || state.issueAnalyzing) return;
    const text = String(prompt || "").trim();
    if (!text) {
      showToast("请输入要分析的问题。", "warning");
      return;
    }
    state.issueAnalyzing = true;
    const button = el("issue-agent-submit");
    if (button) {
      button.disabled = true;
      button.textContent = "分析中…";
    }
    try {
      const data = await apiClient.request("/api/chat/issues", {
        method: "POST",
        body: JSON.stringify({
          project_id: state.project.id,
          prompt: text,
        }),
      });
      state.issueAnalysis = data.reply || "";
      renderPostLaunch();
    } catch (error) {
      showToast(error.message || "Agent 分析失败。", "error", 6000);
    } finally {
      state.issueAnalyzing = false;
      if (button) {
        button.disabled = false;
        button.textContent = "生成分析";
      }
    }
  }

  function exportIssues() {
    if (!state.project) return;
    const client = String(state.project.client_name || "项目").replace(
      /[\\/:*?"<>|\r\n]+/g,
      "_"
    );
    const date = new Date().toISOString().slice(0, 10).replace(/-/g, "");
    apiClient
      .download(
        "/api/export/issues",
        { project_id: state.project.id },
        `${client}_上线问题跟踪_${date}.xlsx`
      )
      .then(() => showToast("问题跟踪表已开始下载。", "success"))
      .catch((error) => showToast(error.message || "导出失败。", "error"));
  }

  function renderAll() {
    if (state.project) {
      const active = Number(state.project.active_stage);
      if (!state.activeStage || !state.stages.some((stage) => stage.id === state.activeStage)) {
        state.activeStage = state.stages.some((stage) => stage.id === active)
          ? active
          : 1;
      }
    }

    renderShellState();
    renderTopbarProject();
    renderProjectCard();
    renderStageNav();
    renderPostLaunchAvailability();

    const emptyState = el("empty-state");
    const workspace = el("stage-workspace");
    if (!state.project) {
      if (emptyState) emptyState.hidden = false;
      if (workspace) workspace.hidden = true;
      renderKnowledgeBase();
      renderContextPanel();
      renderView();
      return;
    }

    if (emptyState) emptyState.hidden = true;
    if (workspace) workspace.hidden = false;
    renderStageHeading();
    renderTemplateContext();
    renderInputZone();
    renderOutput();
    renderHistory();
    renderKnowledgeBase();
    renderContextPanel();
    renderView();
    document.title = `${state.project.client_name || "APS 项目"} · APS 实施顾问 AI 工作台`;
  }

  /* ------------------------------------------------------------------------
     项目加载 / 创建
     ------------------------------------------------------------------------ */

  async function loadProject(projectId) {
    const data = await apiClient.request(`/api/context/${encodeURIComponent(projectId)}`);
    state.project = data.project;
    state.stages = data.stages || state.stages;
    state.activeStage = Number(data.project.active_stage) || 1;
    if (!state.stages.some((stage) => stage.id === state.activeStage)) {
      state.activeStage = 1;
    }
    state.editingStage = null;
    state.chatDrafts = {};
    state.correctionDrafts = {};
    state.answerModes = {};
    state.documents = [];
    state.uploading = false;
    state.uploadMessage = "";
    state.uploadMessageType = "info";
    state.streaming = false;
    state.streamingText = "";
    state.streamingSources = [];
    state.streamingStage = null;
    state.streamingUserInput = "";
    state.loading = false;
    state.view = "stage";
    state.issues = [];
    state.issuesLoaded = false;
    state.issueLoading = false;
    state.issueAnalysis = "";
    updateViewUrl();
    let documentError = null;
    try {
      state.documents = await fetchDocuments(projectId);
    } catch (error) {
      documentError = error;
    }
    resetAssistantForProject();
    renderAll();
    if (documentError) {
      showToast(`知识库加载失败：${documentError.message}`, "error", 7000);
    }
  }

  function openProjectModal() {
    const modal = el("project-modal");
    const form = el("project-form");
    const error = el("project-form-error");
    if (!modal || !form) return;
    form.reset();
    if (error) {
      error.hidden = true;
      error.textContent = "";
    }
    modal.hidden = false;
    window.setTimeout(() => el("modal-client-name")?.focus(), 60);
  }

  function closeProjectModal() {
    const modal = el("project-modal");
    if (modal) modal.hidden = true;
  }

  async function handleCreateProject(event) {
    event.preventDefault();
    const errorBox = el("project-form-error");
    const submitButton = el("modal-submit-btn");
    const payload = {
      client_name: el("modal-client-name")?.value.trim() || "",
      industry: el("modal-industry")?.value.trim() || "",
      production_mode: el("modal-production-mode")?.value || "",
      pain_points: el("modal-pain-points")?.value.trim() || "",
    };

    if (!payload.client_name) {
      if (errorBox) {
        errorBox.textContent = "请填写客户名称。";
        errorBox.hidden = false;
      }
      el("modal-client-name")?.focus();
      return;
    }

    if (errorBox) errorBox.hidden = true;
    if (submitButton) {
      submitButton.disabled = true;
      submitButton.textContent = "创建中…";
    }

    try {
      const data = await apiClient.request("/api/project", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      closeProjectModal();
      const projectId = data.project_id || (data.project && data.project.id);
      if (!projectId) throw new Error("创建成功但未返回项目 ID。");
      window.location.href = `/workbench/${encodeURIComponent(projectId)}`;
    } catch (error) {
      if (errorBox) {
        errorBox.textContent = error.message;
        errorBox.hidden = false;
      }
      showToast(error.message, "error", 6000);
    } finally {
      if (submitButton) {
        submitButton.disabled = false;
        submitButton.textContent = "创建项目";
      }
    }
  }

  /* ------------------------------------------------------------------------
     阶段切换
     ------------------------------------------------------------------------ */

  async function switchStage(targetStage) {
    if (!state.project || state.loading || state.switching) return;
    if (!Number.isInteger(targetStage) || targetStage < 1 || targetStage > 5) return;
    if (state.view === "tracking") {
      state.view = "stage";
      updateViewUrl();
      renderAll();
    }
    if (targetStage === state.activeStage) return;

    const previousStage = state.stages.find((stage) => stage.id === targetStage - 1);
    if (previousStage) {
      const previousState = state.project.stages[String(previousStage.id)] || {};
      if (previousState.status !== "archived") {
        const confirmed = window.confirm("前一阶段未完成，是否继续？");
        if (!confirmed) return;
      }
    }

    state.switching = true;
    try {
      const data = await apiClient.request("/api/stage/switch", {
        method: "POST",
        body: JSON.stringify({
          project_id: state.project.id,
          stage: targetStage,
        }),
      });
      state.project = data.project;
      state.activeStage = targetStage;
      state.editingStage = null;
      state.chatDrafts[targetStage] = "";
      state.correctionDrafts[targetStage] = "";
      closeHistoryClearConfirm();
      renderAll();
      const workspace = document.querySelector(".workspace");
      if (workspace) workspace.scrollTo({ top: 0, behavior: "auto" });
      if (window.innerWidth <= 900) window.scrollTo({ top: 0, behavior: "auto" });
    } catch (error) {
      showToast(error.message, "error", 6000);
    } finally {
      state.switching = false;
    }
  }

  /* ------------------------------------------------------------------------
     对话 / 纠偏 / 归档
     ------------------------------------------------------------------------ */

  function setStageBusy(busy) {
    state.loading = busy;
    const outputCard = document.querySelector(".output-card");
    if (outputCard) outputCard.setAttribute("aria-busy", String(busy));

    [
      "archive-btn",
      "regenerate-btn",
      "copy-output-btn",
      "chat-send-btn",
      "stage-one-submit",
      "upload-doc-btn",
      "kb-upload-btn",
    ].forEach((id) => {
      const button = el(id);
      if (button) button.disabled = busy;
    });
    ["chat-input", "correction-input"].forEach((id) => {
      const field = el(id);
      if (field) field.disabled = busy;
    });
    const form = el("stage-one-form");
    if (form) {
      form.querySelectorAll("input, select, textarea, button").forEach((field) => {
        field.disabled = busy;
      });
    }
  }

  async function sendMessage(
    text,
    { correction = "", answerMode = "", regenerate = false } = {}
  ) {
    if (!state.project) {
      showToast("请先创建项目。", "warning");
      return false;
    }
    if (state.loading) return false;

    const input = String(text || "").trim();
    const correctionText = String(correction || "").trim();
    if (!regenerate && !input && !correctionText) {
      showToast("请输入内容，或填写纠偏意见后再重新生成。", "warning");
      return false;
    }
    if (regenerate && !input && !state.lastUserInput) {
      showToast("当前阶段没有可重新生成的用户问题。", "warning");
      return false;
    }

    const stage = state.activeStage;
    const effectiveUserInput = input || state.lastUserInput;
    const modeForRequest = answerMode || effectiveAnswerMode();
    state.loading = true;
    state.streaming = true;
    state.streamingText = "";
    state.streamingSources = [];
    state.streamingStage = stage;
    state.streamingUserInput = effectiveUserInput;
    setStageBusy(true);
    renderOutput();

    let doneReceived = false;
    try {
      await apiClient.stream(
        "/api/chat/stream",
        {
          project_id: state.project.id,
          message: effectiveUserInput,
          stage,
          answer_mode: modeForRequest,
          correction: correctionText,
          regenerate,
        },
        {
          meta: () => {
            // meta 目前只用于调试；阶段与模式已在请求中确定。
          },
          sources: (data) => {
            state.streamingSources = Array.isArray(data.sources) ? data.sources : [];
            renderOutput();
          },
          delta: (data) => {
            state.streamingText += String(data.text || "");
            renderOutput();
          },
          done: (data) => {
            doneReceived = true;
            state.project = data.project;
            state.chatDrafts[stage] = "";
            state.correctionDrafts[stage] = "";
            state.streaming = false;
            state.streamingText = "";
            state.streamingSources = [];
            state.streamingStage = null;
            state.streamingUserInput = "";
            state.loading = false;
            renderAll();

            if (data.warning) {
              showToast(data.warning, "warning", 9000);
            } else if (data.mode === "no_context") {
              showToast("本轮没有检索到相关文档片段。", "info", 4500);
            }

            window.setTimeout(() => {
              el("agent-output")?.scrollIntoView({
                behavior: "smooth",
                block: "nearest",
              });
            }, 60);
          },
          error: (data) => {
            throw new Error(data.message || "对话生成失败。");
          },
        },
        { timeoutMs: 180_000 }
      );

      if (!doneReceived) {
        throw new Error("流式连接意外结束，请重试。");
      }
      return true;
    } catch (error) {
      state.streaming = false;
      state.streamingText = "";
      state.streamingSources = [];
      state.streamingStage = null;
      state.streamingUserInput = "";
      state.loading = false;
      renderAll();
      showToast(error.message, "error", 7000);
      return false;
    } finally {
      setStageBusy(false);
    }
  }

  function submitChatInput() {
    if (state.loading) return;
    const input = el("chat-input");
    if (!input) return;
    const text = input.value.trim();
    if (!text) {
      showToast("请先输入内容。", "warning");
      return;
    }
    sendMessage(text);
  }

  async function handleStageFormSubmit(event) {
    event.preventDefault();
    if (state.loading || !state.project) return;

    const payload = {
      client_name: el("stage-client-name")?.value.trim() || "",
      industry: el("stage-industry")?.value.trim() || "",
      production_mode: el("stage-production-mode")?.value || "",
      pain_points: el("stage-pain-points")?.value.trim() || "",
    };
    if (!payload.client_name) {
      showToast("客户名称不能为空。", "warning");
      el("stage-client-name")?.focus();
      return;
    }

    const submitButton = el("stage-one-submit");
    if (submitButton) {
      submitButton.disabled = true;
      submitButton.textContent = "保存中…";
    }

    try {
      const data = await apiClient.request(`/api/project/${encodeURIComponent(state.project.id)}`, {
        method: "PUT",
        body: JSON.stringify(payload),
      });
      state.project = data.project;
      renderTopbarProject();
      renderProjectCard();
      const summary = [
        "请基于以下客户信息，开展阶段一现状调研与需求诊断：",
        `客户名称：${payload.client_name}`,
        `行业：${payload.industry || "待确认"}`,
        `生产模式：${payload.production_mode || "待确认"}`,
        `核心痛点：${payload.pain_points || "待确认"}`,
        "请输出调研摘要、初步诊断、APS 关键约束、高频异常场景、需求优先级和待确认清单。",
      ].join("\n");
      const sent = await sendMessage(summary, { answerMode: "stage" });
      if (!sent && submitButton && document.contains(submitButton)) {
        submitButton.disabled = false;
        submitButton.textContent = "保存并生成诊断";
      }
    } catch (error) {
      showToast(error.message, "error", 7000);
      if (submitButton && document.contains(submitButton)) {
        submitButton.disabled = false;
        submitButton.textContent = "保存并生成诊断";
      }
    }
  }

  async function regenerateCurrentStage() {
    if (state.loading) return;
    if (!state.latestReply) {
      showToast("当前阶段还没有可重新生成的 Agent 输出。", "warning");
      return;
    }
    const correction = el("correction-input")?.value.trim() || "";
    const baseInput =
      state.lastUserInput || "请基于当前项目上下文和已归档内容，生成本阶段完整交付物。";
    await sendMessage(baseInput, {
      correction,
      answerMode: "stage",
      regenerate: true,
    });
  }

  function exportFallbackName(format) {
    const stage = state.stages.find((item) => item.id === state.activeStage);
    const client = String(state.project?.client_name || "未命名客户").replace(
      /[\\/:*?"<>|\r\n]+/g,
      "_"
    );
    const stageName = String(stage?.name || `阶段${state.activeStage}`).replace(
      /[\\/:*?"<>|\r\n]+/g,
      "_"
    );
    return `${client}_${stageName}_交付物.${format === "word" ? "docx" : "xlsx"}`;
  }

  function closeExportConfirm() {
    const modal = el("export-confirm-modal");
    if (modal) modal.hidden = true;
    state.exportPendingFormat = "";
  }

  function openExportConfirm(format) {
    if (!state.project) return;
    const stageState = currentStageState() || {};
    if (stageState.status !== "archived") {
      showToast("请先归档当前阶段内容，再导出", "warning");
      return;
    }
    if (!String(stageState.archive?.content || "").trim()) {
      showToast("当前阶段没有可导出的归档内容。", "warning");
      return;
    }
    state.exportPendingFormat = format;
    const modal = el("export-confirm-modal");
    if (modal) modal.hidden = false;
  }

  async function performExport(format) {
    if (!state.project || state.exporting) return;
    const button = el(format === "word" ? "export-word-btn" : "export-excel-btn");
    state.exporting = true;
    if (button) button.disabled = true;
    try {
      await apiClient.download(
        format === "word" ? "/api/export/word" : "/api/export/excel",
        { project_id: state.project.id, stage: state.activeStage },
        exportFallbackName(format)
      );
      showToast("导出文件已开始下载。", "success");
    } catch (error) {
      showToast(error.message || "导出失败，请重试。", "error", 7000);
    } finally {
      state.exporting = false;
      if (button) button.disabled = false;
    }
  }

  async function confirmExport() {
    const format = state.exportPendingFormat;
    closeExportConfirm();
    if (format) await performExport(format);
  }

  function closeArchiveGate() {
    const modal = el("archive-gate-modal");
    if (modal) modal.hidden = true;
    state.archiveGate = null;
  }

  function updateArchiveGateState() {
    const gate = state.archiveGate;
    const confirm = el("archive-gate-confirm");
    if (!gate || !confirm) return;
    if (gate.stage === 1) {
      confirm.disabled = !el("archive-gate-checkbox")?.checked;
    } else if (gate.stage === 3) {
      confirm.disabled = !String(el("archive-gate-input")?.value || "").trim();
    } else {
      confirm.disabled = false;
    }
  }

  function openArchiveGate(stage, content, mode, button) {
    const modal = el("archive-gate-modal");
    if (!modal) return;
    state.archiveGate = { stage, content, mode, button };
    const stage1 = el("archive-gate-stage1");
    const stage3 = el("archive-gate-stage3");
    if (stage1) stage1.hidden = stage !== 1;
    if (stage3) stage3.hidden = stage !== 3;
    const checkbox = el("archive-gate-checkbox");
    if (checkbox) checkbox.checked = false;
    const input = el("archive-gate-input");
    if (input) input.value = "";
    const title = el("archive-gate-title");
    if (title) title.textContent = `阶段${stage}归档前确认`;
    modal.hidden = false;
    updateArchiveGateState();
  }

  async function performArchive(stage, content, confirmations, mode, button) {
    if (!state.project) return;
    const originalText = button?.textContent || "";
    if (button) {
      button.disabled = true;
      button.textContent = mode === "manual" ? "保存中…" : "归档中…";
    }
    try {
      const data = await apiClient.request("/api/archive", {
        method: "POST",
        body: JSON.stringify({
          project_id: state.project.id,
          stage,
          content,
          confirmations: confirmations || {},
        }),
      });
      state.project = data.project;
      state.editingStage = null;
      state.correctionDrafts[stage] = "";
      if (stage === 5) {
        state.issuesLoaded = false;
        state.view = "stage";
      }
      renderAll();
      showToast(
        mode === "manual"
          ? `阶段${stage}的归档内容已更新。`
          : "已归档，后续阶段会自动引用本阶段内容。",
        "success"
      );
    } catch (error) {
      showToast(error.message, "error", 7000);
      if (button && document.contains(button)) {
        button.disabled = false;
        button.textContent = originalText;
      }
    }
  }

  async function confirmArchiveGate() {
    const gate = state.archiveGate;
    if (!gate) return;
    let confirmations = {};
    if (gate.stage === 1) {
      if (!el("archive-gate-checkbox")?.checked) return;
      confirmations = { constraint_priority_confirmed: true };
    } else if (gate.stage === 3) {
      const value = String(el("archive-gate-input")?.value || "").trim();
      if (!value) return;
      confirmations = { confirmer_and_date: value };
    }
    const { stage, content, mode, button } = gate;
    closeArchiveGate();
    await performArchive(stage, content, confirmations, mode, button);
  }

  async function archiveCurrentStage() {
    if (!state.project || state.loading) return;
    const content = state.latestReply;
    if (!content) {
      showToast("当前阶段还没有可归档的 Agent 输出。", "warning");
      return;
    }
    const stage = state.activeStage;
    if (stage === 1 || stage === 3) {
      openArchiveGate(stage, content, "current", el("archive-btn"));
      return;
    }
    await performArchive(stage, content, null, "current", el("archive-btn"));
  }

  async function saveManualArchive(stageId, content, button) {
    if (!state.project) return;
    const value = String(content || "").trim();
    if (!value) {
      showToast("归档内容不能为空。", "warning");
      return;
    }
    if (stageId === 1 || stageId === 3) {
      openArchiveGate(stageId, value, "manual", button);
      return;
    }
    await performArchive(stageId, value, null, "manual", button);
  }

  async function copyCurrentOutput() {
    if (!state.latestReply) return;
    const text = state.latestReply;
    try {
      await navigator.clipboard.writeText(text);
      showToast("当前 Agent 输出已复制。", "success");
    } catch {
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.select();
      try {
        document.execCommand("copy");
        showToast("当前 Agent 输出已复制。", "success");
      } catch {
        showToast("复制失败，请手动选择输出内容。", "error");
      } finally {
        textarea.remove();
      }
    }
  }

  /* ------------------------------------------------------------------------
     全局 AI 小助手（独立于五阶段历史）
     ------------------------------------------------------------------------ */

  const ASSISTANT_ROBOT_SVG = `
    <svg class="assistant-robot" viewBox="0 0 32 32" fill="none" aria-hidden="true">
      <path d="M16 7V4" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
      <circle cx="16" cy="3" r="1.5" fill="currentColor"/>
      <rect x="5" y="8" width="22" height="16" rx="5.5" stroke="currentColor" stroke-width="2"/>
      <circle cx="12" cy="15" r="1.4" fill="currentColor"/>
      <circle cx="20" cy="15" r="1.4" fill="currentColor"/>
      <path d="M12 19.5h8" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
      <path d="M9 24v3M23 24v3" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
    </svg>`;

  function updateAssistantProjectLabel() {
    const label = el("assistant-project-label");
    if (!label) return;
    label.textContent = state.project
      ? `${state.project.client_name || "未命名项目"} · 历史独立保存`
      : "未选择项目";
  }

  function openAssistant() {
    if (!state.project) {
      showToast("请先创建或选择项目，再使用 AI 小助手。", "warning");
      return;
    }
    updateAssistantProjectLabel();
    state.assistantOpen = true;
    document.body.classList.add("assistant-open");
    const drawer = el("assistant-drawer");
    if (drawer) drawer.hidden = false;
    el("assistant-fab")?.setAttribute("aria-expanded", "true");
    renderAssistantMessages();
    if (
      state.assistantLoadedProject !== state.project.id &&
      !state.assistantLoading
    ) {
      loadAssistantHistory();
    }
    window.setTimeout(() => el("assistant-input")?.focus(), 80);
  }

  function closeAssistant() {
    state.assistantOpen = false;
    document.body.classList.remove("assistant-open");
    const drawer = el("assistant-drawer");
    if (drawer) drawer.hidden = true;
    el("assistant-fab")?.setAttribute("aria-expanded", "false");
  }

  function resetAssistantForProject() {
    if (state.assistantAbortController) {
      state.assistantAbortController.abort();
      state.assistantAbortController = null;
    }
    state.assistantMessages = [];
    state.assistantLoadedProject = null;
    state.assistantLoading = false;
    state.assistantStreaming = false;
    closeAssistant();
  }

  async function loadAssistantHistory() {
    if (!state.project) return;
    const projectId = state.project.id;
    state.assistantLoadedProject = projectId;
    state.assistantLoading = true;
    state.assistantMessages = [];
    renderAssistantMessages();
    try {
      const data = await apiClient.request(
        `/api/assistant/history/${encodeURIComponent(projectId)}`
      );
      if (!state.project || state.project.id !== projectId) return;
      state.assistantMessages = Array.isArray(data.messages)
        ? data.messages
        : [];
    } catch (error) {
      if (state.project && state.project.id === projectId) {
        showToast(`助手历史加载失败：${error.message}`, "error", 6000);
      }
    } finally {
      if (state.project && state.project.id === projectId) {
        state.assistantLoading = false;
        renderAssistantMessages();
      }
    }
  }

  function assistantModeLabel(message) {
    if (message.error) return "回答失败";
    if (message.grounded) return "基于项目知识库";
    if (message.mode === "demo-fallback") return "本地兜底 · 通用回答";
    if (message.mode === "demo") return "本地演示 · 通用回答";
    return "通用回答";
  }

  function assistantMessageHtml(message, index) {
    if (message.role === "user") {
      return `
        <div class="assistant-msg assistant-msg--user">
          <div class="assistant-msg-main">
            <div class="assistant-bubble">${escapeHtml(message.content || "")}</div>
          </div>
        </div>
      `;
    }

    const streaming = Boolean(message.streaming);
    const content = String(message.content || "");
    const body = streaming && !content
      ? '<div class="assistant-typing" aria-label="正在生成"><span></span><span></span><span></span></div>'
      : `<div class="markdown-body">${renderMarkdown(content)}</div>${
          streaming ? '<span class="assistant-cursor" aria-hidden="true"></span>' : ""
        }`;
    const sources = Array.isArray(message.sources) ? message.sources : [];
    const sourceHtml = sources.length
      ? `<div class="assistant-message-sources">
          ${sources
            .map((source, sourceIndex) => {
              const locator = sourceLocator(source);
              const title = `${source.file || "未知文件"}${locator ? ` · ${locator}` : ""}`;
              return `
                <button type="button" class="source-chip" data-assistant-msg="${index}" data-assistant-source="${sourceIndex}" title="${escapeHtml(title)}">
                  <span class="source-chip-index">${sourceIndex + 1}</span>
                  <span class="source-chip-file">${escapeHtml(source.file || "未知文件")}</span>
                </button>
              `;
            })
            .join("")}
        </div>`
      : "";

    const modeClass = message.grounded
      ? "assistant-mode-badge--kb"
      : "assistant-mode-badge--general";
    return `
      <div class="assistant-msg assistant-msg--assistant">
        <div class="assistant-msg-avatar" aria-hidden="true">${ASSISTANT_ROBOT_SVG}</div>
        <div class="assistant-msg-main">
          <div class="assistant-bubble ${message.error ? "assistant-error" : ""}">${body}</div>
          ${sourceHtml}
          ${
            message.warning
              ? `<div class="assistant-warning">${escapeHtml(message.warning)}</div>`
              : ""
          }
          <div class="assistant-meta">
            <span class="assistant-mode-badge ${modeClass}">${assistantModeLabel(message)}</span>
            ${message.ts ? `<span>${escapeHtml(formatDate(message.ts))}</span>` : ""}
          </div>
        </div>
      </div>
    `;
  }

  function renderAssistantMessages() {
    const box = el("assistant-messages");
    if (!box) return;
    updateAssistantProjectLabel();

    if (state.assistantLoading) {
      box.innerHTML = `
        <div class="assistant-welcome">
          <div class="assistant-welcome-icon">${ASSISTANT_ROBOT_SVG}</div>
          <div class="assistant-typing" aria-label="正在加载历史"><span></span><span></span><span></span></div>
          <p>正在加载独立助手历史…</p>
        </div>
      `;
      return;
    }

    if (!state.assistantMessages.length) {
      box.innerHTML = `
        <div class="assistant-welcome">
          <div class="assistant-welcome-icon">${ASSISTANT_ROBOT_SVG}</div>
          <h3>随时问我</h3>
          <p>我会优先检索当前项目知识库；没有命中时转通用模型回答。这里的历史与五阶段工作流完全分开保存。</p>
          <div class="assistant-suggestions">
            <button type="button" data-assistant-suggestion="这个项目目前最关键的排产约束是什么？">这个项目目前最关键的排产约束是什么？</button>
            <button type="button" data-assistant-suggestion="帮我梳理下一步实施动作">帮我梳理下一步实施动作</button>
            <button type="button" data-assistant-suggestion="项目文档里有哪些关键参数？">项目文档里有哪些关键参数？</button>
          </div>
        </div>
      `;
    } else {
      box.innerHTML = state.assistantMessages
        .map((message, index) => assistantMessageHtml(message, index))
        .join("");
    }

    box.querySelectorAll("[data-assistant-source]").forEach((button) => {
      button.addEventListener("click", () => {
        const message = state.assistantMessages[Number(button.dataset.assistantMsg)];
        const source = message?.sources?.[Number(button.dataset.assistantSource)];
        openSourceDetail(source);
      });
    });
    box.querySelectorAll("[data-assistant-suggestion]").forEach((button) => {
      button.addEventListener("click", () => {
        sendAssistantMessage(button.dataset.assistantSuggestion || "");
      });
    });
    box.scrollTop = box.scrollHeight;
  }

  function setAssistantBusy(busy) {
    const input = el("assistant-input");
    const button = el("assistant-send-btn");
    if (input) input.disabled = busy;
    if (button) {
      button.disabled = busy;
      button.textContent = busy ? "生成中…" : "发送";
    }
    el("assistant-drawer")?.setAttribute("aria-busy", String(busy));
  }

  function parseSseBlock(block) {
    let event = "message";
    const dataLines = [];
    for (const rawLine of block.split(/\r?\n/)) {
      if (rawLine.startsWith("event:")) {
        event = rawLine.slice(6).trim();
      } else if (rawLine.startsWith("data:")) {
        dataLines.push(rawLine.slice(5).trimStart());
      }
    }
    if (!dataLines.length) return null;
    try {
      return { event, data: JSON.parse(dataLines.join("\n")) };
    } catch {
      return null;
    }
  }

  async function streamSseRequest(url, payload, handlers, options = {}) {
    const { timeoutMs = 120_000, controller = null } = options;
    const activeController = controller || new AbortController();
    const timeout = window.setTimeout(() => activeController.abort(), timeoutMs);
    try {
      const response = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "text/event-stream",
        },
        body: JSON.stringify(payload),
        signal: activeController.signal,
      });

      if (!response.ok) {
        let message = `请求失败（HTTP ${response.status}）`;
        try {
          const errorBody = await response.json();
          message = errorBody.error || errorBody.message || message;
        } catch {
          // 保留默认错误信息
        }
        throw new Error(message);
      }
      if (!response.body) {
        throw new Error("浏览器不支持流式响应。");
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
        let boundary = buffer.indexOf("\n\n");
        while (boundary !== -1) {
          const block = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          const parsed = parseSseBlock(block);
          if (parsed && handlers[parsed.event]) {
            handlers[parsed.event](parsed.data);
          }
          boundary = buffer.indexOf("\n\n");
        }
      }
    } catch (error) {
      if (error.name === "AbortError") {
        throw new Error("流式请求超时或已取消。");
      }
      if (error instanceof TypeError) {
        throw new Error("无法连接后端，请确认 Flask 服务已启动。");
      }
      throw error;
    } finally {
      window.clearTimeout(timeout);
    }
  }

  async function streamAssistantChat(payload, handlers) {
    const controller = new AbortController();
    state.assistantAbortController = controller;
    try {
      await apiClient.stream(
        "/api/assistant/chat",
        { ...payload, stream: true },
        handlers,
        { timeoutMs: 120_000, controller }
      );
    } finally {
      state.assistantAbortController = null;
    }
  }

  async function sendAssistantMessage(text) {
    if (!state.project) {
      showToast("请先创建或选择项目。", "warning");
      return;
    }
    if (state.assistantStreaming) return;
    if (state.assistantLoading) {
      showToast("正在加载助手历史，请稍候…", "info", 2200);
      return;
    }
    const messageText = String(text || "").trim();
    if (!messageText) {
      showToast("请输入问题。", "warning");
      return;
    }
    if (state.assistantLoadedProject !== state.project.id) {
      await loadAssistantHistory();
    }

    state.assistantMessages.push({
      role: "user",
      content: messageText,
      ts: new Date().toISOString(),
    });
    const placeholder = {
      role: "assistant",
      content: "",
      sources: [],
      grounded: false,
      streaming: true,
      mode: "",
      warning: "",
      error: "",
      ts: new Date().toISOString(),
    };
    state.assistantMessages.push(placeholder);
    state.assistantStreaming = true;
    setAssistantBusy(true);
    renderAssistantMessages();

    const input = el("assistant-input");
    if (input) input.value = "";
    let doneReceived = false;

    try {
      await streamAssistantChat(
        { project_id: state.project.id, message: messageText },
        {
          meta: (data) => {
            if (typeof data.grounded === "boolean") {
              placeholder.grounded = data.grounded;
            }
          },
          sources: (data) => {
            placeholder.sources = Array.isArray(data.sources) ? data.sources : [];
            renderAssistantMessages();
          },
          delta: (data) => {
            placeholder.content += String(data.text || "");
            renderAssistantMessages();
          },
          done: (data) => {
            doneReceived = true;
            placeholder.content = String(data.reply || placeholder.content);
            placeholder.sources = Array.isArray(data.sources)
              ? data.sources
              : placeholder.sources;
            placeholder.grounded = Boolean(data.grounded);
            placeholder.mode = data.mode || "";
            placeholder.warning = data.warning || "";
            placeholder.streaming = false;
            renderAssistantMessages();
            if (data.warning) {
              showToast(data.warning, "warning", 7000);
            }
          },
          error: (data) => {
            placeholder.error = data.message || "助手回答失败。";
            placeholder.streaming = false;
            renderAssistantMessages();
            showToast(placeholder.error, "error", 7000);
          },
        }
      );
      if (!doneReceived && !placeholder.error) {
        placeholder.error = "流式连接意外结束，请重试。";
        placeholder.streaming = false;
        renderAssistantMessages();
      }
    } catch (error) {
      placeholder.error = error.message;
      placeholder.streaming = false;
      renderAssistantMessages();
      showToast(error.message, "error", 7000);
    } finally {
      state.assistantStreaming = false;
      setAssistantBusy(false);
      renderAssistantMessages();
      if (!placeholder.error) {
        window.setTimeout(() => el("assistant-input")?.focus(), 40);
      }
    }
  }

  /* ------------------------------------------------------------------------
     全局交互
     ------------------------------------------------------------------------ */

  function toggleContext(forceValue) {
    state.contextCollapsed =
      typeof forceValue === "boolean" ? forceValue : !state.contextCollapsed;
    renderShellState();
    if (!state.contextCollapsed) {
      el("context-close-btn")?.focus();
    }
  }

  function hasUnarchivedWork() {
    if (!state.project) return false;
    const stageState = currentStageState();
    if (!stageState) return false;
    const draft = String(state.chatDrafts[state.activeStage] || "").trim();
    if (draft) return true;
    if (stageState.status === "archived") return false;
    const messages = Array.isArray(stageState.conversation_history)
      ? stageState.conversation_history
      : [];
    return Boolean(
      state.streaming ||
        messages.length > 0 ||
        String(state.latestReply || "").trim() ||
        String(stageState.initial_context?.content || "").trim()
    );
  }

  function closeLeaveConfirm() {
    const panel = el("leave-confirm");
    if (panel) panel.hidden = true;
  }

  function openLeaveConfirm() {
    const panel = el("leave-confirm");
    if (panel) panel.hidden = false;
  }

  function handleBackHome() {
    if (hasUnarchivedWork()) {
      openLeaveConfirm();
      return;
    }
    window.location.href = "/";
  }

  function bindGlobalEvents() {
    ["new-project-btn", "rail-new-project-btn", "empty-create-btn"].forEach((id) => {
      el(id)?.addEventListener("click", openProjectModal);
    });
    el("back-home-btn")?.addEventListener("click", handleBackHome);
    el("leave-cancel-btn")?.addEventListener("click", closeLeaveConfirm);
    el("leave-confirm-btn")?.addEventListener("click", () => {
      window.location.href = "/";
    });
    el("history-clear-btn")?.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      openHistoryClearConfirm();
    });
    el("history-clear-cancel-btn")?.addEventListener("click", closeHistoryClearConfirm);
    el("history-clear-confirm-btn")?.addEventListener(
      "click",
      clearCurrentConversation
    );
    el("export-word-btn")?.addEventListener("click", () => openExportConfirm("word"));
    el("export-excel-btn")?.addEventListener("click", () => openExportConfirm("excel"));
    el("export-confirm-close")?.addEventListener("click", closeExportConfirm);
    el("export-confirm-cancel")?.addEventListener("click", closeExportConfirm);
    el("export-confirm-btn")?.addEventListener("click", confirmExport);
    el("export-confirm-modal")?.addEventListener("click", (event) => {
      if (event.target === el("export-confirm-modal")) closeExportConfirm();
    });
    el("post-launch-nav")?.addEventListener("click", () => setView("tracking"));
    el("post-launch-back-btn")?.addEventListener("click", () => setView("stage"));
    el("issue-add-btn")?.addEventListener("click", openIssueModal);
    el("issues-export-btn")?.addEventListener("click", exportIssues);
    el("issue-modal-close")?.addEventListener("click", closeIssueModal);
    el("issue-cancel")?.addEventListener("click", closeIssueModal);
    el("issue-form")?.addEventListener("submit", submitIssue);
    el("issue-modal")?.addEventListener("click", (event) => {
      if (event.target === el("issue-modal")) closeIssueModal();
    });
    el("issue-agent-submit")?.addEventListener("click", () => {
      analyzeIssues(el("issue-agent-input")?.value || "");
    });
    el("archive-gate-close")?.addEventListener("click", closeArchiveGate);
    el("archive-gate-cancel")?.addEventListener("click", closeArchiveGate);
    el("archive-gate-confirm")?.addEventListener("click", confirmArchiveGate);
    el("archive-gate-checkbox")?.addEventListener("change", updateArchiveGateState);
    el("archive-gate-input")?.addEventListener("input", updateArchiveGateState);
    el("archive-gate-modal")?.addEventListener("click", (event) => {
      if (event.target === el("archive-gate-modal")) closeArchiveGate();
    });
    document.querySelectorAll(".issue-prompt-chip").forEach((button) => {
      button.addEventListener("click", () => {
        const prompt = button.dataset.prompt || "";
        const input = el("issue-agent-input");
        if (input) input.value = prompt;
        analyzeIssues(prompt);
      });
    });
    document.addEventListener("click", (event) => {
      const panel = el("leave-confirm");
      const button = el("back-home-btn");
      if (!panel || panel.hidden) return;
      if (panel.contains(event.target) || (button && button.contains(event.target))) return;
      closeLeaveConfirm();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        closeLeaveConfirm();
        closeHistoryClearConfirm();
        closeSourceModal();
        closeExportConfirm();
        closeIssueModal();
        closeArchiveGate();
      }
    });
    el("modal-close-btn")?.addEventListener("click", closeProjectModal);
    el("modal-cancel-btn")?.addEventListener("click", closeProjectModal);
    el("project-form")?.addEventListener("submit", handleCreateProject);
    el("project-modal")?.addEventListener("click", (event) => {
      if (event.target === el("project-modal")) closeProjectModal();
    });
    el("context-toggle")?.addEventListener("click", () => toggleContext());
    el("context-close-btn")?.addEventListener("click", () => toggleContext(true));
    el("upload-doc-btn")?.addEventListener("click", triggerDocumentUpload);
    el("kb-upload-btn")?.addEventListener("click", triggerDocumentUpload);
    el("file-input")?.addEventListener("change", handleFileSelected);
    el("source-modal-close-btn")?.addEventListener("click", closeSourceModal);
    el("source-modal")?.addEventListener("click", (event) => {
      if (event.target === el("source-modal")) closeSourceModal();
    });
    el("assistant-fab")?.addEventListener("click", openAssistant);
    el("assistant-close-btn")?.addEventListener("click", closeAssistant);
    el("assistant-form")?.addEventListener("submit", (event) => {
      event.preventDefault();
      const input = el("assistant-input");
      if (input) sendAssistantMessage(input.value);
    });
    el("assistant-input")?.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        const input = el("assistant-input");
        if (input) sendAssistantMessage(input.value);
      }
    });

    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      if (!el("source-modal")?.hidden) {
        closeSourceModal();
        return;
      }
      if (!el("project-modal")?.hidden) {
        closeProjectModal();
        return;
      }
      if (state.assistantOpen) {
        closeAssistant();
        return;
      }
      if (!state.contextCollapsed && window.innerWidth <= 1180 && state.project) {
        toggleContext(true);
      }
    });

    let wasWide = window.innerWidth > 1180;
    window.addEventListener("resize", () => {
      const isWide = window.innerWidth > 1180;
      if (isWide !== wasWide) {
        state.contextCollapsed = !isWide;
        renderShellState();
        wasWide = isWide;
      }
    });
  }

  async function init() {
    state.contextCollapsed = window.innerWidth <= 1180;
    bindGlobalEvents();
    renderAll();

    const routeProjectId = (document.body.dataset.projectId || "").trim();
    const initialView = new URLSearchParams(window.location.search).get("view");
    if (!routeProjectId) {
      window.location.replace("/");
      return;
    }

    try {
      const health = await apiClient.request("/api/health");
      if (Array.isArray(health.stages) && health.stages.length) {
        state.stages = health.stages;
      }
    } catch (error) {
      // 阶段元数据也会由 /api/context 返回，健康检查失败不阻塞项目加载。
    }

    try {
      await loadProject(routeProjectId);
      if (initialView === "tracking" && postLaunchUnlocked()) {
        setView("tracking");
      }
    } catch (error) {
      showToast(error.message || "项目加载失败。", "error", 5000);
      window.setTimeout(() => {
        window.location.replace("/?project_missing=1");
      }, 900);
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
