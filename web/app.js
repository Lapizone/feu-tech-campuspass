(function () {
  "use strict";

  const CLIENT_TIMEOUT_MS = 8000;
  const REPORT_TIMEOUT_MS = 10000;

  const ROLE_TABS = {
    STUDENT: ["tab-student"],
    CHECK_IN_STAFF: ["tab-scanner"],
    EVENT_ORGANIZER: ["tab-organizer"],
    ADMINISTRATOR: ["tab-admin", "tab-organizer", "tab-scanner"],
  };

  const state = {
    sessionToken: null,
    user: null,
    sessionEpoch: 0,
    events: [],
    lastUnverified: null,
    auditLogs: [],
    auditFilter: "ALL",
    auditSearch: "",
    scanHistory: [],
    cameraStream: null,
    cameraRafId: null,
    cameraEpoch: 0,
    cameraCooldownUntil: 0,
    lastScannedToken: null,
    modalTrigger: null,
  };

  function el(id) {
    return document.getElementById(id);
  }

  function escapeHtml(text) {
    const str = text == null ? "" : String(text);
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function errCode(res) {
    return escapeHtml((res && res.data && res.data.code) || (res && res.status) || "ERROR");
  }

  function errMsg(res, fallback) {
    return escapeHtml((res && res.data && res.data.message) || fallback || "Request failed.");
  }

  function iconSvg(kind) {
    if (kind === "ok") {
      return '<svg class="alert-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M20 6L9 17l-5-5"/></svg>';
    }
    if (kind === "warn") {
      return '<svg class="alert-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M12 9v4m0 4h.01M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/></svg>';
    }
    if (kind === "err") {
      return '<svg class="alert-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><circle cx="12" cy="12" r="10"/><path d="M15 9l-6 6M9 9l6 6"/></svg>';
    }
    return '<svg class="alert-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4m0-4h.01"/></svg>';
  }

  function showBanner(targetId, kind, html) {
    const node = el(targetId);
    if (!node) return;
    node.innerHTML = `<div class="alert ${kind}">${iconSvg(kind)}<div>${html}</div></div>`;
  }

  function showToast(message, tone) {
    const stack = el("toast-stack");
    if (!stack) return;
    stack.innerHTML = "";
    const toast = document.createElement("div");
    toast.className = `toast ${tone === "ok" ? "is-ok" : tone === "err" ? "is-err" : ""}`;
    toast.textContent = message;
    stack.appendChild(toast);
    setTimeout(() => {
      toast.remove();
    }, 2400);
  }

  function setLoading(buttonEl, loading, loadingLabel) {
    if (!buttonEl) return;
    if (loading) {
      if (!buttonEl.dataset.origLabel) {
        buttonEl.dataset.origLabel = buttonEl.textContent;
      }
      buttonEl.disabled = true;
      buttonEl.classList.add("is-loading");
      if (loadingLabel) buttonEl.textContent = loadingLabel;
    } else {
      buttonEl.disabled = false;
      buttonEl.classList.remove("is-loading");
      if (buttonEl.dataset.origLabel) {
        buttonEl.textContent = buttonEl.dataset.origLabel;
      }
    }
  }

  async function api(method, path, body, options) {
    const timeoutMs = (options && options.timeoutMs) || CLIENT_TIMEOUT_MS;
    const headers = { "Content-Type": "application/json" };
    if (state.sessionToken) {
      headers["Authorization"] = `Bearer ${state.sessionToken}`;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const resp = await fetch(path, {
        method,
        headers,
        credentials: "same-origin",
        signal: controller.signal,
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
      const data = await resp.json().catch(() => ({}));
      return { ok: resp.ok, status: resp.status, data };
    } catch (err) {
      const isAbort = err && err.name === "AbortError";
      return {
        ok: false,
        status: 503,
        data: {
          code: "SERVICE_UNAVAILABLE",
          message: isAbort
            ? `Client request timed out after ${timeoutMs / 1000}s; outcome is unverified.`
            : `Network error: ${err && err.message ? err.message : "service unreachable"}`,
          correlationId: isAbort ? "client-timeout" : "client-network-error",
        },
      };
    } finally {
      clearTimeout(timer);
    }
  }

  function parseCsvList(raw) {
    return String(raw || "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
  }

  function getAllowedTabs() {
    if (!state.user || !state.user.role) return [];
    return ROLE_TABS[state.user.role] || [];
  }

  function formatScopeSummary(user) {
    if (!user) return "";
    if (user.role === "STUDENT") {
      return `Student Scope · ID ${user.studentId || "Unbound"}`;
    }
    const eventsLabel = (user.assignedEventIds || []).join(", ") || "No assigned events";
    if (user.role === "CHECK_IN_STAFF") {
      return `Scanner Scope · ${eventsLabel}`;
    }
    if (user.role === "EVENT_ORGANIZER") {
      return `Organizer Scope · ${eventsLabel}`;
    }
    if (user.role === "ADMINISTRATOR") {
      return "Administrator Scope · 3 Authorized Workspaces";
    }
    return user.role;
  }

  function syncRoleAccessUi() {
    const navRail = el("nav-rail");
    const scopePill = el("rbac-scope-pill");
    const authPanel = el("tab-auth");
    const allowed = getAllowedTabs();

    if (!state.user) {
      document.body.classList.add("is-auth-screen");
      document.body.classList.remove("is-authenticated");
      if (navRail) navRail.hidden = true;
      if (scopePill) scopePill.textContent = "";
      document.querySelectorAll(".tab-btn").forEach((btn) => {
        btn.hidden = true;
        btn.disabled = true;
        btn.classList.remove("active");
        btn.setAttribute("aria-selected", "false");
      });
      document.querySelectorAll(".panel").forEach((panel) => {
        const isAuth = panel.id === "tab-auth";
        panel.hidden = !isAuth;
        panel.classList.toggle("active", isAuth);
      });
      return;
    }

    document.body.classList.remove("is-auth-screen");
    document.body.classList.add("is-authenticated");
    if (navRail) navRail.hidden = false;
    if (scopePill) scopePill.textContent = formatScopeSummary(state.user);

    if (authPanel) {
      authPanel.hidden = true;
      authPanel.classList.remove("active");
    }

    document.querySelectorAll(".tab-btn").forEach((btn) => {
      const isAllowed = allowed.includes(btn.dataset.tab);
      btn.hidden = !isAllowed;
      btn.disabled = !isAllowed;
      if (!isAllowed) {
        btn.classList.remove("active");
        btn.setAttribute("aria-selected", "false");
      }
    });

    document.querySelectorAll(".panel").forEach((panel) => {
      if (panel.id === "tab-auth") return;
      if (!allowed.includes(panel.id)) {
        panel.hidden = true;
        panel.classList.remove("active");
      }
    });
  }

  function switchTab(tabId) {
    if (!state.user) {
      syncRoleAccessUi();
      return;
    }
    if (tabId === "tab-auth") {
      showToast("Sign out first to return to the login screen.", "err");
      return;
    }
    const allowed = getAllowedTabs();
    if (!allowed.includes(tabId)) {
      showToast("Access restricted for your role.", "err");
      return;
    }
    if (tabId !== "tab-scanner") {
      stopCameraStream();
    }
    document.querySelectorAll(".tab-btn").forEach((btn) => {
      const active = btn.dataset.tab === tabId && !btn.disabled;
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-selected", active ? "true" : "false");
    });
    document.querySelectorAll(".panel").forEach((panel) => {
      const active = panel.id === tabId;
      panel.hidden = !active;
      panel.classList.toggle("active", active);
    });
  }

  function switchSegmented(selectorAttr, activeVal, paneMap) {
    document.querySelectorAll(`.seg-btn[${selectorAttr}]`).forEach((btn) => {
      const isActive = btn.getAttribute(selectorAttr) === activeVal;
      btn.classList.toggle("active", isActive);
      btn.setAttribute("aria-pressed", isActive ? "true" : "false");
    });
    Object.entries(paneMap).forEach(([key, paneId]) => {
      const pane = el(paneId);
      if (pane) pane.classList.toggle("active", key === activeVal);
    });
  }

  function updateSessionUi() {
    const summary = el("session-summary");
    const pill = el("session-pill");
    const logoutBtn = el("btn-logout");
    if (!state.user) {
      summary.textContent = "Not signed in";
      pill.classList.remove("is-live");
      pill.classList.add("is-guest");
      logoutBtn.style.display = "none";
      return;
    }
    const extra = state.user.studentId ? ` (${state.user.studentId})` : "";
    summary.textContent = `${state.user.username} · ${state.user.role}${extra}`;
    pill.classList.remove("is-guest");
    pill.classList.add("is-live");
    logoutBtn.style.display = "inline-flex";
  }

  function setScannerStepState(stepStates) {
    document.querySelectorAll("#scanner-stepper .step").forEach((li) => {
      li.classList.remove("is-active", "is-done", "is-warn", "is-error");
      const mode = stepStates[li.dataset.step];
      if (mode) li.classList.add(mode);
      if (mode === "is-active") {
        li.setAttribute("aria-current", "step");
      } else {
        li.removeAttribute("aria-current");
      }
    });
  }

  function setScannerReconcileControls(visible, allowRetry) {
    el("btn-scanner-reconcile").style.display = visible ? "inline-flex" : "none";
    el("btn-scanner-retry").style.display = allowRetry ? "inline-flex" : "none";
  }

  function maskToken(token) {
    const raw = String(token || "");
    if (raw.length <= 8) return "****";
    return `…${raw.slice(-6)}`;
  }

  function recordScanHistory(eventId, token, status, badgeTone) {
    const nowLabel = new Date().toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
    state.scanHistory.unshift({
      eventId,
      tokenSuffix: maskToken(token),
      status,
      badgeTone,
      time: nowLabel,
    });
    if (state.scanHistory.length > 8) state.scanHistory.length = 8;
    renderScanHistory();
  }

  function renderScanHistory() {
    const box = el("scanner-history");
    if (!box) return;
    if (!state.scanHistory.length) {
      box.innerHTML = `<div class="empty-state">No scans recorded in this session yet.</div>`;
      return;
    }
    const rows = state.scanHistory
      .map(
        (item) => `
      <tr>
        <td class="mono-id">${escapeHtml(item.eventId)}</td>
        <td class="mono-id">${escapeHtml(item.tokenSuffix)}</td>
        <td><span class="badge ${escapeHtml(item.badgeTone)}">${escapeHtml(item.status)}</span></td>
        <td class="mono-id">${escapeHtml(item.time)}</td>
      </tr>`
      )
      .join("");
    box.innerHTML = `<div class="table-wrap"><table><thead><tr><th>Event</th><th>Token</th><th>Status</th><th>Time</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  async function refreshEvents() {
    if (!state.user) return;
    const epoch = state.sessionEpoch;
    const role = state.user.role;
    const res = await api("GET", "/api/v1/events");
    if (!state.user || epoch !== state.sessionEpoch) return;
    if (!res.ok) {
      const target = role === "STUDENT" ? "student-events-list" : "global-status";
      showBanner(target, "err", `<strong>${errCode(res)}</strong>: ${errMsg(res, "Unable to load events.")}`);
      return;
    }
    state.events = res.data.events || [];
    renderEvents();
    populateEventSelectors();
  }

  function renderEvents() {
    const container = el("student-events-list");
    if (!state.events.length) {
      container.innerHTML = `<div class="empty-state">No events configured.</div>`;
      return;
    }
    const isStudent = state.user && state.user.role === "STUDENT";
    const cards = state.events
      .map((ev) => {
        const confirmed = Number(ev.confirmedCount) || 0;
        const capacity = Number(ev.capacity) || 0;
        const remaining = Number(ev.remainingSlots) || 0;
        const pct = capacity > 0 ? Math.min(100, Math.round((confirmed / capacity) * 100)) : 0;
        const isFull = remaining <= 0;
        const statusBadges = (ev.eligibleStatuses || [])
          .map((s) => `<span class="badge info">${escapeHtml(s)}</span>`)
          .join(" ");
        return `
        <article class="event-card ${isFull ? "is-full" : ""}">
          <div class="event-card-top">
            <div class="event-card-meta">
              <div class="event-code-row">
                <strong class="mono-id badge ${isFull ? "warn" : "info"}">${escapeHtml(ev.eventId)}</strong>
                ${statusBadges}
              </div>
              <h3>${escapeHtml(ev.title)}</h3>
              ${ev.description ? `<p class="event-desc">${escapeHtml(ev.description)}</p>` : ""}
            </div>
            <div>
              ${
                isStudent
                  ? `<button type="button" class="btn ${isFull ? "secondary" : "primary"} sm btn-register-ev" data-event-id="${escapeHtml(ev.eventId)}">${isFull ? "Full · Try" : "Register"}</button>`
                  : `<span class="badge warn">Student role required</span>`
              }
            </div>
          </div>
          <div class="event-cap-row">
            <span><strong>${escapeHtml(confirmed)} / ${escapeHtml(capacity)}</strong> registered</span>
            <span class="muted">${isFull ? "0 slots remaining" : remaining === 1 ? "1 slot left" : `${escapeHtml(remaining)} slots left`}</span>
          </div>
          <div class="cap-bar"><div class="cap-fill ${isFull ? "is-full" : ""}" style="width:${pct}%"></div></div>
        </article>`;
      })
      .join("");
    container.innerHTML = cards;

    container.querySelectorAll(".btn-register-ev").forEach((btn) => {
      btn.addEventListener("click", () => handleRegister(btn.dataset.eventId, btn));
    });
  }

  function populateEventSelectors() {
    const isScopedStaffOrOrg =
      state.user &&
      (state.user.role === "CHECK_IN_STAFF" || state.user.role === "EVENT_ORGANIZER");
    const assignedSet = new Set((state.user && state.user.assignedEventIds) || []);
    const visibleEvents = isScopedStaffOrOrg
      ? state.events.filter((ev) => assignedSet.has(ev.eventId))
      : state.events;
    const validIds = new Set(visibleEvents.map((ev) => ev.eventId));
    const selectorIds = ["scanner-event-id", "org-event-id", "edit-ev-select"];
    const prevValues = {};
    selectorIds.forEach((id) => {
      prevValues[id] = el(id).value;
    });

    const options = visibleEvents.length
      ? visibleEvents
          .map((ev) => `<option value="${escapeHtml(ev.eventId)}">${escapeHtml(ev.eventId)} - ${escapeHtml(ev.title)}</option>`)
          .join("")
      : `<option value="">No assigned events</option>`;
    selectorIds.forEach((id) => {
      const node = el(id);
      node.innerHTML = options;
      if (prevValues[id] && validIds.has(prevValues[id])) {
        node.value = prevValues[id];
      }
    });
    syncEditEventFields();
  }

  function syncEditEventFields() {
    const selectedId = el("edit-ev-select").value;
    const ev = state.events.find((item) => item.eventId === selectedId);
    if (!ev) return;
    el("edit-ev-title").value = ev.title || "";
    el("edit-ev-capacity").value = String(ev.capacity || 1);
    el("edit-ev-desc").value = ev.description || "";
    el("edit-ev-eligible-statuses").value = (ev.eligibleStatuses || ["ENROLLED"]).join(", ");
  }

  async function handleRegister(eventId, btnEl) {
    if (!state.user || !state.user.studentId) {
      showBanner(
        "student-register-status",
        "err",
        "<strong>FORBIDDEN</strong>: Authenticated student account with a bound Student ID is required to register."
      );
      return;
    }
    setLoading(btnEl, true, "Registering...");
    const res = await api("POST", `/api/v1/events/${encodeURIComponent(eventId)}/registrations`, {
      studentId: state.user.studentId,
    });
    setLoading(btnEl, false);
    if (res.ok) {
      await refreshEvents();
      await refreshMyRegistrations();
      showBanner(
        "student-register-status",
        "ok",
        `Registered! ID: <strong class="mono-id">${escapeHtml(res.data.registrationId)}</strong> &middot; Status: <strong>${escapeHtml(res.data.status)}</strong>`
      );
      showToast(`Confirmed for ${eventId}`, "ok");
      return;
    }
    if (res.status === 503) {
      showBanner(
        "student-register-status",
        "warn",
        `<strong>Outcome Unverified (503 ${errCode(res)})</strong>: ${errMsg(res, "Registration service unreachable.")} <div class="row"><button id="btn-reconcile-reg" type="button" class="btn secondary sm">Reconcile Registration Status</button></div>`
      );
      const btn = el("btn-reconcile-reg");
      if (btn) {
        btn.addEventListener("click", async () => {
          const check = await api(
            "GET",
            `/api/v1/events/${encodeURIComponent(eventId)}/registrations?status=CONFIRMED&studentId=${encodeURIComponent(state.user.studentId)}`
          );
          if (check.ok && (check.data.registrations || []).length > 0) {
            await refreshEvents();
            await refreshMyRegistrations();
            showBanner("student-register-status", "ok", "Reconciliation found existing CONFIRMED registration.");
          } else if (check.ok) {
            showBanner("student-register-status", "info", "Reconciliation confirmed no registration exists; safe to register again.");
          } else {
            showBanner("student-register-status", "warn", "Registration status remains unverified.");
          }
        });
      }
      return;
    }
    showBanner(
      "student-register-status",
      "err",
      `<strong>${errCode(res)}</strong>: ${errMsg(res, "Request rejected")} <span class="muted">(corr: <code class="mono-id">${escapeHtml((res.data && res.data.correlationId) || "n/a")}</code>)</span>`
    );
  }

  function openPassModal(reg, triggerEl) {
    const modal = el("qr-pass-modal");
    if (!modal) return;
    state.modalTrigger = triggerEl || document.activeElement;
    const ev = state.events.find((item) => item.eventId === reg.eventId);
    el("qr-modal-title").textContent = ev ? `${ev.eventId} · ${ev.title}` : reg.eventId;
    let modalSvg = `<span class="muted">QR preview unavailable</span>`;
    if (window.CampusQR) {
      try {
        modalSvg = window.CampusQR.renderQrSvg(reg.qrToken, 250);
      } catch (_e) {
        modalSvg = `<span class="muted">Token exceeds QR preview capacity; copy token manually.</span>`;
      }
    }
    el("qr-modal-svg").innerHTML = modalSvg;
    el("qr-modal-meta").innerHTML = `
      <div>Registration: <strong class="mono-id">${escapeHtml(reg.registrationId)}</strong> &middot; <span class="badge ok">${escapeHtml(reg.status)}</span></div>
      <div class="qr-token-pill" style="margin-top:0.6rem;">${escapeHtml(reg.qrToken)}</div>`;
    modal.hidden = false;
    modal.classList.add("active");
    el("btn-close-qr-modal").focus();
  }

  function closePassModal() {
    const modal = el("qr-pass-modal");
    const wasOpen = Boolean(modal && !modal.hidden);
    if (modal) {
      modal.classList.remove("active");
      modal.hidden = true;
    }
    el("qr-modal-svg").innerHTML = "";
    el("qr-modal-meta").innerHTML = "";
    const trigger = state.modalTrigger;
    state.modalTrigger = null;
    if (wasOpen && trigger && typeof trigger.focus === "function" && trigger.offsetParent !== null) {
      trigger.focus();
    }
  }

  async function refreshMyRegistrations() {
    const container = el("my-registrations-list");
    if (!state.user || state.user.role !== "STUDENT") {
      container.innerHTML = `<div class="empty-state">Sign in as a student account to view QR passes.</div>`;
      return;
    }
    const epoch = state.sessionEpoch;
    const res = await api("GET", "/api/v1/my/registrations");
    if (!state.user || epoch !== state.sessionEpoch || state.user.role !== "STUDENT") return;
    if (!res.ok) {
      showBanner("my-registrations-list", "err", `<strong>${errCode(res)}</strong>: ${errMsg(res, "Unable to load registrations.")}`);
      return;
    }
    const regs = res.data.registrations || [];
    if (!regs.length) {
      container.innerHTML = `<div class="empty-state">No confirmed registrations yet.</div>`;
      return;
    }
    container.innerHTML = regs
      .map((r, idx) => {
        let svg = `<span class="muted">QR preview unavailable</span>`;
        if (window.CampusQR) {
          try {
            svg = window.CampusQR.renderQrSvg(r.qrToken, 148);
          } catch (_e) {
            svg = `<span class="muted">Token exceeds QR preview capacity; copy token manually.</span>`;
          }
        }
        return `
        <article class="qr-card" data-reg-index="${idx}">
          <div class="qr-ticket-banner">
            <span class="mono-id">FEU TECH PASS &middot; ${escapeHtml(r.eventId)}</span>
            <span class="badge ok">${escapeHtml(r.status)}</span>
          </div>
          <div class="qr-card-body">
            <div class="qr-frame">${svg}</div>
            <div class="qr-meta">
              <div class="muted">Registration ID: <strong class="mono-id">${escapeHtml(r.registrationId)}</strong></div>
              <div class="muted" style="margin-top:0.35rem;">Opaque QR Token:</div>
              <div class="qr-token-pill">${escapeHtml(r.qrToken)}</div>
              <div class="row" style="margin-top:0.7rem;">
                <button type="button" class="btn primary sm btn-fullscreen-qr" data-reg-index="${idx}">Present Pass</button>
                <button type="button" class="btn secondary sm btn-download-qr" data-reg-index="${idx}">Download PNG</button>
                <button type="button" class="btn secondary sm btn-copy-token" data-token="${escapeHtml(r.qrToken)}">Copy Token</button>
              </div>
            </div>
          </div>
        </article>`;
      })
      .join("");

    container.querySelectorAll(".btn-fullscreen-qr").forEach((btn) => {
      btn.addEventListener("click", () => {
        const reg = regs[Number(btn.dataset.regIndex)];
        if (reg) openPassModal(reg, btn);
      });
    });

    container.querySelectorAll(".btn-download-qr").forEach((btn) => {
      btn.addEventListener("click", () => {
        const reg = regs[Number(btn.dataset.regIndex)];
        if (!reg || !window.CampusQR || !window.CampusQR.renderQrPngDataUrl) return;
        try {
          const dataUrl = window.CampusQR.renderQrPngDataUrl(reg.qrToken, 360);
          const link = document.createElement("a");
          link.href = dataUrl;
          link.download = `PASS-${reg.eventId}-${reg.registrationId}.png`;
          document.body.appendChild(link);
          link.click();
          link.remove();
          showToast(`Downloaded PNG pass for ${reg.eventId}`, "ok");
        } catch (_err) {
          showToast("Token exceeds QR PNG export capacity; copy token instead", "err");
        }
      });
    });

    container.querySelectorAll(".btn-copy-token").forEach((btn) => {
      btn.addEventListener("click", () => {
        el("scanner-qr-token").value = btn.dataset.token;
        if (navigator.clipboard) navigator.clipboard.writeText(btn.dataset.token).catch(() => {});
        showToast("Copied QR token to clipboard & scanner field", "ok");
      });
    });
  }

  async function decodeCanvasQr(canvas) {
    if (typeof window.BarcodeDetector === "function") {
      try {
        const detector = new window.BarcodeDetector({ formats: ["qr_code"] });
        const found = await detector.detect(canvas);
        if (found && found.length && found[0].rawValue) {
          return String(found[0].rawValue).trim();
        }
      } catch (_err) {
        // Fall through to pure-JS decoder
      }
    }
    if (window.CampusQR && typeof window.CampusQR.decodeImageData === "function") {
      const ctx = canvas.getContext("2d", { willReadFrequently: true });
      const imgData = ctx.getImageData(0, 0, canvas.width, canvas.height);
      const decoded = window.CampusQR.decodeImageData(imgData);
      if (decoded) return String(decoded).trim();
    }
    return null;
  }

  async function applyDecodedQrToken(decoded, sourceLabel) {
    el("scanner-qr-token").value = decoded;
    showToast(`Decoded QR (${sourceLabel}): ${maskToken(decoded)}`, "ok");
    if (el("scanner-auto-checkin").checked) {
      await handleScannerCheckIn();
    }
  }

  async function startCameraStream() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      el("camera-status").textContent = "Webcam unavailable; use Upload QR Image or Manual input.";
      showToast("Camera API unavailable in this browser context", "err");
      return;
    }
    stopCameraStream();
    const epoch = ++state.cameraEpoch;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "environment", width: { ideal: 960 }, height: { ideal: 600 } },
        audio: false,
      });
      if (epoch !== state.cameraEpoch) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      state.cameraStream = stream;
      const video = el("scanner-video");
      video.srcObject = stream;
      await video.play();
      if (epoch !== state.cameraEpoch || state.cameraStream !== stream) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      video.closest(".camera-stage").classList.add("is-streaming");
      el("btn-camera-start").disabled = true;
      el("btn-camera-stop").disabled = false;
      el("camera-status").textContent = "Live camera active — hold QR pass inside frame...";

      const canvas = el("scanner-canvas");
      const ctx = canvas.getContext("2d", { willReadFrequently: true });
      let decoding = false;

      const scanFrame = async () => {
        if (epoch !== state.cameraEpoch || state.cameraStream !== stream) return;
        if (!decoding && !state.lastUnverified && video.readyState >= 2 && video.videoWidth > 0) {
          decoding = true;
          try {
            const scale = Math.min(1, 560 / video.videoWidth);
            canvas.width = Math.max(120, Math.round(video.videoWidth * scale));
            canvas.height = Math.max(120, Math.round(video.videoHeight * scale));
            ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
            const tok = await decodeCanvasQr(canvas);
            if (epoch !== state.cameraEpoch || state.cameraStream !== stream) return;
            const nowMs = Date.now();
            if (tok && (tok !== state.lastScannedToken || nowMs > state.cameraCooldownUntil)) {
              state.lastScannedToken = tok;
              el("camera-status").textContent = `Scanned token ${maskToken(tok)}`;
              await applyDecodedQrToken(tok, "camera");
              state.cameraCooldownUntil = Date.now() + 3000;
            }
          } finally {
            decoding = false;
          }
        }
        if (epoch === state.cameraEpoch && state.cameraStream === stream) {
          state.cameraRafId = requestAnimationFrame(scanFrame);
        }
      };
      state.cameraRafId = requestAnimationFrame(scanFrame);
    } catch (err) {
      if (epoch === state.cameraEpoch) {
        el("camera-status").textContent = `Camera permission/hardware error: ${err && err.message ? err.message : "denied"}`;
        showToast("Could not start camera; use Upload QR Image instead", "err");
      }
    }
  }

  function stopCameraStream() {
    state.cameraEpoch++;
    if (state.cameraRafId !== null) {
      cancelAnimationFrame(state.cameraRafId);
      state.cameraRafId = null;
    }
    if (state.cameraStream) {
      state.cameraStream.getTracks().forEach((track) => track.stop());
      state.cameraStream = null;
    }
    const video = el("scanner-video");
    if (video) {
      video.srcObject = null;
      const stage = video.closest(".camera-stage");
      if (stage) stage.classList.remove("is-streaming");
    }
    const startBtn = el("btn-camera-start");
    const stopBtn = el("btn-camera-stop");
    if (startBtn) startBtn.disabled = false;
    if (stopBtn) stopBtn.disabled = true;
    const camStatus = el("camera-status");
    if (camStatus) camStatus.textContent = "Camera idle";
  }

  async function handleQrImageFile(file) {
    if (!file) return;
    const preview = el("qr-upload-preview");
    preview.innerHTML = `<div class="alert info">${iconSvg("info")}<div>Decoding <strong>${escapeHtml(file.name)}</strong> locally...</div></div>`;
    const url = URL.createObjectURL(file);
    try {
      const img = await new Promise((resolve, reject) => {
        const image = new Image();
        image.onload = () => resolve(image);
        image.onerror = () => reject(new Error("Could not read uploaded image."));
        image.src = url;
      });
      const maxDim = 640;
      const ratio = Math.min(1, maxDim / Math.max(img.naturalWidth || 1, img.naturalHeight || 1));
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(64, Math.round((img.naturalWidth || 320) * ratio));
      canvas.height = Math.max(64, Math.round((img.naturalHeight || 320) * ratio));
      const ctx = canvas.getContext("2d", { willReadFrequently: true });
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      const decoded = await decodeCanvasQr(canvas);
      if (!decoded) {
        preview.innerHTML = `<div class="alert err">${iconSvg("err")}<div><strong>QR_NOT_FOUND</strong>: No readable QR pass detected in <em>${escapeHtml(file.name)}</em>. Ensure the whole QR code &amp; white margin are visible.</div></div>`;
        return;
      }
      preview.innerHTML = `<div class="alert ok">${iconSvg("ok")}<div>Decoded QR token <code class="mono-id">${escapeHtml(maskToken(decoded))}</code> from <em>${escapeHtml(file.name)}</em>.</div></div>`;
      await applyDecodedQrToken(decoded, "image");
    } catch (err) {
      preview.innerHTML = `<div class="alert err">${iconSvg("err")}<div>${escapeHtml(err && err.message ? err.message : "Failed to decode image.")}</div></div>`;
    } finally {
      URL.revokeObjectURL(url);
    }
  }

  async function handleScannerCheckIn() {
    const eventId = el("scanner-event-id").value;
    const qrToken = el("scanner-qr-token").value.trim();
    if (!eventId || !qrToken) {
      setScannerStepState({ validate: "is-error" });
      showBanner("scanner-result", "err", "<strong>INVALID_PAYLOAD</strong>: Select an event and enter or scan a QR token.");
      return;
    }
    const btn = el("btn-scanner-checkin");
    setLoading(btn, true, "Validating...");
    setScannerReconcileControls(false, false);
    state.lastUnverified = null;
    setScannerStepState({ validate: "is-active" });

    // Step 1: Pre-validate via Registration to retain registrationId for safe reconciliation
    const valRes = await api("POST", "/api/v1/registrations/validate", { eventId, qrToken });
    if (!valRes.ok) {
      setLoading(btn, false);
      if (valRes.status === 503) {
        setScannerStepState({ validate: "is-warn" });
        showBanner("scanner-result", "warn", `<strong>UNVERIFIED</strong>: Validation service unavailable (${errCode(valRes)}). No check-in was assumed.`);
        recordScanHistory(eventId, qrToken, "UNVERIFIED", "warn");
      } else {
        setScannerStepState({ validate: "is-error" });
        showBanner("scanner-result", "err", `<strong>${errCode(valRes)}</strong>: ${errMsg(valRes, "Token validation failed.")}`);
        recordScanHistory(eventId, qrToken, errCode(valRes), "err");
      }
      return;
    }
    if (!valRes.data.valid || !valRes.data.registrationId) {
      setLoading(btn, false);
      setScannerStepState({ validate: "is-error" });
      showBanner("scanner-result", "err", `<strong>INVALID_TOKEN</strong>: QR token is invalid, unconfirmed, or belongs to a different event.`);
      recordScanHistory(eventId, qrToken, "INVALID_TOKEN", "err");
      return;
    }

    const regId = valRes.data.registrationId;
    setScannerStepState({ validate: "is-done", checkin: "is-active" });
    setLoading(btn, true, "Checking In...");
    await submitCheckInOnce(eventId, qrToken, regId);
    setLoading(btn, false);
  }

  async function submitCheckInOnce(eventId, qrToken, regId) {
    const chkRes = await api("POST", `/api/v1/events/${encodeURIComponent(eventId)}/check-ins`, { qrToken });
    if (chkRes.status === 201) {
      setScannerReconcileControls(false, false);
      state.lastUnverified = null;
      setScannerStepState({ validate: "is-done", checkin: "is-done" });
      showBanner(
        "scanner-result",
        "ok",
        `<strong>CHECKED_IN</strong> &middot; Registration <code class="mono-id">${escapeHtml(chkRes.data.registrationId)}</code> at <code class="mono-id">${escapeHtml(chkRes.data.checkInTime)}</code>`
      );
      recordScanHistory(eventId, qrToken, "CHECKED_IN", "ok");
      showToast("Participant checked in", "ok");
      return;
    }
    if (chkRes.status === 409) {
      setScannerReconcileControls(false, false);
      setScannerStepState({ validate: "is-done", checkin: "is-warn" });
      showBanner(
        "scanner-result",
        "warn",
        `<strong>ALREADY_CHECKED_IN</strong>: ${errMsg(chkRes, "Already checked in.")} (Registration <code class="mono-id">${escapeHtml(regId)}</code> unchanged)`
      );
      recordScanHistory(eventId, qrToken, "ALREADY_CHECKED_IN", "warn");
      return;
    }
    if (chkRes.status === 404) {
      setScannerReconcileControls(false, false);
      setScannerStepState({ validate: "is-done", checkin: "is-error" });
      showBanner("scanner-result", "err", `<strong>INVALID_TOKEN</strong>: ${errMsg(chkRes, "QR token is invalid.")}`);
      recordScanHistory(eventId, qrToken, "INVALID_TOKEN", "err");
      return;
    }
    if (chkRes.status === 503) {
      state.lastUnverified = { eventId, qrToken, registrationId: regId };
      setScannerReconcileControls(true, false);
      setScannerStepState({ validate: "is-done", checkin: "is-warn", reconcile: "is-active" });
      showBanner(
        "scanner-result",
        "warn",
        `<strong>UNVERIFIED (HTTP 503)</strong>: Attendance outcome unknown for <code class="mono-id">${escapeHtml(regId)}</code>. Reconcile status before retrying.`
      );
      recordScanHistory(eventId, qrToken, "UNVERIFIED (503)", "warn");
      return;
    }
    state.lastUnverified = null;
    setScannerReconcileControls(false, false);
    setScannerStepState({ validate: "is-done", checkin: "is-error" });
    showBanner("scanner-result", "err", `<strong>${errCode(chkRes)}</strong>: ${errMsg(chkRes, "Check-in failed.")}`);
    recordScanHistory(eventId, qrToken, errCode(chkRes), "err");
  }

  async function handleScannerReconcile() {
    if (!state.lastUnverified) return;
    const btn = el("btn-scanner-reconcile");
    setLoading(btn, true, "Reconciling...");
    const { eventId, registrationId } = state.lastUnverified;
    const res = await api(
      "GET",
      `/api/v1/events/${encodeURIComponent(eventId)}/registrations/${encodeURIComponent(registrationId)}/attendance`
    );
    setLoading(btn, false);
    if (!res.ok) {
      setScannerReconcileControls(true, false);
      setScannerStepState({ validate: "is-done", checkin: "is-warn", reconcile: "is-warn" });
      showBanner(
        "scanner-result",
        "warn",
        `<strong>STILL UNVERIFIED</strong>: Unable to retrieve authoritative attendance status (${errCode(res)}).`
      );
      return;
    }
    if (res.data.checkedIn) {
      state.lastUnverified = null;
      setScannerReconcileControls(false, false);
      setScannerStepState({ validate: "is-done", checkin: "is-done", reconcile: "is-done" });
      showBanner(
        "scanner-result",
        "ok",
        `<strong>RECONCILED: CHECKED_IN</strong> &middot; Earlier check-in already committed at <code class="mono-id">${escapeHtml(res.data.checkInTime)}</code>. No duplicate submitted.`
      );
      return;
    }
    setScannerReconcileControls(true, true);
    setScannerStepState({ validate: "is-done", checkin: "is-warn", reconcile: "is-done" });
    showBanner(
      "scanner-result",
      "info",
      `<strong>RECONCILED: ABSENT</strong> &middot; Confirmed no attendance record exists for <code class="mono-id">${escapeHtml(registrationId)}</code>. Safe to click <em>Safe Retry Check-in</em>.`
    );
  }

  async function handleLoadReport() {
    const eventId = el("org-event-id").value;
    if (!eventId) return;
    const btn = el("btn-load-report");
    setLoading(btn, true, "Loading...");
    const res = await api(
      "GET",
      `/api/v1/events/${encodeURIComponent(eventId)}/reports/attendance`,
      undefined,
      { timeoutMs: REPORT_TIMEOUT_MS }
    );
    setLoading(btn, false);
    if (!res.ok) {
      showBanner(
        "org-report-output",
        "err",
        `<strong>Report Unavailable (${errCode(res)})</strong>: ${errMsg(res, "Combined source data unavailable.")}`
      );
      return;
    }
    const r = res.data;
    const regCount = Number(r.registeredCount) || 0;
    const chkCount = Number(r.checkedInCount) || 0;
    const notChkCount = Number(r.notCheckedInCount) || 0;
    const pct = regCount > 0 ? Math.min(100, Math.round((chkCount / regCount) * 100)) : 0;
    const rows = (r.participants || [])
      .map(
        (p) => `
      <tr>
        <td class="mono-id">${escapeHtml(p.registrationId)}</td>
        <td class="mono-id">${escapeHtml(p.studentId)}</td>
        <td><span class="badge info">${escapeHtml(p.registrationStatus)}</span></td>
        <td>${p.checkedIn ? '<span class="badge ok">CHECKED_IN</span>' : '<span class="badge warn">NOT_CHECKED_IN</span>'}</td>
        <td class="mono-id">${escapeHtml(p.checkInTime || "—")}</td>
      </tr>`
      )
      .join("");
    el("org-report-output").innerHTML = `
      <div class="stat-strip">
        <div class="stat-box"><div class="num">${escapeHtml(regCount)}</div><div class="muted">Registered</div></div>
        <div class="stat-box"><div class="num">${escapeHtml(chkCount)}</div><div class="muted">Checked In</div></div>
        <div class="stat-box"><div class="num">${escapeHtml(notChkCount)}</div><div class="muted">Not Checked In</div></div>
      </div>
      <div class="rate-card">
        <div class="rate-top"><span>Attendance Turnout Rate</span><span>${escapeHtml(pct)}%</span></div>
        <div class="cap-bar"><div class="cap-fill" style="width:${pct}%"></div></div>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Registration ID</th><th>Student ID</th><th>Reg Status</th><th>Attendance</th><th>Check-in Time</th></tr></thead>
          <tbody>${rows || '<tr><td colspan="5" class="muted">No confirmed registrations yet.</td></tr>'}</tbody>
        </table>
      </div>`;
  }

  async function handleLoadParticipantsOnly() {
    const eventId = el("org-event-id").value;
    if (!eventId) return;
    const btn = el("btn-load-participants");
    setLoading(btn, true, "Loading...");
    const res = await api("GET", `/api/v1/events/${encodeURIComponent(eventId)}/registrations?status=CONFIRMED`);
    setLoading(btn, false);
    if (!res.ok) {
      showBanner("org-report-output", "err", `<strong>${errCode(res)}</strong>: ${errMsg(res, "Unable to load participants.")}`);
      return;
    }
    const rows = (res.data.registrations || [])
      .map(
        (p) => `<tr><td class="mono-id">${escapeHtml(p.registrationId)}</td><td class="mono-id">${escapeHtml(p.studentId)}</td><td><span class="badge info">${escapeHtml(p.status)}</span></td></tr>`
      )
      .join("");
    el("org-report-output").innerHTML = `<div class="table-wrap"><table><thead><tr><th>Registration ID</th><th>Student ID</th><th>Status</th></tr></thead><tbody>${rows || '<tr><td colspan="3" class="muted">No confirmed participants yet.</td></tr>'}</tbody></table></div>`;
  }

  function renderAuditTable() {
    const container = el("admin-audit-output");
    const logs = state.auditLogs || [];
    const term = state.auditSearch.trim().toLowerCase();
    const byCat = state.auditFilter === "ALL" ? logs : logs.filter((l) => l.category === state.auditFilter);
    const filtered = !term
      ? byCat
      : byCat.filter((l) =>
          `${l.service || ""} ${l.category || ""} ${l.action || ""} ${l.outcome || ""} ${l.errorCode || ""} ${l.correlationId || ""}`
            .toLowerCase()
            .includes(term)
        );
    const unavailableBanner = container.dataset.unavailable
      ? `<div class="alert warn">${iconSvg("warn")}<div>Audit logs unavailable from: ${escapeHtml(container.dataset.unavailable)}</div></div>`
      : "";
    if (!filtered.length) {
      container.innerHTML = `${unavailableBanner}<div class="empty-state">No matching audit entries for this filter.</div>`;
      return;
    }
    container.innerHTML = `${unavailableBanner}<div class="table-wrap"><table><thead><tr><th>Timestamp</th><th>Service</th><th>Category</th><th>Action</th><th>Outcome</th><th>Correlation ID</th></tr></thead><tbody>${filtered
      .slice(0, 40)
      .map(
        (l) => `<tr><td class="mono-id">${escapeHtml(l.timestamp)}</td><td class="mono-id">${escapeHtml(l.service)}</td><td>${escapeHtml(l.category)}</td><td class="mono-id">${escapeHtml(l.action)}</td><td>${escapeHtml(l.outcome)} ${l.errorCode ? `<span class="badge warn">${escapeHtml(l.errorCode)}</span>` : ""}</td><td class="mono-id">${escapeHtml(l.correlationId)}</td></tr>`
      )
      .join("")}</tbody></table></div>`;
  }

  async function refreshAdminConsole() {
    if (!state.user || state.user.role !== "ADMINISTRATOR") return;
    const epoch = state.sessionEpoch;
    const refreshBtn = el("btn-admin-refresh");
    setLoading(refreshBtn, true, "Refreshing...");
    const [healthRes, accRes, notifRes, failRes, auditRes] = await Promise.all([
      api("GET", "/api/v1/admin/service-health"),
      api("GET", "/api/v1/admin/accounts"),
      api("GET", "/api/v1/notifications"),
      api("GET", "/api/v1/admin/failed-notifications"),
      api("GET", "/api/v1/admin/audit-logs"),
    ]);
    setLoading(refreshBtn, false);
    if (!state.user || epoch !== state.sessionEpoch || state.user.role !== "ADMINISTRATOR") return;

    if (!healthRes.ok) {
      showBanner("admin-health-output", "err", `<strong>${errCode(healthRes)}</strong>: ${errMsg(healthRes, "Unable to query service health.")}`);
      return;
    }

    const services = healthRes.data.services || {};
    el("admin-health-output").innerHTML = `<div class="table-wrap"><table><thead><tr><th>Service</th><th>Status</th></tr></thead><tbody>${Object.entries(services)
      .map(([k, v]) => `<tr><td class="mono-id">${escapeHtml(k)}</td><td><span class="badge ${v === "ok" ? "ok" : "err"}">${escapeHtml(v)}</span></td></tr>`)
      .join("")}</tbody></table></div>`;

    if (!accRes.ok) {
      showBanner("admin-accounts-output", "err", `<strong>${errCode(accRes)}</strong>: ${errMsg(accRes, "Unable to load sample accounts.")}`);
    } else {
      const accounts = (accRes.data && accRes.data.accounts) || [];
      el("admin-accounts-output").innerHTML = `<div class="table-wrap"><table><thead><tr><th>Account ID</th><th>Username</th><th>Role</th><th>Assigned Events</th><th>Action</th></tr></thead><tbody>${accounts
        .map(
          (a) =>
            `<tr><td class="mono-id">${escapeHtml(a.accountId)}</td><td><strong>${escapeHtml(a.username)}</strong>${a.studentId ? ` <span class="muted">(${escapeHtml(a.studentId)})</span>` : ""}</td><td><span class="badge info">${escapeHtml(a.role)}</span></td><td class="mono-id">${escapeHtml((a.assignedEventIds || []).join(", ") || "—")}</td><td><button type="button" class="btn secondary sm btn-edit-account" data-account-id="${escapeHtml(a.accountId)}" data-role="${escapeHtml(a.role)}" data-events="${escapeHtml((a.assignedEventIds || []).join(", "))}">Edit</button></td></tr>`
        )
        .join("")}</tbody></table></div>`;

      el("admin-accounts-output").querySelectorAll(".btn-edit-account").forEach((btn) => {
        btn.addEventListener("click", () => {
          el("assign-account-id").value = btn.dataset.accountId;
          el("assign-role").value = btn.dataset.role;
          el("assign-events").value = btn.dataset.events;
          switchSegmented("data-adm-mode", "assign", {
            create: "create-account-form",
            assign: "update-assignments-form",
          });
          el("admin-accounts-output").querySelectorAll("tbody tr").forEach((tr) => tr.classList.remove("is-selected"));
          const row = btn.closest("tr");
          if (row) row.classList.add("is-selected");
        });
      });
    }

    const failUnavailable = (failRes.ok && failRes.data && failRes.data.unavailableServices) || [];
    const notifWarnings = [];
    if (!notifRes.ok) {
      notifWarnings.push(`Notification queue unavailable (${errCode(notifRes)}): ${errMsg(notifRes, "notification-service unreachable.")}`);
    }
    if (!failRes.ok) {
      notifWarnings.push(`Failed-notification feed unavailable (${errCode(failRes)}): ${errMsg(failRes, "Unable to query failed notifications.")}`);
    } else if (failUnavailable.length > 0) {
      notifWarnings.push(`Failed-notification feed incomplete; unavailable services: ${escapeHtml(failUnavailable.join(", "))}`);
    }

    if (!notifRes.ok && !failRes.ok) {
      showBanner("admin-notifications-output", "err", notifWarnings.join("<br>"));
    } else {
      const notifs = (notifRes.ok && notifRes.data && notifRes.data.notifications) || [];
      const failed = (failRes.ok && failRes.data && failRes.data.failedNotifications) || [];
      const warningBanner = notifWarnings.length
        ? `<div class="alert warn">${iconSvg("warn")}<div>${notifWarnings.join("<br>")}</div></div>`
        : "";
      const acceptedLabel = notifRes.ok ? escapeHtml(notifs.length) : "Unavailable";
      const failedLabel = !failRes.ok
        ? "Unavailable"
        : failUnavailable.length
          ? `${escapeHtml(failed.length)} (Partial)`
          : escapeHtml(failed.length);
      const failRows = failed
        .map(
          (f) =>
            `<tr><td class="mono-id">${escapeHtml(f.service || "")}</td><td>${escapeHtml(f.type)}</td><td class="mono-id">${escapeHtml(f.eventId)}</td><td class="mono-id">${escapeHtml(f.registrationId)}</td><td class="mono-id">${escapeHtml(f.correlationId)}</td><td>${escapeHtml(f.errorMessage)}</td></tr>`
        )
        .join("");
      const failedSection = failed.length
        ? `<h4>Failed Notifications Pending Review</h4><div class="table-wrap"><table><thead><tr><th>Service</th><th>Type</th><th>Event</th><th>Reg ID</th><th>Correlation ID</th><th>Error</th></tr></thead><tbody>${failRows}</tbody></table></div>`
        : "";
      const notifBodyRows = !notifRes.ok
        ? '<tr><td colspan="5" class="muted">Notification service unavailable.</td></tr>'
        : notifs.length
          ? notifs
              .slice(0, 20)
              .map((n) => `<tr><td class="mono-id">${escapeHtml(n.notificationId)}</td><td>${escapeHtml(n.type)}</td><td class="mono-id">${escapeHtml(n.eventId)}</td><td class="mono-id">${escapeHtml(n.registrationId)}</td><td><span class="badge ok">${escapeHtml(n.status)}</span> <span class="muted">${escapeHtml(n.deliveryStatus)}</span></td></tr>`)
              .join("")
          : '<tr><td colspan="5" class="muted">No notifications queued yet.</td></tr>';
      el("admin-notifications-output").innerHTML = `
        ${warningBanner}
        <p class="muted">Accepted: <strong>${acceptedLabel}</strong> &middot; Failed-review entries: <strong>${failedLabel}</strong></p>
        <div class="table-wrap"><table><thead><tr><th>ID</th><th>Type</th><th>Event</th><th>Reg ID</th><th>Status / Delivery</th></tr></thead><tbody>${notifBodyRows}</tbody></table></div>${failedSection}`;
    }

    if (!auditRes.ok) {
      showBanner("admin-audit-output", "err", `<strong>${errCode(auditRes)}</strong>: ${errMsg(auditRes, "Unable to retrieve audit logs.")}`);
    } else {
      state.auditLogs = (auditRes.data && auditRes.data.logs) || [];
      const unavailable = (auditRes.data && auditRes.data.unavailableServices) || [];
      el("admin-audit-output").dataset.unavailable = unavailable.join(", ");
      renderAuditTable();
    }
  }

  function syncStudentIdVisibility() {
    const isStudent = el("acc-role").value === "STUDENT";
    const wrap = el("acc-student-id-wrap");
    const input = el("acc-student-id");
    wrap.style.display = isStudent ? "block" : "none";
    input.required = isStudent;
    if (!isStudent) input.value = "";
  }

  // Navigation & Mode Wiring
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });

  document.querySelectorAll(".seg-btn[data-scan-mode]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const mode = btn.dataset.scanMode;
      if (mode !== "camera") stopCameraStream();
      switchSegmented("data-scan-mode", mode, {
        camera: "scanner-pane-camera",
        upload: "scanner-pane-upload",
      });
    });
  });

  document.querySelectorAll(".seg-btn[data-org-mode]").forEach((btn) => {
    btn.addEventListener("click", () => {
      switchSegmented("data-org-mode", btn.dataset.orgMode, {
        create: "create-event-form",
        edit: "edit-event-form",
      });
      if (btn.dataset.orgMode === "edit") syncEditEventFields();
    });
  });

  document.querySelectorAll(".seg-btn[data-adm-mode]").forEach((btn) => {
    btn.addEventListener("click", () => {
      switchSegmented("data-adm-mode", btn.dataset.admMode, {
        create: "create-account-form",
        assign: "update-assignments-form",
      });
    });
  });

  document.querySelectorAll("#admin-audit-filters .filter-pill").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.auditFilter = btn.dataset.category;
      document.querySelectorAll("#admin-audit-filters .filter-pill").forEach((p) => {
        p.classList.toggle("active", p === btn);
        p.setAttribute("aria-pressed", p === btn ? "true" : "false");
      });
      renderAuditTable();
    });
  });

  el("admin-audit-search").addEventListener("input", (e) => {
    state.auditSearch = e.target.value || "";
    renderAuditTable();
  });

  el("btn-toggle-password").addEventListener("click", () => {
    const pw = el("login-password");
    const btn = el("btn-toggle-password");
    const reveal = pw.type === "password";
    pw.type = reveal ? "text" : "password";
    btn.textContent = reveal ? "Hide" : "Show";
    btn.setAttribute("aria-pressed", reveal ? "true" : "false");
  });

  document.querySelectorAll(".quick-login").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".quick-login").forEach((b) => b.classList.remove("is-selected"));
      btn.classList.add("is-selected");
      el("login-username").value = btn.dataset.user;
      el("login-password").value = btn.dataset.pass;
    });
  });

  function resetWorkspaceUi() {
    state.sessionEpoch++;
    stopCameraStream();
    closePassModal();
    state.events = [];
    state.lastUnverified = null;
    state.auditLogs = [];
    state.auditFilter = "ALL";
    state.auditSearch = "";
    state.scanHistory = [];
    state.lastScannedToken = null;
    renderScanHistory();
    switchSegmented("data-scan-mode", "manual", {
      camera: "scanner-pane-camera",
      upload: "scanner-pane-upload",
    });
    switchSegmented("data-org-mode", "create", {
      create: "create-event-form",
      edit: "edit-event-form",
    });
    switchSegmented("data-adm-mode", "create", {
      create: "create-account-form",
      assign: "update-assignments-form",
    });
    const pw = el("login-password");
    const pwToggle = el("btn-toggle-password");
    if (pw) pw.type = "password";
    if (pwToggle) {
      pwToggle.textContent = "Show";
      pwToggle.setAttribute("aria-pressed", "false");
    }
    document.querySelectorAll(".quick-login").forEach((b) => b.classList.remove("is-selected"));
    document.querySelectorAll("#admin-audit-filters .filter-pill").forEach((p) => {
      const isAll = p.dataset.category === "ALL";
      p.classList.toggle("active", isAll);
      p.setAttribute("aria-pressed", isAll ? "true" : "false");
    });
    el("admin-audit-search").value = "";
    el("create-event-form").reset();
    el("ev-capacity").value = "25";
    el("ev-eligible-statuses").value = "ENROLLED";
    el("edit-event-form").reset();
    el("create-account-form").reset();
    el("update-assignments-form").reset();
    syncStudentIdVisibility();
    setScannerReconcileControls(false, false);
    setScannerStepState({});
    el("student-events-list").innerHTML = `<div class="empty-state">Sign in to inspect campus events and register for an open slot.</div>`;
    el("student-register-status").innerHTML = "";
    el("my-registrations-list").innerHTML = `<div class="empty-state">Confirmed event passes will appear here with a scannable QR code.</div>`;
    el("scanner-event-id").innerHTML = "";
    el("scanner-qr-token").value = "";
    el("scanner-qr-file").value = "";
    el("qr-upload-preview").innerHTML = "";
    el("org-event-id").innerHTML = "";
    el("edit-ev-select").innerHTML = "";
    el("org-event-status").innerHTML = "";
    el("org-report-output").innerHTML = `<div class="empty-state">Select an event and load the combined report.</div>`;
    el("admin-health-output").innerHTML = "";
    el("admin-accounts-output").innerHTML = "";
    el("admin-account-status").innerHTML = "";
    el("admin-notifications-output").innerHTML = "";
    el("admin-audit-output").innerHTML = "";
    el("admin-audit-output").dataset.unavailable = "";
    el("toast-stack").innerHTML = "";
    showBanner("scanner-result", "info", "<strong>Awaiting QR Scan</strong><div>Select an assigned event and scan via camera, QR PNG upload, or manual token input.</div>");
  }

  el("login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = el("btn-login-submit");
    setLoading(btn, true, "Signing In...");
    const username = el("login-username").value.trim();
    const password = el("login-password").value;
    const res = await api("POST", "/api/v1/auth/login", { username, password });
    setLoading(btn, false);
    if (!res.ok) {
      showBanner("global-status", "err", `<strong>${errCode(res)}</strong>: ${errMsg(res, "Authentication failed.")}`);
      return;
    }
    resetWorkspaceUi();
    state.sessionToken = res.data.sessionToken;
    state.user = res.data.user;
    updateSessionUi();
    syncRoleAccessUi();
    showBanner("global-status", "ok", `Signed in as <strong>${escapeHtml(state.user.username)}</strong> (<span class="mono-id">${escapeHtml(state.user.role)}</span>).`);
    if (state.user.role === "STUDENT") {
      await Promise.all([refreshEvents(), refreshMyRegistrations()]);
      switchTab("tab-student");
    } else if (state.user.role === "CHECK_IN_STAFF") {
      await refreshEvents();
      switchTab("tab-scanner");
    } else if (state.user.role === "EVENT_ORGANIZER") {
      await refreshEvents();
      switchTab("tab-organizer");
    } else {
      await Promise.all([refreshEvents(), refreshAdminConsole()]);
      switchTab("tab-admin");
    }
  });

  el("btn-logout").addEventListener("click", async () => {
    state.sessionEpoch++;
    await api("POST", "/api/v1/auth/logout");
    state.sessionToken = null;
    state.user = null;
    resetWorkspaceUi();
    el("login-username").value = "";
    el("login-password").value = "";
    updateSessionUi();
    syncRoleAccessUi();
    showBanner("global-status", "info", "Signed out cleanly.");
  });

  el("btn-refresh-events").addEventListener("click", refreshEvents);
  el("btn-refresh-my-regs").addEventListener("click", refreshMyRegistrations);
  el("btn-scanner-checkin").addEventListener("click", handleScannerCheckIn);
  el("btn-scanner-reconcile").addEventListener("click", handleScannerReconcile);
  el("btn-scanner-retry").addEventListener("click", async () => {
    if (!state.lastUnverified) return;
    const btn = el("btn-scanner-retry");
    setLoading(btn, true, "Retrying...");
    const { eventId, qrToken, registrationId } = state.lastUnverified;
    await submitCheckInOnce(eventId, qrToken, registrationId);
    setLoading(btn, false);
  });

  el("scanner-qr-token").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      handleScannerCheckIn();
    }
  });

  el("btn-camera-start").addEventListener("click", startCameraStream);
  el("btn-camera-stop").addEventListener("click", stopCameraStream);

  el("scanner-qr-file").addEventListener("change", (e) => {
    const file = e.target.files && e.target.files[0];
    if (file) handleQrImageFile(file);
    e.target.value = "";
  });

  const dropzone = el("qr-upload-dropzone");
  dropzone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropzone.classList.add("is-dragover");
  });
  dropzone.addEventListener("dragleave", () => dropzone.classList.remove("is-dragover"));
  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.classList.remove("is-dragover");
    const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (file) handleQrImageFile(file);
  });

  el("btn-close-qr-modal").addEventListener("click", closePassModal);
  el("qr-pass-modal").addEventListener("click", (e) => {
    if (e.target === el("qr-pass-modal")) closePassModal();
  });
  document.addEventListener("keydown", (e) => {
    const modal = el("qr-pass-modal");
    if (modal.hidden) return;
    if (e.key === "Escape") {
      closePassModal();
      return;
    }
    if (e.key === "Tab") {
      const closeBtn = el("btn-close-qr-modal");
      e.preventDefault();
      closeBtn.focus();
    }
  });

  el("edit-ev-select").addEventListener("change", syncEditEventFields);

  el("create-event-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const cap = Number(el("ev-capacity").value.trim());
    if (!Number.isInteger(cap) || cap < 1) {
      showBanner("org-event-status", "err", "<strong>INVALID_PAYLOAD</strong>: Capacity must be a positive whole number.");
      return;
    }
    const btn = el("btn-create-event");
    setLoading(btn, true, "Creating...");
    const payload = {
      title: el("ev-title").value.trim(),
      capacity: cap,
      description: el("ev-desc").value.trim(),
      eligibleStatuses: parseCsvList(el("ev-eligible-statuses").value),
    };
    const evId = el("ev-id").value.trim();
    if (evId) payload.eventId = evId;
    const res = await api("POST", "/api/v1/events", payload);
    setLoading(btn, false);
    if (!res.ok) {
      showBanner("org-event-status", "err", `<strong>${errCode(res)}</strong>: ${errMsg(res, "Unable to create event.")}`);
      return;
    }
    el("create-event-form").reset();
    el("ev-capacity").value = "25";
    el("ev-eligible-statuses").value = "ENROLLED";
    const meRes = await api("GET", "/api/v1/auth/me");
    if (meRes.ok && meRes.data && meRes.data.accountId) {
      state.user = meRes.data;
    } else if (
      state.user &&
      state.user.role === "EVENT_ORGANIZER" &&
      res.data &&
      res.data.eventId &&
      !(state.user.assignedEventIds || []).includes(res.data.eventId)
    ) {
      state.user.assignedEventIds = [...(state.user.assignedEventIds || []), res.data.eventId];
    }
    syncRoleAccessUi();
    await refreshEvents();
    showBanner("org-event-status", "ok", `Created event <strong class="mono-id">${escapeHtml(res.data.eventId)}</strong> (capacity ${escapeHtml(Number(res.data.capacity))}).`);
    showToast(`Created ${res.data.eventId}`, "ok");
  });

  el("edit-event-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const eventId = el("edit-ev-select").value;
    if (!eventId) return;
    const cap = Number(el("edit-ev-capacity").value.trim());
    if (!Number.isInteger(cap) || cap < 1) {
      showBanner("org-event-status", "err", "<strong>INVALID_PAYLOAD</strong>: Capacity must be a positive whole number.");
      return;
    }
    const btn = el("btn-edit-event");
    setLoading(btn, true, "Saving...");
    const payload = {
      title: el("edit-ev-title").value.trim(),
      capacity: cap,
      description: el("edit-ev-desc").value.trim(),
      eligibleStatuses: parseCsvList(el("edit-ev-eligible-statuses").value),
    };
    const res = await api("PUT", `/api/v1/events/${encodeURIComponent(eventId)}`, payload);
    setLoading(btn, false);
    if (!res.ok) {
      showBanner("org-event-status", "err", `<strong>${errCode(res)}</strong>: ${errMsg(res, "Unable to save event changes.")}`);
      return;
    }
    await refreshEvents();
    showBanner("org-event-status", "ok", `Updated event <strong class="mono-id">${escapeHtml(res.data.eventId)}</strong> (capacity ${escapeHtml(Number(res.data.capacity))}).`);
    showToast(`Saved ${res.data.eventId}`, "ok");
  });

  el("acc-role").addEventListener("change", syncStudentIdVisibility);
  syncStudentIdVisibility();

  el("create-account-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = el("btn-create-account");
    setLoading(btn, true, "Creating...");
    const role = el("acc-role").value;
    const payload = {
      username: el("acc-username").value.trim(),
      password: el("acc-password").value,
      role,
      studentId: role === "STUDENT" ? el("acc-student-id").value.trim() || null : null,
      assignedEventIds: parseCsvList(el("acc-events").value),
    };
    const res = await api("POST", "/api/v1/admin/accounts", payload);
    setLoading(btn, false);
    if (!res.ok) {
      showBanner("admin-account-status", "err", `<strong>${errCode(res)}</strong>: ${errMsg(res, "Unable to create account.")}`);
      return;
    }
    el("create-account-form").reset();
    syncStudentIdVisibility();
    await refreshAdminConsole();
    showBanner("admin-account-status", "ok", `Created account <strong>${escapeHtml(res.data.username)}</strong> (<span class="mono-id">${escapeHtml(res.data.accountId)}</span>).`);
    showToast(`Created ${res.data.username}`, "ok");
  });

  el("update-assignments-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const accountId = el("assign-account-id").value.trim();
    if (!accountId) return;
    const btn = el("btn-update-assignments");
    setLoading(btn, true, "Saving...");
    const payload = {
      role: el("assign-role").value,
      assignedEventIds: parseCsvList(el("assign-events").value),
    };
    const res = await api("PUT", `/api/v1/admin/accounts/${encodeURIComponent(accountId)}/assignments`, payload);
    setLoading(btn, false);
    if (!res.ok) {
      showBanner("admin-account-status", "err", `<strong>${errCode(res)}</strong>: ${errMsg(res, "Unable to update assignments.")}`);
      return;
    }
    if (state.user && res.data && res.data.accountId === state.user.accountId) {
      const meRes = await api("GET", "/api/v1/auth/me");
      if (meRes.ok && meRes.data && meRes.data.accountId) {
        state.user = meRes.data;
      } else {
        state.user.role = res.data.role;
        state.user.assignedEventIds = res.data.assignedEventIds || [];
      }
      updateSessionUi();
      syncRoleAccessUi();
      populateEventSelectors();
      if (!getAllowedTabs().includes("tab-admin")) {
        const nextTabs = getAllowedTabs();
        if (nextTabs.length) {
          switchTab(nextTabs[0]);
          if (state.user.role === "STUDENT") {
            await refreshMyRegistrations();
          }
        }
        showBanner("global-status", "ok", `Updated your active role to <span class="mono-id">${escapeHtml(state.user.role)}</span>.`);
        showToast(`Role changed to ${state.user.role}`, "ok");
        return;
      }
    }
    await refreshAdminConsole();
    showBanner("admin-account-status", "ok", `Updated assignments for <strong class="mono-id">${escapeHtml(res.data.accountId)}</strong> (${escapeHtml(res.data.role)}).`);
    showToast(`Updated ${res.data.username}`, "ok");
  });

  el("btn-load-report").addEventListener("click", handleLoadReport);
  el("btn-load-participants").addEventListener("click", handleLoadParticipantsOnly);
  el("btn-admin-refresh").addEventListener("click", refreshAdminConsole);
  el("btn-admin-purge").addEventListener("click", async () => {
    const btn = el("btn-admin-purge");
    setLoading(btn, true, "Purging...");
    const res = await api("POST", "/api/v1/admin/audit-logs/purge");
    setLoading(btn, false);
    if (!res.ok) {
      showBanner("global-status", "err", `<strong>${errCode(res)}</strong>: ${errMsg(res, "Audit purge failed.")}`);
      return;
    }
    await refreshAdminConsole();
    showBanner("global-status", "ok", `Purged expired audit logs (retention: ${escapeHtml(Number(res.data.retentionDays) || 30)} days).`);
    showToast("Audit retention purge completed", "ok");
  });

  updateSessionUi();
  syncRoleAccessUi();
})();
