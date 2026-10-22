import re
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


def extract_season_tag(title: str) -> Optional[str]:
    """Kinyeri a formázott évad címkét a release címéből (pl. 'S08 (8. Évad)', 'S01-S08 Csomag')."""
    m_pack = re.search(r'[sS](\d{1,2})\s*[-–]\s*[sS]?(\d{1,2})', title)
    if m_pack:
        return f"S{int(m_pack.group(1)):02d}-S{int(m_pack.group(2)):02d} Csomag"
    if re.search(r'\b(complete|teljes\s*sorozat|pack)\b', title, re.IGNORECASE):
        return "Teljes Sorozat"

    m_s = re.search(r'[sS](\d{1,2})', title)
    if m_s:
        s_val = int(m_s.group(1))
        if 1 <= s_val <= 50:
            return f"S{s_val:02d} ({s_val}. Évad)"

    m_hu = re.search(r'(\d{1,2})\.?\s*[eé]vad', title, re.IGNORECASE)
    if m_hu:
        s_val = int(m_hu.group(1))
        if 1 <= s_val <= 50:
            return f"S{s_val:02d} ({s_val}. Évad)"

    m_hu2 = re.search(r'[eé]vad\s*(\d{1,2})', title, re.IGNORECASE)
    if m_hu2:
        s_val = int(m_hu2.group(1))
        if 1 <= s_val <= 50:
            return f"S{s_val:02d} ({s_val}. Évad)"

    return None


def extract_season_num(title: str) -> Optional[int]:
    """Kinyeri az évad sorszámát egész számként. Csomag / teljes sorozat esetén -1."""
    m_pack = re.search(r'[sS](\d{1,2})\s*[-–]\s*[sS]?(\d{1,2})', title)
    if m_pack or re.search(r'\b(complete|teljes\s*sorozat|pack)\b', title, re.IGNORECASE):
        return -1

    m_s = re.search(r'[sS](\d{1,2})', title)
    if m_s:
        s_val = int(m_s.group(1))
        if 1 <= s_val <= 50:
            return s_val

    m_hu = re.search(r'(\d{1,2})\.?\s*[eé]vad', title, re.IGNORECASE)
    if m_hu:
        s_val = int(m_hu.group(1))
        if 1 <= s_val <= 50:
            return s_val

    m_hu2 = re.search(r'[eé]vad\s*(\d{1,2})', title, re.IGNORECASE)
    if m_hu2:
        s_val = int(m_hu2.group(1))
        if 1 <= s_val <= 50:
            return s_val

    return None


class MediaType(str, Enum):
    MOVIE = "movie"
    TV = "tv"


class MediaItem(BaseModel):
    """
    TMDB-ből kinyert film vagy sorozat adatmodell.
    Tartalmazza az összes vizuális elemet a megerősítéshez és az nCore-hoz kritikus IMDb ID-t.
    """
    tmdb_id: int = Field(..., description="TMDB egyedi azonosító")
    imdb_id: Optional[str] = Field(default=None, description="IMDb kód (pl. tt0111161), aranyat ér az nCore kereséshez!")
    media_type: MediaType = Field(default=MediaType.MOVIE, description="Film vagy sorozat")
    title_hu: str = Field(..., description="Magyar cím")
    title_en: Optional[str] = Field(default=None, description="Eredeti / nemzetközi cím")
    year: Optional[int] = Field(default=None, description="Kiadási év")
    overview: Optional[str] = Field(default=None, description="Magyar nyelvű rövid szinopszis")
    poster_url: Optional[str] = Field(default=None, description="Poszter kép URL")
    backdrop_url: Optional[str] = Field(default=None, description="Háttérkép URL")
    rating: Optional[float] = Field(default=None, description="TMDB értékelés (0-10)")
    genres: List[str] = Field(default_factory=list, description="Műfajok listája magyarul")
    director: Optional[str] = Field(default=None, description="Rendező(k)")
    cast: List[str] = Field(default_factory=list, description="Főbb színészek listája")
    number_of_seasons: Optional[int] = Field(default=None, description="Sorozat esetén az évadok száma")
    trailer_url: Optional[str] = Field(default=None, description="YouTube előzetes URL vagy kulcs")
    runtime_minutes: Optional[int] = Field(default=None, description="Film hossza percben")


def parse_release_specs(title: str, category: str = "", size_bytes: int = 0) -> dict:
    """Kinyeri a részletes specifikációkat (felbontás, HDR, hangszabvány, forrás, kodek)."""
    t_up = title.upper()
    cat = category.lower()

    # 1. Felbontás
    if any(k in t_up for k in ["2160P", "4K", "UHD"]):
        resolution = "2160p (4K UHD)"
    elif any(k in t_up for k in ["1080P", "1080I"]):
        resolution = "1080p (Full HD)"
    elif "720P" in t_up:
        resolution = "720p (HD)"
    elif any(k in cat for k in ["xvid", "dvd"]) or any(k in t_up for k in ["480P", "576P", "XVID"]):
        resolution = "SD (DVD / XviD)"
    else:
        resolution = "HD"

    # 2. Forrás / Kiadás
    is_cam = bool(re.search(r'\b(CAM|HDCAM|TS|TELESYNC|TELECINE|TC|SCREENER|SCR)\b', t_up))
    if is_cam:
        source = "CAM (Mozis)"
    elif "REMUX" in t_up:
        source = "Remux"
    elif any(k in t_up for k in ["BLURAY", "BDRIP", "BRRIP"]):
        source = "BluRay"
    elif any(k in t_up for k in ["WEB-DL", "WEBRIP", "WEB"]):
        source = "WEB-DL"
    elif "HDTV" in t_up:
        source = "HDTV"
    elif "DVD" in t_up or "DVDRIP" in t_up:
        source = "DVD"
    else:
        source = "Digitális"

    # 3. HDR / Dinamikatartomány
    if any(k in t_up for k in ["DV", "DOVI", "DOLBY VISION"]):
        hdr = "Dolby Vision + HDR"
    elif "HDR10+" in t_up:
        hdr = "HDR10+"
    elif "HDR" in t_up:
        hdr = "HDR"
    else:
        hdr = "SDR"

    # 4. Hangszabvány
    if "ATMOS" in t_up:
        audio = "Dolby Atmos"
    elif "TRUEHD" in t_up:
        audio = "Dolby TrueHD"
    elif "DTS-HD" in t_up or "DTSHD" in t_up:
        audio = "DTS-HD MA"
    elif "DTS" in t_up:
        audio = "DTS 5.1"
    elif any(k in t_up for k in ["DDP5.1", "DDP 5.1", "EAC3", "DD+"]):
        audio = "Dolby Digital Plus 5.1 (DDP)"
    elif any(k in t_up for k in ["DD5.1", "DD 5.1", "AC3"]):
        audio = "Dolby Digital 5.1 (AC3)"
    elif "5.1" in t_up:
        audio = "5.1 Hangzás"
    elif any(k in t_up for k in ["2.0", "DDP2.0", "STEREO"]):
        audio = "2.0 Sztereó"
    else:
        audio = "Többcsatornás / Sztereó"

    # 5. Videókodek
    if any(k in t_up for k in ["X265", "H.265", "HEVC"]):
        codec = "HEVC / x265"
    elif any(k in t_up for k in ["X264", "H.264", "AVC"]):
        codec = "AVC / x264"
    elif "XVID" in t_up:
        codec = "XviD"
    else:
        codec = "H.264"

    is_pack = bool(extract_season_num(title) == -1 or re.search(r'\b(pack|complete|teljes\s*sorozat)\b', t_up))

    return {
        "resolution": resolution,
        "source": source,
        "hdr": hdr,
        "audio": audio,
        "codec": codec,
        "is_cam": is_cam,
        "is_pack": is_pack,
    }


class TorrentRelease(BaseModel):
    """
    nCore-ról talált kiadás adatmodellje.
    """
    torrent_id: str = Field(..., description="nCore torrent azonosító")
    title: str = Field(..., description="A release pontos címe az nCore-on")
    category: str = Field(..., description="Kategória: hd_hun, hd, xvid_hun, sorozat_hun, sorozat, stb.")
    size_bytes: int = Field(default=0, description="Méret bájtokban")
    size_human: str = Field(default="0 MB", description="Ember által olvasható méret (pl. 4.2 GB)")
    seeders: int = Field(default=0, description="Aktív feltöltők száma")
    leechers: int = Field(default=0, description="Aktív letöltők száma")
    has_hungarian_audio: bool = Field(default=False, description="Tartalmaz-e magyar szinkront")
    has_hungarian_sub: bool = Field(default=False, description="Tartalmaz-e magyar feliratot")
    download_url: Optional[str] = Field(default=None, description="Letöltési URL vagy .torrent elérési út")
    imdb_id: Optional[str] = Field(default=None, description="nCore adatlapon lévő IMDb kód")

    @property
    def season_tag(self) -> Optional[str]:
        return extract_season_tag(self.title)

    @property
    def season_num(self) -> Optional[int]:
        return extract_season_num(self.title)

    @property
    def specs(self) -> dict:
        return parse_release_specs(self.title, self.category, self.size_bytes)

    @property
    def is_cam(self) -> bool:
        return self.specs["is_cam"]

    @property
    def is_pack(self) -> bool:
        return self.specs["is_pack"]



class SubtitleItem(BaseModel):
    """
    Letöltött vagy megtalált felirat adatmodell.
    """
    language: str = Field(default="hu", description="Nyelvkód (pl. hu)")
    release_name: Optional[str] = Field(default=None, description="A felirathoz illeszkedő release neve")
    download_url: Optional[str] = Field(default=None, description="Felirat letöltési linkje")
    file_name: Optional[str] = Field(default=None, description="Célfájl neve a Plex mappában (pl. Film (2024).hu.srt)")
