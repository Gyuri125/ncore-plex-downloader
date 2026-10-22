from pathlib import Path
from PIL import Image

from src.services.tray_service import create_tray_icon_image, TrayService
from src.services.torrent_service import TorrentService
from src.core.background_worker import BackgroundWorker
from src.ui.app import get_disk_free_gb


def test_create_tray_icon_image():
    img = create_tray_icon_image()
    assert isinstance(img, Image.Image)
    assert img.size == (64, 64)
    assert img.mode == "RGBA"


def test_find_qbittorrent_exe():
    exe_path = TorrentService.find_qbittorrent_exe()
    assert exe_path is not None
    assert isinstance(exe_path, Path)
    assert exe_path.name.lower() == "qbittorrent.exe"
    assert exe_path.exists()


def test_torrent_service_active_summary():
    ts = TorrentService()
    summary = ts.get_active_summary()
    assert isinstance(summary, dict)
    assert "active_count" in summary
    assert "summary" in summary
    assert "tooltip" in summary
    assert "Ncore Letöltő" in summary["tooltip"]


def test_background_worker_lifecycle():
    worker = BackgroundWorker(interval_seconds=10)
    assert worker.interval == 10
    worker.start()
    assert worker._thread is not None
    assert worker._thread.is_alive()
    worker.stop()
    assert worker._thread is None


def test_disk_free_gb():
    current_path = Path.cwd()
    free_gb = get_disk_free_gb(current_path)
    assert free_gb is not None
    assert free_gb > 0.0
