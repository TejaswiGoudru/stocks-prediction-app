from datetime import date, datetime, timedelta
import pandas as pd
import streamlit as st
from utils import (
    COMMON_CSS,
    add_portfolio_position,
    clear_mongo_config,
    delete_portfolio_position,
    fetch_current_quote,
    get_mongo_config,
    load_portfolio,
    mask_mongo_uri,
    migrate_json_to_mongo,
    save_mongo_config,
    test_mongo_connection,
    update_portfolio_position,
)

st.html(COMMON_CSS)

st.title("Stock Portfolio & Holdings")
st.markdown("Save and track your stock holdings along with your active **Covered Call (CC)** and **Cash Secured Put (CSP)** options positions. Specify how many options are present, the purchase/open date, expiry date, strike price, and premium collected.")

# ---------------------------------------------------------
# MONGODB CONNECTION & CACHE MANAGEMENT
# ---------------------------------------------------------
mongo_cfg = get_mongo_config()

if not mongo_cfg:
    st.html("""
    <div style="background: linear-gradient(135deg, #f0fdf4 0%, #eff6ff 100%); border: 1px solid #bbf7d0; border-radius: 12px; padding: 18px 22px; margin-top: 14px; margin-bottom: 20px; box-shadow: 0 1px 4px rgba(0,0,0,0.04);">
        <div style="display:flex; align-items:center; gap:12px;">
            <span style="font-size:28px;">🍃</span>
            <div>
                <h4 style="margin:0; font-size:18px; font-weight:700; color:#14532d;">Connect Remote MongoDB (First-Time Setup)</h4>
                <div style="font-size:13px; color:#166534; margin-top:4px; line-height:1.4;">
                    Your portfolio positions are stored on a remote MongoDB server. Enter your connection string below to connect.
                    <br><b>Security notice:</b> The connection string is cached in a private local file excluded by <code>.gitignore</code> and <b>will never be committed to the code repository</b>.
                </div>
            </div>
        </div>
    </div>
    """)
    with st.expander("🔑 Setup Remote MongoDB Connection String", expanded=True):
        with st.form("mongo_setup_form"):
            uri_input = st.text_input(
                "MongoDB Connection String",
                type="password",
                placeholder="mongodb+srv://<username>:<password>@cluster0.mongodb.net/?retryWrites=true&w=majority",
                help="Enter standard MongoDB URI or MongoDB Atlas SRV URI with user credentials.",
            )
            col_m1, col_m2 = st.columns(2)
            with col_m1:
                db_input = st.text_input("Database Name", value="stock_portfolio")
            with col_m2:
                coll_input = st.text_input("Collection Name", value="portfolio_holdings")

            connect_btn = st.form_submit_button("🚀 Test, Connect & Cache Connection", type="primary")

            if connect_btn:
                if not uri_input:
                    st.error("Please enter a valid MongoDB connection string.")
                else:
                    with st.spinner("Testing connection to remote MongoDB..."):
                        ok, msg = test_mongo_connection(uri_input, db_input, coll_input)
                    if ok:
                        save_mongo_config(uri_input, db_input, coll_input)
                        m_ok, m_msg = migrate_json_to_mongo()
                        st.success(f"{msg} {m_msg if m_ok else ''}")
                        st.rerun()
                    else:
                        st.error(f"❌ {msg}")
                        st.info("💡 **Tips**:\n- In MongoDB Atlas, go to **Network Access** and verify that your IP is whitelisted (or `0.0.0.0/0` is allowed).\n- Check your username and password.\n- If your password contains special characters, make sure they are URL percent-encoded.")
else:
    masked_uri = mask_mongo_uri(mongo_cfg.get("uri", ""))
    db_name = mongo_cfg.get("db_name", "stock_portfolio")
    coll_name = mongo_cfg.get("collection_name", "portfolio_holdings")
    st.html(f"""
    <div style="background:#f0fdf4; border:1px solid #bbf7d0; border-radius:10px; padding:10px 18px; margin-top:14px; margin-bottom:18px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
        <div style="display:flex; align-items:center; gap:8px;">
            <span style="font-size:16px;">🟢</span>
            <span style="color:#14532d; font-weight:700; font-size:14px;">Remote MongoDB Connected</span>
            <span style="color:#15803d; font-size:13px;">&bull; <b>DB:</b> <code>{db_name}</code> &bull; <b>Collection:</b> <code>{coll_name}</code> &bull; <b>URI:</b> <code>{masked_uri}</code></span>
        </div>
    </div>
    """)
    with st.expander("⚙️ MongoDB Connection & Synchronization Settings"):
        col_st1, col_st2, col_st3 = st.columns(3)
        with col_st1:
            if st.button("Ping Connection", key="mongo_ping_btn"):
                ok, msg = test_mongo_connection(mongo_cfg.get("uri", ""), db_name, coll_name)
                if ok:
                    st.success(f"● {msg}")
                else:
                    st.error(f"● {msg}")
        with col_st2:
            if st.button("Re-sync Local JSON to MongoDB", key="mongo_sync_btn", help="Upload local portfolio.json positions to remote MongoDB"):
                ok, msg = migrate_json_to_mongo()
                if ok:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)
        with col_st3:
            if st.button("Disconnect / Reconfigure", key="mongo_disconnect_btn", type="secondary"):
                clear_mongo_config()
                st.warning("MongoDB disconnected from cache. Re-enter your connection string to reconnect.")
                st.rerun()

# Sync ticker with session_state across pages
if "ticker" not in st.session_state:
    st.session_state.ticker = "ORCL"

positions = load_portfolio()

# ---------------------------------------------------------
# PORTFOLIO SUMMARY KPIS (CC & CSP UNIFIED)
# ---------------------------------------------------------
live_quotes = {}
total_stock_cost = 0.0
total_stock_value = 0.0
total_stock_pnl = 0.0

total_csp_collateral = 0.0
total_csp_pnl = 0.0

total_active_cc_premium = 0.0
total_active_csp_premium = 0.0

for p in positions:
    t = p["ticker"]
    shs = p.get("shares", 0)
    cost = p.get("cost_basis", 0.0)

    if t not in live_quotes:
        price, chg = fetch_current_quote(t)
        live_quotes[t] = price if price is not None else cost
    cur_p = live_quotes[t]

    cc = p.get("covered_call", {})
    csp = p.get("cash_secured_put", {})
    has_cc = bool(cc and cc.get("active"))
    has_csp = bool(csp and csp.get("active"))
    is_pure_csp = has_csp and not has_cc and ("cash secured put" in p.get("notes", "").lower() or not has_cc)

    # 1. Stock & Covered Call metrics
    if not is_pure_csp:
        s_cost = shs * cost
        s_val = shs * cur_p
        total_stock_cost += s_cost
        total_stock_value += s_val
        total_stock_pnl += (s_val - s_cost)

    if has_cc:
        total_active_cc_premium += float(cc.get("premium", 0.0)) * 100 * int(cc.get("contracts", 1))

    # 2. Cash Secured Put metrics
    if has_csp:
        c_cnt = int(csp.get("contracts", 1))
        c_strike = float(csp.get("strike", cost))
        c_prem = float(csp.get("premium", 0.0))
        collateral = c_strike * 100 * c_cnt
        income = c_prem * 100 * c_cnt

        total_csp_collateral += collateral
        total_active_csp_premium += income

        if cur_p >= c_strike:
            c_pnl = income
        else:
            intrinsic_loss = (c_strike - cur_p) * 100 * c_cnt
            c_pnl = income - intrinsic_loss
        total_csp_pnl += c_pnl

total_portfolio_cost = total_stock_cost + total_csp_collateral
total_portfolio_value = total_stock_value + total_csp_collateral
total_combined_pnl = total_stock_pnl + total_csp_pnl
total_pnl_pct = (total_combined_pnl / total_portfolio_cost * 100.0) if total_portfolio_cost > 0 else 0.0
pnl_color = "#00875A" if total_combined_pnl >= 0 else "#e5443b"

st.html(f"""
<div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:14px; margin-top:12px; margin-bottom:28px;">
    <div class="metric-card" style="border-top:3px solid #2E384C;">
        <div style="font-size:12px; font-weight:700; color:#64748b; text-transform:uppercase; letter-spacing:0.04em; margin-bottom:6px;">Total Invested Capital</div>
        <div style="font-size:24px; font-weight:800; color:#1e293b;">${total_portfolio_cost:,.2f}</div>
        <div style="font-size:12px; color:#64748b; margin-top:4px;">Stock: ${total_stock_cost:,.0f} &bull; CSP Collateral: ${total_csp_collateral:,.0f}</div>
    </div>
    <div class="metric-card" style="border-top:3px solid #0284c7;">
        <div style="font-size:12px; font-weight:700; color:#0284c7; text-transform:uppercase; letter-spacing:0.04em; margin-bottom:6px;">Current Market Value</div>
        <div style="font-size:24px; font-weight:800; color:#1e293b;">${total_portfolio_value:,.2f}</div>
        <div style="font-size:12px; color:#64748b; margin-top:4px;">Stock: ${total_stock_value:,.0f} &bull; Cash Collateral: ${total_csp_collateral:,.0f}</div>
    </div>
    <div class="metric-card" style="border-top:3px solid {pnl_color};">
        <div style="font-size:12px; font-weight:700; color:{pnl_color}; text-transform:uppercase; letter-spacing:0.04em; margin-bottom:6px;">Total Unrealized P&amp;L</div>
        <div style="font-size:24px; font-weight:800; color:{pnl_color};">{total_pnl_pct:+.2f}%</div>
        <div style="font-size:12px; color:{pnl_color}; font-weight:600; margin-top:4px;">${total_combined_pnl:+,.2f} (Stock: ${total_stock_pnl:+,.0f} &bull; CSP: ${total_csp_pnl:+,.0f})</div>
    </div>
    <div class="metric-card" style="border-top:3px solid #00875A;">
        <div style="font-size:12px; font-weight:700; color:#00875A; text-transform:uppercase; letter-spacing:0.04em; margin-bottom:6px;">Active Option Income</div>
        <div style="font-size:24px; font-weight:800; color:#00875A;">+${(total_active_cc_premium + total_active_csp_premium):,.2f}</div>
        <div style="font-size:12px; color:#00875A; font-weight:600; margin-top:4px;">CC: +${total_active_cc_premium:,.0f} &bull; CSP: +${total_active_csp_premium:,.0f}</div>
    </div>
</div>
""")


# ---------------------------------------------------------
# ADD NEW POSITION FORM
# ---------------------------------------------------------
with st.expander("➕ Add Stock & Option Positions to Portfolio", expanded=(len(positions) == 0)):
    with st.form("add_position_form", clear_on_submit=True):
        st.markdown("##### 1. Stock Holding Details")
        col_f1, col_f2, col_f3, col_f4 = st.columns([1.2, 1.2, 1.4, 1.4])
        with col_f1:
            new_ticker = st.text_input("Ticker Symbol", placeholder="e.g. AAPL, NVDA, ORCL", max_chars=6).strip().upper()
        with col_f2:
            new_shares = st.number_input("Shares Owned", min_value=1, max_value=100000, value=100, step=10)
        with col_f3:
            new_cost = st.number_input("Purchase Price / Share ($)", min_value=0.01, max_value=10000.0, value=150.00, step=1.00, format="%.2f")
        with col_f4:
            new_date = st.date_input("Stock Purchase Date", value=date.today() - timedelta(days=45), max_value=date.today())

        new_notes = st.text_input("Notes / Strategy (optional)", placeholder="e.g. Core long-term holding with monthly call/put wheel")

        st.markdown("---")
        st.markdown("##### 2. Active Options Positions (Optional)")

        col_opt1, col_opt2 = st.columns(2)

        # ---------------- Active Covered Call Section ----------------
        with col_opt1:
            st.markdown("###### 🪙 Active Covered Call (CC)")
            has_cc = st.checkbox("Has active Covered Call for this stock", value=False, key="add_has_cc")
            cc_data = {"active": False}
            if has_cc:
                c_cc1, c_cc2 = st.columns(2)
                with c_cc1:
                    cc_cnt = st.number_input("Options Present (Contracts)", min_value=1, max_value=100, value=max(new_shares // 100, 1), step=1, key="add_cc_cnt")
                    cc_open = st.date_input("Option Purchase / Open Date", value=date.today() - timedelta(days=7), max_value=date.today(), key="add_cc_open")
                    cc_exp = st.date_input("Option Expiry Date", value=date.today() + timedelta(days=30), key="add_cc_exp")
                with c_cc2:
                    cc_strike = st.number_input("Call Strike Price ($)", min_value=0.01, value=float(round(new_cost * 1.05, 2)), step=1.0, format="%.2f", key="add_cc_strike")
                    cc_prem = st.number_input("Premium Received / Share ($)", min_value=0.01, value=2.50, step=0.10, format="%.2f", key="add_cc_prem")

                cc_data = {
                    "active": True,
                    "contracts": int(cc_cnt),
                    "open_date": str(cc_open),
                    "expiration": str(cc_exp),
                    "strike": float(cc_strike),
                    "premium": float(cc_prem),
                }

        # ---------------- Active Cash Secured Put Section ----------------
        with col_opt2:
            st.markdown("###### 💰 Active Cash Secured Put (CSP)")
            has_csp = st.checkbox("Has active Cash Secured Put for this stock", value=False, key="add_has_csp")
            csp_data = {"active": False}
            if has_csp:
                c_csp1, c_csp2 = st.columns(2)
                with c_csp1:
                    csp_cnt = st.number_input("Options Present (Contracts)", min_value=1, max_value=100, value=1, step=1, key="add_csp_cnt")
                    csp_open = st.date_input("Option Purchase / Open Date", value=date.today() - timedelta(days=7), max_value=date.today(), key="add_csp_open")
                    csp_exp = st.date_input("Option Expiry Date", value=date.today() + timedelta(days=30), key="add_csp_exp")
                with c_csp2:
                    csp_strike = st.number_input("Put Strike Price ($)", min_value=0.01, value=float(round(new_cost * 0.95, 2)), step=1.0, format="%.2f", key="add_csp_strike")
                    csp_prem = st.number_input("Premium Received / Share ($)", min_value=0.01, value=2.20, step=0.10, format="%.2f", key="add_csp_prem")

                csp_data = {
                    "active": True,
                    "contracts": int(csp_cnt),
                    "open_date": str(csp_open),
                    "expiration": str(csp_exp),
                    "strike": float(csp_strike),
                    "premium": float(csp_prem),
                }

        submit_btn = st.form_submit_button("Save Position to Portfolio", type="primary")
        if submit_btn:
            if not new_ticker:
                st.error("Please enter a valid stock ticker symbol.")
            else:
                add_portfolio_position(
                    new_ticker,
                    new_shares,
                    new_cost,
                    str(new_date),
                    new_notes,
                    covered_call=cc_data,
                    cash_secured_put=csp_data,
                )
                st.success(f"Saved {new_shares} shares of {new_ticker} with active options to your portfolio!")
                st.rerun()

# ---------------------------------------------------------
# SAVED HOLDINGS LIST & MANAGEMENT
# ---------------------------------------------------------
st.html("""
<div style="display:flex; align-items:center; margin-top:24px; margin-bottom:14px;">
    <div style="width:4px; height:22px; background-color:#489e60; border-radius:2px; margin-right:12px;"></div>
    <h3 style="margin:0; font-size:22px; font-weight:700; color:#1e293b; letter-spacing:-0.02em;">Your Saved Holdings</h3>
</div>
""")

if not positions:
    st.info("No stock positions saved yet. Click **Add Stock & Option Positions** above to track your holdings.")
else:
    for idx, pos in enumerate(positions):
        t = pos["ticker"]
        shs = pos.get("shares", 0)
        cost = pos.get("cost_basis", 0.0)
        p_date = pos.get("purchase_date", "N/A")
        notes = pos.get("notes", "")

        cc = pos.get("covered_call", {})
        csp = pos.get("cash_secured_put", {})
        has_cc = bool(cc and cc.get("active"))
        has_csp = bool(csp and csp.get("active"))
        is_pure_csp = has_csp and not has_cc and ("cash secured put" in notes.lower() or not has_cc)

        cur_p = live_quotes.get(t, cost)

        # ---------------- 1. Covered Call / Stock Calculations ----------------
        pos_cost = shs * cost
        pos_val = shs * cur_p
        pos_pnl = pos_val - pos_cost
        pos_pnl_pct = (pos_pnl / pos_cost * 100.0) if pos_cost > 0 else 0.0
        cc_contracts = shs // 100
        pos_color = "#00875A" if pos_pnl >= 0 else "#e5443b"
        cc_cnt_val = int(cc.get("contracts", max(cc_contracts, 1))) if has_cc else cc_contracts
        cc_prem_val = float(cc.get("premium", 0.0)) if has_cc else 0.0

        # ---------------- 2. Cash Secured Put Calculations ----------------
        csp_cnt_val = int(csp.get("contracts", 1)) if has_csp else 1
        csp_strike_val = float(csp.get("strike", cost)) if has_csp else float(cost)
        csp_prem_val = float(csp.get("premium", 0.0)) if has_csp else 0.0
        csp_open_val = csp.get("open_date", "N/A") if has_csp else "N/A"
        csp_exp_val = csp.get("expiration", "N/A") if has_csp else "N/A"
        csp_collateral = csp_strike_val * 100 * csp_cnt_val
        csp_income = csp_prem_val * 100 * csp_cnt_val
        csp_breakeven = csp_strike_val - csp_prem_val

        # Safety buffer vs Put Strike
        if csp_strike_val > 0:
            csp_diff = cur_p - csp_strike_val
            csp_diff_pct = (csp_diff / csp_strike_val) * 100.0
            if cur_p >= csp_strike_val:
                csp_otm_str = f"+{csp_diff_pct:.1f}% OTM Safe"
                csp_otm_color = "#00875A"
            else:
                csp_otm_str = f"{csp_diff_pct:.1f}% ITM"
                csp_otm_color = "#e5443b"
        else:
            csp_otm_str = ""
            csp_otm_color = "#64748b"

        # CSP Unrealized P&L: If spot >= strike, 100% of premium is kept. If below, intrinsic loss offset by premium.
        if has_csp:
            if cur_p >= csp_strike_val:
                csp_pnl = csp_income
            else:
                intrinsic_loss = (csp_strike_val - cur_p) * 100 * csp_cnt_val
                csp_pnl = csp_income - intrinsic_loss
            csp_pnl_pct = (csp_pnl / csp_collateral * 100.0) if csp_collateral > 0 else 0.0
            csp_pnl_color = "#00875A" if csp_pnl >= 0 else "#e5443b"
        else:
            csp_pnl = 0.0
            csp_pnl_pct = 0.0
            csp_pnl_color = "#64748b"

        # Holding duration
        try:
            ref_date = csp_open_val if (is_pure_csp and csp_open_val != "N/A") else p_date
            days_held = (date.today() - datetime.strptime(ref_date, "%Y-%m-%d").date()).days
            days_str = f"{days_held} days open" if is_pure_csp else f"{days_held} days held"
        except Exception:
            days_str = ""

        # Badges for active options with all 5 details
        options_badges_html = []
        if has_cc:
            total_cc_cash = cc_prem_val * 100 * cc_cnt_val
            options_badges_html.append(
                f'<div style="background:#f0f9ff; color:#0369a1; border:1px solid #bae6fd; padding:6px 12px; border-radius:8px; font-size:13px; line-height:1.4;">'
                f'<b>🪙 Active Covered Call</b>: <b>{cc_cnt_val} option(s)</b> &bull; <b>${cc.get("strike", 0.0):.2f} Strike</b> &bull; '
                f'Premium: <b>+${cc.get("premium", 0.0):.2f}/sh</b> (+${total_cc_cash:,.2f}) &bull; '
                f'Opened: <b>{cc.get("open_date", "N/A")}</b> &bull; Expiry: <b>{cc.get("expiration", "")}</b>'
                f'</div>'
            )

        if has_csp:
            total_csp_cash = csp_prem_val * 100 * csp_cnt_val
            options_badges_html.append(
                f'<div style="background:#fefce8; color:#854d0e; border:1px solid #fef08a; padding:6px 12px; border-radius:8px; font-size:13px; line-height:1.4;">'
                f'<b>💰 Active Cash Secured Put</b>: <b>{csp_cnt_val} option(s)</b> &bull; <b>${csp_strike_val:.2f} Strike</b> &bull; '
                f'Premium: <b>+${csp_prem_val:.2f}/sh</b> (+${total_csp_cash:,.2f}) &bull; '
                f'Opened: <b>{csp_open_val}</b> &bull; Expiry: <b>{csp_exp_val}</b>'
                f'</div>'
            )

        badges_rendered = f'<div style="display:flex; flex-direction:column; gap:6px; margin-top:8px;">{" ".join(options_badges_html)}</div>' if options_badges_html else ''

        # Visual theme & labels
        if is_pure_csp:
            card_border = "#b45309"
            badge_bg = "#fefce8"
            badge_txt = "#854d0e"
            badge_border = "#fef08a"
            strategy_label = "Cash Secured Put Holding"
            subtitle_text = f"Strike: <b>${csp_strike_val:.2f}</b> &bull; Collateral: <b>${csp_collateral:,.2f}</b> &bull; Opened on {csp_open_val} {f'({days_str})' if days_str else ''}"
        elif has_cc and has_csp:
            card_border = "#00875A"
            badge_bg = "#f0fdf4"
            badge_txt = "#166534"
            badge_border = "#bbf7d0"
            strategy_label = "Dual Strategy (CC + CSP Wheel)"
            subtitle_text = f"{shs:,} shares &bull; Bought @ <b>${cost:.2f}</b> on {p_date} {f'({days_str})' if days_str else ''}"
        elif has_cc:
            card_border = "#0284c7"
            badge_bg = "#f0f9ff"
            badge_txt = "#0369a1"
            badge_border = "#bae6fd"
            strategy_label = "Covered Call Holding"
            subtitle_text = f"{shs:,} shares &bull; Bought @ <b>${cost:.2f}</b> on {p_date} {f'({days_str})' if days_str else ''}"
        else:
            card_border = "#2E384C"
            badge_bg = "#f1f5f9"
            badge_txt = "#334155"
            badge_border = "#cbd5e1"
            strategy_label = "Stock Holding"
            subtitle_text = f"{shs:,} shares &bull; Bought @ <b>${cost:.2f}</b> on {p_date} {f'({days_str})' if days_str else ''}"

        # HTML row for Covered Call Metrics (only if active)
        cc_metric_row = ""
        if has_cc:
            cc_metric_row = f"""
        <div style="background:#f8fafc; border:1px solid #e2e8f0; border-radius:8px; padding:10px 16px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px;">
            <div style="display:flex; align-items:center; gap:8px; min-width:160px;">
                <span style="font-size:16px;">🪙</span>
                <div>
                    <div style="font-size:12px; font-weight:700; color:#0369a1; text-transform:uppercase; letter-spacing:0.03em;">Covered Call</div>
                    <div style="font-size:11px; color:#64748b;">Active CC Option &bull; {shs:,} shs</div>
                </div>
            </div>
            <div style="display:flex; gap:22px; align-items:center; flex-wrap:wrap;">
                <div style="text-align:right;">
                    <div style="font-size:11px; color:#64748b; font-weight:600;">Spot Price</div>
                    <div style="font-size:16px; font-weight:700; color:#1e293b;">${cur_p:.2f}</div>
                </div>
                <div style="text-align:right;">
                    <div style="font-size:11px; color:#64748b; font-weight:600;">Market Value</div>
                    <div style="font-size:16px; font-weight:700; color:#1e293b;">${pos_val:,.2f}</div>
                </div>
                <div style="text-align:right;">
                    <div style="font-size:11px; color:#64748b; font-weight:600;">Unrealized P&amp;L</div>
                    <div style="font-size:16px; font-weight:800; color:{pos_color};">{pos_pnl_pct:+.2f}% (${pos_pnl:+,.2f})</div>
                </div>
                <div style="text-align:right; min-width:90px;">
                    <div style="font-size:11px; color:#00875A; font-weight:600;">CC Ready</div>
                    <div style="font-size:16px; font-weight:800; color:#00875A;">{cc_cnt_val} contract(s) Active</div>
                </div>
            </div>
        </div>
        """

        # HTML row for Cash Secured Put Metrics (only if active)
        csp_metric_row = ""
        if has_csp:
            csp_metric_row = f"""
        <div style="background:#fefce8; border:1px solid #fef08a; border-radius:8px; padding:10px 16px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px;">
            <div style="display:flex; align-items:center; gap:8px; min-width:160px;">
                <span style="font-size:16px;">💰</span>
                <div>
                    <div style="font-size:12px; font-weight:700; color:#854d0e; text-transform:uppercase; letter-spacing:0.03em;">Cash Secured Put</div>
                    <div style="font-size:11px; color:#a16207;">Active Put Option &bull; Strike ${csp_strike_val:.2f}</div>
                </div>
            </div>
            <div style="display:flex; gap:22px; align-items:center; flex-wrap:wrap;">
                <div style="text-align:right;">
                    <div style="font-size:11px; color:#64748b; font-weight:600;">Spot Price</div>
                    <div style="font-size:16px; font-weight:700; color:#1e293b;">${cur_p:.2f} <span style="font-size:11px; font-weight:700; color:{csp_otm_color};">({csp_otm_str})</span></div>
                </div>
                <div style="text-align:right;">
                    <div style="font-size:11px; color:#64748b; font-weight:600;">Market Value</div>
                    <div style="font-size:16px; font-weight:700; color:#1e293b;">${csp_collateral:,.2f}</div>
                </div>
                <div style="text-align:right;">
                    <div style="font-size:11px; color:#64748b; font-weight:600;">Unrealized P&amp;L</div>
                    <div style="font-size:16px; font-weight:800; color:{csp_pnl_color};">{csp_pnl_pct:+.2f}% (${csp_pnl:+,.2f})</div>
                </div>
                <div style="text-align:right; min-width:90px;">
                    <div style="font-size:11px; color:#b45309; font-weight:600;">CSP Ready</div>
                    <div style="font-size:16px; font-weight:800; color:#b45309;">{csp_cnt_val} contract(s) Active</div>
                </div>
            </div>
        </div>
        """

        # Assemble rows only for active options
        metric_rows = []
        if is_pure_csp:
            if csp_metric_row: metric_rows.append(csp_metric_row)
            if cc_metric_row: metric_rows.append(cc_metric_row)
        else:
            if cc_metric_row: metric_rows.append(cc_metric_row)
            if csp_metric_row: metric_rows.append(csp_metric_row)

        if metric_rows:
            metrics_html = f'<div style="display:flex; flex-direction:column; gap:8px; margin-top:12px;">{"".join(metric_rows)}</div>'
        else:
            metrics_html = f"""
            <div style="background:#f8fafc; border:1px dashed #cbd5e1; border-radius:8px; padding:12px 18px; margin-top:12px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
                <div style="display:flex; align-items:center; gap:8px; color:#64748b; font-size:13px;">
                    <span>ℹ️</span> <span>No active <b>Covered Call</b> or <b>Cash Secured Put</b> options recorded for {t}.</span>
                </div>
                <div style="font-size:12px; color:#475569;">
                    Use the buttons below to analyze and open active options positions.
                </div>
            </div>
            """


        with st.container():
            st.html(f"""
            <div style="background:#ffffff; border:1px solid #e2e8f0; border-left:4px solid {card_border}; border-radius:12px; padding:18px 22px; margin-bottom:12px; box-shadow:0 1px 3px rgba(0,0,0,0.03);">
                <div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:12px;">
                    <div>
                        <div style="display:flex; align-items:center; gap:10px;">
                            <span style="font-size:22px; font-weight:800; color:#1e293b;">{t}</span>
                            <span style="background:{badge_bg}; color:{badge_txt}; border:1px solid {badge_border}; font-size:11px; font-weight:700; padding:2px 8px; border-radius:6px; text-transform:uppercase;">{strategy_label}</span>
                        </div>
                        <div style="font-size:13px; color:#64748b; margin-top:4px;">{subtitle_text}</div>
                        {f'<div style="font-size:12px; color:#64748b; margin-top:3px; font-style:italic;">"{notes}"</div>' if notes else ''}
                        {badges_rendered}
                    </div>
                </div>
                {metrics_html}
            </div>
            """)


            col_btn1, col_btn2, col_btn3 = st.columns([1.5, 1.5, 0.6])
            with col_btn1:
                cc_label = f"Analyze Covered Call ({t})"
                if st.button(cc_label, key=f"cc_btn_{pos['id']}", type="secondary"):
                    st.session_state.ticker = t
                    st.session_state.selected_portfolio_id = pos["id"]
                    st.session_state.cc_cost_basis = cost
                    try:
                        st.session_state.cc_purchase_date = datetime.strptime(p_date, "%Y-%m-%d").date()
                    except Exception:
                        pass
                    if cc and cc.get("active"):
                        st.session_state.cc_contracts = int(cc.get("contracts", max(shs // 100, 1)))
                        st.session_state.cc_target_open_date = cc.get("open_date")
                        st.session_state.cc_target_exp = cc.get("expiration")
                        st.session_state.cc_target_strike = float(cc.get("strike", 0.0))
                        st.session_state.cc_target_premium = float(cc.get("premium", 0.0))
                    else:
                        st.session_state.cc_contracts = max(shs // 100, 1)
                        st.session_state.cc_target_open_date = None
                        st.session_state.cc_target_exp = None
                        st.session_state.cc_target_strike = None
                        st.session_state.cc_target_premium = None
                    st.switch_page("app_pages/covered_call.py")

            with col_btn2:
                csp_label = f"Analyze Cash Secured Put ({t})"
                if st.button(csp_label, key=f"csp_btn_{pos['id']}", type="secondary"):
                    st.session_state.ticker = t
                    st.session_state.selected_portfolio_id = pos["id"]
                    if csp and csp.get("active"):
                        st.session_state.csp_contracts = int(csp.get("contracts", 1))
                        st.session_state.csp_target_open_date = csp.get("open_date")
                        st.session_state.csp_target_exp = csp.get("expiration")
                        st.session_state.csp_target_strike = float(csp.get("strike", 0.0))
                        st.session_state.csp_target_premium = float(csp.get("premium", 0.0))
                    else:
                        st.session_state.csp_contracts = 1
                        st.session_state.csp_target_open_date = None
                        st.session_state.csp_target_exp = None
                        st.session_state.csp_target_strike = None
                        st.session_state.csp_target_premium = None
                    st.switch_page("app_pages/cash_secured_put.py")

            with col_btn3:
                if st.button("Delete", key=f"del_btn_{pos['id']}", help=f"Remove {t} from portfolio"):
                    delete_portfolio_position(pos["id"])
                    st.toast(f"Removed {t} from portfolio.", icon="🗑️")
                    st.rerun()

            # Optional: In-place edit expander for this stock's active options
            with st.expander(f"⚙️ Quick Edit Active Options for {t}"):
                with st.form(f"edit_options_form_{pos['id']}"):
                    col_ed_cc, col_ed_csp = st.columns(2)
                    with col_ed_cc:
                        st.markdown("###### 🪙 Covered Call (CC)")
                        ed_has_cc = st.checkbox("Active Covered Call", value=bool(cc and cc.get("active")), key=f"ed_has_cc_{pos['id']}")
                        ed_cc_cnt = st.number_input("Options Present (Contracts)", min_value=1, max_value=100, value=int(cc.get("contracts", max(shs // 100, 1))) if cc else 1, key=f"ed_cc_cnt_{pos['id']}")
                        
                        ed_cc_open_def = date.today() - timedelta(days=7)
                        if cc and cc.get("open_date"):
                            try: ed_cc_open_def = datetime.strptime(cc["open_date"], "%Y-%m-%d").date()
                            except: pass
                        ed_cc_open = st.date_input("Purchase / Open Date", value=ed_cc_open_def, max_value=date.today(), key=f"ed_cc_open_{pos['id']}")

                        ed_cc_exp_def = date.today() + timedelta(days=30)
                        if cc and cc.get("expiration"):
                            try: ed_cc_exp_def = datetime.strptime(cc["expiration"], "%Y-%m-%d").date()
                            except: pass
                        ed_cc_exp = st.date_input("Expiry Date", value=ed_cc_exp_def, key=f"ed_cc_exp_{pos['id']}")

                        ed_cc_strike = st.number_input("Call Strike Price ($)", min_value=0.01, value=float(cc.get("strike", round(cost * 1.05, 2))) if cc else float(round(cost * 1.05, 2)), step=1.0, format="%.2f", key=f"ed_cc_strike_{pos['id']}")
                        ed_cc_prem = st.number_input("Premium Received ($ / sh)", min_value=0.01, value=float(cc.get("premium", 2.50)) if cc else 2.50, step=0.10, format="%.2f", key=f"ed_cc_prem_{pos['id']}")

                    with col_ed_csp:
                        st.markdown("###### 💰 Cash Secured Put (CSP)")
                        ed_has_csp = st.checkbox("Active Cash Secured Put", value=bool(csp and csp.get("active")), key=f"ed_has_csp_{pos['id']}")
                        ed_csp_cnt = st.number_input("Options Present (Contracts)", min_value=1, max_value=100, value=int(csp.get("contracts", 1)) if csp else 1, key=f"ed_csp_cnt_{pos['id']}")

                        ed_csp_open_def = date.today() - timedelta(days=7)
                        if csp and csp.get("open_date"):
                            try: ed_csp_open_def = datetime.strptime(csp["open_date"], "%Y-%m-%d").date()
                            except: pass
                        ed_csp_open = st.date_input("Purchase / Open Date", value=ed_csp_open_def, max_value=date.today(), key=f"ed_csp_open_{pos['id']}")

                        ed_csp_exp_def = date.today() + timedelta(days=30)
                        if csp and csp.get("expiration"):
                            try: ed_csp_exp_def = datetime.strptime(csp["expiration"], "%Y-%m-%d").date()
                            except: pass
                        ed_csp_exp = st.date_input("Expiry Date", value=ed_csp_exp_def, key=f"ed_csp_exp_{pos['id']}")

                        ed_csp_strike = st.number_input("Put Strike Price ($)", min_value=0.01, value=float(csp.get("strike", round(cost * 0.95, 2))) if csp else float(round(cost * 0.95, 2)), step=1.0, format="%.2f", key=f"ed_csp_strike_{pos['id']}")
                        ed_csp_prem = st.number_input("Premium Received ($ / sh)", min_value=0.01, value=float(csp.get("premium", 2.20)) if csp else 2.20, step=0.10, format="%.2f", key=f"ed_csp_prem_{pos['id']}")

                    save_ed_btn = st.form_submit_button(f"Update Options for {t}", type="primary")
                    if save_ed_btn:
                        updated_cc = {
                            "active": ed_has_cc,
                            "contracts": int(ed_cc_cnt),
                            "open_date": str(ed_cc_open),
                            "expiration": str(ed_cc_exp),
                            "strike": float(ed_cc_strike),
                            "premium": float(ed_cc_prem),
                        } if ed_has_cc else {"active": False}

                        updated_csp = {
                            "active": ed_has_csp,
                            "contracts": int(ed_csp_cnt),
                            "open_date": str(ed_csp_open),
                            "expiration": str(ed_csp_exp),
                            "strike": float(ed_csp_strike),
                            "premium": float(ed_csp_prem),
                        } if ed_has_csp else {"active": False}

                        update_portfolio_position(pos["id"], {
                            "covered_call": updated_cc,
                            "cash_secured_put": updated_csp,
                        })
                        st.success(f"Updated active options for {t}!")
                        st.rerun()
