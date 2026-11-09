/* ==========================================================================
   首页仪表盘
   ========================================================================== */
(() => {
  "use strict";

  const STAGES = [
    { key: "stage1_research", label: "阶段一 需求调研", short: "阶段一", dot: "调研" },
    { key: "stage2_data", label: "阶段二 数据梳理", short: "阶段二", dot: "数据" },
    { key: "stage3_solution", label: "阶段三 方案设计", short: "阶段三", dot: "方案" },
    { key: "stage4_config", label: "阶段四 配置验证", short: "阶段四", dot: "配置" },
    { key: "stage5_golive", label: "阶段五 上线验收", short: "阶段五", dot: "上线" },
  ];

  const STATUS_LABELS = {
    not_started: "未开始",
    in_progress: "进行中",
    archived: "已归档",
  };

  const state = {
    loaded: false,
    loading: false,
    stats: {
      total_projects: 0,
      active_projects: 0,
      archived_stages: 0,
      active_last_7d: 0,
    },
    projects: [],
    renamingProjectId: "",
    deletingProjectId: "",
  };

  const el = (id) => document.getElementById(id);

  async function api(path, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (options.body) headers["Content-Type"] = "application/json";
    let response;
    try {
      response = await fetch(path, { ...options, headers });
    } catch (error) {
      throw new Error("无法连接本地服务，请确认服务已启动。");
    }
    let data = null;
    try {
      data = await response.json();
    } catch (error) {
      data = null;
    }
    if (!response.ok || (data && data.ok === false)) {
      throw new Error(
        (data && data.message) || `请求失败（HTTP ${response.status}）`
      );
    }
    return data;
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function toast(message, tone = "info") {
    const container = el("toast-container");
    if (!container) return;
    const node = document.createElement("div");
    node.className = `toast toast--${tone}`;
    node.textContent = message;
    container.appendChild(node);
    window.setTimeout(() => {
      node.classList.add("toast--leaving");
      window.setTimeout(() => node.remove(), 220);
    }, 3200);
  }

  function formatRelative(value) {
    if (!value) return "暂无更新";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    const seconds = Math.max(0, (Date.now() - date.getTime()) / 1000);
    if (seconds < 60) return "刚刚更新";
    if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
    if (seconds < 86400 * 7) return `${Math.floor(seconds / 86400)} 天前`;
    return new Intl.DateTimeFormat("zh-CN", {
      month: "numeric",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(date);
  }

  function formatAbsolute(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    }).format(date);
  }

  function renderStats() {
    const values = {
      total_projects: state.stats.total_projects,
      active_projects: state.stats.active_projects,
      archived_stages: state.stats.archived_stages,
      active_last_7d: state.stats.active_last_7d,
    };
    Object.entries(values).forEach(([key, value]) => {
      const card = document.querySelector(`[data-stat="${key}"]`);
      if (!card) return;
      card.classList.remove("is-loading");
      const valueNode = card.querySelector(".stat-value span");
      if (valueNode) valueNode.textContent = Number(value || 0).toLocaleString("zh-CN");
    });
  }

  function resetStats() {
    document.querySelectorAll(".stat-card").forEach((card) => {
      card.classList.remove("is-loading");
      const valueNode = card.querySelector(".stat-value span");
      if (valueNode) valueNode.textContent = "—";
    });
  }

  function stageStatusLabel(status) {
    return STATUS_LABELS[status] || "未开始";
  }

  function renderProjectCard(project) {
    const card = document.createElement("article");
    card.className = "dash-project-card";
    card.tabIndex = 0;
    card.setAttribute("role", "link");
    card.setAttribute("aria-label", `进入 ${project.client_name || "未命名项目"} 的工作台`);
    card.dataset.projectId = project.project_id;
    card.dataset.href = `/workbench/${encodeURIComponent(project.project_id)}`;

    const currentStage =
      STAGES.find((stage) => stage.key === project.current_stage) || STAGES[0];
    const stageHtml = STAGES.map((stage) => {
      const status = (project.stage_status || {})[stage.key] || "not_started";
      const current = stage.key === project.current_stage ? " dash-stage--current" : "";
      return `
        <span class="dash-stage dash-stage--${escapeHtml(status)}${current}"
              title="${escapeHtml(stage.label)}：${escapeHtml(stageStatusLabel(status))}">
          <span class="dash-stage-dot" aria-hidden="true"></span>
          <span class="dash-stage-label">${escapeHtml(stage.dot)}</span>
        </span>
      `;
    }).join("");

    const summary = (project.last_summary || "").trim();
    const summaryHtml = summary ? escapeHtml(summary) : "暂无对话摘要";
    const summaryClass = summary ? "" : " dash-card-summary--empty";
    const lastActive = formatRelative(project.last_active_at);
    const absolute = formatAbsolute(project.last_active_at);

    card.innerHTML = `
      <div class="dash-card-head">
        <h3 class="dash-card-title">${escapeHtml(project.client_name || "未命名项目")}</h3>
        <div class="dash-card-actions">
          <span class="dash-current-stage">${escapeHtml(currentStage.short)}</span>
          <div class="dash-card-menu-wrap">
            <button type="button" class="dash-card-menu-btn" data-action="menu"
                    aria-label="项目操作" aria-haspopup="true" aria-expanded="false">⋯</button>
            <div class="dash-card-menu" role="menu" hidden>
              <button type="button" role="menuitem" data-action="rename">重命名</button>
              <button type="button" role="menuitem" data-action="delete">删除</button>
            </div>
          </div>
        </div>
      </div>
      <div class="dash-card-meta">
        <span>${escapeHtml(project.industry || "行业待补充")}</span>
        <span>${escapeHtml(project.production_mode || "生产模式待补充")}</span>
      </div>
      <div class="dash-stage-row" aria-label="五阶段状态">${stageHtml}</div>
      <p class="dash-card-summary${summaryClass}">${summaryHtml}</p>
      <div class="dash-card-footer">
        <span title="${escapeHtml(absolute)}">最近更新：${escapeHtml(lastActive)}</span>
        <span class="dash-card-enter">进入工作台</span>
      </div>
    `;

    const openCard = () => {
      window.location.href = card.dataset.href;
    };
    card.addEventListener("click", (event) => {
      if (event.target.closest(".dash-card-menu-wrap")) return;
      openCard();
    });
    card.addEventListener("keydown", (event) => {
      if (
        (event.key === "Enter" || event.key === " ") &&
        !event.target.closest(".dash-card-menu-wrap")
      ) {
        event.preventDefault();
        openCard();
      }
    });

    const menuButton = card.querySelector(".dash-card-menu-btn");
    const menu = card.querySelector(".dash-card-menu");
    menuButton.addEventListener("click", (event) => {
      event.stopPropagation();
      const shouldOpen = menu.hidden;
      closeAllCardMenus();
      menu.hidden = !shouldOpen;
      menuButton.setAttribute("aria-expanded", String(shouldOpen));
      if (shouldOpen) {
        const first = menu.querySelector("button");
        if (first) window.setTimeout(() => first.focus(), 10);
      }
    });
    menu.querySelector('[data-action="rename"]').addEventListener("click", (event) => {
      event.stopPropagation();
      closeAllCardMenus();
      openRenameModal(project);
    });
    menu.querySelector('[data-action="delete"]').addEventListener("click", (event) => {
      event.stopPropagation();
      closeAllCardMenus();
      openDeleteModal(project);
    });
    return card;
  }

  function closeAllCardMenus() {
    document.querySelectorAll(".dash-card-menu").forEach((menu) => {
      menu.hidden = true;
    });
    document.querySelectorAll(".dash-card-menu-btn").forEach((button) => {
      button.setAttribute("aria-expanded", "false");
    });
  }

  function renderProjects() {
    const grid = el("project-grid");
    const empty = el("dashboard-empty");
    const count = el("project-count");
    if (!grid) return;
    grid.innerHTML = "";

    if (!state.projects.length) {
      grid.hidden = true;
      if (empty) empty.hidden = false;
      if (count) count.textContent = "共 0 个项目";
      return;
    }

    if (empty) empty.hidden = true;
    state.projects.forEach((project) => {
      grid.appendChild(renderProjectCard(project));
    });
    grid.hidden = false;
    if (count) count.textContent = `共 ${state.projects.length} 个项目`;
  }

  function setLoading(loading) {
    state.loading = loading;
    const skeleton = el("dashboard-loading");
    const grid = el("project-grid");
    const empty = el("dashboard-empty");
    const error = el("dashboard-error");
    const count = el("project-count");
    if (loading) {
      if (skeleton) skeleton.hidden = false;
      if (grid) grid.hidden = true;
      if (empty) empty.hidden = true;
      if (error) error.hidden = true;
      if (count) count.textContent = "加载中…";
      document.querySelectorAll(".stat-card").forEach((card) => {
        card.classList.add("is-loading");
        const valueNode = card.querySelector(".stat-value span");
        if (valueNode) valueNode.textContent = "—";
      });
    } else if (skeleton) {
      skeleton.hidden = true;
    }
  }

  function showError(message) {
    setLoading(false);
    resetStats();
    const error = el("dashboard-error");
    const messageNode = el("dashboard-error-message");
    if (messageNode) messageNode.textContent = message;
    if (error) error.hidden = false;
    const count = el("project-count");
    if (count) count.textContent = "加载失败";
    const grid = el("project-grid");
    const empty = el("dashboard-empty");
    if (grid) grid.hidden = true;
    if (empty) empty.hidden = true;
  }

  async function loadDashboard() {
    if (state.loading) return;
    setLoading(true);
    try {
      const data = (await api("/api/dashboard")) || {};
      state.stats = data.stats || state.stats;
      state.projects = Array.isArray(data.projects) ? data.projects : [];
      state.loaded = true;
      setLoading(false);
      renderStats();
      renderProjects();
    } catch (error) {
      state.loaded = true;
      showError(error.message || "仪表盘加载失败。");
    }
  }

  async function loadTemplates() {
    const select = el("dashboard-template-id");
    if (!select) return;
    const previous = select.value;
    select.disabled = true;
    select.innerHTML = '<option value="">不适用</option>';
    try {
      const data = await api("/api/templates");
      const templates = Array.isArray(data.templates) ? data.templates : [];
      if (!templates.length) {
        const option = document.createElement("option");
        option.value = "";
        option.textContent = "暂无已归档历史模板";
        option.disabled = true;
        select.appendChild(option);
        return;
      }
      templates.forEach((template) => {
        const option = document.createElement("option");
        option.value = template.template_id;
        option.textContent = `阶段${template.stage} · ${template.source_project_name || "未命名项目"} · ${formatAbsolute(template.archived_at)}`;
        select.appendChild(option);
      });
      if (previous) select.value = previous;
    } catch (error) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "模板加载失败";
      option.disabled = true;
      select.appendChild(option);
    } finally {
      select.disabled = false;
    }
  }

  function openProjectModal() {
    const modal = el("dashboard-project-modal");
    if (!modal) return;
    modal.hidden = false;
    document.body.style.overflow = "hidden";
    loadTemplates();
    const input = el("dashboard-client-name");
    if (input) window.setTimeout(() => input.focus(), 20);
  }

  function closeProjectModal() {
    const modal = el("dashboard-project-modal");
    if (!modal) return;
    modal.hidden = true;
    document.body.style.overflow = "";
  }

  async function createProject(event) {
    event.preventDefault();
    const button = el("dashboard-project-submit");
    const clientName = (el("dashboard-client-name")?.value || "").trim();
    if (!clientName) {
      toast("请填写客户名称。", "error");
      el("dashboard-client-name")?.focus();
      return;
    }
    const payload = {
      client_name: clientName,
      industry: (el("dashboard-industry")?.value || "").trim(),
      production_mode: (el("dashboard-production-mode")?.value || "").trim(),
      pain_points: (el("dashboard-pain-points")?.value || "").trim(),
      template_id: el("dashboard-template-id")?.value || "",
    };
    if (button) {
      button.disabled = true;
      button.textContent = "创建中…";
    }
    try {
      const data = await api("/api/project", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      const projectId =
        (data && data.project_id) ||
        (data && data.project && data.project.id) ||
        "";
      if (!projectId) throw new Error("创建成功但未返回项目 ID。");
      window.location.href = `/workbench/${encodeURIComponent(projectId)}`;
    } catch (error) {
      toast(error.message || "项目创建失败。", "error");
      if (button) {
        button.disabled = false;
        button.textContent = "创建并进入工作台";
      }
    }
  }

  function openPresalesModal() {
    const modal = el("presales-modal");
    if (!modal) return;
    el("presales-form")?.reset();
    modal.hidden = false;
    document.body.style.overflow = "hidden";
    loadPresalesHistory();
    window.setTimeout(() => el("presales-industry")?.focus(), 20);
  }

  function closePresalesModal() {
    const modal = el("presales-modal");
    if (modal) modal.hidden = true;
    document.body.style.overflow = "";
  }

  async function loadPresalesHistory() {
    const list = el("presales-history-list");
    if (!list) return;
    list.innerHTML = '<p class="presales-history-empty">加载中…</p>';
    try {
      const data = await api("/api/presales");
      const records = Array.isArray(data.presales) ? data.presales : [];
      if (!records.length) {
        list.innerHTML = '<p class="presales-history-empty">暂无历史评估。</p>';
        return;
      }
      list.innerHTML = records
        .map(
          (record) => `
            <a class="presales-history-item" href="/presales/${encodeURIComponent(record.presales_id)}">
              <span class="presales-history-title">${escapeHtml(record.industry || "未命名行业")}</span>
              <span class="presales-history-meta">${escapeHtml(record.production_mode || "生产模式待补充")} · ${escapeHtml(formatRelative(record.created_at))}</span>
              <span class="presales-history-preview">${escapeHtml(record.preview || "")}</span>
            </a>
          `
        )
        .join("");
    } catch (error) {
      list.innerHTML = `<p class="presales-history-empty">${escapeHtml(error.message || "历史评估加载失败。")}</p>`;
    }
  }

  async function submitPresales(event) {
    event.preventDefault();
    const industry = (el("presales-industry")?.value || "").trim();
    const painPoints = (el("presales-pain-points")?.value || "").trim();
    if (!industry) {
      toast("请填写客户行业。", "error");
      el("presales-industry")?.focus();
      return;
    }
    if (!painPoints) {
      toast("请填写核心痛点。", "error");
      el("presales-pain-points")?.focus();
      return;
    }
    const button = el("presales-submit");
    if (button) {
      button.disabled = true;
      button.textContent = "生成中…";
    }
    try {
      const data = await api("/api/presales", {
        method: "POST",
        body: JSON.stringify({
          industry,
          production_mode: (el("presales-production-mode")?.value || "").trim(),
          pain_points: painPoints,
          scale: (el("presales-scale")?.value || "").trim(),
        }),
      });
      if (!data.presales_id) throw new Error("评估创建成功但未返回记录 ID。");
      window.location.href = `/presales/${encodeURIComponent(data.presales_id)}`;
    } catch (error) {
      toast(error.message || "售前评估生成失败。", "error");
      if (button) {
        button.disabled = false;
        button.textContent = "生成 APS 价值点初稿";
      }
    }
  }

  function recalcStats() {
    const now = Date.now();
    const week = 7 * 24 * 60 * 60 * 1000;
    let archived = 0;
    let active = 0;
    let recent = 0;
    state.projects.forEach((project) => {
      const statuses = Object.values(project.stage_status || {});
      archived += statuses.filter((status) => status === "archived").length;
      if (statuses.some((status) => status !== "archived")) active += 1;
      const timestamp = new Date(project.last_active_at || 0).getTime();
      if (Number.isFinite(timestamp) && now - timestamp <= week) recent += 1;
    });
    state.stats = {
      total_projects: state.projects.length,
      active_projects: active,
      archived_stages: archived,
      active_last_7d: recent,
    };
  }

  function openRenameModal(project) {
    state.renamingProjectId = project.project_id;
    const modal = el("dashboard-rename-modal");
    if (!modal) return;
    const name = el("dashboard-rename-client-name");
    const industry = el("dashboard-rename-industry");
    if (name) name.value = project.client_name || "";
    if (industry) industry.value = project.industry || "";
    modal.hidden = false;
    document.body.style.overflow = "hidden";
    if (name) window.setTimeout(() => name.focus(), 20);
  }

  function closeRenameModal() {
    const modal = el("dashboard-rename-modal");
    if (modal) modal.hidden = true;
    document.body.style.overflow = "";
    state.renamingProjectId = "";
  }

  function openDeleteModal(project) {
    state.deletingProjectId = project.project_id;
    const modal = el("dashboard-delete-modal");
    if (!modal) return;
    modal.hidden = false;
    document.body.style.overflow = "hidden";
    const button = el("dashboard-delete-submit");
    if (button) {
      button.disabled = false;
      button.textContent = "确认删除";
    }
  }

  function closeDeleteModal() {
    const modal = el("dashboard-delete-modal");
    if (modal) modal.hidden = true;
    document.body.style.overflow = "";
    state.deletingProjectId = "";
  }

  async function saveRename(event) {
    event.preventDefault();
    const projectId = state.renamingProjectId;
    if (!projectId) return;
    const name = (el("dashboard-rename-client-name")?.value || "").trim();
    if (!name) {
      toast("客户名称不能为空。", "error");
      el("dashboard-rename-client-name")?.focus();
      return;
    }
    const button = el("dashboard-rename-submit");
    if (button) {
      button.disabled = true;
      button.textContent = "保存中…";
    }
    try {
      const data = await api(`/api/project/${encodeURIComponent(projectId)}`, {
        method: "PATCH",
        body: JSON.stringify({
          client_name: name,
          industry: (el("dashboard-rename-industry")?.value || "").trim(),
        }),
      });
      const summary = data.summary;
      const index = state.projects.findIndex((item) => item.project_id === projectId);
      if (summary && index >= 0) {
        state.projects[index] = { ...state.projects[index], ...summary };
      } else if (index >= 0) {
        state.projects[index] = {
          ...state.projects[index],
          client_name: name,
          industry: (el("dashboard-rename-industry")?.value || "").trim(),
        };
      }
      closeRenameModal();
      renderStats();
      renderProjects();
      toast("项目信息已更新。", "success");
    } catch (error) {
      toast(error.message || "项目更新失败。", "error");
    } finally {
      if (button) {
        button.disabled = false;
        button.textContent = "保存";
      }
    }
  }

  async function confirmDeleteProject() {
    const projectId = state.deletingProjectId;
    if (!projectId) return;
    const button = el("dashboard-delete-submit");
    if (button) {
      button.disabled = true;
      button.textContent = "删除中…";
    }
    try {
      await api(`/api/project/${encodeURIComponent(projectId)}`, {
        method: "DELETE",
      });
      state.projects = state.projects.filter(
        (project) => project.project_id !== projectId
      );
      recalcStats();
      closeDeleteModal();
      renderStats();
      renderProjects();
      toast("项目及其关联数据已删除。", "success");
    } catch (error) {
      toast(error.message || "项目删除失败，请重试。", "error");
      if (button) {
        button.disabled = false;
        button.textContent = "确认删除";
      }
    }
  }

  function bindEvents() {
    el("dashboard-new-project-btn")?.addEventListener("click", openProjectModal);
    el("empty-new-project-btn")?.addEventListener("click", openProjectModal);
    el("presales-quick-btn")?.addEventListener("click", openPresalesModal);
    el("presales-modal-close")?.addEventListener("click", closePresalesModal);
    el("presales-cancel")?.addEventListener("click", closePresalesModal);
    el("presales-form")?.addEventListener("submit", submitPresales);
    el("presales-modal")?.addEventListener("click", (event) => {
      if (event.target === el("presales-modal")) closePresalesModal();
    });
    el("dashboard-project-modal-close")?.addEventListener("click", closeProjectModal);
    el("dashboard-project-cancel")?.addEventListener("click", closeProjectModal);
    el("dashboard-project-form")?.addEventListener("submit", createProject);
    el("dashboard-retry-btn")?.addEventListener("click", loadDashboard);

    const modal = el("dashboard-project-modal");
    if (modal) {
      modal.addEventListener("click", (event) => {
        if (event.target === modal) closeProjectModal();
      });
    }

    el("dashboard-rename-close")?.addEventListener("click", closeRenameModal);
    el("dashboard-rename-cancel")?.addEventListener("click", closeRenameModal);
    el("dashboard-rename-form")?.addEventListener("submit", saveRename);
    const renameModal = el("dashboard-rename-modal");
    if (renameModal) {
      renameModal.addEventListener("click", (event) => {
        if (event.target === renameModal) closeRenameModal();
      });
    }

    el("dashboard-delete-close")?.addEventListener("click", closeDeleteModal);
    el("dashboard-delete-cancel")?.addEventListener("click", closeDeleteModal);
    el("dashboard-delete-submit")?.addEventListener("click", confirmDeleteProject);
    const deleteModal = el("dashboard-delete-modal");
    if (deleteModal) {
      deleteModal.addEventListener("click", (event) => {
        if (event.target === deleteModal) closeDeleteModal();
      });
    }

    document.addEventListener("click", (event) => {
      if (!event.target.closest(".dash-card-menu-wrap")) closeAllCardMenus();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        closeProjectModal();
        closeRenameModal();
        closeDeleteModal();
        closePresalesModal();
        closeAllCardMenus();
      }
    });
  }

  function handleRouteNotice() {
    const params = new URLSearchParams(window.location.search);
    if (params.get("project_missing") === "1") {
      toast("项目不存在或已被删除，已返回首页。", "error");
      window.history.replaceState({}, "", "/");
    }
    if (params.get("presales_missing") === "1") {
      toast("售前评估记录不存在，已返回首页。", "error");
      window.history.replaceState({}, "", "/");
    }
  }

  function init() {
    bindEvents();
    handleRouteNotice();
    loadDashboard();
  }

  init();
})();
