import threading
import time
from typing import Optional, Set
from loguru import logger

from src.core.orchestrator import Orchestrator
from src.services.tray_service import TrayService


class BackgroundWorker:
    """
    Önálló Háttérfolyamat (Background Worker Daemon).
    
    Feladatai:
    1. Folyamatosan (15-20 másodpercenként) ellenőrzi a letöltések állapotát,
       még akkor is, ha a böngésző be van zárva!
    2. Befejezett letöltéseknél azonnal elvégzi a Plexbe rendezést (Hardlinkkel)
       és frissíti a Plex könyvtárat.
    3. Windows Toast értesítést küld jobb alul a tálcán, ha egy új film/rész elkészült.
    4. Frissíti a tálcaikon élő állapotát (hover tooltip - sebesség, folyamat).
    """

    def __init__(
        self,
        orchestrator: Optional[Orchestrator] = None,
        tray_service: Optional[TrayService] = None,
        interval_seconds: int = 15,
    ):
        self.orchestrator = orchestrator or Orchestrator()
        self.tray_service = tray_service
        self.interval = interval_seconds
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._known_organized_tasks: Set[str] = set()

        # Kezdetben már rendezett feladatok feljegyzése, hogy ne küldjön újra értesítést a régiekre
        for t_id, task in self.orchestrator._tasks.items():
            if task.get("plex_organized", False):
                self._known_organized_tasks.add(t_id)

    def _run_loop(self):
        logger.info(f"Háttérfolyamat (Background Worker) elindult. Ciklusidő: {self.interval}s")
        # Első gyors állapotfrissítés 2 másodperc után
        time.sleep(2)

        while not self._stop_event.is_set():
            try:
                # 1. Letöltési feladatok és HnR felügyelete, Plexbe rendezés
                self.orchestrator.check_and_process_tasks()

                # 2. Újonnan Plexbe került feladatok detektálása és értesítés küldése
                for t_id, task in self.orchestrator._tasks.items():
                    if task.get("plex_organized", False) and t_id not in self._known_organized_tasks:
                        self._known_organized_tasks.add(t_id)
                        m_item = task.get("media_item", {})
                        title = m_item.get("title_hu") or m_item.get("title_en") or "Média"
                        year = f" ({m_item.get('year')})" if m_item.get("year") else ""
                        msg = f"'{title}{year}' sikeresen letöltődött és elérhető a TV-n a Plexben!"
                        logger.info(f"Értesítés küldése kész médiafájlról: {msg}")

                        if self.tray_service:
                            self.tray_service.notify(
                                title="🎉 Film letöltve és Plexbe rendezve!",
                                message=msg,
                            )

                # 3. Tálcaikon tooltip frissítése valós idejű adatokkal
                if self.tray_service:
                    summary = self.orchestrator.torrent_service.get_active_summary()
                    tooltip = summary.get("tooltip", "Ncore Letöltő")
                    self.tray_service.update_tooltip(tooltip)

            except Exception as e:
                logger.error(f"Hiba a háttérfolyamat ciklusában: {e}")

            # Várakozás a következő ciklusig vagy a leállításig
            self._stop_event.wait(timeout=self.interval)

        logger.info("Háttérfolyamat (Background Worker) leállt.")

    def start(self):
        """Elindítja a háttérszálat daemon módban."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="BackgroundWorker", daemon=True)
        self._thread.start()

    def stop(self):
        """Kéri a háttérszál leállítását."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
