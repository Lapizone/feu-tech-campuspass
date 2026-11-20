from __future__ import annotations

import time

import pytest
from playwright.sync_api import expect, sync_playwright

from shared.cluster import ServiceCluster


def _assert_no_horizontal_overflow(page, label: str) -> None:
    metrics = page.evaluate(
        """() => {
            const cards = Array.from(document.querySelectorAll('.panel.active .card'));
            const maxCardRight = cards.reduce((acc, c) => Math.max(acc, Math.ceil(c.getBoundingClientRect().right)), 0);
            return {
                scrollWidth: document.documentElement.scrollWidth,
                innerWidth: window.innerWidth,
                maxCardRight,
            };
        }"""
    )
    assert metrics["scrollWidth"] <= metrics["innerWidth"], (
        f"Horizontal overflow at {label}: {metrics}"
    )
    assert metrics["maxCardRight"] <= metrics["innerWidth"], (
        f"Card right-edge clipping at {label}: {metrics}"
    )


def _assert_mobile_no_overflow(page, label: str) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    try:
        _assert_no_horizontal_overflow(page, label)
    finally:
        page.set_viewport_size({"width": 1280, "height": 900})


def _login_ui(page, username: str, password: str | None = None) -> None:
    if password is None:
        page.locator(f'.quick-login[data-user="{username}"]').click()
    else:
        page.locator("#login-username").fill(username)
        page.locator("#login-password").fill(password)
    page.locator("#btn-login-submit").click()


def _logout_ui(page) -> None:
    page.locator("#btn-logout").click()
    page.locator("#tab-auth.active").wait_for(state="visible", timeout=8000)


@pytest.mark.e2e
@pytest.mark.timeout(240)
def test_full_ui_clickthrough_smoke_all_roles_outages_and_responsive(tmp_path):
    cluster = ServiceCluster.build(
        work_dir=tmp_path,
        use_ephemeral_ports=True,
        extra_env={"RETRY_DELAYS_SEC": "0.05,0.05"},
    )
    console_errors: list[str] = []
    page_errors: list[str] = []

    try:
        cluster.start_all()
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                context = browser.new_context(
                    viewport={"width": 1280, "height": 900},
                    ignore_https_errors=True,
                    accept_downloads=True,
                )
                page = context.new_page()
                page.on(
                    "console",
                    lambda msg: console_errors.append(msg.text)
                    if msg.type == "error"
                    else None,
                )
                page.on("pageerror", lambda err: page_errors.append(str(err)))

                # 1. Landing & invalid login check + mobile responsive check
                page.goto(f"{cluster.gateway_url}/", wait_until="networkidle")
                page.locator("#tab-auth.active").wait_for(state="visible", timeout=8000)
                _assert_no_horizontal_overflow(page, "login desktop")
                _assert_mobile_no_overflow(page, "login mobile")

                _login_ui(page, "student_s001", "WrongPass!")
                page.locator("#global-status .alert.err").wait_for(state="visible", timeout=8000)
                assert "UNAUTHORIZED" in page.locator("#global-status").inner_text()

                # 2. Ineligible student S-004 (ON_LEAVE) -> 422 INELIGIBLE
                _login_ui(page, "student_s004")
                page.locator("#tab-student.active").wait_for(state="visible", timeout=8000)
                page.locator('.btn-register-ev[data-event-id="EVT-001"]').click()
                page.locator("#student-register-status .alert.err").wait_for(
                    state="visible", timeout=8000
                )
                assert "INELIGIBLE" in page.locator("#student-register-status").inner_text()
                _logout_ui(page)
                assert page.locator("#my-registrations-list .qr-card").count() == 0

                # 3. Student S-001 registers -> CONFIRMED + SVG QR Pass + Present modal + PNG download + duplicate check
                t_login0 = time.monotonic()
                _login_ui(page, "student_s001")
                page.locator("#tab-student.active").wait_for(state="visible", timeout=8000)
                login_elapsed_ms = (time.monotonic() - t_login0) * 1000.0
                assert login_elapsed_ms < 4000.0, f"Student login too slow: {login_elapsed_ms:.1f}ms"
                page.locator('.btn-register-ev[data-event-id="EVT-001"]').click()
                page.locator("#student-register-status .alert.ok").wait_for(
                    state="visible", timeout=8000
                )
                page.locator("#my-registrations-list .qr-card svg").first.wait_for(
                    state="visible", timeout=8000
                )
                qr_s001 = page.locator(
                    "#my-registrations-list .btn-copy-token"
                ).first.get_attribute("data-token")
                assert qr_s001 and len(qr_s001) >= 32
                page.locator("#my-registrations-list .btn-copy-token").first.click()

                # Verify Present Pass modal opens with SVG and closes cleanly
                page.locator("#my-registrations-list .btn-fullscreen-qr").first.click()
                page.locator("#qr-pass-modal.active").wait_for(state="visible", timeout=5000)
                page.locator("#qr-modal-svg svg").wait_for(state="visible", timeout=5000)
                page.locator("#btn-close-qr-modal").click()
                page.locator("#qr-pass-modal").wait_for(state="hidden", timeout=5000)

                # Download crisp QR PNG to upload into Scanner kiosk later
                pass_png_path = tmp_path / "pass_s001.png"
                with page.expect_download() as dl_info:
                    page.locator("#my-registrations-list .btn-download-qr").first.click()
                dl_info.value.save_as(str(pass_png_path))
                assert pass_png_path.stat().st_size > 200

                # Duplicate attempt via UI Register button
                page.locator('.btn-register-ev[data-event-id="EVT-001"]').click()
                page.locator("#student-register-status .alert.err").wait_for(
                    state="visible", timeout=8000
                )
                assert (
                    "ALREADY_REGISTERED"
                    in page.locator("#student-register-status").inner_text()
                )
                _assert_no_horizontal_overflow(page, "student portal desktop")
                _assert_mobile_no_overflow(page, "student portal mobile")
                _logout_ui(page)

                # 4. Student S-002 takes 2nd slot -> full; Student S-003 hits 409 EVENT_FULL
                _login_ui(page, "student_s002")
                page.locator("#tab-student.active").wait_for(state="visible", timeout=8000)
                page.locator('.btn-register-ev[data-event-id="EVT-001"]').click()
                page.locator("#my-registrations-list .qr-card svg").first.wait_for(
                    state="visible", timeout=8000
                )
                qr_s002 = page.locator(
                    "#my-registrations-list .btn-copy-token"
                ).first.get_attribute("data-token")
                assert qr_s002
                _logout_ui(page)

                _login_ui(page, "student_s003")
                page.locator("#tab-student.active").wait_for(state="visible", timeout=8000)
                page.locator('.btn-register-ev[data-event-id="EVT-001"]').click()
                page.locator("#student-register-status .alert.err").wait_for(
                    state="visible", timeout=8000
                )
                assert "EVENT_FULL" in page.locator("#student-register-status").inner_text()
                _logout_ui(page)

                # 5. Scanner1: Upload downloaded QR PNG -> auto-decode + check-in, repeat scan 409, wrong-event 404, and live FP 2 outage recovery
                _login_ui(page, "scanner1")
                page.locator("#tab-scanner.active").wait_for(state="visible", timeout=8000)
                page.locator("#scanner-event-id").select_option("EVT-001")
                page.locator('.scanner-mode-btn[data-scan-mode="upload"]').click()
                page.locator("#scanner-pane-upload.active").wait_for(state="visible", timeout=5000)
                page.locator("#scanner-qr-file").set_input_files(str(pass_png_path))
                page.locator("#scanner-result .alert.ok").wait_for(state="visible", timeout=8000)
                assert page.locator("#scanner-qr-token").input_value() == qr_s001
                assert "CHECKED_IN" in page.locator("#scanner-result").inner_text()
                assert page.locator("#scanner-history tbody tr").count() >= 1

                # Repeat scan
                page.locator("#btn-scanner-checkin").click()
                page.locator("#scanner-result .alert.warn").wait_for(
                    state="visible", timeout=8000
                )
                assert "ALREADY_CHECKED_IN" in page.locator("#scanner-result").inner_text()

                # Wrong event
                page.locator("#scanner-event-id").select_option("EVT-002")
                page.locator("#btn-scanner-checkin").click()
                page.locator("#scanner-result .alert.err").wait_for(
                    state="visible", timeout=8000
                )
                assert "INVALID_TOKEN" in page.locator("#scanner-result").inner_text()

                # Stop Attendance Service -> UNVERIFIED -> Reconcile while down -> Restart -> Reconcile -> Safe Retry
                page.locator("#scanner-event-id").select_option("EVT-001")
                page.locator("#scanner-qr-token").fill(qr_s002)
                cluster.stop_service("attendance-service")
                page.locator("#btn-scanner-checkin").click()
                page.locator("#scanner-result .alert.warn").wait_for(
                    state="visible", timeout=8000
                )
                assert "UNVERIFIED (HTTP 503)" in page.locator("#scanner-result").inner_text()
                page.locator("#btn-scanner-reconcile").wait_for(
                    state="visible", timeout=8000
                )

                # Reconcile while still down stays unverified
                page.locator("#btn-scanner-reconcile").click()
                expect(page.locator("#scanner-result")).to_contain_text(
                    "STILL UNVERIFIED", timeout=8000
                )

                # Restore Attendance -> Reconcile confirms absent -> Safe Retry records check-in
                cluster.start_service("attendance-service", wait_ready=True)
                page.locator("#btn-scanner-reconcile").click()
                expect(page.locator("#scanner-result")).to_contain_text(
                    "RECONCILED: ABSENT", timeout=8000
                )
                page.locator("#btn-scanner-retry").wait_for(state="visible", timeout=8000)
                page.locator("#btn-scanner-retry").click()
                expect(page.locator("#scanner-result")).to_contain_text(
                    "CHECKED_IN", timeout=8000
                )
                _assert_mobile_no_overflow(page, "scanner mobile")
                _logout_ui(page)

                # 6. Organizer1: combined report, edit EVT-001 capacity, create EVT-003
                _login_ui(page, "organizer1")
                page.locator("#tab-organizer.active").wait_for(state="visible", timeout=8000)
                page.locator("#org-event-id").select_option("EVT-001")
                page.locator("#btn-load-report").click()
                page.locator("#org-report-output .stat-strip").wait_for(
                    state="visible", timeout=8000
                )
                stat_nums = [
                    el.inner_text()
                    for el in page.locator("#org-report-output .stat-box .num").all()
                ]
                assert stat_nums == ["2", "2", "0"]

                # Edit EVT-001 capacity to 4
                page.locator('.seg-btn[data-org-mode="edit"]').click()
                page.locator("#edit-ev-select").select_option("EVT-001")
                page.locator("#edit-ev-capacity").fill("4")
                page.locator("#btn-edit-event").click()
                expect(page.locator("#org-event-status")).to_contain_text(
                    "capacity 4", timeout=8000
                )

                # Create EVT-003
                page.locator('.seg-btn[data-org-mode="create"]').click()
                page.locator("#ev-id").fill("EVT-003")
                page.locator("#ev-title").fill("HCI Design Colloquium")
                page.locator("#ev-capacity").fill("15")
                page.locator("#ev-desc").fill("Smoke test created event")
                page.locator("#btn-create-event").click()
                expect(page.locator("#org-event-status")).to_contain_text(
                    "EVT-003", timeout=8000
                )
                page.locator("#org-event-id").select_option("EVT-003")
                page.locator("#btn-load-report").click()
                expect(page.locator("#org-report-output")).to_contain_text(
                    "No confirmed registrations yet.", timeout=8000
                )
                _assert_mobile_no_overflow(page, "organizer mobile")
                _logout_ui(page)

                # 7. Admin1: health, create scanner3, update assignments via Edit button, filter logs, purge
                _login_ui(page, "admin1")
                page.locator("#tab-admin.active").wait_for(state="visible", timeout=8000)
                page.locator("#admin-health-output table").wait_for(
                    state="visible", timeout=8000
                )
                assert page.locator("#admin-health-output .badge.ok").count() == 5

                # Create scanner3 assigned to EVT-003
                page.locator("#acc-username").fill("scanner3")
                page.locator("#acc-password").fill("Password123!")
                page.locator("#acc-role").select_option("CHECK_IN_STAFF")
                page.locator("#acc-events").fill("EVT-003")
                page.locator("#btn-create-account").click()
                expect(page.locator("#admin-account-status")).to_contain_text(
                    "scanner3", timeout=8000
                )

                # Click Edit on scanner3 row and update assignments to EVT-001, EVT-003
                scanner3_row = page.locator("#admin-accounts-output tbody tr", has_text="scanner3")
                scanner3_row.locator(".btn-edit-account").click()
                page.locator("#update-assignments-form.active").wait_for(
                    state="visible", timeout=5000
                )
                page.locator("#assign-events").fill("EVT-001, EVT-003")
                page.locator("#btn-update-assignments").click()
                expect(page.locator("#admin-account-status")).to_contain_text(
                    "Updated assignments", timeout=8000
                )
                expect(
                    page.locator("#admin-accounts-output tbody tr", has_text="scanner3")
                ).to_contain_text("EVT-001, EVT-003", timeout=8000)

                # Filter audit logs & run 30-day purge
                page.locator('#admin-audit-filters .filter-pill[data-category="Attendance activity"]').click()
                page.locator("#admin-audit-output table").wait_for(
                    state="visible", timeout=5000
                )
                page.locator("#btn-admin-purge").click()
                expect(page.locator("#global-status")).to_contain_text(
                    "Purged expired audit logs", timeout=8000
                )

                # Verify zero leaked tokens/passwords in Admin audit DOM
                audit_text = page.locator("#admin-audit-output").inner_text()
                assert qr_s001 not in audit_text
                assert qr_s002 not in audit_text
                assert "Password123!" not in audit_text
                _assert_no_horizontal_overflow(page, "admin console desktop")
                _assert_mobile_no_overflow(page, "admin console mobile")
                _logout_ui(page)

                # 8. Log in as newly created scanner3 and verify assigned EVT-001 check-in works end-to-end
                _login_ui(page, "scanner3", "Password123!")
                page.locator("#tab-scanner.active").wait_for(state="visible", timeout=8000)
                assert (
                    page.locator('#scanner-event-id option[value="EVT-003"]').count() == 1
                )
                page.locator("#scanner-event-id").select_option("EVT-001")
                page.locator("#scanner-qr-token").fill(qr_s001)
                page.locator("#btn-scanner-checkin").click()
                page.locator("#scanner-result .alert.warn").wait_for(
                    state="visible", timeout=8000
                )
                assert "ALREADY_CHECKED_IN" in page.locator("#scanner-result").inner_text()
            finally:
                browser.close()
    finally:
        cluster.stop_all()

    unexpected_console_errors = [
        msg
        for msg in console_errors
        if not msg.startswith("Failed to load resource: the server responded with a status of ")
    ]
    assert unexpected_console_errors == [], (
        f"Unexpected browser console errors detected: {unexpected_console_errors}"
    )
    assert page_errors == [], f"Uncaught page exceptions detected: {page_errors}"
