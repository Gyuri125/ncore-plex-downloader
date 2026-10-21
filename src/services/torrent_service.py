import socket
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional
from loguru import logger

try:
    import qbittorrentapi
except ImportError:
    qbittorrentapi = None

from src.config.settings import get_settings


class TorrentService:
    """
    qBittorrent WebUI vezérlő és nCore Hit & Run figyelő szolgáltatás.
    Felelős a torrentek hozzáadásáért, a letöltési állapot követéséért,
    valamint a 48 órás seedelési kötelezettség / 1.0 arány betartásáért.
    """

    DEFAULT_CATEGORY = "plex-auto"

    def __init__(
        self,
        host: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ):
        settings = get_settings()
        self.host = host or settings.QBITTORRENT_HOST
        self.username = username or settings.QBITTORRENT_USERNAME
        self.password = password or settings.QBITTORRENT_PASSWORD
        self.min_seed_hours = settings.MIN_SEED_TIME_HOURS
        self.target_ratio = settings.TARGET_RATIO
        self.auto_remove = settings.AUTO_REMOVE_AFTER_SEED
        self.temp_dir = settings.DOWNLOAD_TEMP_DIR

        self.client: Optional["qbittorrentapi.Client"] = None
        self._last_failed_connect = 0.0

    def connect(self) -> bool:
        """
        Csatlakozik a qBittorrent WebUI felületéhez gyors időkéréssel.
        """
        if qbittorrentapi is None:
            logger.error("A 'qbittorrent-api' könyvtár nincs telepítve! Futtasd: pip install qbittorrent-api")
            return False

        # Ha nemrég próbáltuk és nem fut, azonnal lépjünk tovább
        if time.time() - self._last_failed_connect < 15.0:
            return False

        # Előzetes gyors socket ellenőrzés (ha a port zárva van, 0.2s alatt kiderül a 25s requests retry helyett)
        try:
            parsed = urllib.parse.urlparse(self.host)
            host_name = parsed.hostname or "127.0.0.1"
            port_num = parsed.port or 8080
            sock = socket.create_connection((host_name, port_num), timeout=0.2)
            sock.close()
        except Exception:
            self.client = None
            self._last_failed_connect = time.time()
            return False

        try:
            self.client = qbittorrentapi.Client(
                host=self.host,
                username=self.username,
                password=self.password,
                REQUESTS_ARGS={"timeout": (1.0, 2.0)},
            )
            self.client.auth_log_in()
            version = self.client.app.version
            api_version = self.client.app.web_api_version
            logger.info(f"Sikeres kapcsolat a qBittorrenthez! (v{version}, WebAPI: v{api_version})")
            return True

        except qbittorrentapi.LoginFailed:
            logger.error(f"Sikertelen qBittorrent bejelentkezés! Ellenőrizd a felhasználónevet és jelszót: {self.host}")
            self.client = None
            self._last_failed_connect = time.time()
            return False
        except qbittorrentapi.APIConnectionError as e:
            logger.error(f"Nem sikerült csatlakozni a qBittorrent WebUI-hoz ({self.host}): {str(e)}")
            self.client = None
            self._last_failed_connect = time.time()
            return False
        except Exception as e:
            logger.error(f"Váratlan hiba a qBittorrent kapcsolódáskor: {str(e)}")
            self.client = None
            self._last_failed_connect = time.time()
            return False

    def is_connected(self) -> bool:
        """
        Ellenőrzi az aktív kapcsolatot.
        """
        if not self.client:
            return False
        try:
            return bool(self.client.auth.is_logged_in)
        except Exception:
            return False

    def _ensure_connected(self) -> bool:
        if self.is_connected():
            return True
        return self.connect()

    def add_torrent(
        self,
        torrent_file: Path,
        save_path: Optional[Path] = None,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> Optional[str]:
        """
        Hozzáad egy .torrent fájlt a qBittorrenthez.
        Visszaadja a torrent hash-ét, ha sikeres.
        """
        if not self._ensure_connected():
            logger.error("Nincs qBittorrent kapcsolat a torrent hozzáadásához.")
            return None

        if not torrent_file.exists():
            logger.error(f"A torrent fájl nem található a lemezen: {torrent_file}")
            return None

        target_save_path = str(save_path or self.temp_dir)
        target_category = category or self.DEFAULT_CATEGORY
        target_tags = tags or ["ncore", "auto"]

        try:
            logger.info(f"Torrent küldése a qBittorrentnek: {torrent_file.name}")
            with open(torrent_file, "rb") as f:
                torrent_content = f.read()

            # Előző torrentek listázása a hash azonosításához
            existing_hashes = {t.hash for t in self.client.torrents_info()}

            response = self.client.torrents_add(
                torrent_files=torrent_content,
                save_path=target_save_path,
                category=target_category,
                tags=",".join(target_tags),
                is_paused=False,
            )

            # Sikeresség ellenőrzése
            if response == "Fails.":
                logger.error("A qBittorrent elutasította a torrent hozzáadását!")
                return None

            # Megkeressük az új torrent hash-ét
            new_torrents = self.client.torrents_info()
            for t in new_torrents:
                if t.hash not in existing_hashes:
                    logger.info(f"Torrent sikeresen elindítva! Név: '{t.name}', Hash: {t.hash}")
                    return t.hash

            # Ha már létezett a kliensben, keressük név alapján
            for t in new_torrents:
                if t.name in torrent_file.stem or torrent_file.stem in t.name:
                    logger.info(f"Már létező torrent megtalálva: '{t.name}', Hash: {t.hash}")
                    return t.hash

            return "OK"

        except Exception as e:
            logger.error(f"Hiba a torrent hozzáadásakor: {str(e)}")
            return None

    def get_status(self, torrent_hash: str) -> Optional[Dict[str, Any]]:
        """
        Lekéri egy torrent pontos állapotát (haladás, állapot, arány, seedelési idő).
        """
        if not self._ensure_connected():
            return None

        try:
            torrents = self.client.torrents_info(torrent_hashes=torrent_hash)
            if not torrents:
                return None

            t = torrents[0]
            progress_pct = round(t.progress * 100, 1)
            seeding_time_hours = round(t.seeding_time / 3600, 1)

            return {
                "hash": t.hash,
                "name": t.name,
                "state": t.state,
                "progress": progress_pct,
                "is_completed": t.progress >= 1.0 or t.state in ("uploading", "pausedUP", "stalledUP"),
                "ratio": round(t.ratio, 2),
                "seeding_time_hours": seeding_time_hours,
                "download_speed_kb": round(t.dlspeed / 1024, 1),
                "upload_speed_kb": round(t.upspeed / 1024, 1),
                "eta_seconds": t.eta,
                "save_path": Path(t.save_path) if t.save_path else None,
                "content_path": Path(t.content_path) if t.content_path else None,
            }

        except Exception as e:
            logger.error(f"Hiba a torrent állapot lekérésekor ({torrent_hash}): {str(e)}")
            return None

    def check_hnr_compliance(self, torrent_hash: str) -> Dict[str, Any]:
        """
        Ellenőrzi az nCore Hit & Run szabályzat teljesülését:
        - Letelt-e a 48 óra (MIN_SEED_TIME_HOURS) aktív seedelés?
        - VAGY elérte-e az 1.0-s feltöltési arányt (TARGET_RATIO)?

        Ha a szabály teljesült, és engedélyezve van az AUTO_REMOVE_AFTER_SEED:
        - Törli a feladatot a qBittorrentből a LETÖLTÖTT FÁJLOK MEGTARTÁSÁVAL!
        """
        status = self.get_status(torrent_hash)
        if not status:
            return {"compliant": False, "reason": "Torrent nem található"}

        seeding_hours = status["seeding_time_hours"]
        ratio = status["ratio"]
        is_completed = status["is_completed"]

        if not is_completed:
            return {
                "compliant": False,
                "reason": f"Még letöltés alatt ({status['progress']}%)",
                "status": status,
            }

        time_met = seeding_hours >= self.min_seed_hours
        ratio_met = ratio >= self.target_ratio

        if time_met or ratio_met:
            reason = (
                f"Seed idő elérve: {seeding_hours}h / {self.min_seed_hours}h"
                if time_met
                else f"Arány elérve: {ratio} / {self.target_ratio}"
            )
            logger.info(f"nCore HnR szabály teljesült! ({status['name']}): {reason}")

            if self.auto_remove:
                logger.info(f"Torrent törlése a qBittorrentből a fájlok megtartásával: {status['name']}")
                self.client.torrents_delete(delete_files=False, torrent_hashes=torrent_hash)
                action_taken = "removed_files_kept"
            else:
                logger.info(f"Torrent leállítása (szüneteltetése): {status['name']}")
                self.client.torrents_pause(torrent_hashes=torrent_hash)
                action_taken = "paused"

            return {
                "compliant": True,
                "reason": reason,
                "action_taken": action_taken,
                "status": status,
            }

        return {
            "compliant": False,
            "reason": f"Feltöltés folyamatban ({seeding_hours}h / {self.min_seed_hours}h vagy arány: {ratio} / {self.target_ratio})",
            "status": status,
        }

    def list_active_downloads(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Listázza a folyamatban lévő vagy nemrég befejezett letöltéseket.
        """
        if not self._ensure_connected():
            return []

        try:
            target_cat = category or self.DEFAULT_CATEGORY
            torrents = self.client.torrents_info(category=target_cat)
            results = []

            for t in torrents:
                results.append({
                    "hash": t.hash,
                    "name": t.name,
                    "state": t.state,
                    "progress": round(t.progress * 100, 1),
                    "is_completed": t.progress >= 1.0 or t.state in ("uploading", "pausedUP", "stalledUP"),
                    "ratio": round(t.ratio, 2),
                    "seeding_time_hours": round(t.seeding_time / 3600, 1),
                    "download_speed_kb": round(t.dlspeed / 1024, 1),
                })
            return results

        except Exception as e:
            logger.error(f"Hiba a letöltések listázásakor: {str(e)}")
            return []
