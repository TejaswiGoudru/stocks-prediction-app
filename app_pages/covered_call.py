import re
from datetime import datetime
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from utils import (
    COMMON_CSS,
    calc_call_greeks,
    fetch_call_chain,
    fetch_option_expirations,
    fetch_stock_forecast,
    fetch_walletinvestor_data,
)

st.html(COMMON_CSS)

st.title("Covered Call (CC) Strategy")
st.markdown("Generate regular income from shares you own by writing call options at or above technical resistance levels.")

# Sync ticker with session_state across pages
if "ticker" not in st.session_state:
    st.session_state.ticker = "ORCL"

col_top1, col_top2, col_top3, col_top4 = st.columns([1.4, 1.6, 1.0, 1.2])

with col_top1:
    ticker_val = st.text_input(
        "Underlying Ticker Symbol",
        value=st.session_state.ticker,
        max_chars=6,
    ).strip().upper()
    if ticker_val:
        st.session_state.ticker = ticker_val

ticker_input = st.session_state.ticker

if ticker_input:
    data, err = fetch_stock_forecast(ticker_input)
    if err:
        st.error(err)
    else:
        current_p = data["current_price"]
        available_exps = fetch_option_expirations(data["ticker"])

        if not available_exps:
            st.warning(f"No options chain data available for ticker {data['ticker']}.")
            st.stop()

        # Find best default expiration (closest to 30-45 DTE - classic options income sweet spot)
        now_dt = datetime.now()
        exp_dtes = []
        for e in available_exps:
            try:
                days = (datetime.strptime(e, "%Y-%m-%d") - now_dt).days
                exp_dtes.append((e, max(days, 0)))
            except Exception:
                pass

        ideal_idx = 0
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
                "Contracts (100 shs)", min_value=1, max_value=100, value=1, step=1
            )

        with col_top4:
            stock_basis = st.number_input(
                "Cost Basis / Share ($)",
                min_value=0.01,
                value=float(round(current_p, 2)),
                step=0.50,
                format="%.2f",
                help="Your average stock purchase price. Defaults to current spot price.",
            )

        # Pull call chain for selected expiration
        calls_df = fetch_call_chain(data["ticker"], selected_exp)
        if calls_df.empty:
            st.error(f"No call options data found for expiration {selected_exp}.")
            st.stop()

        # Filter calls for strikes around ATM and OTM (from 95% of spot to 140% of spot)
        calls_filtered = calls_df[
            (calls_df["strike"] >= current_p * 0.95)
            & (calls_df["strike"] <= current_p * 1.40)
        ].copy()
        if calls_filtered.empty:
            calls_filtered = calls_df[calls_df["strike"] >= current_p].copy()
        if calls_filtered.empty:
            calls_filtered = calls_df.copy()

        computed_rows = []
        for _, row in calls_filtered.iterrows():
            k = float(row["strike"])
            bid = float(row["bid"]) if not pd.isna(row["bid"]) else 0.0
            ask = float(row["ask"]) if not pd.isna(row["ask"]) else 0.0
            last = float(row["lastPrice"]) if not pd.isna(row["lastPrice"]) else 0.0

            mid = (bid + ask) / 2.0 if (bid + ask) > 0 else last
            if mid <= 0:
                mid = last if last > 0 else 0.05

            iv = float(row["impliedVolatility"]) if not pd.isna(row["impliedVolatility"]) else 0.35
            delta, prob_otm = calc_call_greeks(current_p, k, selected_dte, iv)

            # Static return (if stock closes flat at cost basis)
            static_roc = (mid / stock_basis) * 100.0
            static_apr = static_roc * (365.0 / max(selected_dte, 1))

            # Max return (if called away at strike K)
            profit_if_called = (k - stock_basis) + mid
            called_roc = (profit_if_called / stock_basis) * 100.0
            called_apr = called_roc * (365.0 / max(selected_dte, 1))

            be = stock_basis - mid
            cushion_pct = (mid / stock_basis) * 100.0
            upside_to_strike = ((k - current_p) / current_p) * 100.0

            computed_rows.append({
                "strike": k,
                "bid": bid,
                "ask": ask,
                "mid": round(mid, 2),
                "last": round(last, 2),
                "iv": iv,
                "delta": round(delta, 2),
                "pop": round(prob_otm * 100.0, 1),
                "static_roc": round(static_roc, 2),
                "static_apr": round(static_apr, 1),
                "called_roc": round(called_roc, 2),
                "called_apr": round(called_apr, 1),
                "breakeven": round(be, 2),
                "cushion": round(cushion_pct, 1),
                "upside": round(upside_to_strike, 1),
                "volume": int(row["volume"]) if not pd.isna(row["volume"]) else 0,
                "openInterest": int(row["openInterest"]) if not pd.isna(row["openInterest"]) else 0,
            })

        options_data_df = (
            pd.DataFrame(computed_rows)
            .sort_values("strike", ascending=True)
            .reset_index(drop=True)
        )

        # Default strike selection: target Delta ~ 0.30 (classic ~70% OTM probability sweet spot)
        default_strike_idx = 0
        min_delta_diff = 999.0
        for idx, row in options_data_df.iterrows():
            diff = abs(row["delta"] - 0.30)
            if diff < min_delta_diff:
                min_delta_diff = diff
                default_strike_idx = idx

        # Fetch WalletInvestor technical trading levels to display context
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
                <span>Cost Basis: <b>${stock_basis:.2f}</b></span>
                <span>52W Range: <b>${data['fiftyTwoWeekLow']:.2f} - ${data['fiftyTwoWeekHigh']:.2f}</b></span>
                <span>Pivot: <b>{pivots.get('Pivot', 'N/A')}</b> &bull; R1 Resistance: <b style="color:#e5443b;">{pivots.get('R1', 'N/A')}</b></span>
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
                "Choose Call Strike Price to Sell",
                options=strike_options,
                index=default_strike_idx,
                format_func=lambda s: (
                    f"${s:.2f} Strike (Delta {options_data_df[options_data_df['strike'] == s]['delta'].iloc[0]:.2f} | "
                    f"Keep Shares {options_data_df[options_data_df['strike'] == s]['pop'].iloc[0]:.1f}% | "
                    f"+{options_data_df[options_data_df['strike'] == s]['upside'].iloc[0]:.1f}% Upside)"
                ),
            )

        chosen_option = options_data_df[options_data_df["strike"] == selected_strike].iloc[0]

        with col_premium:
            user_premium = st.number_input(
                "Option Premium per Share ($)",
                min_value=0.01,
                max_value=float(selected_strike),
                value=float(chosen_option["mid"]),
                step=0.05,
                format="%.2f",
            )

        total_shares = contracts_qty * 100
        total_stock_value = stock_basis * total_shares
        total_premium = user_premium * total_shares
        breakeven_price = stock_basis - user_premium
        cushion_pct = (user_premium / stock_basis) * 100.0

        # Returns
        profit_if_called = ((selected_strike - stock_basis) + user_premium) * total_shares
        called_roc_pct = (profit_if_called / total_stock_value) * 100.0
        called_apr_pct = called_roc_pct * (365.0 / max(selected_dte, 1))

        static_roc_pct = (user_premium / stock_basis) * 100.0
        static_apr_pct = static_roc_pct * (365.0 / max(selected_dte, 1))
        pop_pct = chosen_option["pop"]

        # Render Core KPI Cards
        st.html(f"""
        <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:12px; margin-top:8px; margin-bottom:28px;">
            <div class="metric-card" style="border-top:3px solid #2E384C;">
                <div style="font-size:12px; font-weight:700; color:#64748b; text-transform:uppercase; letter-spacing:0.04em; margin-bottom:6px;">Stock Position</div>
                <div style="font-size:22px; font-weight:800; color:#1e293b;">${total_stock_value:,.2f}</div>
                <div style="font-size:12px; color:#64748b; margin-top:4px;">{total_shares} shs @ ${stock_basis:.2f}</div>
            </div>
            <div class="metric-card" style="border-top:3px solid #00875A;">
                <div style="font-size:12px; font-weight:700; color:#00875A; text-transform:uppercase; letter-spacing:0.04em; margin-bottom:6px;">Premium Collected</div>
                <div style="font-size:22px; font-weight:800; color:#00875A;">+${total_premium:,.2f}</div>
                <div style="font-size:12px; color:#00875A; font-weight:600; margin-top:4px;">Immediate Cash Inflow</div>
            </div>
            <div class="metric-card" style="border-top:3px solid #489E60;">
                <div style="font-size:12px; font-weight:700; color:#64748b; text-transform:uppercase; letter-spacing:0.04em; margin-bottom:6px;">Net Breakeven</div>
                <div style="font-size:22px; font-weight:800; color:#1e293b;">${breakeven_price:.2f}</div>
                <div style="font-size:12px; color:#489E60; font-weight:600; margin-top:4px;">{cushion_pct:.1f}% Downside Buffer</div>
            </div>
            <div class="metric-card" style="border-top:3px solid #00875A;">
                <div style="font-size:12px; font-weight:700; color:#64748b; text-transform:uppercase; letter-spacing:0.04em; margin-bottom:6px;">Max Return (If Called)</div>
                <div style="font-size:22px; font-weight:800; color:#00875A;">{called_roc_pct:+.2f}%</div>
                <div style="font-size:12px; color:#00875A; font-weight:600; margin-top:4px;">{called_apr_pct:+.1f}% APR (+${profit_if_called:,.2f})</div>
            </div>
            <div class="metric-card" style="border-top:3px solid #2E384C;">
                <div style="font-size:12px; font-weight:700; color:#64748b; text-transform:uppercase; letter-spacing:0.04em; margin-bottom:6px;">Static Yield (Flat)</div>
                <div style="font-size:22px; font-weight:800; color:#1e293b;">{static_roc_pct:.2f}%</div>
                <div style="font-size:12px; color:#2E384C; font-weight:600; margin-top:4px;">{static_apr_pct:.1f}% APR ({selected_dte}d)</div>
            </div>
            <div class="metric-card" style="border-top:3px solid #489E60;">
                <div style="font-size:12px; font-weight:700; color:#64748b; text-transform:uppercase; letter-spacing:0.04em; margin-bottom:6px;">Keep Shares Prob</div>
                <div style="font-size:22px; font-weight:800; color:#1e293b;">{pop_pct:.1f}%</div>
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
            <h3 style="margin:0; font-size:23px; font-weight:700; color:#1e293b; letter-spacing:-0.02em;">Covered Call Payoff at Expiration</h3>
        </div>
        """)

        col_chart, col_scenarios = st.columns([1.6, 1.0], gap="large")

        with col_chart:
            price_min = max(stock_basis * 0.65, breakeven_price * 0.70)
            price_max = max(selected_strike * 1.30, current_p * 1.30)
            stock_prices = np.linspace(price_min, price_max, 250)

            # Covered Call Payoff = (Stock_Price - Cost_Basis) - max(0, Stock_Price - Strike) + Premium
            pnl_values = np.array([
                ((p - stock_basis) - max(0.0, p - selected_strike) + user_premium) * total_shares
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
                    name="Downside Loss Zone",
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
                    hovertemplate="Stock: $%{x:.2f}<br>Net P&L: $%{y:,.2f}<extra></extra>",
                )
            )

            # Vertical Line: Strike Price (Profit Cap)
            fig_pnl.add_vline(
                x=selected_strike,
                line_width=1.5,
                line_dash="dot",
                line_color="#00875A",
                annotation_text=f"Strike ${selected_strike:.2f} (Cap)",
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

            # Markers: Breakeven point & Max Profit point
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
                    y=[profit_if_called],
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
    <!-- Scenario 1: Best Case / Called Away -->
    <div style="background:#ffffff; border:1px solid #e2e8f0; border-top:3px solid #00875A; border-radius:12px; padding:16px; box-shadow:0 1px 3px rgba(0,0,0,0.04);">
        <div style="display:flex; justify-content:space-between; align-items:center;">
            <span style="font-size:12px; font-weight:700; color:#00875A; text-transform:uppercase;">Called Away &bull; Stock &ge; ${selected_strike:.2f}</span>
            <span style="font-size:12px; font-weight:600; color:#00875A;">Max Profit</span>
        </div>
        <div style="font-size:20px; font-weight:700; color:#1e293b; margin:6px 0 2px 0;">+${profit_if_called:,.2f} ({called_roc_pct:+.1f}%)</div>
        <p style="font-size:13px; color:#64748b; margin:0; line-height:1.4;">Shares are sold at ${selected_strike:.2f}. You capture 100% of the share appreciation up to the strike + keep the <b>${total_premium:,.2f}</b> premium.</p>
    </div>

    <!-- Scenario 2: Flat or Modest Rally (Shares Retained) -->
    <div style="background:#ffffff; border:1px solid #e2e8f0; border-top:3px solid #2E384C; border-radius:12px; padding:16px; box-shadow:0 1px 3px rgba(0,0,0,0.04);">
        <div style="display:flex; justify-content:space-between; align-items:center;">
            <span style="font-size:12px; font-weight:700; color:#2E384C; text-transform:uppercase;">Sweet Spot &bull; ${breakeven_price:.2f} &le; Stock &lt; ${selected_strike:.2f}</span>
            <span style="font-size:12px; font-weight:600; color:#2E384C;">Keep Shares &amp; Cash</span>
        </div>
        <div style="font-size:20px; font-weight:700; color:#1e293b; margin:6px 0 2px 0;">+${total_premium:,.2f} ({static_roc_pct:.1f}% Yield)</div>
        <p style="font-size:13px; color:#64748b; margin:0; line-height:1.4;">Call expires completely worthless. You retain 100% of your shares and keep all premium income to write another call next month.</p>
    </div>

    <!-- Scenario 3: Heavy Pullback -->
    <div style="background:#ffffff; border:1px solid #e2e8f0; border-top:3px solid #E5443B; border-radius:12px; padding:16px; box-shadow:0 1px 3px rgba(0,0,0,0.04);">
        <div style="display:flex; justify-content:space-between; align-items:center;">
            <span style="font-size:12px; font-weight:700; color:#E5443B; text-transform:uppercase;">Downside Drop &bull; Stock &lt; ${breakeven_price:.2f}</span>
            <span style="font-size:12px; font-weight:600; color:#E5443B;">Buffered Loss</span>
        </div>
        <div style="font-size:20px; font-weight:700; color:#1e293b; margin:6px 0 2px 0;">{cushion_pct:.1f}% Safety Cushion</div>
        <p style="font-size:13px; color:#64748b; margin:0; line-height:1.4;">Unrealized loss on stock, but buffered by the <b>${total_premium:,.2f}</b> cash collected. You outperform buy-and-hold by exactly the premium amount.</p>
    </div>
</div>
"""
            st.html(scenarios_html)

        # ---------------------------------------------------------
        # CANDIDATE CALL OPTIONS SCREENER
        # ---------------------------------------------------------
        st.html("""
        <div style="display:flex; align-items:center; margin-top:36px; margin-bottom:14px;">
            <div style="width:4px; height:24px; background-color:#489e60; border-radius:2px; margin-right:12px;"></div>
            <h3 style="margin:0; font-size:23px; font-weight:700; color:#1e293b; letter-spacing:-0.02em;">Candidate Call Options Screener</h3>
        </div>
        """)

        screener_display = options_data_df.copy()
        screener_display["Delta Category"] = screener_display["delta"].apply(
            lambda d: "🟢 Conservative (< 0.20)"
            if d < 0.20
            else (
                "🟡 Balanced (0.20 - 0.35)"
                if d <= 0.35
                else "🔴 Aggressive (> 0.35)"
            )
        )
        screener_display["Strike ($)"] = screener_display["strike"].map("${:,.2f}".format)
        screener_display["Bid / Ask"] = screener_display.apply(
            lambda r: f"${r['bid']:.2f} / ${r['ask']:.2f}", axis=1
        )
        screener_display["Mid Premium ($)"] = screener_display["mid"].map("${:.2f}".format)
        screener_display["Income ($)"] = screener_display["mid"].apply(
            lambda m: f"${m * 100:,.2f}"
        )
        screener_display["Keep Shares (%)"] = screener_display["pop"].map("{:.1f}%".format)
        screener_display["Static Yield (%)"] = screener_display["static_roc"].map("{:.2f}%".format)
        screener_display["If Called Return (%)"] = screener_display["called_roc"].map("{:+.2f}%".format)
        screener_display["Annualized Called (%)"] = screener_display["called_apr"].map("{:+.1f}%".format)
        screener_display["Breakeven ($)"] = screener_display["breakeven"].map("${:.2f}".format)
        screener_display["Downside Buffer (%)"] = screener_display["cushion"].map("{:.1f}%".format)
        screener_display["Upside to Strike (%)"] = screener_display["upside"].map("{:+.1f}%".format)

        cols_to_show = [
            "Strike ($)",
            "Delta Category",
            "delta",
            "Keep Shares (%)",
            "Upside to Strike (%)",
            "Bid / Ask",
            "Mid Premium ($)",
            "Income ($)",
            "Static Yield (%)",
            "If Called Return (%)",
            "Annualized Called (%)",
            "Breakeven ($)",
            "Downside Buffer (%)",
            "openInterest",
        ]
        rename_map = {"delta": "Delta (Δ)", "openInterest": "Open Int"}

        st.dataframe(
            screener_display[cols_to_show].rename(columns=rename_map),
            width="stretch",
            hide_index=True,
        )

        # ---------------------------------------------------------
        # TECHNICAL RESISTANCE & TRADING LEVELS CONTEXT
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
        <h3 style="margin:0; font-size:23px; font-weight:700; color:#1e293b; letter-spacing:-0.02em;">Technical Trading Levels &amp; Resistance Check</h3>
    </div>
    <div class="trading-grid">
        <!-- Left Card: 14-Day Target -->
        <div style="background:#ffffff; border:1px solid #e2e8f0; border-radius:16px; padding:24px; box-shadow:0 1px 4px rgba(0,0,0,0.04); display:flex; flex-direction:column; justify-content:space-between;">
            <div>
                <div style="font-size:12px; font-weight:700; text-transform:uppercase; letter-spacing:0.05em; color:#64748b; margin-bottom:3px;">14-day target</div>
                <p style="margin:0 0 24px 0; font-size:14px; color:#64748b; line-height:1.4;">Verify whether your chosen call strike (${selected_strike:.2f}) sits safely at or above the expected 14-day upside ceiling ({upside_val}) to prevent getting called away prematurely.</p>
            </div>
            <div style="display:flex; align-items:center; gap:22px; padding:8px 0 16px 0;">
                <div style="width:36px; display:flex; flex-direction:column; align-items:center; flex-shrink:0;">
                    <div style="width:2.5px; height:38px; background:linear-gradient(#e5443b, #cdd7e6); border-radius:1px;"></div>
                    <div style="width:14px; height:14px; border-radius:50%; background:#1e293b; border:3px solid #ffffff; box-shadow:0 0 0 1.5px #2e384c;"></div>
                    <div style="width:2.5px; height:38px; background:linear-gradient(#cdd7e6, #489E60); border-radius:1px;"></div>
                </div>
                <div style="flex:1; display:flex; flex-direction:column; gap:8px;">
                    <div style="display:flex; justify-content:space-between; align-items:baseline;">
                        <div>
                            <div style="font-size:12px; color:#64748b; font-weight:600;">Upside</div>
                            <div style="font-size:19px; font-weight:700; color:#1e293b;">{upside_val}</div>
                        </div>
                        <div style="font-size:15px; font-weight:700; color:#e5443b;">{upside_pct}</div>
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
