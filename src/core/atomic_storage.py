import json
import os
import shutil
from pathlib import Path
from typing import Any, Callable
from loguru import logger


def atomic_save_json(file_path: Path, data: Any, create_backup: bool = True) -> bool:
    """
    Biztonságos, atomikus JSON mentés.
    
    1. Ideiglenes fájlba (.tmp) írja az adatot.
    2. Ha az eredeti fájl létezik és nem üres, automatikus biztonsági másolatot készít (.bak).
    3. Az os.replace atomi művelettel lecseréli a fájlt, így egy váratlan leállás vagy
       áramkimaradás esetén sem sérülhet meg az adatállomány.
    """
    try:
        file_path = Path(file_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = file_path.with_suffix(file_path.suffix + ".tmp")
        bak_path = file_path.with_suffix(file_path.suffix + ".bak")

        json_text = json.dumps(data, indent=2, ensure_ascii=False)
        tmp_path.write_text(json_text, encoding="utf-8")

        # Biztonsági másolat készítése az előző állapotról
        if create_backup and file_path.exists() and file_path.stat().st_size > 0:
            try:
                shutil.copy2(file_path, bak_path)
            except Exception as bak_err:
                logger.debug(f"Nem sikerült a .bak mentés ({file_path.name}): {bak_err}")

        # Atomi fájlcsere (NTFS és POSIX rendszereken is atomikus)
        os.replace(tmp_path, file_path)
        return True

    except Exception as e:
        logger.error(f"Hiba az atomikus JSON mentés során ({file_path}): {e}")
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass
        return False


def load_json_with_recovery(file_path: Path, default_factory: Callable[[], Any] = list) -> Any:
    """
    JSON betöltése automatikus .bak visszaállítással és hibatűréssel.
    
    Ha a fő fájl sérült vagy üres (pl. egy korábbi váratlan gépkikapcsolás miatt),
    megkísérli a .bak fájlból való visszaállítást, megelőzve az adatvesztést.
    """
    file_path = Path(file_path)
    bak_path = file_path.with_suffix(file_path.suffix + ".bak")

    # 1. Próbálkozás a fő fájllal
    if file_path.exists():
        try:
            content = file_path.read_text(encoding="utf-8").strip()
            if content:
                return json.loads(content)
        except Exception as e:
            logger.warning(f"Sérült JSON fájl észlelve: {file_path.name} ({e}). Próbálkozás a .bak visszaállítással...")

    # 2. Visszaállítás a biztonsági mentésből (.bak)
    if bak_path.exists():
        try:
            bak_content = bak_path.read_text(encoding="utf-8").strip()
            if bak_content:
                data = json.loads(bak_content)
                logger.info(f"Sikeres visszaállítás a biztonsági mentésből: {bak_path.name} -> {file_path.name}")
                # Helyreállítjuk a fő fájlt is
                atomic_save_json(file_path, data, create_backup=False)
                return data
        except Exception as bak_err:
            logger.error(f"A .bak biztonsági mentés is sérült ({bak_path.name}): {bak_err}")

    # 3. Alapértelmezett érték inicializálása
    default_val = default_factory()
    try:
        atomic_save_json(file_path, default_val, create_backup=False)
    except Exception:
        pass
    return default_val
