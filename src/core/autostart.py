import os
import sys
from pathlib import Path
from typing import Optional
from loguru import logger


class AutostartManager:
    """
    Kezeli az alkalmazás automatikus indítását a Windows rendszerindításakor.
    Nem igényel rendszergazdai jogot, a felhasználó Windows Startup mappájába helyez el
    egy kisméretű .bat indítófájlt, miközben felismeri és kezeli a telepítő által létrehozott .lnk fájlt is.
    """

    FILE_NAME = "Ncore_Media_Letolto_Autostart.bat"
    LNK_FILE_NAME = "Ncore_Media_Letolto.lnk"

    @classmethod
    def get_startup_dir(cls) -> Path:
        """Visszaadja a Windows indítópult könyvtár elérési útját."""
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        return Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"

    @classmethod
    def get_startup_file(cls) -> Path:
        """Visszaadja a Windows indítópultban lévő indítófájl elérési útját."""
        return cls.get_startup_dir() / cls.FILE_NAME

    @classmethod
    def is_autostart_enabled(cls) -> bool:
        """Ellenőrzi, hogy be van-e kapcsolva az automatikus indítás (.bat vagy .lnk létezik)."""
        startup_file = cls.get_startup_file()
        startup_dir = startup_file.parent
        lnk_file = startup_dir / cls.LNK_FILE_NAME
        return startup_file.exists() or lnk_file.exists()

    @classmethod
    def set_autostart(cls, enable: bool, exe_path: Optional[Path] = None) -> bool:
        """
        Be- vagy kikapcsolja az automatikus indítást.
        Ha enable=True, létrehozza a startup bat fájlt és kitakarítja a duplikációkat.
        Ha enable=False, törli a bat és lnk indítófájlokat is.
        """
        startup_file = cls.get_startup_file()
        startup_dir = startup_file.parent
        lnk_file = startup_dir / cls.LNK_FILE_NAME

        if not enable:
            success = True
            for f in [startup_file, lnk_file]:
                if f.exists():
                    try:
                        f.unlink()
                        logger.info(f"Windows automatikus indítás kikapcsolva ({f.name} törölve).")
                    except Exception as e:
                        logger.error(f"Hiba az automatikus indítás törlésekor ({f.name}): {e}")
                        success = False
            return success

        # Bekapcsolás
        try:
            if lnk_file.exists():
                try:
                    lnk_file.unlink()
                except Exception:
                    pass

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
