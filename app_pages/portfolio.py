from datetime import date, datetime, timedelta
import pandas as pd
import streamlit as st
from utils import (
    COMMON_CSS,
    add_portfolio_position,
    delete_portfolio_position,
    fetch_current_quote,
    load_portfolio,
    update_portfolio_position,
)

st.html(COMMON_CSS)

st.title("Stock Portfolio & Holdings")
st.markdown("Save and track your stock holdings along with your active **Covered Call (CC)** and **Cash Secured Put (CSP)** options positions. Specify how many options are present, the purchase/open date, expiry date, strike price, and premium collected.")

# Sync ticker with session_state across pages
if "ticker" not in st.session_state:
    st.session_state.ticker = "ORCL"

positions = load_portfolio()

# ---------------------------------------------------------
# PORTFOLIO SUMMARY KPIS
# ---------------------------------------------------------
total_cost = sum(p["shares"] * p["cost_basis"] for p in positions)
total_value = 0.0
total_active_cc_premium = 0.0
total_active_csp_premium = 0.0
live_quotes = {}

for p in positions:
    t = p["ticker"]
    if t not in live_quotes:
        price, chg = fetch_current_quote(t)
        live_quotes[t] = price if price is not None else p["cost_basis"]
    cur_p = live_quotes[t]
    total_value += p["shares"] * cur_p

    cc = p.get("covered_call", {})
    if cc and cc.get("active"):
        total_active_cc_premium += float(cc.get("premium", 0.0)) * 100 * int(cc.get("contracts", 1))

    csp = p.get("cash_secured_put", {})
    if csp and csp.get("active"):
        total_active_csp_premium += float(csp.get("premium", 0.0)) * 100 * int(csp.get("contracts", 1))

total_pnl = total_value - total_cost
total_pnl_pct = (total_pnl / total_cost * 100.0) if total_cost > 0 else 0.0
total_cc_contracts = sum(p["shares"] // 100 for p in positions)
pnl_color = "#00875A" if total_pnl >= 0 else "#e5443b"

st.html(f"""
<div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:14px; margin-top:12px; margin-bottom:28px;">
    <div class="metric-card" style="border-top:3px solid #2E384C;">
        <div style="font-size:12px; font-weight:700; color:#64748b; text-transform:uppercase; letter-spacing:0.04em; margin-bottom:6px;">Total Invested Cost</div>
        <div style="font-size:24px; font-weight:800; color:#1e293b;">${total_cost:,.2f}</div>
        <div style="font-size:12px; color:#64748b; margin-top:4px;">{len(positions)} position(s) tracked</div>
    </div>
    <div class="metric-card" style="border-top:3px solid #0284c7;">
        <div style="font-size:12px; font-weight:700; color:#0284c7; text-transform:uppercase; letter-spacing:0.04em; margin-bottom:6px;">Current Market Value</div>
        <div style="font-size:24px; font-weight:800; color:#1e293b;">${total_value:,.2f}</div>
        <div style="font-size:12px; color:#64748b; margin-top:4px;">Live market quotes</div>
    </div>
    <div class="metric-card" style="border-top:3px solid {pnl_color};">
        <div style="font-size:12px; font-weight:700; color:{pnl_color}; text-transform:uppercase; letter-spacing:0.04em; margin-bottom:6px;">Total Unrealized P&amp;L</div>
        <div style="font-size:24px; font-weight:800; color:{pnl_color};">{total_pnl_pct:+.2f}%</div>
        <div style="font-size:12px; color:{pnl_color}; font-weight:600; margin-top:4px;">${total_pnl:+,.2f} overall stock gain/loss</div>
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
        shs = pos["shares"]
        cost = pos["cost_basis"]
        p_date = pos.get("purchase_date", "N/A")
        notes = pos.get("notes", "")

        cc = pos.get("covered_call", {})
        csp = pos.get("cash_secured_put", {})

        cur_p = live_quotes.get(t, cost)
        pos_cost = shs * cost
        pos_val = shs * cur_p
        pos_pnl = pos_val - pos_cost
        pos_pnl_pct = (pos_pnl / pos_cost * 100.0) if pos_cost > 0 else 0.0
        cc_contracts = shs // 100

        try:
            days_held = (date.today() - datetime.strptime(p_date, "%Y-%m-%d").date()).days
            days_str = f"{days_held} days held"
        except Exception:
            days_str = ""

        pos_color = "#00875A" if pos_pnl >= 0 else "#e5443b"

        # Badges for active options with all 5 details
        options_badges_html = []
        if cc and cc.get("active"):
            cc_cnt_val = cc.get("contracts", 1)
            cc_strike_val = cc.get("strike", 0.0)
            cc_exp_val = cc.get("expiration", "")
            cc_prem_val = cc.get("premium", 0.0)
            cc_open_val = cc.get("open_date", "N/A")
            total_cc_cash = cc_prem_val * 100 * cc_cnt_val
            options_badges_html.append(
                f'<div style="background:#f0f9ff; color:#0369a1; border:1px solid #bae6fd; padding:6px 12px; border-radius:8px; font-size:13px; line-height:1.4;">'
                f'<b>🪙 Active Covered Call</b>: <b>{cc_cnt_val} option(s)</b> &bull; <b>${cc_strike_val:.2f} Strike</b> &bull; '
                f'Premium: <b>+${cc_prem_val:.2f}/sh</b> (+${total_cc_cash:,.2f}) &bull; '
                f'Opened: <b>{cc_open_val}</b> &bull; Expiry: <b>{cc_exp_val}</b>'
                f'</div>'
            )

        if csp and csp.get("active"):
            csp_cnt_val = csp.get("contracts", 1)
            csp_strike_val = csp.get("strike", 0.0)
            csp_exp_val = csp.get("expiration", "")
            csp_prem_val = csp.get("premium", 0.0)
            csp_open_val = csp.get("open_date", "N/A")
            total_csp_cash = csp_prem_val * 100 * csp_cnt_val
            options_badges_html.append(
                f'<div style="background:#fefce8; color:#854d0e; border:1px solid #fef08a; padding:6px 12px; border-radius:8px; font-size:13px; line-height:1.4;">'
                f'<b>💰 Active Cash Secured Put</b>: <b>{csp_cnt_val} option(s)</b> &bull; <b>${csp_strike_val:.2f} Strike</b> &bull; '
                f'Premium: <b>+${csp_prem_val:.2f}/sh</b> (+${total_csp_cash:,.2f}) &bull; '
                f'Opened: <b>{csp_open_val}</b> &bull; Expiry: <b>{csp_exp_val}</b>'
                f'</div>'
            )

        badges_rendered = f'<div style="display:flex; flex-direction:column; gap:6px; margin-top:10px;">{" ".join(options_badges_html)}</div>' if options_badges_html else ''

        with st.container():
            st.html(f"""
            <div style="background:#ffffff; border:1px solid #e2e8f0; border-left:4px solid {'#00875A' if cc_contracts > 0 else '#2E384C'}; border-radius:12px; padding:18px 22px; margin-bottom:12px; box-shadow:0 1px 3px rgba(0,0,0,0.03);">
                <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px;">
                    <div>
                        <span style="font-size:20px; font-weight:800; color:#1e293b;">{t}</span>
                        <span style="font-size:14px; color:#64748b; margin-left:10px;">{shs:,} shares &bull; Bought @ <b>${cost:.2f}</b> on {p_date} {f'({days_str})' if days_str else ''}</span>
                        {f'<div style="font-size:12px; color:#64748b; margin-top:4px; font-style:italic;">"{notes}"</div>' if notes else ''}
                        {badges_rendered}
                    </div>
                    <div style="display:flex; gap:20px; align-items:center; flex-wrap:wrap;">
                        <div style="text-align:right;">
                            <div style="font-size:12px; color:#64748b; font-weight:600;">Spot Price</div>
                            <div style="font-size:16px; font-weight:700; color:#1e293b;">${cur_p:.2f}</div>
                        </div>
                        <div style="text-align:right;">
                            <div style="font-size:12px; color:#64748b; font-weight:600;">Market Value</div>
                            <div style="font-size:16px; font-weight:700; color:#1e293b;">${pos_val:,.2f}</div>
                        </div>
                        <div style="text-align:right;">
                            <div style="font-size:12px; color:#64748b; font-weight:600;">Unrealized P&amp;L</div>
                            <div style="font-size:16px; font-weight:800; color:{pos_color};">{pos_pnl_pct:+.2f}% (${pos_pnl:+,.2f})</div>
                        </div>
                        <div style="text-align:right;">
                            <div style="font-size:12px; color:#00875A; font-weight:600;">CC Ready</div>
                            <div style="font-size:16px; font-weight:800; color:#00875A;">{cc_contracts} contract(s)</div>
                        </div>
                    </div>
                </div>
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
