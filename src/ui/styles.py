"""
CSS stílusok és vizuális sablonok a Streamlit felhasználói felülethez.
"""

import streamlit as st

CUSTOM_CSS = """
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
"""


def apply_custom_styles():
    """Alkalmazza a Streamlit oldal egyedi sötét témájú és reszponzív CSS stílusait."""
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


def get_app_header_html() -> str:
    """Visszaadja a főoldal fejlécének formázott HTML kódját."""
    return """
    <div class="app-header">
        <div class="app-title">🎬 Ncore Média Letöltő <span style='font-size: 0.82rem; background: linear-gradient(135deg, #2563eb, #3b82f6); color: #ffffff; padding: 3px 10px; border-radius: 9999px; font-weight: 700; vertical-align: middle; margin-left: 8px; letter-spacing: 0.5px; box-shadow: 0 2px 8px rgba(37,99,235,0.35);'>v2.0.0</span></div>
        <div class="app-subtitle">Keresd meg a filmet, és a rendszer mindent automatikusan előkészít a TV-re!</div>
    </div>
    """


def get_mobile_hint_html(target_mobile_url: str) -> str:
    """Visszaadja a mobil és tablet használatot segítő tipp sávot."""
    return f"""
    <div class="mobile-hint">
        📱 <b>Mobilról vagy tabletről is használható!</b> Nyisd meg a telefonod böngészőjében: <code>{target_mobile_url}</code> vagy használd a QR-kódot!
    </div>
    """

