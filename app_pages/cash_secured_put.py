import re
from datetime import date, datetime, timedelta
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from utils import (
    COMMON_CSS,
    calc_put_greeks,
    fetch_option_expirations,
    fetch_put_chain,
    fetch_stock_forecast,
    fetch_walletinvestor_data,
    get_stock_branding_and_ratings,
    load_portfolio,
    render_analyst_ratings_dashboard,
    render_ticker_brand_badge,
    save_or_update_option_position,
)

st.html(COMMON_CSS)

st.title("Cash Secured Put (CSP) Strategy")
st.markdown("Sell cash-secured puts below technical support to generate steady income or acquire quality shares at a net discount.")

# Sync ticker with session_state across pages
if "ticker" not in st.session_state:
    st.session_state.ticker = "ORCL"

# Load saved positions from portfolio
positions = load_portfolio()
portfolio_labels = ["✏️ Custom Entry (Manual Input)"]
for p in positions:
    csp = p.get("cash_secured_put", {})
    if csp and csp.get("active"):
        csp_info = f"💰 Active CSP: ${csp.get('strike', 0):.2f} exp {csp.get('expiration', '')}"
    else:
        csp_info = f"{p.get('shares', 100)} shs"
    portfolio_labels.append(f"{p['ticker']} • ({csp_info})")

selected_port_idx = 0
if "selected_portfolio_id" in st.session_state and st.session_state.selected_portfolio_id:
    for idx, p in enumerate(positions):
        if p["id"] == st.session_state.selected_portfolio_id:
            selected_port_idx = idx + 1
            break

col_pick, col_link = st.columns([2.4, 1.0])
with col_pick:
    chosen_pos_idx = st.selectbox(
        "⚡ Quick Select from Saved Portfolio:",
        options=range(len(portfolio_labels)),
        format_func=lambda i: portfolio_labels[i],
        index=selected_port_idx,
        help="Select any saved stock position to instantly load ticker, active cash secured put details, or contracts.",
    )

with col_link:
    st.write("")
    st.page_link("app_pages/portfolio.py", label="Manage Portfolio Holdings →", icon=":material/account_balance_wallet:")

if chosen_pos_idx > 0:
    chosen_pos = positions[chosen_pos_idx - 1]
    if st.session_state.get("last_selected_port_csp_idx") != chosen_pos_idx:
        st.session_state.last_selected_port_csp_idx = chosen_pos_idx
        st.session_state.ticker = chosen_pos["ticker"]
        csp = chosen_pos.get("cash_secured_put", {})
        if csp and csp.get("active"):
            st.session_state.csp_target_exp = str(csp.get("expiration", ""))
            st.session_state.csp_target_open_date = str(csp.get("open_date", ""))
            st.session_state.csp_target_strike = float(csp.get("strike", 0.0))
            st.session_state.csp_target_premium = float(csp.get("premium", 0.0))
            st.session_state.csp_contracts = int(csp.get("contracts", 1))
        else:
            st.session_state.csp_target_exp = None
            st.session_state.csp_target_open_date = None
            st.session_state.csp_target_strike = None
            st.session_state.csp_target_premium = None
            st.session_state.csp_contracts = 1
else:
    if st.session_state.get("last_selected_port_csp_idx", 0) != 0:
        st.session_state.last_selected_port_csp_idx = 0

col_top1, col_top_open, col_top2, col_top3 = st.columns([1.3, 1.3, 1.6, 1.0])

with col_top1:
    ticker_val = st.text_input(
        "Underlying Ticker Symbol",
        value=st.session_state.ticker,
        max_chars=6,
    ).strip().upper()
    if ticker_val:
        st.session_state.ticker = ticker_val

ticker_input = st.session_state.ticker

with col_top_open:
    csp_open_def = date.today() - timedelta(days=7)
    if st.session_state.get("csp_target_open_date"):
        try:
            csp_open_def = datetime.strptime(st.session_state.csp_target_open_date, "%Y-%m-%d").date()
        except:
            pass
    csp_open_date = st.date_input(
        "Option Open Date",
        value=csp_open_def,
        max_value=date.today(),
        help="Date when you originally sold/opened this cash secured put.",
    )

if ticker_input:
    data, err = fetch_stock_forecast(ticker_input)
    if err:
        st.error(err)
    else:
        current_p = data["current_price"]

        brand_data = get_stock_branding_and_ratings(data["ticker"])
        st.html(render_ticker_brand_badge(data["ticker"], cur_p=current_p, brand_data=brand_data))

        with st.expander("📊 Wall Street Analyst Rating & Technical Intelligence", expanded=False):
            render_analyst_ratings_dashboard(data["ticker"], brand_data=brand_data)

        available_exps = fetch_option_expirations(data["ticker"])

        if not available_exps:
            st.warning(f"No options chain data available for ticker {data['ticker']}.")
            st.stop()

        # Find best default expiration (matches saved target or closest to 30-45 DTE)
        now_dt = datetime.now()
        exp_dtes = []
        for e in available_exps:
            try:
                days = (datetime.strptime(e, "%Y-%m-%d") - now_dt).days
                exp_dtes.append((e, max(days, 0)))
            except Exception:
                pass

        ideal_idx = 0
        target_exp = st.session_state.get("csp_target_exp")
        found_target_exp = False
        if target_exp:
            for i, (e, d) in enumerate(exp_dtes):
                if e == target_exp:
                    ideal_idx = i
                    found_target_exp = True
                    break

        if not found_target_exp:
            min_diff = 9999
            for i, (e, d) in enumerate(exp_dtes):
                diff = abs(d - 35)
                if diff < min_diff:
                    min_diff = diff
                    ideal_idx = i

        with col_top2:
            exp_labels = [f"{e} ({d} DTE)" for e, d in exp_dtes]
            selected_exp_idx = st.selectbox(
                "Expiration Date (DTE)",
                options=range(len(exp_dtes)),
                format_func=lambda i: exp_labels[i],
                index=ideal_idx,
            )
            selected_exp = exp_dtes[selected_exp_idx][0]
            selected_dte = exp_dtes[selected_exp_idx][1]

        with col_top3:
            contracts_qty = st.number_input(
                "Contracts", min_value=1, max_value=100, value=int(st.session_state.get("csp_contracts", 1)), step=1
            )
            st.session_state.csp_contracts = contracts_qty

        # Pull put chain for selected expiration
        puts_df = fetch_put_chain(data["ticker"], selected_exp)
        if puts_df.empty:
            st.error(f"No put options data found for expiration {selected_exp}.")
            st.stop()

        puts_filtered = puts_df[
            (puts_df["strike"] <= current_p * 1.02)
            & (puts_df["strike"] >= current_p * 0.65)
        ].copy()
        if puts_filtered.empty:
            puts_filtered = puts_df[puts_df["strike"] <= current_p].copy()

        computed_rows = []
        for _, row in puts_filtered.iterrows():
            k = float(row["strike"])
            bid = float(row["bid"]) if not pd.isna(row["bid"]) else 0.0
            ask = float(row["ask"]) if not pd.isna(row["ask"]) else 0.0
            last = float(row["lastPrice"]) if not pd.isna(row["lastPrice"]) else 0.0

            mid = (bid + ask) / 2.0 if (bid + ask) > 0 else last
            if mid <= 0:
                mid = last if last > 0 else 0.05

            iv = float(row["impliedVolatility"]) if not pd.isna(row["impliedVolatility"]) else 0.35
            delta, pop = calc_put_greeks(current_p, k, selected_dte, iv)

            roc = (mid / k) * 100.0
            apr = roc * (365.0 / max(selected_dte, 1))
            be = k - mid
            buffer_pct = ((current_p - be) / current_p) * 100.0

            computed_rows.append({
                "strike": k,
                "bid": bid,
                "ask": ask,
                "mid": round(mid, 2),
                "last": round(last, 2),
                "iv": iv,
                "delta": round(delta, 2),
                "pop": round(pop * 100.0, 1),
                "roc": round(roc, 2),
                "apr": round(apr, 1),
                "breakeven": round(be, 2),
                "buffer": round(buffer_pct, 1),
                "volume": int(row["volume"]) if not pd.isna(row["volume"]) else 0,
                "openInterest": int(row["openInterest"]) if not pd.isna(row["openInterest"]) else 0,
            })

        options_data_df = (
            pd.DataFrame(computed_rows)
            .sort_values("strike", ascending=False)
            .reset_index(drop=True)
        )

        # Strike selection: match target strike if saved in portfolio, otherwise target Delta ~ 0.25
        default_strike_idx = 0
        target_strike = st.session_state.get("csp_target_strike")
        found_strike = False
        if target_strike:
            for idx, row in options_data_df.iterrows():
                if abs(row["strike"] - target_strike) < 0.01:
                    default_strike_idx = idx
                    found_strike = True
                    break

        if not found_strike:
            min_delta_diff = 999.0
            for idx, row in options_data_df.iterrows():
                diff = abs(abs(row["delta"]) - 0.25)
                if diff < min_delta_diff:
                    min_delta_diff = diff
                    default_strike_idx = idx

        # Fetch WalletInvestor technical support levels to display context
        wi_data = fetch_walletinvestor_data(
            data["ticker"],
            current_p,
            data["high_target"],
            data["median_target"],
            data["low_target"],
        )
        trading = wi_data.get("trading", {})
        pivots = trading.get("pivots", {})

        # Sub-header bar for Stock Context
        st.html(f"""
        <div style="background:#f8fafc; border:1px solid #e2e8f0; border-radius:12px; padding:14px 20px; margin-top:14px; margin-bottom:20px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px;">
            <div>
                <span style="font-size:18px; font-weight:700; color:#1e293b;">{data['ticker']} &bull; {data['shortName']}</span>
                <span style="font-size:15px; color:#64748b; margin-left:12px;">Spot Price: <b style="color:#1e293b;">${current_p:.2f}</b></span>
            </div>
            <div style="font-size:14px; color:#64748b; display:flex; gap:18px;">
                <span>52W Range: <b>${data['fiftyTwoWeekLow']:.2f} - ${data['fiftyTwoWeekHigh']:.2f}</b></span>
                <span>Analyst Median: <b style="color:#00875A;">${data['median_target']:.2f}</b></span>
                <span>Pivot: <b>{pivots.get('Pivot', 'N/A')}</b> &bull; S1 Support: <b style="color:#489E60;">{pivots.get('S1', 'N/A')}</b></span>
            </div>
        </div>
        """)

        # ---------------------------------------------------------
        # STRIKE SELECTION & KPI CARDS
        # ---------------------------------------------------------
        col_strike, col_premium = st.columns([2, 1])
        with col_strike:
            strike_options = options_data_df["strike"].tolist()
            selected_strike = st.selectbox(
                "Choose Put Strike Price to Sell",
                options=strike_options,
                index=default_strike_idx,
                format_func=lambda s: f"${s:.2f} Strike (Delta {options_data_df[options_data_df['strike'] == s]['delta'].iloc[0]:.2f} | PoP {options_data_df[options_data_df['strike'] == s]['pop'].iloc[0]:.1f}%)",
            )

        chosen_option = options_data_df[options_data_df["strike"] == selected_strike].iloc[0]

        prem_val = float(chosen_option["mid"])
        if st.session_state.get("csp_target_premium") and found_strike:
            prem_val = float(st.session_state.csp_target_premium)

        with col_premium:
            user_premium = st.number_input(
                "Option Premium per Share ($)",
                min_value=0.01,
                max_value=float(selected_strike),
                value=prem_val,
                step=0.05,
                format="%.2f",
            )

        exp_date_obj = datetime.strptime(selected_exp, "%Y-%m-%d").date()
        days_option_open = max((date.today() - csp_open_date).days, 0)
        total_option_duration = max((exp_date_obj - csp_open_date).days, 1)

        # Action bar with timeline & save button
        col_act_left, col_act_right = st.columns([2.6, 1.2])
        with col_act_left:
            st.html(f"""
            <div style="font-size:13px; color:#475569; padding:8px 0;">
                ⏱️ <b>Option Timeline</b>: Sold on <b>{csp_open_date.strftime('%b %d, %Y')}</b> ({days_option_open}d ago) &bull;
                Expiry: <b>{selected_exp}</b> ({selected_dte}d DTE remaining) &bull;
                Total Duration: <b>{total_option_duration} days</b>
            </div>
            """)
        with col_act_right:
            if st.button("💾 Save CSP to Portfolio", type="primary", help=f"Save active Cash Secured Put for {data['ticker']} into portfolio"):
                save_or_update_option_position(
                    data["ticker"],
                    "cash_secured_put",
                    {
                        "active": True,
                        "contracts": int(contracts_qty),
                        "open_date": str(csp_open_date),
                        "expiration": str(selected_exp),
                        "strike": float(selected_strike),
                        "premium": float(user_premium),
                    },
                )
                st.toast(f"Saved active Cash Secured Put for {data['ticker']} into Portfolio!", icon="💾")

        total_collateral = selected_strike * 100 * contracts_qty
        total_premium = user_premium * 100 * contracts_qty
        breakeven_price = selected_strike - user_premium
        cushion_pct = ((current_p - breakeven_price) / current_p) * 100.0
        roc_pct = (user_premium / selected_strike) * 100.0
        apr_pct = roc_pct * (365.0 / max(selected_dte, 1))
        pop_pct = chosen_option["pop"]

        # Render Core KPI Cards
        st.html(f"""
        <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:12px; margin-top:8px; margin-bottom:28px;">
            <div class="metric-card" style="border-top:3px solid #2E384C;">
                <div style="font-size:12px; font-weight:700; color:#64748b; text-transform:uppercase; letter-spacing:0.04em; margin-bottom:6px;">Cash Collateral</div>
                <div style="font-size:22px; font-weight:800; color:#1e293b;">${total_collateral:,.2f}</div>
                <div style="font-size:12px; color:#64748b; margin-top:4px;">100% Cash &bull; {contracts_qty} contract(s)</div>
            </div>
            <div class="metric-card" style="border-top:3px solid #00875A;">
                <div style="font-size:12px; font-weight:700; color:#00875A; text-transform:uppercase; letter-spacing:0.04em; margin-bottom:6px;">Total Premium</div>
                <div style="font-size:22px; font-weight:800; color:#00875A;">+${total_premium:,.2f}</div>
                <div style="font-size:12px; color:#00875A; font-weight:600; margin-top:4px;">Immediate Cash Inflow</div>
            </div>
            <div class="metric-card" style="border-top:3px solid #489E60;">
                <div style="font-size:12px; font-weight:700; color:#64748b; text-transform:uppercase; letter-spacing:0.04em; margin-bottom:6px;">Net Breakeven</div>
                <div style="font-size:22px; font-weight:800; color:#1e293b;">${breakeven_price:.2f}</div>
                <div style="font-size:12px; color:#489E60; font-weight:600; margin-top:4px;">{cushion_pct:+.1f}% Safety Buffer</div>
            </div>
            <div class="metric-card" style="border-top:3px solid #2E384C;">
                <div style="font-size:12px; font-weight:700; color:#64748b; text-transform:uppercase; letter-spacing:0.04em; margin-bottom:6px;">Return on Capital</div>
                <div style="font-size:22px; font-weight:800; color:#1e293b;">{roc_pct:.2f}%</div>
                <div style="font-size:12px; color:#2E384C; font-weight:600; margin-top:4px;">{apr_pct:.1f}% APR ({selected_dte}d)</div>
            </div>
            <div class="metric-card" style="border-top:3px solid #00875A;">
                <div style="font-size:12px; font-weight:700; color:#64748b; text-transform:uppercase; letter-spacing:0.04em; margin-bottom:6px;">Win Rate (PoP)</div>
                <div style="font-size:22px; font-weight:800; color:#00875A;">{pop_pct:.1f}%</div>
                <div style="font-size:12px; color:#64748b; margin-top:4px;">Delta: {chosen_option['delta']:.2f}</div>
            </div>
        </div>
        """)

        # ---------------------------------------------------------
        # INTERACTIVE P&L PAYOFF DIAGRAM & EXPIRATION SCENARIOS
        # ---------------------------------------------------------
        st.html("""
        <div style="display:flex; align-items:center; margin-top:10px; margin-bottom:14px;">
            <div style="width:4px; height:24px; background-color:#489e60; border-radius:2px; margin-right:12px;"></div>
            <h3 style="margin:0; font-size:23px; font-weight:700; color:#1e293b; letter-spacing:-0.02em;">Cash Secured Put Payoff at Expiration</h3>
        </div>
        """)

        col_chart, col_scenarios = st.columns([1.6, 1.0], gap="large")

        with col_chart:
            price_min = max(selected_strike * 0.70, current_p * 0.65)
            price_max = max(selected_strike * 1.25, current_p * 1.25)
            stock_prices = np.linspace(price_min, price_max, 250)

            pnl_values = np.array([
                (user_premium - max(0.0, selected_strike - p)) * 100.0 * contracts_qty
                for p in stock_prices
            ])

            fig_pnl = go.Figure()

            # Zero reference line
            fig_pnl.add_hline(y=0, line_dash="solid", line_color="#cbd5e1", line_width=1.5)

            # Green Profit Area
            fig_pnl.add_trace(
                go.Scatter(
                    x=stock_prices,
                    y=np.maximum(0, pnl_values),
                    mode="lines",
                    name="Profit Zone",
                    line=dict(color="#00875A", width=0),
                    fill="tozeroy",
                    fillcolor="rgba(0, 135, 90, 0.12)",
                    hoverinfo="none",
                )
            )

            # Red Loss Area
            fig_pnl.add_trace(
                go.Scatter(
                    x=stock_prices,
                    y=np.minimum(0, pnl_values),
                    mode="lines",
                    name="Assignment Loss Zone",
                    line=dict(color="#DE350B", width=0),
                    fill="tozeroy",
                    fillcolor="rgba(222, 53, 11, 0.10)",
                    hoverinfo="none",
                )
            )

            # P&L Payoff Line
            fig_pnl.add_trace(
                go.Scatter(
                    x=stock_prices,
                    y=pnl_values,
                    mode="lines",
                    name="P&L at Expiration",
                    line=dict(color="#172B4D", width=3),
                    hovertemplate="Stock: $%{x:.2f}<br>P&L: $%{y:,.2f}<extra></extra>",
                )
            )

            # Vertical Line: Strike Price
            fig_pnl.add_vline(
                x=selected_strike,
                line_width=1.5,
                line_dash="dot",
                line_color="#00875A",
                annotation_text=f"Strike ${selected_strike:.2f}",
                annotation_position="top left",
                annotation_font=dict(color="#00875A", size=11),
            )

            # Vertical Line: Breakeven
            fig_pnl.add_vline(
                x=breakeven_price,
                line_width=1.5,
                line_dash="dot",
                line_color="#E5443B",
                annotation_text=f"Breakeven ${breakeven_price:.2f}",
                annotation_position="bottom right",
                annotation_font=dict(color="#E5443B", size=11),
            )

            # Vertical Line: Current Stock Price
            fig_pnl.add_vline(
                x=current_p,
                line_width=1.5,
                line_dash="dash",
                line_color="#475569",
                annotation_text=f"Current ${current_p:.2f}",
                annotation_position="top right",
                annotation_font=dict(color="#475569", size=11),
            )

            # Markers
            fig_pnl.add_trace(
                go.Scatter(
                    x=[breakeven_price],
                    y=[0],
                    mode="markers",
                    marker=dict(size=8, color="#E5443B", line=dict(color="#ffffff", width=2)),
                    showlegend=False,
                    hoverinfo="none",
                )
            )

            fig_pnl.add_trace(
                go.Scatter(
                    x=[selected_strike],
                    y=[total_premium],
                    mode="markers",
                    marker=dict(size=8, color="#00875A", line=dict(color="#ffffff", width=2)),
                    showlegend=False,
                    hoverinfo="none",
                )
            )

            fig_pnl.update_layout(
                showlegend=False,
                height=380,
                margin=dict(l=20, r=20, t=30, b=30),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                xaxis=dict(
                    title="Underlying Stock Price at Expiration ($)",
                    showgrid=True,
                    gridcolor="#f1f5f9",
                    zeroline=False,
                    tickprefix="$",
                    tickformat=".1f",
                ),
                yaxis=dict(
                    title="Total Profit / Loss ($)",
                    showgrid=True,
                    gridcolor="#f1f5f9",
                    zeroline=False,
                    tickprefix="$",
                    tickformat=",.0f",
                ),
            )

            st.plotly_chart(fig_pnl, width="stretch")

        with col_scenarios:
            scenarios_html = f"""
<div style="display:flex; flex-direction:column; gap:12px; height:100%; justify-content:center;">
    <!-- Scenario 1: Best Case -->
    <div style="background:#ffffff; border:1px solid #e2e8f0; border-top:3px solid #00875A; border-radius:12px; padding:16px; box-shadow:0 1px 3px rgba(0,0,0,0.04);">
        <div style="display:flex; justify-content:space-between; align-items:center;">
            <span style="font-size:12px; font-weight:700; color:#00875A; text-transform:uppercase;">Best Case &bull; Stock &ge; ${selected_strike:.2f}</span>
            <span style="font-size:12px; font-weight:600; color:#00875A;">Max Profit</span>
        </div>
        <div style="font-size:20px; font-weight:700; color:#1e293b; margin:6px 0 2px 0;">+${total_premium:,.2f}</div>
        <p style="font-size:13px; color:#64748b; margin:0; line-height:1.4;">Put expires completely worthless. You keep 100% of the premium and the full <b>${total_collateral:,.2f}</b> collateral is immediately freed.</p>
    </div>

    <!-- Scenario 2: Moderate Pullback -->
    <div style="background:#ffffff; border:1px solid #e2e8f0; border-top:3px solid #2E384C; border-radius:12px; padding:16px; box-shadow:0 1px 3px rgba(0,0,0,0.04);">
        <div style="display:flex; justify-content:space-between; align-items:center;">
            <span style="font-size:12px; font-weight:700; color:#2E384C; text-transform:uppercase;">Discount Buy &bull; ${breakeven_price:.2f} &lt; Stock &lt; ${selected_strike:.2f}</span>
            <span style="font-size:12px; font-weight:600; color:#2E384C;">Net Positive</span>
        </div>
        <div style="font-size:20px; font-weight:700; color:#1e293b; margin:6px 0 2px 0;">Own shares at ${breakeven_price:.2f} basis</div>
        <p style="font-size:13px; color:#64748b; margin:0; line-height:1.4;">You are assigned <b>{contracts_qty * 100} shares</b> at ${selected_strike:.2f}. Because you collected premium, your effective basis is lower than today's price by <b>{cushion_pct:.1f}%</b>.</p>
    </div>

    <!-- Scenario 3: Heavy Drop -->
    <div style="background:#ffffff; border:1px solid #e2e8f0; border-top:3px solid #E5443B; border-radius:12px; padding:16px; box-shadow:0 1px 3px rgba(0,0,0,0.04);">
        <div style="display:flex; justify-content:space-between; align-items:center;">
            <span style="font-size:12px; font-weight:700; color:#E5443B; text-transform:uppercase;">Downside Risk &bull; Stock &lt; ${breakeven_price:.2f}</span>
            <span style="font-size:12px; font-weight:600; color:#E5443B;">Unrealized Loss</span>
        </div>
        <div style="font-size:20px; font-weight:700; color:#1e293b; margin:6px 0 2px 0;">Wheel &bull; Sell Covered Calls</div>
        <p style="font-size:13px; color:#64748b; margin:0; line-height:1.4;">You own {contracts_qty * 100} shares with unrealized loss. As part of the classic Options Wheel strategy, you can begin writing Covered Calls against these shares.</p>
    </div>
</div>
"""
            st.html(scenarios_html)

        # ---------------------------------------------------------
        # CANDIDATE PUT OPTIONS SCREENER
        # ---------------------------------------------------------
        st.html("""
        <div style="display:flex; align-items:center; margin-top:36px; margin-bottom:14px;">
            <div style="width:4px; height:24px; background-color:#489e60; border-radius:2px; margin-right:12px;"></div>
            <h3 style="margin:0; font-size:23px; font-weight:700; color:#1e293b; letter-spacing:-0.02em;">Candidate Put Options Screener</h3>
        </div>
        """)

        screener_display = options_data_df.copy()
        screener_display["Delta Category"] = screener_display["delta"].apply(
            lambda d: "🟢 Conservative (< 0.20)"
            if abs(d) < 0.20
            else (
                "🟡 Balanced (0.20 - 0.30)"
                if abs(d) <= 0.30
                else "🔴 Aggressive (> 0.30)"
            )
        )
        screener_display["Strike ($)"] = screener_display["strike"].map("${:,.2f}".format)
        screener_display["Bid / Ask"] = screener_display.apply(
            lambda r: f"${r['bid']:.2f} / ${r['ask']:.2f}", axis=1
        )
        screener_display["Mid Premium ($)"] = screener_display["mid"].map("${:.2f}".format)
        screener_display["Collateral ($)"] = screener_display["strike"].apply(
            lambda k: f"${k * 100:,.0f}"
        )
        screener_display["Income ($)"] = screener_display["mid"].apply(
            lambda m: f"${m * 100:,.2f}"
        )
        screener_display["PoP (%)"] = screener_display["pop"].map("{:.1f}%".format)
        screener_display["ROC (%)"] = screener_display["roc"].map("{:.2f}%".format)
        screener_display["Annualized APR (%)"] = screener_display["apr"].map("{:.1f}%".format)
        screener_display["Breakeven ($)"] = screener_display["breakeven"].map("${:.2f}".format)
        screener_display["Safety Buffer (%)"] = screener_display["buffer"].map("{:+.1f}%".format)

        cols_to_show = [
            "Strike ($)",
            "Delta Category",
            "delta",
            "PoP (%)",
            "Bid / Ask",
            "Mid Premium ($)",
            "Collateral ($)",
            "Income ($)",
            "ROC (%)",
            "Annualized APR (%)",
            "Breakeven ($)",
            "Safety Buffer (%)",
            "openInterest",
        ]
        rename_map = {"delta": "Delta (Δ)", "openInterest": "Open Int"}

        st.dataframe(
            screener_display[cols_to_show].rename(columns=rename_map),
            width="stretch",
            hide_index=True,
        )

        # ---------------------------------------------------------
        # TECHNICAL SUPPORT & RESISTANCE CONTEXT
        # ---------------------------------------------------------
        target_14d = trading.get("target_14d", {})

        def parse_price(val_str):
            try:
                return float(re.sub(r"[^\d.]", "", str(val_str)))
            except Exception:
                return 0.0

        now_str = pivots.get("Now", f"${current_p:.2f}")
        now_val = parse_price(now_str)

        standard_order = [
            ("R3", pivots.get("R3", ""), "#e5443b", "std"),
            ("R2", pivots.get("R2", ""), "#e5443b", "std"),
            ("R1", pivots.get("R1", ""), "#e5443b", "std"),
            ("Pivot", pivots.get("Pivot", ""), "#2E384C", "std"),
            ("S1", pivots.get("S1", ""), "#489E60", "std"),
            ("S2", pivots.get("S2", ""), "#489E60", "std"),
            ("S3", pivots.get("S3", ""), "#489E60", "std"),
        ]

        ordered_levels = []
        now_inserted = False
        for label, p_str, col, kind in standard_order:
            val = parse_price(p_str)
            if not now_inserted and now_val >= val and val > 0:
                ordered_levels.append(("now", now_str, "#1e293b", "now"))
                now_inserted = True
            if p_str:
                ordered_levels.append((label, p_str, col, kind))

        if not now_inserted:
            ordered_levels.append(("now", now_str, "#1e293b", "now"))

        pivot_rows_html = []
        for label, p_str, col, kind in ordered_levels:
            if kind == "now":
                pivot_rows_html.append(
                    f'<div style="display:flex; align-items:center; justify-content:space-between; margin:11px 0;">'
                    f'<div style="display:flex; align-items:center; width:65px;">'
                    f'<span style="font-size:12px; font-weight:700; color:#1e293b; background:#eceff4; padding:2px 8px; border-radius:5px; letter-spacing:0.02em;">now</span>'
                    f'</div>'
                    f'<div style="flex:1; height:2px; background:#1e293b; margin:0 12px;"></div>'
                    f'<span style="font-size:16px; font-weight:800; color:#1e293b; font-variant-numeric:tabular-nums;">{p_str}</span>'
                    f'</div>'
                )
            else:
                pivot_rows_html.append(
                    f'<div style="display:flex; align-items:center; justify-content:space-between; margin:9px 0;">'
                    f'<div style="display:flex; align-items:center; width:65px;">'
                    f'<span style="font-size:14px; font-weight:700; color:{col}; width:38px;">{label}</span>'
                    f'<span style="width:8px; height:8px; border-radius:50%; background:{col}; display:inline-block;"></span>'
                    f'</div>'
                    f'<div style="flex:1; border-top:1px dashed #e2e8f0; margin:0 12px;"></div>'
                    f'<span style="font-size:14px; font-weight:500; color:#51607a; font-variant-numeric:tabular-nums;">{p_str}</span>'
                    f'</div>'
                )

        levels_rendered_html = "\n".join(pivot_rows_html)

        upside_val = target_14d.get("upside", "$0.00")
        upside_pct = target_14d.get("upside_pct", "+0.00%")
        now_target_val = target_14d.get("now", now_str)
        downside_val = target_14d.get("downside", "$0.00")
        downside_pct = target_14d.get("downside_pct", "+0.00%")

        trading_levels_html = f"""
<div style="background-color:#ebf4ef; border:1px solid #d9e9df; border-radius:20px; padding:26px; margin-top:36px; margin-bottom:32px;">
    <div style="display:flex; align-items:center; margin-bottom:18px;">
        <div style="width:4px; height:22px; background-color:#489e60; border-radius:2px; margin-right:12px;"></div>
        <h3 style="margin:0; font-size:23px; font-weight:700; color:#1e293b; letter-spacing:-0.02em;">Technical Trading Levels & Support Check</h3>
    </div>
    <div class="trading-grid">
        <!-- Left Card: 14-Day Target -->
        <div style="background:#ffffff; border:1px solid #e2e8f0; border-radius:16px; padding:24px; box-shadow:0 1px 4px rgba(0,0,0,0.04); display:flex; flex-direction:column; justify-content:space-between;">
            <div>
                <div style="font-size:12px; font-weight:700; text-transform:uppercase; letter-spacing:0.05em; color:#64748b; margin-bottom:3px;">14-day target</div>
                <p style="margin:0 0 24px 0; font-size:14px; color:#64748b; line-height:1.4;">Verify whether your chosen put strike (${selected_strike:.2f}) sits comfortably below the expected 14-day downside range.</p>
            </div>
            <div style="display:flex; align-items:center; gap:22px; padding:8px 0 16px 0;">
                <div style="width:36px; display:flex; flex-direction:column; align-items:center; flex-shrink:0;">
                    <div style="width:2.5px; height:38px; background:linear-gradient(#489E60, #cdd7e6); border-radius:1px;"></div>
                    <div style="width:14px; height:14px; border-radius:50%; background:#1e293b; border:3px solid #ffffff; box-shadow:0 0 0 1.5px #2e384c;"></div>
                    <div style="width:2.5px; height:38px; background:linear-gradient(#cdd7e6, #e5443b); border-radius:1px;"></div>
                </div>
                <div style="flex:1; display:flex; flex-direction:column; gap:8px;">
                    <div style="display:flex; justify-content:space-between; align-items:baseline;">
                        <div>
                            <div style="font-size:12px; color:#64748b; font-weight:600;">Upside</div>
                            <div style="font-size:19px; font-weight:700; color:#1e293b;">{upside_val}</div>
                        </div>
                        <div style="font-size:15px; font-weight:700; color:#489e60;">{upside_pct}</div>
                    </div>
                    <div style="border-top:1px dashed #e2e8f0; border-bottom:1px dashed #e2e8f0; padding:10px 0; display:flex; justify-content:space-between; align-items:baseline;">
                        <div>
                            <div style="font-size:12px; color:#64748b; font-weight:600;">Now</div>
                            <div style="font-size:19px; font-weight:700; color:#1e293b;">{now_target_val}</div>
                        </div>
                    </div>
                    <div style="display:flex; justify-content:space-between; align-items:baseline;">
                        <div>
                            <div style="font-size:12px; color:#64748b; font-weight:600;">Downside</div>
                            <div style="font-size:19px; font-weight:700; color:#1e293b;">{downside_val}</div>
                        </div>
                        <div style="font-size:15px; font-weight:700; color:#489e60;">{downside_pct}</div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Right Card: Pivot, Resistance & Support -->
        <div style="background:#ffffff; border:1px solid #e2e8f0; border-radius:16px; padding:24px; box-shadow:0 1px 4px rgba(0,0,0,0.04);">
            <div style="font-size:12px; font-weight:700; text-transform:uppercase; letter-spacing:0.05em; color:#64748b; margin-bottom:18px;">
                Pivot, resistance &amp; support <span style="font-size:13px; color:#94a3b8; font-weight:normal;" title="Calculated price levels traders watch.">ⓘ</span>
            </div>
            <div>
                {levels_rendered_html}
            </div>
        </div>
    </div>
</div>
"""
        st.html(trading_levels_html)
