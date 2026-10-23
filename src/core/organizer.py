import os
import re
import shutil
from pathlib import Path
from typing import List, Optional
from loguru import logger

from src.config.settings import get_settings
from src.models.media import MediaItem, MediaType, TorrentRelease


class MediaOrganizer:
    """
    Fájlrendező és Plex-kompatibilis átnevező motor.
    
    Fő funkciók:
    1. A letöltött filmek és sorozatok átnevezése és rendszerezése a Plex hivatalos ajánlása szerint.
    2. Hardlink technológia használata (nem foglal dupla helyet a lemezen, és a torrent kliens
       továbbra is zavartalanul tudja seedelni az eredeti fájlt az nCore HnR szabály betartásához).
    3. Nem kívánt szemetek (mintafájlok, .nfo, .txt, .url) kiszűrése.
    """

    VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv"}
    SUBTITLE_EXTENSIONS = {".srt", ".sub"}

    def __init__(self):
        self.settings = get_settings()

    def organize_media(
        self,
        source_path: Path,
        media_item: MediaItem,
        release: Optional[TorrentRelease] = None,
        attached_subtitles: Optional[List[Path]] = None,
    ) -> List[Path]:
        """
        Rendszerezi a letöltött fájlokat a megfelelő Plex mappába.
        Visszaadja a sikeresen elhelyezett Plex médiafájlok listáját.
        """
        if not source_path.exists():
            logger.error(f"A forrás mappa vagy fájl nem létezik: {source_path}")
            return []

        logger.info(f"Média rendszerezésének megkezdése: '{media_item.title_hu}' ({source_path})")

        if media_item.media_type == MediaType.MOVIE:
            return self._organize_movie(source_path, media_item, release, attached_subtitles)
        else:
            return self._organize_series(source_path, media_item, release, attached_subtitles)

    def _organize_movie(
        self,
        source_path: Path,
        media_item: MediaItem,
        release: Optional[TorrentRelease] = None,
        attached_subtitles: Optional[List[Path]] = None,
    ) -> List[Path]:
        """
        Filmek rendezése:
        PLEX_MOVIES_DIR / {Cím} ({Év}) / {Cím} ({Év}) [{Minőség}].{kiterjesztés}
        """
        year_str = f" ({media_item.year})" if media_item.year else ""
        clean_title = self._sanitize_filename(media_item.title_hu)
        folder_name = f"{clean_title}{year_str}"
        target_dir = self.settings.PLEX_MOVIES_DIR / folder_name
        target_dir.mkdir(parents=True, exist_ok=True)

        video_files = self._find_video_files(source_path)
        if not video_files:
            logger.warning(f"Nem található videófájl a letöltésben: {source_path}")
            return []

        # A legnagyobb videófájl a fő film
        main_video = max(video_files, key=lambda f: f.stat().st_size)
        quality_tag = self._extract_quality_tag(release.title if release else main_video.stem)
        dest_filename = f"{clean_title}{year_str} [{quality_tag}]{main_video.suffix}"
        dest_video_path = target_dir / dest_filename

        placed_files = []
        if self._place_file(main_video, dest_video_path):
            placed_files.append(dest_video_path)

        # Csatolt külső feliratok mozgatása
        if attached_subtitles:
            for sub_file in attached_subtitles:
                if sub_file.exists():
                    dest_sub = target_dir / f"{clean_title}{year_str}.hu.srt"
                    if self._place_file(sub_file, dest_sub):
                        placed_files.append(dest_sub)

        # Letöltésben lévő beágyazott feliratok keresése
        for sub_file in self._find_subtitle_files(source_path):
            dest_sub = target_dir / f"{clean_title}{year_str}.hu.srt"
            if not dest_sub.exists() and self._place_file(sub_file, dest_sub):
                placed_files.append(dest_sub)

        logger.info(f"Film sikeresen elhelyezve a Plex mappában: {target_dir}")
        return placed_files

    def _organize_series(
        self,
        source_path: Path,
        media_item: MediaItem,
        release: Optional[TorrentRelease] = None,
        attached_subtitles: Optional[List[Path]] = None,
    ) -> List[Path]:
        """
        Sorozatok rendezése:
        PLEX_SERIES_DIR / {Sorozat Cím} / Season {Évad:02d} / {Sorozat Cím} - S{Évad:02d}E{Epizód:02d}.{kiterjesztés}
        """
        clean_show_title = self._sanitize_filename(media_item.title_hu)
        series_dir = self.settings.PLEX_SERIES_DIR / clean_show_title
        series_dir.mkdir(parents=True, exist_ok=True)

        video_files = self._find_video_files(source_path)
        placed_files = []

        for video in video_files:
            season, episode = self._extract_season_episode(video.name)
            if not season and release and release.season_num and release.season_num > 0:
                season = release.season_num
            if not season:
                parent_s, _ = self._extract_season_episode(video.parent.name)
                if parent_s:
                    season = parent_s

            season_num = season or 1
            season_folder = series_dir / f"Season {season_num:02d}"
            season_folder.mkdir(parents=True, exist_ok=True)

            if episode:
                ep_tag = f"S{season_num:02d}E{episode:02d}"
            else:
                ep_tag = f"S{season_num:02d}E01"

            dest_filename = f"{clean_show_title} - {ep_tag}{video.suffix}"
            dest_video_path = season_folder / dest_filename

            if self._place_file(video, dest_video_path):
                placed_files.append(dest_video_path)

        logger.info(f"Sorozat epizódok elhelyezve a Plex mappában: {series_dir}")
        return placed_files

    def _place_file(self, src: Path, dst: Path) -> bool:
        """
        Fájl elhelyezése:
        1. Elsődlegesen Hardlink (os.link) készítése: Nem foglal dupla lemezterületet,
           és a torrent kliens tovább tud seedelni az nCore HnR szabály teljesüléséig.
        2. Ha a hardlink nem támogatott (pl. más partíción van a Plex mappa), másolás (copy2).
        """
        try:
            if dst.exists():
                logger.debug(f"A célfájl már létezik a Plex mappában, felülírás kihagyva: {dst.name}")
                return True

            try:
                # 1. Hardlink kísérlet
                os.link(src, dst)
                logger.info(f"Hardlink létrehozva: {src.name} -> {dst}")
                return True
            except (OSError, NotImplementedError):
                # 2. Ha más meghajtón van a Plex mappa, másolás
                logger.info(f"Hardlink nem lehetséges (más partíció), fájl másolása: {src.name} -> {dst}")
                shutil.copy2(src, dst)
                return True

        except Exception as e:
            logger.error(f"Hiba a fájl elhelyezésekor ({src} -> {dst}): {str(e)}")
            return False

    def _is_file_ready(self, file_path: Path) -> bool:
        """
        Biztonsági ellenőrzés a fájl mozgatása előtt:
        1. Nem befejezetlen kiterjesztés (.!qB, .part, .downloading)?
        2. Nem üres fájl (méret > 0)?
        3. Nincs írási zárolás alatt egy másik folyamat (pl. még író torrentkliens) által?
        """
        try:
            if not file_path.exists() or not file_path.is_file():
                return False
            name_lower = file_path.name.lower()
            if any(name_lower.endswith(ext) for ext in [".!qb", ".part", ".tmp", ".downloading", ".crdownload"]):
                logger.debug(f"A fájl még letöltés alatt áll (befejezetlen kiterjesztés): {file_path.name}")
                return False

            if file_path.stat().st_size == 0:
                return False

            # Tesztnyitás bináris olvasásra a zároltság teszteléséhez
            with open(file_path, "rb") as test_f:
                test_f.read(1024)
            return True
        except (IOError, PermissionError) as lock_err:
            logger.warning(f"A fájl még írási zárolás alatt van: {file_path.name} ({lock_err})")
            return False
        except Exception:
            return False

    def cleanup_old_temp_files(self, days: int = 7) -> int:
        """
        Kitakarítja a letöltési munkamappából a 'days' napnál régebbi
        segédfájlokat (pl. régi .torrent állományok). Visszaadja a törölt fájlok számát.
        """
        import time
        deleted_count = 0
        cutoff_time = time.time() - (days * 86400)
        temp_torrents_dir = self.settings.DOWNLOAD_TEMP_DIR / "_torrents"
        if not temp_torrents_dir.exists():
            return 0

        try:
            for f in temp_torrents_dir.glob("*.torrent"):
                if f.is_file() and f.stat().st_mtime < cutoff_time:
                    try:
                        f.unlink()
                        deleted_count += 1
                    except Exception:
                        pass
            if deleted_count > 0:
                logger.info(f"Ideiglenes munkamappa karbantartva: {deleted_count} db régi segédfájl törölve.")
        except Exception as e:
            logger.debug(f"Hiba az ideiglenes fájlok takarításakor: {e}")

        return deleted_count

    def _find_video_files(self, path: Path) -> List[Path]:
        """
        Megkeresi a valódi média videófájlokat, kiszűrve a mintafájlokat (samples) és a zárolt állományokat.
        """
        if path.is_file():
            if self._is_file_ready(path) and path.suffix.lower() in self.VIDEO_EXTENSIONS:
                return [path]
            return []

        videos = []
        for file in path.rglob("*"):
            if file.is_file() and file.suffix.lower() in self.VIDEO_EXTENSIONS:
                if not self._is_file_ready(file):
                    continue
                # Mintafájlok kiszűrése (pl. sample.mkv vagy 150 MB alatti minták)
                name_lower = file.name.lower()
                size_mb = file.stat().st_size / (1024 * 1024)
                if ("sample" in name_lower or "proof" in name_lower) and size_mb < 200:
                    continue
                videos.append(file)
        return videos

    def _find_subtitle_files(self, path: Path) -> List[Path]:
        if path.is_file():
            return [path] if path.suffix.lower() in self.SUBTITLE_EXTENSIONS else []
        return [f for f in path.rglob("*") if f.is_file() and f.suffix.lower() in self.SUBTITLE_EXTENSIONS]

    @staticmethod
    def _extract_season_episode(filename: str):
        """
        Kinyeri az évad és epizód számot a fájlnévből (pl. S01E04 vagy 1x04 vagy 01.mkv).
        """
        m = re.search(r"[sS](\d{1,2})[eE](\d{1,2})", filename)
        if m:
            return int(m.group(1)), int(m.group(2))

        m = re.search(r"(\d{1,2})x(\d{1,2})", filename)
        if m:
            return int(m.group(1)), int(m.group(2))

        m_s = re.search(r"\b[sS](\d{1,2})\b", filename)
        season = int(m_s.group(1)) if m_s else None

        m_e = re.search(r"\b[eE](\d{1,2})\b", filename)
        episode = int(m_e.group(1)) if m_e else None

        if not episode:
            m_num = re.search(r"(?:^|[^\d])(\d{1,2})\.(?:mkv|mp4|avi)", filename, re.IGNORECASE)
            if m_num:
                episode = int(m_num.group(1))

        return season, episode

    @staticmethod
    def _extract_quality_tag(title: str) -> str:
        """
        Minőségi címke kinyerése a címből a szép elnevezéshez (pl. 1080p BluRay).
        """
        tags = []
        title_upper = title.upper()
        if "2160P" in title_upper or "4K" in title_upper:
            tags.append("4K")
        elif "1080P" in title_upper:
            tags.append("1080p")
        elif "720P" in title_upper:
            tags.append("720p")

        if "BLURAY" in title_upper or "REMUX" in title_upper:
            tags.append("BluRay")
        elif "WEB-DL" in title_upper or "WEBRIP" in title_upper:
            tags.append("WEB-DL")

        return " ".join(tags) if tags else "HD"

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        """Eltávolítja az operációs rendszer számára tiltott karaktereket."""
        return re.sub(r'[\\/*?:"<>|]', "", name).strip()
