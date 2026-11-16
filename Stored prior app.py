from datetime import datetime, timedelta, timezone
import math
from zoneinfo import ZoneInfo
import folium
from folium.plugins import MarkerCluster
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
import streamlit as st
from streamlit_folium import st_folium
from urllib3.util.retry import Retry

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


def get_resilient_session():
    # Automatically retries transient timeouts / 502-504 server hiccups
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


def build_map(launch_list, center, zoom, mode="upcoming", draw_arcs=True):
    m = folium.Map(location=center, zoom_start=zoom, tiles=None)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}",
        attr="Esri",
    ).add_to(m)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}",
        attr="Esri",
        overlay=True,
    ).add_to(m)

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


def render_inspector(selected, tz_label, allow_ics=False):
    with st.container(border=True):
        provider = selected.get("launch_service_provider") or {}
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

        t_mission, t_rocket, t_company, t_parts = st.tabs(
            ["[01] MISSION", "[02] VEHICLE", "[03] AGENCY", "[04] HARDWARE"]
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
            st.write(f"**OPERATOR:** {provider.get('name', 'UNKNOWN')}")
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


# --- TOP TERMINAL HEADER BAR ---
hdr1, hdr2, hdr3 = st.columns([2.1, 1.4, 0.9])
with hdr1:
    st.markdown(
        '<div class="term-header">'
        '<div class="term-title">&gt;_ T-MINUS TELEMETRY<span class="cursor-blink">_</span></div>'
        '<div class="term-sub">SYS.LINK: LL2_ORBITAL_FEED // MULTI-YEAR ARCHIVE: READY</div>'
        '</div>',
        unsafe_allow_html=True,
    )
with hdr2:
    st.write("")
    view_mode = st.radio(
        "MODE",
        ["UPCOMING_QUEUE", "HISTORICAL_ARCHIVE"],
        horizontal=True,
        label_visibility="collapsed",
    )
with hdr3:
    st.write("")
    tz_choice = st.selectbox("TZ", list(TIMEZONES.keys()), index=0, label_visibility="collapsed")

# ==========================================
# VIEW 1: UPCOMING LAUNCHES
# ==========================================
if view_mode == "UPCOMING_QUEUE":
    st.caption(
        "LEGEND: [<24H] GREEN  |  [<1WK] AMBER  |  [>1WK] BLUE  |  [HOLD] RED  |  DASHED ARC = ORBITAL AZIMUTH"
    )
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
        m = build_map(upcoming, st.session_state.up_center, st.session_state.up_zoom, mode="upcoming", draw_arcs=True)
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
        render_inspector(selected, tz_choice, allow_ics=True)

# ==========================================
# VIEW 2: MULTI-YEAR & AGGREGATED HISTORICAL ARCHIVE
# ==========================================
else:
    f0, f1, f2 = st.columns([1.4, 2.5, 1.1])
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
            placeholder="ALL AGENCIES (TYPE TO FILTER: SPACEX, CASC, ROCKET LAB...)",
        )
    with f2:
        st.write("")
        anomalies_only = st.checkbox("ANOMALIES ONLY")

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
        )
        arch_map_state = st_folium(m_arch, use_container_width=True, height=370, key="map_archive")

        clicked_tip = (arch_map_state or {}).get("last_object_clicked_tooltip") or ""
        if clicked_tip and " — " in clicked_tip:
            name = clicked_tip.split(" — ")[0]
            match = next((x for x in filtered_launches if x["name"] == name), None)
            if match and match["id"] != st.session_state.arch_selected:
                st.session_state.arch_selected = match["id"]
                st.rerun()

        rows = []
        for item in filtered_launches:
            pad = item.get("pad") or {}
            mission = item.get("mission") or {}
            dt = parse_utc(item.get("net"))
            rows.append(
                {
                    "Date": format_local_time(item.get("net"), tz_choice),
                    "Year": str(dt.year) if dt else "UNK",
                    "Month": dt.strftime("%Y-%m") if dt else "UNK",
                    "Mission": item.get("name", "Unknown"),
                    "Provider": (item.get("launch_service_provider") or {}).get("name", "Unknown"),
                    "Rocket": ((item.get("rocket") or {}).get("configuration") or {}).get("name", "Unknown"),
                    "Outcome": (item.get("status") or {}).get("name", "Unknown"),
                    "Orbit": (mission.get("orbit") or {}).get("name", "TBD"),
                    "Country": extract_country(pad),
                }
            )
        df = pd.DataFrame(rows)

        total_cnt = len(df)
        succ_cnt = (df["Outcome"] == "Launch Successful").sum()
        fail_cnt = df["Outcome"].isin(["Launch Failure", "Launch Partial Failure"]).sum()
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("WINDOW_FLIGHTS", total_cnt)
        k2.metric("NOMINAL_SUCCESS", succ_cnt)
        k3.metric("ANOMALIES", fail_cnt)
        k4.metric("SUCCESS_RATE", f"{(succ_cnt / total_cnt * 100):.1f}%" if total_cnt else "N/A")

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
            height=255,
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

        with st.expander(f"[ + ] {label_tag} // ANALYTICS & LEADERBOARD", expanded=False):
            c1, c2, c3 = st.columns(3)
            with c1:
                st.caption("TOP PROVIDERS (FLIGHTS)")
                st.bar_chart(df["Provider"].value_counts().head(8), color="#00ff66", height=210)
            with c2:
                st.caption("LAUNCHES BY COUNTRY")
                st.bar_chart(df["Country"].value_counts().head(8), color="#ffb000", height=210)
            with c3:
                if is_aggregated:
                    st.caption("ANNUAL LAUNCH CADENCE (2023-2026)")
                    st.bar_chart(df["Year"].value_counts().sort_index(), color="#38bdf8", height=210)
                else:
                    st.caption("MONTHLY LAUNCH CADENCE")
                    st.bar_chart(df["Month"].value_counts().sort_index(), color="#38bdf8", height=210)

    selected_arch = next(
        (x for x in filtered_launches if x["id"] == st.session_state.arch_selected),
        filtered_launches[0],
    )
    with col_details:
        render_inspector(selected_arch, tz_choice, allow_ics=False)