import os
import sys
from pathlib import Path
from typing import Optional
from loguru import logger


class AutostartManager:
    """
    Kezeli az alkalmazás automatikus indítását a Windows rendszerindításakor.
    Nem igényel rendszergazdai jogot, a felhasználó Windows Startup mappájába helyez el
    egy kisméretű .bat indítófájlt.
    """

    FILE_NAME = "Ncore_Media_Letolto_Autostart.bat"

    @classmethod
    def get_startup_file(cls) -> Path:
        """Visszaadja a Windows indítópultban lévő indítófájl elérési útját."""
        appdata = os.environ.get("APPDATA")
        if appdata:
            startup_dir = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        else:
            startup_dir = Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        return startup_dir / cls.FILE_NAME

    @classmethod
    def is_autostart_enabled(cls) -> bool:
        """Ellenőrzi, hogy be van-e kapcsolva az automatikus indítás."""
        return cls.get_startup_file().exists()

    @classmethod
    def set_autostart(cls, enable: bool, exe_path: Optional[Path] = None) -> bool:
        """
        Be- vagy kikapcsolja az automatikus indítást.
        Ha enable=True, létrehozza a startup bat fájlt.
        Ha enable=False, törli azt.
        """
        startup_file = cls.get_startup_file()

        if not enable:
            if startup_file.exists():
                try:
                    startup_file.unlink()
                    logger.info("Windows automatikus indítás kikapcsolva (indítófájl törölve).")
                    return True
                except Exception as e:
                    logger.error(f"Hiba az automatikus indítás törlésekor: {e}")
                    return False
            return True

        # Bekapcsolás
        try:
            if not exe_path:
                desktop_exe = Path.home() / "Desktop" / "Ncore_Media_Letolto.exe"
                if desktop_exe.exists():
                    exe_path = desktop_exe
                else:
                    exe_path = Path(__file__).resolve().parent.parent.parent / "dist" / "Ncore_Media_Letolto.exe"

            startup_file.parent.mkdir(parents=True, exist_ok=True)
            # /min indítás, hogy diszkréten a háttérben induljon el
            bat_content = f'@echo off\r\nstart "" /min "{str(exe_path)}"\r\n'
            startup_file.write_text(bat_content, encoding="utf-8")
            logger.info(f"Windows automatikus indítás bekapcsolva: {startup_file} -> {exe_path}")
            return True
        except Exception as e:
            logger.error(f"Hiba az automatikus indítás beállításakor: {e}")
            return False
