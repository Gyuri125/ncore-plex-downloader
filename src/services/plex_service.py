import os
import platform
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from loguru import logger

try:
    from plexapi.server import PlexServer
except ImportError:
    PlexServer = None

from src.config.settings import get_settings


class PlexService:
    """
    Plex Médiaszerver API integráció.
    Felelős a Plex szerverhez való csatlakozásért és a könyvtárak automatikus
    frissítéséért (Library Scan), hogy az új filmek/sorozatok azonnal megjelenjenek a TV-n.
    """

    def __init__(self, server_url: Optional[str] = None, token: Optional[str] = None):
        settings = get_settings()
        self.server_url = settings.PLEX_SERVER_URL if server_url is None else server_url
        self.auto_refresh = settings.AUTO_REFRESH_PLEX
        self.server: Optional["PlexServer"] = None

        # Memóriabeli TTL gyorsítótár a has_media lekérdezésekhez (120 mp lejárattal)
        self._has_media_cache: Dict[Tuple[str, Optional[int]], Tuple[bool, float]] = {}
        self._cache_ttl = 120.0

        if token is None:
            self.token = settings.PLEX_TOKEN
            # Ha a beállításokban sincs token, megkíséreljük automatikusan kinyerni a helyi gépből
            if not self.token or self.token == "ide_ird_a_plex_tokent":
                local_tok = self.extract_local_token()
                if local_tok:
                    self.token = local_tok
                    try:
                        from src.config.settings import save_settings
                        save_settings({"PLEX_TOKEN": local_tok})
                        logger.info("A helyi gépről automatikusan kinyert Plex token mentve a .env fájlba!")
                    except Exception as save_err:
                        logger.debug(f"Plex token mentési figyelmeztetés: {save_err}")
        else:
            self.token = token

    @staticmethod
    def extract_local_token() -> Optional[str]:
        """
        Megkísérli automatikusan kinyerni a Plex hitelesítési tokent a helyi gépből.
        - Windows esetén a Registry-ből (HKCU/HKLM) és a %LOCALAPPDATA% Preferences.xml-ből.
        - Linux / macOS esetén a Preferences.xml fájlból.
        """
        system = platform.system()

        if system == "Windows":
            # 1. Windows Registry (HKCU és HKLM)
            try:
                import winreg
                key_paths = [
                    (winreg.HKEY_CURRENT_USER, r"Software\Plex, Inc.\Plex Media Server"),
                    (winreg.HKEY_LOCAL_MACHINE, r"Software\Plex, Inc.\Plex Media Server"),
                    (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Plex, Inc.\Plex Media Server"),
                ]
                for root_k, kp in key_paths:
                    try:
                        with winreg.OpenKey(root_k, kp) as key:
                            token, _ = winreg.QueryValueEx(key, "PlexOnlineToken")
                            if token and isinstance(token, str) and len(token.strip()) > 5:
                                logger.info("Plex token sikeresen kinyerve a Windows Registry-ből!")
                                return token.strip()
                    except Exception:
                        pass
            except Exception as e:
                logger.debug(f"Helyi Windows Plex Registry token nem található: {e}")

        # 2. Preferences.xml keresése (Windows, macOS, Linux)
        candidate_paths = []
        if system == "Windows":
            local_app_data = os.environ.get("LOCALAPPDATA")
            prog_data = os.environ.get("PROGRAMDATA")
            if local_app_data:
                candidate_paths.append(Path(local_app_data) / "Plex Media Server" / "Preferences.xml")
            if prog_data:
                candidate_paths.append(Path(prog_data) / "Plex Media Server" / "Preferences.xml")
            candidate_paths.append(Path.home() / "AppData" / "Local" / "Plex Media Server" / "Preferences.xml")
        else:
            candidate_paths.extend([
                Path.home() / "Library/Application Support/Plex Media Server/Preferences.xml",
                Path.home() / ".config/plexmediaserver/Library/Application Support/Plex Media Server/Preferences.xml",
                Path("/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/Preferences.xml"),
            ])

        for pref_file in candidate_paths:
            try:
                if pref_file.exists():
                    import xml.etree.ElementTree as ET
                    tree = ET.parse(pref_file)
                    root = tree.getroot()
                    token = root.attrib.get("PlexOnlineToken")
                    if token and len(token.strip()) > 5:
                        logger.info(f"Plex token sikeresen kinyerve a Preferences.xml-ből: {pref_file}")
                        return token.strip()
            except Exception as e:
                logger.debug(f"Hiba a Preferences.xml olvasásakor ({pref_file}): {e}")

        return None

    @staticmethod
    def fetch_token_from_plex_account(
        username: str, password: str, code: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Plex token megszerzése hivatalos MyPlexAccount belépéssel (ahogy a python-plexapi plex-gettoken.py csinálja).
        Sikeres belépés esetén visszaadja a tokent, a felhasználói fiók nevét és az elérhető Plex szervereket.
        """
        try:
            from plexapi.myplex import MyPlexAccount
            logger.info(f"Kapcsolódás a plex.tv MyPlex fiókhoz mint '{username}'...")
            account = MyPlexAccount(username=username, password=password, code=code)
            token = account.authenticationToken

            servers = []
            try:
                for r in account.resources():
                    if getattr(r, "provides", "") == "server":
                        local_conn = next((c.uri for c in r.connections if getattr(c, "local", False)), None)
                        primary_conn = local_conn or (r.connections[0].uri if r.connections else "http://localhost:32400")
                        servers.append({
                            "name": r.name,
                            "uri": primary_conn,
                            "all_connections": [c.uri for c in r.connections],
                            "access_token": getattr(r, "accessToken", token),
                        })
            except Exception as s_err:
                logger.debug(f"Plex szerverek felderítési figyelmeztetés: {s_err}")

            return {
                "success": True,
                "token": token,
                "username": account.username,
                "email": getattr(account, "email", ""),
                "servers": servers,
            }
        except Exception as e:
            logger.error(f"Sikertelen Plex fiók belépés: {e}")
            return {
                "success": False,
                "message": f"Nem sikerült bejelentkezni a Plex fiókba: {e}",
            }

    @staticmethod
    def start_pin_login() -> Dict[str, Any]:
        """
        Plex OAuth / PIN alapú hitelesítés indítása (jelszó megadása nélküli böngészős összekapcsolás).
        """
        try:
            from plexapi.myplex import MyPlexPinLogin
            pin_login = MyPlexPinLogin(oauth=True)
            code = pin_login._getCode()
            oauth_url = pin_login.oauthUrl()
            return {
                "success": True,
                "pin_login": pin_login,
                "code": code,
                "oauth_url": oauth_url,
            }
        except Exception as e:
            logger.error(f"Nem sikerült elindítani a Plex PIN hitelesítést: {e}")
            return {"success": False, "message": str(e)}

    @staticmethod
    def check_pin_login(pin_login) -> Optional[str]:
        """
        Ellenőrzi a folyamatban lévő PIN / OAuth hitelesítés állapotát.
        Ha a felhasználó jóváhagyta a böngészőben, visszaadja az authenticationToken-t.
        """
        try:
            if pin_login.checkLogin():
                return pin_login.token
            return None
        except Exception as e:
            logger.debug(f"PIN ellenőrzési hiba: {e}")
            return None

    def save_and_apply_token(self, token: str, server_url: Optional[str] = None) -> bool:
        """
        Elmenti az új Plex tokent (és opcionálisan URL-t) a .env fájlba,
        frissíti a futó konfigurációt, és újrakapcsolódik a szerverhez.
        """
        from src.config.settings import save_settings
        updates = {"PLEX_TOKEN": token}
        if server_url:
            updates["PLEX_SERVER_URL"] = server_url

        ok = save_settings(updates)
        if ok:
            self.token = token
            if server_url:
                self.server_url = server_url
            self.clear_cache()
            return self.connect()
        return False

    def _is_configured(self) -> bool:
        return bool(
            self.server_url
            and self.token
            and self.token != "ide_ird_a_plex_tokent"
        )

    def connect(self) -> bool:
        """
        Csatlakozik a Plex Médiaszerverhez a megadott URL és X-Plex-Token segítségével.
        """
        if PlexServer is None:
            logger.warning("A 'plexapi' könyvtár nincs telepítve! Futtasd: pip install plexapi")
            return False

        if not self._is_configured():
            logger.warning("Plex szerver URL vagy Token nincs konfigurálva a .env fájlban.")
            return False

        try:
            logger.info(f"Csatlakozás a Plex szerverhez: {self.server_url}...")
            self.server = PlexServer(baseurl=self.server_url, token=self.token, timeout=6)
            logger.info(f"Sikeres kapcsolat a Plexhez! Szerver név: '{self.server.friendlyName}', Verzió: {self.server.version}")
            return True

        except Exception as e:
            logger.error(f"Nem sikerült csatlakozni a Plex szerverhez ({self.server_url}): {str(e)}")
            self.server = None
            return False

    def is_connected(self) -> bool:
        return self.server is not None

    def refresh_library(self, media_type: str = "all") -> bool:
        """
        Könyvtár frissítés (Library Scan) triggerelése.
        media_type lehet: 'movie', 'show' vagy 'all'.
        """
        if not self.auto_refresh:
            logger.info("Automatikus Plex frissítés ki van kapcsolva a beállításokban.")
            return True

        if not self.is_connected() and not self.connect():
            logger.warning("Plex könyvtárfrissítés kihagyva (nincs aktív szerver kapcsolat).")
            return False

        try:
            sections = self.server.library.sections()
            logger.info(f"Plex könyvtárfrissítés indítása (típus: {media_type})...")

            scanned_any = False
            for section in sections:
                if media_type == "all" or section.type == media_type:
                    logger.info(f"Plex szekció frissítése: '{section.title}' (Típus: {section.type})")
                    section.update()
                    scanned_any = True

            if scanned_any:
                logger.info("Plex könyvtárfrissítés sikeresen elindítva!")
                self.clear_cache()
                return True
            else:
                logger.warning(f"Nem található megfelelő típusú Plex szekció ({media_type})")
                return False

        except Exception as e:
            logger.error(f"Hiba a Plex könyvtárfrissítés során: {str(e)}")
            return False

    def clear_cache(self):
        """Kiüríti a meglévő Plex média jelenléti gyorsítótárat."""
        self._has_media_cache.clear()

    def refresh_movies(self) -> bool:
        """Kifejezetten a filmek szekció frissítése."""
        return self.refresh_library(media_type="movie")

    def refresh_series(self) -> bool:
        """Kifejezetten a sorozatok szekció frissítése."""
        return self.refresh_library(media_type="show")

    def has_media(
        self, title: str, year: Optional[int] = None, alt_title: Optional[str] = None
    ) -> bool:
        """
        Ellenőrzi, hogy a film vagy sorozat már szerepel-e a Plex könyvtárban.
        Támogatja az elsődleges magyar és az alternatív/angol/eredeti cím szerinti egyeztetést is.
        Memóriabeli TTL gyorsítótárral van ellátva (120 mp), így kártyák kirajzolásakor
        nem indít külön hálózati kérést, hanem azonnal, 0.001 ms alatt visszaadja az eredményt.
        """
        if not title and not alt_title:
            return False

        clean_title = (title or "").strip().lower()
        clean_alt = (alt_title or "").strip().lower()
        cache_key = (clean_title, clean_alt, year)
        now = time.time()

        if cache_key in self._has_media_cache:
            res_val, timestamp = self._has_media_cache[cache_key]
            if now - timestamp < self._cache_ttl:
                return res_val

        if not self._is_configured():
            return False
        if not self.is_connected() and not self.connect():
            return False

        try:
            # Elsődleges és alternatív keresési kulcsszavak
            search_queries = []
            for q in [title, alt_title]:
                if q and q.strip() and q.strip() not in search_queries:
                    search_queries.append(q.strip())

            found = False
            for sq in search_queries:
                results = self.server.library.search(sq)
                for res in results:
                    p_title = res.title.strip().lower() if hasattr(res, "title") and res.title else ""
                    p_orig = getattr(res, "originalTitle", "").strip().lower() if hasattr(res, "originalTitle") and res.originalTitle else ""

                    matches = (
                        (clean_title and (p_title == clean_title or p_orig == clean_title))
                        or (clean_alt and (p_title == clean_alt or p_orig == clean_alt))
                    )
                    if matches:
                        if year and hasattr(res, "year") and res.year:
                            if abs(res.year - year) <= 1:
                                found = True
                                break
                        else:
                            found = True
                            break
                if found:
                    break

            self._has_media_cache[cache_key] = (found, now)
            return found
        except Exception:
            self._has_media_cache[cache_key] = (False, now)
            return False

    def get_watched_items(self) -> List[Dict[str, Any]]:
        """
        Lekéri a Plex szerverről a már megnézett (isWatched == True vagy viewCount > 0)
        filmeket és epizódokat méretükkel és utolsó megtekintési dátumukkal,
        méret szerint csökkenő sorrendben a tárhely felszabadításához.
        """
        if not self.is_connected() and not self.connect():
            return []

        watched_items = []
        try:
            sections = self.server.library.sections()
            for sec in sections:
                if sec.type not in ("movie", "show"):
                    continue

                try:
                    items = sec.search(unwatched=False) if hasattr(sec, "search") else sec.all()
                except Exception:
                    items = sec.all()

                for m in items:
                    is_w = getattr(m, "isWatched", False) or (getattr(m, "viewCount", 0) or 0) > 0
                    if not is_w:
                        continue

                    file_paths = []
                    total_bytes = 0
                    for media in getattr(m, "media", []):
                        for part in getattr(media, "parts", []):
                            if hasattr(part, "file") and part.file:
                                file_paths.append(part.file)
                            if hasattr(part, "size") and part.size:
                                total_bytes += part.size

                    if total_bytes == 0 and file_paths:
                        for fp in file_paths:
                            p = Path(fp)
                            if p.exists() and p.is_file():
                                total_bytes += p.stat().st_size

                    size_gb = total_bytes / (1024 ** 3)
                    last_v = getattr(m, "lastViewedAt", None)
                    last_v_str = (
                        last_v.strftime("%Y-%m-%d %H:%M")
                        if hasattr(last_v, "strftime")
                        else (str(last_v) if last_v else "Ismeretlen")
                    )

                    watched_items.append({
                        "rating_key": str(getattr(m, "ratingKey", "")),
                        "title": getattr(m, "title", "N/A"),
                        "year": getattr(m, "year", None),
                        "type": sec.type,
                        "size_bytes": total_bytes,
                        "size_human": f"{size_gb:.2f} GB" if size_gb >= 1.0 else f"{total_bytes / (1024 ** 2):.1f} MB",
                        "file_paths": file_paths,
                        "last_viewed_at": last_v_str,
                        "view_count": getattr(m, "viewCount", 1),
                    })

            watched_items.sort(key=lambda x: x["size_bytes"], reverse=True)
            return watched_items
        except Exception as e:
            logger.error(f"Hiba a megnézett Plex elemek lekérdezésekor: {e}")
            return []

    def get_active_sessions(self) -> List[Dict[str, Any]]:
        """
        Lekéri a Plex szerveren jelenleg aktív lejátszási munkameneteket (TV, tablet, web kliens).
        """
        if not self.is_connected() and not self.connect():
            return []

        active = []
        try:
            sessions = self.server.sessions()
            for s in sessions:
                user_list = getattr(s, "usernames", [])
                p_obj = getattr(s, "player", None)
                active.append({
                    "user": user_list[0] if user_list else "Felhasználó",
                    "player": getattr(p_obj, "title", "Kliens") if p_obj else "Kliens",
                    "title": getattr(s, "title", "Média"),
                    "state": getattr(p_obj, "state", "playing") if p_obj else "playing",
                    "bandwidth": getattr(s, "bandwidth", None),
                })
            return active
        except Exception as e:
            logger.debug(f"Plex munkamenet lekérdezési figyelmeztetés: {e}")
            return []

    def has_active_playback(self) -> bool:
        """
        Megvizsgálja, hogy éppen néz-e valaki filmet vagy sorozatot a Plexen
        (akadozásmentes 4K lejátszást biztosító torrent sávszélesség-korlátozáshoz).
        """
        sessions = self.get_active_sessions()
        return any(s.get("state") in ("playing", "buffering") for s in sessions)

    def delete_item_files(self, file_paths: List[str]) -> Tuple[bool, int]:
        """
        Törli a megnézett médiafájlokat a merevlemezről, és frissíti a könyvtárat.
        Visszaadja a sikeres műveletet és a felszabadított bájtok számát.
        """
        freed_bytes = 0
        success_any = False
        for fp in file_paths:
            try:
                p = Path(fp)
                if p.exists() and p.is_file():
                    sz = p.stat().st_size
                    p.unlink()
                    freed_bytes += sz
                    success_any = True
                    # Ha a szülőmappa kiürült, takarítsuk el azt is
                    parent = p.parent
                    if parent.exists() and not any(parent.iterdir()):
                        parent.rmdir()
            except Exception as e:
                logger.error(f"Nem sikerült törölni a médiafájlt ({fp}): {e}")

        if success_any:
            self.refresh_library()
        return success_any, freed_bytes
