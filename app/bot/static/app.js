/* Post Bot — minimal vanilla chat client. No build step, no dependencies. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const messagesEl = $("messages");
  const inputEl = $("input");
  const sendBtn = $("sendBtn");
  const typingEl = $("typing");
  const typingText = $("typingText");
  const platformBar = $("platformBar");
  const statusLine = $("statusLine");

  const STORAGE_KEY = "mpt-bot-chat-id";
  let chatId = localStorage.getItem(STORAGE_KEY) || "";
  let busy = false;
  let pollTimer = null;
  let platforms = [];

  /* ---------------- transport ---------------- */

  async function api(path, options = {}) {
    const response = await fetch(path, {
      headers: options.body instanceof FormData
        ? undefined
        : { "Content-Type": "application/json" },
      ...options,
    });
    if (!response.ok) {
      let detail = response.statusText;
      try {
        const body = await response.json();
        detail = body.detail || detail;
      } catch (_) { /* ignore */ }
      throw new Error(detail);
    }
    return response.json();
  }

  /* ---------------- rendering ---------------- */

  // Tiny, safe markup: escape first, then re-introduce **bold**, _italic_, `code`.
  function format(text) {
    const escaped = String(text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
    return escaped
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/(^|\s)_([^_]+)_(?=\s|$|\.)/g, "$1<em>$2</em>")
      // 下载包：这是唯一需要真的能点的东西（手机上下载一周的成品）。
      .replace(
        /(^|\s)((?:\/api\/bot\/export\/[\w.-]+\.zip)|(https?:\/\/[^\s<]+))/g,
        '$1<a href="$2" target="_blank" rel="noopener">$2</a>',
      );
  }

  function scrollToBottom() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function appendMessage({ text, video_url: videoUrl, poster_url: posterUrl, buttons, role }) {
    const wrap = document.createElement("div");
    wrap.className = `msg ${role || "bot"}`;

    if (text) {
      const bubble = document.createElement("div");
      bubble.className = "bubble";
      bubble.innerHTML = format(text);
      wrap.appendChild(bubble);
    }

    if (videoUrl) {
      const media = document.createElement("div");
      media.className = "media";
      const video = document.createElement("video");
      video.src = videoUrl;
      video.controls = true;
      video.playsInline = true;
      video.preload = "metadata";
      if (posterUrl) video.poster = posterUrl;
      media.appendChild(video);
      wrap.appendChild(media);
    }

    if (buttons && buttons.length) {
      const actions = document.createElement("div");
      actions.className = "actions";
      buttons.flat().forEach((button) => {
        const el = document.createElement("button");
        el.className = "action" + (/publish|post now/i.test(button.label) ? " primary" : "");
        el.textContent = button.label;
        el.addEventListener("click", () => {
          actions.querySelectorAll("button").forEach((b) => (b.disabled = true));
          submitCallback(button.data);
        });
        actions.appendChild(el);
      });
      wrap.appendChild(actions);
    }

    messagesEl.appendChild(wrap);
    scrollToBottom();
  }

  function renderReplies(replies) {
    (replies || []).forEach((reply) => appendMessage({ ...reply, role: "bot" }));
  }

  function userBubble(text) {
    appendMessage({ text, role: "user" });
  }

  function setBusy(value, label) {
    busy = value;
    sendBtn.disabled = value;
    typingEl.hidden = !value;
    if (label) typingText.textContent = label;
  }

  function showError(message) {
    const banner = document.createElement("div");
    banner.className = "error-banner";
    banner.textContent = message;
    messagesEl.appendChild(banner);
    scrollToBottom();
    setTimeout(() => banner.remove(), 6000);
  }

  /* ---------------- platforms ---------------- */

  function renderPlatforms(selected) {
    if (platforms.length === 0) return;
    platformBar.innerHTML = "";
    const chosen = new Set(selected || []);
    platforms.forEach((item) => {
      const chip = document.createElement("button");
      chip.className = "chip";
      chip.type = "button";
      chip.setAttribute("aria-pressed", chosen.has(item.value) ? "true" : "false");
      chip.textContent = `${item.emoji} ${item.label}`;
      chip.addEventListener("click", () => togglePlatform(item.value, chip));
      platformBar.appendChild(chip);
    });
  }

  async function togglePlatform(value, chip) {
    chip.disabled = true;
    try {
      const result = await api("/api/bot/callback", {
        method: "POST",
        body: JSON.stringify({ chat_id: chatId, data: `pl:${value}` }),
      });
      renderReplies(result.replies);
      await refreshSession();
    } catch (error) {
      showError(error.message);
    } finally {
      chip.disabled = false;
    }
  }

  async function refreshSession() {
    const session = await api(`/api/bot/session?chat_id=${encodeURIComponent(chatId)}`);
    chatId = session.chat_id;
    localStorage.setItem(STORAGE_KEY, chatId);
    platforms = session.platform_labels;
    renderPlatforms(session.platforms);
  }

  /* ---------------- sending ---------------- */

  async function sendText() {
    const text = inputEl.value.trim();
    if (!text || busy) return;
    $("emptyState")?.remove();
    inputEl.value = "";
    inputEl.style.height = "auto";
    userBubble(text);
    setBusy(true, "Working…");

    try {
      const result = await api("/api/bot/message", {
        method: "POST",
        body: JSON.stringify({ chat_id: chatId, text }),
      });
      renderReplies(result.replies);
      startPolling();
    } catch (error) {
      showError(error.message);
    } finally {
      setBusy(false);
    }
  }

  async function submitCallback(data) {
    try {
      const result = await api("/api/bot/callback", {
        method: "POST",
        body: JSON.stringify({ chat_id: chatId, data }),
      });
      renderReplies(result.replies);
      startPolling();
    } catch (error) {
      showError(error.message);
    }
  }

  async function uploadFile(file) {
    if (!file) return;
    if (file.size > 200 * 1024 * 1024) {
      showError("That file is too large (limit 200 MB).");
      return;
    }
    $("emptyState")?.remove();
    userBubble(`📎 ${file.name}`);
    setBusy(true, "Uploading…");
    const form = new FormData();
    form.append("file", file);
    try {
      const result = await api(
        `/api/bot/upload?chat_id=${encodeURIComponent(chatId)}`,
        { method: "POST", body: form }
      );
      renderReplies(result.replies);
    } catch (error) {
      showError(error.message);
    } finally {
      setBusy(false);
    }
  }

  /* ---------------- async feed ---------------- */

  function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(async () => {
      try {
        const feed = await api(`/api/bot/feed?chat_id=${encodeURIComponent(chatId)}`);
        if (feed.replies && feed.replies.length) {
          renderReplies(feed.replies);
          setBusy(false);
        } else if (busy) {
          // 任务仍在后台运行时保持“输入中”提示。
        }
      } catch (_) {
        /* 网络抖动不弹出错误，下一轮继续 */
      }
    }, 1600);
  }

  async function consumePending() {
    try {
      const feed = await api(`/api/bot/feed?chat_id=${encodeURIComponent(chatId)}`);
      if (feed.replies && feed.replies.length) renderReplies(feed.replies);
    } catch (_) { /* ignore */ }
  }

  /* ---------------- guides ---------------- */

  async function openGuides() {
    const sheet = $("guidesSheet");
    sheet.hidden = false;
    const body = $("guidesBody");
    try {
      const data = await api("/api/bot/guides");
      body.innerHTML = data.guides
        .map(
          (guide) => `
          <div class="guide">
            <h4>${guide.label} · max ${guide.max_seconds}s · ${guide.aspect}</h4>
            <dl>
              <dt>Account</dt><dd>${guide.account}</dd>
              <dt>How it pays</dt><dd>${guide.monetization}</dd>
              <dt>Watch out</dt><dd>${guide.gotcha}</dd>
            </dl>
          </div>`
        )
        .join("");
    } catch (error) {
      body.textContent = error.message;
    }
  }

  const esc = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (ch) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch],
    );

  async function openConnections() {
    const sheet = $("guidesSheet");
    sheet.hidden = false;
    const body = $("guidesBody");
    body.innerHTML = "Checking each platform…";
    try {
      // 手动点开时强制刷新，明确表达“我现在就想知道真实状态”。
      const data = await api("/api/bot/connections?refresh=1");
      const rows = data.report.platforms
        .map((item) => {
          const icon = item.reauth_required ? "⚠️" : item.connected ? "✅" : "❌";
          const label = item.platform;
          const detail = item.reauth_required
            ? "login expired — reconnect it"
            : item.connected
              ? `connected as ${item.handle || item.display_name || "account"}`
              : item.note || "not connected";
          return `<div class="guide"><h4>${icon} ${esc(label)}</h4>
                  <dl><dd>${esc(detail)}</dd></dl></div>`;
        })
        .join("");
      body.innerHTML =
        `<div class="guide"><h4>${data.all_connected ? "All four connected" : "Not everything is connected yet"}</h4>
           <dl><dd>Profile: ${esc(data.report.profile_name || "—")}${
             data.report.plan_limit ? ` · plan allows ${data.report.plan_limit} profile(s)` : ""
           }</dd>
           <dt>Cost of this check</dt>
           <dd>1 API call · cached 5 minutes${
             data.report.config_ready ? "" : " · not configured yet"
           }</dd></dl></div>` +
        rows +
        `<div class="guide"><h4>Connect or fix accounts</h4>
           <dl><dd><a href="${data.manage_url}" target="_blank" rel="noopener">${data.manage_url}</a></dd>
               <dt>API key</dt>
               <dd><a href="${data.api_keys_url}" target="_blank" rel="noopener">${data.api_keys_url}</a></dd></dl></div>`;
    } catch (error) {
      body.textContent = error.message;
    }
  }

  /* ---------------- boot ---------------- */

  async function boot() {
    try {
      const health = await api("/api/bot/health");
      if (health.bot_name) {
        document.title = health.bot_name;
        const heading = document.querySelector(".brand h1");
        if (heading) heading.textContent = health.bot_name;
      }
      const badges = [];
      if (health.demo_mode) badges.push('<span class="badge demo">demo render</span>');
      badges.push(
        health.publishing_live
          ? '<span class="badge live">publishing live</span>'
          : '<span class="badge sim">publishing simulated</span>'
      );
      statusLine.innerHTML = badges.join(" ");
    } catch (error) {
      statusLine.textContent = "Backend unreachable";
    }

    try {
      await refreshSession();
    } catch (error) {
      showError(error.message);
    }

    await consumePending();
    startPolling();
  }

  /* ---------------- events ---------------- */

  sendBtn.addEventListener("click", sendText);

  inputEl.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendText();
    }
  });

  inputEl.addEventListener("input", () => {
    inputEl.style.height = "auto";
    inputEl.style.height = Math.min(inputEl.scrollHeight, 132) + "px";
  });

  document.querySelectorAll("[data-suggest]").forEach((button) => {
    button.addEventListener("click", () => {
      inputEl.value = button.dataset.suggest;
      sendText();
    });
  });

  $("attachBtn").addEventListener("click", () => $("fileInput").click());
  $("fileInput").addEventListener("change", (event) => {
    const file = event.target.files && event.target.files[0];
    event.target.value = "";
    uploadFile(file);
  });

  async function openAutopilot() {
    const sheet = $("guidesSheet");
    sheet.hidden = false;
    const body = $("guidesBody");
    body.innerHTML = "Loading the schedule…";
    try {
      const data = await api("/api/bot/autopilot");
      if (data.error) throw new Error(data.error);
      const plan = data.interval_minutes
        ? `every ${data.interval_minutes} minutes`
        : `at ${(data.times || []).join(", ")}`;
      const recent = (data.runs || [])
        .slice()
        .reverse()
        .map((run) => {
          const icon = run.published ? "✅" : run.ok ? "🎬" : "❌";
          return `<div class="guide"><h4>${icon} ${esc(run.topic || "—")}</h4>
                  <dl><dd>${esc(run.at || "")}${
                    run.error ? ` · ${esc(run.error)}` : ""
                  }</dd></dl></div>`;
        })
        .join("");
      body.innerHTML =
        `<div class="guide"><h4>${data.paused ? "⏸ Paused" : "🤖 Running"} · ${esc(data.mode)}</h4>
           <dl><dt>Plan</dt><dd>${esc(plan)} · ${esc(data.timezone)}</dd>
               <dt>Today</dt><dd>${data.runs_today}/${data.max_per_day}</dd>
               <dt>Next run</dt><dd>${esc(data.next_run || "paused")}</dd>
               <dt>Next topic</dt><dd>${esc(data.next_topic || "—")}</dd>
               <dt>Queue</dt><dd>${data.queue_size} topics (${
                 data.custom_topics ? "your list" : "built-in rotation"
               })</dd></dl>
           <div class="tools" style="margin-top:10px">
             <button class="ghost" id="autoRun">▶️ Run one now</button>
             <button class="ghost" id="autoToggle">${
               data.paused ? "▶️ Resume" : "⏸ Pause"
             }</button>
           </div></div>` + recent;
      $("autoRun").addEventListener("click", () => autoAction("run"));
      $("autoToggle").addEventListener("click", () =>
        autoAction(data.paused ? "resume" : "pause"),
      );
    } catch (error) {
      body.textContent = error.message;
    }
  }

  async function autoAction(action) {
    try {
      await api(`/api/bot/autopilot/${action}`, {
        method: "POST",
        body: JSON.stringify({ chat_id: chatId }),
      });
    } catch (error) {
      $("guidesBody").textContent = error.message;
      return;
    }
    if (action === "run") {
      $("guidesBody").innerHTML =
        "▶️ Running one cycle in the background. The result appears in the chat.";
      setTimeout(openAutopilot, 4000);
      return;
    }
    openAutopilot();
  }

  $("autoBtn").addEventListener("click", openAutopilot);
  $("connBtn").addEventListener("click", openConnections);
  $("guidesBtn").addEventListener("click", openGuides);
  $("guidesClose").addEventListener("click", () => ($("guidesSheet").hidden = true));
  $("guidesSheet").addEventListener("click", (event) => {
    if (event.target.id === "guidesSheet") $("guidesSheet").hidden = true;
  });

  boot();
})();
