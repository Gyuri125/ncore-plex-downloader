import io
import shutil
import socket
from typing import List, Optional, Dict, Any
from pathlib import Path
import streamlit as st

from src.config.settings import get_settings, save_settings
from src.core.logger import setup_logging, get_log_text, clear_memory_logs
from src.core.orchestrator import Orchestrator
from src.models.media import MediaItem, MediaType
from src.services.diagnostic_service import DiagnosticService
from src.services.ncore_service import NcoreService
from src.services.tmdb_service import TMDBService
from src.services.torrent_service import TorrentService

# Naplózás inicializálása
setup_logging()

# Oldal konfiguráció (széles elrendezés, sötét téma, mobilbarát nézet)
st.set_page_config(
    page_title="Ncore Média Letöltő",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Egyedi stílusok a letisztult megjelenéshez (nagy gombok, kártyák, mobilbarát margók)
st.markdown(
    """
    <style>
    /* Fő konténer és betűméretek */
    .main .block-container {
        padding-top: 1.5rem;
        padding-bottom: 3rem;
        max-width: 1200px;
    }
    
    /* Fejléc stílus */
    .app-header {
        text-align: center;
        padding: 1.5rem 0 1rem 0;
        background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
        border-radius: 16px;
        margin-bottom: 2rem;
        border: 1px solid #334155;
    }
    .app-title {
        font-size: 2.2rem;
        font-weight: 800;
        color: #f8fafc;
        margin-bottom: 0.2rem;
    }
    .app-subtitle {
        font-size: 1.1rem;
        color: #94a3b8;
    }

    /* Film kártyák stílusa */
    .movie-card {
        background: #1e293b;
        border: 1px solid #334155;
        border-radius: 12px;
        padding: 1rem;
        margin-bottom: 1.5rem;
        transition: transform 0.2s, border-color 0.2s;
    }
    .movie-card:hover {
        border-color: #38bdf8;
        transform: translateY(-2px);
    }
    .movie-title {
        font-size: 1.3rem;
        font-weight: 700;
        color: #38bdf8;
        margin-bottom: 0.3rem;
    }
    .movie-meta {
        font-size: 0.95rem;
        color: #cbd5e1;
        margin-bottom: 0.6rem;
    }
    .movie-overview {
        font-size: 0.9rem;
        color: #94a3b8;
        line-height: 1.4;
        margin-bottom: 0.8rem;
    }

    /* Nagy gombok */
    div.stButton > button:first-child {
        width: 100%;
        border-radius: 10px;
        font-size: 1.05rem;
        font-weight: 700;
        padding: 0.6rem 1rem;
    }

    /* Mobil hálózati sáv */
    .mobile-hint {
        background: #0f172a;
        padding: 0.8rem;
        border-radius: 8px;
        border-left: 4px solid #38bdf8;
        font-size: 0.9rem;
        color: #cbd5e1;
        margin-bottom: 1rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource
def get_orchestrator() -> Orchestrator:
    orch = Orchestrator()
    if not hasattr(orch, "radar") or orch.radar is None:
        from src.core.movie_radar import MovieRadarManager
        orch.radar = MovieRadarManager()
    return orch


def get_local_ip() -> str:
    """Megkeresi a számítógép helyi Wi-Fi / LAN IP címét a telefonos eléréshez."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def get_disk_free_gb(path: Path) -> Optional[float]:
    """Megadja a meghajtó szabad kapacitását gigabájtban."""
    try:
        if path.exists():
            usage = shutil.disk_usage(path)
            return round(usage.free / (1024**3), 1)
    except Exception:
        pass
    return None



@st.dialog("📱 Mobil / Tablet Elérés (QR-kód)")
def show_mobile_qr_dialog(target_url: str):
    st.markdown("Irányítsd a telefonod vagy tableted kameráját az alábbi QR-kódra a felület azonnali megnyitásához:")
    try:
        import qrcode

        qr = qrcode.QRCode(
            version=1,
            box_size=8,
            border=2,
        )
        qr.add_data(target_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#0f172a", back_color="#ffffff")

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        st.image(buf.getvalue(), caption=f"Cím: {target_url}", use_container_width=True)
    except Exception as e:
        st.error(f"Nem sikerült a QR-kód generálása: {e}")
        st.code(target_url)


@st.dialog("🎬 Részletes Bemutató & Adatlap", width="large")
def show_movie_details_dialog(item: MediaItem, orchestrator: Orchestrator):

    # Nagy felbontású háttérkép ha elérhető
    if item.backdrop_url:
        st.image(item.backdrop_url, use_container_width=True)

    col_l, col_r = st.columns([1, 2])
    with col_l:
        if item.poster_url:
            st.image(item.poster_url, use_container_width=True)

        st.markdown("##### 🖼️ Borítóképek & Hivatkozások")
        if item.poster_url:
            st.markdown(f"📥 [Nagy felbontású poszter megnyitása]({item.poster_url})")
        if item.backdrop_url:
            st.markdown(f"🖼️ [Eredeti háttérkép (4K) megnyitása]({item.backdrop_url})")
        if item.imdb_id:
            st.markdown(f"⭐ [IMDb Adatlap ({item.imdb_id})](https://www.imdb.com/title/{item.imdb_id}/)")
        st.markdown(f"🌐 [TMDB Adatlap](https://www.themoviedb.org/{'movie' if item.media_type == MediaType.MOVIE else 'tv'}/{item.tmdb_id})")

    with col_r:
        type_txt = "🎬 Film" if item.media_type == MediaType.MOVIE else "📺 Sorozat"
        year_txt = f"({item.year})" if item.year else ""
        st.markdown(f"## {item.title_hu} {year_txt}")
        if item.title_en and item.title_en != item.title_hu:
            st.caption(f"Eredeti cím: *{item.title_en}*")

        meta = [f"**{type_txt}**"]
        if item.number_of_seasons:
            meta.append(f"📺 **{item.number_of_seasons} évad**")
        if item.rating:
            meta.append(f"⭐ **{item.rating}/10**")
        if item.runtime_minutes:
            meta.append(f"⏱️ **{item.runtime_minutes} perc**")
        if item.genres:
            meta.append(f"🎭 *{', '.join(item.genres)}*")
        st.markdown(" • ".join(meta))

        st.markdown("#### 📖 Teljes Szinopszis")
        st.markdown(item.overview or "Nincs leírás.")

        if item.director or item.cast:
            st.markdown("#### 🎭 Alkotók & Szereplők (Kattints a filmográfiáért)")
            creator_count = (1 if item.director else 0) + len(item.cast[:3]) if item.cast else (1 if item.director else 0)
            if creator_count > 0:
                creator_cols = st.columns(min(4, creator_count))
                c_idx = 0
                if item.director and c_idx < len(creator_cols):
                    with creator_cols[c_idx]:
                        if st.button(f"🎬 {item.director} (Rendező)", key=f"det_dir_{item.tmdb_id}", use_container_width=True):
                            show_person_dialog(item.director, orchestrator)
                    c_idx += 1
                if item.cast:
                    for act_name in item.cast[:3]:
                        if c_idx < len(creator_cols):
                            with creator_cols[c_idx]:
                                if st.button(f"🎭 {act_name}", key=f"det_act_{item.tmdb_id}_{c_idx}", use_container_width=True):
                                    show_person_dialog(act_name, orchestrator)
                            c_idx += 1

        # Előzetes videó beágyazása
        st.markdown("#### 🎬 Hivatalos Előzetes (Trailer)")
        if item.trailer_url:
            st.video(item.trailer_url)
        else:
            yt_query = f"{item.title_hu} {item.year or ''} előzetes trailer".replace(" ", "+")
            st.info("A filmes adatbázisban nincs közvetlen videolink, de a YouTube-on egy kattintással elérhető:")
            st.markdown(f"▶️ [🎬 **Hivatalos előzetes megnyitása a YouTube-on (Kattints ide)**](https://www.youtube.com/results?search_query={yt_query})")


    st.markdown("---")
    st.markdown("### 🚀 Letöltési Lehetőségek")
    dlg_col1, dlg_col2 = st.columns([1, 1])
    with dlg_col1:
        if st.button("⚡ Automata letöltés (Ajánlott)", key=f"dlg_auto_{item.tmdb_id}", type="primary", use_container_width=True):
            with st.spinner("Küldés qBittorrentnek..."):
                res = orchestrator.start_download_flow(item, prefer_hungarian=True)
                if res.get("success"):
                    st.success(f"🎉 **{res['message']}**")
                    st.balloons()
                else:
                    st.error(f"❌ {res.get('message')}")
    with dlg_col2:
        if st.button("📋 Kiadások lenyitása a keresőben", key=f"dlg_open_{item.tmdb_id}", use_container_width=True):
            st.session_state[f"show_releases_{item.tmdb_id}"] = True
            st.rerun()


@st.dialog("✨ Hasonló Filmek & Ajánlások", width="large")
def show_similar_dialog(item: MediaItem, orchestrator: Orchestrator):
    """Megjeleníti az adott filmhez/sorozathoz leginkább hasonló alkotásokat."""
    st.markdown(f"### ✨ Filmek, amik tetszeni fognak ehhez: *„{item.title_hu}”*")
    st.caption("A TMDB intelligens ajánló motorja által kiválasztott rokon alkotások azonnali nCore letöltéssel:")
    with st.spinner("Hasonló filmek keresése a TMDB-n..."):
        similar_items = orchestrator.get_similar_media(item, limit=6)
    if not similar_items:
        st.info("Nem találtunk közvetlenül hasonló címet ebben a kategóriában.")
        return

    s_cols = st.columns(2)
    for s_idx, s_item in enumerate(similar_items):
        with s_cols[s_idx % 2]:
            render_media_card(s_item, orchestrator, key_prefix=f"sim_{s_idx}_{s_item.tmdb_id}")


@st.dialog("📦 Filmgyűjtemény (Franchise / Saga)", width="large")
def show_collection_dialog(collection_id: int, orchestrator: Orchestrator):
    """Megjeleníti a teljes franchise részeit sorban, és egykattintásos teljes letöltést biztosít."""
    with st.spinner("Gyűjtemény részeinek felkutatása..."):
        coll = orchestrator.get_collection_details(collection_id)
    if not coll or not coll.get("parts"):
        st.error("Nem sikerült lekérni a gyűjtemény adatait a TMDB-ről.")
        return

    coll_name = coll.get("name", "Filmgyűjtemény")
    parts = coll.get("parts", [])

    c_coll_img, c_coll_info = st.columns([1, 2.5])
    with c_coll_img:
        if coll.get("poster_url"):
            st.image(coll["poster_url"], use_container_width=True)
    with c_coll_info:
        st.markdown(f"## 📦 {coll_name}")
        st.caption(f"Összesen: **{len(parts)} db film** időrendi sorrendben")
        if coll.get("overview"):
            st.markdown(f"<div style='font-size: 0.88rem; color: #94a3b8; margin-bottom: 12px;'>{coll['overview']}</div>", unsafe_allow_html=True)

        if st.button(f"⚡ Teljes gyűjtemény letöltése ({len(parts)} db film)", type="primary", use_container_width=True, key=f"btn_dl_all_coll_{collection_id}"):
            with st.spinner(f"A(z) {coll_name} filmjeinek beküldése a qBittorrentbe..."):
                res = orchestrator.download_collection_all(collection_id)
                if res.get("success"):
                    st.success(f"🎉 **{res['message']}**")
                    st.balloons()
                else:
                    st.error(f"❌ {res.get('message')}")

    st.markdown("<hr style='margin: 16px 0; border: none; border-top: 1px solid rgba(255,255,255,0.08);'/>", unsafe_allow_html=True)
    st.markdown(f"#### 🎬 A gyűjtemény részei időrendben ({len(parts)} rész):")

    part_cols = st.columns(2)
    for p_idx, p_item in enumerate(parts):
        with part_cols[p_idx % 2]:
            render_media_card(p_item, orchestrator, key_prefix=f"coll_{collection_id}_{p_idx}_{p_item.tmdb_id}")


@st.dialog("🎭 Színész / Rendező Filmográfia", width="large")
def show_person_dialog(person_name: str, orchestrator: Orchestrator):
    """Megjeleníti a kiválasztott színész vagy rendező legfontosabb alkotásait."""
    with st.spinner(f"{person_name} filmográfiájának betöltése a TMDB-ről..."):
        person_data = orchestrator.get_person_filmography(person_name, limit=8)
    if not person_data or not person_data.get("items"):
        st.info(f"Nem található részletes filmográfia ehhez a névhez: {person_name}")
        return

    p_name = person_data.get("name", person_name)
    dept = person_data.get("department", "Filmes alkotó")
    items = person_data.get("items", [])
    profile_url = person_data.get("profile_url")

    p_col1, p_col2 = st.columns([1, 3])
    with p_col1:
        if profile_url:
            st.image(profile_url, use_container_width=True)
    with p_col2:
        st.markdown(f"## 🎭 {p_name}")
        st.caption(f"Fő tevékenység: **{dept}** | Legnépszerűbb filmjei és sorozatai")

    st.markdown("<hr style='margin: 14px 0; border: none; border-top: 1px solid rgba(255,255,255,0.08);'/>", unsafe_allow_html=True)
    st.markdown(f"#### 🌟 {p_name} legnépszerűbb alkotásai ({len(items)} cím):")

    pr_cols = st.columns(2)
    for pr_idx, pr_item in enumerate(items):
        with pr_cols[pr_idx % 2]:
            render_media_card(pr_item, orchestrator, key_prefix=f"person_{pr_idx}_{pr_item.tmdb_id}")


def render_media_card(item: MediaItem, orchestrator: Orchestrator, key_prefix: str = "item"):
    """
    Közös, modern média kártya komponens a kereséshez, ajánlókhoz és a kívánságlistához.
    """
    with st.container():
        st.markdown('<div class="movie-card">', unsafe_allow_html=True)
        c_img, c_info = st.columns([1, 2])

        with c_img:
            if item.poster_url:
                st.image(item.poster_url, width="stretch")
            else:
                st.image("https://via.placeholder.com/300x450.png?text=Nincs+Kép", width="stretch")

        with c_info:
            type_tag = "🎬 Film" if item.media_type == MediaType.MOVIE else "📺 Sorozat"
            year_txt = f"({item.year})" if item.year else ""
            rating_txt = f"⭐ {item.rating}/10" if item.rating else ""
            in_plex = orchestrator.plex_service.has_media(item.title_hu, item.year)
            plex_url = orchestrator.settings.PLEX_SERVER_URL
            plex_badge = f"<a href='{plex_url}' target='_blank' style='background: #059669; color: white; padding: 2px 7px; border-radius: 4px; font-size: 0.72rem; text-decoration: none; margin-left: 6px;' title='Megnyitás a Plexben'>✅ Plexben van ▶️</a>" if in_plex else ""

            st.markdown(f"<div class='movie-title'>{item.title_hu} {year_txt}{plex_badge}</div>", unsafe_allow_html=True)
            genres_txt = f" | <i>{', '.join(item.genres[:3])}</i>" if item.genres else ""
            st.markdown(
                f"<div class='movie-meta'><b>{type_tag}</b> | {rating_txt}{genres_txt}</div>",
                unsafe_allow_html=True,
            )

            if item.director:
                c_dir_txt, c_dir_btn = st.columns([3, 1.2])
                with c_dir_txt:
                    st.caption(f"🎬 **Rendező:** {item.director}")
                with c_dir_btn:
                    if st.button("🎭 Filmjei", key=f"{key_prefix}_dir_{item.tmdb_id}", help=f"{item.director} összes filmjének listázása"):
                        show_person_dialog(item.director, orchestrator)
            elif item.cast:
                c_act_txt, c_act_btn = st.columns([3, 1.2])
                with c_act_txt:
                    st.caption(f"🎭 **Szereplők:** {', '.join(item.cast[:2])}")
                with c_act_btn:
                    if st.button("🎭 Filmjei", key=f"{key_prefix}_act_{item.tmdb_id}", help=f"{item.cast[0]} filmjeinek listázása"):
                        show_person_dialog(item.cast[0], orchestrator)

            # Rövid leírás
            overview_snippet = item.overview[:170] + "..." if len(item.overview) > 170 else item.overview
            st.markdown(f"<div class='movie-overview'>{overview_snippet}</div>", unsafe_allow_html=True)

            # Élő / Gyorstárazott nCore Kiadás-összegző Jelvény
            cache_key = f"cached_releases_{item.tmdb_id}"
            if cache_key in st.session_state:
                cached_rels = st.session_state[cache_key]
                if not cached_rels:
                    st.markdown("<div style='margin-bottom: 8px;'><span style='background: #334155; color: #94a3b8; padding: 3px 8px; border-radius: 6px; font-size: 0.75rem; font-weight: 500;'>🚫 Még nincs fent nCore-on</span></div>", unsafe_allow_html=True)
                else:
                    b_rel = cached_rels[0]
                    b_sp = b_rel.specs
                    if all(r.is_cam for r in cached_rels):
                        st.markdown("<div style='margin-bottom: 8px;'><span style='background: #dc2626; color: white; padding: 3px 8px; border-radius: 6px; font-size: 0.75rem; font-weight: bold;'>⏳ Csak mozis (CAM) van • Várj a digitálisra!</span></div>", unsafe_allow_html=True)
                    elif b_rel.has_hungarian_audio:
                        st.markdown(f"<div style='margin-bottom: 8px;'><span style='background: #059669; color: white; padding: 3px 8px; border-radius: 6px; font-size: 0.75rem; font-weight: bold;'>🔊 Magyar: {b_sp['resolution']} ({b_sp['hdr']}) • {b_rel.size_human}</span></div>", unsafe_allow_html=True)
                    else:
                        st.markdown(f"<div style='margin-bottom: 8px;'><span style='background: #2563eb; color: white; padding: 3px 8px; border-radius: 6px; font-size: 0.75rem; font-weight: bold;'>🇬🇧 Eredeti hang + Felirat • {b_sp['resolution']} • {b_rel.size_human}</span></div>", unsafe_allow_html=True)
            else:
                c_chk, _ = st.columns([3, 1])
                with c_chk:
                    if st.button("🔍 nCore állapot ellenőrzése", key=f"chk_badge_{key_prefix}_{item.tmdb_id}"):
                        detailed_item = orchestrator.tmdb_service.get_details(item.tmdb_id, item.media_type) or item
                        st.session_state[cache_key] = orchestrator.get_available_releases(detailed_item, prefer_hungarian=True)
                        st.rerun()

            # 1. Gombsor: Előzetes & Infó, Hasonló filmek és Kívánságlista
            b1_col1, b1_col2, b1_col3 = st.columns([1, 1, 1])
            with b1_col1:
                if st.button("🎬 Infó", key=f"{key_prefix}_details_{item.tmdb_id}", use_container_width=True):
                    detailed_item = orchestrator.tmdb_service.get_details(item.tmdb_id, item.media_type) or item
                    show_movie_details_dialog(detailed_item, orchestrator)
            with b1_col2:
                if st.button("✨ Hasonló", key=f"{key_prefix}_sim_{item.tmdb_id}", use_container_width=True, help="Hasonló filmek és ajánlások"):
                    show_similar_dialog(item, orchestrator)
            with b1_col3:
                is_saved = orchestrator.watchlist.is_in_watchlist(item.tmdb_id)
                star_label = "⭐ Mentve" if is_saved else "🤍 Lista"
                if st.button(star_label, key=f"{key_prefix}_wl_{item.tmdb_id}", use_container_width=True):
                    orchestrator.watchlist.toggle(item)
                    st.rerun()

            # 2. Gombsor: Mozi-Radar és Gyűjtemény (ha elérhető)
            is_radar = orchestrator.radar.is_monitored(item.tmdb_id)
            radar_label = "🔔 Figyelve (Radar)" if is_radar else "🔔 Mozi-Radar"
            radar_type = "primary" if is_radar else "secondary"

            if item.collection_id:
                b2_c1, b2_c2 = st.columns([1.3, 1])
                with b2_c1:
                    coll_name = item.collection_name or "Gyűjtemény"
                    coll_btn_txt = f"📦 {coll_name[:18]}..." if len(coll_name) > 18 else f"📦 {coll_name}"
                    if st.button(coll_btn_txt, key=f"{key_prefix}_coll_{item.tmdb_id}", use_container_width=True, help="A teljes franchise részeinek megtekintése"):
                        show_collection_dialog(item.collection_id, orchestrator)
                with b2_c2:
                    if st.button(radar_label, key=f"{key_prefix}_radar_{item.tmdb_id}", type=radar_type, use_container_width=True, help="Automatikus letöltés jó minőségben megjelenéskor"):
                        orchestrator.radar.toggle(item)
                        st.rerun()
            else:
                if st.button(radar_label, key=f"{key_prefix}_radar_{item.tmdb_id}", type=radar_type, use_container_width=True, help="Automatikus letöltés jó minőségben megjelenéskor"):
                    orchestrator.radar.toggle(item)
                    st.rerun()

            # 3. Gombsor: Letöltés és Kiadások választása
            b2_col1, b2_col2 = st.columns([1, 1])
            btn_auto_key = f"dl_auto_{key_prefix}_{item.tmdb_id}"
            toggle_key = f"show_releases_{key_prefix}_{item.tmdb_id}"

            with b2_col1:
                if st.button("⚡ Letöltés", key=btn_auto_key, type="primary", use_container_width=True):
                    with st.spinner("Keresés nCore-on és tárhely ellenőrzése..."):
                        detailed_item = orchestrator.tmdb_service.get_details(item.tmdb_id, item.media_type) or item
                        releases = orchestrator.get_available_releases(detailed_item, prefer_hungarian=True)
                        if not releases:
                            st.error(f"Nem található kiadás az nCore-on ehhez: {item.title_hu}")
                        else:
                            best_rel = releases[0]
                            st.session_state[cache_key] = releases
                            space_info = orchestrator.check_disk_space_for_release(best_rel)
                            if not space_info["has_enough"]:
                                st.session_state[f"space_warn_{item.tmdb_id}"] = {
                                    "release": best_rel,
                                    "item": detailed_item,
                                    "space_info": space_info,
                                }
                                st.rerun()
                            else:
                                res = orchestrator.download_specific_release(detailed_item, best_rel)
                                if res.get("success"):
                                    st.success(f"🎉 **{res['message']}**")
                                    st.balloons()
                                else:
                                    st.error(f"❌ {res.get('message')}")

            with b2_col2:
                btn_rel_label = "🔼 Bezárás" if st.session_state.get(toggle_key, False) else "📋 Kiadások"
                if st.button(btn_rel_label, key=f"btn_toggle_{key_prefix}_{item.tmdb_id}", use_container_width=True):
                    st.session_state[toggle_key] = not st.session_state.get(toggle_key, False)
                    st.rerun()

            # Intelligens Tárhely Figyelmeztetés Panel
            warn_data = st.session_state.get(f"space_warn_{item.tmdb_id}")
            if warn_data:
                sp_rel = warn_data["release"]
                sp_item = warn_data["item"]
                sp_info = warn_data["space_info"]
                st.warning(
                    f"⚠️ **Kevés a hely a merevlemezen!**\n\n"
                    f"• Szabad hely: **{sp_info['free_gb']} GB**\n"
                    f"• Választott kiadás: **{sp_info['required_gb']} GB** (`{sp_rel.title}`)\n"
                    f"• Biztonsági tartalékkal szükséges: **{sp_info['needed_gb']} GB** (Hiányzik: **{sp_info['shortage_gb']} GB**)\n\n"
                    f"Szeretnéd mégis elindítani a letöltést, vagy kisebb kiadást választasz?"
                )
                c_w1, c_w2 = st.columns(2)
                with c_w1:
                    if st.button("⚠️ Letöltés mégis", key=f"force_{key_prefix}_{sp_rel.torrent_id}", type="primary", use_container_width=True):
                        del st.session_state[f"space_warn_{item.tmdb_id}"]
                        res = orchestrator.download_specific_release(sp_item, sp_rel)
                        if res.get("success"):
                            st.success(f"🎉 **{res['message']}**")
                            st.balloons()
                        else:
                            st.error(f"❌ {res.get('message')}")
                        st.rerun()
                with c_w2:
                    if st.button("🔍 Kisebb kiadás választása", key=f"cancel_{key_prefix}_{item.tmdb_id}", use_container_width=True):
                        del st.session_state[f"space_warn_{item.tmdb_id}"]
                        st.session_state[toggle_key] = True
                        st.rerun()

            # Lenyíló kiadás választó és részletes specifikációs táblázat
            if st.session_state.get(toggle_key, False):
                st.markdown("<hr style='margin: 12px 0 8px 0; border: none; border-top: 1px solid rgba(255,255,255,0.1);'/>", unsafe_allow_html=True)
                if cache_key not in st.session_state:
                    with st.spinner("Kiadások lekérése az nCore-ról..."):
                        detailed_item = orchestrator.tmdb_service.get_details(item.tmdb_id, item.media_type) or item
                        st.session_state[cache_key] = orchestrator.get_available_releases(detailed_item, prefer_hungarian=True)

                releases = st.session_state.get(cache_key, [])
                if not releases:
                    st.warning("Nem található ellenőrzött kiadás az nCore-on ehhez a filmhez/sorozathoz.")
                else:
                    display_releases = releases
                    # Sorozatok esetén: Egygombos Teljes Évad és 1. Évad gombok
                    if item.media_type == MediaType.TV:
                        st.markdown("##### 📦 Egygombos Évad-letöltés")
                        c_pack1, c_pack2 = st.columns(2)
                        with c_pack1:
                            if st.button("📦 Teljes Sorozat (Minden évad)", key=f"dl_all_s_{key_prefix}_{item.tmdb_id}", use_container_width=True):
                                with st.spinner("Sorozatcsomagok keresése és beküldése..."):
                                    detailed_item = orchestrator.tmdb_service.get_details(item.tmdb_id, item.media_type) or item
                                    res = orchestrator.download_all_available_seasons(detailed_item, prefer_hungarian=True)
                                    if res.get("success"):
                                        st.success(f"🎉 **{res['message']}**")
                                        st.balloons()
                                    else:
                                        st.error(f"❌ {res.get('message')}")
                        with c_pack2:
                            if st.button("📺 1. Évad Letöltése Egyben", key=f"dl_s1_{key_prefix}_{item.tmdb_id}", use_container_width=True):
                                with st.spinner("1. Évad csomag keresése..."):
                                    detailed_item = orchestrator.tmdb_service.get_details(item.tmdb_id, item.media_type) or item
                                    res = orchestrator.download_season_pack(detailed_item, season_num=1, prefer_hungarian=True)
                                    if res.get("success"):
                                        st.success(f"🎉 **{res['message']}**")
                                        st.balloons()
                                    else:
                                        st.error(f"❌ {res.get('message')}")

                        available_seasons = []
                        has_pack = False
                        for r in releases:
                            s_num = r.season_num
                            if s_num == -1:
                                has_pack = True
                            elif s_num is not None and s_num not in available_seasons:
                                available_seasons.append(s_num)
                        available_seasons.sort()

                        if available_seasons or has_pack:
                            season_opts = ["🎬 Minden elérhető évad"]
                            if has_pack:
                                season_opts.append("📦 Évadcsomagok / Teljes sorozat")
                            for s in available_seasons:
                                season_opts.append(f"📺 {s}. Évad (S{s:02d})")

                            c_flt, _ = st.columns([2, 1])
                            with c_flt:
                                selected_season = st.selectbox(
                                    "🔍 Szűrés évad szerint:",
                                    options=season_opts,
                                    key=f"flt_{key_prefix}_{item.tmdb_id}",
                                )
                            if selected_season == "📦 Évadcsomagok / Teljes sorozat":
                                display_releases = [r for r in releases if r.season_num == -1]
                            elif selected_season != "🎬 Minden elérhető évad":
                                try:
                                    target_num = int(selected_season.split(".")[0].replace("📺", "").strip())
                                    display_releases = [r for r in releases if r.season_num == target_num]
                                except Exception:
                                    pass

                    st.markdown("##### 📋 Elérhető Kiadások és Részletes Specifikációk:")
                    for r_idx, rel in enumerate(display_releases):
                        is_best = (r_idx == 0)
                        sp = rel.specs
                        with st.container():
                            c_rel_info, c_rel_btn = st.columns([3, 1])
                            with c_rel_info:
                                best_badge = "<span style='background: #10b981; color: white; padding: 2px 7px; border-radius: 4px; font-size: 0.72rem; font-weight: bold; margin-right: 4px;'>⭐ AJÁNLOTT</span>" if is_best else ""
                                cam_badge = "<span style='background: #dc2626; color: white; padding: 2px 6px; border-radius: 4px; font-size: 0.72rem; margin-right: 4px;'>⚠️ MOZIS (CAM)</span>" if rel.is_cam else ""
                                season_badge = f"<span style='background: #7c3aed; color: white; padding: 2px 6px; border-radius: 4px; font-size: 0.72rem; margin-right: 4px;'>📺 {rel.season_tag}</span>" if (item.media_type == MediaType.TV and rel.season_tag) else ""
                                audio_badge = "<span style='background: #3b82f6; color: white; padding: 2px 6px; border-radius: 4px; font-size: 0.72rem; margin-right: 4px;'>🔊 Magyar</span>" if rel.has_hungarian_audio else "<span style='background: #64748b; color: white; padding: 2px 6px; border-radius: 4px; font-size: 0.72rem; margin-right: 4px;'>🇬🇧 Eredeti</span>"
                                res_badge = f"<span style='background: #0f766e; color: white; padding: 2px 6px; border-radius: 4px; font-size: 0.72rem; margin-right: 4px;'>{sp['resolution']}</span>"
                                hdr_badge = f"<span style='background: #854d0e; color: white; padding: 2px 6px; border-radius: 4px; font-size: 0.72rem; margin-right: 4px;'>{sp['hdr']}</span>" if sp['hdr'] != "SDR" else ""

                                st.markdown(f"{best_badge}{cam_badge}{season_badge}{audio_badge}{res_badge}{hdr_badge} **`{rel.title}`**", unsafe_allow_html=True)
                                st.caption(
                                    f"🎧 **Hang:** `{sp['audio']}` | 🎞️ **Forrás/Kodek:** `{sp['source']} • {sp['codec']}` | "
                                    f"💾 **Méret:** `{rel.size_human}` | 👥 **Seed:** `{rel.seeders}` | 📥 **Leech:** `{rel.leechers}`"
                                )

                            with c_rel_btn:
                                btn_r_key = f"dl_{key_prefix}_{item.tmdb_id}_{rel.torrent_id}_{r_idx}"
                                btn_text = "⚡ Letöltés" if is_best else "⬇️ Letöltés"
                                btn_type = "primary" if is_best else "secondary"
                                if st.button(btn_text, key=btn_r_key, type=btn_type, use_container_width=True):
                                    space_chk = orchestrator.check_disk_space_for_release(rel)
                                    detailed_item = orchestrator.tmdb_service.get_details(item.tmdb_id, item.media_type) or item
                                    if not space_chk["has_enough"]:
                                        st.session_state[f"space_warn_{item.tmdb_id}"] = {
                                            "release": rel,
                                            "item": detailed_item,
                                            "space_info": space_chk,
                                        }
                                        st.rerun()
                                    else:
                                        with st.spinner(f"Küldés qBittorrentnek ({rel.title})..."):
                                            res = orchestrator.download_specific_release(detailed_item, rel)
                                            if res.get("success"):
                                                st.success(f"🎉 **{res['message']}**")
                                                st.balloons()
                                            else:
                                                st.error(f"❌ {res.get('message')}")
                            st.markdown("<hr style='margin: 6px 0; border: none; border-top: 1px solid rgba(255,255,255,0.05);'/>", unsafe_allow_html=True)

        st.markdown("</div>", unsafe_allow_html=True)


def fetch_browse_items(
    orchestrator: Orchestrator,
    category: str,
    media_type: MediaType,
    sort_by: str,
    year_min: Optional[int],
    year_max: Optional[int],
    query: Optional[str],
    page: int,
    limit: int = 12,
    watch_provider: Optional[str] = None,
) -> List[MediaItem]:
    """Segédfüggvény a különböző kategóriák és szűrők dinamikus TMDB lekéréséhez."""
    clean_q = query.strip() if query and query.strip() else None

    # Ha kifejezetten streaming szolgáltatóra szűrünk (Netflix, Max, Disney+, stb.)
    if watch_provider:
        return orchestrator.discover_media(
            media_type=media_type,
            sort_by=sort_by,
            year_min=year_min,
            year_max=year_max,
            page=page,
            limit=limit,
            query=clean_q,
            with_watch_providers=watch_provider,
        )

    if category == "⚔️ Háborús & Történelmi":
        return orchestrator.get_war_media(
            media_type=media_type,
            page=page,
            limit=limit,
            sort_by=sort_by,
            year_min=year_min,
            year_max=year_max,
            query=clean_q,
        )
    elif category == "🔥 Népszerű Filmek":
        if clean_q or year_min or year_max or sort_by != "popularity.desc":
            return orchestrator.discover_media(
                media_type=MediaType.MOVIE,
                sort_by=sort_by,
                year_min=year_min,
                year_max=year_max,
                page=page,
                limit=limit,
                query=clean_q,
            )
        return orchestrator.get_popular_movies(page=page, limit=limit)
    elif category == "📺 Népszerű Sorozatok":
        if clean_q or year_min or year_max or sort_by != "popularity.desc":
            return orchestrator.discover_media(
                media_type=MediaType.TV,
                sort_by=sort_by,
                year_min=year_min,
                year_max=year_max,
                page=page,
                limit=limit,
                query=clean_q,
            )
        return orchestrator.get_popular_series(page=page, limit=limit)
    elif category == "⭐ Kritikuskedvencek":
        if clean_q or year_min or year_max:
            return orchestrator.discover_media(
                media_type=media_type,
                sort_by="vote_average.desc",
                year_min=year_min,
                year_max=year_max,
                page=page,
                limit=limit,
                query=clean_q,
            )
        return orchestrator.get_top_rated(media_type=media_type, page=page, limit=limit)
    elif category == "💥 Akció & Kaland":
        genre_id = 28 if media_type == MediaType.MOVIE else 10759
        return orchestrator.get_by_genre(
            genre_id, media_type, page=page, limit=limit, sort_by=sort_by, year_min=year_min, year_max=year_max, query=clean_q
        )
    elif category == "😂 Vígjáték":
        return orchestrator.get_by_genre(
            35, media_type, page=page, limit=limit, sort_by=sort_by, year_min=year_min, year_max=year_max, query=clean_q
        )
    elif category == "🚀 Sci-Fi & Fantasy":
        genre_id = 878 if media_type == MediaType.MOVIE else 10765
        return orchestrator.get_by_genre(
            genre_id, media_type, page=page, limit=limit, sort_by=sort_by, year_min=year_min, year_max=year_max, query=clean_q
        )
    elif category == "😱 Horror & Thriller":
        genre_id = 27 if media_type == MediaType.MOVIE else 9648
        return orchestrator.get_by_genre(
            genre_id, media_type, page=page, limit=limit, sort_by=sort_by, year_min=year_min, year_max=year_max, query=clean_q
        )
    elif category == "👶 Családi & Animáció":
        return orchestrator.get_by_genre(
            16, media_type, page=page, limit=limit, sort_by=sort_by, year_min=year_min, year_max=year_max, query=clean_q
        )
    elif category == "🕵️ Krimi & Rejtély":
        return orchestrator.get_by_genre(
            80, media_type, page=page, limit=limit, sort_by=sort_by, year_min=year_min, year_max=year_max, query=clean_q
        )
    elif category == "🎭 Dráma":
        return orchestrator.get_by_genre(
            18, media_type, page=page, limit=limit, sort_by=sort_by, year_min=year_min, year_max=year_max, query=clean_q
        )
    return []


def main():
    settings = get_settings()
    orchestrator = get_orchestrator()
    if not hasattr(orchestrator, "radar") or orchestrator.radar is None:
        from src.core.movie_radar import MovieRadarManager
        orchestrator.radar = MovieRadarManager()
    local_ip = get_local_ip()

    # Fejléc
    st.markdown(
        """
        <div class="app-header">
            <div class="app-title">🎬 Ncore Média Letöltő</div>
            <div class="app-subtitle">Keresd meg a filmet, és a rendszer mindent automatikusan előkészít a TV-re!</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Telefonos elérési sáv QR kóddal
    c_hint, c_qr = st.columns([5, 1])
    target_mobile_url = f"http://{local_ip}:{settings.APP_PORT}"
    with c_hint:
        st.markdown(
            f"""
            <div class="mobile-hint">
                📱 <b>Mobilról vagy tabletről is használható!</b> Nyisd meg a telefonod böngészőjében: <code>{target_mobile_url}</code> vagy használd a QR-kódot!
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c_qr:
        if st.button("📱 QR-kód", use_container_width=True):
            show_mobile_qr_dialog(target_mobile_url)

    # Fő fülek
    wl_count = len(orchestrator.watchlist.get_all())
    radar_count = len(orchestrator.radar.get_monitoring())
    tab_search, tab_radar, tab_watchlist, tab_downloads, tab_status, tab_debug = st.tabs([
        "🔍 Film & Sorozat Keresés",
        f"🔔 Mozi-Radar ({radar_count})",
        f"⭐ Kívánságlista ({wl_count})",
        "📥 Letöltések Állapota",
        "⚙️ Rendszer & Beállítások",
        "🛠️ Hibakeresés & Logok",
    ])

    # ==============================================================
    # 1. FÜL: KERESÉS ÉS AJÁNLÓK
    # ==============================================================
    with tab_search:
        st.write("")
        col_search, col_btn = st.columns([5, 1])
        with col_search:
            search_query = st.text_input(
                label="Film vagy Sorozat Címe",
                placeholder="Írd be a film vagy sorozat címét (pl. Gladiátor, Oppenheimer, Shogun)...",
                label_visibility="collapsed",
                key="search_input",
            )
        with col_btn:
            search_submitted = st.button("🔍 Keresés", type="primary", use_container_width=True)

        # Keresési találatok tárolása a munkamenetben
        current_query = search_query.strip()
        last_query = st.session_state.get("last_query", "")
        should_search = search_submitted or (current_query and current_query != last_query)

        if should_search and current_query:
            with st.spinner("Keresés a filmes adatbázisban..."):
                results = orchestrator.search_media(current_query)
                st.session_state["search_results"] = results
                st.session_state["last_query"] = current_query

        results = st.session_state.get("search_results", [])
        if results and current_query:
            st.markdown(f"### Találatok a(z) *„{st.session_state.get('last_query', '')}”* keresésre:")
            st.caption("Kattints a kártyán a letöltés indításához vagy az évadok kiválasztásához!")

            cols = st.columns(2)
            for idx, item in enumerate(results):
                with cols[idx % 2]:
                    render_media_card(item, orchestrator, key_prefix=f"s_{idx}")

        elif "search_results" in st.session_state and current_query and not results:
            st.info("Nem található film vagy sorozat ezzel a címmel. Próbáld meg az eredeti angol címet vagy pontosabb kifejezést!")

        else:
            # ==============================================================
            # KEZDŐLAPI AJÁNLÓK: VÉGTELEN MÉDIA FELFEDEZŐ & NCORE GYORSKERESŐ
            # ==============================================================
            st.markdown("<hr style='margin: 16px 0; border: none; border-top: 1px solid rgba(255,255,255,0.08);'/>", unsafe_allow_html=True)
            
            c_head_left, c_head_right = st.columns([4, 2])
            with c_head_left:
                st.markdown("### 🌟 Mit nézzünk ma este? - Végtelen Média Felfedező")
                st.caption("Lapozz kedvedre a kategóriák, platformok és korszakok között, azonnali nCore elérhetőséggel és Mozi-Radarral!")

            # 1. Kategória Választó Sáv (Pills)
            rec_options = [
                "⚔️ Háborús & Történelmi",
                "🔥 Népszerű Filmek",
                "📺 Népszerű Sorozatok",
                "⭐ Kritikuskedvencek",
                "💥 Akció & Kaland",
                "😂 Vígjáték",
                "🚀 Sci-Fi & Fantasy",
                "😱 Horror & Thriller",
                "👶 Családi & Animáció",
                "🕵️ Krimi & Rejtély",
                "🎭 Dráma",
            ]
            rec_choice = st.pills(
                "Kategóriák",
                options=rec_options,
                default="⚔️ Háborús & Történelmi",
                key="home_rec_pills",
            )
            if not rec_choice:
                rec_choice = "⚔️ Háborús & Történelmi"

            # 2. Beépített Kereső & Szűrősáv a Kategórián Belül
            with st.container():
                st.markdown(
                    """
                    <div style="background: rgba(15, 23, 42, 0.45); border: 1px solid rgba(255,255,255,0.06); 
                                border-radius: 10px; padding: 12px 14px; margin: 10px 0 14px 0;">
                    """,
                    unsafe_allow_html=True,
                )
                f_col1, f_col2, f_col3, f_col4, f_col5 = st.columns([2.5, 1.8, 1.6, 1.8, 1.3])
                with f_col1:
                    sub_q = st.text_input(
                        "🔍 Keresés a kategórián belül",
                        placeholder="Pl. 'Normandia', 'tengeralattjáró', 'Dunkirk'...",
                        key="browse_sub_q",
                    )
                with f_col2:
                    sort_mode = st.selectbox(
                        "📊 Rendezési elv",
                        options=["🔥 Legnépszerűbb", "⭐ Legjobbra értékelt", "🆕 Legújabb kiadás"],
                        key="browse_sort_mode",
                    )
                with f_col3:
                    era_mode = st.selectbox(
                        "📅 Korszak / Évszám",
                        options=["Összes évjárat", "🌟 2020 utániak", "🎬 2010 – 2020", "📼 2000 – 2010", "🎞️ 2000 előttiek"],
                        key="browse_era_mode",
                    )
                with f_col4:
                    prov_mode = st.selectbox(
                        "📺 Streaming Platform",
                        options=["Összes platform", "🔴 Netflix", "🟣 Max (HBO)", "🔵 Disney+", "🍏 Apple TV+", "🟡 SkyShowtime", "📦 Prime Video"],
                        key="browse_prov_mode",
                    )
                with f_col5:
                    if rec_choice == "📺 Népszerű Sorozatok":
                        default_t_idx = 1
                    elif rec_choice == "🔥 Népszerű Filmek":
                        default_t_idx = 0
                    else:
                        default_t_idx = 0
                    type_choice = st.selectbox(
                        "🎬 Formátum",
                        options=["🎬 Filmek", "📺 Sorozatok"],
                        index=default_t_idx,
                        key="browse_type_choice",
                    )
                st.markdown("</div>", unsafe_allow_html=True)

            # Szűrőparaméterek feloldása
            target_media_type = (
                MediaType.TV
                if ("Sorozat" in type_choice or rec_choice == "📺 Népszerű Sorozatok")
                else MediaType.MOVIE
            )

            sort_map = {
                "🔥 Legnépszerűbb": "popularity.desc",
                "⭐ Legjobbra értékelt": "vote_average.desc",
                "🆕 Legújabb kiadás": (
                    "primary_release_date.desc"
                    if target_media_type == MediaType.MOVIE
                    else "first_air_date.desc"
                ),
            }
            sort_by = sort_map.get(sort_mode, "popularity.desc")

            year_min, year_max = None, None
            if era_mode == "🌟 2020 utániak":
                year_min = 2020
            elif era_mode == "🎬 2010 – 2020":
                year_min, year_max = 2010, 2020
            elif era_mode == "📼 2000 – 2010":
                year_min, year_max = 2000, 2009
            elif era_mode == "🎞️ 2000 előttiek":
                year_max = 1999

            prov_map = {
                "🔴 Netflix": "8",
                "🟣 Max (HBO)": "1899",
                "🔵 Disney+": "337",
                "🍏 Apple TV+": "350",
                "🟡 SkyShowtime": "1773",
                "📦 Prime Video": "119",
            }
            watch_provider_id = prov_map.get(prov_mode)

            # Filter Hash figyelése - ha megváltozik a szűrő, újraindul az 1. oldalról
            clean_sub_q = sub_q.strip() if sub_q else ""
            filter_state_hash = f"{rec_choice}|{clean_sub_q}|{sort_by}|{year_min}|{year_max}|{watch_provider_id}|{target_media_type.value}"

            if st.session_state.get("last_filter_state_hash") != filter_state_hash:
                st.session_state["last_filter_state_hash"] = filter_state_hash
                st.session_state["browse_page"] = 1
                st.session_state["browse_accumulated_items"] = []

            current_page = st.session_state.get("browse_page", 1)
            accumulated_items = st.session_state.get("browse_accumulated_items", [])

            if not accumulated_items:
                with st.spinner("Tartalmak betöltése a TMDB-ről..."):
                    accumulated_items = fetch_browse_items(
                        orchestrator,
                        category=rec_choice,
                        media_type=target_media_type,
                        sort_by=sort_by,
                        year_min=year_min,
                        year_max=year_max,
                        query=clean_sub_q,
                        page=current_page,
                        limit=12,
                        watch_provider=watch_provider_id,
                    )
                    st.session_state["browse_accumulated_items"] = accumulated_items

            # 3. nCore & Plex Elérhetőség-ellenőrző Sáv
            chk_col1, chk_col2, chk_col3, chk_col4 = st.columns([3.0, 2.2, 2.6, 2.0])

            with chk_col1:
                btn_chk_text = f"⚡ Oldalon lévők ({len(accumulated_items)} db) nCore ellenőrzése"
                if st.button(btn_chk_text, key="btn_check_all_ncore", use_container_width=True, type="secondary"):
                    items_to_check = [
                        it for it in accumulated_items
                        if f"cached_releases_{it.tmdb_id}" not in st.session_state
                    ]
                    if not items_to_check:
                        st.info("Az oldalon lévő összes film már ellenőrizve van!")
                    else:
                        with st.status(f"⚡ Gyorsított nCore ellenőrzés ({len(items_to_check)} db cím, 3 szálon)...", expanded=True) as status_box:
                            prog = st.progress(0.0)

                            def _on_batch_progress(done: int, total: int, it: Any, rels: List[Any]):
                                prog.progress(done / total)
                                stat_str = f"✅ {len(rels)} kiadás" if rels else "⏳ nincs még fent"
                                status_box.write(f"• **{it.title_hu}** ({it.year or ''}): {stat_str}")

                            batch_res = orchestrator.check_releases_batch(
                                items_to_check, max_workers=3, callback=_on_batch_progress
                            )
                            for tmdb_id, rels in batch_res.items():
                                st.session_state[f"cached_releases_{tmdb_id}"] = rels

                            status_box.update(
                                label="✅ nCore kiadások villámgyorsan lekérve (Párhuzamos 3-szálas motorral)!",
                                state="complete",
                                expanded=False,
                            )
                        st.rerun()

            with chk_col2:
                show_only_available = st.toggle("🎯 Csak nCore-on elérhetők", key="toggle_browse_only_available")

            with chk_col3:
                hide_in_plex = st.toggle("🙈 Hiányzók (nincs Plexben)", key="toggle_browse_hide_in_plex")

            with chk_col4:
                checked_cnt = sum(1 for it in accumulated_items if f"cached_releases_{it.tmdb_id}" in st.session_state)
                avail_cnt = sum(1 for it in accumulated_items if len(st.session_state.get(f"cached_releases_{it.tmdb_id}", [])) > 0)
                if checked_cnt > 0:
                    st.caption(f"📊 **{checked_cnt}/{len(accumulated_items)}** ellenőrizve | **{avail_cnt}** elérhető")
                else:
                    st.caption("ℹ️ Kattints az ellenőrzésre!")

            # 4. Kártyák Megjelenítése és Szűrése
            display_items = accumulated_items
            if hide_in_plex:
                display_items = [
                    it for it in display_items
                    if not orchestrator.plex_service.has_media(it.title_hu, it.year)
                ]

            if show_only_available:
                display_items = [
                    it for it in display_items
                    if f"cached_releases_{it.tmdb_id}" in st.session_state
                    and len(st.session_state[f"cached_releases_{it.tmdb_id}"]) > 0
                ]
                if not display_items:
                    if checked_cnt == 0:
                        st.info(
                            "💡 Az oldalon lévő filmek még nincsenek ellenőrizve az nCore-on. "
                            "Kattints a fenti **'⚡ Oldalon lévők nCore ellenőrzése'** gombra az elérhető kiadások felderítéséhez!"
                        )
                    else:
                        st.warning("A jelenleg betöltött elemek közül még egyik sincs fent az nCore-on. Lapozz tovább vagy tölts be még 12 filmet az alábbi zöld gombbal!")

            if display_items:
                r_cols = st.columns(2)
                for r_idx, r_item in enumerate(display_items):
                    with r_cols[r_idx % 2]:
                        render_media_card(r_item, orchestrator, key_prefix=f"browse_{r_idx}_{r_item.tmdb_id}")

            # 5. Lapozó & Végtelen Betöltő (Pagináció & Load More)
            st.markdown("<hr style='margin: 20px 0 12px 0; border: none; border-top: 1px solid rgba(255,255,255,0.08);'/>", unsafe_allow_html=True)
            nav_col1, nav_col2, nav_col3 = st.columns([2, 4, 2])

            with nav_col1:
                if current_page > 1:
                    if st.button("◀ Előző oldal (12 film)", key="btn_browse_prev", use_container_width=True):
                        prev_p = max(1, current_page - 1)
                        st.session_state["browse_page"] = prev_p
                        st.session_state["browse_accumulated_items"] = fetch_browse_items(
                            orchestrator,
                            category=rec_choice,
                            media_type=target_media_type,
                            sort_by=sort_by,
                            year_min=year_min,
                            year_max=year_max,
                            query=clean_sub_q,
                            page=prev_p,
                            limit=12,
                            watch_provider=watch_provider_id,
                        )
                        st.rerun()

            with nav_col2:
                btn_load_more_text = f"➕ További 12 film betöltése ({len(accumulated_items)} eddig betöltve • {current_page}. oldal)"
                if st.button(btn_load_more_text, type="primary", use_container_width=True, key="btn_browse_load_more"):
                    next_p = current_page + 1
                    with st.spinner("Következő oldal betöltése a TMDB-ről..."):
                        more_items = fetch_browse_items(
                            orchestrator,
                            category=rec_choice,
                            media_type=target_media_type,
                            sort_by=sort_by,
                            year_min=year_min,
                            year_max=year_max,
                            query=clean_sub_q,
                            page=next_p,
                            limit=12,
                            watch_provider=watch_provider_id,
                        )
                        existing_ids = {x.tmdb_id for x in accumulated_items}
                        for m in more_items:
                            if m.tmdb_id not in existing_ids:
                                accumulated_items.append(m)
                                existing_ids.add(m.tmdb_id)
                        st.session_state["browse_page"] = next_p
                        st.session_state["browse_accumulated_items"] = accumulated_items
                    st.rerun()

            with nav_col3:
                if st.button("Következő oldal ▶", key="btn_browse_next", use_container_width=True):
                    next_p = current_page + 1
                    st.session_state["browse_page"] = next_p
                    st.session_state["browse_accumulated_items"] = fetch_browse_items(
                        orchestrator,
                        category=rec_choice,
                        media_type=target_media_type,
                        sort_by=sort_by,
                        year_min=year_min,
                        year_max=year_max,
                        query=clean_sub_q,
                        page=next_p,
                        limit=12,
                        watch_provider=watch_provider_id,
                    )
                    st.rerun()

    # ==============================================================
    # 2. FÜL: MOZI-RADAR & AUTOMATA LETÖLTŐ
    # ==============================================================
    with tab_radar:
        st.write("")
        r_items = orchestrator.radar.get_all()
        r_monitoring = [x for x in r_items if x.get("status") == "monitoring"]
        r_downloaded = [x for x in r_items if x.get("status") == "downloaded"]

        r_head, r_check, r_clear = st.columns([4, 2, 1.2])
        with r_head:
            st.markdown(f"### 🔔 Mozi-Radar & Automata Letöltő ({len(r_monitoring)} figyelve)")
            st.caption("A háttérben futó figyelőnk automatikusan letölti és Plexbe rendezi a filmeket, amint kijön a digitális, jó minőségű (nem mozis CAM) szinkronos kiadás!")
        with r_check:
            if r_monitoring and st.button("⚡ Figyeltek ellenőrzése most", use_container_width=True, key="btn_check_radar_now"):
                with st.spinner("Mozi-Radar tételek felderítése az nCore-on..."):
                    hits = orchestrator.radar.check_and_trigger_downloads(orchestrator)
                    if hits:
                        st.success(f"🎉 {len(hits)} db új film megjelent digitálisan és elindult a letöltése!")
                        st.balloons()
                    else:
                        st.info("Egyelőre még nincs új digitális kiadás a figyelt filmekből.")
                st.rerun()
        with r_clear:
            if r_items and st.button("🗑️ Ürítés", use_container_width=True, key="btn_clear_radar"):
                for it in r_items:
                    orchestrator.radar.remove(it["tmdb_id"])
                st.rerun()

        if not r_items:
            st.info("💡 A Mozi-Radaron jelenleg nincs figyelt film. Böngészés közben kattints a filmek kártyáján a **🔔 Mozi-Radar** gombra a felvételhez!")
        else:
            if r_monitoring:
                st.markdown("#### ⏳ Aktívan Figyelt Filmek (Várakozás digitális kiadásra):")
                r_m_cols = st.columns(2)
                for m_idx, m_entry in enumerate(r_monitoring):
                    with r_m_cols[m_idx % 2]:
                        m_obj = MediaItem(**m_entry["media_item"])
                        render_media_card(m_obj, orchestrator, key_prefix=f"radar_mon_{m_idx}")

            if r_downloaded:
                st.markdown("<hr style='margin: 16px 0; border: none; border-top: 1px solid rgba(255,255,255,0.08);'/>", unsafe_allow_html=True)
                st.markdown(f"#### ✅ Már Megjelent és Letöltött Filmek ({len(r_downloaded)} db):")
                r_d_cols = st.columns(2)
                for d_idx, d_entry in enumerate(r_downloaded):
                    with r_d_cols[d_idx % 2]:
                        d_obj = MediaItem(**d_entry["media_item"])
                        render_media_card(d_obj, orchestrator, key_prefix=f"radar_done_{d_idx}")

    # ==============================================================
    # 2. FÜL: KÍVÁNSÁGLISTA (Később megnézem)
    # ==============================================================
    with tab_watchlist:
        st.write("")
        wl_items = orchestrator.watchlist.get_all()
        wl_head, wl_clear = st.columns([5, 1])
        with wl_head:
            st.markdown(f"### ⭐ Kívánságlista ({len(wl_items)} db)")
            st.caption("A későbbre elmentett filmjeid és sorozataid gyűjteménye. Egy kattintással letöltheted őket!")
        with wl_clear:
            if wl_items and st.button("🗑️ Lista ürítése", use_container_width=True, key="btn_clear_wl"):
                for it in wl_items:
                    orchestrator.watchlist.remove(it.tmdb_id)
                st.rerun()

        if not wl_items:
            st.info("💡 A kívánságlistád jelenleg üres. Keresés közben vagy az ajánlóknál kattints a **🤍 Kívánságlista** gombra bármelyik kártyán a mentéshez!")
        else:
            wl_cols = st.columns(2)
            for w_idx, w_item in enumerate(wl_items):
                with wl_cols[w_idx % 2]:
                    render_media_card(w_item, orchestrator, key_prefix=f"wl_{w_idx}")

    # ==============================================================
    # 2. FÜL: LETÖLTÉSEK ÁLLAPOTA
    # ==============================================================
    with tab_downloads:
        st.write("")
        c_head, c_refresh = st.columns([5, 1])
        with c_head:
            st.markdown("### 📥 Letöltések és Előzmények")
            st.caption("Valós idejű letöltési folyamat, nCore Hit&Run felügyelet és megspórolt tárhely statisztika.")
        with c_refresh:
            if st.button("🔄 Frissítés", use_container_width=True):
                st.rerun()

        # Lemezterület és HnR Automata Takarítási Statisztika
        stats = orchestrator.get_storage_and_hnr_stats()
        st.markdown(
            f"""
            <div style="background: linear-gradient(135deg, rgba(30, 41, 59, 0.7) 0%, rgba(15, 23, 42, 0.8) 100%);
                        border: 1px solid rgba(59, 130, 246, 0.3); border-radius: 12px; padding: 14px 18px; margin-bottom: 18px;">
                <div style="font-size: 0.95rem; font-weight: 600; color: #93c5fd; margin-bottom: 8px;">
                    🧹 Lemezterület & nCore Hit&Run Automata Felügyelet
                </div>
                <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px;">
                    <div>
                        <div style="font-size: 0.78rem; color: #94a3b8;">Megspórolt tárhely (Hardlink):</div>
                        <div style="font-size: 1.3rem; font-weight: bold; color: #10b981;">{stats['saved_human']}</div>
                        <div style="font-size: 0.72rem; color: #64748b;">{stats['organized_count']} db médiánál dupla másolás nélkül</div>
                    </div>
                    <div>
                        <div style="font-size: 0.78rem; color: #94a3b8;">Aktív seedelés:</div>
                        <div style="font-size: 1.3rem; font-weight: bold; color: #60a5fa;">{stats['seeding_count']} db torrent</div>
                        <div style="font-size: 0.72rem; color: #64748b;">HnR kötelezettség figyelése</div>
                    </div>
                    <div>
                        <div style="font-size: 0.78rem; color: #94a3b8;">HnR szabályt elérte:</div>
                        <div style="font-size: 1.3rem; font-weight: bold; color: #f59e0b;">{stats['hnr_met_count']} db</div>
                        <div style="font-size: 0.72rem; color: #64748b;">{stats['target_hours']}h vagy {stats['target_ratio']} arány után takarítható</div>
                    </div>
                    <div>
                        <div style="font-size: 0.78rem; color: #94a3b8;">Összes aktív seed idő:</div>
                        <div style="font-size: 1.3rem; font-weight: bold; color: #c084fc;">{stats['total_seed_hours']} óra</div>
                        <div style="font-size: 0.72rem; color: #64748b;">A qBittorrent automatikusan fut</div>
                    </div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        tab_sub_active, tab_sub_history = st.tabs([
            "📥 Folyamatban lévő Letöltések",
            "📜 Letöltési Előzmények (Történet)",
        ])

        with tab_sub_active:
            downloads = orchestrator.get_active_downloads()
            if not downloads:
                st.info("Jelenleg nincs aktív letöltés a várólistában a qBittorrentben.")
            else:
                for item in downloads:
                    progress = item.get("progress", 0)
                    is_completed = item.get("is_completed", False)
                    seed_h = round(item.get("seeding_time_hours", 0.0), 1)
                    target_h = orchestrator.settings.MIN_SEED_TIME_HOURS
                    ratio = round(item.get("ratio", 0.0), 2)
                    target_r = orchestrator.settings.TARGET_RATIO
                    hnr_compliant = (seed_h >= target_h) or (ratio >= target_r)

                    status_icon = "✅" if is_completed else "📥"
                    exp_title = f"{status_icon} {item['name']} ({progress}%)"
                    with st.expander(exp_title, expanded=True):
                        col_p, col_stat = st.columns([3, 2])
                        with col_p:
                            st.progress(progress / 100.0)
                            st.caption(f"Állapot: **{item['state']}** | Letöltés: **{item.get('download_speed_kb', 0)} KB/s** | Feltöltés: **{item.get('upload_speed_kb', 0)} KB/s**")
                        with col_stat:
                            if is_completed:
                                if hnr_compliant:
                                    st.success("✅ HnR Teljesítve! (48h vagy 1.0 arány)")
                                    st.caption(f"⏱️ Seed: **{seed_h} / {target_h} óra** | 📈 Arány: **{ratio} / {target_r}**")
                                    st.caption("🧹 A rendszer diszkréten törölheti a qBittorrentből a médiafájlok megőrzésével.")
                                else:
                                    rem_h = round(max(0.0, target_h - seed_h), 1)
                                    st.info(f"⏳ Seed folyamatban (HnR kötelezettség)")
                                    st.caption(f"⏱️ Seed: **{seed_h} / {target_h} óra** (még **{rem_h} óra**) | 📈 Arány: **{ratio} / {target_r}**")
                                    hnr_pct = min(1.0, max(seed_h / float(target_h), ratio / float(target_r)))
                                    st.progress(hnr_pct)
                            else:
                                st.warning(f"⏳ Letöltés alatt ({progress}%)")
                                st.caption("Amint végez, a rendszer azonnal hardlinkkel Plexbe rendezi!")

        with tab_sub_history:
            history = orchestrator.get_download_history()
            if not history:
                st.info("Még nincsenek korábbi letöltési előzmények a rendszerben.")
            else:
                for h_idx, task in enumerate(history):
                    m_item = task.get("media_item", {})
                    rel = task.get("release", {})
                    created_at = task.get("created_at", "Korábban")
                    is_plex = task.get("plex_organized", False)

                    with st.container():
                        st.markdown('<div class="movie-card" style="padding: 14px; margin-bottom: 12px;">', unsafe_allow_html=True)
                        h_col_img, h_col_details, h_col_action = st.columns([1, 4, 1])

                        with h_col_img:
                            poster = m_item.get("poster_url")
                            if poster:
                                st.image(poster, use_container_width=True)
                            else:
                                st.image("https://via.placeholder.com/150x220.png?text=Film", use_container_width=True)

                        with h_col_details:
                            m_title = m_item.get("title_hu", "Ismeretlen cím")
                            m_year = f"({m_item.get('year')})" if m_item.get("year") else ""
                            st.markdown(f"#### {m_title} {m_year}")

                            status_badge = "<span style='background: #10b981; color: white; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: bold;'>✅ Plexbe rendezve</span>" if is_plex else "<span style='background: #f59e0b; color: white; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: bold;'>⏳ Letöltés / Feldolgozás</span>"
                            audio_badge = "<span style='background: #3b82f6; color: white; padding: 2px 6px; border-radius: 4px; font-size: 0.72rem;'>🔊 Magyar szinkron</span>" if rel.get("has_hungarian_audio") else "<span style='background: #64748b; color: white; padding: 2px 6px; border-radius: 4px; font-size: 0.72rem;'>🇬🇧 Eredeti hang</span>"

                            st.markdown(f"{status_badge} {audio_badge}", unsafe_allow_html=True)
                            st.caption(f"📦 **Kiadás:** `{rel.get('title', 'N/A')}`")
                            st.caption(f"💾 Méret: **{rel.get('size_human', 'N/A')}** | 📅 Indítva: **{created_at}**")

                        with h_col_action:
                            task_id = task.get("task_id")
                            if st.button("🗑️ Törlés", key=f"del_task_{task_id}_{h_idx}", use_container_width=True):
                                orchestrator.remove_task(task_id)
                                st.success("Eltávolítva az előzményekből!")
                                st.rerun()

                        st.markdown('</div>', unsafe_allow_html=True)

        st.write("")
        if st.button("🍿 Plex Könyvtár Kézi Frissítése"):
            with st.spinner("Plex szerver értesítése..."):
                if orchestrator.plex_service.refresh_library():
                    st.success("Plex könyvtár sikeresen frissítve!")
                else:
                    st.warning("Nem sikerült elérni a Plex szervert. Ellenőrizd a beállításokat!")

    # ==============================================================
    # 3. FÜL: RENDSZER & BEÁLLÍTÁSOK (INTERAKTÍV SETUP)
    # ==============================================================
    with tab_status:
        st.markdown("### ⚙️ Rendszer és Beállítások Kezelése")
        st.caption("Itt adhatod meg az nCore belépési adataidat, a letöltési mappákat és ellenőrizheted a kapcsolatokat.")

        # Gyors áttekintő státusz kártyák
        sc1, sc2, sc3, sc4 = st.columns(4)
        with sc1:
            if settings.TMDB_API_KEY:
                st.success("TMDB: Beállítva ✅")
            else:
                st.warning("TMDB: Alapértelmezett ⚠️")
        with sc2:
            if settings.NCORE_USERNAME and settings.NCORE_USERNAME != "felhasznaloneved":
                st.success(f"nCore: {settings.NCORE_USERNAME} ✅")
            else:
                st.warning("nCore: Nincs megadva ⚠️")
        with sc3:
            st.info(f"qBittorrent: {settings.QBITTORRENT_HOST}")
        with sc4:
            st.info(f"Plex: {settings.PLEX_SERVER_URL}")

        # Lemezterület információk
        temp_free = get_disk_free_gb(settings.DOWNLOAD_TEMP_DIR)
        plex_free = get_disk_free_gb(settings.PLEX_MOVIES_DIR)

        st.write("")
        d_col1, d_col2 = st.columns(2)
        with d_col1:
            if temp_free is not None:
                if temp_free < 20.0:
                    st.warning(f"⚠️ Letöltési mappa ({settings.DOWNLOAD_TEMP_DIR}): **{temp_free} GB szabad** (Alacsony!)")
                else:
                    st.success(f"💾 Letöltési mappa ({settings.DOWNLOAD_TEMP_DIR}): **{temp_free} GB szabad** ✅")
            else:
                st.info(f"📁 Letöltési mappa: `{settings.DOWNLOAD_TEMP_DIR}`")
        with d_col2:
            if plex_free is not None:
                if plex_free < 20.0:
                    st.warning(f"⚠️ Plex mappa ({settings.PLEX_MOVIES_DIR}): **{plex_free} GB szabad** (Alacsony!)")
                else:
                    st.success(f"🍿 Plex mappa ({settings.PLEX_MOVIES_DIR}): **{plex_free} GB szabad** ✅")
            else:
                st.info(f"📁 Plex mappa: `{settings.PLEX_MOVIES_DIR}`")

        st.markdown("---")
        st.markdown("#### 📝 Beállítások Módosítása")

        with st.form("settings_form"):
            col_left, col_right = st.columns(2)

            with col_left:
                st.markdown("##### 🌐 nCore Hozzáférés")
                inp_ncore_user = st.text_input(
                    "nCore Felhasználónév",
                    value=settings.NCORE_USERNAME if settings.NCORE_USERNAME != "felhasznaloneved" else "",
                    placeholder="pl. kovacs.janos",
                )
                inp_ncore_pass = st.text_input(
                    "nCore Jelszó",
                    value=settings.NCORE_PASSWORD if settings.NCORE_PASSWORD != "jelszavad" else "",
                    type="password",
                    placeholder="••••••••••••",
                )
                inp_ncore_passkey = st.text_input(
                    "nCore Passkey / Cookie (opcionális)",
                    value=settings.NCORE_PASSKEY or "",
                    type="password",
                    help="Ha a webes belépés kétlépcsős ellenőrzést kér, add meg a passkey-t vagy böngésző sütit.",
                )

                st.write("")
                st.markdown("##### 🎬 TMDB Filmadatbázis")
                inp_tmdb_key = st.text_input(
                    "TMDB API Kulcs",
                    value=settings.TMDB_API_KEY or "2c46288716a18fb7aadcc2a801f3fc6b",
                    help="A filmek posztereihez és magyar leírásaihoz. Az előre kitöltött kulcs azonnal működik.",
                )
                inp_tmdb_lang = st.text_input(
                    "Keresési és Leírás Nyelv",
                    value=settings.TMDB_LANGUAGE or "hu-HU",
                )

                st.write("")
                st.markdown("##### 🍿 Plex Médiaszerver")
                inp_plex_url = st.text_input(
                    "Plex Szerver Cím",
                    value=settings.PLEX_SERVER_URL or "http://localhost:32400",
                )
                inp_plex_token = st.text_input(
                    "Plex X-Plex-Token (opcionális)",
                    value=settings.PLEX_TOKEN or "",
                    type="password",
                    help="A könyvtár automatikus frissítéséhez letöltés után.",
                )

            with col_right:
                st.markdown("##### 📥 qBittorrent Letöltő Kliens")
                inp_qbit_host = st.text_input(
                    "qBittorrent WebUI Cím",
                    value=settings.QBITTORRENT_HOST or "http://localhost:8080",
                )
                inp_qbit_user = st.text_input(
                    "qBittorrent Felhasználónév",
                    value=settings.QBITTORRENT_USERNAME or "admin",
                )
                inp_qbit_pass = st.text_input(
                    "qBittorrent Jelszó",
                    value=settings.QBITTORRENT_PASSWORD or "adminadmin",
                    type="password",
                )
                inp_qbit_exe = st.text_input(
                    "qBittorrent.exe Elérési Útja (opcionális)",
                    value=settings.QBITTORRENT_EXE_PATH or "",
                    placeholder="pl. C:\\Program Files\\qBittorrent\\qbittorrent.exe",
                    help="Ha üresen hagyod, a rendszer automatikusan megkeresi a gépeden.",
                )

                st.write("")
                st.markdown("##### 📁 Mappák Elérési Útja (Meghajtók és Könyvtárak)")
                inp_temp_dir = st.text_input(
                    "Ideiglenes Letöltési Mappa (qBittorrent célmappa)",
                    value=str(settings.DOWNLOAD_TEMP_DIR),
                )
                inp_movies_dir = st.text_input(
                    "Plex Filmek Mappa",
                    value=str(settings.PLEX_MOVIES_DIR),
                )
                inp_series_dir = st.text_input(
                    "Plex Sorozatok Mappa",
                    value=str(settings.PLEX_SERIES_DIR),
                )

                st.write("")
                st.markdown("##### ⏱️ nCore Hit & Run (Seed) Szabályok")
                col_s1, col_s2 = st.columns(2)
                with col_s1:
                    inp_min_seed = st.number_input(
                        "Kötelező Seed (óra)",
                        min_value=1,
                        max_value=168,
                        value=int(settings.MIN_SEED_TIME_HOURS or 48),
                    )
                with col_s2:
                    inp_target_ratio = st.number_input(
                        "Cél Feltöltési Arány",
                        min_value=0.1,
                        max_value=10.0,
                        value=float(settings.TARGET_RATIO or 1.0),
                        step=0.1,
                    )
                inp_auto_remove = st.checkbox(
                    "Torrent levétele a qBittorrentből a feltételek teljesülése után (fájlok megtartásával)",
                    value=bool(settings.AUTO_REMOVE_AFTER_SEED),
                )

            st.write("")
            save_button = st.form_submit_button("💾 Beállítások Mentése", type="primary", use_container_width=True)

        if save_button:
            new_config = {
                "NCORE_USERNAME": inp_ncore_user.strip(),
                "NCORE_PASSWORD": inp_ncore_pass.strip(),
                "NCORE_PASSKEY": inp_ncore_passkey.strip() if inp_ncore_passkey else "",
                "TMDB_API_KEY": inp_tmdb_key.strip(),
                "TMDB_LANGUAGE": inp_tmdb_lang.strip(),
                "QBITTORRENT_HOST": inp_qbit_host.strip(),
                "QBITTORRENT_USERNAME": inp_qbit_user.strip(),
                "QBITTORRENT_PASSWORD": inp_qbit_pass.strip(),
                "QBITTORRENT_EXE_PATH": inp_qbit_exe.strip(),
                "PLEX_SERVER_URL": inp_plex_url.strip(),
                "PLEX_TOKEN": inp_plex_token.strip() if inp_plex_token else "",
                "DOWNLOAD_TEMP_DIR": inp_temp_dir.strip(),
                "PLEX_MOVIES_DIR": inp_movies_dir.strip(),
                "PLEX_SERIES_DIR": inp_series_dir.strip(),
                "MIN_SEED_TIME_HOURS": str(int(inp_min_seed)),
                "TARGET_RATIO": str(float(inp_target_ratio)),
                "AUTO_REMOVE_AFTER_SEED": "true" if inp_auto_remove else "false",
            }

            if save_settings(new_config):
                get_orchestrator.clear()
                st.success("✅ A beállítások sikeresen elmentve a .env fájlba és érvénybe léptek!")
                st.rerun()
            else:
                st.error("❌ Nem sikerült elmenteni a beállításokat a .env fájlba!")

        st.write("")
        st.markdown("---")
        st.markdown("#### ⏰ Windows Rendszerindítás (Autostart)")
        st.caption(
            "Engedélyezd, hogy a számítógép bekapcsolásakor az alkalmazás, a tálcaikon és a qBittorrent automatikusan elinduljon a háttérben. "
            "Így a telefonról bármikor elérhető a letöltő a PC fizikai érintése nélkül!"
        )

        is_auto = orchestrator.autostart.is_autostart_enabled()
        c_auto_toggle, c_auto_status = st.columns([2, 3])
        with c_auto_toggle:
            new_auto = st.checkbox(
                "🚀 Indítás a Windows indulásakor a háttérben",
                value=is_auto,
                key="chk_autostart",
                help="A Windows Indítópulthoz (Startup) ad hozzá egy diszkrét indítófájlt adminisztrátori jogok nélkül."
            )
            if new_auto != is_auto:
                orchestrator.autostart.set_autostart(new_auto)
                st.rerun()

        with c_auto_status:
            if is_auto:
                st.success("✅ **Bekapcsolva:** A gép újraindulásakor a rendszer és a qBittorrent automatikusan elindul a háttérben.")
            else:
                st.info("⚪ **Kikapcsolva:** Az alkalmazást manuálisan kell elindítani az asztali ikonnal.")

        st.write("")
        st.markdown("---")
        st.markdown("#### 🔍 Szolgáltatások Kapcsolatának Tesztelése")
        st.caption("Kattints a gombokra az adott kapcsolat azonnali, biztonságos ellenőrzéséhez:")

        tc1, tc2, tc3, tc4 = st.columns(4)

        with tc1:
            if st.button("🎬 TMDB Teszt", use_container_width=True):
                with st.spinner("TMDB lekérdezés..."):
                    tmdb_test = TMDBService(api_key=settings.TMDB_API_KEY)
                    res = tmdb_test.search("Top Gun", max_results=1)
                    if res:
                        st.success(f"✅ TMDB Rendben!\n(Találat: {res[0].title_hu})")
                    else:
                        st.error("❌ TMDB nem adott találatot.")

        with tc2:
            if st.button("🌐 nCore Teszt", use_container_width=True):
                with st.spinner("nCore belépés tesztelése..."):
                    ncore_test = NcoreService(
                        username=settings.NCORE_USERNAME,
                        password=settings.NCORE_PASSWORD,
                        passkey=settings.NCORE_PASSKEY,
                    )
                    if not ncore_test._is_configured():
                        st.warning("⚠️ Add meg az nCore felhasználóneved és jelszavad fent!")
                    elif ncore_test.login():
                        st.success(f"✅ nCore Belépés sikeres!\n(Felhasználó: {settings.NCORE_USERNAME})")
                    else:
                        st.error("❌ nCore belépés sikertelen! Ellenőrizd az adataid.")

        with tc3:
            if st.button("📥 qBittorrent Teszt", use_container_width=True):
                with st.spinner("qBittorrent kapcsolat..."):
                    qbit_test = TorrentService(
                        host=settings.QBITTORRENT_HOST,
                        username=settings.QBITTORRENT_USERNAME,
                        password=settings.QBITTORRENT_PASSWORD,
                    )
                    if qbit_test.connect():
                        st.success(f"✅ qBittorrent Kapcsolódva!\n({settings.QBITTORRENT_HOST})")
                    else:
                        st.error(f"❌ qBittorrent nem elérhető!\n({settings.QBITTORRENT_HOST})")

        with tc4:
            if st.button("🍿 Plex Teszt", use_container_width=True):
                with st.spinner("Plex kapcsolat..."):
                    if orchestrator.plex_service.refresh_library():
                        st.success(f"✅ Plex Kapcsolat sikeres!\n({settings.PLEX_SERVER_URL})")
                    elif orchestrator.plex_service._is_configured():
                        st.error("❌ Plex nem válaszolt a megadott címen.")
                    else:
                        st.info("ℹ️ Plex Token nincs beállítva.")

    # ==============================================================
    # 4. FÜL: HIBAKERESÉS, LOGOK ÉS DIAGNOSZTIKA
    # ==============================================================
    with tab_debug:
        st.write("")
        st.markdown("### 🛠️ Rendszer Diagnosztika és Élő Logok")
        st.caption("Itt részletesen tesztelheted a keresési logikát, megtekintheted a lefutási naplót, és kimásolhatod a jelentést.")

        diag_service = DiagnosticService(orchestrator=orchestrator)
        health = diag_service.get_system_health()

        # 1. Rendszerinformációk sáv
        c_p1, c_p2, c_p3, c_p4 = st.columns(4)
        with c_p1:
            st.metric("🐍 Python PID", f"{health['python']['pid']}")
        with c_p2:
            st.metric("⚙️ TMDB", health["services"]["tmdb"]["status"])
        with c_p3:
            st.metric("🌐 nCore", health["services"]["ncore"]["status"])
        with c_p4:
            st.metric("📥 qBittorrent", "OK" if health["services"]["qbittorrent"]["connected"] else "HIBA")

        st.markdown("---")

        # 2. Részletes Keresési Diagnosztika
        st.markdown("#### 🧪 Keresési és Elemzési Folyamat Tesztelése")
        st.write("Írj be egy film vagy sorozat címet a teljes lefutási lánc (TMDB -> nCore -> Szűrés -> Pontozás) vizsgálatához:")

        cd_input, cd_btn = st.columns([4, 1])
        with cd_input:
            diag_query = st.text_input(
                "Tesztelendő cím",
                value="Vámpír Naplók",
                key="diag_query_input",
                label_visibility="collapsed"
            )
        with cd_btn:
            run_diag = st.button("🧪 Diagnosztika Futtatása", type="primary", use_container_width=True)

        if run_diag and diag_query.strip():
            with st.spinner("Részletes diagnosztikai vizsgálat futtatása..."):
                report = diag_service.run_search_diagnostic(diag_query.strip())
                markdown_report = diag_service.format_report_as_markdown(report)

                st.session_state["diag_last_report"] = report
                st.session_state["diag_last_md"] = markdown_report

        if "diag_last_report" in st.session_state:
            rep = st.session_state["diag_last_report"]
            md_rep = st.session_state["diag_last_md"]

            st.success(f"✅ Diagnosztika lefutott a(z) *„{rep.get('query')}”* címre!")

            # Lépések megjelenítése
            for s_idx, step in enumerate(rep.get("steps", [])):
                with st.expander(f"📌 {step.get('name')}", expanded=(s_idx == 0 or "error" in step)):
                    st.json(step)

            st.markdown("##### 📋 Egy kattintással kimásolható és a chatbe beküldhető jelentés:")
            st.text_area(
                "Jelentés vágólapra másolása",
                value=md_rep,
                height=220,
                help="Jelöld ki (Ctrl+A) és másold ki (Ctrl+C), majd küldd be a chatbe!",
            )

        st.markdown("---")

        # 3. Élő Rendszernapló (Live Logs)
        st.markdown("#### 📜 Élő Rendszernapló (Legutóbbi események)")
        c_l_refresh, c_l_clear, c_l_dl = st.columns([1, 1, 2])
        with c_l_refresh:
            if st.button("🔄 Logok Frissítése", use_container_width=True):
                st.rerun()
        with c_l_clear:
            if st.button("🗑️ Puffer Törlése", use_container_width=True):
                clear_memory_logs()
                st.rerun()
        with c_l_dl:
            log_file = Path(__file__).resolve().parent.parent.parent / "logs" / "app.log"
            if log_file.exists():
                try:
                    with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                        log_content = f.read()
                    st.download_button(
                        "📥 Teljes app.log Letöltése",
                        data=log_content,
                        file_name="app.log",
                        mime="text/plain",
                        use_container_width=True
                    )
                except Exception:
                    pass

        recent_logs = get_log_text(150)
        if recent_logs:
            st.code(recent_logs, language="log")
        else:
            st.info("A napló jelenleg üres. Végezz egy műveletet a felületen az események naplózásához.")


if __name__ == "__main__":
    main()
