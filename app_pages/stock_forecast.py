import re
import numpy as np
import plotly.graph_objects as go
import streamlit as st
from dateutil.relativedelta import relativedelta
from utils import (
    COMMON_CSS,
    fetch_stock_forecast,
    fetch_walletinvestor_data,
    get_stock_branding_and_ratings,
    render_analyst_ratings_dashboard,
    render_ticker_brand_badge,
)

st.html(COMMON_CSS)

st.title("Stock Price Forecast")
col_input, _ = st.columns([2, 5])

# Sync ticker with session_state across pages
if "ticker" not in st.session_state:
    st.session_state.ticker = "ORCL"

with col_input:
    ticker_val = st.text_input(
        "Enter Ticker Symbol",
        value=st.session_state.ticker,
        max_chars=6,
    ).strip().upper()
    if ticker_val:
        st.session_state.ticker = ticker_val

ticker_input = st.session_state.ticker

if ticker_input:
    brand_data = get_stock_branding_and_ratings(ticker_input)
    st.html(render_ticker_brand_badge(ticker_input, brand_data=brand_data))

    # Render Wall Street Analyst Rating, Technical Summary & Market Performance
    render_analyst_ratings_dashboard(ticker_input, brand_data=brand_data)

    data, err = fetch_stock_forecast(ticker_input)
    if err:
        st.error(err)
    else:
        st.markdown(f"### **Interactive Chart & Scenario Forecasts**")
        col_left, col_right = st.columns([1, 2], gap="large")

        # ---------------------------------------------------------
        # LEFT PANEL: Analyst Ratings Donut Chart
        # ---------------------------------------------------------
        with col_left:
            st.markdown(f"#### **{data['total_ratings']} analyst ratings**")

            labels = ["Buy", "Hold", "Sell"]
            values = [data["buy"], data["hold"], data["sell"]]
            colors = ["#00875A", "#B0B3B8", "#DE350B"]

            tot = sum(values)
            p_buy = round((values[0] / tot) * 100) if tot else 0
            p_hold = round((values[1] / tot) * 100) if tot else 0
            p_sell = round((values[2] / tot) * 100) if tot else 0

            st.html(
                f"<div style='display:flex; justify-content:space-around; font-size:14px; font-weight:600; margin-bottom: 12px;'>"
                f"<span><span style='color:#00875A;'>●</span> Buy {p_buy}%</span>"
                f"<span><span style='color:#B0B3B8;'>●</span> Hold {p_hold}%</span>"
                f"<span><span style='color:#DE350B;'>●</span> Sell {p_sell}%</span>"
                f"</div>"
            )

            fig_donut = go.Figure(
                data=[
                    go.Pie(
                        labels=labels,
                        values=values,
                        hole=0.68,
                        marker=dict(colors=colors),
                        textinfo="none",
                        direction="clockwise",
                        sort=False,
                    )
                ]
            )
            fig_donut.update_layout(
                showlegend=False,
                margin=dict(l=10, r=10, t=10, b=10),
                height=320,
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig_donut, width="stretch")

        # ---------------------------------------------------------
        # RIGHT PANEL: 1-Year Historical + 1-Year Forecast Fan Chart
        # ---------------------------------------------------------
        with col_right:
            st.markdown("#### **1-year stock price forecast**")

            hist_df = data["hist"]
            hist_dates = hist_df.index
            hist_prices = hist_df["Close"]

            last_date = hist_dates[-1]
            future_date = last_date + relativedelta(years=1)
            curr_p = data["current_price"]

            pct_high = ((data["high_target"] - curr_p) / curr_p) * 100
            pct_median = ((data["median_target"] - curr_p) / curr_p) * 100
            pct_low = ((data["low_target"] - curr_p) / curr_p) * 100

            fig_forecast = go.Figure()

            # 1. Past 12-month historical line
            fig_forecast.add_trace(
                go.Scatter(
                    x=hist_dates,
                    y=hist_prices,
                    mode="lines",
                    name="Past 12 Months",
                    line=dict(color="#DE350B", width=2.5),
                    hoverinfo="x+y",
                )
            )

            # 2. Dotted Forecast rays: High, Median, Low
            forecast_lines = [
                ("High", data["high_target"], "#00875A", pct_high),
                ("Median", data["median_target"], "#172B4D", pct_median),
                ("Low", data["low_target"], "#DE350B", pct_low),
            ]

            for name, target, color, pct in forecast_lines:
                fig_forecast.add_trace(
                    go.Scatter(
                        x=[last_date, future_date],
                        y=[curr_p, target],
                        mode="lines",
                        name=name,
                        line=dict(color=color, width=2.5, dash="dot"),
                        hoverinfo="none",
                    )
                )

            # 3. Present anchor circle dot
            fig_forecast.add_trace(
                go.Scatter(
                    x=[last_date],
                    y=[curr_p],
                    mode="markers",
                    marker=dict(
                        size=9,
                        color="#ffffff",
                        line=dict(color="#000000", width=3),
                    ),
                    showlegend=False,
                    hoverinfo="none",
                )
            )

            # 4. Vertical dividing dotted line
            fig_forecast.add_vline(
                x=last_date, line_width=1.5, line_dash="dot", line_color="#999999"
            )

            # 5. Annotation: Current Price box
            fig_forecast.add_annotation(
                x=last_date,
                y=curr_p,
                text=f"<b>Current Price</b><br>${curr_p:.2f}",
                showarrow=True,
                arrowhead=0,
                arrowsize=0.3,
                arrowwidth=1,
                ax=-65,
                ay=-25,
                bgcolor="#FFFFFF",
                bordercolor="#C1C7D0",
                borderwidth=1,
                borderpad=5,
                font=dict(size=12, color="#172B4D"),
            )

            # 6. Target Price text markers on the right edge
            for name, target, color, pct in forecast_lines:
                symbol = "▲" if pct >= 0 else "▼"
                fig_forecast.add_annotation(
                    x=future_date,
                    y=target,
                    text=(
                        f"<b>{name}</b><br>"
                        f"${target:.2f}<br>"
                        f"<span style='color:{color};'>{abs(pct):.2f}% {symbol}</span>"
                    ),
                    showarrow=False,
                    xanchor="left",
                    align="left",
                    font=dict(size=12, color="#172B4D"),
                )

            min_y = min(min(hist_prices), data["low_target"]) * 0.9
            max_y = max(max(hist_prices), data["high_target"]) * 1.08

            fig_forecast.update_layout(
                showlegend=False,
                height=450,
                margin=dict(l=20, r=110, t=20, b=40),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                xaxis=dict(
                    showgrid=False,
                    showline=False,
                    showticklabels=False,
                    range=[hist_dates[0], future_date + relativedelta(months=3)],
                ),
                yaxis=dict(
                    showgrid=False,
                    showline=False,
                    showticklabels=False,
                    range=[min_y, max_y],
                ),
                annotations=[
                    *fig_forecast.layout.annotations,
                    dict(
                        x=last_date,
                        y=min_y,
                        text="◀ past 12 months &nbsp;&nbsp;|&nbsp;&nbsp; next 12 months ▶",
                        showarrow=False,
                        yanchor="bottom",
                        font=dict(size=12, color="#42526E"),
                    ),
                ],
            )

            st.plotly_chart(fig_forecast, width="stretch")

        # ---------------------------------------------------------
        # FETCH WALLETINVESTOR EXTENSIONS (1-YEAR SCENARIOS & TRADING LEVELS)
        # ---------------------------------------------------------
        wi_data = fetch_walletinvestor_data(
            data["ticker"],
            data["current_price"],
            data["high_target"],
            data["median_target"],
            data["low_target"],
        )
        scenarios = wi_data.get("scenarios", [])
        trading = wi_data.get("trading", {})

        # ---------------------------------------------------------
        # SECTION 1: 1-Year Scenarios
        # ---------------------------------------------------------
        st.html("""
        <div style="display:flex; align-items:center; margin-top:36px; margin-bottom:16px;">
            <div style="width:4px; height:24px; background-color:#489e60; border-radius:2px; margin-right:12px;"></div>
            <h3 style="margin:0; font-size:23px; font-weight:700; color:#1e293b; letter-spacing:-0.02em;">1-year scenarios</h3>
        </div>
        """)

        if scenarios:
            cols = st.columns(len(scenarios), gap="medium")
            for i, sc in enumerate(scenarios):
                title_lower = sc["title"].lower()
                if "bear" in title_lower:
                    accent_color = "#e5443b"
                elif "base" in title_lower:
                    accent_color = "#2E384C"
                else:
                    accent_color = "#489E60"

                with cols[i]:
                    st.html(f"""
                    <div style="background:#ffffff; border:1px solid #e9eef6; border-top:3px solid {accent_color}; border-radius:16px; padding:18px 20px; box-shadow:0 1px 4px rgba(0,0,0,0.06);">
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                            <span style="color:{accent_color}; font-size:12px; font-weight:700; text-transform:uppercase; letter-spacing:0.04em;">{sc['title']}</span>
                            <span style="color:#64748b; font-size:12px; font-weight:500;">{sc['prob']}</span>
                        </div>
                        <div style="font-size:26px; font-weight:700; color:#1e293b; margin-bottom:3px; letter-spacing:-0.01em;">{sc['price']}</div>
                        <div style="font-size:13px; font-weight:600; color:{accent_color};">{sc['pct']}</div>
                        <div style="height:6px; background:#eef2f8; border-radius:3px; overflow:hidden; margin-top:14px;">
                            <div style="width:{min(max(sc['prob_num'], 5), 100)}%; height:100%; background:{accent_color}; border-radius:3px;"></div>
                        </div>
                    </div>
                    """)

        # ---------------------------------------------------------
        # SECTION 2: Trading Levels
        # ---------------------------------------------------------
        target_14d = trading.get("target_14d", {})
        pivots = trading.get("pivots", {})

        def parse_price(val_str):
            try:
                return float(re.sub(r"[^\d.]", "", str(val_str)))
            except Exception:
                return 0.0

        now_str = pivots.get("Now", f"${data['current_price']:.2f}")
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
        <h3 style="margin:0; font-size:23px; font-weight:700; color:#1e293b; letter-spacing:-0.02em;">Trading levels</h3>
    </div>
    <div class="trading-grid">
        <!-- Left Card: 14-Day Target -->
        <div style="background:#ffffff; border:1px solid #e2e8f0; border-radius:16px; padding:24px; box-shadow:0 1px 4px rgba(0,0,0,0.04); display:flex; flex-direction:column; justify-content:space-between;">
            <div>
                <div style="font-size:12px; font-weight:700; text-transform:uppercase; letter-spacing:0.05em; color:#64748b; margin-bottom:3px;">14-day target</div>
                <p style="margin:0 0 24px 0; font-size:14px; color:#64748b; line-height:1.4;">Highest and lowest price the model expects within two weeks.</p>
            </div>
            <div style="display:flex; align-items:center; gap:22px; padding:8px 0 16px 0;">
                <!-- Gauge slider -->
                <div style="width:36px; display:flex; flex-direction:column; align-items:center; flex-shrink:0;">
                    <div style="width:2.5px; height:38px; background:linear-gradient(#489E60, #cdd7e6); border-radius:1px;"></div>
                    <div style="width:14px; height:14px; border-radius:50%; background:#1e293b; border:3px solid #ffffff; box-shadow:0 0 0 1.5px #2e384c;"></div>
                    <div style="width:2.5px; height:38px; background:linear-gradient(#cdd7e6, #e5443b); border-radius:1px;"></div>
                </div>
                <!-- Levels readout -->
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
