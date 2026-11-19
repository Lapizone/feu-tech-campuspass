(function () {
  "use strict";

  const state = {
    sessionToken: null,
    user: null,
    events: [],
    lastUnverified: null,
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

  function showBanner(targetId, kind, html) {
    el(targetId).innerHTML = `<div class="alert ${kind}">${html}</div>`;
  }

  async function api(method, path, body) {
    const headers = { "Content-Type": "application/json" };
    if (state.sessionToken) {
      headers["Authorization"] = `Bearer ${state.sessionToken}`;
    }
    try {
      const resp = await fetch(path, {
        method,
        headers,
        credentials: "same-origin",
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
      const data = await resp.json().catch(() => ({}));
      return { ok: resp.ok, status: resp.status, data };
    } catch (err) {
      return {
        ok: false,
        status: 503,
        data: {
          code: "SERVICE_UNAVAILABLE",
          message: `Network or timeout error: ${err.message}`,
          correlationId: "client-network-error",
        },
      };
    }
  }

  function switchTab(tabId) {
    document.querySelectorAll(".tab-btn").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.tab === tabId);
    });
    document.querySelectorAll(".panel").forEach((panel) => {
      panel.classList.toggle("active", panel.id === tabId);
    });
  }

  function updateSessionUi() {
    const summary = el("session-summary");
    const logoutBtn = el("btn-logout");
    if (!state.user) {
      summary.textContent = "Not signed in";
      logoutBtn.style.display = "none";
      return;
    }
    const extra = state.user.studentId ? ` (${state.user.studentId})` : "";
    summary.textContent = `${state.user.username} · ${state.user.role}${extra}`;
    logoutBtn.style.display = "inline-block";
  }

  async function refreshEvents() {
    if (!state.user) return;
    const res = await api("GET", "/api/v1/events");
    if (!res.ok) {
      showBanner("student-events-list", "err", `${escapeHtml(res.data.code)}: ${escapeHtml(res.data.message)}`);
      return;
    }
    state.events = res.data.events || [];
    renderEvents();
    populateEventSelectors();
  }

  function renderEvents() {
    const container = el("student-events-list");
    if (!state.events.length) {
      container.innerHTML = `<p class="muted">No events configured.</p>`;
      return;
    }
    const canRegister = state.user && state.user.role === "STUDENT";
    const rows = state.events
      .map(
        (ev) => `
      <tr>
        <td><strong>${escapeHtml(ev.eventId)}</strong><br><span class="muted">${escapeHtml(ev.title)}</span></td>
        <td>${ev.confirmedCount} / ${ev.capacity} (${ev.remainingSlots} left)</td>
        <td>${escapeHtml((ev.eligibleStatuses || []).join(", "))}</td>
        <td>${
          canRegister
            ? `<button class="primary btn-register-ev" data-event-id="${escapeHtml(ev.eventId)}">Register</button>`
            : `<span class="muted">Student role required</span>`
        }</td>
      </tr>`
      )
      .join("");
    container.innerHTML = `<table><thead><tr><th>Event</th><th>Capacity</th><th>Eligibility</th><th>Action</th></tr></thead><tbody>${rows}</tbody></table>`;

    container.querySelectorAll(".btn-register-ev").forEach((btn) => {
      btn.addEventListener("click", () => handleRegister(btn.dataset.eventId));
    });
  }

  function populateEventSelectors() {
    const options = state.events
      .map((ev) => `<option value="${escapeHtml(ev.eventId)}">${escapeHtml(ev.eventId)} - ${escapeHtml(ev.title)}</option>`)
      .join("");
    el("scanner-event-id").innerHTML = options;
    el("org-event-id").innerHTML = options;
  }

  async function handleRegister(eventId) {
    if (!state.user || !state.user.studentId) return;
    const res = await api("POST", `/api/v1/events/${encodeURIComponent(eventId)}/registrations`, {
      studentId: state.user.studentId,
    });
    if (res.ok) {
      showBanner(
        "student-register-status",
        "ok",
        `Registered! ID: <strong>${escapeHtml(res.data.registrationId)}</strong> · Status: ${escapeHtml(res.data.status)}`
      );
      await refreshEvents();
      await refreshMyRegistrations();
      return;
    }
    if (res.status === 503) {
      showBanner(
        "student-register-status",
        "warn",
        `Outcome unverified (503 ${escapeHtml(res.data.code)}). <button id="btn-reconcile-reg" class="secondary">Reconcile Registration Status</button>`
      );
      const btn = el("btn-reconcile-reg");
      if (btn) {
        btn.addEventListener("click", async () => {
          const check = await api(
            "GET",
            `/api/v1/events/${encodeURIComponent(eventId)}/registrations?status=CONFIRMED&studentId=${encodeURIComponent(state.user.studentId)}`
          );
          if (check.ok && (check.data.registrations || []).length > 0) {
            showBanner("student-register-status", "ok", "Reconciliation found existing CONFIRMED registration.");
            await refreshMyRegistrations();
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
      `<strong>${escapeHtml(res.data.code || res.status)}</strong>: ${escapeHtml(res.data.message || "Request rejected")} (corr: <code>${escapeHtml(res.data.correlationId)}</code>)`
    );
  }

  async function refreshMyRegistrations() {
    const container = el("my-registrations-list");
    if (!state.user || state.user.role !== "STUDENT") {
      container.innerHTML = `<p class="muted">Sign in as a student account to view QR passes.</p>`;
      return;
    }
    const res = await api("GET", "/api/v1/my/registrations");
    if (!res.ok) {
      showBanner("my-registrations-list", "err", `${escapeHtml(res.data.code)}: ${escapeHtml(res.data.message)}`);
      return;
    }
    const regs = res.data.registrations || [];
    if (!regs.length) {
      container.innerHTML = `<p class="muted">No confirmed registrations yet.</p>`;
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
          <div>${svg}</div>
          <div>
            <div><strong>${escapeHtml(r.eventId)}</strong> &middot; <span class="badge ok">${escapeHtml(r.status)}</span></div>
            <div class="muted">Registration: <span class="mono">${escapeHtml(r.registrationId)}</span></div>
            <div class="muted">Opaque QR Token:</div>
            <div class="mono">${escapeHtml(r.qrToken)}</div>
            <div class="row">
              <button class="secondary btn-copy-token" data-token="${escapeHtml(r.qrToken)}">Copy Token</button>
            </div>
          </div>
        </div>`;
      })
      .join("");

    container.querySelectorAll(".btn-copy-token").forEach((btn) => {
      btn.addEventListener("click", () => {
        el("scanner-qr-token").value = btn.dataset.token;
        if (navigator.clipboard) navigator.clipboard.writeText(btn.dataset.token).catch(() => {});
      });
    });
  }

  function setScannerReconcileControls(visible, allowRetry) {
    el("btn-scanner-reconcile").style.display = visible ? "inline-block" : "none";
    el("btn-scanner-retry").style.display = allowRetry ? "inline-block" : "none";
  }

  async function handleScannerCheckIn() {
    const eventId = el("scanner-event-id").value;
    const qrToken = el("scanner-qr-token").value.trim();
    if (!eventId || !qrToken) {
      showBanner("scanner-result", "err", "Select an event and enter a QR token.");
      return;
    }
    setScannerReconcileControls(false, false);
    state.lastUnverified = null;

    // Step 1: Pre-validate via Registration to retain registrationId for safe reconciliation
    const valRes = await api("POST", "/api/v1/registrations/validate", { eventId, qrToken });
    if (!valRes.ok) {
      if (valRes.status === 503) {
        showBanner("scanner-result", "warn", `<strong>UNVERIFIED</strong>: Validation service unavailable (${escapeHtml(valRes.data.code)}). No check-in was assumed.`);
      } else {
        showBanner("scanner-result", "err", `<strong>${escapeHtml(valRes.data.code)}</strong>: ${escapeHtml(valRes.data.message)}`);
      }
      return;
    }
    if (!valRes.data.valid || !valRes.data.registrationId) {
      showBanner("scanner-result", "err", `<strong>INVALID_TOKEN</strong>: QR token is invalid or belongs to a different event.`);
      return;
    }

    const regId = valRes.data.registrationId;
    await submitCheckInOnce(eventId, qrToken, regId);
  }

  async function submitCheckInOnce(eventId, qrToken, regId) {
    const chkRes = await api("POST", `/api/v1/events/${encodeURIComponent(eventId)}/check-ins`, { qrToken });
    if (chkRes.status === 201) {
      setScannerReconcileControls(false, false);
      state.lastUnverified = null;
      showBanner(
        "scanner-result",
        "ok",
        `<strong>CHECKED_IN</strong> · Registration <code>${escapeHtml(chkRes.data.registrationId)}</code> at <code>${escapeHtml(chkRes.data.checkInTime)}</code>`
      );
      return;
    }
    if (chkRes.status === 409) {
      setScannerReconcileControls(false, false);
      showBanner(
        "scanner-result",
        "warn",
        `<strong>ALREADY_CHECKED_IN</strong>: ${escapeHtml(chkRes.data.message)} (Registration <code>${escapeHtml(regId)}</code> unchanged)`
      );
      return;
    }
    if (chkRes.status === 404) {
      setScannerReconcileControls(false, false);
      showBanner("scanner-result", "err", `<strong>INVALID_TOKEN</strong>: ${escapeHtml(chkRes.data.message)}`);
      return;
    }
    if (chkRes.status === 503) {
      state.lastUnverified = { eventId, qrToken, registrationId: regId };
      setScannerReconcileControls(true, false);
      showBanner(
        "scanner-result",
        "warn",
        `<strong>UNVERIFIED (HTTP 503)</strong>: Attendance outcome unknown for <code>${escapeHtml(regId)}</code>. Reconcile status before retrying.`
      );
      return;
    }
    showBanner("scanner-result", "err", `<strong>${escapeHtml(chkRes.data.code)}</strong>: ${escapeHtml(chkRes.data.message)}`);
  }

  async function handleScannerReconcile() {
    if (!state.lastUnverified) return;
    const { eventId, registrationId } = state.lastUnverified;
    const res = await api(
      "GET",
      `/api/v1/events/${encodeURIComponent(eventId)}/registrations/${encodeURIComponent(registrationId)}/attendance`
    );
    if (!res.ok) {
      setScannerReconcileControls(true, false);
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
      showBanner(
        "scanner-result",
        "ok",
        `<strong>RECONCILED: CHECKED_IN</strong> · Earlier check-in already committed at <code>${escapeHtml(res.data.checkInTime)}</code>. No duplicate submitted.`
      );
      return;
    }
    setScannerReconcileControls(true, true);
    showBanner(
      "scanner-result",
      "info",
      `<strong>RECONCILED: ABSENT</strong> · Confirmed no attendance record exists for <code>${escapeHtml(registrationId)}</code>. Safe to click <em>Safe Retry Check-in</em>.`
    );
  }

  async function handleLoadReport() {
    const eventId = el("org-event-id").value;
    if (!eventId) return;
    const res = await api("GET", `/api/v1/events/${encodeURIComponent(eventId)}/reports/attendance`);
    if (!res.ok) {
      showBanner(
        "org-report-output",
        "err",
        `<strong>Report Unavailable (${escapeHtml(res.data.code || res.status)})</strong>: ${escapeHtml(res.data.message || "Combined source data unavailable.")}`
      );
      return;
    }
    const r = res.data;
    const rows = (r.participants || [])
      .map(
        (p) => `
      <tr>
        <td class="mono">${escapeHtml(p.registrationId)}</td>
        <td>${escapeHtml(p.studentId)}</td>
        <td><span class="badge info">${escapeHtml(p.registrationStatus)}</span></td>
        <td>${p.checkedIn ? '<span class="badge ok">CHECKED_IN</span>' : '<span class="badge warn">NOT_CHECKED_IN</span>'}</td>
        <td class="mono">${escapeHtml(p.checkInTime || "—")}</td>
      </tr>`
      )
      .join("");
    el("org-report-output").innerHTML = `
      <div class="stat-strip">
        <div class="stat-box"><div class="num">${r.registeredCount}</div><div class="muted">Registered</div></div>
        <div class="stat-box"><div class="num">${r.checkedInCount}</div><div class="muted">Checked In</div></div>
        <div class="stat-box"><div class="num">${r.notCheckedInCount}</div><div class="muted">Not Checked In</div></div>
      </div>
      <table>
        <thead><tr><th>Registration ID</th><th>Student ID</th><th>Reg Status</th><th>Attendance</th><th>Check-in Time</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="5" class="muted">No confirmed registrations yet.</td></tr>'}</tbody>
      </table>`;
  }

  async function handleLoadParticipantsOnly() {
    const eventId = el("org-event-id").value;
    if (!eventId) return;
    const res = await api("GET", `/api/v1/events/${encodeURIComponent(eventId)}/registrations?status=CONFIRMED`);
    if (!res.ok) {
      showBanner("org-report-output", "err", `${escapeHtml(res.data.code)}: ${escapeHtml(res.data.message)}`);
      return;
    }
    const rows = (res.data.registrations || [])
      .map(
        (p) => `<tr><td class="mono">${escapeHtml(p.registrationId)}</td><td>${escapeHtml(p.studentId)}</td><td>${escapeHtml(p.status)}</td></tr>`
      )
      .join("");
    el("org-report-output").innerHTML = `<table><thead><tr><th>Registration ID</th><th>Student ID</th><th>Status</th></tr></thead><tbody>${rows}</tbody></table>`;
  }

  async function refreshAdminConsole() {
    const [healthRes, accRes, notifRes, failRes, auditRes] = await Promise.all([
      api("GET", "/api/v1/admin/service-health"),
      api("GET", "/api/v1/admin/accounts"),
      api("GET", "/api/v1/notifications"),
      api("GET", "/api/v1/admin/failed-notifications"),
      api("GET", "/api/v1/admin/audit-logs"),
    ]);

    if (!healthRes.ok) {
      showBanner("admin-health-output", "err", `${escapeHtml(healthRes.data.code)}: ${escapeHtml(healthRes.data.message)}`);
      return;
    }

    const services = healthRes.data.services || {};
    el("admin-health-output").innerHTML = `<table><thead><tr><th>Service</th><th>Status</th></tr></thead><tbody>${Object.entries(services)
      .map(([k, v]) => `<tr><td>${escapeHtml(k)}</td><td><span class="badge ${v === "ok" ? "ok" : "err"}">${escapeHtml(v)}</span></td></tr>`)
      .join("")}</tbody></table>`;

    if (!accRes.ok) {
      showBanner("admin-accounts-output", "err", `${escapeHtml(accRes.data.code)}: ${escapeHtml(accRes.data.message)}`);
    } else {
      const accounts = (accRes.data && accRes.data.accounts) || [];
      el("admin-accounts-output").innerHTML = `<table><thead><tr><th>Username</th><th>Role</th><th>Assigned Events</th></tr></thead><tbody>${accounts
        .map((a) => `<tr><td>${escapeHtml(a.username)}</td><td>${escapeHtml(a.role)}</td><td class="mono">${escapeHtml((a.assignedEventIds || []).join(", ") || "—")}</td></tr>`)
        .join("")}</tbody></table>`;
    }

    if (!notifRes.ok && !failRes.ok) {
      showBanner("admin-notifications-output", "err", `${escapeHtml(notifRes.data.code)}: ${escapeHtml(notifRes.data.message)}`);
    } else {
      const notifs = (notifRes.ok && notifRes.data && notifRes.data.notifications) || [];
      const failed = (failRes.ok && failRes.data && failRes.data.failedNotifications) || [];
      const failRows = failed
        .map(
          (f) =>
            `<tr><td>${escapeHtml(f.service || "")}</td><td>${escapeHtml(f.type)}</td><td>${escapeHtml(f.eventId)}</td><td class="mono">${escapeHtml(f.registrationId)}</td><td class="mono">${escapeHtml(f.correlationId)}</td><td>${escapeHtml(f.errorMessage)}</td></tr>`
        )
        .join("");
      const failedSection = failed.length
        ? `<h4>Failed Notifications Pending Review</h4><table><thead><tr><th>Service</th><th>Type</th><th>Event</th><th>Reg ID</th><th>Correlation ID</th><th>Error</th></tr></thead><tbody>${failRows}</tbody></table>`
        : "";
      el("admin-notifications-output").innerHTML = `
        <p class="muted">Accepted: ${notifs.length} &middot; Failed-review entries: ${failed.length}</p>
        <table><thead><tr><th>ID</th><th>Type</th><th>Event</th><th>Reg ID</th><th>Status / Delivery</th></tr></thead><tbody>${notifs
          .slice(0, 20)
          .map((n) => `<tr><td class="mono">${escapeHtml(n.notificationId)}</td><td>${escapeHtml(n.type)}</td><td>${escapeHtml(n.eventId)}</td><td class="mono">${escapeHtml(n.registrationId)}</td><td>${escapeHtml(n.status)} / ${escapeHtml(n.deliveryStatus)}</td></tr>`)
          .join("")}</tbody></table>${failedSection}`;
    }

    if (!auditRes.ok) {
      showBanner("admin-audit-output", "err", `${escapeHtml(auditRes.data.code)}: ${escapeHtml(auditRes.data.message)}`);
    } else {
      const logs = (auditRes.data && auditRes.data.logs) || [];
      const unavailable = (auditRes.data && auditRes.data.unavailableServices) || [];
      const warnHtml = unavailable.length
        ? `<div class="alert warn">Audit logs unavailable from: ${escapeHtml(unavailable.join(", "))}</div>`
        : "";
      el("admin-audit-output").innerHTML = `${warnHtml}<table><thead><tr><th>Timestamp</th><th>Service</th><th>Category</th><th>Action</th><th>Outcome</th><th>Correlation ID</th></tr></thead><tbody>${logs
        .slice(0, 40)
        .map((l) => `<tr><td class="mono">${escapeHtml(l.timestamp)}</td><td>${escapeHtml(l.service)}</td><td>${escapeHtml(l.category)}</td><td>${escapeHtml(l.action)}</td><td>${escapeHtml(l.outcome)} ${l.errorCode ? `(${escapeHtml(l.errorCode)})` : ""}</td><td class="mono">${escapeHtml(l.correlationId)}</td></tr>`)
        .join("")}</tbody></table>`;
    }
  }

  // Event wiring
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });

  document.querySelectorAll(".quick-login").forEach((btn) => {
    btn.addEventListener("click", () => {
      el("login-username").value = btn.dataset.user;
      el("login-password").value = btn.dataset.pass;
    });
  });

  el("login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const username = el("login-username").value.trim();
    const password = el("login-password").value;
    const res = await api("POST", "/api/v1/auth/login", { username, password });
    if (!res.ok) {
      showBanner("global-status", "err", `${escapeHtml(res.data.code)}: ${escapeHtml(res.data.message)}`);
      return;
    }
    state.sessionToken = res.data.sessionToken;
    state.user = res.data.user;
    updateSessionUi();
    showBanner("global-status", "ok", `Signed in as <strong>${escapeHtml(state.user.username)}</strong> (${escapeHtml(state.user.role)}).`);
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
    setScannerReconcileControls(false, false);
    el("student-events-list").innerHTML = "";
    el("student-register-status").innerHTML = "";
    el("my-registrations-list").innerHTML = "";
    el("scanner-event-id").innerHTML = "";
    el("org-event-id").innerHTML = "";
    el("org-report-output").innerHTML = "";
    el("admin-health-output").innerHTML = "";
    el("admin-accounts-output").innerHTML = "";
    el("admin-notifications-output").innerHTML = "";
    el("admin-audit-output").innerHTML = "";
    showBanner("scanner-result", "info", "Awaiting QR scan.");
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
    const { eventId, qrToken, registrationId } = state.lastUnverified;
    await submitCheckInOnce(eventId, qrToken, registrationId);
  });

  el("create-event-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = {
      title: el("ev-title").value.trim(),
      capacity: parseInt(el("ev-capacity").value, 10),
      description: el("ev-desc").value.trim(),
    };
    const evId = el("ev-id").value.trim();
    if (evId) payload.eventId = evId;
    const res = await api("POST", "/api/v1/events", payload);
    if (!res.ok) {
      showBanner("org-report-output", "err", `${escapeHtml(res.data.code)}: ${escapeHtml(res.data.message)}`);
      return;
    }
    showBanner("org-report-output", "ok", `Created event <strong>${escapeHtml(res.data.eventId)}</strong>.`);
    await refreshEvents();
  });

  el("btn-load-report").addEventListener("click", handleLoadReport);
  el("btn-load-participants").addEventListener("click", handleLoadParticipantsOnly);
  el("btn-admin-refresh").addEventListener("click", refreshAdminConsole);
  el("btn-admin-purge").addEventListener("click", async () => {
    const res = await api("POST", "/api/v1/admin/audit-logs/purge");
    if (!res.ok) {
      showBanner("global-status", "err", `${escapeHtml(res.data.code)}: ${escapeHtml(res.data.message)}`);
      return;
    }
    showBanner("global-status", "ok", `Purged expired audit logs (retention: ${res.data.retentionDays} days).`);
    await refreshAdminConsole();
  });
})();
