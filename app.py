import os
import json
import base64
import requests
import pandas as pd
import streamlit as st

# --- PAGE STYLING LAYOUT ---
st.set_page_config(page_title="NSE Monitor Hub", page_icon="⚡", layout="wide")

# Inject Custom Professional UI CSS
st.markdown("""
    <style>
    .main .block-container { padding-top: 2rem; }

    /* Metric numbers */
    div[data-testid="stMetricValue"] { font-size: 24px; color: #2DD4BF; font-weight: bold; }

    /* Top status banner */
    .status-badge {
        background-color: #0E1117;
        padding: 12px 16px;
        border-radius: 8px;
        border-left: 5px solid #2DD4BF;
        margin-bottom: 20px;
        color: #E5E7EB;
    }

    /* Section card wrapper */
    .section-card {
        background-color: #12161C;
        padding: 20px 24px;
        border-radius: 10px;
        border: 1px solid #232833;
        margin-bottom: 18px;
    }
    .section-title {
        font-size: 15px;
        font-weight: 600;
        color: #9CA3AF;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        margin-bottom: 10px;
    }

    /* Tracked stock chips (replaces default red multiselect tags) */
    .chip-row { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 6px; }
    .stock-chip {
        display: inline-flex;
        align-items: center;
        background-color: #1E293B;
        color: #7DD3D8;
        border: 1px solid #2DD4BF55;
        padding: 6px 12px;
        border-radius: 20px;
        font-size: 14px;
        font-weight: 500;
    }

    /* Override any native multiselect tag color to match palette (used in removal widget) */
    span[data-baseweb="tag"] {
        background-color: #1E293B !important;
        border: 1px solid #2DD4BF55 !important;
    }

    /* Primary button styling -> teal instead of default red */
    button[kind="primary"] {
        background-color: #2DD4BF !important;
        border-color: #2DD4BF !important;
        color: #0E1117 !important;
        font-weight: 600 !important;
    }
    button[kind="primary"]:hover {
        background-color: #26B8A5 !important;
        border-color: #26B8A5 !important;
    }
    </style>
""", unsafe_allow_html=True)

st.title("⚡ NSE Corporate AI Monitor Engine")

# --- FETCH SECRETS FROM ENV ---
GH_PAT = os.environ.get("GH_PAT")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY")  # e.g. "Username/RepoName"

@st.cache_data(ttl=86400)
def get_master_nse_tickers():
    """Fetches full valid master equities ticker list from exchange source records."""
    url = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            lines = r.content.decode('utf-8').splitlines()
            df = pd.read_csv(requests.compat.StringIO('\n'.join(lines)))
            df.columns = df.columns.str.strip()
            tickers = df['SYMBOL'].dropna().astype(str).str.strip().tolist()
            return sorted(list(set([t for t in tickers if t and t.upper() != "SYMBOL"])))
    except Exception:
        pass
    return ["SUZLON", "POWERGRID", "HCLTECH", "RELIANCE", "TCS", "SBIN", "HDFCBANK"]

NSE_TICKERS = get_master_nse_tickers()

# --- GITHUB FILE PERSISTENCE HANDLERS ---
def load_repo_config():
    url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/contents/watchlist.json"
    headers = {"Authorization": f"token {GH_PAT}", "Accept": "application/vnd.github.v3+json"}
    res = requests.get(url, headers=headers)
    if res.status_code == 200:
        data = res.json()
        content = base64.b64decode(data["content"]).decode("utf-8")
        return json.loads(content), data["sha"]
    return {}, None

def save_repo_config(new_config, sha):
    url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/contents/watchlist.json"
    headers = {"Authorization": f"token {GH_PAT}", "Accept": "application/vnd.github.v3+json"}

    payload = {
        "message": "Update tracking parameters from mobile dashboard interface",
        "content": base64.b64encode(json.dumps(new_config, indent=2).encode("utf-8")).decode("utf-8"),
        "sha": sha
    }
    res = requests.put(url, json=payload, headers=headers)
    return res.status_code in [200, 201]

# Load Current Persistent Records
try:
    current_config, file_sha = load_repo_config()
except Exception:
    st.error("Could not sync configurations via GitHub API. Check credentials.")
    current_config, file_sha = {}, None

# Session state holds the working copy of the watchlist so add/remove feels instant
if "watchlist" not in st.session_state:
    st.session_state.watchlist = list(current_config.get("selected_watchlist", []))

# --- HEADER TOP ZONE STATUS ---
st.markdown(
    '<div class="status-badge">🧬 <b>Engine Core Status:</b> 🟢 Background Relay Loop Active (10s Tracking)</div>',
    unsafe_allow_html=True
)

c1, c2, c3 = st.columns(3)
c1.metric("Selected Tracker Mode", current_config.get("tracking_mode", "All Stocks (Default)"))
c2.metric("Tracked Stock Count", len(st.session_state.watchlist))
c3.metric("High-Value Rules Count", len(current_config.get("high_value_keywords", [])))

st.markdown("---")

# --- CONTROL TABS CENTER ---
tab1, tab2 = st.tabs(["🎯 Watchlist Customization Control", "📋 Operational Metadata Logs"])

with tab1:
    st.subheader("Watchlist Processing Configuration")

    mode_selection = st.radio(
        "Select Active Pipeline Filter Mode:",
        options=["All Stocks (Default)", "Filtered Custom Watchlist"],
        index=0 if current_config.get("tracking_mode") == "All Stocks (Default)" else 1
    )

    # --- SECTION 1: SEARCH & ADD (separate from the tracked-stock display) ---
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">Search & Add Equity Symbols</div>', unsafe_allow_html=True)

    search_col, add_col = st.columns([4, 1])
    search_query = search_col.text_input(
        "Search NSE ticker symbols",
        placeholder="Type a symbol, e.g. RELIANCE, TCS, SUZLON...",
        label_visibility="collapsed"
    )

    filtered_options = [t for t in NSE_TICKERS if search_query.upper() in t] if search_query else []
    picked_symbol = None
    if filtered_options:
        picked_symbol = st.selectbox(
            "Matching symbols",
            options=filtered_options[:25],
            label_visibility="collapsed"
        )

    if add_col.button("➕ Add", type="primary", use_container_width=True):
        if picked_symbol and picked_symbol not in st.session_state.watchlist:
            st.session_state.watchlist.append(picked_symbol)
            st.rerun()
        elif not picked_symbol:
            st.warning("Search and select a symbol first.")
        else:
            st.info(f"{picked_symbol} is already tracked.")

    st.markdown('</div>', unsafe_allow_html=True)

    # --- SECTION 2: CURRENTLY TRACKED STOCKS (visually separate section) ---
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">Currently Tracked Stocks</div>', unsafe_allow_html=True)

    if not st.session_state.watchlist:
        st.caption("No stocks added yet. Use the search box above to add symbols.")
    else:
        # Render each tracked stock as its own row with a remove button,
        # grouped visually in rows of 4.
        cols_per_row = 4
        for i in range(0, len(st.session_state.watchlist), cols_per_row):
            row_symbols = st.session_state.watchlist[i:i + cols_per_row]
            row_cols = st.columns(cols_per_row)
            for j, symbol in enumerate(row_symbols):
                with row_cols[j]:
                    chip_col, remove_col = st.columns([3, 1])
                    chip_col.markdown(
                        f'<div class="stock-chip">{symbol}</div>',
                        unsafe_allow_html=True
                    )
                    if remove_col.button("✕", key=f"remove_{symbol}"):
                        st.session_state.watchlist.remove(symbol)
                        st.rerun()

    st.markdown('</div>', unsafe_allow_html=True)

    # --- KEYWORD FILTERS ---
    st.subheader("Keyword Filter Definitions")
    col_left, col_right = st.columns(2)

    h_value_str = col_left.text_area(
        "High-Value Trigger Keywords (Comma Separated):",
        value=", ".join(current_config.get("high_value_keywords", ["FINANCIAL RESULTS", "DIVIDEND"]))
    )

    routine_str = col_right.text_area(
        "Routine Filter Keywords (Comma Separated):",
        value=", ".join(current_config.get("routine_keywords", ["SHAREHOLDING PATTERN"]))
    )

    if st.button("💾 Apply Changes & Sync Engine", type="primary"):
        updated_payload = {
            "tracking_mode": mode_selection,
            "selected_watchlist": st.session_state.watchlist,
            "high_value_keywords": [k.strip().upper() for k in h_value_str.split(",") if k.strip()],
            "routine_keywords": [k.strip().upper() for k in routine_str.split(",") if k.strip()]
        }

        with st.spinner("Pushing updates directly to remote file engine storage..."):
            success = save_repo_config(updated_payload, file_sha)
            if success:
                st.success("Watchlist synced successfully! Active loop will update within 10 seconds.")
                st.rerun()
            else:
                st.error("Write conflict encountered. Please reload page and retry.")

with tab2:
    st.subheader("System Health Metadata")
    st.info("System health logs and alert histories are streamed directly to your designated Telegram channel.")
