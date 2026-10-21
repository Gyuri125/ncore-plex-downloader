from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


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


class SubtitleItem(BaseModel):
    """
    Letöltött vagy megtalált felirat adatmodell.
    """
    language: str = Field(default="hu", description="Nyelvkód (pl. hu)")
    release_name: Optional[str] = Field(default=None, description="A felirathoz illeszkedő release neve")
    download_url: Optional[str] = Field(default=None, description="Felirat letöltési linkje")
    file_name: Optional[str] = Field(default=None, description="Célfájl neve a Plex mappában (pl. Film (2024).hu.srt)")
