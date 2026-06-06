"""
End-to-end Playwright tests for Real Estate FlipFinder dashboard.

Tests cover:
  - Login page: design, form validation, auth flow
  - Main dashboard: layout, sidebar, quota chip, navigation
  - Archive/history: sidebar history list
  - Favorites modal: open/close, tabs, empty state
  - Admin panel: stats, users table, add-user form
  - Responsive / mobile viewport: hamburger menu, FAB
  - Logout flow

Run:
    source venv/bin/activate
    DASHBOARD_PORT=8010 python -m pytest tests/test_e2e_playwright.py -v
"""

import pytest
from playwright.sync_api import Page, expect

BASE_URL = "http://127.0.0.1:8010"
ADMIN_EMAIL = "arashyazd@gmail.com"
ADMIN_PASSWORD = "letmein"


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def browser_type_launch_args():
    return {"headless": True}


def login(page: Page) -> None:
    """Helper: log in as admin and wait for main app to load."""
    page.goto(f"{BASE_URL}/login")
    page.fill("#email", ADMIN_EMAIL)
    page.fill("#password", ADMIN_PASSWORD)
    page.click("button[type=submit]")
    page.wait_for_url(f"{BASE_URL}/")


# ─── Login page ───────────────────────────────────────────────────────────────

class TestLoginPage:
    def test_redirects_unauthenticated_to_login(self, page: Page):
        """Unauthenticated visit to / should land on /login."""
        page.goto(BASE_URL)
        page.wait_for_url(f"**/login")
        expect(page).to_have_url(f"{BASE_URL}/login")

    def test_login_page_title(self, page: Page):
        page.goto(f"{BASE_URL}/login")
        expect(page).to_have_title("Sign in — Real Estate Analyzer")

    def test_login_page_has_form_elements(self, page: Page):
        page.goto(f"{BASE_URL}/login")
        expect(page.locator("#email")).to_be_visible()
        expect(page.locator("#password")).to_be_visible()
        expect(page.locator("button[type=submit]")).to_be_visible()
        expect(page.locator("button[type=submit]")).to_have_text("Sign in")

    def test_login_dark_background(self, page: Page):
        """Page should have the dark #000000 background."""
        page.goto(f"{BASE_URL}/login")
        bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
        assert bg == "rgb(0, 0, 0)", f"Expected black bg, got: {bg}"

    def test_login_card_visible(self, page: Page):
        page.goto(f"{BASE_URL}/login")
        card = page.locator(".login-card")
        expect(card).to_be_visible()
        expect(card.locator("h1")).to_have_text("Real Estate Analyzer")
        expect(card.locator("p.muted")).to_have_text("Sign in to continue")

    def test_invalid_credentials_show_error(self, page: Page):
        page.goto(f"{BASE_URL}/login")
        page.fill("#email", "wrong@example.com")
        page.fill("#password", "wrongpassword")
        page.click("button[type=submit]")
        error_el = page.locator("#error")
        expect(error_el).to_be_visible()
        expect(error_el).not_to_have_text("")

    def test_error_hidden_initially(self, page: Page):
        page.goto(f"{BASE_URL}/login")
        expect(page.locator("#error")).to_be_hidden()

    def test_valid_login_redirects_to_main(self, page: Page):
        login(page)
        expect(page).to_have_url(f"{BASE_URL}/")
        expect(page.locator(".topbar")).to_be_visible()

    def test_login_submit_button_amber_color(self, page: Page):
        page.goto(f"{BASE_URL}/login")
        btn = page.locator("button[type=submit]")
        bg = btn.evaluate("el => getComputedStyle(el).backgroundColor")
        # amber is rgb(232, 160, 32)
        assert "232" in bg, f"Submit button should be amber, got: {bg}"


# ─── Main dashboard ───────────────────────────────────────────────────────────

class TestMainDashboard:
    def test_page_title(self, page: Page):
        login(page)
        expect(page).to_have_title("Real Estate Analyzer")

    def test_topbar_present(self, page: Page):
        login(page)
        topbar = page.locator(".topbar")
        expect(topbar).to_be_visible()
        expect(topbar.locator(".brand")).to_have_text("Real Estate Analyzer")

    def test_quota_chip_shown_for_admin(self, page: Page):
        login(page)
        page.wait_for_selector("#quota-chip:not([hidden])", timeout=5000)
        chip = page.locator("#quota-chip")
        expect(chip).to_be_visible()
        # Admin sees ∞ symbol
        expect(chip).to_contain_text("∞")

    def test_admin_link_visible_for_admin(self, page: Page):
        login(page)
        page.wait_for_selector("#admin-link:not([hidden])", timeout=5000)
        expect(page.locator("#admin-link")).to_be_visible()
        expect(page.locator("#admin-link")).to_have_text("Admin")

    def test_api_status_dot_present(self, page: Page):
        login(page)
        dot = page.locator("#api-status")
        expect(dot).to_be_visible()

    def test_sign_out_button_present(self, page: Page):
        login(page)
        expect(page.locator("#logout-btn")).to_be_visible()
        expect(page.locator("#logout-btn")).to_have_text("Sign out")

    def test_sidebar_present(self, page: Page):
        login(page)
        expect(page.locator(".sidebar")).to_be_visible()

    def test_sidebar_new_search_heading(self, page: Page):
        login(page)
        expect(page.locator(".sidebar h2").first).to_have_text("New Search")

    def test_search_form_fields_present(self, page: Page):
        login(page)
        expect(page.locator("#city")).to_be_visible()
        expect(page.locator("#count")).to_be_visible()
        expect(page.locator("#intent")).to_be_visible()
        expect(page.locator("#search-btn")).to_be_visible()

    def test_city_input_placeholder(self, page: Page):
        login(page)
        expect(page.locator("#city")).to_have_attribute("placeholder", "Sausalito, CA")

    def test_count_default_value(self, page: Page):
        login(page)
        expect(page.locator("#count")).to_have_value("10")

    def test_intent_select_options(self, page: Page):
        login(page)
        options = page.locator("#intent option").all_text_contents()
        assert "Flip" in options
        assert "Rent" in options
        assert "Both (Flip + Rent)" in options

    def test_intent_default_is_both(self, page: Page):
        login(page)
        expect(page.locator("#intent")).to_have_value("both")

    def test_search_button_text(self, page: Page):
        login(page)
        expect(page.locator("#search-btn")).to_have_text("Run Analysis")

    def test_search_button_amber_color(self, page: Page):
        login(page)
        btn = page.locator("#search-btn")
        bg = btn.evaluate("el => getComputedStyle(el).backgroundColor")
        assert "232" in bg, f"Run Analysis button should be amber, got: {bg}"

    def test_archive_heading_present(self, page: Page):
        login(page)
        archive_heading = page.locator(".sidebar h3", has_text="Archive")
        expect(archive_heading).to_be_visible()

    def test_results_area_present(self, page: Page):
        login(page)
        expect(page.locator(".results")).to_be_visible()
        # results-grid is empty until a search runs; confirm it's in the DOM
        assert page.locator("#results-grid").count() == 1, "#results-grid should be in the DOM"

    def test_nav_archive_button(self, page: Page):
        login(page)
        expect(page.locator("#nav-archive")).to_be_visible()
        expect(page.locator("#nav-archive")).to_contain_text("Archive")

    def test_nav_favorites_button(self, page: Page):
        login(page)
        expect(page.locator("#nav-favorites")).to_be_visible()
        expect(page.locator("#nav-favorites")).to_contain_text("Favorites")

    def test_dark_theme_bg(self, page: Page):
        login(page)
        bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
        assert bg == "rgb(0, 0, 0)", f"Expected black background, got: {bg}"


# ─── Favorites modal ──────────────────────────────────────────────────────────

class TestFavoritesModal:
    def test_modal_hidden_by_default(self, page: Page):
        login(page)
        expect(page.locator("#modal-favorites")).to_be_hidden()

    def test_favorites_modal_opens_from_nav_button(self, page: Page):
        login(page)
        page.click("#nav-favorites")
        expect(page.locator("#modal-favorites")).to_be_visible()

    def test_favorites_modal_title(self, page: Page):
        login(page)
        page.click("#nav-favorites")
        expect(page.locator("#modal-favorites .modal-card header h3")).to_have_text("Favorites")

    def test_favorites_modal_close_button(self, page: Page):
        login(page)
        page.click("#nav-favorites")
        expect(page.locator("#modal-favorites")).to_be_visible()
        page.click("#modal-favorites button[data-close]")
        expect(page.locator("#modal-favorites")).to_be_hidden()

    def test_favorites_modal_has_two_tabs(self, page: Page):
        login(page)
        page.click("#nav-favorites")
        tabs = page.locator("#modal-favorites .tab").all_text_contents()
        assert any("Saved Listings" in t for t in tabs)
        assert any("Watched Cities" in t for t in tabs)

    def test_saved_listings_tab_active_by_default(self, page: Page):
        login(page)
        page.click("#nav-favorites")
        active_tab = page.locator("#modal-favorites .tab.active")
        expect(active_tab).to_have_text("Saved Listings")

    def test_saved_listings_pane_visible(self, page: Page):
        login(page)
        page.click("#nav-favorites")
        expect(page.locator("#fav-props")).to_be_visible()
        expect(page.locator("#fav-cities")).to_be_hidden()

    def test_watched_cities_tab_switch(self, page: Page):
        login(page)
        page.click("#nav-favorites")
        page.click(".tab[data-tab='cities']")
        expect(page.locator("#fav-cities")).to_be_visible()
        expect(page.locator("#fav-props")).to_be_hidden()


# ─── Archive panel ────────────────────────────────────────────────────────────

class TestArchivePanel:
    def test_history_list_present(self, page: Page):
        login(page)
        expect(page.locator("#history-list")).to_be_visible()

    def test_active_runs_list_present(self, page: Page):
        login(page)
        # active-runs is empty (zero height) when no runs are in flight;
        # just confirm it's in the DOM
        assert page.locator("#active-runs").count() == 1, "#active-runs should be in the DOM"


# ─── Search form interaction ──────────────────────────────────────────────────

class TestSearchFormInteraction:
    def test_city_input_accepts_text(self, page: Page):
        login(page)
        page.fill("#city", "Beverly Hills, CA")
        expect(page.locator("#city")).to_have_value("Beverly Hills, CA")

    def test_count_input_accepts_number(self, page: Page):
        login(page)
        page.fill("#count", "5")
        expect(page.locator("#count")).to_have_value("5")

    def test_intent_can_be_changed(self, page: Page):
        login(page)
        page.select_option("#intent", "flip")
        expect(page.locator("#intent")).to_have_value("flip")
        page.select_option("#intent", "rent")
        expect(page.locator("#intent")).to_have_value("rent")

    def test_empty_city_shows_validation(self, page: Page):
        """HTML5 required validation fires when city is empty."""
        login(page)
        page.fill("#city", "")
        page.click("#search-btn")
        # The city field should now be focused (browser native required validation)
        validity = page.evaluate("document.getElementById('city').validity.valueMissing")
        assert validity is True


# ─── Admin page ───────────────────────────────────────────────────────────────

class TestAdminPage:
    def test_admin_page_title(self, page: Page):
        login(page)
        page.goto(f"{BASE_URL}/admin")
        expect(page).to_have_title("Admin — Real Estate Analyzer")

    def test_admin_topbar_brand(self, page: Page):
        login(page)
        page.goto(f"{BASE_URL}/admin")
        expect(page.locator(".topbar .brand")).to_have_text("Admin Console")

    def test_admin_back_link(self, page: Page):
        login(page)
        page.goto(f"{BASE_URL}/admin")
        back = page.locator("a[href='/']")
        expect(back).to_be_visible()
        expect(back).to_contain_text("Back to app")

    def test_admin_overview_section(self, page: Page):
        login(page)
        page.goto(f"{BASE_URL}/admin")
        page.wait_for_selector("#stat-grid .stat-card", timeout=8000)
        stat_cards = page.locator("#stat-grid .stat-card")
        assert stat_cards.count() == 5, f"Expected 5 stat cards, got {stat_cards.count()}"

    def test_admin_stat_cards_have_values(self, page: Page):
        login(page)
        page.goto(f"{BASE_URL}/admin")
        page.wait_for_selector("#stat-grid .stat-card .value:not(:empty)", timeout=8000)
        cards = page.locator("#stat-grid .stat-card .value").all()
        for card in cards:
            text = card.inner_text()
            assert text and text.strip() != "", "Stat card value should not be empty"

    def test_admin_users_table_loaded(self, page: Page):
        login(page)
        page.goto(f"{BASE_URL}/admin")
        page.wait_for_selector("#users-table tbody tr[data-email]", timeout=8000)
        rows = page.locator("#users-table tbody tr[data-email]")
        assert rows.count() >= 1, "Users table should have at least 1 row"

    def test_admin_user_row_has_admin_pill(self, page: Page):
        login(page)
        page.goto(f"{BASE_URL}/admin")
        page.wait_for_selector("#users-table tbody tr[data-email]", timeout=8000)
        admin_row = page.locator(f"tr[data-email='{ADMIN_EMAIL}']")
        expect(admin_row).to_be_visible()
        expect(admin_row.locator(".pill-admin")).to_have_text("admin")

    def test_admin_user_row_has_active_pill(self, page: Page):
        login(page)
        page.goto(f"{BASE_URL}/admin")
        page.wait_for_selector("#users-table tbody tr[data-email]", timeout=8000)
        admin_row = page.locator(f"tr[data-email='{ADMIN_EMAIL}']")
        expect(admin_row.locator(".pill-active")).to_have_text("active")

    def test_add_user_toggle_shows_panel(self, page: Page):
        login(page)
        page.goto(f"{BASE_URL}/admin")
        page.wait_for_selector("#add-user-toggle", timeout=5000)
        expect(page.locator("#add-user-panel")).to_be_hidden()
        page.click("#add-user-toggle")
        expect(page.locator("#add-user-panel")).to_be_visible()

    def test_add_user_panel_close(self, page: Page):
        login(page)
        page.goto(f"{BASE_URL}/admin")
        page.wait_for_selector("#add-user-toggle")
        page.click("#add-user-toggle")
        expect(page.locator("#add-user-panel")).to_be_visible()
        page.click("#add-user-toggle")
        expect(page.locator("#add-user-panel")).to_be_hidden()

    def test_add_user_form_fields(self, page: Page):
        login(page)
        page.goto(f"{BASE_URL}/admin")
        page.wait_for_selector("#add-user-toggle")
        page.click("#add-user-toggle")
        panel = page.locator("#add-user-panel")
        expect(panel.locator("input[name=email]")).to_be_visible()
        expect(panel.locator("input[name=password]")).to_be_visible()
        expect(panel.locator("input[name=daily_cap]")).to_be_visible()

    def test_runs_table_present(self, page: Page):
        login(page)
        page.goto(f"{BASE_URL}/admin")
        page.wait_for_selector("#runs-table", timeout=5000)
        expect(page.locator("#runs-table")).to_be_visible()

    def test_admin_logout_button(self, page: Page):
        login(page)
        page.goto(f"{BASE_URL}/admin")
        expect(page.locator("#logout-btn")).to_be_visible()
        expect(page.locator("#logout-btn")).to_have_text("Sign out")


# ─── Logout flow ──────────────────────────────────────────────────────────────

class TestLogout:
    def test_logout_redirects_to_login(self, page: Page):
        login(page)
        page.click("#logout-btn")
        page.wait_for_url(f"**/login", timeout=5000)
        expect(page).to_have_url(f"{BASE_URL}/login")

    def test_logged_out_cannot_access_main(self, page: Page):
        login(page)
        page.click("#logout-btn")
        page.wait_for_url(f"**/login")
        page.goto(BASE_URL)
        page.wait_for_url(f"**/login")
        expect(page).to_have_url(f"{BASE_URL}/login")


# ─── Responsive / mobile ──────────────────────────────────────────────────────

class TestMobileViewport:
    def test_hamburger_menu_visible_on_mobile(self, page: Page):
        page.set_viewport_size({"width": 390, "height": 844})
        login(page)
        expect(page.locator("#menu-toggle")).to_be_visible()

    def test_sidebar_open_by_default_on_mobile(self, page: Page):
        page.set_viewport_size({"width": 390, "height": 844})
        login(page)
        # app.js line 752: sidebar starts OPEN on mobile (≤768px)
        page.wait_for_selector(".sidebar.open", timeout=3000)
        sidebar = page.locator(".sidebar")
        has_open = sidebar.evaluate("el => el.classList.contains('open')")
        assert has_open, "Sidebar should start open on mobile"

    def test_hamburger_closes_then_reopens_sidebar(self, page: Page):
        page.set_viewport_size({"width": 390, "height": 844})
        login(page)
        # sidebar starts open on mobile — first click closes it
        page.wait_for_selector(".sidebar.open", timeout=3000)
        page.click("#menu-toggle")
        page.wait_for_function("!document.querySelector('.sidebar').classList.contains('open')", timeout=2000)
        is_closed = page.locator(".sidebar").evaluate("el => !el.classList.contains('open')")
        assert is_closed, "Sidebar should close after hamburger click"
        # second click re-opens it
        page.click("#menu-toggle")
        page.wait_for_selector(".sidebar.open", timeout=2000)
        is_open = page.locator(".sidebar").evaluate("el => el.classList.contains('open')")
        assert is_open, "Sidebar should re-open after second hamburger click"

    def test_fab_present_on_mobile(self, page: Page):
        page.set_viewport_size({"width": 390, "height": 844})
        login(page)
        expect(page.locator("#fab-run")).to_be_visible()
        expect(page.locator("#fab-run")).to_have_text("Run")

    def test_nav_buttons_hidden_on_mobile(self, page: Page):
        page.set_viewport_size({"width": 390, "height": 844})
        login(page)
        # On mobile nav is display:none
        nav_display = page.evaluate("getComputedStyle(document.querySelector('.nav')).display")
        assert nav_display == "none", f"Nav should be hidden on mobile, got display={nav_display}"


# ─── API health check ─────────────────────────────────────────────────────────

class TestAPIEndpoints:
    def test_login_api_returns_ok(self, page: Page):
        resp = page.request.post(
            f"{BASE_URL}/api/login",
            data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        )
        assert resp.ok, f"Login should succeed, got {resp.status}"

    def test_login_api_wrong_creds_returns_401(self, page: Page):
        resp = page.request.post(
            f"{BASE_URL}/api/login",
            data={"email": "bad@x.com", "password": "wrong"},
        )
        assert resp.status == 401, f"Wrong creds should return 401, got {resp.status}"

    def test_me_endpoint_unauthenticated(self, page: Page):
        resp = page.request.get(f"{BASE_URL}/api/me")
        data = resp.json()
        assert data.get("authenticated") is False

    def test_history_endpoint_requires_auth(self, page: Page):
        resp = page.request.get(f"{BASE_URL}/api/history")
        assert resp.status == 401, f"History should require auth, got {resp.status}"

    def test_favorites_endpoint_requires_auth(self, page: Page):
        resp = page.request.get(f"{BASE_URL}/api/favorites")
        assert resp.status == 401, f"Favorites should require auth, got {resp.status}"
