"""
Prediction Market Scanner v6
=============================
Triple-source: Kalshi + Polymarket + Manifold Markets
Searches all three platforms' public APIs for prediction markets by keyword.

Usage:
    pip install streamlit requests plotly pandas
    streamlit run prediction_scanner.py
"""

import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import time, re, json
from datetime import datetime

# ─── Config ───────────────────────────────────────────────────────────────────
KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
POLY_GAMMA = "https://gamma-api.polymarket.com"
POLY_CLOB = "https://clob.polymarket.com"
MANIFOLD_BASE = "https://api.manifold.markets"
SESSION = requests.Session()
SESSION.headers.update({"Accept": "application/json"})

st.set_page_config(page_title="Prediction Market Scanner", page_icon="📊",
                    layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
<style>
    .stApp{background-color:#0a0e17}
    [data-testid="stHeader"]{background-color:#0a0e17}
    .block-container{padding-top:1.2rem;max-width:1100px}
    h1,h2,h3,p,span,div,label{color:#e2e8f0 !important}
    .stTextInput>div>div>input{background-color:#0f172a;color:#f1f5f9;border:1px solid #1e293b;border-radius:6px}
    .stTextInput>div>div>input:focus{border-color:#3b82f6;box-shadow:0 0 0 1px #3b82f6}
    .stButton>button{background-color:#3b82f6;color:white;border:none;border-radius:6px;font-weight:600}
    .stButton>button:hover{background-color:#2563eb}
    .prob-badge{display:inline-block;padding:2px 8px;border-radius:4px;font-weight:700;font-family:monospace;font-size:13px}
    .src-badge{display:inline-block;padding:1px 6px;border-radius:3px;font-size:9px;font-weight:700;letter-spacing:0.5px}
    .stTabs [data-baseweb="tab-list"]{gap:4px}
    .stTabs [data-baseweb="tab"]{background-color:#0f172a;border:1px solid #1e293b;border-radius:6px;color:#94a3b8}
    .stTabs [aria-selected="true"]{background-color:#1e293b !important;color:#e2e8f0 !important;border-color:#3b82f6 !important}
    /* Selectbox: white background, black text */
    .stSelectbox [data-baseweb="select"]{background-color:white !important}
    .stSelectbox [data-baseweb="select"] *{color:#000 !important}
    .stSelectbox [data-baseweb="select"] svg{fill:#000 !important}
    .stSelectbox div[data-baseweb="select"]>div{background-color:white !important;color:#000 !important}
    /* Dropdown menu items */
    [data-baseweb="menu"] li{color:#000 !important}
    [data-baseweb="popover"]{background-color:white !important}
    [data-baseweb="popover"] li{color:#000 !important}
    [data-baseweb="popover"] li:hover{background-color:#e2e8f0 !important}
</style>
""", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# KALSHI
# ═══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=600, show_spinner=False)
def kalshi_fetch_events():
    out = []
    cursor = ""
    for _ in range(8):
        params = {"limit": 200, "status": "open", "with_nested_markets": "true"}
        if cursor: params["cursor"] = cursor
        try:
            r = SESSION.get(f"{KALSHI_BASE}/events", params=params, timeout=15)
            r.raise_for_status()
            d = r.json()
            out.extend(d.get("events", []))
            cursor = d.get("cursor", "")
            if not cursor: break
        except Exception as e:
            if not out: raise e
            break
    return out

@st.cache_data(ttl=600, show_spinner=False)
def kalshi_fetch_nonmve_markets():
    """Fetch non-combo markets directly. Catches earnings, economics, etc. missed by events pagination."""
    out = []
    cursor = ""
    for _ in range(6):
        params = {"limit": 1000, "status": "open", "mve_filter": "exclude"}
        if cursor: params["cursor"] = cursor
        try:
            r = SESSION.get(f"{KALSHI_BASE}/markets", params=params, timeout=15)
            r.raise_for_status()
            d = r.json()
            batch = d.get("markets", [])
            out.extend(batch)
            cursor = d.get("cursor", "")
            if not cursor or not batch: break
        except: break
    return out

@st.cache_data(ttl=600, show_spinner=False)
def kalshi_fetch_series_list():
    """Fetch all Kalshi series with their categories."""
    try:
        r = SESSION.get(f"{KALSHI_BASE}/series", timeout=15)
        r.raise_for_status()
        return r.json().get("series", [])
    except:
        return []

@st.cache_data(ttl=600, show_spinner=False)
def kalshi_fetch_by_category(category_names):
    """Fetch events filtered by category via series_ticker matching."""
    all_series = kalshi_fetch_series_list()
    cats = [c.strip() for c in category_names.split(",")]
    
    # Find series tickers matching the requested categories
    matching_tickers = [s["ticker"] for s in all_series if s.get("category","") in cats]
    
    # Fetch events for each matching series
    out = []
    seen = set()
    for ticker in matching_tickers:
        try:
            r = SESSION.get(f"{KALSHI_BASE}/events", params={
                "series_ticker": ticker, "status": "open",
                "with_nested_markets": "true", "limit": 200,
            }, timeout=10)
            if not r.ok: continue
            for ev in r.json().get("events", []):
                eid = ev.get("event_ticker","")
                if eid not in seen:
                    seen.add(eid)
                    out.append(ev)
        except: continue
    return out

def kalshi_search(query, events):
    kw = query.lower().strip().split()
    results = []
    for ev in events:
        et = ev.get("event_ticker","")
        st_tick = ev.get("series_ticker","")
        title = ev.get("title","")
        cat = ev.get("category","")
        blob = f"{title} {cat} {et} {ev.get('sub_title','')}".lower()
        # Also check market-level fields for keyword matches
        market_blobs = []
        for m in ev.get("markets", []):
            mb = f"{m.get('title','')} {m.get('subtitle','')} {m.get('yes_sub_title','')} {m.get('ticker','')}".lower()
            market_blobs.append(mb)
        combined_blob = blob + " " + " ".join(market_blobs)
        if not all(k in combined_blob for k in kw): continue
        for m in ev.get("markets", []):
            # Build a display title that distinguishes sub-markets
            m_title = m.get("title") or ""
            m_subtitle = m.get("subtitle") or m.get("yes_sub_title") or ""
            
            # If the market title is the same as the event title (multi-outcome event),
            # try to differentiate using subtitle, yes_sub_title, or ticker suffix
            display_title = m_title or title
            if m_subtitle and m_subtitle != m_title and m_subtitle != title:
                # Subtitle has the differentiating info (e.g. "Google", "OpenAI")
                display_title = f"{title}: {m_subtitle}"
            elif m_title == title or not m_title:
                # Try extracting from ticker (e.g. KXAIMODEL-26-GOOG -> GOOG)
                ticker_val = m.get("ticker","")
                parts = ticker_val.rsplit("-", 1)
                if len(parts) > 1 and len(parts[-1]) <= 10:
                    display_title = f"{title} — {parts[-1]}"
            
            results.append({
                "source": "Kalshi", "title": display_title,
                "question": display_title, "ticker": m.get("ticker",""),
                "event_ticker": m.get("event_ticker","") or et,
                "series_ticker": st_tick, "category": cat,
                "price": float(m.get("last_price_dollars") or m.get("yes_bid_dollars") or 0),
                "volume_24h": float(m.get("volume_24h_fp") or 0),
                "volume_total": float(m.get("volume_fp") or 0),
                "_clob_token": None,
            })
    results.sort(key=lambda x: x["volume_24h"], reverse=True)
    return results

@st.cache_data(ttl=300, show_spinner=False)
def kalshi_candles(series, ticker):
    end = int(time.time()); start = end - 90*86400
    for iv in ["1440","60"]:
        try:
            r = SESSION.get(f"{KALSHI_BASE}/series/{series}/markets/{ticker}/candlesticks",
                            params={"start_ts":start,"end_ts":end,"period_interval":iv}, timeout=10)
            if not r.ok: continue
            out = []
            for c in r.json().get("candlesticks",[]):
                p = c.get("price",{})
                cl = float(p.get("close_dollars") or p.get("close") or 0)
                if cl <= 0: continue
                out.append({"ts":c["end_period_ts"],
                    "datetime":datetime.fromtimestamp(c["end_period_ts"]),
                    "close":cl,
                    "high":float(p.get("high_dollars") or p.get("high") or cl),
                    "low":float(p.get("low_dollars") or p.get("low") or cl),
                    "volume":float(c.get("volume_fp") or c.get("volume") or 0)})
            out.sort(key=lambda x:x["ts"])
            if out: return out
        except: continue
    return []


# ═══════════════════════════════════════════════════════════════════════════════
# POLYMARKET
# ═══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=600, show_spinner=False)
def poly_fetch_markets():
    """Fetch active Polymarket markets. Three passes, graceful degradation on failures."""
    seen_ids = set()
    out = []
    
    def _add(batch):
        n = 0
        for m in batch:
            mid = m.get("conditionId") or m.get("id") or m.get("slug","")
            if mid and mid not in seen_ids:
                seen_ids.add(mid)
                out.append(m)
                n += 1
        return n
    
    for sort_field in ["volume24hr", "volumeNum", "liquidityNum"]:
        for offset in range(0, 800, 100):
            try:
                r = SESSION.get(f"{POLY_GAMMA}/markets", params={
                    "limit": 100, "offset": offset,
                    "order": sort_field, "ascending": "false",
                    "active": "true",
                }, timeout=15)
                if not r.ok: break
                batch = r.json()
                if not isinstance(batch, list) or not batch: break
                n = _add(batch)
                if n == 0 and offset > 200: break
            except: break
    
    return out

def poly_search(query, markets):
    kw = query.lower().strip().split()
    results = []
    seen = set()
    for m in markets:
        q = m.get("question") or ""
        slug = m.get("slug") or ""
        cat = m.get("category") or ""
        desc = m.get("description") or ""
        git = m.get("groupItemTitle") or ""
        blob = f"{q} {slug} {cat} {git} {desc}".lower()
        if not all(k in blob for k in kw): continue
        
        mid = m.get("conditionId") or m.get("id") or slug
        if mid in seen: continue
        seen.add(mid)
        
        # Parse outcomePrices
        price = 0
        op = m.get("outcomePrices")
        if op:
            try:
                prices = json.loads(op) if isinstance(op, str) else op
                price = float(prices[0]) if prices else 0
            except: pass
        if not price:
            price = float(m.get("lastTradePrice") or 0)
        if not price:
            price = float(m.get("bestBid") or 0)
        
        # Extract CLOB token ID
        token_id = None
        ctids = m.get("clobTokenIds")
        if ctids:
            try:
                ids = json.loads(ctids) if isinstance(ctids, str) else ctids
                token_id = ids[0] if ids else None
            except: pass
        
        # Event title from nested events
        event_title = ""
        evs = m.get("events")
        if evs and isinstance(evs, list) and len(evs) > 0 and isinstance(evs[0], dict):
            event_title = evs[0].get("title","")
        
        results.append({
            "source": "Polymarket",
            "title": git or q,
            "question": q,
            "ticker": slug,
            "event_ticker": event_title,
            "series_ticker": "",
            "category": cat,
            "price": price,
            "volume_24h": float(m.get("volume24hr") or 0),
            "volume_total": float(m.get("volumeNum") or m.get("volume") or 0),
            "_clob_token": token_id,
        })
    results.sort(key=lambda x: x["volume_24h"], reverse=True)
    return results

@st.cache_data(ttl=300, show_spinner=False)
def poly_history(token_id):
    if not token_id: return []
    try:
        r = SESSION.get(f"{POLY_CLOB}/prices-history",
                        params={"market": token_id, "interval": "max", "fidelity": 720}, timeout=10)
        if not r.ok: return []
        out = []
        for pt in r.json().get("history",[]):
            ts = pt.get("t",0); p = float(pt.get("p",0))
            if p <= 0 or ts == 0: continue
            out.append({"ts":ts, "datetime":datetime.fromtimestamp(ts),
                        "close":p, "high":p, "low":p, "volume":0})
        out.sort(key=lambda x:x["ts"])
        return out
    except: return []


# ═══════════════════════════════════════════════════════════════════════════════
# MANIFOLD MARKETS
# ═══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=120, show_spinner=False)
def manifold_search(query):
    """Search Manifold Markets — has native server-side text search."""
    results = []
    try:
        r = SESSION.get(f"{MANIFOLD_BASE}/v0/search-markets", params={
            "term": query,
            "sort": "most-popular",
            "filter": "open",
            "contractType": "BINARY",
            "limit": 100,
        }, timeout=12)
        if not r.ok:
            return []
        markets = r.json()
        if not isinstance(markets, list):
            return []
        
        for m in markets:
            prob = float(m.get("probability") or 0)
            vol = float(m.get("volume") or 0)
            vol24 = float(m.get("volume24Hours") or 0)
            
            results.append({
                "source": "Manifold",
                "title": m.get("question") or "",
                "question": m.get("question") or "",
                "ticker": m.get("slug") or m.get("id",""),
                "event_ticker": "",
                "series_ticker": "",
                "category": "",  # Manifold uses tags, not categories on market objects
                "price": prob,
                "volume_24h": vol24,
                "volume_total": vol,
                "_clob_token": None,
                "_manifold_id": m.get("id"),
                "_manifold_slug": m.get("slug"),
                "_manifold_url": m.get("url") or "",
                "_manifold_creator": m.get("creatorUsername") or "",
            })
    except Exception as e:
        pass
    
    results.sort(key=lambda x: x["volume_24h"], reverse=True)
    return results


@st.cache_data(ttl=300, show_spinner=False)
def manifold_bet_history(market_id):
    """Fetch bet history for a Manifold market and convert to probability timeseries."""
    if not market_id:
        return []
    try:
        # Get the market itself for the probability history via bets
        r = SESSION.get(f"{MANIFOLD_BASE}/v0/bets", params={
            "contractId": market_id,
            "limit": 1000,
        }, timeout=10)
        if not r.ok:
            return []
        bets = r.json()
        if not isinstance(bets, list) or not bets:
            return []
        
        # Bets have probAfter field — use that for timeseries
        out = []
        for b in bets:
            ts = b.get("createdTime")
            prob = b.get("probAfter")
            if ts and prob:
                ts_sec = ts / 1000 if ts > 1e12 else ts  # ms to sec
                out.append({
                    "ts": int(ts_sec),
                    "datetime": datetime.fromtimestamp(ts_sec),
                    "close": float(prob),
                    "high": float(prob),
                    "low": float(prob),
                    "volume": abs(float(b.get("amount") or 0)),
                })
        
        out.sort(key=lambda x: x["ts"])
        
        # Downsample if too many points — take daily snapshots
        if len(out) > 200:
            daily = {}
            for pt in out:
                day = pt["datetime"].strftime("%Y-%m-%d")
                daily[day] = pt  # Last bet of each day wins
            out = sorted(daily.values(), key=lambda x: x["ts"])
        
        return out
    except:
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# SHARED
# ═══════════════════════════════════════════════════════════════════════════════

def prob_color(v):
    p = v*100
    if p >= 70: return "#22c55e"
    if p >= 40: return "#eab308"
    if p <= 15: return "#64748b"
    return "#ef4444"

def fmt_vol(v):
    n = float(v or 0)
    if n >= 1e6: return f"${n/1e6:.1f}M"
    if n >= 1e3: return f"${n/1e3:.0f}K"
    if n > 0: return f"${int(n)}"
    return "—"

def render_chart(entry, candles):
    if not candles:
        st.warning("No price history available.")
        return
    df = pd.DataFrame(candles)
    first, last = df.iloc[0]["close"], df.iloc[-1]["close"]
    ch = last - first
    chp = (ch/first*100) if first > 0 else 0
    lc = "#22c55e" if ch >= 0 else "#ef4444"
    fc = "rgba(34,197,94,0.08)" if ch >= 0 else "rgba(239,68,68,0.08)"

    c1,c2,c3,c4,c5 = st.columns(5)
    c1.metric("Current", f"{last*100:.1f}%")
    c2.metric("Change", f"{ch*100:+.1f}pp", f"{chp:+.1f}%")
    c3.metric("High", f"{df['high'].max()*100:.0f}%")
    c4.metric("Low", f"{df['low'].min()*100:.0f}%")
    c5.metric("Data Points", len(df))

    has_vol = df["volume"].sum() > 0
    fig = make_subplots(rows=2 if has_vol else 1, cols=1, shared_xaxes=True,
                        row_heights=[0.8,0.2] if has_vol else [1], vertical_spacing=0.03)
    fig.add_trace(go.Scatter(x=df["datetime"], y=df["close"]*100, mode="lines",
        line=dict(color=lc, width=2), fill="tozeroy", fillcolor=fc,
        hovertemplate="%{x|%b %d}<br>%{y:.1f}%<extra></extra>"), row=1, col=1)
    if 15 < last*100 < 85:
        fig.add_hline(y=50, line_dash="dot", line_color="#334155", row=1, col=1)
    if has_vol:
        fig.add_trace(go.Bar(x=df["datetime"], y=df["volume"],
            marker_color="rgba(59,130,246,0.3)"), row=2, col=1)
    fig.update_layout(height=360 if has_vol else 300, plot_bgcolor="#0a0e17",
        paper_bgcolor="#0a0e17", font=dict(color="#94a3b8",size=11),
        showlegend=False, margin=dict(l=50,r=20,t=10,b=10), hovermode="x unified")
    fig.update_xaxes(gridcolor="#1e293b", showgrid=True, tickfont=dict(size=10,color="#475569"))
    fig.update_yaxes(gridcolor="#1e293b", showgrid=True, row=1, col=1,
        ticksuffix="%", tickfont=dict(size=10,color="#475569"))
    st.plotly_chart(fig, use_container_width=True)
    
    # Download CSV
    csv_df = df[["datetime","close","high","low","volume"]].copy()
    csv_df.columns = ["Date","Probability","High","Low","Volume"]
    csv_df["Probability"] = (csv_df["Probability"] * 100).round(2)
    csv_df["High"] = (csv_df["High"] * 100).round(2)
    csv_df["Low"] = (csv_df["Low"] * 100).round(2)
    csv_df["Volume"] = csv_df["Volume"].round(0).astype(int)
    csv_df["Market"] = entry.get("question") or entry.get("title","")
    csv_df["Source"] = entry.get("source","")
    csv_data = csv_df.to_csv(index=False)
    slug = re.sub(r'[^a-z0-9]+', '_', (entry.get("ticker") or "market").lower())[:40]
    st.download_button("📥 Download CSV", csv_data, file_name=f"{slug}_history.csv",
                       mime="text/csv", use_container_width=False)
OUTCOME_COLORS = [
    "#3b82f6", "#ef4444", "#22c55e", "#f59e0b", "#8b5cf6",
    "#ec4899", "#06b6d4", "#f97316", "#14b8a6", "#a855f7",
    "#eab308", "#6366f1", "#d946ef", "#84cc16",
]

def render_multi_outcome_chart(event_title, siblings):
    """Render all outcomes of a multi-market event on one chart."""
    fig = go.Figure()
    outcome_data = []
    
    for i, sib in enumerate(siblings[:14]):  # Cap at 14 outcomes
        label = sib["title"].replace(event_title, "").strip(" :—-")
        if not label:
            label = sib["ticker"].rsplit("-", 1)[-1] if "-" in sib["ticker"] else f"Outcome {i+1}"
        
        color = OUTCOME_COLORS[i % len(OUTCOME_COLORS)]
        
        # Fetch candles for this outcome
        candles = []
        if sib["source"] == "Kalshi" and sib.get("series_ticker"):
            candles = kalshi_candles(sib["series_ticker"], sib["ticker"])
        elif sib["source"] == "Polymarket" and sib.get("_clob_token"):
            candles = poly_history(sib["_clob_token"])
        
        price = sib["price"]
        outcome_data.append({"label": label, "price": price, "color": color, "has_data": len(candles) > 0})
        
        if candles:
            df = pd.DataFrame(candles)
            fig.add_trace(go.Scatter(
                x=df["datetime"], y=df["close"] * 100,
                mode="lines", name=f"{label} ({price*100:.0f}%)",
                line=dict(color=color, width=2),
                hovertemplate=f"{label}<br>%{{x|%b %d}}: %{{y:.1f}}%<extra></extra>",
            ))
    
    # Add 50% reference
    fig.add_hline(y=50, line_dash="dot", line_color="#334155")
    
    fig.update_layout(
        height=420, plot_bgcolor="#0a0e17", paper_bgcolor="#0a0e17",
        font=dict(color="#94a3b8", size=11),
        showlegend=True,
        legend=dict(
            bgcolor="#0f172a", bordercolor="#1e293b", borderwidth=1,
            font=dict(size=10, color="#e2e8f0"),
            orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
        ),
        margin=dict(l=50, r=20, t=40, b=10), hovermode="x unified",
    )
    fig.update_xaxes(gridcolor="#1e293b", showgrid=True, tickfont=dict(size=10, color="#475569"))
    fig.update_yaxes(gridcolor="#1e293b", showgrid=True,
                     ticksuffix="%", tickfont=dict(size=10, color="#475569"),
                     title_text="Implied Probability")
    
    st.plotly_chart(fig, use_container_width=True)
    
    # Show current probabilities as a summary table below the chart
    if outcome_data:
        for od in sorted(outcome_data, key=lambda x: x["price"], reverse=True):
            c = od["color"]
            p = od["price"]
            pct = f"{p*100:.0f}%" if p > 0 else "—"
            bar_w = max(int(p * 100), 1) if p > 0 else 0
            st.markdown(f"""<div style="display:flex;align-items:center;gap:8px;margin:3px 0;">
                <span style="color:{c};font-size:12px;font-weight:600;min-width:120px;">{od['label'][:30]}</span>
                <div style="flex:1;height:6px;background:#1e293b;border-radius:3px;overflow:hidden;">
                    <div style="width:{bar_w}%;height:100%;background:{c};border-radius:3px;"></div>
                </div>
                <span style="font-size:12px;font-weight:700;font-family:monospace;color:{c};min-width:40px;text-align:right;">{pct}</span>
            </div>""", unsafe_allow_html=True)
    
    # Download CSV with all outcomes' current probabilities
    csv_rows = []
    for od in sorted(outcome_data, key=lambda x: x["price"], reverse=True):
        csv_rows.append({
            "Event": event_title,
            "Outcome": od["label"],
            "Probability (%)": round(od["price"] * 100, 1) if od["price"] > 0 else None,
            "Has History": od["has_data"],
        })
    if csv_rows:
        csv_df = pd.DataFrame(csv_rows)
        csv_data = csv_df.to_csv(index=False)
        slug = re.sub(r'[^a-z0-9]+', '_', event_title.lower())[:40]
        st.download_button("📥 Download CSV", csv_data, file_name=f"{slug}_outcomes.csv",
                           mime="text/csv", use_container_width=False)


def group_by_event(results):
    """Group results by event_ticker to identify multi-outcome events."""
    groups = {}
    ungrouped = []
    for r in results:
        et = r.get("event_ticker","")
        if et and r["source"] == "Kalshi":
            if et not in groups:
                groups[et] = []
            groups[et].append(r)
        else:
            ungrouped.append(r)
    return groups, ungrouped

def render_row(entry, idx, tab):
    price = entry["price"]
    color = prob_color(price) if price > 0 else "#334155"
    src_colors = {"Polymarket": "#8b5cf6", "Kalshi": "#3b82f6", "Manifold": "#f59e0b"}
    src_c = src_colors.get(entry["source"], "#64748b")
    
    ct, cp, cv, cb = st.columns([5,1,1,1])
    with ct:
        q = entry.get("question") or entry["title"]
        cat_h = f'<span style="font-size:9px;color:#334155;background:#1e293b;padding:1px 5px;border-radius:3px;margin-right:4px;">{entry["category"]}</span>' if entry["category"] else ""
        st.markdown(f"""<div style="line-height:1.3;">
            <span class="src-badge" style="background:{src_c}20;color:{src_c};border:1px solid {src_c}40;margin-right:4px;">{entry["source"]}</span>
            {cat_h}<span style="font-size:13px;font-weight:500;color:#e2e8f0;">{q[:130]}</span>
        </div>""", unsafe_allow_html=True)
    with cp:
        if price > 0:
            st.markdown(f'<span class="prob-badge" style="background:{color}15;color:{color};border:1px solid {color}30;">{price*100:.0f}¢</span>', unsafe_allow_html=True)
        else:
            st.markdown('<span style="color:#334155;">—</span>', unsafe_allow_html=True)
    with cv:
        vol_display = fmt_vol(entry["volume_24h"]) if entry["volume_24h"] > 0 else fmt_vol(entry.get("volume_total",0))
        st.markdown(f'<span style="font-size:11px;color:#94a3b8;font-family:monospace;">{vol_display}</span>', unsafe_allow_html=True)
    with cb:
        has_data = bool(entry.get("_clob_token") or entry.get("series_ticker") or entry.get("_manifold_id"))
        if has_data and st.button("📈", key=f"c_{tab}_{idx}_{hash(entry['ticker'])}"):
            st.session_state.selected_market = entry
            st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# UI
# ═══════════════════════════════════════════════════════════════════════════════

st.markdown("""<div style="margin-bottom:2px;">
    <span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:#3b82f6;box-shadow:0 0 6px #3b82f680;margin-right:6px;"></span>
    <span style="font-size:10px;font-weight:700;color:#3b82f6;text-transform:uppercase;letter-spacing:1.5px;">Prediction Market Intelligence</span>
</div>""", unsafe_allow_html=True)
st.markdown("## Market Scanner")
st.caption("**Kalshi** + **Polymarket** + **Manifold** · Public APIs · No auth · Click 📈 for probability trend charts")

# Category filter — maps to Kalshi series categories and Polymarket category strings
CATEGORIES = {
    "All Categories":          {"kalshi": None, "poly_kw": None},
    "🔥 Trending":              {"kalshi": None, "poly_kw": None, "trending": True},
    "🏢 Companies & Earnings":  {"kalshi": "Companies,Mentions", "poly_kw": ["business","corporate","company","earnings"]},
    "💰 Economics & Finance":   {"kalshi": "Economics,Financials", "poly_kw": ["economics","finance","financial"]},
    "🗳️ Elections":             {"kalshi": "Elections", "poly_kw": ["elections","election"]},
    "🏛️ Politics":              {"kalshi": "Politics", "poly_kw": ["politics","political","government"]},
    "🔬 Science & Tech":        {"kalshi": "Science and Technology", "poly_kw": ["science","technology","tech","ai"]},
    "🌍 World & Climate":       {"kalshi": "World,Climate and Weather", "poly_kw": ["world","climate","weather","global"]},
    "🏈 Sports":                {"kalshi": "Sports", "poly_kw": ["sports","nba","nfl","mlb","soccer","football"]},
    "🎬 Entertainment":         {"kalshi": "Entertainment", "poly_kw": ["entertainment","culture","media","music","movies"]},
    "💊 Health":                {"kalshi": "Health", "poly_kw": ["health","medical","covid"]},
    "₿ Crypto":                 {"kalshi": "Crypto", "poly_kw": ["crypto","bitcoin","ethereum","defi"]},
    "🚗 Transportation":        {"kalshi": "Transportation", "poly_kw": ["transportation","auto","aviation"]},
}

TAGS = ["Trump","Bitcoin","recession","inflation","Fed","tariff","AI","Pope","Tesla","China","oil","earnings"]

c_cat, cs, cb = st.columns([2, 4, 1])
with c_cat:
    selected_cat = st.selectbox("Category", options=list(CATEGORIES.keys()),
                                 index=0, label_visibility="collapsed", key="cat")
with cs:
    query = st.text_input("q", placeholder="Search (Trump, recession, Bitcoin, tariff, constellation...)",
                          label_visibility="collapsed", key="q")
with cb:
    sc = st.button("Search", use_container_width=True)

tc = None
cols = st.columns(min(len(TAGS),6))
for i,t in enumerate(TAGS):
    with cols[i%len(cols)]:
        if st.button(t, key=f"t_{t}", use_container_width=True): tc = t

for k in ["last_query","selected_market","last_cat"]:
    if k not in st.session_state: st.session_state[k] = None

# Allow trending mode without a search term
is_trending = CATEGORIES.get(selected_cat, {}).get("trending", False)
aq = tc or (query if sc or query else None)
if is_trending and not aq:
    aq = "__trending__"  # Sentinel to trigger trending mode
if aq and (aq != st.session_state.last_query or selected_cat != st.session_state.last_cat):
    st.session_state.last_query = aq
    st.session_state.last_cat = selected_cat
    st.session_state.selected_market = None

# ─── Main ─────────────────────────────────────────────────────────────────────
if st.session_state.last_query:
    q = st.session_state.last_query

    if st.session_state.selected_market:
        e = st.session_state.selected_market
        if st.button("← Back to results"):
            st.session_state.selected_market = None; st.rerun()
        src_colors = {"Polymarket": "#8b5cf6", "Kalshi": "#3b82f6", "Manifold": "#f59e0b"}
        src_c = src_colors.get(e["source"], "#64748b")
        st.markdown(f'<span class="src-badge" style="background:{src_c}20;color:{src_c};border:1px solid {src_c}40;">{e["source"]}</span>', unsafe_allow_html=True)
        
        if e.get("_manifold_url"):
            st.caption(f"[View on Manifold →]({e['_manifold_url']})")
        
        # Check if this is part of a multi-outcome Kalshi event
        siblings = e.get("_siblings", [])
        
        if siblings and len(siblings) > 1:
            # Multi-outcome event — show all outcomes on one chart
            event_title = e.get("_event_title") or e.get("question") or e["title"]
            st.markdown(f"### {event_title}")
            st.caption(f"{len(siblings)} outcomes · Click legend entries to toggle")
            
            with st.spinner(f"Loading price history for {len(siblings)} outcomes..."):
                render_multi_outcome_chart(event_title, siblings)
        else:
            # Single outcome — original behavior
            st.markdown(f"### {e.get('question') or e['title']}")
            if e.get("category"): st.caption(f"Category: **{e['category']}**")
            
            with st.spinner("Loading price history..."):
                if e["source"]=="Polymarket" and e.get("_clob_token"):
                    candles = poly_history(e["_clob_token"])
                elif e["source"]=="Kalshi" and e.get("series_ticker"):
                    candles = kalshi_candles(e["series_ticker"], e["ticker"])
                elif e["source"]=="Manifold" and e.get("_manifold_id"):
                    candles = manifold_bet_history(e["_manifold_id"])
                else:
                    candles = []
            render_chart(e, candles)

    else:
        kr, pr, mr = [], [], []
        cat_cfg = CATEGORIES.get(selected_cat, {"kalshi": None, "poly_kw": None})
        cat_kalshi = cat_cfg["kalshi"]
        cat_poly_kw = cat_cfg.get("poly_kw")
        is_trending_mode = cat_cfg.get("trending", False)
        
        if is_trending_mode:
            # Trending mode: top markets by volume, no keyword filter
            with st.spinner("Loading trending Kalshi markets..."):
                try:
                    ke = kalshi_fetch_events()
                    for ev in ke:
                        et = ev.get("event_ticker","")
                        st_tick = ev.get("series_ticker","")
                        title = ev.get("title","")
                        cat = ev.get("category","")
                        for m in ev.get("markets", []):
                            vol = float(m.get("volume_24h_fp") or 0)
                            if vol <= 0: continue
                            kr.append({
                                "source": "Kalshi", "title": m.get("title") or title,
                                "question": title, "ticker": m.get("ticker",""),
                                "event_ticker": m.get("event_ticker","") or et,
                                "series_ticker": st_tick, "category": cat,
                                "price": float(m.get("last_price_dollars") or m.get("yes_bid_dollars") or 0),
                                "volume_24h": vol,
                                "volume_total": float(m.get("volume_fp") or 0),
                                "_clob_token": None,
                            })
                    kr.sort(key=lambda x: x["volume_24h"], reverse=True)
                    kr = kr[:50]
                except Exception as e: st.warning(f"Kalshi: {e}")
            
            with st.spinner("Loading trending Polymarket markets..."):
                try:
                    pm = poly_fetch_markets()
                    for m in pm[:50]:
                        vol24 = float(m.get("volume24hr") or 0)
                        if vol24 <= 0: continue
                        price = 0
                        op = m.get("outcomePrices")
                        if op:
                            try:
                                prices = json.loads(op) if isinstance(op, str) else op
                                price = float(prices[0]) if prices else 0
                            except: pass
                        if not price: price = float(m.get("lastTradePrice") or 0)
                        token_id = None
                        ctids = m.get("clobTokenIds")
                        if ctids:
                            try:
                                ids = json.loads(ctids) if isinstance(ctids, str) else ctids
                                token_id = ids[0] if ids else None
                            except: pass
                        pr.append({
                            "source": "Polymarket",
                            "title": m.get("question",""),
                            "question": m.get("question",""),
                            "ticker": m.get("slug",""),
                            "event_ticker": "", "series_ticker": "",
                            "category": m.get("category",""),
                            "price": price, "volume_24h": vol24,
                            "volume_total": float(m.get("volumeNum") or 0),
                            "_clob_token": token_id,
                        })
                except Exception as e: st.warning(f"Polymarket: {e}")
            
            with st.spinner("Loading trending Manifold markets..."):
                try:
                    r = SESSION.get(f"{MANIFOLD_BASE}/v0/search-markets", params={
                        "sort": "24-hour-vol", "filter": "open",
                        "contractType": "BINARY", "limit": 30,
                    }, timeout=12)
                    if r.ok:
                        for m in r.json():
                            prob = float(m.get("probability") or 0)
                            mr.append({
                                "source": "Manifold",
                                "title": m.get("question",""),
                                "question": m.get("question",""),
                                "ticker": m.get("slug") or m.get("id",""),
                                "event_ticker": "", "series_ticker": "",
                                "category": "", "price": prob,
                                "volume_24h": float(m.get("volume24Hours") or 0),
                                "volume_total": float(m.get("volume") or 0),
                                "_clob_token": None,
                                "_manifold_id": m.get("id"),
                                "_manifold_slug": m.get("slug"),
                                "_manifold_url": m.get("url",""),
                                "_manifold_creator": m.get("creatorUsername",""),
                            })
                except Exception as e: st.warning(f"Manifold: {e}")
        
        else:
            # Normal keyword search
            with st.spinner(f"Searching Kalshi{' (' + selected_cat + ')' if cat_kalshi else ''}..."):
                try:
                    if cat_kalshi:
                        ke = kalshi_fetch_by_category(cat_kalshi)
                        kr = kalshi_search(q, ke)
                    else:
                        ke = kalshi_fetch_events()
                        kr = kalshi_search(q, ke)
                        
                        nonmve = kalshi_fetch_nonmve_markets()
                        kw = q.lower().strip().split()
                        seen_tickers = {r["ticker"] for r in kr}
                        for m in nonmve:
                            if m.get("ticker","") in seen_tickers: continue
                            blob = f"{m.get('title','')} {m.get('subtitle','')} {m.get('yes_sub_title','')} {m.get('ticker','')} {m.get('event_ticker','')}".lower()
                            if not all(k in blob for k in kw): continue
                            et = m.get("event_ticker","")
                            sr = re.match(r'^([A-Z]+)', et)
                            sr_tick = sr.group(1) if sr else et.split("-")[0] if et else ""
                            kr.append({
                                "source": "Kalshi", "title": m.get("title",""),
                                "question": m.get("title",""), "ticker": m.get("ticker",""),
                                "event_ticker": et, "series_ticker": sr_tick, "category": "",
                                "price": float(m.get("last_price_dollars") or m.get("yes_bid_dollars") or 0),
                                "volume_24h": float(m.get("volume_24h_fp") or 0),
                                "volume_total": float(m.get("volume_fp") or 0),
                                "_clob_token": None,
                            })
                            seen_tickers.add(m.get("ticker",""))
                        kr.sort(key=lambda x: x["volume_24h"], reverse=True)
                except Exception as e: st.warning(f"Kalshi: {e}")

            with st.spinner("Searching Polymarket..."):
                try:
                    pm = poly_fetch_markets()
                    pr = poly_search(q, pm)
                    if cat_poly_kw and pr:
                        pr = [r for r in pr if any(kw in (r.get("category","")+" "+r.get("question","")).lower() for kw in cat_poly_kw)] or pr
                except Exception as e: st.warning(f"Polymarket: {e}")

            with st.spinner("Searching Manifold..."):
                try:
                    mr = manifold_search(q)
                    if cat_poly_kw and mr:
                        filtered = [r for r in mr if any(kw in r.get("question","").lower() for kw in cat_poly_kw)]
                        if filtered: mr = filtered
                except Exception as e: st.warning(f"Manifold: {e}")

        # Group Kalshi results by event for display
        kalshi_groups, kalshi_ungrouped = group_by_event(kr)
        
        # Build display list: grouped Kalshi events become single entries
        kalshi_display = []
        for et, siblings in kalshi_groups.items():
            if len(siblings) > 1:
                # Multi-outcome event — create a grouped entry
                top = max(siblings, key=lambda x: x["price"])
                event_title = siblings[0].get("question","") or siblings[0].get("title","")
                # Strip any outcome-specific suffix to get clean event title
                for sib in siblings:
                    # The original event title is typically the shortest common prefix
                    if sib.get("event_ticker") == et:
                        break
                kalshi_display.append({
                    **top,
                    "title": f"{event_title} ({len(siblings)} outcomes)",
                    "question": event_title,
                    "_siblings": siblings,
                    "_event_title": event_title,
                })
            else:
                kalshi_display.append(siblings[0])
        kalshi_display.extend(kalshi_ungrouped)
        kalshi_display.sort(key=lambda x: x["volume_24h"], reverse=True)
        
        combined = sorted(kalshi_display + pr + mr, key=lambda x: x["volume_24h"], reverse=True)

        st.markdown(f"""<div style="font-size:12px;color:#64748b;margin:8px 0 12px;">
            <strong style="color:#e2e8f0;">{len(combined)}</strong> {"trending markets" if is_trending_mode else f'results for "<span style="color:#3b82f6;">{q}</span>"'}
            — <span style="color:#3b82f6;">{len(kr)} Kalshi</span>
            + <span style="color:#8b5cf6;">{len(pr)} Polymarket</span>
            + <span style="color:#f59e0b;">{len(mr)} Manifold</span>
        </div>""", unsafe_allow_html=True)

        if not combined:
            st.info("No open markets match this query on any platform. Try broader terms.")
        else:
            # Download all results
            export_rows = []
            for r in combined:
                export_rows.append({
                    "Source": r.get("source",""),
                    "Question": r.get("question","") or r.get("title",""),
                    "Probability (%)": round(r["price"] * 100, 1) if r["price"] > 0 else None,
                    "24h Volume": round(r.get("volume_24h",0), 2),
                    "Total Volume": round(r.get("volume_total",0), 2),
                    "Category": r.get("category",""),
                    "Ticker": r.get("ticker",""),
                })
            export_df = pd.DataFrame(export_rows)
            st.download_button(
                f"📥 Download all {len(combined)} results as CSV",
                export_df.to_csv(index=False),
                file_name=f"prediction_markets_{re.sub(r'[^a-z0-9]+','_',q.lower())[:30]}.csv",
                mime="text/csv",
            )
            t_all, t_k, t_p, t_m = st.tabs([
                f"All ({len(combined)})",
                f"Kalshi ({len(kalshi_display)})",
                f"Polymarket ({len(pr)})",
                f"Manifold ({len(mr)})",
            ])
            with t_all:
                for i, e in enumerate(combined[:60]):
                    render_row(e, i, "a")
                    if i < min(len(combined),60)-1:
                        st.markdown('<hr style="border:none;border-top:1px solid #111827;margin:2px 0;">', unsafe_allow_html=True)
            with t_k:
                if not kalshi_display: st.caption("No Kalshi results.")
                for i, e in enumerate(kalshi_display[:40]):
                    render_row(e, i, "k")
                    if i < min(len(kalshi_display),40)-1:
                        st.markdown('<hr style="border:none;border-top:1px solid #111827;margin:2px 0;">', unsafe_allow_html=True)
            with t_p:
                if not pr: st.caption("No Polymarket results.")
                for i, e in enumerate(pr[:40]):
                    render_row(e, i, "p")
                    if i < min(len(pr),40)-1:
                        st.markdown('<hr style="border:none;border-top:1px solid #111827;margin:2px 0;">', unsafe_allow_html=True)
            with t_m:
                if not mr: st.caption("No Manifold results.")
                for i, e in enumerate(mr[:40]):
                    render_row(e, i, "m")
                    if i < min(len(mr),40)-1:
                        st.markdown('<hr style="border:none;border-top:1px solid #111827;margin:2px 0;">', unsafe_allow_html=True)
else:
    st.markdown("""<div style="text-align:center;padding:40px 20px;color:#475569;">
        <p style="font-size:14px;max-width:420px;margin:0 auto;line-height:1.6;">
            Search prediction markets across <strong style="color:#3b82f6;">Kalshi</strong>,
            <strong style="color:#8b5cf6;">Polymarket</strong>, and
            <strong style="color:#f59e0b;">Manifold</strong> simultaneously.
            Click 📈 for probability trends over time.
        </p>
        <p style="font-size:11px;color:#334155;margin-top:12px;">
            Manifold has server-side search — great for brand-specific queries like "Tesla", "Netflix", "OpenAI"
        </p>
    </div>""", unsafe_allow_html=True)

st.markdown("---")
st.caption("Kalshi + Polymarket + Manifold public APIs · Prices = implied probabilities · Not financial advice · v6")
