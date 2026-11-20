(function () {
  "use strict";

  const CLIENT_TIMEOUT_MS = 8000;
  const REPORT_TIMEOUT_MS = 10000;

  const state = {
    sessionToken: null,
    user: null,
    events: [],
    lastUnverified: null,
    auditLogs: [],
    auditFilter: "ALL",
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

  function switchTab(tabId) {
    document.querySelectorAll(".tab-btn").forEach((btn) => {
      const active = btn.dataset.tab === tabId;
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-selected", active ? "true" : "false");
    });
    document.querySelectorAll(".panel").forEach((panel) => {
      panel.classList.toggle("active", panel.id === tabId);
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

  async function refreshEvents() {
    if (!state.user) return;
    const res = await api("GET", "/api/v1/events");
    if (!res.ok) {
      showBanner("student-events-list", "err", `<strong>${errCode(res)}</strong>: ${errMsg(res, "Unable to load events.")}`);
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
    const rows = state.events
      .map((ev) => {
        const confirmed = Number(ev.confirmedCount) || 0;
        const capacity = Number(ev.capacity) || 0;
        const remaining = Number(ev.remainingSlots) || 0;
        const pct = capacity > 0 ? Math.min(100, Math.round((confirmed / capacity) * 100)) : 0;
        const isFull = remaining <= 0;
        return `
        <tr>
          <td>
            <strong class="mono-id">${escapeHtml(ev.eventId)}</strong>
            <div>${escapeHtml(ev.title)}</div>
          </td>
          <td class="cap-meter">
            <div><strong>${escapeHtml(confirmed)} / ${escapeHtml(capacity)}</strong> <span class="muted">(${escapeHtml(remaining)} left)</span></div>
            <div class="cap-bar"><div class="cap-fill ${isFull ? "is-full" : ""}" style="width:${pct}%"></div></div>
          </td>
          <td>${(ev.eligibleStatuses || []).map((s) => `<span class="badge info">${escapeHtml(s)}</span>`).join(" ")}</td>
          <td>${
            isStudent
              ? `<button type="button" class="btn ${isFull ? "secondary" : "primary"} sm btn-register-ev" data-event-id="${escapeHtml(ev.eventId)}">${isFull ? "Full · Try" : "Register"}</button>`
              : `<span class="badge warn">Student role required</span>`
          }</td>
        </tr>`;
      })
      .join("");
    container.innerHTML = `<div class="table-wrap"><table><thead><tr><th>Event</th><th>Capacity</th><th>Eligibility</th><th>Action</th></tr></thead><tbody>${rows}</tbody></table></div>`;

    container.querySelectorAll(".btn-register-ev").forEach((btn) => {
      btn.addEventListener("click", () => handleRegister(btn.dataset.eventId, btn));
    });
  }

  function populateEventSelectors() {
    const validIds = new Set(state.events.map((ev) => ev.eventId));
    const selectorIds = ["scanner-event-id", "org-event-id", "edit-ev-select"];
    const prevValues = {};
    selectorIds.forEach((id) => {
      prevValues[id] = el(id).value;
    });

    const options = state.events
      .map((ev) => `<option value="${escapeHtml(ev.eventId)}">${escapeHtml(ev.eventId)} - ${escapeHtml(ev.title)}</option>`)
      .join("");
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

  async function refreshMyRegistrations() {
    const container = el("my-registrations-list");
    if (!state.user || state.user.role !== "STUDENT") {
      container.innerHTML = `<div class="empty-state">Sign in as a student account to view QR passes.</div>`;
      return;
    }
    const res = await api("GET", "/api/v1/my/registrations");
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
      .map((r) => {
        let svg = `<span class="muted">QR preview unavailable</span>`;
        if (window.CampusQR) {
          try {
            svg = window.CampusQR.renderQrSvg(r.qrToken, 150);
          } catch (_e) {
            svg = `<span class="muted">Token exceeds QR preview capacity; copy token manually.</span>`;
          }
        }
        return `
        <div class="qr-card">
          <div class="qr-frame">${svg}</div>
          <div class="qr-meta">
            <div><strong class="mono-id">${escapeHtml(r.eventId)}</strong> &middot; <span class="badge ok">${escapeHtml(r.status)}</span></div>
            <div class="muted" style="margin-top:0.35rem;">Registration: <span class="mono-id">${escapeHtml(r.registrationId)}</span></div>
            <div class="muted" style="margin-top:0.35rem;">Opaque QR Token:</div>
            <div class="mono">${escapeHtml(r.qrToken)}</div>
            <div class="row">
              <button type="button" class="btn secondary sm btn-copy-token" data-token="${escapeHtml(r.qrToken)}">Copy Token</button>
            </div>
          </div>
        </div>`;
      })
      .join("");

    container.querySelectorAll(".btn-copy-token").forEach((btn) => {
      btn.addEventListener("click", () => {
        el("scanner-qr-token").value = btn.dataset.token;
        if (navigator.clipboard) navigator.clipboard.writeText(btn.dataset.token).catch(() => {});
        showToast("Copied QR token to clipboard & scanner field", "ok");
      });
    });
  }

  async function handleScannerCheckIn() {
    const eventId = el("scanner-event-id").value;
    const qrToken = el("scanner-qr-token").value.trim();
    if (!eventId || !qrToken) {
      setScannerStepState({ validate: "is-error" });
      showBanner("scanner-result", "err", "<strong>INVALID_PAYLOAD</strong>: Select an event and enter a QR token.");
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
      } else {
        setScannerStepState({ validate: "is-error" });
        showBanner("scanner-result", "err", `<strong>${errCode(valRes)}</strong>: ${errMsg(valRes, "Token validation failed.")}`);
      }
      return;
    }
    if (!valRes.data.valid || !valRes.data.registrationId) {
      setLoading(btn, false);
      setScannerStepState({ validate: "is-error" });
      showBanner("scanner-result", "err", `<strong>INVALID_TOKEN</strong>: QR token is invalid, unconfirmed, or belongs to a different event.`);
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
      return;
    }
    if (chkRes.status === 404) {
      setScannerReconcileControls(false, false);
      setScannerStepState({ validate: "is-done", checkin: "is-error" });
      showBanner("scanner-result", "err", `<strong>INVALID_TOKEN</strong>: ${errMsg(chkRes, "QR token is invalid.")}`);
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
      return;
    }
    state.lastUnverified = null;
    setScannerReconcileControls(false, false);
    setScannerStepState({ validate: "is-done", checkin: "is-error" });
    showBanner("scanner-result", "err", `<strong>${errCode(chkRes)}</strong>: ${errMsg(chkRes, "Check-in failed.")}`);
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
        `<strong>STILL UNVERIFIED</strong>: Unable to retrieve authoritative attendance status (${escapeHtml(res.data.code || res.status)}).`
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
    const filtered = state.auditFilter === "ALL" ? logs : logs.filter((l) => l.category === state.auditFilter);
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

  // Event wiring
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
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

  document.querySelectorAll(".quick-login").forEach((btn) => {
    btn.addEventListener("click", () => {
      el("login-username").value = btn.dataset.user;
      el("login-password").value = btn.dataset.pass;
    });
  });

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
    state.sessionToken = res.data.sessionToken;
    state.user = res.data.user;
    updateSessionUi();
    showBanner("global-status", "ok", `Signed in as <strong>${escapeHtml(state.user.username)}</strong> (<span class="mono-id">${escapeHtml(state.user.role)}</span>).`);
    await refreshEvents();
    if (state.user.role === "STUDENT") {
      await refreshMyRegistrations();
      switchTab("tab-student");
    } else if (state.user.role === "CHECK_IN_STAFF") {
      switchTab("tab-scanner");
    } else if (state.user.role === "EVENT_ORGANIZER") {
      switchTab("tab-organizer");
    } else {
      await refreshAdminConsole();
      switchTab("tab-admin");
    }
  });

  el("btn-logout").addEventListener("click", async () => {
    await api("POST", "/api/v1/auth/logout");
    state.sessionToken = null;
    state.user = null;
    state.events = [];
    state.lastUnverified = null;
    state.auditLogs = [];
    state.auditFilter = "ALL";
    document.querySelectorAll("#admin-audit-filters .filter-pill").forEach((p) => {
      const isAll = p.dataset.category === "ALL";
      p.classList.toggle("active", isAll);
      p.setAttribute("aria-pressed", isAll ? "true" : "false");
    });
    el("login-password").value = "";
    el("create-event-form").reset();
    el("ev-capacity").value = "25";
    el("ev-eligible-statuses").value = "ENROLLED";
    el("edit-event-form").reset();
    el("create-account-form").reset();
    el("update-assignments-form").reset();
    syncStudentIdVisibility();
    setScannerReconcileControls(false, false);
    setScannerStepState({});
    el("student-events-list").innerHTML = `<div class="empty-state">Sign in to inspect campus events and remaining slots.</div>`;
    el("student-register-status").innerHTML = "";
    el("my-registrations-list").innerHTML = `<div class="empty-state">Confirmed event passes will render here with a scannable QR code.</div>`;
    el("scanner-event-id").innerHTML = "";
    el("scanner-qr-token").value = "";
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
    showBanner("scanner-result", "info", "<strong>Awaiting QR Scan</strong><div>Select an assigned event and scan or paste a confirmed participant QR token.</div>");
    updateSessionUi();
    showBanner("global-status", "info", "Signed out cleanly.");
    switchTab("tab-auth");
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
})();
