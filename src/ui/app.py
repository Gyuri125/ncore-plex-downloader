import io
import shutil
import socket
from typing import List, Optional, Dict, Any
from pathlib import Path
import streamlit as st

from src.config.settings import get_settings, save_settings
from src.core.logger import setup_logging, get_log_text, clear_memory_logs
from src.core.orchestrator import Orchestrator
from src.models.media import MediaItem, MediaType, TorrentRelease
from src.services.diagnostic_service import DiagnosticService
from src.services.ncore_service import NcoreService
from src.services.plex_service import PlexService
from src.services.tmdb_service import TMDBService
from src.services.torrent_service import TorrentService
from src.ui.styles import apply_custom_styles, get_app_header_html, get_mobile_hint_html

# Naplózás inicializálása
setup_logging()

# Oldal konfiguráció (széles elrendezés, sötét téma, mobilbarát nézet)
st.set_page_config(
    page_title="Ncore Média Letöltő v2.0.0",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Egyedi stílusok alkalmazása
apply_custom_styles()


@st.cache_resource
def get_orchestrator() -> Orchestrator:
    orch = Orchestrator()
    if not hasattr(orch, "radar") or orch.radar is None:
        from src.core.movie_radar import MovieRadarManager
        orch.radar = MovieRadarManager()
    if not hasattr(orch, "series_radar") or orch.series_radar is None:
        from src.core.series_radar import SeriesRadarManager
        orch.series_radar = SeriesRadarManager()
    if not hasattr(orch, "watchlist_sync") or orch.watchlist_sync is None:
        from src.core.watchlist_sync import WatchlistSyncService
        orch.watchlist_sync = WatchlistSyncService()
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


def safe_rerun(is_in_dialog: bool = False):
    """
    Biztonságos frissítés: ha párbeszédablakban (fragment) vagyunk,
    csak a fragmentet futtatjuk újra (scope='fragment'), így nem záródik be az ablak.
    Ha a főoldalon vagyunk, a teljes alkalmazást frissítjük.
    """
    if is_in_dialog:
        st.rerun(scope="fragment")
    else:
        st.rerun()



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
def show_movie_details_dialog(item: MediaItem, orchestrator: Orchestrator, key_prefix: str = "item"):

    # Ha a felhasználó rákattintott egy színészre vagy rendezőre az adatlapon belül:
    selected_person = st.session_state.get(f"det_selected_person_{item.tmdb_id}")
    if selected_person:
        if st.button(f"⬅️ Vissza a(z) {item.title_hu} adatlapjához", key=f"back_det_{item.tmdb_id}", type="secondary"):
            st.session_state.pop(f"det_selected_person_{item.tmdb_id}", None)
            safe_rerun(is_in_dialog=True)

        with st.spinner(f"{selected_person} alkotásainak betöltése a TMDB-ről..."):
            person_data = orchestrator.get_person_filmography(selected_person, limit=6)

        st.markdown(f"### 🎭 {selected_person} legnépszerűbb alkotásai")
        if person_data and person_data.get("items"):
            p_cols = st.columns(2)
            for idx, p_it in enumerate(person_data["items"]):
                with p_cols[idx % 2]:
                    render_media_card(p_it, orchestrator, key_prefix=f"dlg_p_{idx}_{p_it.tmdb_id}")
        else:
            st.info("Nem található további film ehhez az alkotóhoz.")
        return

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
        if item.title_en and item.title_en.strip().lower() != item.title_hu.strip().lower():
            st.markdown(f"<div style='color: #38bdf8; font-size: 1.05rem; margin-top: -8px; margin-bottom: 6px;'>🇬🇧 Nemzetközi / Angol cím: <b>{item.title_en}</b></div>", unsafe_allow_html=True)
        if item.original_title and item.original_title.strip().lower() != (item.title_en or "").strip().lower() and item.original_title.strip().lower() != item.title_hu.strip().lower():
            orig_lang_txt = f" ({item.original_language})" if item.original_language else ""
            st.caption(f"Eredeti cím{orig_lang_txt}: *{item.original_title}*")

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
                            st.session_state[f"det_selected_person_{item.tmdb_id}"] = item.director
                            safe_rerun(is_in_dialog=True)
                    c_idx += 1
                if item.cast:
                    for act_name in item.cast[:3]:
                        if c_idx < len(creator_cols):
                            with creator_cols[c_idx]:
                                if st.button(f"🎭 {act_name}", key=f"det_act_{item.tmdb_id}_{c_idx}", use_container_width=True):
                                    st.session_state[f"det_selected_person_{item.tmdb_id}"] = act_name
                                    safe_rerun(is_in_dialog=True)
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
        if st.button("📋 Kiadások lenyitása a kártyán", key=f"dlg_open_{item.tmdb_id}", use_container_width=True):
            st.session_state[f"show_releases_{key_prefix}_{item.tmdb_id}"] = True
            st.rerun()


@st.dialog("✨ Hasonló Filmek & Ajánlások", width="large")
def show_similar_dialog(item: MediaItem, orchestrator: Orchestrator):
    """Megjeleníti az adott filmhez/sorozathoz leginkább hasonló alkotásokat."""
    # Ha van navigációs előzmény vagy váltás:
    current_item = st.session_state.get("similar_current_root_item") or item

    sim_history = st.session_state.get("similar_history", [])
    if sim_history:
        prev_item = sim_history[-1]
        prev_en = f" ({prev_item.title_en})" if (prev_item.title_en and prev_item.title_en.strip().lower() != prev_item.title_hu.strip().lower()) else ""
        if st.button(f"⬅️ Vissza az előző filmhez: {prev_item.title_hu}{prev_en}", key="btn_sim_history_back", type="secondary"):
            st.session_state["similar_current_root_item"] = sim_history.pop()
            st.session_state.pop("similar_selected_sub_item", None)
            safe_rerun(is_in_dialog=True)

    # Ha a felhasználó a Hasonló filmek közül kiválasztott egyet részletes előnézetre:
    selected_sub_item = st.session_state.get("similar_selected_sub_item")
    if selected_sub_item:
        if st.button("⬅️ Vissza a hasonló filmek listájához", key="btn_back_to_sim_list", type="secondary"):
            st.session_state.pop("similar_selected_sub_item", None)
            safe_rerun(is_in_dialog=True)

        sub_en = f" *({selected_sub_item.title_en})*" if (selected_sub_item.title_en and selected_sub_item.title_en.strip().lower() != selected_sub_item.title_hu.strip().lower()) else ""
        st.markdown(f"### 🎬 {selected_sub_item.title_hu}{sub_en} ({selected_sub_item.year or ''})")
        if selected_sub_item.backdrop_url:
            st.image(selected_sub_item.backdrop_url, use_container_width=True)

        col_l, col_r = st.columns([1, 2])
        with col_l:
            if selected_sub_item.poster_url:
                st.image(selected_sub_item.poster_url, use_container_width=True)
            if selected_sub_item.rating:
                st.markdown(f"⭐ **Értékelés:** {selected_sub_item.rating}/10")
            if selected_sub_item.runtime_minutes:
                st.markdown(f"⏱️ **Hossz:** {selected_sub_item.runtime_minutes} perc")
            if selected_sub_item.genres:
                st.caption(f"🎭 *{', '.join(selected_sub_item.genres)}*")
        with col_r:
            st.markdown("#### 📖 Szinopszis")
            st.markdown(selected_sub_item.overview or "Nincs leírás.")
            if selected_sub_item.director:
                st.caption(f"🎬 **Rendező:** {selected_sub_item.director}")
            if selected_sub_item.cast:
                st.caption(f"🎭 **Főbb szereplők:** {', '.join(selected_sub_item.cast[:3])}")

        # Előnézeti letöltés siker üzenet
        prev_dl_msg = st.session_state.get(f"sim_prev_dl_{selected_sub_item.tmdb_id}")
        if prev_dl_msg:
            st.success(f"🎉 **{prev_dl_msg}**")

        st.write("")
        c_dl_s1, c_dl_s2 = st.columns(2)
        with c_dl_s1:
            if st.button("⚡ Letöltés indítása", key=f"sim_sub_dl_{selected_sub_item.tmdb_id}", type="primary", use_container_width=True):
                with st.spinner("Kiadás keresése és indítás..."):
                    res = orchestrator.start_download_flow(selected_sub_item, prefer_hungarian=True)
                    if res.get("success"):
                        st.session_state[f"sim_prev_dl_{selected_sub_item.tmdb_id}"] = res["message"]
                        st.balloons()
                        safe_rerun(is_in_dialog=True)
                    else:
                        st.error(f"❌ {res.get('message')}")
        with c_dl_s2:
            sub_chk_label = "🔼 Kiadások elrejtése" if st.session_state.get(f"show_releases_sim_{selected_sub_item.tmdb_id}") else "🔍 nCore kiadások megtekintése"
            if st.button(sub_chk_label, key=f"sim_sub_chk_{selected_sub_item.tmdb_id}", use_container_width=True):
                new_state = not st.session_state.get(f"show_releases_sim_{selected_sub_item.tmdb_id}", False)
                if new_state and f"cached_releases_{selected_sub_item.tmdb_id}" not in st.session_state:
                    with st.spinner("nCore ellenőrzése..."):
                        rels = orchestrator.get_available_releases(selected_sub_item, prefer_hungarian=True)
                        st.session_state[f"cached_releases_{selected_sub_item.tmdb_id}"] = rels
                st.session_state[f"show_releases_sim_{selected_sub_item.tmdb_id}"] = new_state
                safe_rerun(is_in_dialog=True)

        if st.session_state.get(f"show_releases_sim_{selected_sub_item.tmdb_id}"):
            rels = st.session_state.get(f"cached_releases_{selected_sub_item.tmdb_id}", [])
            if rels:
                st.markdown("##### 📦 Elérhető nCore változatok:")
                for r in rels[:5]:
                    r_c1, r_c2 = st.columns([3, 1])
                    with r_c1:
                        audio_tag = "🔊 Magyar" if r.has_hungarian_audio else "🇬🇧 Eredeti"
                        st.caption(f"**{r.title}** ({r.size_human} • {audio_tag})")
                    with r_c2:
                        if st.button("📥 Letöltés", key=f"sim_rel_dl_{r.torrent_id}", use_container_width=True):
                            res = orchestrator.download_specific_release(selected_sub_item, r)
                            if res.get("success"):
                                st.session_state[f"sim_prev_dl_{selected_sub_item.tmdb_id}"] = res["message"]
                                st.balloons()
                                safe_rerun(is_in_dialog=True)
                            else:
                                st.error(res.get("message"))
            else:
                st.info("Nem található elérhető kiadás az nCore-on ehhez a címhez.")
        return

    st.markdown(f"### ✨ Filmek, amik tetszeni fognak ehhez: *„{current_item.title_hu}”*")
    st.caption("A TMDB intelligens ajánló motorja által kiválasztott rokon alkotások azonnali nCore letöltéssel:")
    with st.spinner("Hasonló filmek keresése a TMDB-n..."):
        similar_items = orchestrator.get_similar_media(current_item, limit=6)
    if not similar_items:
        st.info("Nem találtunk közvetlenül hasonló címet ebben a kategóriában.")
        return

    s_cols = st.columns(2)
    for s_idx, s_item in enumerate(similar_items):
        with s_cols[s_idx % 2]:
            render_media_card(s_item, orchestrator, key_prefix=f"sim_{s_idx}_{s_item.tmdb_id}")


@st.dialog("⚠️ Letöltés Megerősítése", width="large")
def show_download_confirm_dialog(
    item: MediaItem,
    release: TorrentRelease,
    releases: List[TorrentRelease],
    orchestrator: Orchestrator,
    key_prefix: str = "main",
):
    """
    Részletes megerősítő ablak a letöltés indítása előtt.
    Egyértelműen tájékoztat a nyelvről (szinkron/eredeti), felbontásról és a méretről.
    """
    st.markdown("### 🎬 Biztosan ezt a kiadást szeretnéd letölteni?")
    dl_en = f" *({item.title_en})*" if (item.title_en and item.title_en.strip().lower() != item.title_hu.strip().lower()) else ""
    st.markdown(f"#### **{item.title_hu}**{dl_en} ({item.year or ''})")

    col_poster, col_info = st.columns([1, 2.2])
    with col_poster:
        if item.poster_url:
            st.image(item.poster_url, use_container_width=True)
        else:
            st.image("https://via.placeholder.com/200x300.png?text=Film", use_container_width=True)

    with col_info:
        specs = release.specs

        # 1. Nyelv / Szinkron állapot kiemelt vizuális dobozban
        if release.has_hungarian_audio:
            st.markdown(
                """
                <div style="background: rgba(16, 185, 129, 0.15); border: 1px solid #10b981; 
                            border-radius: 8px; padding: 12px 14px; margin-bottom: 12px;">
                    <div style="color: #10b981; font-weight: bold; font-size: 1.05rem;">
                        🔊 Magyar Szinkron: ELÉRHETŐ! ✅
                    </div>
                    <div style="color: #cbd5e1; font-size: 0.85rem; margin-top: 3px;">
                        Jó hír! Ez a kiadás tartalmaz minőségi magyar szinkront, így magyarul fog szólni a TV-n.
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f"""
                <div style="background: rgba(239, 68, 68, 0.15); border: 1px solid #ef4444; 
                            border-radius: 8px; padding: 12px 14px; margin-bottom: 8px;">
                    <div style="color: #f87171; font-weight: bold; font-size: 1.05rem;">
                        🇬🇧 Figyelem: Csak Eredeti Hang (Nincs magyar szinkron)! ⚠️
                    </div>
                    <div style="color: #cbd5e1; font-size: 0.85rem; margin-top: 3px;">
                        Ehhez a címhez még nem jelent meg magyar szinkron az nCore-on.
                    </div>
                </div>
                <div style="background: rgba(14, 165, 233, 0.12); border: 1px solid #0ea5e9; 
                            border-radius: 8px; padding: 10px 14px; margin-bottom: 12px;">
                    <div style="color: #38bdf8; font-weight: bold; font-size: 0.95rem;">
                        💬 Automatikus Magyar Felirat Csatolás (.hu.srt)
                    </div>
                    <div style="color: #cbd5e1; font-size: 0.82rem; margin-top: 4px; line-height: 1.4;">
                        &bull; <b>Forrás:</b> Feliratok.info (elsődleges) és OpenSubtitles (tartalék).<br>
                        &bull; <b>Hitelesség & Időzítés:</b> A rendszer a release neve alapján ({specs['resolution']} {specs['source']}) pontosan illeszkedő feliratot választ.<br>
                        &bull; <b>Elhelyezés:</b> A videofájl mellé kerül UTF-8 kódolással (<code>{item.title_hu}.hu.srt</code>), így a Plex és az okostévék azonnal felajánlják!<br>
                        &bull; <b>Saját felirat feltöltése:</b> Ha az automatika nem találna feliratot, a letöltési előzményekben a böngészőből saját <code>.srt</code> fájlt is csatolhatsz hozzá!
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        # 2. Minőség és Méret adatok
        st.markdown(
            f"""
            <div style="background: rgba(15, 23, 42, 0.5); border: 1px solid rgba(255,255,255,0.08); 
                        border-radius: 8px; padding: 10px 14px; margin-bottom: 14px; font-size: 0.9rem;">
                <div>📺 <b>Felbontás / Minőség:</b> <span style="color: #38bdf8; font-weight: bold;">{specs['resolution']} ({specs['hdr']})</span> • {specs['source']}</div>
                <div style="margin-top: 4px;">💾 <b>Helyfoglalás a lemezen:</b> <span style="color: #f59e0b; font-weight: bold;">{release.size_human}</span></div>
                <div style="margin-top: 4px; font-size: 0.8rem; color: #94a3b8;">📦 Kiadás neve: <code>{release.title}</code></div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # 3. Lemezkapacitás ellenőrzés
        space_info = orchestrator.check_disk_space_for_release(release)
        if not space_info["has_enough"]:
            st.error(f"⚠️ Kevés a hely a merevlemezen! Szabad: {space_info['free_gb']} GB, szükséges: {space_info['needed_gb']} GB")

    st.markdown("---")
    c_btn1, c_btn2, c_btn3 = st.columns([1.6, 1.8, 1])

    with c_btn1:
        if st.button("🚀 Igen, letöltöm ezt a kiadást!", type="primary", use_container_width=True, key=f"confirm_yes_{release.torrent_id}"):
            with st.spinner("Letöltés indítása a qBittorrentben..."):
                res = orchestrator.download_specific_release(item, release)
                if res.get("success"):
                    st.success(f"🎉 **{res['message']}**")
                    st.balloons()
                else:
                    st.error(f"❌ {res.get('message')}")
            st.rerun()

    with c_btn2:
        if st.button("📋 Megnézem a többi változatot is", use_container_width=True, key=f"confirm_view_other_{release.torrent_id}"):
            st.session_state[f"show_releases_{key_prefix}_{item.tmdb_id}"] = True
            st.rerun()

    with c_btn3:
        if st.button("❌ Mégsem", use_container_width=True, key=f"confirm_cancel_{release.torrent_id}"):
            st.rerun()


@st.dialog("📦 Filmgyűjtemény (Franchise / Saga)", width="large")
def show_collection_dialog(collection_id: int, orchestrator: Orchestrator):
    """Megjeleníti a teljes franchise részeit sorban, és intelligens hiányzó-részek letöltést biztosít."""
    with st.spinner("Gyűjtemény részeinek felkutatása..."):
        coll = orchestrator.get_collection_details(collection_id)
    if not coll or not coll.get("parts"):
        st.error("Nem sikerült lekérni a gyűjtemény adatait a TMDB-ről.")
        return

    coll_name = coll.get("name", "Filmgyűjtemény")
    parts = coll.get("parts", [])

    # Plex könyvtár-összehasonlítás a gyors memóriagyorsítótárral
    existing_in_plex = []
    missing_parts = []
    if orchestrator.plex_service.is_configured:
        for p in parts:
            try:
                if orchestrator.plex_service.has_media(p.title_hu, p.year, alt_title=p.title_en):
                    existing_in_plex.append(p)
                else:
                    missing_parts.append(p)
            except Exception:
                missing_parts.append(p)
    else:
        missing_parts = list(parts)

    c_coll_img, c_coll_info = st.columns([1, 2.5])
    with c_coll_img:
        if coll.get("poster_url"):
            st.image(coll["poster_url"], use_container_width=True)
    with c_coll_info:
        st.markdown(f"## 📦 {coll_name}")
        st.caption(f"Összesen: **{len(parts)} db film** időrendi sorrendben")
        if coll.get("overview"):
            st.markdown(f"<div style='font-size: 0.88rem; color: #94a3b8; margin-bottom: 12px;'>{coll['overview']}</div>", unsafe_allow_html=True)

        if existing_in_plex:
            st.markdown(
                f"<div style='background: rgba(16, 185, 129, 0.12); border-left: 3px solid #10b981; padding: 6px 12px; border-radius: 6px; font-size: 0.85rem; color: #6ee7b7; margin-bottom: 12px;'>"
                f"🍿 <b>{len(existing_in_plex)} / {len(parts)} film</b> már megtalálható a Plex szervereden!"
                f"</div>",
                unsafe_allow_html=True,
            )

        btn_label = f"🚀 Hiányzó részek letöltése ({len(missing_parts)} db film)" if missing_parts else "🍿 Az összes rész már a Plexben van!"
        btn_type = "primary" if missing_parts else "secondary"
        if st.button(btn_label, type=btn_type, use_container_width=True, key=f"btn_dl_all_coll_{collection_id}", disabled=(len(missing_parts) == 0)):
            with st.spinner(f"A(z) {coll_name} hiányzó filmjeinek felkutatása nCore-on és beküldése..."):
                res = orchestrator.download_collection_all(collection_id, skip_existing_in_plex=True)
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
            is_in_plex = any(ep.tmdb_id == p_item.tmdb_id for ep in existing_in_plex)
            if is_in_plex:
                st.markdown("<div style='display: inline-block; background: rgba(16, 185, 129, 0.2); color: #34d399; font-size: 0.78rem; font-weight: 600; padding: 2px 8px; border-radius: 4px; margin-bottom: 4px;'>🍿 Már a Plexben</div>", unsafe_allow_html=True)
            render_media_card(p_item, orchestrator, key_prefix=f"coll_{collection_id}_{p_idx}_{p_item.tmdb_id}")


@st.dialog("📋 Külső Watchlist Szinkronizáció", width="large")
def show_watchlist_sync_dialog(orchestrator: Orchestrator):
    """
    Külső Watchlist (IMDb / Letterboxd / Trakt / CSV export) importálása és szinkronizálása.
    Összeveti a Plex könyvtárral, és automatikusan elindítja a letöltést nCore-on vagy hozzáadja a Mozi-Radarhoz.
    """
    st.markdown("### 📥 Külső Watchlist Szinkronizáció")
    st.markdown(
        "<div style='font-size: 0.88rem; color: #94a3b8; margin-bottom: 12px;'>"
        "Szinkronizáld az IMDb, Letterboxd vagy Trakt kívánságlistádat egyetlen kattintással! "
        "A rendszer összeveti a filmeket a Plex könyvtáraddal: ami már megvan, azt átugorja, "
        "ami elérhető az nCore-on, azt azonnal letölti, ami pedig még mozis vagy jövőbeli, azt felveszi a Mozi-Radarba!"
        "</div>",
        unsafe_allow_html=True,
    )

    tab_url, tab_csv = st.tabs(["🌐 Link vagy Felhasználónév", "📁 Exportált CSV Fájl"])

    input_text = ""
    csv_bytes = None

    with tab_url:
        st.caption("Másold be a nyilvános listád / profilod URL-jét vagy a felhasználónevedet:")
        input_text = st.text_input(
            "Watchlist URL vagy Felhasználónév",
            placeholder="pl. https://letterboxd.com/username/watchlist/ vagy https://www.imdb.com/user/ur12345678/watchlist",
            key="sync_url_input",
        )
        st.markdown(
            "<div style='font-size: 0.75rem; color: #64748b; margin-top: -6px; margin-bottom: 10px;'>"
            "💡 Támogatott: Letterboxd profil/watchlist link, IMDb nyilvános lista URL, vagy Trakt lista."
            "</div>",
            unsafe_allow_html=True,
        )

    with tab_csv:
        st.caption("Töltsd fel a letöltött watchlist.csv fájlt (IMDb / Letterboxd appból exportálva):")
        uploaded_file = st.file_uploader(
            "Válaszd ki a CSV fájlt",
            type=["csv"],
            key="sync_csv_uploader",
            help="100%-os megbízhatóságú közvetlen beolvasás webes korlátozások nélkül",
        )
        if uploaded_file is not None:
            csv_bytes = uploaded_file.getvalue()

    st.markdown("<hr style='margin: 12px 0; border: none; border-top: 1px solid rgba(255,255,255,0.08);'/>", unsafe_allow_html=True)

    col_opt1, col_opt2 = st.columns(2)
    with col_opt1:
        auto_download = st.checkbox("🚀 Automatikus letöltés indítása nCore-on", value=True, help="Azonnal letölti a legjobb magyar szinkronos kiadást, ha elérhető")
    with col_opt2:
        auto_radar = st.checkbox("🔔 Hozzáadás Mozi-Radarhoz", value=True, help="Ha még nincs jó minőségű digitális release, figyelőbe teszi")

    col_act1, col_act2 = st.columns([2, 1])
    with col_act1:
        btn_start = st.button("🚀 Szinkronizálás és Elemzés Indítása", type="primary", use_container_width=True)
    with col_act2:
        if st.button("❌ Bezárás", use_container_width=True):
            st.rerun()

    if btn_start:
        parsed_items = []
        if csv_bytes:
            with st.spinner("CSV fájl beolvasása..."):
                parsed_items = orchestrator.watchlist_sync.parse_csv_content(csv_bytes)
        elif input_text.strip():
            with st.spinner("Adatok lekérése a megadott forrásból..."):
                parsed_items = orchestrator.watchlist_sync.parse_input(input_text.strip())
        else:
            st.warning("Kérlek adj meg egy webes linket vagy tölts fel egy CSV fájlt a szinkronizáláshoz!")
            return

        if not parsed_items:
            st.error("Nem sikerült filmeket kinyerni a megadott forrásból. Ellenőrizd a link nyilvánosságát vagy a CSV fájl formátumát!")
            return

        st.info(f"📊 **{len(parsed_items)} db film** azonosítva a listában. Megkezdődik az adatok elemzése és egyeztetése...")

        prog_bar = st.progress(0.0)
        prog_status = st.empty()

        def _on_progress(cur, total, item_name):
            prog_bar.progress(cur / total)
            prog_status.caption(f"Feldolgozás ({cur}/{total}): **{item_name}**...")

        sync_result = orchestrator.watchlist_sync.sync_items(
            raw_items=parsed_items,
            orchestrator=orchestrator,
            auto_download=auto_download,
            auto_radar=auto_radar,
            max_items=50,
            progress_callback=_on_progress,
        )
        prog_bar.empty()
        prog_status.empty()

        st.markdown("<hr style='margin: 14px 0; border: none; border-top: 1px solid rgba(255,255,255,0.08);'/>", unsafe_allow_html=True)
        st.markdown("#### 📋 Szinkronizáció Eredménye:")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Összes Elemzett", f"{sync_result['processed_count']} db")
        m2.metric("🚀 Letöltés Indítva", f"{sync_result['downloaded_count']} db")
        m3.metric("🔔 Mozi-Radarhoz Adva", f"{sync_result['radar_count']} db")
        m4.metric("🍿 Már a Plexben", f"{sync_result['plex_count']} db")

        if sync_result["downloaded_count"] > 0:
            st.balloons()

        results = sync_result.get("results", [])
        if results:
            st.markdown("<div style='margin-top: 14px; max-height: 380px; overflow-y: auto; padding-right: 6px;'>", unsafe_allow_html=True)
            for res in results:
                status_color = res.get("status_color", "#94a3b8")
                status_label = res.get("status_label", "")
                rel_info = f"<div style='font-size: 0.75rem; color: #64748b; font-family: monospace;'>📦 {res.get('release_title')}</div>" if res.get("release_title") else ""
                year_str = f"({res.get('year')})" if res.get("year") else ""
                st.markdown(
                    f"""
                    <div style="background: rgba(30, 41, 59, 0.6); border: 1px solid rgba(255,255,255,0.06);
                                border-left: 4px solid {status_color}; border-radius: 8px; padding: 10px 14px; margin-bottom: 8px;
                                display: flex; justify-content: space-between; align-items: center;">
                        <div>
                            <div style="font-weight: 600; font-size: 0.95rem; color: #f1f5f9;">{res.get('title')} <span style="color: #94a3b8; font-weight: normal;">{year_str}</span></div>
                            {rel_info}
                        </div>
                        <div>
                            <span style="background: {status_color}22; color: {status_color}; border: 1px solid {status_color}55; padding: 4px 10px; border-radius: 6px; font-size: 0.8rem; font-weight: 600;">
                                {status_label}
                            </span>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            st.markdown("</div>", unsafe_allow_html=True)


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
    is_in_dialog = (
        key_prefix.startswith("sim_")
        or key_prefix.startswith("coll_")
        or key_prefix.startswith("person_")
        or key_prefix.startswith("dlg_")
    )

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
            in_plex = orchestrator.plex_service.has_media(item.title_hu, item.year, alt_title=item.title_en)
            plex_url = orchestrator.settings.PLEX_SERVER_URL
            plex_badge = f"<a href='{plex_url}' target='_blank' style='background: #059669; color: white; padding: 2px 7px; border-radius: 4px; font-size: 0.72rem; text-decoration: none; margin-left: 6px;' title='Megnyitás a Plexben'>✅ Plexben van ▶️</a>" if in_plex else ""

            show_en = bool(item.title_en and item.title_en.strip().lower() != item.title_hu.strip().lower())
            en_txt = f"<span style='color: #94a3b8; font-weight: normal; font-size: 0.88em;'>({item.title_en})</span> " if show_en else ""

            st.markdown(f"<div class='movie-title'>{item.title_hu} {en_txt}{year_txt}{plex_badge}</div>", unsafe_allow_html=True)
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
                        if is_in_dialog:
                            st.info(f"Rendező: {item.director}")
                        else:
                            show_person_dialog(item.director, orchestrator)
            elif item.cast:
                c_act_txt, c_act_btn = st.columns([3, 1.2])
                with c_act_txt:
                    st.caption(f"🎭 **Szereplők:** {', '.join(item.cast[:2])}")
                with c_act_btn:
                    if st.button("🎭 Filmjei", key=f"{key_prefix}_act_{item.tmdb_id}", help=f"{item.cast[0]} filmjeinek listázása"):
                        if is_in_dialog:
                            st.info(f"Szereplő: {item.cast[0]}")
                        else:
                            show_person_dialog(item.cast[0], orchestrator)

            # Rövid leírás
            overview_snippet = item.overview[:170] + "..." if len(item.overview) > 170 else item.overview
            st.markdown(f"<div class='movie-overview'>{overview_snippet}</div>", unsafe_allow_html=True)

            # Letöltés siker visszajelzés a kártyán
            card_dl_msg = st.session_state.get(f"dl_success_{key_prefix}_{item.tmdb_id}")
            if card_dl_msg:
                st.success(f"🎉 **{card_dl_msg}**")

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
                        rels = orchestrator.get_available_releases(detailed_item, prefer_hungarian=True)
                        st.session_state[cache_key] = rels
                        safe_rerun(is_in_dialog)

            # 1. Gombsor: Előzetes & Infó, Hasonló filmek és Kívánságlista
            b1_col1, b1_col2, b1_col3 = st.columns([1, 1, 1])
            with b1_col1:
                if key_prefix.startswith("sim_"):
                    if st.button("🎬 Előnézet", key=f"{key_prefix}_details_{item.tmdb_id}", use_container_width=True, help="Részletes bemutató és letöltés megtekintése"):
                        detailed_item = orchestrator.tmdb_service.get_details(item.tmdb_id, item.media_type) or item
                        st.session_state["similar_selected_sub_item"] = detailed_item
                        safe_rerun(is_in_dialog=True)
                elif is_in_dialog:
                    if st.button("🎬 Infó", key=f"{key_prefix}_details_{item.tmdb_id}", use_container_width=True):
                        st.info(f"{item.title_hu}: {item.overview[:200]}...")
                else:
                    if st.button("🎬 Infó", key=f"{key_prefix}_details_{item.tmdb_id}", use_container_width=True):
                        detailed_item = orchestrator.tmdb_service.get_details(item.tmdb_id, item.media_type) or item
                        show_movie_details_dialog(detailed_item, orchestrator, key_prefix=key_prefix)
            with b1_col2:
                if st.button("✨ Hasonló", key=f"{key_prefix}_sim_{item.tmdb_id}", use_container_width=True, help="Hasonló filmek és ajánlások"):
                    if is_in_dialog:
                        sim_history = st.session_state.setdefault("similar_history", [])
                        curr = st.session_state.get("similar_current_root_item")
                        if curr and curr.tmdb_id != item.tmdb_id:
                            sim_history.append(curr)
                        st.session_state["similar_current_root_item"] = item
                        st.session_state.pop("similar_selected_sub_item", None)
                        safe_rerun(is_in_dialog=True)
                    else:
                        st.session_state["similar_current_root_item"] = item
                        st.session_state.pop("similar_selected_sub_item", None)
                        st.session_state["similar_history"] = []
                        show_similar_dialog(item, orchestrator)
            with b1_col3:
                is_saved = orchestrator.watchlist.is_in_watchlist(item.tmdb_id)
                star_label = "⭐ Mentve" if is_saved else "🤍 Lista"
                if st.button(star_label, key=f"{key_prefix}_wl_{item.tmdb_id}", use_container_width=True):
                    orchestrator.watchlist.toggle(item)
                    safe_rerun(is_in_dialog)

            # 2. Gombsor: Mozi-Radar / Sorozat-Radar és Gyűjtemény (ha elérhető)
            is_series = (item.media_type == MediaType.TV or str(item.media_type).lower() == "tv")
            if is_series:
                is_radar = orchestrator.series_radar.is_monitored(item.tmdb_id)
                radar_label = "📺 Figyelve (Sorozat)" if is_radar else "📺 Sorozat-Radar"
                radar_help = "Új heti epizódok figyelése és automatikus letöltése az nCore-ról"
            else:
                is_radar = orchestrator.radar.is_monitored(item.tmdb_id)
                radar_label = "🔔 Figyelve (Radar)" if is_radar else "🔔 Mozi-Radar"
                radar_help = "Automatikus letöltés jó minőségben megjelenéskor"
            radar_type = "primary" if is_radar else "secondary"

            def _toggle_radar():
                if is_series:
                    orchestrator.series_radar.toggle(item)
                else:
                    orchestrator.radar.toggle(item)
                safe_rerun(is_in_dialog)

            if item.collection_id and not is_in_dialog:
                b2_c1, b2_c2 = st.columns([1.3, 1])
                with b2_c1:
                    coll_name = item.collection_name or "Gyűjtemény"
                    coll_btn_txt = f"📦 {coll_name[:18]}..." if len(coll_name) > 18 else f"📦 {coll_name}"
                    if st.button(coll_btn_txt, key=f"{key_prefix}_coll_{item.tmdb_id}", use_container_width=True, help="A teljes franchise részeinek megtekintése"):
                        show_collection_dialog(item.collection_id, orchestrator)
                with b2_c2:
                    if st.button(radar_label, key=f"{key_prefix}_radar_{item.tmdb_id}", type=radar_type, use_container_width=True, help=radar_help):
                        _toggle_radar()
            else:
                if st.button(radar_label, key=f"{key_prefix}_radar_{item.tmdb_id}", type=radar_type, use_container_width=True, help=radar_help):
                    _toggle_radar()

            # 3. Gombsor: Letöltés és Kiadások választása
            b2_col1, b2_col2 = st.columns([1, 1])
            btn_auto_key = f"dl_auto_{key_prefix}_{item.tmdb_id}"
            toggle_key = f"show_releases_{key_prefix}_{item.tmdb_id}"

            with b2_col1:
                if st.button("⚡ Letöltés", key=btn_auto_key, type="primary", use_container_width=True):
                    with st.spinner("Keresés nCore-on és adatok ellenőrzése..."):
                        detailed_item = orchestrator.tmdb_service.get_details(item.tmdb_id, item.media_type) or item
                        releases = orchestrator.get_available_releases(detailed_item, prefer_hungarian=True)
                        if not releases:
                            st.error(f"Nem található kiadás az nCore-on ehhez: {item.title_hu}")
                        else:
                            best_rel = releases[0]
                            st.session_state[cache_key] = releases

                            # Ha be van kapcsolva a letöltés előtti megerősítő ablak:
                            if getattr(orchestrator.settings, "CONFIRM_BEFORE_DOWNLOAD", True):
                                if is_in_dialog:
                                    # Dialógusban a kártyán belül jelenítjük meg a megerősítő panelt (beágyazott dialógus elkerülése)
                                    st.session_state[f"inline_confirm_{key_prefix}_{item.tmdb_id}"] = {
                                        "item": detailed_item,
                                        "release": best_rel,
                                    }
                                    safe_rerun(is_in_dialog=True)
                                else:
                                    show_download_confirm_dialog(
                                        detailed_item, best_rel, releases, orchestrator, key_prefix=key_prefix
                                    )
                            else:
                                space_info = orchestrator.check_disk_space_for_release(best_rel)
                                if not space_info["has_enough"]:
                                    st.session_state[f"space_warn_{item.tmdb_id}"] = {
                                        "release": best_rel,
                                        "item": detailed_item,
                                        "space_info": space_info,
                                    }
                                    safe_rerun(is_in_dialog)
                                else:
                                    res = orchestrator.download_specific_release(detailed_item, best_rel)
                                    if res.get("success"):
                                        st.session_state[f"dl_success_{key_prefix}_{item.tmdb_id}"] = res["message"]
                                        st.balloons()
                                        safe_rerun(is_in_dialog)
                                    else:
                                        st.error(f"❌ {res.get('message')}")

            with b2_col2:
                btn_rel_label = "🔼 Bezárás" if st.session_state.get(toggle_key, False) else "📋 Kiadások"
                if st.button(btn_rel_label, key=f"btn_toggle_{key_prefix}_{item.tmdb_id}", use_container_width=True):
                    st.session_state[toggle_key] = not st.session_state.get(toggle_key, False)
                    safe_rerun(is_in_dialog)

            # Intelligens In-Dialog Letöltés Megerősítő Panel
            confirm_box = st.session_state.get(f"inline_confirm_{key_prefix}_{item.tmdb_id}")
            if confirm_box:
                c_item = confirm_box["item"]
                c_rel = confirm_box["release"]
                c_sp = c_rel.specs

                st.markdown("<hr style='margin: 10px 0; border: none; border-top: 1px solid rgba(255,255,255,0.15);'/>", unsafe_allow_html=True)
                st.markdown("##### ⚠️ Letöltés megerősítése")

                if c_rel.has_hungarian_audio:
                    st.markdown(
                        """
                        <div style="background: rgba(16, 185, 129, 0.15); border: 1px solid #10b981; 
                                    border-radius: 8px; padding: 10px 12px; margin-bottom: 8px;">
                            <div style="color: #10b981; font-weight: bold; font-size: 0.95rem;">
                                🔊 Magyar Szinkron: ELÉRHETŐ! ✅
                            </div>
                            <div style="color: #cbd5e1; font-size: 0.8rem; margin-top: 2px;">
                                Ez a kiadás minőségi magyar szinkronos, így magyarul fog szólni a TV-n.
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        """
                        <div style="background: rgba(239, 68, 68, 0.15); border: 1px solid #ef4444; 
                                    border-radius: 8px; padding: 10px 12px; margin-bottom: 8px;">
                            <div style="color: #f87171; font-weight: bold; font-size: 0.95rem;">
                                🇬🇧 Figyelem: Csak Eredeti Hang ⚠️
                            </div>
                            <div style="color: #cbd5e1; font-size: 0.8rem; margin-top: 2px;">
                                Nincs még szinkron. A rendszer <b>automatikusan magyar feliratot</b> keres és csatol hozzá (valamint saját <code>.srt</code> fájlt is feltölthetsz hozzá)!
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

                st.markdown(
                    f"""
                    <div style="background: rgba(15, 23, 42, 0.6); border: 1px solid rgba(255,255,255,0.08); 
                                border-radius: 6px; padding: 8px 10px; margin-bottom: 10px; font-size: 0.82rem;">
                        <div>📺 <b>Felbontás / Minőség:</b> <span style="color: #38bdf8;">{c_sp['resolution']} ({c_sp['hdr']})</span> • {c_sp['source']}</div>
                        <div style="margin-top: 2px;">💾 <b>Helyfoglalás a lemezen:</b> <span style="color: #f59e0b;">{c_rel.size_human}</span></div>
                        <div style="margin-top: 2px; font-size: 0.75rem; color: #94a3b8; word-break: break-all;">📦 Kiadás neve: <code>{c_rel.title}</code></div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                c_act1, c_act2, c_act3 = st.columns([1.6, 1.6, 1])
                with c_act1:
                    if st.button("🚀 Igen, letöltöm!", key=f"in_yes_{key_prefix}_{item.tmdb_id}_{c_rel.torrent_id}", type="primary", use_container_width=True):
                        space_chk = orchestrator.check_disk_space_for_release(c_rel)
                        if not space_chk["has_enough"]:
                            st.session_state[f"space_warn_{item.tmdb_id}"] = {
                                "release": c_rel,
                                "item": c_item,
                                "space_info": space_chk,
                            }
                        else:
                            with st.spinner("Letöltés indítása a qBittorrentben..."):
                                res = orchestrator.download_specific_release(c_item, c_rel)
                                if res.get("success"):
                                    st.session_state[f"dl_success_{key_prefix}_{item.tmdb_id}"] = res["message"]
                                    st.balloons()
                                else:
                                    st.error(f"❌ {res.get('message')}")
                        st.session_state.pop(f"inline_confirm_{key_prefix}_{item.tmdb_id}", None)
                        safe_rerun(is_in_dialog)
                with c_act2:
                    if st.button("📋 Többi kiadás", key=f"in_other_{key_prefix}_{item.tmdb_id}_{c_rel.torrent_id}", use_container_width=True):
                        st.session_state.pop(f"inline_confirm_{key_prefix}_{item.tmdb_id}", None)
                        st.session_state[toggle_key] = True
                        safe_rerun(is_in_dialog)
                with c_act3:
                    if st.button("❌ Mégsem", key=f"in_cancel_{key_prefix}_{item.tmdb_id}_{c_rel.torrent_id}", use_container_width=True):
                        st.session_state.pop(f"inline_confirm_{key_prefix}_{item.tmdb_id}", None)
                        safe_rerun(is_in_dialog)

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
                            st.session_state[f"dl_success_{key_prefix}_{item.tmdb_id}"] = res["message"]
                            st.balloons()
                        else:
                            st.error(f"❌ {res.get('message')}")
                        safe_rerun(is_in_dialog)
                with c_w2:
                    if st.button("🔍 Kisebb kiadás választása", key=f"cancel_{key_prefix}_{item.tmdb_id}", use_container_width=True):
                        del st.session_state[f"space_warn_{item.tmdb_id}"]
                        st.session_state[toggle_key] = True
                        safe_rerun(is_in_dialog)

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
                                        safe_rerun(is_in_dialog)
                                    else:
                                        with st.spinner(f"Küldés qBittorrentnek ({rel.title})..."):
                                            res = orchestrator.download_specific_release(detailed_item, rel)
                                            if res.get("success"):
                                                st.session_state[f"dl_success_{key_prefix}_{item.tmdb_id}"] = res["message"]
                                                st.balloons()
                                                safe_rerun(is_in_dialog)
                                            else:
                                                st.error(f"❌ {res.get('message')}")
                            st.markdown("<hr style='margin: 6px 0; border: none; border-top: 1px solid rgba(255,255,255,0.05);'/>", unsafe_allow_html=True)

        st.markdown("</div>", unsafe_allow_html=True)


CATEGORY_GENRES: Dict[str, Dict[MediaType, List[int]]] = {
    "💥 Akció": {MediaType.MOVIE: [28], MediaType.TV: [10759]},
    "🤠 Kaland": {MediaType.MOVIE: [12], MediaType.TV: [10759]},
    "😂 Vígjáték": {MediaType.MOVIE: [35], MediaType.TV: [35]},
    "🎭 Dráma": {MediaType.MOVIE: [18], MediaType.TV: [18]},
    "😱 Horror": {MediaType.MOVIE: [27], MediaType.TV: [9648]},
    "🔪 Thriller": {MediaType.MOVIE: [53], MediaType.TV: [9648, 80]},
    "🕵️ Krimi": {MediaType.MOVIE: [80], MediaType.TV: [80]},
    "🔍 Rejtély": {MediaType.MOVIE: [9648], MediaType.TV: [9648]},
    "🚀 Sci-Fi": {MediaType.MOVIE: [878], MediaType.TV: [10765]},
    "🧙 Fantasy": {MediaType.MOVIE: [14], MediaType.TV: [10765]},
    "🎨 Animáció": {MediaType.MOVIE: [16], MediaType.TV: [16]},
    "👶 Családi": {MediaType.MOVIE: [10751], MediaType.TV: [10751, 10762]},
    "⚔️ Háborús": {MediaType.MOVIE: [10752], MediaType.TV: [10768]},
    "📜 Történelmi": {MediaType.MOVIE: [36], MediaType.TV: [10768, 18]},
    "💖 Romantikus": {MediaType.MOVIE: [10749], MediaType.TV: [18]},
    "🎥 Dokumentum": {MediaType.MOVIE: [99], MediaType.TV: [99]},
}

NATIONALITY_OPTIONS: Dict[str, Dict[str, str]] = {
    "🇰🇷 Dél-Koreai (K-Mozi / K-Drama)": {"country": "KR", "lang": "ko"},
    "🇯🇵 Japán (Anime / J-Cinema)": {"country": "JP", "lang": "ja"},
    "🇭🇺 Magyar alkotások": {"country": "HU", "lang": "hu"},
    "🇫🇷 Francia": {"country": "FR", "lang": "fr"},
    "🇪🇸 Spanyol & Latin-Amerika": {"country": "ES|MX|AR|CO", "lang": "es"},
    "🇮🇹 Olasz": {"country": "IT", "lang": "it"},
    "🇩🇪 Német": {"country": "DE|AT|CH", "lang": "de"},
    "🇬🇧 Brit (UK)": {"country": "GB", "lang": "en"},
    "🇺🇸 Amerikai (USA)": {"country": "US", "lang": "en"},
    "🇸🇪🇩🇰 Skandináv (Nordic Noir)": {"country": "SE|DK|NO|FI|IS", "lang": "sv|da|no|fi|is"},
    "🇮🇳 Indiai (Bollywood / Tollywood)": {"country": "IN", "lang": "hi|ta|te|ml|bn"},
    "🇨🇳 Kínai / Hong Kong": {"country": "CN|HK|TW", "lang": "zh|yue|cmn"},
    "🇹🇷 Török": {"country": "TR", "lang": "tr"},
    "🇵🇱 Lengyel": {"country": "PL", "lang": "pl"},
    "🇦🇺 Ausztrál": {"country": "AU", "lang": "en"},
    "🇨🇦 Kanadai": {"country": "CA", "lang": "en|fr"},
}


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
    with_origin_country: Optional[str] = None,
    with_original_language: Optional[str] = None,
) -> List[MediaItem]:
    """Segédfüggvény a különböző kategóriák, nemzetiségek és szűrők dinamikus TMDB lekéréséhez."""
    clean_q = query.strip() if query and query.strip() else None

    # Műfaj azonosítók feloldása
    genre_ids = None
    if category in CATEGORY_GENRES:
        mapping = CATEGORY_GENRES[category]
        genre_ids = mapping.get(media_type)

    # Effektív rendezési elv (Kritikuskedvenceknél alapértelmezetten a legjobb értékelés, hacsak mást nem választottak)
    effective_sort = sort_by
    if category == "⭐ Kritikuskedvencek" and sort_by == "popularity.desc":
        effective_sort = "vote_average.desc"

    # Ha a Felkapott kategóriában vagyunk alapértelmezett beállításokkal (nemzetiségi szűrő nélkül)
    if (
        category == "🔥 Felkapott"
        and not clean_q
        and not year_min
        and not year_max
        and not watch_provider
        and not with_origin_country
        and not with_original_language
        and effective_sort == "popularity.desc"
    ):
        if media_type == MediaType.MOVIE:
            return orchestrator.get_popular_movies(page=page, limit=limit)
        else:
            return orchestrator.get_popular_series(page=page, limit=limit)

    # Univerzális discover lekérés a kategória műfajaival, nemzetiséggel, streaming szolgáltatóval és rendezéssel
    return orchestrator.discover_media(
        media_type=media_type,
        genre_ids=genre_ids,
        sort_by=effective_sort,
        year_min=year_min,
        year_max=year_max,
        page=page,
        limit=limit,
        query=clean_q,
        with_watch_providers=watch_provider,
        with_origin_country=with_origin_country,
        with_original_language=with_original_language,
    )


@st.fragment(run_every=3)
def render_live_top_download_ticker(orchestrator: Orchestrator):
    """
    Fejléc alatt automatikusan megjelenő élő letöltés- és másolás-jelző sáv.
    Minden fülön látható, és 3 másodpercenként magától frissül a százalékkal, sebességgel és célkönyvtárral.
    """
    # 1. Ha éppen fájl másolódik át a Plex célmappába lemezek között
    try:
        transfer = orchestrator.get_transfer_progress()
        if transfer and transfer.get("active"):
            fn = transfer.get("filename", "Médiafájl")
            pct = transfer.get("percent", 0.0)
            spd = transfer.get("speed_mbps", 0.0)
            eta_str = transfer.get("eta_str", "Számítás alatt...")
            src_p = transfer.get("source", "Letöltési mappa")
            dst_p = transfer.get("destination", "Plex mappa")
            cop_h = transfer.get("copied_human", "")
            tot_h = transfer.get("total_human", "")
            size_txt = f"({cop_h} / {tot_h})" if cop_h and tot_h else ""
            st.markdown(
                f"""
                <div style="background: linear-gradient(90deg, rgba(16, 185, 129, 0.22) 0%, rgba(30, 41, 59, 0.6) 100%);
                            border: 1px solid #10b981; border-radius: 10px; padding: 12px 16px; margin-bottom: 14px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; font-size: 0.95rem; color: #f8fafc;">
                        <div>
                            🚚 <b>Fájl Átmozgatása a Plex Könyvtárba:</b> <span style="color: #34d399; font-weight: 600;">{fn}</span>
                        </div>
                        <div style="font-weight: bold; color: #34d399; font-size: 1.05rem;">
                            {pct}% {size_txt} • {spd} MB/s
                        </div>
                    </div>
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px; font-size: 0.78rem; color: #94a3b8; margin-top: 6px;">
                        <div>📂 <b>Honnan (Forrás):</b> <span style="color: #cbd5e1; font-family: monospace;">{src_p}</span></div>
                        <div>🎯 <b>Hová (Cél Plex):</b> <span style="color: #34d399; font-family: monospace;">{dst_p}</span></div>
                    </div>
                    <div style="font-size: 0.78rem; color: #94a3b8; margin-top: 4px;">
                        ⏱️ Hátralévő becsült idő (ETA): <b style="color: #38bdf8;">{eta_str}</b>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            st.progress(min(1.0, max(0.0, pct / 100.0)))
            return
    except Exception:
        pass

    try:
        downloads = orchestrator.get_active_downloads(include_all=True)
    except Exception:
        return

    active_dls = [d for d in downloads if not d.get("is_completed", False)]
    if not active_dls:
        return

    first_dl = active_dls[0]
    name = first_dl.get("name", "Média")
    progress = first_dl.get("progress", 0.0)
    dl_speed = first_dl.get("download_speed_kb", 0.0)
    eta_s = first_dl.get("eta_seconds", 0)

    speed_str = f"{dl_speed / 1024:.1f} MB/s" if dl_speed >= 1024 else f"{dl_speed:.0f} KB/s"
    if eta_s and 0 < eta_s < 86400 * 7:
        if eta_s < 60:
            eta_str = f"~{eta_s} mp"
        elif eta_s < 3600:
            eta_str = f"~{eta_s // 60} perc"
        else:
            eta_str = f"~{eta_s // 3600} óra {(eta_s % 3600) // 60} perc"
    else:
        eta_str = "Számítás alatt..."

    other_count = len(active_dls) - 1
    more_text = f" *(+ további {other_count} letöltés)*" if other_count > 0 else ""

    st.markdown(
        f"""
        <div style="background: linear-gradient(90deg, rgba(14, 165, 233, 0.18) 0%, rgba(30, 41, 59, 0.4) 100%);
                    border: 1px solid #0284c7; border-radius: 10px; padding: 10px 14px; margin-bottom: 14px;">
            <div style="display: flex; justify-content: space-between; align-items: center; font-size: 0.92rem; color: #f8fafc;">
                <div>
                    📥 <b>Folyamatban lévő letöltés:</b> <span style="color: #38bdf8; font-weight: 600;">{name}</span>{more_text}
                </div>
                <div style="font-weight: bold; color: #38bdf8;">
                    {progress}% • {speed_str}
                </div>
            </div>
            <div style="font-size: 0.78rem; color: #94a3b8; margin-top: 3px;">
                ⏱️ Hátralévő becsült idő: <b>{eta_str}</b> | Állapot: <b>{first_dl.get('state', 'Letöltés')}</b>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.progress(min(1.0, max(0.0, progress / 100.0)))


@st.fragment(run_every=3)
def render_active_downloads_section(orchestrator: Orchestrator):
    """
    A letöltések fülön lévő élő, automatikusan frissülő letöltési lista (3 mp-es háttérfrissítéssel).
    A felhasználó beavatkozása nélkül, magától lépteti a százalékot, sebességet, célhelyet és a HnR állapotot.
    """
    # 1. Ha éppen fájl másolódik át a célmappába
    try:
        transfer = orchestrator.get_transfer_progress()
        if transfer and transfer.get("active"):
            fn = transfer.get("filename", "Médiafájl")
            pct = transfer.get("percent", 0.0)
            spd = transfer.get("speed_mbps", 0.0)
            eta_str = transfer.get("eta_str", "Számítás alatt...")
            src_p = transfer.get("source", "Letöltési mappa")
            dst_p = transfer.get("destination", "Plex mappa")
            cop_h = transfer.get("copied_human", "")
            tot_h = transfer.get("total_human", "")
            size_txt = f"({cop_h} / {tot_h})" if cop_h and tot_h else ""
            st.markdown(
                f"""
                <div style="background: linear-gradient(90deg, rgba(16, 185, 129, 0.22) 0%, rgba(15, 23, 42, 0.7) 100%);
                            border: 1px solid #10b981; border-radius: 10px; padding: 12px 16px; margin-bottom: 14px;">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <span style="font-size: 0.95rem; font-weight: 600; color: #34d399;">
                            🚚 Média átmásolása a Plex célmappába: <b>{fn}</b>
                        </span>
                        <span style="font-weight: bold; color: #34d399; font-size: 1.05rem;">
                            {pct}% {size_txt} • {spd} MB/s
                        </span>
                    </div>
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px; font-size: 0.78rem; color: #94a3b8; margin-top: 6px;">
                        <div>📂 <b>Honnan (Forrás):</b> <span style="color: #cbd5e1; font-family: monospace;">{src_p}</span></div>
                        <div>🎯 <b>Hová (Cél Plex):</b> <span style="color: #34d399; font-family: monospace;">{dst_p}</span></div>
                    </div>
                    <div style="font-size: 0.8rem; color: #cbd5e1; margin-top: 4px;">
                        ⏱️ Hátralévő becsült idő (ETA): <b style="color: #38bdf8;">{eta_str}</b>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            st.progress(min(1.0, max(0.0, pct / 100.0)))
    except Exception:
        pass

    downloads = orchestrator.get_active_downloads(include_all=True)

    c_live1, c_live2, c_live3 = st.columns([3, 1, 1])
    with c_live1:
        st.caption("🟢 **Élő automatikus frissítés bekapcsolva (3 másodpercenként).** A százalék és sebesség magától frissül.")
    with c_live2:
        if st.button("🛠️ Feltöltési mód javítása", key="btn_fix_upload_mode", help="Ha a torrent nem indul el és 'csak feltöltés módban' ragadt be, ez a gomb kényszeríti az aktív letöltést és újraellenőrzi a darabokat.", use_container_width=True):
            fixed = orchestrator.torrent_service.fix_stuck_upload_mode()
            if fixed:
                st.success(f"✅ Sikeresen feloldva és aktív letöltésre állítva: {', '.join(fixed)}")
            else:
                st.info("ℹ️ Nincs beragadt feltöltési módban lévő torrent.")
            st.rerun(scope="fragment")
    with c_live3:
        if st.button("🔄 Kézi frissítés", key="btn_frag_manual_refresh", use_container_width=True):
            st.rerun(scope="fragment")

    if not downloads:
        st.info("Jelenleg nincs aktív letöltés a várólistában a qBittorrentben.")
        return

    for item in downloads:
        progress = item.get("progress", 0.0)
        is_completed = item.get("is_completed", False)
        seed_h = round(item.get("seeding_time_hours", 0.0), 1)
        target_h = orchestrator.settings.MIN_SEED_TIME_HOURS
        ratio = round(item.get("ratio", 0.0), 2)
        target_r = orchestrator.settings.TARGET_RATIO
        hnr_compliant = (seed_h >= target_h) or (ratio >= target_r)
        dl_speed = item.get("download_speed_kb", 0.0)
        up_speed = item.get("upload_speed_kb", 0.0)
        dl_str = f"{dl_speed / 1024:.1f} MB/s" if dl_speed >= 1024 else f"{dl_speed:.0f} KB/s"
        up_str = f"{up_speed / 1024:.1f} MB/s" if up_speed >= 1024 else f"{up_speed:.0f} KB/s"

        eta_s = item.get("eta_seconds", 0)
        if eta_s and 0 < eta_s < 86400 * 7:
            if eta_s < 60:
                eta_str = f"~{eta_s} mp"
            elif eta_s < 3600:
                eta_str = f"~{eta_s // 60} perc"
            else:
                eta_str = f"~{eta_s // 3600} óra {(eta_s % 3600) // 60} perc"
        else:
            eta_str = "Kész" if is_completed else "Számítás alatt..."

        status_icon = "✅" if is_completed else "📥"
        exp_title = f"{status_icon} {item['name']} ({progress}% - {dl_str})"
        with st.expander(exp_title, expanded=True):
            col_p, col_stat = st.columns([3, 2])
            with col_p:
                st.progress(min(1.0, max(0.0, progress / 100.0)))
                st.caption(f"Állapot: **{item['state']}** | Letöltés: **{dl_str}** | Feltöltés: **{up_str}** | Hátralévő idő: **{eta_str}**")

                matching_task = next(
                    (t for t in getattr(orchestrator, "tasks", [])
                     if t.get("torrent_hash") == item.get("hash") or t.get("release", {}).get("title") == item.get("name")),
                    None,
                )

                if matching_task:
                    rel = matching_task.get("release", {})
                    has_hun = rel.get("has_hungarian_audio", False)
                    sub_p = matching_task.get("subtitle_path")
                    sub_info = matching_task.get("subtitle_info") or {}
                    task_id = matching_task.get("task_id") or matching_task.get("torrent_hash") or item.get("hash")
                    has_embedded = matching_task.get("has_embedded_hungarian_sub", False)
                    if has_hun:
                        st.markdown(
                            "<div style='font-size: 0.78rem; color: #10b981; margin-top: 3px; font-weight: 600;'>"
                            "🔊 <b>Hang:</b> Magyar szinkron ✅ (nincs szükség feliratra)"
                            "</div>",
                            unsafe_allow_html=True,
                        )
                    elif has_embedded:
                        st.markdown(
                            "<div style='font-size: 0.78rem; color: #10b981; margin-top: 3px; font-weight: 600;'>"
                            "💬 <b>Magyar felirat:</b> 💬 Beágyazott magyar felirat az MKV-ban ✅"
                            "</div>",
                            unsafe_allow_html=True,
                        )
                    elif sub_p:
                        src_label = sub_info.get("source", "Feliratok.info")
                        sub_fn = sub_info.get("file_name") or ".hu.srt"
                        st.markdown(
                            f"<div style='font-size: 0.78rem; color: #38bdf8; margin-top: 3px; font-weight: 600;'>"
                            f"💬 <b>Magyar felirat:</b> ✅ Csatolva ({src_label}) &bull; <code>{sub_fn}</code>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )
                    else:
                        st.markdown(
                            "<div style='font-size: 0.78rem; color: #ef4444; margin-top: 3px; font-weight: 600;'>"
                            "💬 <b>Magyar felirat:</b> ⚠️ Nincs csatolva (lent kereshetsz vagy feltölthetsz hozzá sajátot)!"
                            "</div>",
                            unsafe_allow_html=True,
                        )

                    # Kézi feliratkezelő lenyíló ablak eredeti nyelvű kiadásoknál
                    if not has_hun and task_id:
                        with st.expander(
                            f"💬 Felirat Kezelő & Saját .srt feltöltése ({'✅ Csatolva' if sub_p else '⚠️ Felirat szükséges'})",
                            expanded=False,
                        ):
                            c_f1, c_f2 = st.columns([1, 1.2])
                            with c_f1:
                                st.caption("🔍 Automatikus keresés online:")
                                if st.button("🔄 Keresés most", key=f"act_sub_btn_{task_id}", use_container_width=True):
                                    with st.spinner("Keresés Feliratok.eu-n, Subirat.net-en és OpenSubtitles-on..."):
                                        s_res = orchestrator.attach_or_update_subtitle(task_id)
                                        if s_res.get("success"):
                                            st.success(s_res["message"])
                                            st.rerun()
                                        else:
                                            st.warning(s_res["message"])
                            with c_f2:
                                st.caption("📤 Saját felirat (.srt vagy .zip):")
                                up_sub = st.file_uploader(
                                    "Fájl feltöltése:",
                                    type=["srt", "zip"],
                                    key=f"act_sub_up_{task_id}",
                                    label_visibility="collapsed",
                                )
                                if up_sub is not None:
                                    if st.button("💾 Csatolás a filmhez", key=f"act_sub_save_{task_id}", type="primary", use_container_width=True):
                                        with st.spinner("Felirat UTF-8 kódolása és csatolása..."):
                                            m_res = orchestrator.attach_manual_subtitle(task_id, up_sub.name, up_sub.getvalue())
                                            if m_res.get("success"):
                                                st.success(m_res["message"])
                                                st.rerun()
                                            else:
                                                st.error(m_res["message"])

                # Ha a letöltés befejeződött, visszajelzés a célmappáról
                if is_completed and matching_task and matching_task.get("destination_folder"):
                    dest_f = matching_task.get("destination_folder")
                    is_hl = matching_task.get("is_hardlink", True)
                    hl_txt = "⚡ Hardlink (azonnali, 0 MB extra hely)" if is_hl else "📦 Átmásolva célmeghajtóra"
                    st.markdown(
                        f"""
                        <div style="background: rgba(16, 185, 129, 0.12); border: 1px solid rgba(16, 185, 129, 0.3); border-radius: 6px; padding: 6px 10px; margin-top: 8px; font-size: 0.8rem;">
                            <span style="color: #34d399; font-weight: 600;">📂 Elhelyezve a Plexben:</span> <span style="color: #f1f5f9; font-family: monospace;">{dest_f}</span><br>
                            <span style="color: #94a3b8; font-size: 0.74rem;">Módszer: <b>{hl_txt}</b></span>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

            with col_stat:
                if is_completed:
                    if hnr_compliant:
                        temp_del = matching_task.get("temp_deleted", False) if matching_task else False
                        if temp_del:
                            st.success("✅ HnR Teljesítve! (Letöltési temp törölve, Plexben megőrizve)")
                        else:
                            st.success("✅ HnR Teljesítve! (48h vagy 1.0 arány)")
                        st.caption(f"⏱️ Seed: **{seed_h} / {target_h} óra** | 📈 Arány: **{ratio} / {target_r}**")
                        st.caption("🧹 A rendszer automatikusan törölheti a qBittorrentből a médiafájlok megőrzésével.")
                    else:
                        rem_h = round(max(0.0, target_h - seed_h), 1)
                        st.info(f"⏳ Seed folyamatban (HnR kötelezettség)")
                        st.caption(f"⏱️ Seed: **{seed_h} / {target_h} óra** (még **{rem_h} óra**) | 📈 Arány: **{ratio} / {target_r}**")
                        hnr_pct = min(1.0, max(seed_h / float(target_h), ratio / float(target_r)))
                        st.progress(hnr_pct)

                    # KÉNYSZERÍTETT MŰVELETI GOMBOK
                    st.write("")
                    item_hash = item.get("hash")
                    task_key = matching_task.get("task_id") if matching_task else item_hash

                    # 1. Kényszerített átmozgatás gomb
                    is_in_plex = matching_task.get("plex_organized", False) if matching_task else False
                    force_move_label = "🚀 Kényszerített Plexbe Áthelyezés" if not is_in_plex else "🔄 Újrarendezés / Frissítés Plexbe"
                    if st.button(force_move_label, key=f"btn_force_move_{item_hash}", type="primary" if not is_in_plex else "secondary", use_container_width=True):
                        prog_container = st.container()
                        with prog_container:
                            p_box = st.empty()
                            p_bar = st.empty()
                            def _live_ui_cb(info):
                                pct = info.get("percent", 0.0)
                                spd = info.get("speed_mbps", 0.0)
                                eta = info.get("eta_str", "")
                                src = info.get("source", "")
                                dst = info.get("destination", "")
                                cop = info.get("copied_human", "")
                                tot = info.get("total_human", "")
                                size_info = f"({cop} / {tot})" if cop and tot else ""
                                p_bar.progress(min(1.0, max(0.0, pct / 100.0)))
                                p_box.markdown(
                                    f"<div style='background: rgba(16, 185, 129, 0.15); border: 1px solid #10b981; border-radius: 6px; padding: 8px; font-size: 0.8rem;'>"
                                    f"🚚 <b>Átmozgatás folyamatban ({pct}% {size_info} • {spd} MB/s, ETA: {eta})</b><br>"
                                    f"📂 <b>Honnan:</b> <code style='font-size: 0.72rem;'>{src}</code><br>"
                                    f"🎯 <b>Hová:</b> <code style='font-size: 0.72rem; color: #34d399;'>{dst}</code>"
                                    f"</div>",
                                    unsafe_allow_html=True,
                                )
                            with st.spinner("Médiafájlok elhelyezése a Plex könyvtárban..."):
                                f_res = orchestrator.force_organize_task(task_key, progress_callback=_live_ui_cb)
                                if f_res.get("success"):
                                    st.success(f_res["message"])
                                    st.rerun()
                                else:
                                    st.error(f_res["message"])

                    # 2. Kényszerített törlés gomb (helyfelszabadítás)
                    if st.button("🧹 Törlés qBittorrentből & Fájlok Törlése", key=f"btn_force_del_{item_hash}", use_container_width=True, help="Eltávolítja a torrentet a qBittorrentből, és törli a duplikált letöltési temp adatokat (a Plexben lévő film megőrzésével)!"):
                        with st.spinner("Torrent és letöltési adatok törlése..."):
                            del_res = orchestrator.force_delete_task(item_hash, delete_data=True)
                            if del_res.get("success"):
                                st.success(del_res["message"])
                                st.rerun()
                            else:
                                st.error(del_res["message"])
                else:
                    st.warning(f"⏳ Letöltés alatt ({progress}%)")
                    st.caption("Amint végez, a rendszer automatikusan és észrevétlenül Plexbe rendezi!")


def main():
    settings = get_settings()
    orchestrator = get_orchestrator()
    if not hasattr(orchestrator, "radar") or orchestrator.radar is None:
        from src.core.movie_radar import MovieRadarManager
        orchestrator.radar = MovieRadarManager()
    local_ip = get_local_ip()

    # Fejléc
    st.markdown(get_app_header_html(), unsafe_allow_html=True)

    # Telefonos elérési sáv QR kóddal
    c_hint, c_qr = st.columns([5, 1])
    target_mobile_url = f"http://{local_ip}:{settings.APP_PORT}"
    with c_hint:
        st.markdown(get_mobile_hint_html(target_mobile_url), unsafe_allow_html=True)
    with c_qr:
        if st.button("📱 QR-kód", use_container_width=True):
            show_mobile_qr_dialog(target_mobile_url)

    # Élő letöltési állapot sáv (ha van folyamatban lévő letöltés)
    render_live_top_download_ticker(orchestrator)

    # Fő fülek
    wl_count = len(orchestrator.watchlist.get_all())
    radar_count = len(orchestrator.radar.get_monitoring())
    series_radar_count = len(orchestrator.series_radar.get_monitoring())
    sub_radar_count = len(orchestrator.subtitle_radar.get_monitoring())
    audio_radar_count = len(orchestrator.audio_radar.get_monitoring())
    total_radar_count = radar_count + series_radar_count + sub_radar_count + audio_radar_count
    tab_search, tab_radar, tab_watchlist, tab_downloads, tab_status, tab_debug = st.tabs([
        "🔍 Film & Sorozat Keresés",
        f"🔔 Radar ({total_radar_count})",
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
            suggestion = getattr(orchestrator.tmdb_service, "last_search_suggestion", None)
            if suggestion:
                st.info(f"💡 **Elgépelés-javítás:** Nem volt közvetlen találat erre: *„{current_query}”*. Találatok a legvalószínűbb címre: **{suggestion}**")
            st.markdown(f"### Találatok a(z) *„{suggestion or st.session_state.get('last_query', '')}”* keresésre:")
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
                "🔥 Felkapott",
                "⭐ Kritikuskedvencek",
                "💥 Akció",
                "🤠 Kaland",
                "😂 Vígjáték",
                "🎭 Dráma",
                "😱 Horror",
                "🔪 Thriller",
                "🕵️ Krimi",
                "🔍 Rejtély",
                "🚀 Sci-Fi",
                "🧙 Fantasy",
                "🎨 Animáció",
                "👶 Családi",
                "⚔️ Háborús",
                "📜 Történelmi",
                "💖 Romantikus",
                "🎥 Dokumentum",
            ]
            rec_choice = st.pills(
                "Kategóriák",
                options=rec_options,
                default="🔥 Felkapott",
                key="home_rec_pills",
            )
            if not rec_choice:
                rec_choice = "🔥 Felkapott"

            # 2. Beépített Kereső & Szűrősáv a Kategórián Belül
            with st.container():
                st.markdown(
                    """
                    <div style="background: rgba(15, 23, 42, 0.45); border: 1px solid rgba(255,255,255,0.06); 
                                border-radius: 10px; padding: 12px 14px; margin: 10px 0 14px 0;">
                    """,
                    unsafe_allow_html=True,
                )
                f_top1, f_top2, f_top3 = st.columns([2.0, 3.2, 1.2])
                with f_top1:
                    sub_q = st.text_input(
                        "🔍 Keresés a kategórián belül",
                        placeholder="Pl. 'Normandia', 'tengeralattjáró'...",
                        key="browse_sub_q",
                    )
                with f_top2:
                    selected_nations = st.multiselect(
                        "🌍 Nemzetiség / Származási ország (Ötvözhető!)",
                        options=list(NATIONALITY_OPTIONS.keys()),
                        default=[],
                        placeholder="🌐 Összes nemzet (vagy válassz pl. Koreai, Japán...)",
                        key="browse_nations",
                        help="Válassz ki egy vagy több nemzetiséget! Halmozható és ötvözhető a kategóriákkal (pl. Dél-Korea + Dráma/Thriller, Japán + Animáció, Francia + Vígjáték).",
                    )
                with f_top3:
                    type_choice = st.selectbox(
                        "🎬 Formátum",
                        options=["🎬 Filmek", "📺 Sorozatok"],
                        index=0,
                        key="browse_type_choice",
                    )

                f_col1, f_col2, f_col3 = st.columns([1.8, 1.4, 1.8])
                with f_col1:
                    sort_mode = st.selectbox(
                        "📊 Rendezési elv",
                        options=[
                            "🔥 Felkapottság szerint",
                            "⭐ Értékelés szerint",
                            "🕒 Idő szerint (Legújabbak)",
                            "🕰️ Idő szerint (Klasszikusok)",
                        ],
                        key="browse_sort_mode",
                    )
                with f_col2:
                    era_mode = st.selectbox(
                        "📅 Korszak / Évszám",
                        options=["Összes évjárat", "🌟 2020 utániak", "🎬 2010 – 2020", "📼 2000 – 2010", "🎞️ 2000 előttiek"],
                        key="browse_era_mode",
                    )
                with f_col3:
                    prov_mode = st.selectbox(
                        "📺 Streaming Platform",
                        options=["Összes platform", "🔴 Netflix", "🟣 Max (HBO)", "🔵 Disney+", "🍏 Apple TV+", "🟡 SkyShowtime", "📦 Prime Video"],
                        key="browse_prov_mode",
                    )
                st.markdown("</div>", unsafe_allow_html=True)

            # Szűrőparaméterek feloldása
            target_media_type = (
                MediaType.TV if "Sorozat" in type_choice else MediaType.MOVIE
            )

            # Nemzetiségek feloldása (ország és nyelv szerint)
            with_origin_country = None
            with_original_language = None
            if selected_nations:
                c_codes = []
                l_codes = []
                for n_name in selected_nations:
                    n_info = NATIONALITY_OPTIONS.get(n_name, {})
                    if n_info.get("country"):
                        c_codes.append(n_info["country"])
                    if n_info.get("lang"):
                        l_codes.append(n_info["lang"])
                if c_codes:
                    with_origin_country = "|".join(c_codes)
                if l_codes:
                    with_original_language = "|".join(l_codes)

            sort_map = {
                "🔥 Felkapottság szerint": "popularity.desc",
                "⭐ Értékelés szerint": "vote_average.desc",
                "🕒 Idő szerint (Legújabbak)": (
                    "primary_release_date.desc"
                    if target_media_type == MediaType.MOVIE
                    else "first_air_date.desc"
                ),
                "🕰️ Idő szerint (Klasszikusok)": (
                    "primary_release_date.asc"
                    if target_media_type == MediaType.MOVIE
                    else "first_air_date.asc"
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
            filter_state_hash = f"{rec_choice}|{clean_sub_q}|{sort_by}|{year_min}|{year_max}|{watch_provider_id}|{target_media_type.value}|{with_origin_country}|{with_original_language}"

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
                        with_origin_country=with_origin_country,
                        with_original_language=with_original_language,
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
                    if not orchestrator.plex_service.has_media(it.title_hu, it.year, alt_title=it.title_en)
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
    # 2. FÜL: RADAR & AUTOMATA LETÖLTŐK (FILMEK ÉS SOROZATOK)
    # ==============================================================
    with tab_radar:
        st.write("")
        r_items = orchestrator.radar.get_all()
        r_monitoring = [x for x in r_items if x.get("status") == "monitoring"]
        r_downloaded = [x for x in r_items if x.get("status") == "downloaded"]

        s_items = orchestrator.series_radar.get_all()
        s_monitoring = [x for x in s_items if x.get("status") == "monitoring"]
        s_completed = [x for x in s_items if x.get("status") == "completed"]

        sub_items = orchestrator.subtitle_radar.get_all()
        sub_monitoring = [x for x in sub_items if x.get("status") == "monitoring"]
        sub_resolved = [x for x in sub_items if x.get("status") == "resolved"]

        audio_items = orchestrator.audio_radar.get_all()
        audio_monitoring = [x for x in audio_items if x.get("status") == "monitoring"]
        audio_upgraded = [x for x in audio_items if x.get("status") == "upgraded"]

        subtab_movies, subtab_series, subtab_subtitles, subtab_audio = st.tabs([
            f"🎬 Mozi-Radar Filmek ({len(r_monitoring)})",
            f"📺 Sorozat-Radar Új Epizódok ({len(s_monitoring)})",
            f"💬 Felirat-Radar ({len(sub_monitoring)})",
            f"🔊 Szinkron-Radar ({len(audio_monitoring)})",
        ])

        with subtab_movies:
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

        with subtab_series:
            s_head, s_check, s_clear = st.columns([4, 2, 1.2])
            with s_head:
                st.markdown(f"### 📺 Sorozat-Radar ({len(s_monitoring)} figyelve)")
                st.caption("A háttérszál folyamatosan figyeli a futó heti sorozatokat. Amint megjelenik a következő epizód (pl. S02E05), automatikusan letölti magyar szinkronnal vagy felirattal, és Plexbe rendezi!")
            with s_check:
                if s_monitoring and st.button("⚡ Új epizódok keresése most", use_container_width=True, key="btn_check_series_now"):
                    with st.spinner("Új epizódok felkutatása az nCore-on..."):
                        hits = orchestrator.series_radar.check_and_trigger_downloads(orchestrator)
                        if hits:
                            st.success(f"🎉 {len(hits)} db új sorozatepizód megjelent és elindult a letöltése!")
                            st.balloons()
                        else:
                            st.info("Egyelőre még nincs új epizód az nCore-on a figyelt sorozatokból.")
                    st.rerun()
            with s_clear:
                if s_items and st.button("🗑️ Ürítés", use_container_width=True, key="btn_clear_series_radar"):
                    for it in s_items:
                        orchestrator.series_radar.remove(it["tmdb_id"])
                    st.rerun()

            if not s_items:
                st.info("💡 A Sorozat-Radaron jelenleg nincs figyelt sorozat. Keresés közben kattints bármelyik sorozat kártyáján a **📺 Sorozat-Radar** gombra a heti epizódok figyeléséhez!")
            else:
                if s_monitoring:
                    st.markdown("#### ⏳ Aktívan Figyelt Heti Sorozatok:")
                    s_cols = st.columns(2)
                    for s_idx, s_entry in enumerate(s_monitoring):
                        with s_cols[s_idx % 2]:
                            cur_s = s_entry.get("current_season", 1)
                            cur_e = s_entry.get("current_episode", 0)
                            next_e = cur_e + 1
                            next_code = f"S{cur_s:02d}E{next_e:02d}"
                            last_rel = s_entry.get("last_release_title")

                            poster_html = f"<img src='{s_entry.get('poster_url')}' style='width: 75px; border-radius: 8px; object-fit: cover;'/>" if s_entry.get("poster_url") else ""
                            last_info = f"<div style='font-size: 0.72rem; color: #64748b; margin-top: 6px; font-family: monospace;'>Legutóbb: {last_rel[:36]}...</div>" if last_rel else ""

                            st.markdown(
                                f"""
                                <div style="background: rgba(30, 41, 59, 0.75); border: 1px solid rgba(255,255,255,0.08);
                                            border-radius: 12px; padding: 14px; margin-bottom: 12px;">
                                    <div style="display: flex; gap: 14px;">
                                        {poster_html}
                                        <div style="flex: 1;">
                                            <div style="font-weight: 700; font-size: 1.05rem; color: #f8fafc;">{s_entry.get('title_hu')}</div>
                                            <div style="font-size: 0.8rem; color: #94a3b8;">{s_entry.get('title_en', '')} ({s_entry.get('year', '')})</div>
                                            <div style="margin-top: 8px;">
                                                <span style="background: rgba(59, 130, 246, 0.2); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.4); padding: 3px 8px; border-radius: 6px; font-size: 0.8rem; font-weight: 600;">
                                                    ⏳ Várt új rész: {next_code}
                                                </span>
                                            </div>
                                            {last_info}
                                        </div>
                                    </div>
                                </div>
                                """,
                                unsafe_allow_html=True,
                            )
                            c_s_btn1, c_s_btn2 = st.columns([1, 1])
                            with c_s_btn1:
                                if st.button("🗑️ Levétel", key=f"btn_s_rem_{s_idx}_{s_entry['tmdb_id']}", use_container_width=True):
                                    orchestrator.series_radar.remove(s_entry["tmdb_id"])
                                    st.rerun()
                            with c_s_btn2:
                                if st.button("🔍 Keresés most", key=f"btn_s_chk_{s_idx}_{s_entry['tmdb_id']}", use_container_width=True):
                                    with st.spinner("Keresés az nCore-on..."):
                                        hits = orchestrator.series_radar.check_and_trigger_downloads(orchestrator)
                                        if hits:
                                            st.success("🎉 Új epizód elindult!")
                                            st.balloons()
                                        else:
                                            st.info("Még nem jelent meg az új epizód.")
                                    st.rerun()

        with subtab_subtitles:
            sub_head, sub_check, sub_clear = st.columns([4, 2, 1.2])
            with sub_head:
                st.markdown(f"### 💬 Felirat-Radar & Automata Letöltő ({len(sub_monitoring)} figyelve)")
                st.caption(
                    "A háttérben futó figyelő automatikusan letölti és Plexbe illeszti a magyar feliratot "
                    "a 3 legfőbb adatbázisból (Feliratok.eu, Subirat.net, OpenSubtitles), amint megjelenik a fordítás a filmhez vagy epizódhoz!"
                )
            with sub_check:
                if sub_monitoring and st.button("⚡ Feliratok keresése most", use_container_width=True, key="btn_check_sub_radar_now"):
                    with st.spinner("Magyar feliratok keresése a 3 szolgáltatónál (Feliratok.eu, Subirat.net, OpenSubtitles)..."):
                        hits = orchestrator.check_subtitle_radar()
                        if hits:
                            st.success(f"🎉 {len(hits)} db új magyar felirat letöltve és Plexhez társítva!")
                            st.balloons()
                        else:
                            st.info("Egyelőre még nem jelent meg új magyar felirat a figyelt kiadásokhoz.")
                    st.rerun()
            with sub_clear:
                if sub_resolved and st.button("🗑️ Készek ürítése", use_container_width=True, key="btn_clear_sub_resolved"):
                    orchestrator.subtitle_radar.clear_resolved()
                    st.rerun()

            if not sub_items:
                st.info("💡 A Felirat-Radaron jelenleg nincs figyelt film. Ha egy letöltött vagy meglévő kiadáshoz még nincs magyar felirat, a rendszer automatikusan felveszi ide a figyelőlistára!")
            else:
                if sub_monitoring:
                    st.markdown("#### ⏳ Aktívan Figyelt Filmek & Sorozatok (Várakozás magyar feliratra):")
                    sub_m_cols = st.columns(2)
                    for sm_idx, sm_entry in enumerate(sub_monitoring):
                        with sub_m_cols[sm_idx % 2]:
                            with st.container(border=True):
                                c_sub_l, c_sub_r = st.columns([1, 2.5])
                                with c_sub_l:
                                    if sm_entry.get("poster_url"):
                                        st.image(sm_entry["poster_url"], use_container_width=True)
                                with c_sub_r:
                                    st.markdown(f"**{sm_entry.get('title_hu')}** ({sm_entry.get('year') or ''})")
                                    if sm_entry.get("title_en") and sm_entry.get("title_en") != sm_entry.get("title_hu"):
                                        st.caption(f"Angol cím: {sm_entry.get('title_en')}")
                                    st.markdown(f"<div style='font-size: 0.8rem; color: #94a3b8;'>Kiadás: <code>{sm_entry.get('release_title', '')[:35]}</code></div>", unsafe_allow_html=True)
                                    added_time = sm_entry.get("added_at", "")[:16].replace("T", " ")
                                    st.caption(f"Radaron: {added_time}")
                                    if sm_entry.get("last_checked_at"):
                                        last_chk = sm_entry["last_checked_at"][:16].replace("T", " ")
                                        st.caption(f"Utolsó ellenőrzés: {last_chk}")

                                c_btn_s1, c_btn_s2 = st.columns(2)
                                with c_btn_s1:
                                    if st.button("❌ Eltávolítás", key=f"btn_del_sub_{sm_idx}_{sm_entry.get('radar_id')}", use_container_width=True):
                                        orchestrator.subtitle_radar.remove(sm_entry.get("radar_id"))
                                        st.rerun()
                                with c_btn_s2:
                                    if st.button("🔍 Keresés most", key=f"btn_chk_sub_{sm_idx}_{sm_entry.get('radar_id')}", use_container_width=True):
                                        with st.spinner("Keresés a 3 feliratoldalon..."):
                                            hits = orchestrator.check_subtitle_radar(single_radar_id=sm_entry.get("radar_id"))
                                            if hits:
                                                st.success("🎉 Felirat megtalálva és hozzáadva!")
                                                st.balloons()
                                            else:
                                                st.info("Egyelőre még nincs felirat a 3 oldalon.")
                                        st.rerun()

                if sub_resolved:
                    st.write("")
                    st.markdown("#### ✅ Sikeresen Letöltött & Plexhez Társított Feliratok:")
                    for sr_entry in sub_resolved:
                        with st.container(border=True):
                            c_r_l, c_r_r = st.columns([3, 1.5])
                            with c_r_l:
                                st.markdown(f"**{sr_entry.get('title_hu')}** ({sr_entry.get('year') or ''})")
                                st.caption(f"Kiadás: {sr_entry.get('release_title', '')}")
                                if sr_entry.get("subtitle_path"):
                                    st.caption(f"Fájl: `{sr_entry.get('subtitle_path')}`")
                            with c_r_r:
                                src_tag = sr_entry.get('found_source', 'Online')
                                st.markdown(f"<div style='background-color: #065f46; color: #6ee7b7; padding: 4px 8px; border-radius: 6px; font-size: 0.8rem; text-align: center; margin-bottom: 6px;'>✅ {src_tag}</div>", unsafe_allow_html=True)
                                if sr_entry.get("resolved_at"):
                                    res_t = sr_entry["resolved_at"][:16].replace("T", " ")
                                    st.caption(f"Társítva: {res_t}")

        with subtab_audio:
            a_head, a_check, a_clear = st.columns([4, 2, 1.2])
            with a_head:
                st.markdown(f"### 🔊 Szinkron-Radar & Minőség-Upgrade ({len(audio_monitoring)} figyelve)")
                st.caption(
                    "A háttérben futó figyelő automatikusan letölti a hivatalos HD magyar szinkronos kiadást (hd_hun / sorozat_hun), "
                    "amint megjelenik az nCore-on az eredetileg csak angolul vagy SD-ben letöltött filmekhez!"
                )
            with a_check:
                if audio_monitoring and st.button("⚡ Szinkronok keresése most", use_container_width=True, key="btn_check_audio_radar_now"):
                    with st.spinner("HD Magyar szinkronos kiadások felderítése az nCore-on..."):
                        hits = orchestrator.check_audio_radar()
                        if hits:
                            st.success(f"🎉 {len(hits)} db filmhez megjelent a HD magyar szinkron! Letöltés elindult!")
                            st.balloons()
                        else:
                            st.info("Egyelőre még nincs új HD magyar szinkronos kiadás a figyelt címekhez.")
                    st.rerun()
            with a_clear:
                if audio_upgraded and st.button("🗑️ Frissítettek ürítése", use_container_width=True, key="btn_clear_audio_upgraded"):
                    orchestrator.audio_radar.clear_upgraded()
                    st.rerun()

            if not audio_items:
                st.info("💡 A Szinkron-Radaron jelenleg nincs figyelt film. Ha egy letöltött kiadás csak angol nyelvű, a rendszer automatikusan felveszi ide a figyelőlistára a későbbi magyar szinkronhoz!")
            else:
                if audio_monitoring:
                    st.markdown("#### ⏳ Aktívan Figyelt Filmek (Várakozás HD magyar szinkronra):")
                    a_m_cols = st.columns(2)
                    for am_idx, am_entry in enumerate(audio_monitoring):
                        with a_m_cols[am_idx % 2]:
                            with st.container(border=True):
                                c_a_l, c_a_r = st.columns([1, 2.5])
                                with c_a_l:
                                    if am_entry.get("poster_url"):
                                        st.image(am_entry["poster_url"], use_container_width=True)
                                with c_a_r:
                                    st.markdown(f"**{am_entry.get('title_hu')}** ({am_entry.get('year') or ''})")
                                    if am_entry.get("title_en") and am_entry.get("title_en") != am_entry.get("title_hu"):
                                        st.caption(f"Angol cím: {am_entry.get('title_en')}")
                                    st.markdown(f"<div style='font-size: 0.8rem; color: #94a3b8;'>Jelenlegi: <code>{am_entry.get('current_release_title', '')[:35]}</code></div>", unsafe_allow_html=True)
                                    st.markdown("<div style='font-size: 0.8rem; color: #38bdf8;'>Cél: <b>🇭🇺 HD Magyar Szinkron (hd_hun)</b></div>", unsafe_allow_html=True)
                                    added_time = am_entry.get("added_at", "")[:16].replace("T", " ")
                                    st.caption(f"Radaron: {added_time}")
                                    if am_entry.get("last_checked_at"):
                                        last_chk = am_entry["last_checked_at"][:16].replace("T", " ")
                                        st.caption(f"Utolsó ellenőrzés: {last_chk}")

                                c_btn_a1, c_btn_a2 = st.columns(2)
                                with c_btn_a1:
                                    if st.button("❌ Eltávolítás", key=f"btn_del_aud_{am_idx}_{am_entry.get('radar_id')}", use_container_width=True):
                                        orchestrator.audio_radar.remove(am_entry.get("radar_id"))
                                        st.rerun()
                                with c_btn_a2:
                                    if st.button("🔍 Keresés most", key=f"btn_chk_aud_{am_idx}_{am_entry.get('radar_id')}", use_container_width=True):
                                        with st.spinner("Keresés az nCore-on..."):
                                            hits = orchestrator.check_audio_radar(single_radar_id=am_entry.get("radar_id"))
                                            if hits:
                                                st.success("🎉 Megjelent a szinkron, letöltés elindult!")
                                                st.balloons()
                                            else:
                                                st.info("Egyelőre még nincs HD magyar szinkronos kiadás.")
                                        st.rerun()

                if audio_upgraded:
                    st.write("")
                    st.markdown("#### ✅ Sikeresen Szinkronizált & Frissített Kiadások:")
                    for au_entry in audio_upgraded:
                        with st.container(border=True):
                            c_au_l, c_au_r = st.columns([3, 1.5])
                            with c_au_l:
                                st.markdown(f"**{au_entry.get('title_hu')}** ({au_entry.get('year') or ''})")
                                st.caption(f"Új HD kiadás: `{au_entry.get('upgraded_release_title', '')}`")
                            with c_au_r:
                                st.markdown("<div style='background-color: #065f46; color: #6ee7b7; padding: 4px 8px; border-radius: 6px; font-size: 0.8rem; text-align: center; margin-bottom: 6px;'>✅ HD Szinkron Frissítve</div>", unsafe_allow_html=True)
                                if au_entry.get("upgraded_at"):
                                    up_t = au_entry["upgraded_at"][:16].replace("T", " ")
                                    st.caption(f"Frissítve: {up_t}")

    # ==============================================================
    # 3. FÜL: KÍVÁNSÁGLISTA & KÜLSŐ SZINKRONIZÁCIÓ
    # ==============================================================
    with tab_watchlist:
        st.write("")
        wl_items = orchestrator.watchlist.get_all()
        wl_head, wl_sync_col, wl_clear = st.columns([3.5, 2.2, 1.2])
        with wl_head:
            st.markdown(f"### ⭐ Kívánságlista ({len(wl_items)} db)")
            st.caption("A későbbre elmentett filmjeid és sorozataid gyűjteménye. Egy kattintással letöltheted őket!")
        with wl_sync_col:
            if st.button("📥 Külső Watchlist Szinkronizálás", use_container_width=True, help="IMDb, Letterboxd vagy Trakt lista importálása és szinkronizálása"):
                show_watchlist_sync_dialog(orchestrator)
        with wl_clear:
            if wl_items and st.button("🗑️ Lista ürítése", use_container_width=True, key="btn_clear_wl"):
                for it in wl_items:
                    orchestrator.watchlist.remove(it.tmdb_id)
                st.rerun()

        if not wl_items:
            st.info("💡 A kívánságlistád jelenleg üres. Keresés közben vagy az ajánlóknál kattints a **🤍 Lista** gombra bármelyik kártyán a mentéshez, vagy importáld a külső profilodat a **Külső Watchlist Szinkronizálás** gombbal!")
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

        # Plex-Aware Throttling élő állapotjelző
        try:
            if orchestrator.plex_service.has_active_playback():
                sessions = orchestrator.plex_service.get_active_sessions()
                playing_titles = ", ".join(s.get("title", "Film") for s in sessions)
                st.info(f"⚡ **Plex Lejátszás Észlelve:** `{playing_titles}` &bull; A qBittorrent sebessége ideiglenesen korlátozva az akadozásmentes 4K lejátszás érdekében.")
        except Exception:
            pass

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

        # Plex Megnézett Tartalmak Takarító Modul
        with st.expander("🧹 Megnézett Tartalmak Takarítása & Tárhely Felszabadítás (Plex Watched Cleaner)", expanded=False):
            st.caption("A rendszer a Plex és qBittorrent adatai alapján feltárja a már megnézett filmeket és epizódokat, amelyeknél a Hit & Run seed kötelezettség (48h) is teljesült. Egyetlen kattintással felszabadíthatod a 20-60 GB-os fájlokat!")
            c_w1, c_w2 = st.columns([4, 1])
            with c_w2:
                if st.button("🔄 Megnézettek keresése", key="btn_refresh_watched", use_container_width=True):
                    st.session_state["watched_items"] = orchestrator.get_watched_plex_media()
                    st.rerun()

            watched_list = st.session_state.get("watched_items")
            if watched_list is None:
                watched_list = orchestrator.get_watched_plex_media()
                st.session_state["watched_items"] = watched_list

            if not watched_list:
                st.info("Jelenleg nincs megnézett médiaelem a Plex könyvtárban, vagy a Plex szerver kapcsolat nem aktív.")
            else:
                st.markdown(f"**Talált megnézett elemek száma:** {len(watched_list)} db")
                for w in watched_list[:12]:
                    col_t, col_sz, col_act = st.columns([5, 2, 2])
                    with col_t:
                        st.markdown(f"🎬 **{w['title']}** ({w.get('year') or 'N/A'})")
                        hnr_status = "✅ 48h HnR kész (biztonságos)" if w.get("is_hnr_done") else "⏳ HnR seed még tart"
                        st.caption(f"Megnézve: **{w['last_viewed_at']}** &bull; {hnr_status}")
                    with col_sz:
                        st.markdown(f"💾 **{w['size_human']}**")
                    with col_act:
                        del_key = f"del_watched_{w['rating_key']}_{w['title'][:10]}"
                        if st.button("🗑️ Törlés", key=del_key, use_container_width=True, help="Törli a fájlt a merevlemezről, eltávolítja a torrentet és frissíti a Plexet!"):
                            with st.spinner("Törlés folyamatban..."):
                                d_res = orchestrator.delete_watched_plex_media(rating_key=w["rating_key"], file_paths=w["file_paths"])
                                if d_res.get("success"):
                                    st.success(d_res["message"])
                                    st.session_state["watched_items"] = [x for x in watched_list if x["rating_key"] != w["rating_key"]]
                                    st.rerun()
                                else:
                                    st.error("Nem sikerült törölni a fájlokat.")

        btn_sync_c1, btn_sync_c2, btn_sync_c3, btn_sync_c4 = st.columns([2.5, 2.5, 2.2, 1.8])
        with btn_sync_c1:
            if st.button("🔄 Állapot frissítése & Kész rendezése", type="primary", use_container_width=True, key="btn_check_tasks_now"):
                with st.spinner("Letöltések ellenőrzése és rendezése..."):
                    updated = orchestrator.check_and_process_tasks()
                    orchestrator.plex_service.refresh_library()
                    st.success("✅ Letöltések ellenőrizve és a Plex könyvtár frissítve!")
                    st.rerun()
        with btn_sync_c2:
            if st.button("🔍 Meglévő torrentek beolvasása", use_container_width=True, key="btn_sync_all_qb", help="Átvizsgálja a qBittorrentben lévő ÖSSZES torrentet (a meglévőket is), és azonnal elhelyezi a kész filmeket a Plexben!"):
                with st.spinner("qBittorrent torrentek beolvasása és rendezése..."):
                    s_res = orchestrator.sync_all_qbittorrent_torrents(auto_organize=True)
                    st.success(s_res["message"])
                    st.rerun()
        with btn_sync_c3:
            hnr_ready_count = stats.get("hnr_met_count", 0)
            btn_clean_label = f"🧹 48h+ Seedeltek Törlése ({hnr_ready_count} db)" if hnr_ready_count > 0 else "🧹 48h+ Seedeltek Törlése"
            if st.button(btn_clean_label, use_container_width=True, key="btn_clean_all_hnr", help="Törli az összes olyan torrentet a qBittorrentből és a letöltési mappából, ami elérte a 48 óra seedelést vagy az 1.0 arányt."):
                with st.spinner("Seedelt torrentek takarítása..."):
                    c_res = orchestrator.cleanup_all_seeded_torrents()
                    if c_res.get("count", 0) > 0:
                        st.success(c_res["message"])
                    else:
                        st.info(c_res["message"])
                    st.rerun()
        with btn_sync_c4:
            if st.button("🍿 Plex Frissítés", use_container_width=True, key="btn_plex_scan_only"):
                with st.spinner("Plex szerver értesítése..."):
                    if orchestrator.plex_service.refresh_library():
                        st.success("✅ Plex frissítve!")
                    else:
                        st.warning("⚠️ Nem sikerült elérni a Plex szervert.")

        st.write("")
        tab_sub_active, tab_sub_history = st.tabs([
            "📥 Folyamatban lévő Letöltések",
            "📜 Letöltési Előzmények (Történet)",
        ])

        with tab_sub_active:
            render_active_downloads_section(orchestrator)

        with tab_sub_history:
            history = orchestrator.get_download_history()
            if not history:
                st.info("Még nincsenek korábbi letöltési előzmények a rendszerben.")
            else:
                # 1. Kategória- és felirat szerinti intelligens szűrő
                all_count = len(history)
                hun_audio_count = sum(1 for t in history if t.get("release", {}).get("has_hungarian_audio"))
                with_sub_count = sum(
                    1 for t in history
                    if not t.get("release", {}).get("has_hungarian_audio") and (
                        bool(t.get("subtitle_path")) or any(f.endswith(".srt") for f in t.get("destination_files", []))
                    )
                )
                missing_sub_count = sum(
                    1 for t in history
                    if not t.get("release", {}).get("has_hungarian_audio") and not (
                        bool(t.get("subtitle_path")) or any(f.endswith(".srt") for f in t.get("destination_files", []))
                    )
                )

                st.markdown("#### 🔍 Letöltések Szűrése Nyelv és Felirat Szerint:")
                filter_choice = st.radio(
                    "Szűrés:",
                    [
                        f"Összes ({all_count})",
                        f"🔊 Magyar szinkronos ({hun_audio_count})",
                        f"💬 Csatolt felirattal ({with_sub_count})",
                        f"⚠️ Felirat hiányzik ({missing_sub_count})",
                    ],
                    horizontal=True,
                    key="history_filter_choice",
                    label_visibility="collapsed",
                )

                filtered_history = history
                if "Szinkronos" in filter_choice:
                    filtered_history = [t for t in history if t.get("release", {}).get("has_hungarian_audio")]
                elif "Csatolt felirattal" in filter_choice:
                    filtered_history = [
                        t for t in history
                        if not t.get("release", {}).get("has_hungarian_audio") and (
                            bool(t.get("subtitle_path")) or any(f.endswith(".srt") for f in t.get("destination_files", []))
                        )
                    ]
                elif "Felirat hiányzik" in filter_choice:
                    filtered_history = [
                        t for t in history
                        if not t.get("release", {}).get("has_hungarian_audio") and not (
                            bool(t.get("subtitle_path")) or any(f.endswith(".srt") for f in t.get("destination_files", []))
                        )
                    ]

                if not filtered_history:
                    st.info("Nincs a kiválasztott szűrési feltételnek megfelelő letöltés az előzményekben.")
                else:
                    for h_idx, task in enumerate(filtered_history):
                        m_item = task.get("media_item") or {}
                        rel = task.get("release") or {}
                        created_at = task.get("created_at", "Korábban")
                        is_plex = task.get("plex_organized", False)
                        task_id = task.get("task_id") or task.get("torrent_hash") or f"hist_{h_idx}"
                        m_tmdb_id = m_item.get("tmdb_id") if isinstance(m_item, dict) else getattr(m_item, "tmdb_id", None)

                        with st.container():
                            st.markdown('<div class="movie-card" style="padding: 14px; margin-bottom: 12px;">', unsafe_allow_html=True)
                            h_col_img, h_col_details, h_col_action = st.columns([1, 3.5, 1.5])

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

                                status_badge = "<span style='background: #10b981; color: white; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: bold;'>✅ Plexbe rendezve</span>" if is_plex else "<span style='background: #f59e0b; color: white; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: bold;'>⏳ Letöltés / Rendezésre vár</span>"

                                # Nyelv és Felirat Bélyegek egyértelmű színkóddal
                                sub_badge = ""
                                has_hun_sub = bool(task.get("subtitle_path")) or any(f.endswith(".srt") for f in task.get("destination_files", []))
                                sub_info = task.get("subtitle_info") or {}
                                src_label = sub_info.get("source", "Feliratok.info")

                                if rel.get("has_hungarian_audio"):
                                    audio_badge = "<span style='background: #10b981; color: white; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: bold;'>🔊 Magyar szinkron ✅</span>"
                                else:
                                    is_aud_mon = orchestrator.audio_radar.is_monitored(m_tmdb_id) if (m_tmdb_id and hasattr(orchestrator, "audio_radar")) else False
                                    aud_mon_txt = " • 🔊 Szinkron-Radar aktív" if is_aud_mon else ""
                                    audio_badge = f"<span style='background: #475569; color: white; padding: 2px 6px; border-radius: 4px; font-size: 0.72rem;'>🇬🇧 Eredeti hang{aud_mon_txt}</span>"
                                    has_embedded = task.get("has_embedded_hungarian_sub", False)
                                    if has_embedded:
                                        sub_badge = "<span style='background: #059669; color: white; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: bold;'>💬 Beágyazott magyar felirat az MKV-ban ✅</span>"
                                    elif has_hun_sub:
                                        sub_badge = f"<span style='background: #0284c7; color: white; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: bold;'>💬 Magyar felirat ({src_label}) ✅</span>"
                                    else:
                                        sub_badge = "<span style='background: #dc2626; color: white; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: bold;'>⚠️ Nincs magyar felirat!</span>"

                                st.markdown(f"{status_badge} {audio_badge} {sub_badge}", unsafe_allow_html=True)
                                st.caption(f"📦 **Kiadás:** `{rel.get('title', 'N/A')}`")
                                st.caption(f"💾 Méret: **{rel.get('size_human', 'N/A')}** | 📅 Indítva: **{created_at}**")

                                dest_f = task.get("destination_folder")
                                if is_plex and dest_f:
                                    is_hl = task.get("is_hardlink", True)
                                    hl_txt = "⚡ Hardlink (azonnali, 0 MB extra helyfoglalás)" if is_hl else "📦 Átmásolva célmeghajtóra"
                                    org_time = task.get("organized_at")
                                    dest_files = task.get("destination_files", [])
                                    f_str = f" • Fájlok száma: {len(dest_files)} db" if dest_files else ""
                                    sub_file_note = ""
                                    if has_hun_sub:
                                        sub_file_note = f" • 💬 Felirat: <b>.hu.srt ({src_label})</b>"
                                    st.markdown(
                                        f"""
                                        <div style="background: rgba(16, 185, 129, 0.1); border: 1px solid rgba(16, 185, 129, 0.35); border-radius: 8px; padding: 7px 11px; margin-top: 6px; font-size: 0.8rem;">
                                            <div style="color: #34d399; font-weight: 600;">✅ Elhelyezve a Plex célkönyvtárában:</div>
                                            <div style="color: #f1f5f9; font-family: monospace; font-size: 0.78rem; word-break: break-all; margin: 2px 0;">📂 {dest_f}</div>
                                            <div style="color: #94a3b8; font-size: 0.73rem;">
                                                🔗 Módszer: <b>{hl_txt}</b>{f_str}{sub_file_note} {f'| 🕒 {org_time}' if org_time else ''}
                                            </div>
                                        </div>
                                        """,
                                        unsafe_allow_html=True,
                                    )

                                # Interaktív Felirat Kezelő lenyíló modul (ha nem szinkronos)
                                if not rel.get("has_hungarian_audio"):
                                    with st.expander(
                                        f"💬 Felirat Kezelő & Saját Felirat (.srt) Feltöltése ({'✅ Csatolva' if has_hun_sub else '⚠️ Felirat hiányzik!'})",
                                        expanded=(not has_hun_sub),
                                    ):
                                        if has_hun_sub:
                                            cur_sub_name = sub_info.get("file_name") or ".hu.srt"
                                            st.markdown(
                                                f"<div style='color: #38bdf8; font-size: 0.85rem; margin-bottom: 6px;'>"
                                                f"✅ <b>Csatolt felirat aktív:</b> <code>{cur_sub_name}</code> (Forrás: <b>{src_label}</b>)"
                                                f"</div>",
                                                unsafe_allow_html=True,
                                            )
                                        else:
                                            is_sub_mon = orchestrator.subtitle_radar.is_monitored(m_tmdb_id) if (m_tmdb_id and hasattr(orchestrator, "subtitle_radar")) else False
                                            if is_sub_mon:
                                                st.markdown(
                                                    "<div style='background: rgba(245, 158, 11, 0.12); border: 1px solid #f59e0b; border-radius: 6px; padding: 8px 10px; margin-bottom: 8px; color: #fde68a; font-size: 0.85rem;'>"
                                                    "🔔 <b>Felirat-Radar aktívan figyeli:</b> A háttérfolyamat folyamatosan ellenőrzi a 3 adatbázist (Feliratok.eu, Subirat.net, OpenSubtitles). Amint megjelenik a fordítás, automatikusan letölti és Plexbe illeszti!<br>"
                                                    "Ha nem szeretnél várni a háttérfolyamatra, az alábbi gombbal azonnal újrapróbálhatod vagy tölthetsz fel saját <code>.srt</code> fájlt."
                                                    "</div>",
                                                    unsafe_allow_html=True,
                                                )
                                            else:
                                                st.markdown(
                                                    "<div style='background: rgba(239, 68, 68, 0.12); border: 1px solid #ef4444; border-radius: 6px; padding: 8px 10px; margin-bottom: 8px; color: #fca5a5; font-size: 0.85rem;'>"
                                                    "⚠️ <b>Ehhez a kiadáshoz még nem található magyar felirat</b> az online adatbázisokban (Feliratok.eu, Subirat.net, OpenSubtitles).<br>"
                                                    "Tölts fel hozzá egy saját <code>.srt</code> vagy <code>.zip</code> fájlt, vagy próbáld újra az automata keresést!"
                                                    "</div>",
                                                    unsafe_allow_html=True,
                                                )

                                        f_col1, f_col2 = st.columns([1, 1.2])
                                        with f_col1:
                                            st.markdown("###### 🔍 Online keresés:")
                                            st.caption("3 szolgáltató ellenőrzése:")
                                            if st.button("🔄 Újrakeresés most", key=f"hist_sub_btn_{task_id}_{h_idx}", use_container_width=True):
                                                with st.spinner("Online felirat keresése 3 szolgáltatónál..."):
                                                    s_res = orchestrator.attach_or_update_subtitle(task_id)
                                                    if s_res.get("success"):
                                                        st.success(s_res["message"])
                                                        st.rerun()
                                                    else:
                                                        st.warning(s_res["message"])

                                        with f_col2:
                                            st.markdown("###### 📤 Saját felirat feltöltése:")
                                            st.caption("Tallózz be egy .srt vagy .zip fájlt:")
                                            hist_sub_up = st.file_uploader(
                                                "Feliratfájl:",
                                                type=["srt", "zip"],
                                                key=f"hist_sub_up_{task_id}_{h_idx}",
                                                label_visibility="collapsed",
                                            )
                                            if hist_sub_up is not None:
                                                if st.button("💾 Csatolás a filmhez és Plex frissítése", key=f"hist_sub_save_{task_id}_{h_idx}", type="primary", use_container_width=True):
                                                    with st.spinner("Felirat UTF-8 konvertálása és Plex könyvtárba másolása..."):
                                                        m_res = orchestrator.attach_manual_subtitle(task_id, hist_sub_up.name, hist_sub_up.getvalue())
                                                        if m_res.get("success"):
                                                            st.success(m_res["message"])
                                                            st.rerun()
                                                        else:
                                                            st.error(m_res["message"])

                            with h_col_action:
                                if not is_plex:
                                    if st.button("⚡ Plexbe rendezés", key=f"force_org_{task_id}_{h_idx}", use_container_width=True, type="primary"):
                                        with st.spinner("Fájlok áthelyezése a Plexbe..."):
                                            res = orchestrator.force_organize_task(task_id)
                                            if res.get("success"):
                                                st.success(res["message"])
                                                st.rerun()
                                            else:
                                                st.error(res["message"])

                                if st.button("🗑️ Törlés", key=f"del_task_{task_id}_{h_idx}", use_container_width=True):
                                    orchestrator.remove_task(task_id)
                                    st.success("Eltávolítva az előzményekből!")
                                    st.rerun()

                            st.markdown('</div>', unsafe_allow_html=True)

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

        # Meghajtók egyezőségének ellenőrzése a villámgyors Hardlink érdekében
        temp_drive = getattr(settings.DOWNLOAD_TEMP_DIR, "drive", "").upper()
        plex_drive = getattr(settings.PLEX_MOVIES_DIR, "drive", "").upper()
        if temp_drive and plex_drive and temp_drive != plex_drive:
            st.warning(
                f"💡 **Tipp a villámgyors működéshez:** A letöltési mappa (`{temp_drive}`) és a Plex filmek mappája (`{plex_drive}`) különböző meghajtókon található!\n\n"
                f"Két különböző merevlemez között nem lehetséges az azonnali Hardlink, ezért a rendszernek a teljes filmet át kell másolnia lemezek között, ami dupla helyfoglalást és másolási időt igényel.\n\n"
                f"**Ajánlott megoldás:** Állítsd be a letöltési mappát is a(z) `{plex_drive}` meghajtóra (pl. `{plex_drive}\\Letoltesek`), VAGY a Plexben add hozzá könyvtárként a(z) `{temp_drive}` meghajtón lévő média mappát is! Így a film azonnal (1 ms alatt), dupla helyfoglalás nélkül jelenik meg a Plexben."
            )

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
                    help="A könyvtár automatikus frissítéséhez letöltés után. (Tipp: Plex Webben bármelyik film 3 pontján -> 'XML megtekintése' -> az URL legvégén másold ki a token értékét az 'X-Plex-Token=' után!)",
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
                    "Torrent levétele a qBittorrentből a feltételek teljesülése után",
                    value=bool(settings.AUTO_REMOVE_AFTER_SEED),
                )
                inp_del_torrent_data = st.checkbox(
                    "🧹 Letöltési mappa automatikus takarítása (duplikált torrent fájlok törlése a seedelés után)",
                    value=bool(getattr(settings, "DELETE_TORRENT_DATA_AFTER_SEED", True)),
                    help="Ha be van kapcsolva, a 48 óra (vagy 1.0 arány) letelte után a letöltési mappából automatikusan törlődik a letöltött torrent adat, így nem foglal felesleges helyet. A Plex mappába átmozgatott/másolt film természetesen 100%-ban megmarad!",
                )

                st.write("")
                st.markdown("##### 💡 Felhasználói Védelem & Megerősítések")
                inp_confirm_dl = st.checkbox(
                    "Megerősítő ablak megjelenítése a letöltés gomb megnyomásakor",
                    value=bool(getattr(settings, "CONFIRM_BEFORE_DOWNLOAD", True)),
                    help="Ha be van kapcsolva, a letöltés gombra kattintva felugrik egy nagy ablak, ami pontosan kiírja a film szinkronját (magyar/eredeti), felbontását és méretét a téves letöltések megelőzésére.",
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
                "DELETE_TORRENT_DATA_AFTER_SEED": "true" if inp_del_torrent_data else "false",
                "CONFIRM_BEFORE_DOWNLOAD": "true" if inp_confirm_dl else "false",
            }

            if save_settings(new_config):
                get_orchestrator.clear()
                st.success("✅ A beállítások sikeresen elmentve a .env fájlba és érvénybe léptek!")
                st.rerun()
            else:
                st.error("❌ Nem sikerült elmenteni a beállításokat a .env fájlba!")

        st.write("")

        # ==============================================================
        # PLEX TOKEN SEGÉD ÉS HITELESÍTŐ VARÁZSLÓ (A FORMON KÍVÜL)
        # ==============================================================
        with st.expander("🔑 Plex Token Segéd (Hogyan szerezd meg / Automatikus opciók)", expanded=not bool(settings.PLEX_TOKEN)):
            st.markdown(
                """
                <div style="background: rgba(30, 41, 59, 0.6); border: 1px solid rgba(148, 163, 184, 0.25); border-radius: 8px; padding: 12px 16px; margin-bottom: 12px; font-size: 0.88rem;">
                    <b>💡 A leggyorsabb és legbiztosabb módszer (Plex Web XML):</b><br>
                    1. Nyisd meg a Plexet a böngészőben (<code>http://localhost:32400/web</code> vagy <code>https://app.plex.tv</code>).<br>
                    2. Bármelyik filmednél vagy sorozatodnál kattints a <b>3 pontra (...)</b> &rarr; válaszd a <b>„Fájlinformációk” (Get Info)</b> lehetőséget.<br>
                    3. Kattints a bal alsó sarokban az <b>„XML megtekintése” (View XML)</b> linkre.<br>
                    4. A megnyíló lap címsorában (URL) a legvégén másold ki a tokent az <code>X-Plex-Token=<b>...</b></code> után!<br>
                    5. Illeszd be a fenti <b>Plex X-Plex-Token</b> mezőbe, és kattints a Mentés gombra.
                </div>
                """,
                unsafe_allow_html=True,
            )

            local_token_found = PlexService.extract_local_token()
            if local_token_found and (not settings.PLEX_TOKEN or settings.PLEX_TOKEN != local_token_found):
                st.info("✨ Találtunk egy helyi Plex tokent a gépeden!")
                if st.button("⚡ Helyi Plex Token Automatikus Kinyerése és Mentése", key="btn_extract_local_plex", use_container_width=True, type="primary"):
                    if orchestrator.plex_service.save_and_apply_token(local_token_found):
                        st.success("🎉 A helyi gépen talált Plex token sikeresen elmentve a .env fájlba!")
                    else:
                        st.info("A token elmentve a .env-be! Indítsd el a Plex Médiaszervert a kapcsolat aktiválásához.")
                    st.rerun()

            st.caption("Vagy használhatod az alábbi fiók- vagy böngésző-alapú kényelmi funkciókat:")
            p_tab_acc, p_tab_pin = st.tabs(["🔐 Plex Fiók Belépés", "🌐 Böngészős Jóváhagyás (PIN)"])

            with p_tab_acc:
                st.markdown("<small>A hivatalos <code>python-plexapi</code> motorral közvetlenül a Plex fiókodból kéri le a tokent és a szervereid címét.</small>", unsafe_allow_html=True)
                u_plex = st.text_input("Plex Email vagy Felhasználónév", key="inp_plex_acc_user")
                p_plex = st.text_input("Plex Jelszó", type="password", key="inp_plex_acc_pass")
                c_plex = st.text_input("2FA Kód (ha van kétlépcsős azonosítás, egyébként üres)", key="inp_plex_acc_code")

                if st.button("🚀 Token Lekérése és Mentése", key="btn_fetch_plex_token_acc", use_container_width=True):
                    if not u_plex or not p_plex:
                        st.warning("Add meg a Plex felhasználóneved/email címed és jelszavad!")
                    else:
                        with st.spinner("Kapcsolódás a plex.tv fiókhoz..."):
                            res_tok = PlexService.fetch_token_from_plex_account(u_plex.strip(), p_plex.strip(), c_plex.strip() if c_plex else None)
                            if res_tok.get("success"):
                                tok = res_tok["token"]
                                servers = res_tok.get("servers", [])
                                serv_url = servers[0]["uri"] if servers else None
                                orchestrator.plex_service.save_and_apply_token(tok, serv_url)
                                st.success(f"🎉 Sikeres belépés mint '{res_tok['username']}'! A Plex token és szerver elmentve a .env-be.")
                                st.rerun()
                            else:
                                st.error(f"❌ {res_tok.get('message', 'Sikertelen bejelentkezés')}")

            with p_tab_pin:
                st.markdown("<small>Jelszó beírása nélkül: nyisd meg a hivatalos Plex oldalt, hagyd jóvá, és a token automatikusan megérkezik!</small>", unsafe_allow_html=True)
                if "plex_pin_login" not in st.session_state:
                    if st.button("🌐 Plex Hitelesítési Link Létrehozása", key="btn_start_plex_pin"):
                        pin_res = PlexService.start_pin_login()
                        if pin_res.get("success"):
                            st.session_state["plex_pin_login"] = pin_res["pin_login"]
                            st.session_state["plex_oauth_url"] = pin_res["oauth_url"]
                            st.rerun()
                        else:
                            st.error(f"Nem sikerült indítani: {pin_res.get('message')}")
                else:
                    oauth_url = st.session_state.get("plex_oauth_url", "")
                    st.markdown(f"👉 **[Kattints ide a Plex Bejelentkezéshez]({oauth_url})**", unsafe_allow_html=True)
                    if st.button("✅ Jóváhagytam a böngészőben, Token Mentése!", key="btn_check_pin_now", type="primary", use_container_width=True):
                        pin_obj = st.session_state["plex_pin_login"]
                        got_tok = PlexService.check_pin_login(pin_obj)
                        if got_tok:
                            orchestrator.plex_service.save_and_apply_token(got_tok)
                            st.session_state.pop("plex_pin_login", None)
                            st.session_state.pop("plex_oauth_url", None)
                            st.success("🎉 Sikeres Plex jóváhagyás! A token elmentve a .env fájlba.")
                            st.rerun()
                        else:
                            st.warning("⚠️ A jóváhagyás még nem történt meg a böngészőben. Nyisd meg a linket, jelentkezz be, majd kattints újra!")

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
