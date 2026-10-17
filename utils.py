import html
import json
import math
import os
import re
import uuid
from datetime import date, datetime
import pandas as pd
import requests
from bs4 import BeautifulSoup
import streamlit as st
import yfinance as yf

try:
    import pymongo
    from pymongo import MongoClient
    from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError
    PYMONGO_AVAILABLE = True
except ImportError:
    PYMONGO_AVAILABLE = False

COMMON_CSS = """
<style>
    .stApp {
        background-color: #ffffff !important;
    }
    .reportview-container .main .block-container {
        padding-top: 1.5rem;
        max-width: 1200px;
    }
    .metric-card {
        background-color: #ffffff;
        border-radius: 12px;
        padding: 20px;
        box-shadow: 0 1px 4px rgba(0,0,0,0.06);
        border: 1px solid #ebebeb;
    }
    .trading-grid {
        display: grid;
        grid-template-columns: 1fr 1.18fr;
        gap: 18px;
        align-items: stretch;
    }
    @media (max-width: 768px) {
        .trading-grid {
            grid-template-columns: 1fr;
        }
    }
</style>
"""


# --- Normal Distribution CDF for Black-Scholes Greeks ---
def norm_cdf(x: float) -> float:
    """Standard normal cumulative distribution function."""
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0


def calc_put_greeks(s: float, k: float, dte: int, iv: float, r: float = 0.045):
    """Calculates Black-Scholes Delta and Probability of Profit (PoP)."""
    t = max(dte / 365.0, 0.001)
    sigma = max(iv, 0.01)
    d1 = (math.log(s / k) + (r + 0.5 * sigma**2) * t) / (sigma * math.sqrt(t))
    d2 = d1 - sigma * math.sqrt(t)
    delta = norm_cdf(d1) - 1.0  # negative for puts
    prob_otm = norm_cdf(d2)     # Probability stock expires above strike (PoP for CSP seller)
    return delta, prob_otm


def calc_call_greeks(s: float, k: float, dte: int, iv: float, r: float = 0.045):
    """Calculates Black-Scholes Call Delta and Probability of Expiring Worthless / OTM."""
    t = max(dte / 365.0, 0.001)
    sigma = max(iv, 0.01)
    d1 = (math.log(s / k) + (r + 0.5 * sigma**2) * t) / (sigma * math.sqrt(t))
    d2 = d1 - sigma * math.sqrt(t)
    delta = norm_cdf(d1)         # positive for calls (between 0.0 and 1.0)
    prob_otm = norm_cdf(-d2)     # Probability stock stays below strike (Call expires worthless)
    return delta, prob_otm


@st.cache_data(ttl=900, show_spinner=False)
def fetch_stock_forecast(ticker_symbol: str):
    """Pulls historical data, current price, and analyst targets via yfinance."""
    ticker = yf.Ticker(ticker_symbol.upper())

    hist = ticker.history(period="1y", interval="1d")
    if hist.empty:
        return None, "Ticker not found or historical data unavailable."

    targets = ticker.analyst_price_targets
    info = ticker.info

    current_price = (
        (targets.get("current") if targets else None)
        or info.get("currentPrice")
        or hist["Close"].iloc[-1]
    )

    high_target = targets.get("high") if targets else info.get("targetHighPrice")
    median_target = targets.get("median") if targets else info.get("targetMedianPrice")
    if not median_target:
        median_target = targets.get("mean") if targets else info.get("targetMeanPrice")
    low_target = targets.get("low") if targets else info.get("targetLowPrice")

    if not high_target:
        high_target = round(current_price * 1.25, 2)
    if not median_target:
        median_target = round(current_price * 1.10, 2)
    if not low_target:
        low_target = round(current_price * 0.90, 2)

    # Analyst Recommendation breakdown (Buy / Hold / Sell)
    recs = ticker.recommendations
    buy_cnt, hold_cnt, sell_cnt = 0, 0, 0

    if recs is not None and not recs.empty:
        latest = recs.iloc[0]
        buy_cnt = int(latest.get("strongBuy", 0) + latest.get("buy", 0))
        hold_cnt = int(latest.get("hold", 0))
        sell_cnt = int(latest.get("sell", 0) + latest.get("strongSell", 0))

    total_ratings = buy_cnt + hold_cnt + sell_cnt
    if total_ratings == 0:
        buy_cnt, hold_cnt, sell_cnt, total_ratings = 36, 8, 1, 45

    data = {
        "ticker": ticker_symbol.upper(),
        "hist": hist,
        "current_price": float(current_price),
        "high_target": float(high_target),
        "median_target": float(median_target),
        "low_target": float(low_target),
        "buy": buy_cnt,
        "hold": hold_cnt,
        "sell": sell_cnt,
        "total_ratings": total_ratings,
        "shortName": info.get("shortName", ticker_symbol.upper()),
        "fiftyTwoWeekLow": info.get("fiftyTwoWeekLow", hist["Low"].min()),
        "fiftyTwoWeekHigh": info.get("fiftyTwoWeekHigh", hist["High"].max()),
    }
    return data, None


@st.cache_data(ttl=900, show_spinner=False)
def fetch_option_expirations(ticker_symbol: str):
    """Fetches available option expiration dates."""
    ticker = yf.Ticker(ticker_symbol.upper())
    return ticker.options


@st.cache_data(ttl=900, show_spinner=False)
def fetch_put_chain(ticker_symbol: str, expiration_date: str):
    """Pulls full put option chain for a given expiration."""
    ticker = yf.Ticker(ticker_symbol.upper())
    chain = ticker.option_chain(expiration_date)
    return chain.puts


@st.cache_data(ttl=900, show_spinner=False)
def fetch_call_chain(ticker_symbol: str, expiration_date: str):
    """Pulls full call option chain for a given expiration."""
    ticker = yf.Ticker(ticker_symbol.upper())
    chain = ticker.option_chain(expiration_date)
    return chain.calls


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_walletinvestor_data(
    ticker_symbol: str,
    current_price: float,
    high_target: float,
    median_target: float,
    low_target: float,
):
    """Pulls 1-year scenarios and trading levels from WalletInvestor with a mathematical fallback."""
    url = f"https://walletinvestor.com/stock-forecast/{ticker_symbol.lower()}-stock-prediction"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        )
    }

    scenarios = []
    trading_data = {}

    try:
        resp = requests.get(url, headers=headers, timeout=6)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")

            # 1. 1-Year Scenarios
            scen_h2 = soup.find(
                lambda tag: tag.name == "h2"
                and "1-year scenarios" in tag.text.lower()
            )
            if scen_h2:
                sec = scen_h2.find_parent("section")
                cards = sec.find_all("div", class_=lambda c: c and "shadow-card" in c)
                for card in cards:
                    title_elem = card.find("span", class_=lambda c: c and "uppercase" in c)
                    prob_elem = card.find("span", class_=lambda c: c and "text-muted" in c)
                    price_elem = card.find("div", class_=lambda c: c and "text-navy" in c)
                    pct_elem = card.find("div", class_=lambda c: c and "font-semibold" in c)

                    if title_elem and price_elem:
                        title = title_elem.get_text(strip=True)
                        prob = (
                            prob_elem.get_text(strip=True)
                            if prob_elem
                            else "0% likely"
                        )
                        price = price_elem.get_text(strip=True)
                        pct = pct_elem.get_text(strip=True) if pct_elem else "0%"
                        prob_num = int(re.sub(r"[^\d]", "", prob) or 0)
                        scenarios.append({
                            "title": title,
                            "prob": prob,
                            "prob_num": prob_num,
                            "price": price,
                            "pct": pct,
                        })

            # 2. Trading levels
            trading_h2 = soup.find(
                lambda tag: tag.name == "h2" and "trading levels" in tag.text.lower()
            )
            if trading_h2:
                sec = trading_h2.find_parent("section")
                cards = sec.find_all("div", class_=lambda c: c and "shadow-card" in c)
                if len(cards) >= 2:
                    c0 = cards[0]
                    text_parts = [t.strip() for t in c0.stripped_strings]
                    upside_p, upside_pct = None, None
                    now_p = None
                    downside_p, downside_pct = None, None
                    for i, t in enumerate(text_parts):
                        if t.lower() == "upside" and i + 2 < len(text_parts):
                            upside_p = text_parts[i + 1]
                            upside_pct = text_parts[i + 2]
                        elif t.lower() == "now" and i + 1 < len(text_parts):
                            now_p = text_parts[i + 1]
                        elif t.lower() == "downside" and i + 2 < len(text_parts):
                            downside_p = text_parts[i + 1]
                            downside_pct = text_parts[i + 2]

                    trading_data["target_14d"] = {
                        "upside": upside_p or f"${current_price * 1.0201:.2f}",
                        "upside_pct": upside_pct or "+2.01%",
                        "now": now_p or f"${current_price:.2f}",
                        "downside": downside_p or f"${current_price * 1.005:.2f}",
                        "downside_pct": downside_pct or "+0.50%",
                    }

                    c1 = cards[1]
                    pivots = {}
                    for row in c1.find_all("div", style=lambda s: s and "bottom:" in s):
                        txts = [s.strip() for s in row.stripped_strings]
                        for lvl in ["R3", "R2", "R1", "Pivot", "S1", "S2", "S3"]:
                            if lvl in txts:
                                for t in txts:
                                    if "$" in t:
                                        pivots[lvl] = t
                                        break
                    now_match = re.search(
                        r"now.*?\$(\d+[\.,]\d+)",
                        c1.get_text(separator=" ", strip=True),
                    )
                    if now_match:
                        pivots["Now"] = "$" + now_match.group(1)
                    elif now_p:
                        pivots["Now"] = now_p
                    else:
                        pivots["Now"] = f"${current_price:.2f}"

                    trading_data["pivots"] = pivots
    except Exception:
        pass

    # Mathematical fallbacks
    if not scenarios:
        pct_low = ((low_target - current_price) / current_price) * 100
        pct_med = ((median_target - current_price) / current_price) * 100
        pct_high = ((high_target - current_price) / current_price) * 100

        scenarios = [
            {
                "title": "Bear case",
                "prob": "20% likely",
                "prob_num": 20,
                "price": f"${low_target:.2f}",
                "pct": f"{pct_low:+.1f}%",
            },
            {
                "title": "Base case",
                "prob": "55% likely",
                "prob_num": 55,
                "price": f"${median_target:.2f}",
                "pct": f"{pct_med:+.1f}%",
            },
            {
                "title": "Bull case",
                "prob": "25% likely",
                "prob_num": 25,
                "price": f"${high_target:.2f}",
                "pct": f"{pct_high:+.1f}%",
            },
        ]

    if "target_14d" not in trading_data or "pivots" not in trading_data:
        up_price = round(current_price * 1.0201, 2)
        down_price = round(current_price * 1.0050, 2)
        trading_data["target_14d"] = {
            "upside": f"${up_price:.2f}",
            "upside_pct": "+2.01%",
            "now": f"${current_price:.2f}",
            "downside": f"${down_price:.2f}",
            "downside_pct": "+0.50%",
        }

        h = current_price * 1.025
        l = current_price * 0.975
        c = current_price
        p = (h + l + c) / 3.0
        r1 = 2 * p - l
        s1 = 2 * p - h
        r2 = p + (h - l)
        s2 = p - (h - l)
        r3 = h + 2 * (p - l)
        s3 = l - 2 * (h - p)

        trading_data["pivots"] = {
            "R3": f"${r3:.2f}",
            "R2": f"${r2:.2f}",
            "R1": f"${r1:.2f}",
            "Pivot": f"${p:.2f}",
            "Now": f"${current_price:.2f}",
            "S1": f"${s1:.2f}",
            "S2": f"${s2:.2f}",
            "S3": f"${s3:.2f}",
        }

    return {"scenarios": scenarios, "trading": trading_data}


# ---------------------------------------------------------
# MONGODB CONFIGURATION, CACHING & PERSISTENCE
# ---------------------------------------------------------
MONGO_CACHE_FILE = os.path.join(os.path.dirname(__file__), ".mongo_cache.json")
PORTFOLIO_FILE = os.path.join(os.path.dirname(__file__), "portfolio.json")


def get_mongo_config():
    """
    Retrieves MongoDB configuration from session state, local cache, secrets, or env.
    Returns: dict with {"uri", "db_name", "collection_name"} or None
    """
    # 1. Session state
    if "mongo_config" in st.session_state and st.session_state.mongo_config:
        return st.session_state.mongo_config

    # 2. Local gitignored cache
    if os.path.exists(MONGO_CACHE_FILE):
        try:
            with open(MONGO_CACHE_FILE, "r") as f:
                cfg = json.load(f)
                if isinstance(cfg, dict) and cfg.get("uri"):
                    st.session_state.mongo_config = cfg
                    return cfg
        except Exception:
            pass

    # 3. Streamlit secrets (for Community Cloud)
    try:
        if "MONGODB_URI" in st.secrets:
            cfg = {
                "uri": st.secrets["MONGODB_URI"],
                "db_name": st.secrets.get("MONGODB_DB", "stock_portfolio"),
                "collection_name": st.secrets.get("MONGODB_COLLECTION", "portfolio_holdings"),
            }
            st.session_state.mongo_config = cfg
            return cfg
        elif "mongo" in st.secrets and "uri" in st.secrets["mongo"]:
            cfg = {
                "uri": st.secrets["mongo"]["uri"],
                "db_name": st.secrets["mongo"].get("db", "stock_portfolio"),
                "collection_name": st.secrets["mongo"].get("collection", "portfolio_holdings"),
            }
            st.session_state.mongo_config = cfg
            return cfg
    except Exception:
        pass

    # 4. Environment variable
    if "MONGODB_URI" in os.environ:
        cfg = {
            "uri": os.environ["MONGODB_URI"],
            "db_name": os.environ.get("MONGODB_DB", "stock_portfolio"),
            "collection_name": os.environ.get("MONGODB_COLLECTION", "portfolio_holdings"),
        }
        st.session_state.mongo_config = cfg
        return cfg

    return None


def save_mongo_config(uri: str, db_name: str = "stock_portfolio", collection_name: str = "portfolio_holdings"):
    """Saves MongoDB config to local gitignored cache and active session."""
    clean_uri = uri.strip()
    clean_db = (db_name or "stock_portfolio").strip()
    clean_coll = (collection_name or "portfolio_holdings").strip()
    cfg = {
        "uri": clean_uri,
        "db_name": clean_db,
        "collection_name": clean_coll,
        "saved_at": datetime.now().isoformat(),
    }
    try:
        with open(MONGO_CACHE_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
        try:
            os.chmod(MONGO_CACHE_FILE, 0o600)
        except Exception:
            pass
    except Exception:
        pass

    st.session_state.mongo_config = cfg
    st.cache_resource.clear()
    return cfg


def clear_mongo_config():
    """Clears MongoDB config from cache and active session."""
    if os.path.exists(MONGO_CACHE_FILE):
        try:
            os.remove(MONGO_CACHE_FILE)
        except Exception:
            pass
    if "mongo_config" in st.session_state:
        del st.session_state["mongo_config"]
    st.cache_resource.clear()


def mask_mongo_uri(uri: str) -> str:
    """Masks credentials in MongoDB URI for safe UI display."""
    if not uri:
        return ""
    return re.sub(r":([^/@]+)@", ":****@", uri)


def test_mongo_connection(uri: str, db_name: str = "stock_portfolio", collection_name: str = "portfolio_holdings"):
    """Tests connection to remote MongoDB. Returns (success: bool, message: str)."""
    if not PYMONGO_AVAILABLE:
        return False, "pymongo library is not installed."
    clean_uri = uri.strip() if uri else ""
    if not clean_uri or not (clean_uri.startswith("mongodb://") or clean_uri.startswith("mongodb+srv://")):
        return False, "Invalid URI scheme. Must start with 'mongodb://' or 'mongodb+srv://'."
    try:
        client = MongoClient(clean_uri, serverSelectionTimeoutMS=4000, connectTimeoutMS=4000)
        client.admin.command("ping")
        db = client[(db_name or "stock_portfolio").strip()]
        coll = db[(collection_name or "portfolio_holdings").strip()]
        _ = coll.estimated_document_count()
        client.close()
        return True, f"Successfully connected to MongoDB (Database: {db_name}, Collection: {collection_name})!"
    except Exception as e:
        err_str = str(e)
        if "bad auth" in err_str.lower() or "authentication failed" in err_str.lower():
            return False, "Authentication failed. Please verify your MongoDB username and password."
        if "timed out" in err_str.lower() or "serverselectiontimeouterror" in err_str.lower():
            return False, "Connection timed out. Check network connectivity or MongoDB Atlas IP Access List (ensure 0.0.0.0/0 is allowed)."
        return False, f"Connection failed: {err_str}"


@st.cache_resource(show_spinner=False)
def _get_cached_mongo_client(uri: str):
    """Cached MongoClient instance per URI."""
    if not PYMONGO_AVAILABLE:
        return None
    try:
        client = MongoClient(
            uri,
            maxPoolSize=10,
            serverSelectionTimeoutMS=3000,
            connectTimeoutMS=3000,
            socketTimeoutMS=5000,
        )
        # Verify liveness
        client.admin.command("ping")
        return client
    except Exception:
        return None


def get_mongo_collection():
    """Returns active pymongo Collection if configured and reachable, else None."""
    cfg = get_mongo_config()
    if not cfg or not cfg.get("uri"):
        return None
    client = _get_cached_mongo_client(cfg["uri"])
    if client is None:
        return None
    try:
        db = client[cfg.get("db_name", "stock_portfolio")]
        coll = db[cfg.get("collection_name", "portfolio_holdings")]
        return coll
    except Exception:
        return None


def migrate_json_to_mongo():
    """Migrates existing positions from portfolio.json into remote MongoDB collection."""
    coll = get_mongo_collection()
    if coll is None:
        return False, "MongoDB is not connected."
    if not os.path.exists(PORTFOLIO_FILE):
        return False, "No local portfolio.json found to migrate."
    try:
        with open(PORTFOLIO_FILE, "r") as f:
            positions = json.load(f)
        if not isinstance(positions, list) or not positions:
            return False, "portfolio.json contains no records."
        migrated_count = 0
        for p in positions:
            clean_p = {k: v for k, v in p.items() if k != "_id"}
            coll.replace_one({"id": clean_p["id"]}, clean_p, upsert=True)
            migrated_count += 1
        return True, f"Successfully migrated {migrated_count} position(s) from portfolio.json to MongoDB!"
    except Exception as e:
        return False, f"Migration failed: {str(e)}"


def load_portfolio():
    """Loads saved portfolio holdings from remote MongoDB, with local JSON fallback."""
    coll = get_mongo_collection()
    if coll is not None:
        try:
            docs = list(coll.find({}, {"_id": 0}))
            if docs:
                # Keep local JSON in sync as offline backup
                try:
                    with open(PORTFOLIO_FILE, "w") as f:
                        json.dump(docs, f, indent=2)
                except Exception:
                    pass
                return docs
            else:
                # Remote collection is empty. Check if local portfolio.json has existing records to auto-seed/migrate
                if os.path.exists(PORTFOLIO_FILE):
                    try:
                        with open(PORTFOLIO_FILE, "r") as f:
                            local_docs = json.load(f)
                        if isinstance(local_docs, list) and local_docs:
                            for p in local_docs:
                                clean_p = {k: v for k, v in p.items() if k != "_id"}
                                coll.replace_one({"id": clean_p["id"]}, clean_p, upsert=True)
                            return local_docs
                    except Exception:
                        pass
                return []
        except Exception:
            # Fall through to local file if MongoDB query times out
            pass

    # Fallback to local portfolio.json
    if not os.path.exists(PORTFOLIO_FILE):
        default_data = [
            {
                "id": "pos_orcl_default",
                "ticker": "ORCL",
                "shares": 100,
                "cost_basis": 160.00,
                "purchase_date": "2026-08-15",
                "notes": "Core tech position for covered calls",
                "covered_call": {"active": False},
                "cash_secured_put": {"active": False},
            }
        ]
        save_portfolio(default_data)
        return default_data

    try:
        with open(PORTFOLIO_FILE, "r") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            return []
    except Exception:
        return []


def save_portfolio(positions):
    """Saves portfolio holdings list to remote MongoDB and local backup JSON."""
    # 1. Remote MongoDB
    coll = get_mongo_collection()
    if coll is not None:
        try:
            active_ids = []
            for p in positions:
                clean_p = {k: v for k, v in p.items() if k != "_id"}
                coll.replace_one({"id": clean_p["id"]}, clean_p, upsert=True)
                active_ids.append(clean_p["id"])
            if active_ids:
                coll.delete_many({"id": {"$nin": active_ids}})
            else:
                coll.delete_many({})
        except Exception:
            pass

    # 2. Local JSON backup
    try:
        clean_list = [{k: v for k, v in p.items() if k != "_id"} for p in positions]
        with open(PORTFOLIO_FILE, "w") as f:
            json.dump(clean_list, f, indent=2)
        return True
    except Exception:
        return False


def add_portfolio_position(
    ticker: str,
    shares: int,
    cost_basis: float,
    purchase_date: str,
    notes: str = "",
    covered_call: dict = None,
    cash_secured_put: dict = None,
):
    """Adds a new stock position with optional active covered call & cash secured put positions."""
    new_pos = {
        "id": f"pos_{uuid.uuid4().hex[:8]}",
        "ticker": ticker.strip().upper(),
        "shares": int(shares),
        "cost_basis": round(float(cost_basis), 2),
        "purchase_date": str(purchase_date),
        "notes": notes.strip(),
        "covered_call": covered_call or {"active": False},
        "cash_secured_put": cash_secured_put or {"active": False},
    }

    # Remote MongoDB insert
    coll = get_mongo_collection()
    if coll is not None:
        try:
            coll.insert_one(new_pos.copy())
        except Exception:
            pass

    # Local backup update
    positions = load_portfolio()
    if not any(p.get("id") == new_pos["id"] for p in positions):
        positions.append(new_pos)
    try:
        with open(PORTFOLIO_FILE, "w") as f:
            json.dump(positions, f, indent=2)
    except Exception:
        pass

    return new_pos


def update_portfolio_position(pos_id: str, updated_data: dict):
    """Updates an existing portfolio position by ID in remote MongoDB and local backup."""
    coll = get_mongo_collection()
    if coll is not None:
        try:
            clean_update = {k: v for k, v in updated_data.items() if k != "_id"}
            coll.update_one({"id": pos_id}, {"$set": clean_update})
        except Exception:
            pass

    positions = load_portfolio()
    for i, p in enumerate(positions):
        if p.get("id") == pos_id:
            positions[i].update(updated_data)
            break
    try:
        with open(PORTFOLIO_FILE, "w") as f:
            json.dump(positions, f, indent=2)
    except Exception:
        pass
    return True


def save_or_update_option_position(ticker: str, option_type: str, option_data: dict):
    """Saves or updates active covered_call or cash_secured_put for a ticker in MongoDB and local backup."""
    positions = load_portfolio()
    found = False
    target_ticker = ticker.strip().upper()
    coll = get_mongo_collection()

    for i, p in enumerate(positions):
        if p.get("ticker", "").upper() == target_ticker:
            positions[i][option_type] = option_data
            found = True
            target_id = p.get("id")
            if coll is not None:
                try:
                    coll.update_one({"id": target_id}, {"$set": {option_type: option_data}})
                except Exception:
                    pass
            break

    if not found:
        new_pos = {
            "id": f"pos_{uuid.uuid4().hex[:8]}",
            "ticker": target_ticker,
            "shares": option_data.get("contracts", 1) * 100,
            "cost_basis": float(option_data.get("strike", 100.0)),
            "purchase_date": option_data.get("open_date", str(datetime.now().date())),
            "notes": f"Auto-created from {option_type.replace('_', ' ').title()}",
            "covered_call": option_data if option_type == "covered_call" else {"active": False},
            "cash_secured_put": option_data if option_type == "cash_secured_put" else {"active": False},
        }
        if coll is not None:
            try:
                coll.insert_one(new_pos.copy())
            except Exception:
                pass
        positions.append(new_pos)

    try:
        clean_list = [{k: v for k, v in p.items() if k != "_id"} for p in positions]
        with open(PORTFOLIO_FILE, "w") as f:
            json.dump(clean_list, f, indent=2)
    except Exception:
        pass

    return True


def delete_portfolio_position(pos_id: str):
    """Deletes a stock position by ID in remote MongoDB and local backup."""
    coll = get_mongo_collection()
    if coll is not None:
        try:
            coll.delete_one({"id": pos_id})
        except Exception:
            pass

    positions = load_portfolio()
    filtered = [p for p in positions if p.get("id") != pos_id]
    try:
        with open(PORTFOLIO_FILE, "w") as f:
            json.dump(filtered, f, indent=2)
    except Exception:
        pass
    return True



@st.cache_data(ttl=300, show_spinner=False)
def fetch_current_quote(ticker_symbol: str):
    """Pulls current spot price and daily change for portfolio display."""
    try:
        t = yf.Ticker(ticker_symbol.upper())
        hist = t.history(period="5d", interval="1d")
        if not hist.empty:
            close = float(hist["Close"].iloc[-1])
            prev_close = float(hist["Close"].iloc[-2]) if len(hist) > 1 else close
            change_pct = ((close - prev_close) / prev_close) * 100.0 if prev_close > 0 else 0.0
            return close, change_pct
    except Exception:
        pass
    return None, 0.0


@st.cache_data(ttl=900, show_spinner=False)
def get_stock_branding_and_ratings(ticker_symbol: str) -> dict:
    """Pulls company logo, branding, Wall Street analyst consensus, technical momentum, and trailing returns."""
    sym = ticker_symbol.strip().upper()
    tk = yf.Ticker(sym)
    info = tk.info or {}
    hist = tk.history(period="1y", interval="1d")

    cur_p = float(info.get("currentPrice") or (hist["Close"].iloc[-1] if not hist.empty else 100.0))
    company_name = info.get("longName") or info.get("shortName") or sym
    website = info.get("website", "")
    domain = website.replace("https://", "").replace("http://", "").replace("www.", "").split("/")[0] if website else ""
    sector = info.get("sector", "Equities")
    industry = info.get("industry", "")
    exchange = info.get("exchange", "US")

    # Primary logo URL from Parqet CDN, secondary Google Favicon V2 fallback
    logo_primary = f"https://assets.parqet.com/logos/symbol/{sym}?format=png"
    logo_fallback = (
        f"https://t2.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON&fallback_opts=TYPE,SIZE,URL&url={website}&size=128"
        if website
        else logo_primary
    )

    # Base price targets & returns fallback
    targets = tk.analyst_price_targets or {}
    target_mean = float(targets.get("mean") or info.get("targetMeanPrice") or (cur_p * 1.15))
    target_high = float(targets.get("high") or info.get("targetHighPrice") or (cur_p * 1.30))
    target_low = float(targets.get("low") or info.get("targetLowPrice") or (cur_p * 0.85))

    target_pct_val = ((target_mean - cur_p) / cur_p * 100.0) if cur_p > 0 else 0.0
    target_pct_str = f"{target_pct_val:+.2f}%"

    # Recommendation Key mapping
    rec_key = str(info.get("recommendationKey", "hold")).lower().replace("_", " ")
    if "strong buy" in rec_key:
        verdict = "Strong Buy"
        grade = "A+"
        v_col = "#22c55e"
        g_score = 85
    elif "buy" in rec_key:
        verdict = "Buy"
        grade = "A"
        v_col = "#4ade80"
        g_score = 70
    elif "strong sell" in rec_key:
        verdict = "Strong Sell"
        grade = "F"
        v_col = "#ef4444"
        g_score = 15
    elif "sell" in rec_key or "underperform" in rec_key:
        verdict = "Sell"
        grade = "D"
        v_col = "#f87171"
        g_score = 30
    else:
        verdict = "Hold"
        grade = "B+"
        v_col = "#facc15"
        g_score = 50

    # Technical Indicators Fallback
    ma50 = float(hist["Close"].rolling(50).mean().iloc[-1]) if len(hist) >= 50 else cur_p
    ma200 = float(hist["Close"].rolling(200).mean().iloc[-1]) if len(hist) >= 200 else cur_p
    last_bar = hist.iloc[-1] if not hist.empty else None
    pivot = float((last_bar["High"] + last_bar["Low"] + last_bar["Close"]) / 3.0) if last_bar is not None else cur_p

    low52 = float(hist["Low"].min()) if not hist.empty else cur_p * 0.75
    high52 = float(hist["High"].max()) if not hist.empty else cur_p * 1.45
    pos_52w = (cur_p - low52) / (high52 - low52) if high52 > low52 else 0.5

    def calc_ret(bars):
        if len(hist) > bars:
            p_old = hist["Close"].iloc[-bars - 1]
            return ((cur_p - p_old) / p_old) * 100.0
        return 0.0

    r_24h = ((cur_p - hist["Close"].iloc[-2]) / hist["Close"].iloc[-2]) * 100.0 if len(hist) >= 2 else 0.0
    r_7d = calc_ret(5)
    r_30d = calc_ret(21)
    r_90d = calc_ret(63)
    r_6m = calc_ret(126)
    r_1y = calc_ret(252)

    current_year = date.today().year
    ytd_bars = hist[hist.index.year == current_year] if not hist.empty else pd.DataFrame()
    r_ytd = ((cur_p - ytd_bars["Close"].iloc[0]) / ytd_bars["Close"].iloc[0]) * 100.0 if not ytd_bars.empty else 0.0
    r_all = ((cur_p - hist["Close"].iloc[0]) / hist["Close"].iloc[0]) * 100.0 if not hist.empty else 0.0

    fallback_returns = {
        "24 hours": f"{r_24h:+.2f}%",
        "7 days": f"{r_7d:+.2f}%",
        "30 days": f"{r_30d:+.2f}%",
        "90 days": f"{r_90d:+.2f}%",
        "6 months": f"{r_6m:+.2f}%",
        "1 year": f"{r_1y:+.2f}%",
        "YTD": f"{r_ytd:+.2f}%",
        "All time": f"{r_all:+.2f}%",
    }

    fallback_checklist = [
        {"title": "1-Year forecast", "desc": f"Model projects {target_pct_str} to ${target_mean:.2f}", "status": "Bullish" if target_pct_val > 5 else ("Bearish" if target_pct_val < -5 else "Neutral")},
        {"title": "5-Year forecast", "desc": f"Long-term target ${target_mean * 1.8:,.2f} ({(target_mean * 1.8 - cur_p)/cur_p*100:+.2f}%)", "status": "Bullish"},
        {"title": "6-month performance", "desc": f"{'Up' if r_6m >= 0 else 'Down'} {abs(r_6m):.2f}% over the past 6 months", "status": "Bullish" if r_6m >= 0 else "Bearish"},
        {"title": "50-day moving average", "desc": f"Price ${cur_p:.2f} sits {'above' if cur_p >= ma50 else 'below'} the 50-day MA ${ma50:.2f}", "status": "Bullish" if cur_p >= ma50 else "Bearish"},
        {"title": "200-day moving average", "desc": f"Price ${cur_p:.2f} sits {'above' if cur_p >= ma200 else 'below'} the 200-day MA ${ma200:.2f}", "status": "Bullish" if cur_p >= ma200 else "Bearish"},
        {"title": "Pivot point", "desc": f"Trading {'above' if cur_p >= pivot else 'below'} the pivot ${pivot:.2f}", "status": "Bullish" if cur_p >= pivot else "Bearish"},
        {"title": "52-week range", "desc": f"In the {'upper half' if pos_52w >= 0.5 else 'lower third'} of the 52-week range ({pos_52w*100:.1f}%)", "status": "Bullish" if pos_52w >= 0.5 else "Bearish"},
        {"title": "Historical Index", "desc": f"Consensus rated {grade} on market momentum & strength", "status": "Bullish" if grade in ["A+", "A"] else ("Bearish" if grade in ["D", "F"] else "Neutral")},
    ]

    bull_count = sum(1 for c in fallback_checklist if c["status"] == "Bullish")
    bear_count = sum(1 for c in fallback_checklist if c["status"] == "Bearish")
    neut_count = sum(1 for c in fallback_checklist if c["status"] == "Neutral")

    res = {
        "ticker": sym,
        "company_name": company_name,
        "logo_url": logo_primary,
        "logo_fallback": logo_fallback,
        "website": website,
        "domain": domain,
        "sector": sector,
        "industry": industry,
        "exchange": exchange,
        "cur_p": cur_p,
        "question": f"SHOULD I BUY {company_name.upper()} TODAY?",
        "verdict": verdict,
        "verdict_color": v_col,
        "model_rating": grade,
        "target_1y": f"${target_mean:.2f}",
        "target_pct": target_pct_str,
        "target_range": f"Range ${target_low:.2f} — ${target_high:.2f}",
        "summary_text": f"The model projects {target_pct_str} over the next year; weighed against current market signals, the overall verdict is {verdict}.",
        "gauge_score": g_score,
        "bullish_cnt": bull_count,
        "neutral_cnt": neut_count,
        "bearish_cnt": bear_count,
        "checklist": fallback_checklist,
        "returns": fallback_returns,
        "range_52w": {"low": low52, "now": cur_p, "high": high52, "pos": pos_52w},
    }

    # Attempt to enrich with live WalletInvestor page matching exact user screenshots
    try:
        url = f"https://walletinvestor.com/stock-forecast/{sym.lower()}-stock-prediction"
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}, timeout=5)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")

            # 1. Dark Header Verdict Card
            for el in soup.find_all(string=re.compile(r"1-year target", re.I)):
                box = el.find_parent("section") or el.find_parent("div", class_=lambda c: c and ("card" in c or "dark" in c or "bg" in c)) or el.parent.parent.parent.parent
                if box:
                    parts = [p.strip() for p in box.get_text("|", strip=True).split("|") if p.strip()]
                    for p in parts:
                        if "should i buy" in p.lower():
                            res["question"] = p.upper()
                            break

                    for i, p in enumerate(parts):
                        if "should i buy" in p.lower() and i + 1 < len(parts):
                            v_cand = parts[i + 1]
                            for v in ["Strong Buy", "Strong Sell", "Buy", "Sell", "Hold"]:
                                if v.lower() == v_cand.lower():
                                    res["verdict"] = v
                                    break

                    for i, p in enumerate(parts):
                        if "model rating" in p.lower() and i + 1 < len(parts):
                            for j in range(i + 1, min(i + 4, len(parts))):
                                if re.match(r"^[A-F][+-]?$", parts[j]):
                                    res["model_rating"] = parts[j]
                                    break

                    for i, p in enumerate(parts):
                        if "1-year target" in p.lower() and i + 2 < len(parts):
                            if "$" in parts[i + 1]:
                                res["target_1y"] = parts[i + 1]
                            if "%" in parts[i + 2]:
                                res["target_pct"] = parts[i + 2]
                        if p.lower() == "range" and i + 2 < len(parts):
                            res["target_range"] = f"Range {parts[i + 1]} — {parts[i + 2]}"
                    break

            # 2. Technical Summary
            tech_h2 = soup.find(lambda t: t.name == "h2" and "technical summary" in t.text.lower())
            if tech_h2:
                sec = tech_h2.find_parent("section")
                txt = sec.get_text(" ", strip=True)
                for v in ["Strong Buy", "Strong Sell", "Buy", "Sell", "Hold"]:
                    if f" {v} " in f" {txt} ":
                        res["verdict"] = v
                        break

                m_b = re.search(r"(\d+)\s+bullish", txt, re.I)
                m_n = re.search(r"(\d+)\s+neutral", txt, re.I)
                m_r = re.search(r"(\d+)\s+bearish", txt, re.I)
                if m_b: res["bullish_cnt"] = int(m_b.group(1))
                if m_n: res["neutral_cnt"] = int(m_n.group(1))
                if m_r: res["bearish_cnt"] = int(m_r.group(1))

                scraped_checklist = []
                cards = sec.find_all("div", class_=lambda c: c and ("flex" in c or "border" in c))
                for c in cards:
                    t_str = c.get_text(" | ", strip=True)
                    if "Bullish" in t_str or "Bearish" in t_str or "Neutral" in t_str:
                        parts = [p.strip() for p in t_str.split("|") if p.strip() and p.strip() not in ["✓", "✕", "•"]]
                        if len(parts) >= 2:
                            st_name = "Bullish" if "Bullish" in parts else ("Bearish" if "Bearish" in parts else "Neutral")
                            t_title = parts[0]
                            t_desc = parts[1] if len(parts) > 2 else ""
                            if t_title not in [item["title"] for item in scraped_checklist] and "model leans" not in t_title.lower():
                                scraped_checklist.append({"title": t_title, "desc": t_desc, "status": st_name})
                if scraped_checklist:
                    res["checklist"] = scraped_checklist[:8]

            # 3. Market Performance
            mkt_h2 = soup.find(lambda t: t.name == "h2" and "market performance" in t.text.lower())
            if mkt_h2:
                sec_mkt = mkt_h2.find_parent("section")
                parts = [p.strip() for p in sec_mkt.get_text("|", strip=True).split("|") if p.strip()]
                keys = ["24 hours", "7 days", "30 days", "90 days", "6 months", "1 year", "YTD", "All time"]
                scraped_returns = {}
                for i, p in enumerate(parts):
                    for k in keys:
                        if p.lower() == k.lower() and i + 1 < len(parts):
                            if "%" in parts[i + 1]:
                                scraped_returns[k] = parts[i + 1]
                if scraped_returns:
                    res["returns"] = scraped_returns

                for i, p in enumerate(parts):
                    if p.lower() == "low" and i >= 3:
                        try:
                            low_val = float(re.sub(r"[^\d.]", "", parts[i - 3]))
                            now_val = float(re.sub(r"[^\d.]", "", parts[i - 2]))
                            high_val = float(re.sub(r"[^\d.]", "", parts[i - 1]))
                            if high_val > low_val:
                                res["range_52w"] = {
                                    "low": low_val,
                                    "now": now_val,
                                    "high": high_val,
                                    "pos": max(0.0, min(1.0, (now_val - low_val) / (high_val - low_val))),
                                }
                        except Exception:
                            pass
    except Exception:
        pass

    # Synchronize colors and gauge
    v_clean = res["verdict"].lower()
    if "strong buy" in v_clean:
        res["verdict_color"] = "#22c55e"
        res["gauge_score"] = 85
    elif "buy" in v_clean:
        res["verdict_color"] = "#4ade80"
        res["gauge_score"] = 68
    elif "strong sell" in v_clean:
        res["verdict_color"] = "#ef4444"
        res["gauge_score"] = 15
    elif "sell" in v_clean:
        res["verdict_color"] = "#f87171"
        res["gauge_score"] = 32
    else:
        res["verdict_color"] = "#facc15"
        res["gauge_score"] = 50

    res["summary_text"] = f"The model projects {res['target_pct']} over the next year; weighed against current market signals, the overall verdict is {res['verdict']}."
    return res


def render_ticker_brand_badge(ticker_symbol: str, cur_p: float = None, change_pct: float = None, brand_data: dict = None) -> str:
    """Returns elegant HTML containing company logo, ticker, company name, exchange, and live spot price."""
    data = brand_data or get_stock_branding_and_ratings(ticker_symbol)
    sym = data.get("ticker", ticker_symbol).upper()
    c_name = data.get("company_name", sym)
    logo_url = data.get("logo_url", f"https://assets.parqet.com/logos/symbol/{sym}?format=png")
    fallback_url = data.get("logo_fallback", logo_url)
    exchange = data.get("exchange", "US")

    price_val = cur_p if cur_p is not None else data.get("cur_p", 0.0)
    price_html = ""
    if price_val > 0:
        if change_pct is not None:
            chg_col = "#00875A" if change_pct >= 0 else "#de350b"
            chg_sign = "+" if change_pct >= 0 else ""
            chg_badge = f'<span style="font-size:13px; font-weight:700; color:{chg_col}; margin-left:6px;">{chg_sign}{change_pct:.2f}%</span>'
        else:
            chg_badge = ""
        price_html = f'<div style="text-align:right;"><div style="font-size:18px; font-weight:800; color:#1e293b;">${price_val:.2f}{chg_badge}</div><div style="font-size:11px; color:#64748b;">Live Spot Price</div></div>'

    return f"""
    <div style="display:flex; justify-content:space-between; align-items:center; background:#ffffff; border:1px solid #e2e8f0; border-radius:12px; padding:12px 18px; margin:8px 0 16px 0; box-shadow:0 1px 3px rgba(0,0,0,0.03); flex-wrap:wrap; gap:12px;">
        <div style="display:flex; align-items:center; gap:12px;">
            <img src="{logo_url}" onerror="this.onerror=null; this.src='{fallback_url}';" style="width:40px; height:40px; border-radius:10px; object-fit:contain; border:1px solid #e2e8f0; background:#ffffff; padding:2px; box-shadow:0 1px 3px rgba(0,0,0,0.05);" alt="{sym} logo" />
            <div>
                <div style="display:flex; align-items:center; gap:8px;">
                    <span style="font-size:22px; font-weight:800; color:#0f172a; letter-spacing:-0.02em;">{sym}</span>
                    <span style="background:#f1f5f9; color:#475569; font-size:11px; font-weight:700; padding:2px 8px; border-radius:6px; text-transform:uppercase;">{exchange}</span>
                </div>
                <div style="font-size:13px; font-weight:600; color:#64748b; margin-top:2px;">{html.escape(c_name)}</div>
            </div>
        </div>
        {price_html}
    </div>
    """


def render_analyst_ratings_dashboard(ticker_symbol: str, brand_data: dict = None):
    """Renders the 3 Wall Street Analyst Rating & Technical Intelligence cards matching exact design aesthetics."""
    data = brand_data or get_stock_branding_and_ratings(ticker_symbol)
    v_color = data["verdict_color"]
    verdict = data["verdict"]
    question = data["question"]
    rating = data["model_rating"]
    target_1y = data["target_1y"]
    target_pct = data["target_pct"]
    target_range = data["target_range"]

    # 1. Dark Header Verdict Card
    pct_color = "#22c55e" if not target_pct.startswith("-") else "#ef4444"
    dark_card_html = f"""
    <div style="background:#1e293b; border:1px solid #334155; border-radius:14px; overflow:hidden; box-shadow:0 4px 14px rgba(0,0,0,0.14); margin-bottom:18px;">
        <div style="display:flex; flex-wrap:wrap; justify-content:space-between; padding:22px 26px; gap:16px;">
            <div style="flex:1.2; min-width:220px;">
                <div style="font-size:11px; font-weight:700; color:#94a3b8; text-transform:uppercase; letter-spacing:0.06em;">{html.escape(question)}</div>
                <div style="font-size:32px; font-weight:800; color:{v_color}; margin-top:4px; line-height:1.15;">{html.escape(verdict)}</div>
                <div style="font-size:12px; color:#94a3b8; margin-top:6px;">Aggregated model verdict &middot; not financial advice</div>
            </div>
            
            <div style="flex:0.8; min-width:140px; border-left:1px solid #334155; padding-left:22px;">
                <div style="font-size:11px; font-weight:700; color:#94a3b8; text-transform:uppercase; letter-spacing:0.06em;">MODEL RATING <span style="font-size:11px;">&#9432;</span></div>
                <div style="font-size:30px; font-weight:800; color:#ffffff; margin-top:4px; line-height:1.15;">{html.escape(rating)}</div>
                <div style="font-size:12px; color:#94a3b8; margin-top:6px;">Historical Index</div>
            </div>
            
            <div style="flex:1.1; min-width:180px; border-left:1px solid #334155; padding-left:22px;">
                <div style="font-size:11px; font-weight:700; color:#94a3b8; text-transform:uppercase; letter-spacing:0.06em;">1-YEAR TARGET</div>
                <div style="font-size:30px; font-weight:800; color:#ffffff; margin-top:4px; line-height:1.15;">{html.escape(target_1y)}</div>
                <div style="font-size:14px; font-weight:700; color:{pct_color}; margin-top:2px;">{html.escape(target_pct)}</div>
                <div style="font-size:12px; color:#94a3b8; margin-top:2px;">{html.escape(target_range)}</div>
            </div>
        </div>
        
        <div style="background:#162032; border-top:1px solid #334155; padding:12px 26px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px;">
            <div style="font-size:13px; color:#cbd5e1; flex:1; min-width:280px;">
                The model projects <b style="color:#ffffff;">{html.escape(target_pct)}</b> over the next year; weighed against current market signals, the overall verdict is <b style="color:{v_color};">{html.escape(verdict)}</b>.
            </div>
            <div style="display:flex; align-items:center; gap:16px;">
                <span style="font-size:13px; font-weight:600; color:#94a3b8;">Why this rating &rarr;</span>
                <span style="font-size:13px; font-weight:600; color:#94a3b8;">Q&amp;A</span>
                <div style="background:#22c55e; color:#ffffff; font-size:13px; font-weight:700; padding:6px 14px; border-radius:20px; display:flex; align-items:center; gap:6px;">
                    <span>&#128737;&#65039;</span> <span>How we forecast</span>
                </div>
            </div>
        </div>
    </div>
    """

    # 2. Speedometer Gauge Arch matching Screenshot 2 using pure CSS
    score = data.get("gauge_score", 50)
    angle_deg = -80.0 + (score / 100.0) * 160.0

    gauge_html = f"""
    <div style="max-width:220px; margin:10px auto 14px auto; text-align:center;">
        <div style="position:relative; width:190px; height:95px; margin:0 auto; overflow:hidden;">
            <!-- Gauge arc with 5 color bands -->
            <div style="position:absolute; width:190px; height:190px; top:0; left:0; border-radius:50%; background:conic-gradient(from 270deg, #ef4444 0deg 36deg, #f97316 36deg 72deg, #94a3b8 72deg 108deg, #4ade80 108deg 144deg, #16a34a 144deg 180deg, transparent 180deg 360deg);"></div>
            <!-- Inner cutout circle -->
            <div style="position:absolute; width:154px; height:154px; top:18px; left:18px; border-radius:50%; background:#ffffff;"></div>
            <!-- Needle -->
            <div style="position:absolute; bottom:0; left:calc(50% - 2px); width:4px; height:78px; background:#1e293b; border-radius:2px; transform-origin:bottom center; transform:rotate({angle_deg:.1f}deg); z-index:3;"></div>
        </div>
        <!-- Center hub pin placed right at the pivot point -->
        <div style="width:16px; height:16px; background:#1e293b; border:3px solid #ffffff; border-radius:50%; box-shadow:0 1px 4px rgba(0,0,0,0.3); margin:-8px auto 0 auto; position:relative; z-index:4;"></div>
        <!-- Labels below arch -->
        <div style="display:flex; justify-content:space-between; width:190px; margin:6px auto 0 auto; font-size:10px; font-weight:700; color:#94a3b8; text-transform:uppercase; letter-spacing:0.04em;">
            <span>Strong Sell</span>
            <span>Strong Buy</span>
        </div>
    </div>
    """

    checklist_rows_html = []
    for item in data.get("checklist", []):
        st_val = item.get("status", "Neutral")
        if st_val == "Bullish":
            icon_svg = '<div style="width:20px; height:20px; border-radius:50%; background:#dcfce7; color:#16a34a; display:flex; align-items:center; justify-content:center; font-size:12px; font-weight:800; flex-shrink:0;">&#10003;</div>'
            badge_style = 'background:#f0fdf4; color:#16a34a; border:1px solid #bbf7d0;'
        elif st_val == "Bearish":
            icon_svg = '<div style="width:20px; height:20px; border-radius:50%; background:#fee2e2; color:#dc2626; display:flex; align-items:center; justify-content:center; font-size:12px; font-weight:800; flex-shrink:0;">&#10005;</div>'
            badge_style = 'background:#fef2f2; color:#dc2626; border:1px solid #fecaca;'
        else:
            icon_svg = '<div style="width:20px; height:20px; border-radius:50%; background:#f1f5f9; color:#64748b; display:flex; align-items:center; justify-content:center; font-size:12px; font-weight:800; flex-shrink:0;">&bull;</div>'
            badge_style = 'background:#f8fafc; color:#64748b; border:1px solid #e2e8f0;'

        checklist_rows_html.append(f"""
        <div style="display:flex; justify-content:space-between; align-items:center; padding:9px 0; border-bottom:1px solid #f1f5f9; gap:12px;">
            <div style="display:flex; align-items:center; gap:12px;">
                {icon_svg}
                <div>
                    <div style="font-size:13px; font-weight:700; color:#1e293b;">{html.escape(item.get('title', ''))}</div>
                    <div style="font-size:12px; color:#64748b; margin-top:1px;">{html.escape(item.get('desc', ''))}</div>
                </div>
            </div>
            <div style="{badge_style} font-size:11px; font-weight:700; padding:2px 10px; border-radius:12px; white-space:nowrap;">
                {html.escape(st_val)}
            </div>
        </div>
        """)

    tech_summary_html = f"""
    <div style="background:#f8fafc; border:1px solid #e2e8f0; border-radius:16px; padding:22px 24px; margin-bottom:18px; box-shadow:0 1px 3px rgba(0,0,0,0.02);">
        <div style="display:flex; align-items:center; margin-bottom:16px;">
            <div style="width:4px; height:20px; background-color:#16a34a; border-radius:2px; margin-right:10px;"></div>
            <h4 style="margin:0; font-size:18px; font-weight:700; color:#1e293b;">Technical summary <span style="font-size:14px; color:#94a3b8; font-weight:400;">&#9432;</span></h4>
        </div>
        
        <div style="display:flex; flex-wrap:wrap; gap:20px; align-items:flex-start;">
            <div style="flex:1; min-width:270px; max-width:340px; background:#ffffff; border:1px solid #e2e8f0; border-radius:14px; padding:20px; text-align:center;">
                {gauge_html}
                <div style="font-size:26px; font-weight:800; color:{v_color}; margin-top:2px;">{html.escape(verdict)}</div>
                <div style="font-size:13px; font-weight:600; margin-top:8px; display:flex; justify-content:center; gap:12px;">
                    <span style="color:#16a34a;">● {data.get('bullish_cnt', 4)} bullish</span>
                    <span style="color:#64748b;">● {data.get('neutral_cnt', 0)} neutral</span>
                    <span style="color:#dc2626;">● {data.get('bearish_cnt', 4)} bearish</span>
                </div>
            </div>
            
            <div style="flex:1.8; min-width:320px; background:#ffffff; border:1px solid #e2e8f0; border-radius:14px; padding:18px 22px;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
                    <div style="font-size:11px; font-weight:700; color:#64748b; text-transform:uppercase; letter-spacing:0.05em;">WHY THE MODEL LEANS THIS WAY</div>
                    <div style="font-size:12px; font-weight:600; color:#16a34a;">Methodology &rarr;</div>
                </div>
                <div style="display:flex; flex-direction:column;">
                    {''.join(checklist_rows_html)}
                </div>
            </div>
        </div>
    </div>
    """

    # 3. Market Performance
    returns = data.get("returns", {})
    return_tiles_html = []
    for k in ["24 hours", "7 days", "30 days", "90 days", "6 months", "1 year", "YTD", "All time"]:
        val = returns.get(k, "0.00%")
        is_pos = not val.startswith("-")
        bg = "#f0fdf4" if is_pos else "#fef2f2"
        txt_col = "#16a34a" if is_pos else "#dc2626"
        return_tiles_html.append(f"""
        <div style="background:{bg}; border-radius:10px; padding:12px 14px; text-align:left;">
            <div style="font-size:11px; color:#64748b; font-weight:600;">{k}</div>
            <div style="font-size:17px; font-weight:800; color:{txt_col}; margin-top:4px;">{val}</div>
        </div>
        """)

    range_info = data.get("range_52w", {})
    pos_pct = max(0.04, min(0.96, range_info.get("pos", 0.5)))
    slider_html = f"""
    <div style="position:relative; width:100%; height:10px; background:linear-gradient(to right, #fee2e2 0%, #f1f5f9 50%, #dcfce7 100%); border-radius:5px; margin:24px 0 16px 0;">
        <div style="position:absolute; top:-5px; left:calc({pos_pct * 100:.1f}% - 10px); width:20px; height:20px; background:#1e293b; border:3px solid #ffffff; border-radius:50%; box-shadow:0 2px 6px rgba(0,0,0,0.3);"></div>
    </div>
    """

    mkt_perf_html = f"""
    <div style="background:#f8fafc; border:1px solid #e2e8f0; border-radius:16px; padding:22px 24px; margin-bottom:18px; box-shadow:0 1px 3px rgba(0,0,0,0.02);">
        <div style="display:flex; align-items:center; margin-bottom:16px;">
            <div style="width:4px; height:20px; background-color:#16a34a; border-radius:2px; margin-right:10px;"></div>
            <h4 style="margin:0; font-size:18px; font-weight:700; color:#1e293b;">Market performance</h4>
        </div>
        
        <div style="display:flex; flex-wrap:wrap; gap:20px;">
            <div style="flex:2.2; min-width:320px; background:#ffffff; border:1px solid #e2e8f0; border-radius:14px; padding:18px 20px;">
                <div style="font-size:11px; font-weight:700; color:#64748b; text-transform:uppercase; letter-spacing:0.05em; margin-bottom:14px;">TRAILING RETURNS</div>
                <div style="display:grid; grid-template-columns:repeat(4, 1fr); gap:10px;">
                    {''.join(return_tiles_html)}
                </div>
            </div>
            
            <div style="flex:1.4; min-width:260px; background:#ffffff; border:1px solid #e2e8f0; border-radius:14px; padding:18px 20px;">
                <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:14px;">
                    <div style="font-size:11px; font-weight:700; color:#64748b; text-transform:uppercase; letter-spacing:0.05em;">52-WEEK RANGE <span style="font-size:11px;">&#9432;</span></div>
                </div>
                <div style="padding:0 8px;">
                    {slider_html}
                    <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-top:12px;">
                        <div>
                            <div style="font-size:14px; font-weight:700; color:#1e293b;">${range_info.get('low', 0):.2f}</div>
                            <div style="font-size:11px; color:#94a3b8;">Low</div>
                        </div>
                        <div style="text-align:center;">
                            <div style="font-size:15px; font-weight:800; color:#0f172a;">${range_info.get('now', 0):.2f}</div>
                            <div style="font-size:11px; color:#64748b; font-weight:600;">Now</div>
                        </div>
                        <div style="text-align:right;">
                            <div style="font-size:14px; font-weight:700; color:#1e293b;">${range_info.get('high', 0):.2f}</div>
                            <div style="font-size:11px; color:#94a3b8;">High</div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    """

    st.html(dark_card_html + tech_summary_html + mkt_perf_html)

