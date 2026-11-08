import os
import shutil
import socket
import subprocess
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
        self.delete_torrent_data = getattr(settings, "DELETE_TORRENT_DATA_AFTER_SEED", True)
        self.temp_dir = settings.DOWNLOAD_TEMP_DIR

        self.client: Optional["qbittorrentapi.Client"] = None
        self._last_failed_connect = 0.0
        self._consecutive_failures = 0

    @staticmethod
    def find_qbittorrent_exe() -> Optional[Path]:
        """
        Megkeresi a qBittorrent futtatható állományát (.exe) a gépen.
        """
        settings = get_settings()
        if settings.QBITTORRENT_EXE_PATH:
            p = Path(settings.QBITTORRENT_EXE_PATH)
            if p.exists() and p.is_file():
                return p

        candidates = [
            Path(r"C:\Program Files\qBittorrent\qbittorrent.exe"),
            Path(r"C:\Program Files (x86)\qBittorrent\qbittorrent.exe"),
            Path.home() / "AppData" / "Local" / "Programs" / "qBittorrent" / "qbittorrent.exe",
        ]
        for c in candidates:
            if c.exists() and c.is_file():
                return c

        which_path = shutil.which("qbittorrent")
        if which_path:
            p = Path(which_path)
            if p.exists():
                return p

        return None

    @classmethod
    def is_process_running(cls) -> bool:
        """Ellenőrzi, hogy a qBittorrent folyamat fut-e már a memóriában."""
        try:
            import psutil
            for p in psutil.process_iter(["name"]):
                name = (p.info["name"] or "").lower()
                if "qbittorrent" in name:
                    return True
        except Exception:
            pass
        return False

    @classmethod
    def ensure_qbittorrent_running(cls, wait_timeout: float = 12.0) -> bool:
        """
        Ha a qBittorrent nem fut vagy a WebUI nem válaszol, elindítja a háttérben
        és megvárja, amíg a WebUI (8080) elérhetővé válik.
        """
        settings = get_settings()
        parsed = urllib.parse.urlparse(settings.QBITTORRENT_HOST)
        host_name = parsed.hostname or "127.0.0.1"
        port_num = parsed.port or 8080

        def is_port_open() -> bool:
            try:
                s = socket.create_connection((host_name, port_num), timeout=0.3)
                s.close()
                return True
            except Exception:
                return False

        if is_port_open():
            return True

        exe_path = cls.find_qbittorrent_exe()
        if not exe_path:
            logger.warning("A qBittorrent.exe nem található az ismert elérési utakon.")
            return False

        logger.info(f"qBittorrent indítása a háttérben: {exe_path}")
        try:
            if hasattr(os, "startfile"):
                os.startfile(str(exe_path))
            else:
                subprocess.Popen([str(exe_path)], creationflags=0x00000008)
        except Exception as e:
            logger.error(f"Hiba a qBittorrent indításakor: {e}")
            return False

        start_time = time.time()
        while time.time() - start_time < wait_timeout:
            time.sleep(0.8)
            if is_port_open():
                logger.info(f"qBittorrent sikeresen elindult és a WebUI elérhető ({host_name}:{port_num})!")
                return True

        return is_port_open()

    def connect(self, auto_start: bool = True) -> bool:
        """
        Csatlakozik a qBittorrent WebUI felületéhez gyors időkéréssel.
        Ha a kapcsolat nem él és auto_start=True, kísérletet tesz a qBittorrent elindítására.
        """
        if qbittorrentapi is None:
            logger.error("A 'qbittorrent-api' könyvtár nincs telepítve! Futtasd: pip install qbittorrent-api")
            return False

        # Circuit Breaker: Dinamikus pihentetés ismétlődő hibák esetén (10 mp -> max 30 mp)
        backoff = min(30.0, 10.0 + (self._consecutive_failures * 3.0))
        if time.time() - self._last_failed_connect < backoff:
            return False

        # Előzetes gyors socket ellenőrzés
        parsed = urllib.parse.urlparse(self.host)
        host_name = parsed.hostname or "127.0.0.1"
        port_num = parsed.port or 8080

        port_open = False
        try:
            sock = socket.create_connection((host_name, port_num), timeout=0.25)
            sock.close()
            port_open = True
        except Exception:
            port_open = False

        if not port_open:
            if auto_start and (host_name in ("127.0.0.1", "localhost")):
                logger.info("A qBittorrent WebUI zárva van, kísérlet az automatikus indításra...")
                if self.ensure_qbittorrent_running():
                    port_open = True

            if not port_open:
                self.client = None
                self._last_failed_connect = time.time()
                self._consecutive_failures += 1
                return False

        try:
            self.client = qbittorrentapi.Client(
                host=self.host,
                username=self.username,
                password=self.password,
                REQUESTS_ARGS={"timeout": (1.0, 2.5)},
            )
            self.client.auth_log_in()
            version = self.client.app.version
            api_version = self.client.app.web_api_version
            logger.info(f"Sikeres kapcsolat a qBittorrenthez! (v{version}, WebAPI: v{api_version})")
            self._consecutive_failures = 0
            self._last_failed_connect = 0.0
            return True

        except qbittorrentapi.LoginFailed:
            logger.error(f"Sikertelen qBittorrent bejelentkezés! Ellenőrizd a felhasználónevet és jelszót: {self.host}")
            self.client = None
            self._last_failed_connect = time.time()
            self._consecutive_failures += 1
            return False
        except qbittorrentapi.APIConnectionError as e:
            logger.error(f"Nem sikerült csatlakozni a qBittorrent WebUI-hoz ({self.host}): {str(e)}")
            self.client = None
            self._last_failed_connect = time.time()
            self._consecutive_failures += 1
            return False
        except Exception as e:
            logger.error(f"Váratlan hiba a qBittorrent kapcsolódáskor: {str(e)}")
            self.client = None
            self._last_failed_connect = time.time()
            self._consecutive_failures += 1
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

            add_kwargs = {
                "torrent_files": torrent_content,
                "save_path": target_save_path,
                "category": target_category,
                "tags": ",".join(target_tags),
                "is_paused": False,
                "use_auto_torrent_management": False,
                "forced": True,
            }
            try:
                response = self.client.torrents_add(**add_kwargs, upload_mode=False)
            except (TypeError, Exception):
                response = self.client.torrents_add(**add_kwargs)

            # Sikeresség ellenőrzése
            if response == "Fails.":
                logger.error("A qBittorrent elutasította a torrent hozzáadását!")
                return None

            # Megkeressük az új torrent hash-ét
            target_hash = None
            new_torrents = self.client.torrents_info()
            for t in new_torrents:
                if t.hash not in existing_hashes:
                    logger.info(f"Torrent sikeresen elindítva! Név: '{t.name}', Hash: {t.hash}")
                    target_hash = t.hash
                    break

            # Ha már létezett a kliensben, keressük név alapján
            if not target_hash:
                for t in new_torrents:
                    if t.name in torrent_file.stem or torrent_file.stem in t.name:
                        logger.info(f"Már létező torrent megtalálva: '{t.name}', Hash: {t.hash}")
                        target_hash = t.hash
                        break

            # Feltöltési mód kivédése: ellenőrizzük és javítjuk ha véletlenül feltöltési módba került
            if target_hash:
                self.fix_stuck_upload_mode(target_hash)
                return target_hash

            return "OK"

        except Exception as e:
            logger.error(f"Hiba a torrent hozzáadásakor: {str(e)}")
            return None

    def fix_stuck_upload_mode(self, torrent_hash: Optional[str] = None) -> List[str]:
        """
        Kivédi és megszünteti a qBittorrent 'csak feltöltés módban van' hibáját.
        Ha egy torrent még nincs teljesen letöltve (progress < 99.5%), de a kliens
        feltöltőként (uploading, stalledUP, pausedUP, superSeeding) kezeli:
        - Kikapcsolja a super-seedinget
        - Kényszerített letöltési módra állítja (force start)
        - Folytatja a letöltést (resume) és újraellenőrzi a darabokat (recheck).
        """
        if not self._ensure_connected():
            return []

        fixed_torrents: List[str] = []
        try:
            if torrent_hash and torrent_hash != "OK":
                torrents = self.client.torrents_info(torrent_hashes=torrent_hash)
            else:
                torrents = self.client.torrents_info()

            for t in torrents:
                prog = getattr(t, "progress", 0.0)
                state = (getattr(t, "state", "") or "").lower()
                is_super_seed = bool(getattr(t, "super_seeding", False))

                # Ha a letöltés még nincs 100%-on (progress < 0.99)
                if prog < 0.99:
                    is_upload_state = state in ("uploading", "stalledup", "pausedup", "forcedup", "queuedup")
                    if is_upload_state or is_super_seed:
                        logger.warning(
                            f"Feltöltési mód hiba érzékelve ({t.name}, haladás: {round(prog * 100, 1)}%, állapot: {state})! "
                            "Automatikus átkapcsolás aktív letöltésre és újraellenőrzésre..."
                        )
                        try:
                            self.client.torrents_set_super_seeding(enable=False, torrent_hashes=t.hash)
                        except Exception:
                            pass
                        try:
                            self.client.torrents_set_force_start(enable=True, torrent_hashes=t.hash)
                        except Exception:
                            pass
                        try:
                            self.client.torrents_resume(torrent_hashes=t.hash)
                        except Exception:
                            pass
                        try:
                            self.client.torrents_recheck(torrent_hashes=t.hash)
                        except Exception:
                            pass

                        fixed_torrents.append(t.name)
                        logger.info(f"Sikeresen javítva: '{t.name}' átállítva aktív letöltésre!")

        except Exception as exc:
            logger.debug(f"Hiba a feltöltési mód ellenőrzésekor: {exc}")

        return fixed_torrents

    def get_status(self, torrent_hash: str) -> Optional[Dict[str, Any]]:
        """
        Lekéri egy torrent pontos állapotát (haladás, állapot, arány, seedelési idő).
        """
        if not torrent_hash or not str(torrent_hash).strip() or str(torrent_hash).upper() == "OK":
            return None

        if not self._ensure_connected():
            return None

        try:
            torrents = self.client.torrents_info(torrent_hashes=torrent_hash)
            if not torrents:
                return None

            t = torrents[0]
            progress_pct = round(t.progress * 100, 1)
            seeding_time_hours = round(t.seeding_time / 3600, 1)
            state_str = getattr(t, "state", "") or ""
            is_completed = (
                t.progress >= 0.999
                or getattr(t, "seeding_time", 0) > 0
                or "UP" in state_str.upper()
                or state_str in ("uploading", "pausedUP", "stalledUP", "forcedUP", "queuedUP", "checkingUP")
                or getattr(t, "amount_left", 1) == 0
            )

            return {
                "hash": t.hash,
                "name": t.name,
                "state": t.state,
                "progress": progress_pct,
                "is_completed": is_completed,
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

    def check_hnr_compliance(self, torrent_hash: str, delete_data_files: bool = False) -> Dict[str, Any]:
        """
        Ellenőrzi az nCore Hit & Run szabályzat teljesülését:
        - Letelt-e a 48 óra (MIN_SEED_TIME_HOURS) aktív seedelés?
        - VAGY elérte-e az 1.0-s feltöltési arányt (TARGET_RATIO)?

        Ha a szabály teljesült, és engedélyezve van az AUTO_REMOVE_AFTER_SEED:
        - Ha delete_data_files=True (mert a média már átkerült a Plex mappába):
          Törli a torrentet és a letöltési mappában lévő temp fájlokat, felszabadítva a helyet!
        - Egyébként megtartja a fájlokat (delete_files=False).
        """
        status = self.get_status(torrent_hash)
        if not status:
            return {"compliant": False, "reason": "Torrent nem található"}

        seeding_hours = status.get("seeding_time_hours", 0.0)
        ratio = status.get("ratio", 0.0)
        is_completed = status.get("is_completed", False) or seeding_hours > 0

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
                if delete_data_files:
                    logger.info(
                        f"Torrent és letöltési adatok törlése a qBittorrentből (HnR teljesült, Plexben megőrizve): {status['name']}"
                    )
                    self.client.torrents_delete(delete_files=True, torrent_hashes=torrent_hash)
                    action_taken = "removed_and_temp_deleted"
                else:
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

    def list_active_downloads(self, category: Optional[str] = None, include_all: bool = False) -> List[Dict[str, Any]]:
        """
        Listázza a folyamatban lévő vagy nemrég befejezett letöltéseket.
        Ha include_all=True vagy category='all', az összes qBittorrent torrentet visszaadja.
        """
        if not self._ensure_connected():
            return []

        try:
            if include_all or category == "all":
                torrents = self.client.torrents_info()
            else:
                target_cat = category or self.DEFAULT_CATEGORY
                torrents = self.client.torrents_info(category=target_cat)
                # Ha kategória szerint nincs találat, de vannak torrentek, vizsgáljuk az összeset
                if not torrents and category is None:
                    torrents = self.client.torrents_info()

            results = []

            for t in torrents:
                prog = getattr(t, "progress", 0.0)
                state = getattr(t, "state", "") or ""
                seed_t = getattr(t, "seeding_time", 0)
                is_comp = (
                    prog >= 0.999
                    or seed_t > 0
                    or "UP" in state.upper()
                    or state in ("uploading", "pausedUP", "stalledUP", "forcedUP", "queuedUP", "checkingUP")
                    or getattr(t, "amount_left", 1) == 0
                )
                results.append({
                    "hash": t.hash,
                    "name": t.name,
                    "state": state,
                    "progress": round(prog * 100, 1),
                    "is_completed": is_comp,
                    "ratio": round(getattr(t, "ratio", 0.0), 2),
                    "seeding_time_hours": round(seed_t / 3600, 1),
                    "download_speed_kb": round(getattr(t, "dlspeed", 0) / 1024, 1),
                    "upload_speed_kb": round(getattr(t, "upspeed", 0) / 1024, 1),
                    "eta_seconds": getattr(t, "eta", 0),
                    "save_path": Path(t.save_path) if getattr(t, "save_path", None) else None,
                    "content_path": Path(t.content_path) if getattr(t, "content_path", None) else None,
                })
            return results

        except Exception as e:
            logger.error(f"Hiba a letöltések listázásakor: {str(e)}")
            return []

    def get_active_summary(self) -> Dict[str, Any]:
        """
        Gyors összefoglaló a tálcaikon tooltip és állapotkijelzés számára.
        """
        downloads = self.list_active_downloads()
        if not downloads:
            return {
                "active_count": 0,
                "download_speed_kb": 0.0,
                "summary": "Nincs aktív letöltés (Minden kész)",
                "tooltip": "Ncore Letöltő: Nincs aktív letöltés (Minden kész)",
            }

        active_items = [d for d in downloads if not d.get("is_completed")]
        total_speed = sum(d.get("download_speed_kb", 0) for d in downloads)

        if not active_items:
            return {
                "active_count": 0,
                "download_speed_kb": total_speed,
                "summary": f"{len(downloads)} letöltés kész (Seedelés folyamatban)",
                "tooltip": f"Ncore Letöltő: {len(downloads)} kész | Feltöltés: {round(total_speed / 1024, 1)} MB/s",
            }

        progress_strs = [f"{d['progress']}%" for d in active_items[:3]]
        speed_str = (
            f"{round(total_speed / 1024, 1)} MB/s"
            if total_speed >= 1024
            else f"{int(total_speed)} KB/s"
        )
        summary_txt = f"{len(active_items)} letöltés ({', '.join(progress_strs)}) | {speed_str}"
        tooltip_txt = f"🎬 Ncore Letöltő: {summary_txt}"

        return {
            "active_count": len(active_items),
            "download_speed_kb": total_speed,
            "summary": summary_txt,
            "tooltip": tooltip_txt,
        }

    def set_speed_limits(self, download_limit_kbs: int = 0, upload_limit_kbs: int = 0) -> bool:
        """
        Beállítja a qBittorrent globális letöltési és feltöltési sebességkorlátját KB/s-ban.
        0 vagy negatív érték esetén a korlátozás feloldódik (korlátlan / unlimited).
        """
        if not self._ensure_connected():
            return False
        try:
            dl_bytes = max(0, download_limit_kbs) * 1024
            ul_bytes = max(0, upload_limit_kbs) * 1024
            if hasattr(self.client, "transfer_set_download_limit"):
                self.client.transfer_set_download_limit(dl_bytes)
            if hasattr(self.client, "transfer_set_upload_limit"):
                self.client.transfer_set_upload_limit(ul_bytes)
            logger.info(f"qBittorrent sebességkorlát sikeresen beállítva: DL={download_limit_kbs} KB/s, UL={upload_limit_kbs} KB/s")
            return True
        except Exception as e:
            logger.debug(f"Hiba a qBittorrent sebességkorlát beállításakor: {e}")
            return False

    def get_speed_limits(self) -> Dict[str, Any]:
        """
        Lekéri a jelenleg érvényes globális letöltési és feltöltési korlátokat (KB/s).
        """
        if not self._ensure_connected():
            return {"dl_limit_kbs": 0, "ul_limit_kbs": 0, "dl_speed_kbs": 0, "ul_speed_kbs": 0}
        try:
            if hasattr(self.client, "transfer_info"):
                info = self.client.transfer_info()
                return {
                    "dl_limit_kbs": int(info.get("dl_rate_limit", 0)) // 1024,
                    "ul_limit_kbs": int(info.get("up_rate_limit", 0)) // 1024,
                    "dl_speed_kbs": int(info.get("dl_info_speed", 0)) // 1024,
                    "ul_speed_kbs": int(info.get("up_info_speed", 0)) // 1024,
                }
            return {"dl_limit_kbs": 0, "ul_limit_kbs": 0, "dl_speed_kbs": 0, "ul_speed_kbs": 0}
        except Exception as e:
            logger.debug(f"Hiba az átviteli információk lekérdezésekor: {e}")
            return {"dl_limit_kbs": 0, "ul_limit_kbs": 0, "dl_speed_kbs": 0, "ul_speed_kbs": 0}

