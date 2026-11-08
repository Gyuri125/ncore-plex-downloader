import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
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
    4. Haladáskövetés (% kész, MB/s sebesség, ETA hátralévő idő) lemezek közötti másolás esetén.
    """

    VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv"}
    SUBTITLE_EXTENSIONS = {".srt", ".sub"}

    def __init__(self):
        self.settings = get_settings()
        self.last_placement_hardlink: bool = True
        self.current_transfer: Optional[Dict[str, Any]] = None
        self.last_placement_info: Optional[Dict[str, Any]] = None
        self.last_has_embedded_hungarian_sub: bool = False

    @staticmethod
    def check_mkv_embedded_hungarian_sub(file_path: Path) -> bool:
        """
        Megvizsgálja a .mkv fájl fejlécét (első 4 MB), hogy tartalmaz-e beágyazott magyar feliratsávot
        (TrackType 17 / S_TEXT / S_HDMV / S_VOBSUB nyelv: hun / hu).
        Külső függőség és ffprobe nélkül, tiszta bináris fejlécelemzéssel működik.
        """
        if not file_path or not file_path.exists() or file_path.suffix.lower() != ".mkv":
            return False

        try:
            read_size = min(file_path.stat().st_size, 4 * 1024 * 1024)
            with open(file_path, "rb") as f:
                header = f.read(read_size)

            if not header.startswith(b"\x1a\x45\xdf\xa3"):
                return False

            tracks_pos = header.find(b"\x16\x54\xae\x6b")
            search_block = header[tracks_pos:] if tracks_pos != -1 else header

            sub_codecs = [b"S_TEXT/UTF8", b"S_TEXT/ASS", b"S_TEXT/SSA", b"S_HDMV/PGS", b"S_VOBSUB"]
            has_sub_codec = any(c in search_block for c in sub_codecs)

            hun_lang_tags = [
                b"\x22\xb5\x9c\x83hun",
                b"\x22\xb5\x9c\x82hu",
                b"\x22\xb5\x9c\x03hun",
                b"\x22\xb5\x9c\x02hu",
                b"\x22\xb5\x9dhun",
                b"\x22\xb5\x9dhu",
            ]
            has_hun_tag = any(tag in search_block for tag in hun_lang_tags)

            name_patterns = [rb"(?i)magyar\s*(felirat|sub)?", rb"(?i)hungarian\s*(sub|forced)?"]
            has_name_match = any(re.search(pat, search_block) for pat in name_patterns)

            if has_sub_codec and (has_hun_tag or has_name_match):
                return True

            if re.search(rb"\x83[\x01\x11][\s\S]{0,150}(hun|magyar)", search_block, re.IGNORECASE):
                return True

            return False
        except Exception as e:
            logger.debug(f"Hiba a beágyazott MKV felirat vizsgálatakor: {e}")
            return False

    def get_current_transfer(self) -> Optional[Dict[str, Any]]:
        """Visszaadja a jelenleg futó lemezek közötti másolási feladat adatait (százalék, sebesség, ETA)."""
        return self.current_transfer

    def get_last_placement_info(self) -> Optional[Dict[str, Any]]:
        """Visszaadja a legutóbbi sikeres elhelyezés adatait (célmappa, fájlok, hardlink/másolás)."""
        return self.last_placement_info

    def organize_media(
        self,
        source_path: Path,
        media_item: MediaItem,
        release: Optional[TorrentRelease] = None,
        attached_subtitles: Optional[List[Path]] = None,
        callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> List[Path]:
        """
        Rendszerezi a letöltött fájlokat a megfelelő Plex mappába.
        Visszaadja a sikeresen elhelyezett Plex médiafájlok listáját.
        """
        self.last_placement_hardlink = True
        self.last_has_embedded_hungarian_sub = False
        if not source_path.exists():
            logger.error(f"A forrás mappa vagy fájl nem létezik: {source_path}")
            return []

        logger.info(f"Média rendszerezésének megkezdése: '{media_item.title_hu}' ({source_path})")

        placed = []
        if media_item.media_type == MediaType.MOVIE:
            placed = self._organize_movie(source_path, media_item, release, attached_subtitles, callback=callback)
        else:
            placed = self._organize_series(source_path, media_item, release, attached_subtitles, callback=callback)

        if placed:
            has_embedded = any(f.suffix.lower() == ".mkv" and self.check_mkv_embedded_hungarian_sub(f) for f in placed)
            self.last_has_embedded_hungarian_sub = has_embedded
            target_dir = placed[0].parent
            self.last_placement_info = {
                "status": "completed",
                "destination_folder": str(target_dir),
                "destination_files": [f.name for f in placed],
                "is_hardlink": self.last_placement_hardlink,
                "has_embedded_hungarian_sub": has_embedded,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "count": len(placed),
            }
        return placed

    def _organize_movie(
        self,
        source_path: Path,
        media_item: MediaItem,
        release: Optional[TorrentRelease] = None,
        attached_subtitles: Optional[List[Path]] = None,
        callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> List[Path]:
        """
        Filmek rendezése:
        PLEX_MOVIES_DIR / {Cím} ({Év}) / {Cím} ({Év}) [{Minőség}].{kiterjesztés}
        """
        year_str = f" ({media_item.year})" if media_item.year else ""
        clean_title = self._sanitize_filename(media_item.title_hu or media_item.title_en or "Movie")
        folder_name = f"{clean_title}{year_str}"
        target_dir = self.settings.PLEX_MOVIES_DIR / folder_name
        target_dir.mkdir(parents=True, exist_ok=True)

        video_files = self._find_video_files(source_path)
        if not video_files:
            logger.warning(f"Nem található videófájl a letöltésben: {source_path}")
            return []

        # Ellenőrizzük, hogy több-lemezes filmről van-e szó (pl. CD1 és CD2, vagy Part 1 és Part 2)
        part_pattern = re.compile(r"\b(cd|part|pt|disc)[\s._\-]*([1-9])\b", re.IGNORECASE)
        has_multi_parts = False
        if len(video_files) > 1:
            part_matches = [part_pattern.search(v.name) for v in video_files]
            if all(m is not None for m in part_matches):
                has_multi_parts = True
            elif all(v.stat().st_size > 250 * 1024 * 1024 for v in video_files) and len(video_files) in (2, 3):
                sizes = [v.stat().st_size for v in video_files]
                if min(sizes) / max(sizes) > 0.5:
                    has_multi_parts = True

        placed_files = []
        if has_multi_parts:
            quality_tag = self._extract_quality_tag(release.title if release else video_files[0].stem)
            for idx, vid in enumerate(sorted(video_files), start=1):
                dest_filename = f"{clean_title}{year_str} [{quality_tag}] - pt{idx}{vid.suffix}"
                dest_video_path = target_dir / dest_filename
                if self._place_file(vid, dest_video_path, callback=callback):
                    placed_files.append(dest_video_path)
            video_stem = f"{clean_title}{year_str} [{quality_tag}] - pt1"
        else:
            main_video = max(video_files, key=lambda f: f.stat().st_size)
            quality_tag = self._extract_quality_tag(release.title if release else main_video.stem)
            dest_filename = f"{clean_title}{year_str} [{quality_tag}]{main_video.suffix}"
            dest_video_path = target_dir / dest_filename
            if self._place_file(main_video, dest_video_path, callback=callback):
                placed_files.append(dest_video_path)
            video_stem = dest_video_path.stem

        # Csatolt külső feliratok mozgatása
        # Plex és okostévék számára a videó nevével megegyező stem szükséges: {video_stem}.hu.srt,
        # miközben a rövidített változatot {clean_title}{year_str}.hu.srt is biztosítjuk a maximális kompatibilitásért.
        if attached_subtitles:
            for sub_file in attached_subtitles:
                if sub_file.exists():
                    dest_sub = target_dir / f"{video_stem}.hu.srt"
                    if self._place_file(sub_file, dest_sub, callback=callback):
                        placed_files.append(dest_sub)
                    short_sub = target_dir / f"{clean_title}{year_str}.hu.srt"
                    if short_sub != dest_sub and not short_sub.exists():
                        self._place_file(sub_file, short_sub, callback=callback)

        # Letöltésben lévő beágyazott feliratok keresése
        source_subs = self._find_subtitle_files(source_path)
        if has_multi_parts and len(source_subs) == len(video_files):
            for idx, sub_file in enumerate(sorted(source_subs), start=1):
                pt_sub = target_dir / f"{clean_title}{year_str} [{quality_tag}] - pt{idx}.hu.srt"
                if not pt_sub.exists() and self._place_file(sub_file, pt_sub, callback=callback):
                    placed_files.append(pt_sub)
        else:
            for sub_file in source_subs:
                dest_sub = target_dir / f"{video_stem}.hu.srt"
                if not dest_sub.exists() and self._place_file(sub_file, dest_sub, callback=callback):
                    placed_files.append(dest_sub)
                short_sub = target_dir / f"{clean_title}{year_str}.hu.srt"
                if short_sub != dest_sub and not short_sub.exists():
                    self._place_file(sub_file, short_sub, callback=callback)

        logger.info(f"Film sikeresen elhelyezve a Plex mappában: {target_dir}")
        return placed_files

    def _organize_series(
        self,
        source_path: Path,
        media_item: MediaItem,
        release: Optional[TorrentRelease] = None,
        attached_subtitles: Optional[List[Path]] = None,
        callback: Optional[Callable[[Dict[str, Any]], None]] = None,
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

        for idx, video in enumerate(sorted(video_files), start=1):
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

            ep_num = episode if episode is not None else idx
            ep_tag = f"S{season_num:02d}E{ep_num:02d}"

            dest_filename = f"{clean_show_title} - {ep_tag}{video.suffix}"
            dest_video_path = season_folder / dest_filename

            if self._place_file(video, dest_video_path, callback=callback):
                placed_files.append(dest_video_path)

        # Sorozat feliratok kezelése (csatolt feliratok és a forrásban lévő feliratfájlok)
        all_subs: List[Path] = []
        if attached_subtitles:
            all_subs.extend([s for s in attached_subtitles if s.exists()])
        for s in self._find_subtitle_files(source_path):
            if s not in all_subs:
                all_subs.append(s)

        for sub_file in all_subs:
            sub_s, sub_e = self._extract_season_episode(sub_file.name)
            if not sub_s and release and release.season_num and release.season_num > 0:
                sub_s = release.season_num
            target_season = sub_s or 1
            season_folder = series_dir / f"Season {target_season:02d}"
            season_folder.mkdir(parents=True, exist_ok=True)

            if sub_e:
                dest_sub_name = f"{clean_show_title} - S{target_season:02d}E{sub_e:02d}.hu.srt"
            elif len(video_files) == 1:
                # Egyetlen epizód esetén hozzákötjük a videó nevéhez
                single_s, single_e = self._extract_season_episode(video_files[0].name)
                s_tag = single_s or target_season
                e_tag = single_e or 1
                dest_sub_name = f"{clean_show_title} - S{s_tag:02d}E{e_tag:02d}.hu.srt"
            else:
                dest_sub_name = f"{self._sanitize_filename(sub_file.stem)}.hu.srt"

            dest_sub_path = season_folder / dest_sub_name
            if not dest_sub_path.exists() and self._place_file(sub_file, dest_sub_path):
                placed_files.append(dest_sub_path)

        logger.info(f"Sorozat epizódok és feliratok elhelyezve a Plex mappában: {series_dir}")
        return placed_files

    def _copy_with_progress(
        self,
        src: Path,
        dst: Path,
        chunk_size: int = 4 * 1024 * 1024,
        callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> bool:
        """
        Biztonságos chunk-alapú másolás valós idejű sebesség-, százalék- és ETA-számítással.
        Lehetővé teszi, hogy a felület folyamatosan visszajelzést kapjon a másolás haladásáról
        amikor a Plex mappa másik merevlemezen található mint az ideiglenes letöltési mappa.
        """
        try:
            total_size = src.stat().st_size
            copied = 0
            start_time = time.time()
            last_update_time = 0.0

            # Kezdeti állapot
            size_h = f"{total_size / (1024**3):.2f} GB" if total_size >= 1024**3 else f"{total_size / (1024**2):.1f} MB"
            self.current_transfer = {
                "active": True,
                "filename": dst.name,
                "source": str(src),
                "destination": str(dst),
                "destination_folder": str(dst.parent),
                "total_bytes": total_size,
                "copied_bytes": 0,
                "copied_human": "0 MB",
                "total_human": size_h,
                "percent": 0.0,
                "speed_mbps": 0.0,
                "eta_seconds": 0,
                "eta_str": "Számítás alatt...",
                "is_hardlink": False,
            }

            with open(src, "rb") as f_src, open(dst, "wb") as f_dst:
                while True:
                    chunk = f_src.read(chunk_size)
                    if not chunk:
                        break
                    f_dst.write(chunk)
                    copied += len(chunk)

                    now = time.time()
                    if now - last_update_time >= 0.25 or copied == total_size:
                        last_update_time = now
                        elapsed = max(0.001, now - start_time)
                        speed_bytes = copied / elapsed
                        speed_mbps = round(speed_bytes / (1024 * 1024), 1)
                        percent = round((copied / total_size) * 100.0, 1) if total_size > 0 else 100.0
                        rem_bytes = max(0, total_size - copied)
                        eta_sec = int(rem_bytes / speed_bytes) if speed_bytes > 0 else 0

                        if eta_sec < 60:
                            eta_str = f"~{eta_sec} mp"
                        elif eta_sec < 3600:
                            eta_str = f"~{eta_sec // 60} perc {eta_sec % 60} mp"
                        else:
                            eta_str = f"~{eta_sec // 3600} óra {(eta_sec % 3600) // 60} perc"

                        copied_h = f"{copied / (1024**3):.2f} GB" if copied >= 1024**3 else f"{copied / (1024**2):.1f} MB"
                        info = {
                            "active": True,
                            "filename": dst.name,
                            "source": str(src),
                            "destination": str(dst),
                            "destination_folder": str(dst.parent),
                            "total_bytes": total_size,
                            "copied_bytes": copied,
                            "copied_human": copied_h,
                            "total_human": size_h,
                            "percent": percent,
                            "speed_mbps": speed_mbps,
                            "eta_seconds": eta_sec,
                            "eta_str": eta_str,
                            "is_hardlink": False,
                        }
                        self.current_transfer = info
                        if callback:
                            try:
                                callback(info)
                            except Exception:
                                pass

            # Metaadatok és időbélyeg átvitele
            try:
                shutil.copystat(src, dst)
            except Exception:
                pass
            return True
        except Exception as e:
            logger.error(f"Hiba a haladás-követett másolás során ({src} -> {dst}): {e}")
            return False
        finally:
            self.current_transfer = None

    def _place_file(self, src: Path, dst: Path, callback: Optional[Callable[[Dict[str, Any]], None]] = None) -> bool:
        """
        Fájl elhelyezése:
        1. Elsődlegesen Hardlink (os.link) készítése: Nem foglal dupla lemezterületet,
           és a torrent kliens tovább tud seedelni az nCore HnR szabály teljesüléséig.
        2. Ha a hardlink nem támogatott (pl. más partíción van a Plex mappa), haladáskövetett másolás.
        """
        try:
            if dst.exists():
                logger.debug(f"A célfájl már létezik a Plex mappában, felülírás kihagyva: {dst.name}")
                return True

            dst.parent.mkdir(parents=True, exist_ok=True)

            try:
                # 1. Hardlink kísérlet (ugyanazon a meghajtón azonnali, nem foglal dupla helyet)
                os.link(src, dst)
                self.last_placement_hardlink = True
                src_size = src.stat().st_size if src.exists() else 0
                size_h = f"{src_size / (1024**3):.2f} GB" if src_size >= 1024**3 else f"{src_size / (1024**2):.1f} MB"
                self.last_placement_info = {
                    "active": False,
                    "status": "completed",
                    "method": "hardlink",
                    "source": str(src),
                    "destination": str(dst),
                    "destination_folder": str(dst.parent),
                    "filename": dst.name,
                    "is_hardlink": True,
                    "total_bytes": src_size,
                    "copied_bytes": src_size,
                    "copied_human": size_h,
                    "total_human": size_h,
                    "percent": 100.0,
                    "speed_mbps": 0.0,
                    "eta_seconds": 0,
                    "eta_str": "Azonnali (Hardlink)",
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                if callback:
                    try:
                        callback(self.last_placement_info)
                    except Exception:
                        pass
                logger.info(f"Hardlink létrehozva: {src.name} -> {dst}")
                return True
            except (OSError, NotImplementedError):
                # 2. Ha más meghajtón van a Plex mappa (pl. D: -> E:), biztonságos atomi másolás haladáskövetéssel
                self.last_placement_hardlink = False
                src_drive = getattr(src, "drive", "")
                dst_drive = getattr(dst, "drive", "")
                logger.info(
                    f"Hardlink nem lehetséges (különböző meghajtók: '{src_drive}' -> '{dst_drive}'), "
                    f"biztonságos fájl másolás indul: {src.name} -> {dst}..."
                )

                # Szabad hely ellenőrzése a célmeghajtón
                try:
                    src_size = src.stat().st_size
                    usage = shutil.disk_usage(dst.parent)
                    if usage.free < (src_size + 1024 * 1024 * 1024):
                        free_gb = round(usage.free / (1024**3), 1)
                        needed_gb = round(src_size / (1024**3), 1)
                        logger.error(f"Nincs elegendő szabad hely a célmeghajtón ({dst_drive})! Szabad: {free_gb} GB, szükséges: {needed_gb} GB")
                        return False
                except Exception as disk_err:
                    logger.debug(f"Lemezterület előzetes ellenőrzési figyelmeztetés: {disk_err}")

                # Átmeneti staging fájlba másolás valós idejű haladáskövetéssel
                staging_dst = dst.parent / f".{dst.name}.transfer"
                if staging_dst.exists():
                    try:
                        staging_dst.unlink()
                    except Exception:
                        pass

                copy_success = self._copy_with_progress(src, staging_dst, callback=callback)
                if not copy_success:
                    if staging_dst.exists():
                        try:
                            staging_dst.unlink()
                        except Exception:
                            pass
                    return False

                staging_dst.replace(dst)
                self.last_placement_info = {
                    "active": False,
                    "status": "completed",
                    "method": "copy",
                    "source": str(src),
                    "destination": str(dst),
                    "destination_folder": str(dst.parent),
                    "filename": dst.name,
                    "is_hardlink": False,
                    "percent": 100.0,
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                logger.info(f"Fájl sikeresen átmásolva és véglegesítve a Plex könyvtárban: {dst.name}")
                return True

        except Exception as e:
            logger.error(f"Hiba a fájl elhelyezésekor ({src} -> {dst}): {str(e)}")
            return False

    def _is_file_ready(self, file_path: Path) -> bool:
        """
        Biztonsági ellenőrzés a fájl mozgatása előtt:
        1. Nem befejezetlen kiterjesztés (.!qB, .part, .downloading)?
        2. Nem üres fájl (méret > 0)?
        3. Fájl hozzáférhetőség ellenőrzése anélkül, hogy a seedelő torrent klienst blokkolná.
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

            # Ha a fájl nem befejezetlen és van mérete, késznek tekintjük.
            # Az os.link (Hardlink) nem igényel exkluzív olvasási zárat, és a seedelést sem zavarja!
            return True
        except Exception as e:
            logger.debug(f"Hiba a fájl állapotának ellenőrzésekor: {file_path.name} ({e})")
            return False

    def cleanup_old_temp_files(self, days: int = 7) -> int:
        """
        Kitakarítja a letöltési munkamappából a 'days' napnál régebbi
        segédfájlokat (pl. régi .torrent állományok, .srt feliratok és _subs mappák).
        Visszaadja a törölt tételek számát.
        """
        import time
        deleted_count = 0
        cutoff_time = time.time() - (days * 86400)
        temp_torrents_dir = self.settings.DOWNLOAD_TEMP_DIR / "_torrents"
        if not temp_torrents_dir.exists():
            return 0

        try:
            for item in temp_torrents_dir.iterdir():
                if item.stat().st_mtime < cutoff_time:
                    try:
                        if item.is_file() and item.suffix.lower() in {".torrent", ".srt"}:
                            item.unlink()
                            deleted_count += 1
                        elif item.is_dir() and item.name.endswith("_subs"):
                            shutil.rmtree(item, ignore_errors=True)
                            deleted_count += 1
                    except Exception:
                        pass
            if deleted_count > 0:
                logger.info(f"Ideiglenes munkamappa karbantartva: {deleted_count} db régi segédfájl/mappa törölve.")
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
            subs = [path] if path.suffix.lower() in self.SUBTITLE_EXTENSIONS else []
        else:
            subs = [f for f in path.rglob("*") if f.is_file() and f.suffix.lower() in self.SUBTITLE_EXTENSIONS]

        def _sub_priority(p: Path) -> int:
            name_l = p.stem.lower()
            if any(k in name_l for k in [".hun", "_hun", "-hun", "magyar", ".hu", "_hu"]):
                return 0
            if any(k in name_l for k in [".eng", "_eng", "-eng", ".en", "_en"]):
                return 2
            return 1

        return sorted(subs, key=_sub_priority)

    @staticmethod
    def _extract_season_episode(filename: str):
        """
        Kinyeri az évad és epizód számot a fájlnévből (pl. S01E04 vagy 1x04 vagy 01.mkv).
        """
        m = re.search(r"[sS](\d{1,2})[eE](\d{1,3})", filename)
        if m:
            return int(m.group(1)), int(m.group(2))

        m = re.search(r"(\d{1,2})x(\d{1,3})", filename)
        if m:
            return int(m.group(1)), int(m.group(2))

        m_s = re.search(r"\b[sS](\d{1,2})\b", filename)
        season = int(m_s.group(1)) if m_s else None

        m_e = re.search(r"\b(?:[eE]|[eE][pP])(\d{1,3})\b", filename)
        episode = int(m_e.group(1)) if m_e else None

        # 1. Fallback: Kiterjesztés előtti közvetlen sorszám (pl. " - 05.mkv" vagy ".05.mkv")
        if not episode:
            m_num = re.search(r"(?:^|[^\d])(\d{1,2})\.(?:mkv|mp4|avi|m4v)$", filename, re.IGNORECASE)
            if m_num:
                episode = int(m_num.group(1))

        # 2. Fallback: Pontok vagy elválasztók közötti 2-jegyű epizódszám a technikai címkék előtt (pl. ".05.1080p.")
        if not episode:
            m_dot = re.search(
                r"[\._\s](0[1-9]|[1-9]\d)[\._\s](?:1080p|720p|2160p|4k|web|hdtv|bluray|dvdrip|xvid|[hx]\.?26[45])",
                filename,
                re.IGNORECASE,
            )
            if m_dot:
                episode = int(m_dot.group(1))

        # 3. Fallback: 3 jegyű epizódszám (pl. 104 -> S01E04), kizárva a felbontásokat és kodekeket (H.264, H.265)
        if not episode:
            m_3d = re.search(r"(?<![hHxX]\.)\b([1-9])(\d{2})\b", filename)
            if m_3d:
                full_num = int(m_3d.group(0))
                if full_num not in (264, 265, 480, 576, 720, 100):
                    cand_s, cand_e = int(m_3d.group(1)), int(m_3d.group(2))
                    if not season:
                        season = cand_s
                    episode = cand_e

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
