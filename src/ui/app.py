import socket
from pathlib import Path
import streamlit as st

from src.config.settings import get_settings
from src.core.orchestrator import Orchestrator
from src.models.media import MediaItem, MediaType

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
    return Orchestrator()


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


def main():
    settings = get_settings()
    orchestrator = get_orchestrator()
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

    # Telefonos elérési sáv
    st.markdown(
        f"""
        <div class="mobile-hint">
            📱 <b>Mobilról vagy tabletről is használható!</b> Nyisd meg a telefonod böngészőjében: 
            <code style="background: #1e293b; padding: 2px 6px; border-radius: 4px; color: #38bdf8;">http://{local_ip}:{settings.APP_PORT}</code>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Fő fülek
    tab_search, tab_downloads, tab_status = st.tabs([
        "🔍 Film & Sorozat Keresés",
        "📥 Letöltések Állapota",
        "⚙️ Rendszer & Beállítások",
    ])

    # ==============================================================
    # 1. FÜL: KERESÉS ÉS VÁLASZTÁS
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
        if (search_submitted or search_query) and search_query.strip():
            with st.spinner("Keresés a filmes adatbázisban..."):
                results = orchestrator.search_media(search_query.strip())
                st.session_state["search_results"] = results
                st.session_state["last_query"] = search_query.strip()

        results = st.session_state.get("search_results", [])
        if results:
            st.markdown(f"### Találatok a(z) *„{st.session_state.get('last_query', '')}”* keresésre:")
            st.caption("Kattints a zöld gombra a kívánt film alatt a letöltés indításához!")

            # Kártyás megjelenítés (2 oszlopban nagyobb képernyőn, mobilon automatikusan egy oszlopba rendeződik)
            cols = st.columns(2)
            for idx, item in enumerate(results):
                col = cols[idx % 2]
                with col:
                    with st.container():
                        st.markdown('<div class="movie-card">', unsafe_allow_html=True)
                        c_img, c_info = st.columns([1, 2])

                        with c_img:
                            if item.poster_url:
                                st.image(item.poster_url, use_column_width=True)
                            else:
                                st.image("https://via.placeholder.com/300x450.png?text=Nincs+Kép", use_column_width=True)

                        with c_info:
                            type_tag = "🎬 Film" if item.media_type == MediaType.MOVIE else "📺 Sorozat"
                            year_txt = f"({item.year})" if item.year else ""
                            rating_txt = f"⭐ {item.rating}/10" if item.rating else ""

                            st.markdown(f"<div class='movie-title'>{item.title_hu} {year_txt}</div>", unsafe_allow_html=True)
                            st.markdown(
                                f"<div class='movie-meta'><b>{type_tag}</b> | {rating_txt} | <i>{', '.join(item.genres[:3])}</i></div>",
                                unsafe_allow_html=True,
                            )

                            if item.director:
                                st.caption(f"🎬 **Rendező:** {item.director}")
                            if item.cast:
                                st.caption(f"🎭 **Szereplők:** {', '.join(item.cast[:3])}")

                            # Rövid leírás
                            overview_snippet = item.overview[:180] + "..." if len(item.overview) > 180 else item.overview
                            st.markdown(f"<div class='movie-overview'>{overview_snippet}</div>", unsafe_allow_html=True)

                            # Letöltés gomb
                            btn_key = f"dl_btn_{item.tmdb_id}_{idx}"
                            if st.button("🎬 Igen, ezt kérem!", key=btn_key, type="primary"):
                                with st.spinner("Keresés nCore-on és beküldés a qBittorrentbe..."):
                                    res = orchestrator.start_download_flow(item, prefer_hungarian=True)
                                    if res.get("success"):
                                        st.success(f"🎉 **{res['message']}**")
                                        if res.get("has_hungarian_audio"):
                                            st.info("🔊 Magyar szinkronos verzió indítva!")
                                        elif res.get("has_subtitle"):
                                            st.info("📝 Angol verzió indítva + magyar felirat automatikusan társítva!")
                                        else:
                                            st.info("Eredeti nyelvű kiadás letöltése indul.")
                                        st.balloons()
                                    else:
                                        st.error(f"❌ {res.get('message')}")

                        st.markdown("</div>", unsafe_allow_html=True)

        elif "search_results" in st.session_state and not results:
            st.info("Nem található film vagy sorozat ezzel a címmel. Próbáld meg az eredeti angol címet vagy pontosabb kifejezést!")

    # ==============================================================
    # 2. FÜL: LETÖLTÉSEK ÁLLAPOTA
    # ==============================================================
    with tab_downloads:
        st.write("")
        c_head, c_refresh = st.columns([5, 1])
        with c_head:
            st.markdown("### 📥 Aktív és Befejezett Letöltések")
            st.caption("A qBittorrentből kinyert valós idejű letöltési és megosztási állapotok.")
        with c_refresh:
            if st.button("🔄 Frissítés", use_container_width=True):
                st.rerun()

        downloads = orchestrator.get_active_downloads()

        if not downloads:
            st.info("Jelenleg nincs aktív letöltés a várólistában.")
        else:
            for item in downloads:
                with st.expander(f"📁 {item['name']} ({item['progress']}%)", expanded=True):
                    col_p, col_stat = st.columns([3, 1])
                    with col_p:
                        st.progress(item["progress"] / 100.0)
                        st.caption(f"Állapot: **{item['state']}** | Letöltési sebesség: **{item.get('download_speed_kb', 0)} KB/s**")
                    with col_stat:
                        if item.get("is_completed"):
                            st.success("✅ Letöltve (Plexben elérhető!)")
                            st.caption(f"Seedelve: {item.get('seeding_time_hours', 0)}h / 48h (Arány: {item.get('ratio', 0)})")
                        else:
                            st.warning(f"⏳ Letöltés alatt ({item['progress']}%)")

        st.write("")
        if st.button("🍿 Plex Könyvtár Kézi Frissítése"):
            with st.spinner("Plex szerver értesítése..."):
                if orchestrator.plex_service.refresh_library():
                    st.success("Plex könyvtár sikeresen frissítve!")
                else:
                    st.warning("Nem sikerült elérni a Plex szervert. Ellenőrizd a beállításokat!")

    # ==============================================================
    # 3. FÜL: RENDSZER & BEÁLLÍTÁSOK DIAGNOSZTIKA
    # ==============================================================
    with tab_status:
        st.markdown("### ⚙️ Rendszer és Szolgáltatások Állapota")
        st.caption("Itt ellenőrizheted, hogy minden külső szolgáltatás megfelelően van-e beállítva.")

        col1, col2 = st.columns(2)
        with col1:
            st.markdown("#### 1. Külső Szolgáltatások")

            # TMDB
            if settings.TMDB_API_KEY and settings.TMDB_API_KEY != "ide_ird_a_tmdb_api_kulcsodat":
                st.success("✅ **TMDB Filmadatbázis**: Beállítva és kész")
            else:
                st.warning("⚠️ **TMDB API kulcs**: Nincs beállítva a `.env` fájlban!")

            # nCore
            if settings.NCORE_USERNAME and settings.NCORE_USERNAME != "felhasznaloneved":
                st.success(f"✅ **nCore**: Beállítva (Felhasználó: {settings.NCORE_USERNAME})")
            else:
                st.warning("⚠️ **nCore Belépési adatok**: Nincsenek megadva a `.env` fájlban!")

            # qBittorrent
            if orchestrator.torrent_service.is_connected() or orchestrator.torrent_service.connect():
                st.success(f"✅ **qBittorrent WebUI**: Csatlakozva ({settings.QBITTORRENT_HOST})")
            else:
                st.error(f"❌ **qBittorrent WebUI**: Nem elérhető! ({settings.QBITTORRENT_HOST}) Indítsd el a qBittorrentet!")

            # Plex
            if orchestrator.plex_service._is_configured():
                st.success(f"✅ **Plex Médiaszerver**: Beállítva ({settings.PLEX_SERVER_URL})")
            else:
                st.info("ℹ️ **Plex Token**: Még nincs beállítva (opcionális, de ajánlott)")

        with col2:
            st.markdown("#### 2. Mappák Útvonalai")
            st.write(f"📂 **Letöltési ideiglenes mappa:** `{settings.DOWNLOAD_TEMP_DIR}`")
            st.write(f"🎬 **Plex Filmek mappa:** `{settings.PLEX_MOVIES_DIR}`")
            st.write(f"📺 **Plex Sorozatok mappa:** `{settings.PLEX_SERIES_DIR}`")

            st.write("")
            st.markdown("#### 3. nCore Hit & Run Szabályok")
            st.write(f"⏱️ **Kötelező seed idő:** `{settings.MIN_SEED_TIME_HOURS} óra`")
            st.write(f"📈 **Cél feltöltési arány:** `{settings.TARGET_RATIO}`")
            st.write(f"🗑️ **Fájlok megőrzése törléskor:** `Igen (csak a torrentet veszi le)`")


if __name__ == "__main__":
    main()
