import json
import os
import platform
import sys
import time
from datetime import datetime
from typing import Any, Dict, List
from loguru import logger

from src.config.settings import get_settings
from src.models.media import MediaType, MediaItem, TorrentRelease


class DiagnosticService:
    """
    Részletes rendszer- és keresési diagnosztikai szolgáltatás.
    Lehetővé teszi a teljes folyamat lépésről lépésre történő tesztelését
    és átlátható hibajelentés generálását a felhasználó számára.
    """

    def __init__(self, orchestrator=None):
        self.settings = get_settings()
        self.orchestrator = orchestrator

    def get_system_health(self) -> Dict[str, Any]:
        """Lekéri a háttérrendszerek aktuális állapotát."""
        health = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "python": {
                "version": sys.version.split()[0],
                "executable": sys.executable,
                "pid": os.getpid(),
                "platform": platform.platform(),
            },
            "services": {}
        }

        # 1. TMDB ellenőrzés
        has_tmdb_key = bool(self.settings.TMDB_API_KEY)
        health["services"]["tmdb"] = {
            "configured": has_tmdb_key,
            "api_key_masked": (self.settings.TMDB_API_KEY[:4] + "..." + self.settings.TMDB_API_KEY[-4:]) if len(self.settings.TMDB_API_KEY) > 8 else "Nincs megadva",
            "status": "OK" if has_tmdb_key else "HIÁNYZIK"
        }

        # 2. nCore ellenőrzés
        ncore_user = bool(self.settings.NCORE_USERNAME)
        ncore_pwd = bool(self.settings.NCORE_PASSWORD)
        health["services"]["ncore"] = {
            "configured": ncore_user and ncore_pwd,
            "username": self.settings.NCORE_USERNAME if ncore_user else "Nincs megadva",
            "status": "OK" if (ncore_user and ncore_pwd) else "HIÁNYZIK"
        }

        # 3. qBittorrent ellenőrzés
        qb_ok = False
        qb_info = "Nem érhető el"
        try:
            if self.orchestrator and self.orchestrator.torrent_service:
                client = self.orchestrator.torrent_service._get_client()
                if client:
                    qb_ok = True
                    qb_info = f"Csatlakozva (Verzió: {client.app.version})"
        except Exception as e:
            qb_info = str(e)

        health["services"]["qbittorrent"] = {
            "host": self.settings.QBITTORRENT_HOST,
            "connected": qb_ok,
            "details": qb_info,
        }

        # 4. Plex ellenőrzés
        plex_ok = False
        plex_info = "Nem érhető el"
        try:
            if self.orchestrator and self.orchestrator.plex_service:
                srv = self.orchestrator.plex_service._get_server()
                if srv:
                    plex_ok = True
                    sections = [s.title for s in srv.library.sections]
                    plex_info = f"Csatlakozva ({srv.friendlyName}), Könyvtárak: {', '.join(sections)}"
        except Exception as e:
            plex_info = str(e)

        health["services"]["plex"] = {
            "server_url": self.settings.PLEX_SERVER_URL,
            "has_token": bool(self.settings.PLEX_TOKEN),
            "connected": plex_ok,
            "details": plex_info,
        }

        return health

    def run_search_diagnostic(self, query: str) -> Dict[str, Any]:
        """
        Lépésről lépésre végrehajtja a keresési logikát a megadott címre,
        és részletesen dokumentálja, hol mi történt.
        """
        report: Dict[str, Any] = {
            "query": query,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "steps": [],
            "final_results": [],
            "error": None,
        }

        if not self.orchestrator:
            report["error"] = "Orchestrator nincs inicializálva."
            return report

        # 1. LÉPÉS: TMDB lekérdezés és variációk
        t0 = time.time()
        tmdb_step = {
            "name": "1. TMDB Metaadat Lekérdezés",
            "searched_variants": [],
            "items_found": [],
        }

        tmdb = self.orchestrator.tmdb_service
        raw_variants = [query]
        clean_no_spaces = query.replace(" ", "")
        if clean_no_spaces != query:
            raw_variants.append(clean_no_spaces)

        tmdb_step["searched_variants"] = raw_variants

        items = tmdb.search(query)
        tmdb_step["duration_sec"] = round(time.time() - t0, 3)
        tmdb_step["count"] = len(items)

        for it in items:
            tmdb_step["items_found"].append({
                "title_hu": it.title_hu,
                "title_en": it.title_en,
                "year": it.year,
                "type": it.media_type.value,
                "tmdb_id": it.tmdb_id,
                "imdb_id": it.imdb_id,
                "seasons": it.number_of_seasons,
            })
        report["steps"].append(tmdb_step)

        # 2. LÉPÉS: nCore Fallback (ha TMDB üres volt)
        fallback_step = {
            "name": "2. nCore Fallback Keresés",
            "triggered": len(items) == 0,
            "ncore_raw_hits": 0,
            "resolved_items": [],
        }
        if not items:
            try:
                ncore_res = self.orchestrator.ncore_service.search_by_title(query, MediaType.TV)
                if not ncore_res:
                    ncore_res = self.orchestrator.ncore_service.search_by_title(query, MediaType.MOVIE)
                fallback_step["ncore_raw_hits"] = len(ncore_res)
                for r in ncore_res:
                    if r.imdb_id:
                        resolved = tmdb.get_by_imdb_id(r.imdb_id)
                        if resolved:
                            fallback_step["resolved_items"].append({
                                "imdb_id": r.imdb_id,
                                "title": resolved.title_hu,
                                "type": resolved.media_type.value
                            })
                            items.append(resolved)
                            if len(items) >= 4:
                                break
            except Exception as e:
                fallback_step["error"] = str(e)
        report["steps"].append(fallback_step)

        # 3. LÉPÉS: nCore Kiadások vizsgálata a legelső találatra
        if items:
            target_item = items[0]
            ncore_step = {
                "name": f"3. nCore Kiadások Lekérése: '{target_item.title_hu}' ({target_item.imdb_id or 'nincs IMDb'})",
                "target_media": target_item.title_hu,
                "raw_releases_count": 0,
                "verified_releases": [],
                "rejected_releases": [],
            }

            try:
                # Keresés közvetlenül az nCore-on
                ncore_service = self.orchestrator.ncore_service
                raw_releases = []
                if target_item.imdb_id:
                    raw_releases = ncore_service.search_by_imdb(target_item.imdb_id, target_item.media_type)
                if not raw_releases:
                    raw_releases = ncore_service.search_by_title(target_item.title_hu, target_item.media_type)
                if not raw_releases and target_item.title_en:
                    raw_releases = ncore_service.search_by_title(target_item.title_en, target_item.media_type)

                ncore_step["raw_releases_count"] = len(raw_releases)

                # Érvényesítés és pontozás vizsgálata
                for rel in raw_releases:
                    is_match = ncore_service.verify_release_match(rel, target_item)
                    rel_summary = {
                        "title": rel.title,
                        "category": rel.category,
                        "season_tag": rel.season_tag,
                        "size": rel.size_human,
                        "seeders": rel.seeders,
                        "hungarian_audio": rel.has_hungarian_audio,
                    }
                    if is_match:
                        score = ncore_service.score_release(rel, prefer_hungarian=True)
                        rel_summary["score"] = score
                        ncore_step["verified_releases"].append(rel_summary)
                    else:
                        ncore_step["rejected_releases"].append(rel_summary)

                ncore_step["verified_releases"].sort(key=lambda x: x.get("score", 0), reverse=True)
            except Exception as e:
                ncore_step["error"] = str(e)

            report["steps"].append(ncore_step)

        # Végső eredmények összefoglalása
        for it in items:
            report["final_results"].append({
                "title_hu": it.title_hu,
                "title_en": it.title_en,
                "year": it.year,
                "imdb_id": it.imdb_id,
                "media_type": it.media_type.value,
            })

        return report

    @staticmethod
    def format_report_as_markdown(report: Dict[str, Any]) -> str:
        """Könnyen olvasható és másolható Markdown jelentést készít a diagnosztikából."""
        lines = []
        lines.append(f"### 📋 Hibakeresési Jelentés: `{report.get('query')}`")
        lines.append(f"⏱️ **Időpont:** {report.get('timestamp')}")
        lines.append("")

        for step in report.get("steps", []):
            lines.append(f"#### {step.get('name')}")
            if "searched_variants" in step:
                lines.append(f"- **Kipróbált kifejezések:** `{', '.join(step['searched_variants'])}`")
                lines.append(f"- **Találatok száma:** {step.get('count')} db ({step.get('duration_sec', 0)} mp)")
                for item in step.get("items_found", []):
                    lines.append(f"  - 🎬 **{item.get('title_hu')}** ({item.get('year')}) | IMDb: `{item.get('imdb_id')}` | Típus: `{item.get('type')}`")

            if "triggered" in step and step.get("triggered"):
                lines.append(f"- **nCore fallback lefutott:** {step.get('ncore_raw_hits')} találat")
                if "error" in step:
                    lines.append(f"- ⚠️ **Hiba:** `{step['error']}`")

            if "raw_releases_count" in step:
                lines.append(f"- **nCore nyers találatok:** {step.get('raw_releases_count')} db")
                lines.append(f"- **Elfogadott kiadások:** {len(step.get('verified_releases', []))} db")
                lines.append(f"- **Kiszűrt/elutasított kiadások:** {len(step.get('rejected_releases', []))} db")
                if step.get("verified_releases"):
                    lines.append("  - **Top kiadás:** " + step["verified_releases"][0]["title"] + f" (Kategória: {step['verified_releases'][0]['category']})")

            lines.append("")

        lines.append("---")
        return "\n".join(lines)
