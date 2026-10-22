import os
import threading
import webbrowser
from pathlib import Path
from typing import Callable, Optional
from loguru import logger
from PIL import Image, ImageDraw

try:
    import pystray
except ImportError:
    pystray = None

from src.config.settings import get_settings


def create_tray_icon_image() -> Image.Image:
    """
    Készít egy esztétikus, modern tálcaikont (sötét kék alapon ragyogó ciánkék film-szimbólum).
    """
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Lekerekített sötétkék háttér (#0f172a)
    draw.rounded_rectangle(
        [(2, 2), (size - 3, size - 3)],
        radius=14,
        fill=(15, 23, 42, 255),
        outline=(56, 189, 248, 255),
        width=3,
    )

    # Filmcsapó / Lejátszás háromszög (#38bdf8 - ragyogó cián)
    # Csúcsok: bal felső, bal alsó, jobb középső
    points = [(22, 17), (22, 47), (47, 32)]
    draw.polygon(points, fill=(56, 189, 248, 255))

    # Kis dekoratív film-pontok a felső sávban
    dot_color = (148, 163, 184, 220)
    for x in [10, 16, 48, 54]:
        draw.ellipse([(x, 8), (x + 3, 11)], fill=dot_color)
        draw.ellipse([(x, 52), (x + 3, 55)], fill=dot_color)

    return img


class TrayService:
    """
    Windows Tálcaikon (System Tray) szolgáltatás.
    - Dupla kattintásra vagy menüből megnyitja a webes felületet
    - Ráhúzásra (Hover Tooltip) mutatja a letöltési folyamatot és sebességet
    - Jobb klikk menü a fontosabb műveletekhez
    - Windows értesítések küldése a letöltések befejezésekor
    """

    def __init__(
        self,
        web_url: str = "http://localhost:8501",
        on_exit: Optional[Callable[[], None]] = None,
        on_refresh_plex: Optional[Callable[[], bool]] = None,
    ):
        self.web_url = web_url
        self.on_exit = on_exit
        self.on_refresh_plex = on_refresh_plex
        self.icon: Optional["pystray.Icon"] = None
        self._thread: Optional[threading.Thread] = None

    def open_web(self, *args):
        """Megnyitja az alkalmazás felületét az alapértelmezett böngészőben."""
        try:
            webbrowser.open(self.web_url)
        except Exception as e:
            logger.error(f"Hiba a weboldal megnyitásakor: {e}")

    def open_qbittorrent(self, *args):
        """Megnyitja a qBittorrent WebUI-t böngészőben."""
        settings = get_settings()
        try:
            webbrowser.open(settings.QBITTORRENT_HOST)
        except Exception as e:
            logger.error(f"Hiba a qBittorrent megnyitásakor: {e}")

    def open_plex(self, *args):
        """Megnyitja a Plex szerver felületét."""
        settings = get_settings()
        try:
            webbrowser.open(settings.PLEX_SERVER_URL)
        except Exception as e:
            logger.error(f"Hiba a Plex megnyitásakor: {e}")

    def open_folder(self, path: Path):
        """Megnyit egy mappát a Windows Fájlkezelőben."""
        try:
            if path.exists():
                os.startfile(str(path))
            else:
                logger.warning(f"A mappa még nem létezik: {path}")
        except Exception as e:
            logger.error(f"Hiba a mappa megnyitásakor ({path}): {e}")

    def trigger_plex_refresh(self, *args):
        """Frissíti a Plex könyvtárat a tálcáról és értesítést ad."""
        success = False
        if self.on_refresh_plex:
            try:
                success = bool(self.on_refresh_plex())
            except Exception as e:
                logger.error(f"Hiba a Plex frissítésekor: {e}")

        if success:
            self.notify("Plex Médiaszerver", "A Plex könyvtárak frissítése sikeresen elindult!")
        else:
            self.notify("Plex Médiaszerver", "A Plex szerver nem válaszolt. Ellenőrizd a beállításokat!")

    def exit_app(self, *args):
        """Leállítja a tálcaikont és az alkalmazást."""
        logger.info("Kilépés kezdeményezve a tálcaikonból...")
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass

        if self.on_exit:
            try:
                self.on_exit()
            except Exception as e:
                logger.error(f"Hiba a kilépési callback futtatásakor: {e}")

    def update_tooltip(self, text: str):
        """
        Frissíti az ikon felugró szövegét (hover tooltip).
        A Windows maximum 127 karaktert jelenít meg a tooltipben.
        """
        if not self.icon:
            return
        try:
            clean_text = text.strip()[:127]
            self.icon.title = clean_text
        except Exception as e:
            logger.debug(f"Nem sikerült a tooltip frissítése: {e}")

    def notify(self, title: str, message: str):
        """
        Natív Windows Toast értesítést jelenít meg a tálca felett.
        """
        if not self.icon:
            return
        try:
            self.icon.notify(message, title)
        except Exception as e:
            logger.debug(f"Nem sikerült az értesítés megjelenítése: {e}")

    def _build_menu(self) -> "pystray.Menu":
        settings = get_settings()

        return pystray.Menu(
            pystray.MenuItem(
                "🌐 Webes felület megnyitása",
                self.open_web,
                default=True,  # Duplakattintásra ez fut le
            ),
            pystray.MenuItem(
                "📥 qBittorrent megnyitása",
                self.open_qbittorrent,
            ),
            pystray.MenuItem(
                "🍿 Plex Web megnyitása",
                self.open_plex,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "📁 Mappák megnyitása",
                pystray.Menu(
                    pystray.MenuItem(
                        "📥 Letöltési mappa",
                        lambda: self.open_folder(settings.DOWNLOAD_TEMP_DIR),
                    ),
                    pystray.MenuItem(
                        "🎬 Plex Filmek mappa",
                        lambda: self.open_folder(settings.PLEX_MOVIES_DIR),
                    ),
                    pystray.MenuItem(
                        "📺 Plex Sorozatok mappa",
                        lambda: self.open_folder(settings.PLEX_SERIES_DIR),
                    ),
                ),
            ),
            pystray.MenuItem(
                "🔄 Plex könyvtár frissítése",
                self.trigger_plex_refresh,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "❌ Kilépés és Leállítás",
                self.exit_app,
            ),
        )

    def start(self, blocking: bool = False):
        """
        Elindítja a tálcaikont. Ha blocking=False, külön szálon fut.
        """
        if pystray is None:
            logger.warning("A 'pystray' nincs telepítve, a tálcaikon nem indul el.")
            return

        icon_image = create_tray_icon_image()
        initial_title = "Ncore Média Letöltő (Kattints a megnyitáshoz)"
        menu = self._build_menu()

        self.icon = pystray.Icon(
            name="ncore_media_downloader",
            icon=icon_image,
            title=initial_title,
            menu=menu,
        )

        if blocking:
            self.icon.run()
        else:
            self._thread = threading.Thread(target=self.icon.run, daemon=True)
            self._thread.start()
            logger.info("Tálcaikon sikeresen elindult a háttérben.")

    def stop(self):
        """Leállítja a tálcaikont."""
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass
            self.icon = None
