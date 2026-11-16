from datetime import datetime, timedelta, timezone
import math
from zoneinfo import ZoneInfo
import folium
from folium.plugins import MarkerCluster
import pandas as pd
import plotly.graph_objects as go
import requests
from requests.adapters import HTTPAdapter
import streamlit as st
from streamlit_folium import st_folium
from urllib3.util.retry import Retry
import yfinance as yf

st.set_page_config(layout="wide", page_title="T-MINUS // ORBITAL TELEMETRY TERMINAL")

# --- TERMINAL / CRT FLIGHT-CONTROL CSS ---
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&display=swap');

    html, body, [class*="css"], .stApp {
        font-family: 'JetBrains Mono', 'Courier New', monospace !important;
    }

    .stApp {
        background-color: #030706;
        background-image:
            repeating-linear-gradient(
                0deg,
                rgba(0, 255, 102, 0.025) 0px,
                rgba(0, 255, 102, 0.025) 1px,
                transparent 1px,
                transparent 3px
            ),
            radial-gradient(circle at 50% 0%, #071912 0%, #030706 75%);
    }

    div[data-testid="stVerticalBlockBorderWrapper"] {
        background: rgba(5, 14, 10, 0.92) !important;
        border: 1px solid rgba(0, 255, 102, 0.35) !important;
        border-radius: 2px !important;
        box-shadow: 0 0 18px rgba(0, 255, 102, 0.06);
    }

    .stButton > button, .stDownloadButton > button, .stLinkButton > a {
        border-radius: 2px !important;
        border: 1px solid rgba(0, 255, 102, 0.45) !important;
        font-family: 'JetBrains Mono', monospace !important;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }

    /* Cap rocket image height so mission metadata stays visible above the fold */
    div[data-testid="stImage"] img {
        max-height: 270px;
        object-fit: cover;
        border-radius: 2px;
        border: 1px solid rgba(0, 255, 102, 0.25);
    }

    @keyframes blink {
        0%, 49% { opacity: 1; }
        50%, 100% { opacity: 0; }
    }
    .cursor-blink {
        animation: blink 1.1s infinite;
        color: #00ff66;
    }
    .term-header {
        border-bottom: 1px solid rgba(0, 255, 102, 0.35);
        padding-bottom: 8px;
        margin-bottom: 12px;
    }
    .term-title {
        color: #00ff66;
        font-size: 1.65rem;
        font-weight: 700;
        letter-spacing: 0.08em;
        margin: 0;
        text-shadow: 0 0 10px rgba(0, 255, 102, 0.4);
    }
    .term-sub {
        color: #6ee7b7;
        font-size: 0.8rem;
        opacity: 0.85;
    }
    .countdown-readout {
        font-size: 1.05rem;
        font-weight: 700;
        color: #00ff66;
        background: rgba(0, 255, 102, 0.08);
        border: 1px solid rgba(0, 255, 102, 0.45);
        padding: 3px 9px;
        border-radius: 2px;
        display: inline-block;
    }
    .status-tag {
        font-size: 0.85rem;
        color: #ffb000;
        background: rgba(255, 176, 0, 0.1);
        border: 1px solid rgba(255, 176, 0, 0.4);
        padding: 3px 8px;
        border-radius: 2px;
        display: inline-block;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

TIMEZONES = {
    "UTC": "UTC",
    "US/Eastern": "America/New_York",
    "US/Central": "America/Chicago",
    "US/Mountain": "America/Denver",
    "US/Pacific": "America/Los_Angeles",
    "Europe/London": "Europe/London",
}

ARCHIVE_YEARS = [2026, 2025, 2024, 2023]

# Mapping Launch Library provider names to public tickers or private/state profiles
PROVIDER_MARKET_MAP = {
    "Rocket Lab": {"type": "PUBLIC", "tickers": ["RKLB"], "note": "Direct Pure-Play Launcher (NASDAQ: RKLB)"},
    "Firefly Aerospace": {"type": "PUBLIC", "tickers": ["FLY"], "note": "Small/Medium Launch & Lunar Systems (NASDAQ: FLY)"},
    "Virgin Galactic": {"type": "PUBLIC", "tickers": ["SPCE"], "note": "Suborbital Spaceflight (NYSE: SPCE)"},
    "United Launch Alliance": {
        "type": "JV_PUBLIC",
        "tickers": ["BA", "LMT"],
        "note": "50/50 Joint Venture: Boeing (BA) & Lockheed Martin (LMT)",
    },
    "Northrop Grumman": {"type": "PUBLIC", "tickers": ["NOC"], "note": "Defense & Space Systems Prime (NYSE: NOC)"},
    "ArianeGroup": {
        "type": "JV_PUBLIC",
        "tickers": ["AIR.PA", "SAF.PA"],
        "note": "50/50 Joint Venture: Airbus (AIR.PA) & Safran (SAF.PA)",
    },
    "Arianespace": {
        "type": "JV_PUBLIC",
        "tickers": ["AIR.PA", "SAF.PA"],
        "note": "Subsidiary of ArianeGroup (Airbus / Safran)",
    },
    "Mitsubishi Heavy Industries": {
        "type": "PUBLIC",
        "tickers": ["7011.T"],
        "note": "H3 / H-IIA Prime Contractor (TSE: 7011.T)",
    },
    "SpaceX": {
        "type": "PRIVATE",
        "tickers": [],
        "est_val": "~$350B+ (Secondary Tender Est.)",
        "note": "Privately Held // Dominates global mass-to-orbit via Falcon & Starship.",
    },
    "Blue Origin": {
        "type": "PRIVATE",
        "tickers": [],
        "est_val": "Privately Funded (Jeff Bezos)",
        "note": "Privately Held // New Glenn heavy-lift & BE-4 engine supplier to ULA.",
    },
    "China Aerospace Science and Technology Corporation": {
        "type": "STATE",
        "tickers": [],
        "est_val": "State-Owned Enterprise (PRC)",
        "note": "Primary contractor for China's Long March rocket family & space program.",
    },
    "Russian Federal Space Agency (ROSCOSMOS)": {
        "type": "STATE",
        "tickers": [],
        "est_val": "State Space Corporation (RF)",
        "note": "Operates Soyuz, Proton, and Angara launch vehicles.",
    },
    "Indian Space Research Organization": {
        "type": "STATE",
        "tickers": [],
        "est_val": "~$1.6B Annual Gov Budget",
        "note": "National space agency of India (LVM3, PSLV, Gaganyaan).",
    },
}

SECTOR_WATCHLIST = [
    ("RKLB", "Rocket Lab USA"),
    ("FLY", "Firefly Aerospace"),
    ("LUNR", "Intuitive Machines"),
    ("ASTS", "AST SpaceMobile"),
    ("SPCE", "Virgin Galactic"),
    ("BA", "Boeing (ULA 50% JV)"),
    ("LMT", "Lockheed Martin (ULA 50% JV)"),
    ("NOC", "Northrop Grumman"),
    ("7011.T", "Mitsubishi Heavy Ind."),
]


def get_resilient_session():
    session = requests.Session()
    retry_strategy = Retry(
        total=3,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def get_image_url(obj):
    if isinstance(obj, dict):
        return obj.get("image_url")
    return obj if isinstance(obj, str) else None


def extract_country(pad):
    loc = (pad or {}).get("location") or {}
    country = loc.get("country")
    if isinstance(country, dict):
        return country.get("alpha_3_code") or country.get("name") or "UNK"
    return loc.get("country_code") or "UNK"


def parse_utc(net_str):
    if not net_str:
        return None
    return datetime.fromisoformat(net_str.replace("Z", "+00:00"))


def format_local_time(net_str, tz_label):
    dt = parse_utc(net_str)
    if not dt:
        return "TBD"
    local_dt = dt.astimezone(ZoneInfo(TIMEZONES[tz_label]))
    return local_dt.strftime(f"%Y-%m-%d %H:%M {tz_label}")


def format_countdown(net_str, tz_label):
    dt = parse_utc(net_str)
    if not dt:
        return "TBD"
    delta = dt - datetime.now(timezone.utc)
    total_seconds = int(delta.total_seconds())
    if total_seconds < 0:
        return dt.astimezone(ZoneInfo(TIMEZONES[tz_label])).strftime("%Y-%m-%d")
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    return f"T-{days:02d}D {hours:02d}H {minutes:02d}M"


def format_large_num(num):
    if num is None or (isinstance(num, float) and math.isnan(num)):
        return "N/A"
    abs_n = abs(num)
    if abs_n >= 1e12:
        return f"${num / 1e12:.2f}T"
    if abs_n >= 1e9:
        return f"${num / 1e9:.2f}B"
    if abs_n >= 1e6:
        return f"${num / 1e6:.1f}M"
    return f"${num:,.0f}"


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_stock_bundle(ticker_symbol):
    tk = yf.Ticker(ticker_symbol)
    hist = tk.history(period="1y")
    info = tk.info or {}

    price = info.get("currentPrice") or info.get("regularMarketPrice")
    if price is None and not hist.empty:
        price = float(hist["Close"].iloc[-1])

    prev_close = info.get("previousClose")
    if prev_close is None and len(hist) >= 2:
        prev_close = float(hist["Close"].iloc[-2])

    day_chg_pct = ((price - prev_close) / prev_close * 100) if (price and prev_close) else 0.0

    # Calculate Cash Runway (Total Cash / Annualized Operating Cash Burn)
    total_cash = info.get("totalCash")
    op_cf = info.get("operatingCashflow")
    if total_cash and op_cf is not None:
        if op_cf >= 0:
            runway_str = "FCF+ (Self-Funded)"
        else:
            months = (total_cash / abs(op_cf)) * 12
            runway_str = f"~{months:.1f} Mos"
    else:
        runway_str = "N/A"

    rev_growth = info.get("revenueGrowth")
    rev_growth_str = f"{rev_growth * 100:+.1f}%" if rev_growth is not None else "N/A"

    pe_val = info.get("trailingPE") or info.get("forwardPE")
    pe_str = f"{pe_val:.1f}x" if pe_val else "Neg / N/A"

    return {
        "ticker": ticker_symbol,
        "name": info.get("shortName") or ticker_symbol,
        "price": price,
        "day_chg_pct": day_chg_pct,
        "market_cap": format_large_num(info.get("marketCap")),
        "market_cap_raw": info.get("marketCap") or 0,
        "pe": pe_str,
        "rev_growth": rev_growth_str,
        "cash": format_large_num(total_cash),
        "runway": runway_str,
        "high_52w": info.get("fiftyTwoWeekHigh"),
        "low_52w": info.get("fiftyTwoWeekLow"),
        "hist": hist,
    }


@st.cache_data(ttl=120, show_spinner=False)
def fetch_iss_telemetry():
    session = get_resilient_session()
    # Fetch current position + past/future 45 minutes of orbital coordinates
    now_ts = int(datetime.now(timezone.utc).timestamp())
    timestamps = [now_ts + (m * 60) for m in range(-45, 50, 5)]
    ts_str = ",".join(str(t) for t in timestamps)
    url = f"https://api.wheretheiss.at/v1/satellites/25544/positions?timestamps={ts_str}"
    r = session.get(url, timeout=10)
    r.raise_for_status()
    return r.json()


def build_ics_file(launch):
    dt = parse_utc(launch.get("net"))
    if not dt:
        return None
    dt_end = dt + timedelta(hours=1)
    fmt = "%Y%m%dT%H%M%SZ"
    name = launch.get("name", "Rocket Launch")
    pad_name = ((launch.get("pad") or {}).get("location") or {}).get("name", "Unknown Pad")
    desc = ((launch.get("mission") or {}).get("description") or "").replace("\n", " ")
    vid_urls = launch.get("vid_urls") or []
    stream = vid_urls[0].get("url", "") if (vid_urls and isinstance(vid_urls[0], dict)) else ""
    if stream:
        desc = f"Webcast: {stream} | {desc}"

    return (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//T-Minus Telemetry//Launch Console//EN\r\n"
        "BEGIN:VEVENT\r\n"
        f"UID:{launch.get('id', 'launch')}@tminus-telemetry\r\n"
        f"DTSTAMP:{datetime.now(timezone.utc).strftime(fmt)}\r\n"
        f"DTSTART:{dt.strftime(fmt)}\r\n"
        f"DTEND:{dt_end.strftime(fmt)}\r\n"
        f"SUMMARY:🚀 {name}\r\n"
        f"LOCATION:{pad_name}\r\n"
        f"DESCRIPTION:{desc}\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    ).encode("utf-8")


def compute_trajectory(lat, lon, orbit_name):
    orbit = (orbit_name or "").lower()
    if "sun-synchronous" in orbit or "sso" in orbit:
        bearing_deg = 192.0
    elif "polar" in orbit:
        bearing_deg = 180.0
    elif "iss" in orbit or "station" in orbit:
        bearing_deg = 51.6
    else:
        bearing_deg = 92.0

    rad_b = math.radians(bearing_deg)
    points = [[lat, lon]]
    for step in range(1, 6):
        dist_deg = step * 1.15
        d_lat = dist_deg * math.cos(rad_b)
        d_lon = (dist_deg * math.sin(rad_b)) / max(math.cos(math.radians(lat)), 0.2)
        points.append([lat + d_lat, lon + d_lon])
    return points


def get_upcoming_pin(status_abbr, net_str):
    if status_abbr == "Hold":
        return "red", "pause", "[HOLD]", "Launch Hold", "#ef4444"
    if status_abbr == "In Flight":
        return "purple", "rocket", "[LIVE]", "In Flight", "#c084fc"
    dt = parse_utc(net_str)
    if dt:
        hours_away = (dt - datetime.now(timezone.utc)).total_seconds() / 3600
        if 0 <= hours_away <= 24:
            return "green", "fire", "[<24H]", "Launching in < 24 Hours", "#00ff66"
        if 24 < hours_away <= 168:
            return "orange", "clock-o", "[<1WK]", "Launching in < 1 Week", "#ffb000"
    return "blue", "rocket", "[>1WK]", "Scheduled (> 1 Week Out)", "#38bdf8"


def get_archive_pin(status_abbr):
    if status_abbr == "Success":
        return "green", "check", "Success", "#00ff66"
    if status_abbr == "Failure":
        return "red", "times", "Failure", "#ef4444"
    if status_abbr == "Partial Failure":
        return "orange", "exclamation", "Partial Failure", "#ffb000"
    return "blue", "rocket", status_abbr, "#38bdf8"


@st.cache_data(ttl=900, show_spinner=False)
def fetch_upcoming():
    session = get_resilient_session()
    url = "https://ll.thespacedevs.com/2.3.0/launches/upcoming/?limit=20&mode=detailed"
    r = session.get(url, timeout=30)
    r.raise_for_status()
    return sorted(r.json().get("results", []), key=lambda x: x.get("net") or "9999-12-31")


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_year_launches(year):
    session = get_resilient_session()
    now_utc = datetime.now(timezone.utc)
    start_iso = f"{year}-01-01T00:00:00Z"
    if year == now_utc.year:
        end_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        end_iso = f"{year}-12-31T23:59:59Z"

    url = (
        f"https://ll.thespacedevs.com/2.3.0/launches/"
        f"?net__gte={start_iso}&net__lte={end_iso}&limit=100&mode=detailed"
    )
    all_results = []
    pages = 0
    while url and pages < 5:
        r = session.get(url, timeout=30)
        r.raise_for_status()
        payload = r.json()
        all_results.extend(payload.get("results", []))
        url = payload.get("next")
        pages += 1
    return sorted(all_results, key=lambda x: x.get("net") or "", reverse=True)


def build_map(launch_list, center, zoom, mode="upcoming", draw_arcs=True, show_iss=False):
    m = folium.Map(location=center, zoom_start=zoom, tiles=None)

    # Inject dark-mode CSS directly inside the Folium map iframe for zoom & attribution controls
    dark_controls_css = """
    <style>
    .leaflet-control-zoom a {
        background-color: #050e0a !important;
        color: #00ff66 !important;
        border-color: rgba(0, 255, 102, 0.4) !important;
    }
    .leaflet-control-zoom a:hover {
        background-color: #0d241a !important;
    }
    .leaflet-control-attribution {
        background: rgba(5, 14, 10, 0.85) !important;
        color: #6ee7b7 !important;
        font-family: monospace !important;
        font-size: 10px !important;
    }
    .leaflet-control-attribution a {
        color: #00ff66 !important;
    }
    </style>
    """
    m.get_root().header.add_child(folium.Element(dark_controls_css))

    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}",
        attr="Esri",
    ).add_to(m)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}",
        attr="Esri",
        overlay=True,
    ).add_to(m)

    # Optional Live ISS Position & 90-Minute Orbital Ground Track
    if show_iss:
        try:
            iss_pts = fetch_iss_telemetry()
            if iss_pts:
                # Split ground track if it crosses the international date line (+/-180 lon)
                segments = [[]]
                for pt in iss_pts:
                    coord = [pt["latitude"], pt["longitude"]]
                    if segments[-1] and abs(coord[1] - segments[-1][-1][1]) > 180:
                        segments.append([])
                    segments[-1].append(coord)

                for seg in segments:
                    if len(seg) > 1:
                        folium.PolyLine(
                            locations=seg,
                            color="#00ff66",
                            weight=2.5,
                            opacity=0.85,
                            dash_array="4, 6",
                            tooltip="ISS 90-Min Orbital Ground Track",
                        ).add_to(m)

                cur_iss = iss_pts[len(iss_pts) // 2]
                folium.Marker(
                    location=[cur_iss["latitude"], cur_iss["longitude"]],
                    tooltip=(
                        f"ISS LIVE // ALT: {cur_iss['altitude']:.1f} km | "
                        f"VEL: {cur_iss['velocity']:,.0f} km/h"
                    ),
                    icon=folium.Icon(color="cadetblue", icon="globe", prefix="fa"),
                ).add_to(m)
        except Exception:
            pass

    cluster = MarkerCluster(
        options={
            "spiderfyOnMaxZoom": True,
            "showCoverageOnHover": False,
            "zoomToBoundsOnClick": True,
            "maxClusterRadius": 25,
            "spiderfyDistanceMultiplier": 1.6,
        }
    ).add_to(m)

    for item in launch_list:
        pad = item.get("pad") or {}
        lat, lon = pad.get("latitude"), pad.get("longitude")
        if lat is None or lon is None:
            continue
        lat_f, lon_f = float(lat), float(lon)
        status_abbr = (item.get("status") or {}).get("abbrev", "TBD")
        orbit_name = ((item.get("mission") or {}).get("orbit") or {}).get("name", "")

        if mode == "upcoming":
            color, icon_name, _, label, hex_col = get_upcoming_pin(status_abbr, item.get("net"))
        else:
            color, icon_name, label, hex_col = get_archive_pin(status_abbr)

        if draw_arcs:
            arc_pts = compute_trajectory(lat_f, lon_f, orbit_name)
            folium.PolyLine(
                locations=arc_pts,
                color=hex_col,
                weight=2,
                opacity=0.65,
                dash_array="6, 8",
                tooltip=f"Azimuth Vector: {item['name']} ({orbit_name or 'Orbit TBD'})",
            ).add_to(m)

        folium.Marker(
            location=[lat_f, lon_f],
            tooltip=f"{item['name']} — {label}",
            icon=folium.Icon(color=color, icon=icon_name, prefix="fa"),
        ).add_to(cluster)
    return m


def plot_stock_with_launches(stock_data, provider_name, archive_launches):
    hist = stock_data["hist"]
    if hist.empty:
        st.warning("NO HISTORICAL PRICE DATA RETURNED.")
        return

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=hist.index,
            y=hist["Close"],
            mode="lines",
            name=stock_data["ticker"],
            line=dict(color="#00ff66", width=2),
            fill="tozeroy",
            fillcolor="rgba(0, 255, 102, 0.08)",
        )
    )

    # Overlay vertical markers for that provider's actual launches over the past year
    min_date = hist.index.min().tz_localize(None) if hist.index.tz else hist.index.min()
    max_date = hist.index.max().tz_localize(None) if hist.index.tz else hist.index.max()

    matched_events = []
    for item in archive_launches:
        prov = (item.get("launch_service_provider") or {}).get("name", "")
        if provider_name.lower() in prov.lower():
            dt = parse_utc(item.get("net"))
            if dt:
                dt_naive = dt.replace(tzinfo=None)
                if min_date <= dt_naive <= max_date:
                    abbr = (item.get("status") or {}).get("abbrev", "")
                    is_fail = abbr in ("Failure", "Partial Failure")
                    matched_events.append((dt_naive, item.get("name", "Launch"), is_fail))

    for ev_dt, ev_name, is_fail in matched_events[:25]:
        line_col = "#ef4444" if is_fail else "rgba(56, 189, 248, 0.55)"
        fig.add_vline(
            x=ev_dt,
            line_width=1.5 if is_fail else 1,
            line_dash="dash",
            line_color=line_col,
        )

    fig.update_layout(
        title=dict(
            text=f"{stock_data['ticker']} 1Y PRICE // BLUE = LAUNCH | RED = ANOMALY ({len(matched_events)} EVENTS)",
            font=dict(size=11, color="#6ee7b7", family="JetBrains Mono, monospace"),
        ),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(3,7,6,0.6)",
        font=dict(color="#d1fae5", family="JetBrains Mono, monospace", size=10),
        margin=dict(l=10, r=10, t=32, b=10),
        height=235,
        xaxis=dict(showgrid=False),
        yaxis=dict(showgrid=True, gridcolor="rgba(0,255,102,0.1)"),
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)


def render_inspector(selected, tz_label, archive_pool, allow_ics=False):
    with st.container(border=True):
        provider = selected.get("launch_service_provider") or {}
        provider_name = provider.get("name", "UNKNOWN")
        status = selected.get("status") or {}
        mission = selected.get("mission") or {}
        rocket = selected.get("rocket") or {}
        config = rocket.get("configuration") or {}
        builder = config.get("manufacturer") or {}

        logo_url = get_image_url(provider.get("logo"))
        rocket_img = get_image_url(selected.get("image"))

        h1, h2 = st.columns([1, 3.2])
        with h1:
            if logo_url:
                st.image(logo_url, width=82)
        with h2:
            st.markdown(f"### > {selected.get('name', 'UNKNOWN MISSION')}")
            st.markdown(
                f'<span class="countdown-readout">{format_countdown(selected.get("net"), tz_label)}</span> '
                f'<span class="status-tag">{status.get("name", "TBD").upper()}</span>',
                unsafe_allow_html=True,
            )

        vid_urls = selected.get("vid_urls") or []
        has_vid = bool(vid_urls and isinstance(vid_urls[0], dict) and vid_urls[0].get("url"))
        ics_bytes = build_ics_file(selected) if allow_ics else None

        if has_vid and ics_bytes:
            b1, b2 = st.columns(2)
            with b1:
                st.link_button("▶ LIVE WEBCAST", vid_urls[0]["url"], use_container_width=True)
            with b2:
                st.download_button(
                    "+ CALENDAR (.ICS)",
                    data=ics_bytes,
                    file_name=f"{selected.get('slug', 'launch')}.ics",
                    mime="text/calendar",
                    use_container_width=True,
                )
        elif has_vid:
            st.link_button("▶ WATCH WEBCAST", vid_urls[0]["url"], use_container_width=True)
        elif ics_bytes:
            st.download_button(
                "+ ADD TO CALENDAR (.ICS)",
                data=ics_bytes,
                file_name=f"{selected.get('slug', 'launch')}.ics",
                mime="text/calendar",
                use_container_width=True,
            )

        t_mission, t_rocket, t_company, t_parts, t_market = st.tabs(
            ["[01] MISSION", "[02] VEHICLE", "[03] AGENCY", "[04] HARDWARE", "[05] MARKET"]
        )

        with t_mission:
            if rocket_img:
                st.image(rocket_img, use_container_width=True)
            st.write(f"**MISSION_TYPE:** {mission.get('type', 'UNCLASSIFIED / TBD')}")
            st.write(f"**TARGET_ORBIT:** {(mission.get('orbit') or {}).get('name', 'TBD')}")
            st.write(f"**T_ZERO_LOCAL:** {format_local_time(selected.get('net'), tz_label)}")
            st.write(
                f"**LAUNCH_COMPLEX:** {((selected.get('pad') or {}).get('location') or {}).get('name', 'UNKNOWN')}"
            )
            st.info(mission.get("description") or "NO MISSION BRIEFING IN TELEMETRY STREAM.")

        with t_rocket:
            st.write(f"**VEHICLE_ID:** {config.get('full_name', 'UNKNOWN')}")
            st.write(f"**VARIANT:** {config.get('variant', 'STANDARD')}")
            st.write(f"**MANUFACTURER:** {builder.get('name', 'UNKNOWN')}")
            if config.get("leo_capacity"):
                st.write(f"**PAYLOAD_LEO:** {config['leo_capacity']:,} kg")
            if config.get("gto_capacity"):
                st.write(f"**PAYLOAD_GTO:** {config['gto_capacity']:,} kg")
            if config.get("length"):
                st.write(f"**AIRFRAME_HEIGHT:** {config['length']} m")
            if config.get("description"):
                st.write(config["description"])

        with t_company:
            st.write(f"**OPERATOR:** {provider_name}")
            st.write(f"**CLASSIFICATION:** {(provider.get('type') or {}).get('name', 'COMMERCIAL / GOV')}")
            if provider.get("founding_year"):
                st.write(f"**ESTABLISHED:** {provider['founding_year']}")

            total = provider.get("total_launch_count") or 0
            success = provider.get("successful_launches") or 0
            failed = provider.get("failed_launches") or 0
            streak = provider.get("consecutive_successful_launches") or 0

            m1, m2, m3 = st.columns(3)
            m1.metric("TOTAL_FLIGHTS", total)
            m2.metric("RELIABILITY", f"{(success / total * 100):.1f}%" if total > 0 else "N/A")
            m3.metric("WIN_STREAK", streak)

            if failed:
                st.caption(f"RECORD: {success} NOMINAL // {failed} ANOMALY")
            if provider.get("description"):
                st.write(provider["description"])

        with t_parts:
            stages = rocket.get("launcher_stage") or []
            capsules = rocket.get("spacecraft_stage") or []

            if stages:
                st.markdown("**BOOSTER_CORES**")
                for stage in stages:
                    core = stage.get("launcher") or {}
                    serial = core.get("serial_number") or "EXPENDABLE / UNLISTED"
                    flights = stage.get("launcher_flight_number") or 1
                    st.write(f"- **CORE `{serial}`** // FLIGHT #{flights}")
                    landing = stage.get("landing") or {}
                    if landing.get("attempt"):
                        loc = (landing.get("landing_location") or {}).get("name", "TBD")
                        st.caption(f"  RECOVERY_ZONE: {loc}")

            if capsules:
                st.markdown("**SPACECRAFT_PAYLOAD**")
                for cap in capsules:
                    craft = cap.get("spacecraft") or {}
                    craft_maker = (
                        (craft.get("spacecraft_config") or {}).get("agency") or {}
                    ).get("name", "UNKNOWN")
                    st.write(f"- **{craft.get('name', 'SPACECRAFT')}** // MFR: {craft_maker}")

            if not stages and not capsules:
                st.info("BOOSTER SERIALS & CAPSULE TELEMETRY NOT YET ASSIGNED.")

        with t_market:
            mkt_entry = None
            for key_name, val in PROVIDER_MARKET_MAP.items():
                if key_name.lower() in provider_name.lower():
                    mkt_entry = val
                    break

            if not mkt_entry:
                st.info(f"[{provider_name.upper()}] // NO PUBLIC EQUITY MAPPING CONFIGURED.")
            elif mkt_entry["type"] in ("PRIVATE", "STATE"):
                st.warning(f"ENTITY_STATUS: [{mkt_entry['type']}] — NOT PUBLICLY TRADED")
                st.write(f"**VALUATION / BUDGET:** {mkt_entry['est_val']}")
                st.caption(mkt_entry["note"])
            else:
                st.caption(mkt_entry["note"])
                tickers = mkt_entry["tickers"]
                chosen_tk = tickers[0]
                if len(tickers) > 1:
                    chosen_tk = st.radio("SELECT_PARENT_EQUITY", tickers, horizontal=True)

                try:
                    with st.spinner(f"FETCHING {chosen_tk} MARKET TELEMETRY..."):
                        sdata = fetch_stock_bundle(chosen_tk)

                    c1, c2, c3 = st.columns(3)
                    c1.metric(
                        f"{chosen_tk} PRICE",
                        f"${sdata['price']:,.2f}" if sdata["price"] else "N/A",
                        f"{sdata['day_chg_pct']:+.2f}%",
                    )
                    c2.metric("MARKET_CAP", sdata["market_cap"])
                    c3.metric("P/E_RATIO", sdata["pe"])

                    c4, c5, c6 = st.columns(3)
                    c4.metric("REV_GROWTH", sdata["rev_growth"])
                    c5.metric("TOTAL_CASH", sdata["cash"])
                    c6.metric("CASH_RUNWAY", sdata["runway"])

                    plot_stock_with_launches(sdata, provider_name, archive_pool)
                except Exception as e:
                    st.error(f"[MKT_FEED_ERR]: Could not load {chosen_tk} ({e})")


# --- TOP TERMINAL HEADER BAR ---
hdr1, hdr2, hdr3 = st.columns([1.8, 1.8, 0.8])
with hdr1:
    st.markdown(
        '<div class="term-header">'
        '<div class="term-title">&gt;_ T-MINUS TELEMETRY<span class="cursor-blink">_</span></div>'
        '<div class="term-sub">SYS.LINK: LL2_ORBITAL + NYSE/NASDAQ_FEED // LIVE_ISS: READY</div>'
        '</div>',
        unsafe_allow_html=True,
    )
with hdr2:
    st.write("")
    view_mode = st.radio(
        "MODE",
        ["UPCOMING_QUEUE", "HISTORICAL_ARCHIVE", "ORBITAL_MARKETS"],
        horizontal=True,
        label_visibility="collapsed",
    )
with hdr3:
    st.write("")
    tz_choice = st.selectbox("TZ", list(TIMEZONES.keys()), index=0, label_visibility="collapsed")

# Pre-load 2026 launches for event-overlay correlation in Market views
try:
    ytd_2026_pool = fetch_year_launches(2026)
except Exception:
    ytd_2026_pool = []

# ==========================================
# VIEW 1: UPCOMING LAUNCHES
# ==========================================
if view_mode == "UPCOMING_QUEUE":
    leg_col, iss_col = st.columns([3.5, 1.1])
    with leg_col:
        st.caption(
            "LEGEND: [<24H] GREEN  |  [<1WK] AMBER  |  [>1WK] BLUE  |  [HOLD] RED  |  DASHED ARC = ORBITAL AZIMUTH"
        )
    with iss_col:
        show_iss_up = st.toggle("OVERLAY_LIVE_ISS", value=False, key="iss_up")

    try:
        with st.spinner("ACQUIRING ORBITAL TELEMETRY STREAM..."):
            upcoming = fetch_upcoming()
    except Exception as e:
        st.error(f"[SYS.ERR // TELEMETRY_LINK_TIMEOUT]: {e}")
        if st.button("RETRY_CONNECTION"):
            st.cache_data.clear()
            st.rerun()
        st.stop()

    if not upcoming:
        st.warning("NO UPCOMING LAUNCHES IN QUEUE.")
        st.stop()

    st.session_state.setdefault("up_selected", upcoming[0]["id"])
    st.session_state.setdefault("up_center", [20.0, 0.0])
    st.session_state.setdefault("up_zoom", 2)

    col_map, col_details = st.columns([1.55, 1.15], gap="large")

    with col_map:
        m = build_map(
            upcoming,
            st.session_state.up_center,
            st.session_state.up_zoom,
            mode="upcoming",
            draw_arcs=True,
            show_iss=show_iss_up,
        )
        map_state = st_folium(m, use_container_width=True, height=430, key="map_upcoming")

        clicked_tip = (map_state or {}).get("last_object_clicked_tooltip") or ""
        if clicked_tip and " — " in clicked_tip:
            name = clicked_tip.split(" — ")[0]
            match = next((x for x in upcoming if x["name"] == name), None)
            if match and match["id"] != st.session_state.up_selected:
                st.session_state.up_selected = match["id"]
                st.rerun()

        h_left, h_right = st.columns([3, 1])
        with h_left:
            st.markdown("#### > CHRONOLOGICAL_FLIGHT_QUEUE")
        with h_right:
            if st.button("RESET_MAP", key="reset_up", use_container_width=True):
                st.session_state.up_center = [20.0, 0.0]
                st.session_state.up_zoom = 2
                st.rerun()

        with st.container(height=275, border=True):
            for idx, item in enumerate(upcoming, start=1):
                abbr = (item.get("status") or {}).get("abbrev", "TBD")
                _, _, badge, _, _ = get_upcoming_pin(abbr, item.get("net"))
                prov = (item.get("launch_service_provider") or {}).get("name", "UNKNOWN")
                active = item["id"] == st.session_state.up_selected

                r1, r2, r3 = st.columns([3.2, 1.6, 1.0])
                with r1:
                    st.markdown(f"**{'>> ' if active else ''}[{idx:02d}] {item['name']}**")
                    st.caption(f"{prov} // {format_local_time(item.get('net'), tz_choice)}")
                with r2:
                    st.markdown(f"`{badge}` `{format_countdown(item.get('net'), tz_choice)}`")
                with r3:
                    if st.button(
                        "LOCK" if not active else "ACTIVE",
                        key=f"up_btn_{item['id']}",
                        type="primary" if active else "secondary",
                        use_container_width=True,
                    ):
                        st.session_state.up_selected = item["id"]
                        pad = item.get("pad") or {}
                        if pad.get("latitude") and pad.get("longitude"):
                            st.session_state.up_center = [float(pad["latitude"]), float(pad["longitude"])]
                            st.session_state.up_zoom = 6
                        st.rerun()

    selected = next((x for x in upcoming if x["id"] == st.session_state.up_selected), upcoming[0])
    with col_details:
        render_inspector(selected, tz_choice, ytd_2026_pool, allow_ics=True)

# ==========================================
# VIEW 2: MULTI-YEAR & AGGREGATED HISTORICAL ARCHIVE
# ==========================================
elif view_mode == "HISTORICAL_ARCHIVE":
    f0, f1, f2, f3 = st.columns([1.3, 2.1, 0.9, 0.9])
    with f0:
        period_options = [
            "2026 (YTD)",
            "2025 (FULL YEAR)",
            "2024 (FULL YEAR)",
            "2023 (FULL YEAR)",
            "ALL YEARS (2023-2026 AGGREGATED)",
        ]
        selected_period = st.selectbox("TIME_WINDOW", options=period_options, index=0)

    is_aggregated = "ALL YEARS" in selected_period

    try:
        if is_aggregated:
            with st.spinner("COMPILING MULTI-YEAR ORBITAL ARCHIVE (2023-2026)..."):
                raw_archive = []
                for yr in ARCHIVE_YEARS:
                    raw_archive.extend(fetch_year_launches(yr))
                raw_archive.sort(key=lambda x: x.get("net") or "", reverse=True)
        else:
            chosen_year = int(selected_period[:4])
            with st.spinner(f"LOADING {chosen_year} TELEMETRY LOGS..."):
                raw_archive = fetch_year_launches(chosen_year)
    except Exception as e:
        st.error(f"[SYS.ERR // ARCHIVE_FEED_TIMEOUT]: {e}")
        if st.button("RETRY_ARCHIVE_SYNC"):
            st.cache_data.clear()
            st.rerun()
        st.stop()

    if not raw_archive:
        st.warning("NO ARCHIVE RECORDS RETURNED FOR THIS WINDOW.")
        st.stop()

    all_providers = sorted(
        {(x.get("launch_service_provider") or {}).get("name", "Unknown") for x in raw_archive}
    )
    with f1:
        chosen_providers = st.multiselect(
            "FILTER_BY_AGENCY",
            options=all_providers,
            default=[],
            placeholder="ALL AGENCIES (SPACEX, CASC, ROCKET LAB...)",
        )
    with f2:
        st.write("")
        anomalies_only = st.checkbox("ANOMALIES ONLY")
    with f3:
        st.write("")
        show_iss_arch = st.toggle("LIVE_ISS", value=False, key="iss_arch")

    filtered_launches = [
        x
        for x in raw_archive
        if (not chosen_providers or (x.get("launch_service_provider") or {}).get("name") in chosen_providers)
        and (not anomalies_only or (x.get("status") or {}).get("abbrev") in ("Failure", "Partial Failure"))
    ]

    if not filtered_launches:
        st.info("NO FLIGHTS MATCH CURRENT FILTER CRITERIA.")
        st.stop()

    st.session_state.setdefault("arch_selected", filtered_launches[0]["id"])
    st.session_state.setdefault("arch_center", [20.0, 0.0])
    st.session_state.setdefault("arch_zoom", 2)

    col_map, col_details = st.columns([1.55, 1.15], gap="large")

    with col_map:
        m_arch = build_map(
            filtered_launches,
            st.session_state.arch_center,
            st.session_state.arch_zoom,
            mode="archive",
            draw_arcs=not is_aggregated,
            show_iss=show_iss_arch,
        )
        arch_map_state = st_folium(m_arch, use_container_width=True, height=360, key="map_archive")

        clicked_tip = (arch_map_state or {}).get("last_object_clicked_tooltip") or ""
        if clicked_tip and " — " in clicked_tip:
            name = clicked_tip.split(" — ")[0]
            match = next((x for x in filtered_launches if x["name"] == name), None)
            if match and match["id"] != st.session_state.arch_selected:
                st.session_state.arch_selected = match["id"]
                st.rerun()

        rows = []
        booster_counts = {}
        for item in filtered_launches:
            pad = item.get("pad") or {}
            mission = item.get("mission") or {}
            rocket = item.get("rocket") or {}
            config = rocket.get("configuration") or {}
            dt = parse_utc(item.get("net"))

            # Extract payload mass (kg) if available
            leo_cap = config.get("leo_capacity") or 0

            # Track booster serial reuse flights
            for stg in rocket.get("launcher_stage") or []:
                serial = (stg.get("launcher") or {}).get("serial_number")
                flt = stg.get("launcher_flight_number") or 1
                if serial:
                    booster_counts[serial] = max(booster_counts.get(serial, 0), flt)

            rows.append(
                {
                    "Date": format_local_time(item.get("net"), tz_choice),
                    "Year": str(dt.year) if dt else "UNK",
                    "Month": dt.strftime("%Y-%m") if dt else "UNK",
                    "Mission": item.get("name", "Unknown"),
                    "Provider": (item.get("launch_service_provider") or {}).get("name", "Unknown"),
                    "Rocket": config.get("name", "Unknown"),
                    "Outcome": (item.get("status") or {}).get("name", "Unknown"),
                    "Orbit": (mission.get("orbit") or {}).get("name", "TBD"),
                    "Country": extract_country(pad),
                    "Est_Capacity_kg": leo_cap,
                }
            )
        df = pd.DataFrame(rows)

        total_cnt = len(df)
        succ_cnt = (df["Outcome"] == "Launch Successful").sum()
        fail_cnt = df["Outcome"].isin(["Launch Failure", "Launch Partial Failure"]).sum()
        total_mass_tons = df["Est_Capacity_kg"].sum() / 1000.0

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("WINDOW_FLIGHTS", total_cnt)
        k2.metric("SUCCESS_RATE", f"{(succ_cnt / total_cnt * 100):.1f}%" if total_cnt else "N/A")
        k3.metric("ANOMALIES", fail_cnt)
        k4.metric("LIFT_CAPACITY_T", f"{total_mass_tons:,.0f} t")

        t_head1, t_head2, t_head3 = st.columns([2.4, 1.0, 1.2])
        with t_head1:
            label_tag = "2023-2026_AGGREGATED" if is_aggregated else f"{selected_period[:4]}_FLIGHT_LOG"
            st.markdown(f"#### > {label_tag} ({total_cnt} RECORDS)")
        with t_head2:
            if st.button("RESET_MAP", key="reset_arch", use_container_width=True):
                st.session_state.arch_center = [20.0, 0.0]
                st.session_state.arch_zoom = 2
                st.rerun()
        with t_head3:
            st.download_button(
                "EXPORT_CSV",
                data=df.drop(columns=["Month"]).to_csv(index=False).encode("utf-8"),
                file_name=f"tminus_{label_tag.lower()}.csv",
                mime="text/csv",
                use_container_width=True,
            )

        display_cols = ["Date", "Year", "Mission", "Provider", "Rocket", "Outcome", "Orbit", "Country"]
        if not is_aggregated:
            display_cols.remove("Year")

        event = st.dataframe(
            df[display_cols],
            use_container_width=True,
            height=245,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
        )

        selected_rows = event.selection.rows if event and event.selection else []
        if selected_rows:
            chosen = filtered_launches[selected_rows[0]]
            if chosen["id"] != st.session_state.arch_selected:
                st.session_state.arch_selected = chosen["id"]
                pad = chosen.get("pad") or {}
                if pad.get("latitude") and pad.get("longitude"):
                    st.session_state.arch_center = [float(pad["latitude"]), float(pad["longitude"])]
                    st.session_state.arch_zoom = 6
                st.rerun()

        with st.expander(f"[ + ] {label_tag} // ANALYTICS, MASS-TO-ORBIT & BOOSTER FLEET", expanded=False):
            c1, c2, c3 = st.columns(3)
            with c1:
                st.caption("TOP PROVIDERS (FLIGHTS)")
                st.bar_chart(df["Provider"].value_counts().head(8), color="#00ff66", height=200)
            with c2:
                st.caption("LIFT CAPACITY BY AGENCY (TONS)")
                mass_by_prov = (df.groupby("Provider")["Est_Capacity_kg"].sum() / 1000.0).sort_values(ascending=False).head(8)
                st.bar_chart(mass_by_prov, color="#ffb000", height=200)
            with c3:
                if is_aggregated:
                    st.caption("ANNUAL LAUNCH CADENCE")
                    st.bar_chart(df["Year"].value_counts().sort_index(), color="#38bdf8", height=200)
                else:
                    st.caption("LAUNCHES BY COUNTRY")
                    st.bar_chart(df["Country"].value_counts().head(8), color="#38bdf8", height=200)

            if booster_counts:
                st.markdown("**MOST FLOWN REUSABLE BOOSTER CORES IN WINDOW**")
                top_boosters = pd.DataFrame(
                    sorted(booster_counts.items(), key=lambda x: x[1], reverse=True)[:10],
                    columns=["Booster Serial", "Max Recorded Flight #"],
                )
                st.dataframe(top_boosters, use_container_width=True, hide_index=True, height=180)

    selected_arch = next(
        (x for x in filtered_launches if x["id"] == st.session_state.arch_selected),
        filtered_launches[0],
    )
    with col_details:
        render_inspector(selected_arch, tz_choice, raw_archive, allow_ics=False)

# ==========================================
# VIEW 3: ORBITAL MARKETS & AEROSPACE FINANCIALS
# ==========================================
else:
    st.caption(
        "PUBLIC SPACE SECTOR TELEMETRY // CLICK ANY TICKER ROW TO INSPECT 1-YEAR PRICE & LAUNCH EVENT OVERLAY"
    )

    sector_rows = []
    sector_cache = {}
    with st.spinner("PULLING LIVE EQUITY TELEMETRY (NYSE / NASDAQ / TSE)..."):
        for tk, label in SECTOR_WATCHLIST:
            try:
                sd = fetch_stock_bundle(tk)
                sector_cache[tk] = sd
                sector_rows.append(
                    {
                        "Ticker": tk,
                        "Company": label,
                        "Price": f"${sd['price']:,.2f}" if sd["price"] else "N/A",
                        "Day Chg": f"{sd['day_chg_pct']:+.2f}%",
                        "Market Cap": sd["market_cap"],
                        "P/E": sd["pe"],
                        "Rev Growth": sd["rev_growth"],
                        "Cash": sd["cash"],
                        "Cash Runway": sd["runway"],
                    }
                )
            except Exception:
                sector_rows.append(
                    {
                        "Ticker": tk,
                        "Company": label,
                        "Price": "ERR",
                        "Day Chg": "N/A",
                        "Market Cap": "N/A",
                        "P/E": "N/A",
                        "Rev Growth": "N/A",
                        "Cash": "N/A",
                        "Cash Runway": "N/A",
                    }
                )

    df_mkt = pd.DataFrame(sector_rows)
    left_mkt, right_mkt = st.columns([1.45, 1.25], gap="large")

    with left_mkt:
        st.markdown("#### > PUBLIC_AEROSPACE_SECTOR_MATRIX")
        mkt_event = st.dataframe(
            df_mkt,
            use_container_width=True,
            height=360,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
        )
        st.download_button(
            "EXPORT_SECTOR_FINANCIALS_CSV",
            data=df_mkt.to_csv(index=False).encode("utf-8"),
            file_name="tminus_orbital_markets.csv",
            mime="text/csv",
        )

    sel_idx = 0
    if mkt_event and mkt_event.selection and mkt_event.selection.rows:
        sel_idx = mkt_event.selection.rows[0]

    active_tk, active_label = SECTOR_WATCHLIST[sel_idx]
    with right_mkt:
        with st.container(border=True):
            st.markdown(f"### > {active_label.upper()} ({active_tk})")
            if active_tk in sector_cache:
                sd = sector_cache[active_tk]
                m1, m2, m3 = st.columns(3)
                m1.metric("LAST_PRICE", f"${sd['price']:,.2f}" if sd["price"] else "N/A", f"{sd['day_chg_pct']:+.2f}%")
                m2.metric("MARKET_CAP", sd["market_cap"])
                m3.metric("CASH_RUNWAY", sd["runway"])

                # Match ticker back to provider name for launch event overlays
                prov_search = active_label.split(" ")[0]
                if active_tk in ("BA", "LMT"):
                    prov_search = "United Launch Alliance"
                elif active_tk == "7011.T":
                    prov_search = "Mitsubishi"

                plot_stock_with_launches(sd, prov_search, ytd_2026_pool)
            else:
                st.warning("TELEMETRY UNAVAILABLE FOR SELECTED TICKER.")