/* ==========================================================================
   售前快速评估页面
   ========================================================================== */
(() => {
  "use strict";

  const record = window.__PRESALES__ || {};
  const el = (id) => document.getElementById(id);

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function inline(text) {
    let value = escapeHtml(text);
    value = value.replace(/`([^`]+)`/g, "<code>$1</code>");
    value = value.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    value = value.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
    return value;
  }

  function tableCells(line) {
    let value = line.trim();
    if (value.startsWith("|")) value = value.slice(1);
    if (value.endsWith("|")) value = value.slice(0, -1);
    return value.split("|").map((cell) => cell.trim());
  }

  function isSeparator(line) {
    const cells = tableCells(line);
    return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
  }

  function renderMarkdown(markdown) {
    const lines = String(markdown || "").replace(/\r\n/g, "\n").split("\n");
    const html = [];
    let list = null;
    let paragraph = [];

    const flushParagraph = () => {
      if (!paragraph.length) return;
      html.push(`<p>${paragraph.map(inline).join("<br>")}</p>`);
      paragraph = [];
    };
    const closeList = () => {
      if (list) {
        html.push(`</${list}>`);
        list = null;
      }
    };

    for (let index = 0; index < lines.length; index += 1) {
      const line = lines[index].trim();
      if (!line) {
        flushParagraph();
        closeList();
        continue;
      }
      const heading = line.match(/^(#{1,4})\s+(.+)$/);
      if (heading) {
        flushParagraph();
        closeList();
        const level = heading[1].length;
        html.push(`<h${level}>${inline(heading[2])}</h${level}>`);
        continue;
      }
      if (
        line.includes("|") &&
        index + 1 < lines.length &&
        isSeparator(lines[index + 1])
      ) {
        flushParagraph();
        closeList();
        const rows = [tableCells(line)];
        index += 2;
        while (index < lines.length && lines[index].includes("|")) {
          rows.push(tableCells(lines[index]));
          index += 1;
        }
        index -= 1;
        html.push("<table><thead><tr>");
        rows[0].forEach((cell) => html.push(`<th>${inline(cell)}</th>`));
        html.push("</tr></thead><tbody>");
        rows.slice(1).forEach((row) => {
          html.push("<tr>");
          row.forEach((cell) => html.push(`<td>${inline(cell)}</td>`));
          html.push("</tr>");
        });
        html.push("</tbody></table>");
        continue;
      }
      const bullet = line.match(/^[-*+]\s+(.+)$/);
      const numbered = line.match(/^\d+[.)]\s+(.+)$/);
      if (bullet || numbered) {
        flushParagraph();
        const type = bullet ? "ul" : "ol";
        if (list !== type) {
          closeList();
          list = type;
          html.push(`<${type}>`);
        }
        html.push(`<li>${inline((bullet || numbered)[1])}</li>`);
        continue;
      }
      closeList();
      paragraph.push(line);
    }
    flushParagraph();
    closeList();
    return html.join("");
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

  async function exportWord() {
    const button = el("presales-export-btn");
    if (button) {
      button.disabled = true;
      button.textContent = "生成中…";
    }
    try {
      const response = await fetch(
        `/api/presales/${encodeURIComponent(record.id)}/export/word`,
        { method: "POST" }
      );
      if (!response.ok) {
        let message = "导出失败，请重试。";
        try {
          const data = await response.json();
          if (data && data.message) message = data.message;
        } catch (error) {
          // 非 JSON 异常。
        }
        throw new Error(message);
      }
      const blob = await response.blob();
      const filename =
        filenameFromDisposition(response.headers.get("Content-Disposition")) ||
        `${record.industry || "售前评估"}_APS价值点初稿.docx`;
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1500);
      toast("导出文件已开始下载。", "success");
    } catch (error) {
      toast(error.message || "导出失败，请重试。", "error");
    } finally {
      if (button) {
        button.disabled = false;
        button.innerHTML = '<span aria-hidden="true">W</span> 导出 Word';
      }
    }
  }

  function render() {
    const intake = el("presales-intake");
    if (intake) {
      const fields = [
        ["客户行业", record.industry],
        ["生产模式", record.production_mode],
        ["核心痛点", record.pain_points],
        ["预计规模", record.scale],
      ];
      intake.innerHTML = fields
        .map(
          ([label, value]) =>
            `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value || "未填写")}</dd>`
        )
        .join("");
    }
    const assessment = el("presales-assessment");
    if (assessment) {
      assessment.innerHTML = renderMarkdown(
        (record.assessment || {}).content || "暂无评估内容。"
      );
    }
    el("presales-export-btn")?.addEventListener("click", exportWord);
  }

  render();
})();
