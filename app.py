import streamlit as st

st.set_page_config(page_title="Stock & Options Dashboard", layout="wide")

# Initialize shared session state across pages
if "ticker" not in st.session_state:
    st.session_state.ticker = "ORCL"

# Modern Streamlit Multi-page navigation
page = st.navigation(
    [
        st.Page(
            "app_pages/portfolio.py",
            title="Portfolio Holdings",
            icon=":material/account_balance_wallet:",
        ),
        st.Page(
            "app_pages/stock_forecast.py",
            title="Stock Price Forecast",
            icon=":material/trending_up:",
            default=True,
        ),
        st.Page(
            "app_pages/covered_call.py",
            title="Covered Call (CC)",
            icon=":material/monetization_on:",
        ),
        st.Page(
            "app_pages/cash_secured_put.py",
            title="Cash Secured Put (CSP)",
            icon=":material/savings:",
        ),
    ],
    position="sidebar",
)

page.run()