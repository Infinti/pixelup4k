"""Yollar, harici araç bulma (ffmpeg/ffprobe) ve küçük yardımcılar."""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

KOK = Path(__file__).resolve().parent.parent
TOOLS = KOK / "tools"
KLIPLER = KOK / "klipler"
CIKTI = KOK / "cikti"
ESKI_UZANTILAR = (".vob", ".mpg", ".mpeg", ".m2v")  # DVD / MPEG-2: iyileştirmeden önce hazırlık adımı zorunlu
VIDEO_UZANTILARI = (".mkv", ".mp4", ".webm", ".avi", ".mov", ".m2ts", ".ts", *ESKI_UZANTILAR)


def arac_bul(ad: str) -> str | None:
    """Önce tools\\ altındaki portable kopyaya, sonra PATH'e bakar."""
    for aday in (TOOLS / f"{ad}.exe", TOOLS / ad / f"{ad}.exe", TOOLS / "ffmpeg" / "bin" / f"{ad}.exe"):
        if aday.is_file():
            return str(aday)
    return shutil.which(ad)


def zorunlu(ad: str) -> str:
    yol = arac_bul(ad)
    if not yol:
        sys.exit(f"HATA: '{ad}' bulunamadı. tools\\ altına koy veya PATH'e ekle.")
    return yol


def tam_hiz(surec: subprocess.Popen | None = None) -> None:
    """Windows'un arka plan güç azaltmasını (EcoQoS) bu süreç ya da verilen alt süreç için kapatır.

    Windows 11 penceresi simge durumunda / arka planda olan uygulamayı ve alt süreçlerini düşük QoS'a alıp verimli
    çekirdeklere ve düşük frekansa itebiliyor; arayüz simge durumundayken CPU aşamaları (ton sabitleme) belirgin
    yavaşlıyordu. PROCESS_POWER_THROTTLING_EXECUTION_SPEED denetimi açık + durumu kapalı = "yüksek QoS, azaltma yok".
    Yönetici yetkisi gerekmez; öncelik sınıfı değiştirilmez. Desteklemeyen Windows'ta sessizce geçer.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        class _Durum(ctypes.Structure):
            _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG), ("StateMask", wintypes.ULONG)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.SetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.SetProcessInformation.restype = wintypes.BOOL
        tutamac = k32.GetCurrentProcess() if surec is None else int(surec._handle)  # type: ignore[attr-defined]
        durum = _Durum(1, 1, 0)  # sürüm 1, denetim = EXECUTION_SPEED, durum = 0 (azaltma yok)
        k32.SetProcessInformation(tutamac, 4, ctypes.byref(durum), ctypes.sizeof(durum))  # 4 = ProcessPowerThrottling
    except (OSError, AttributeError, ValueError):
        pass


def calistir(komut: list[str], **kw) -> subprocess.CompletedProcess:
    """subprocess.run(capture_output=True) eşdeğeri; alt süreç güç azaltmasından muaf tutulur (tam_hiz)."""
    with subprocess.Popen(komut, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                          errors="replace", **kw) as p:
        tam_hiz(p)
        cikti, hata = p.communicate()
    return subprocess.CompletedProcess(komut, p.returncode, cikti, hata)


def url_mi(s: str) -> bool:
    """Yalnız yerel dosya desteklenir; URL verilince anlaşılır hata vermek için."""
    return bool(re.match(r"^https?://", s, re.I))


def ps_komut(argumanlar: list[str]) -> str:
    """Windows PowerShell'e olduğu gibi yapıştırılabilen komut metni (arayüz günlüğü, bilgi satırları).

    Boşluk veya özel karakter içeren argüman tek tırnağa alınır: PowerShell çift tırnak içinde $ ve ` karakterlerini
    genişletir, tek tırnakta ise yalnız '' kaçışı vardır. (PowerShell 5.1'de denendi: 2,4 / a=b / -1 / Türkçe harf
    tırnaksız doğru geçiyor.)
    """
    return " ".join(a if re.fullmatch(r"[\w.:\\/=,+-]+", a) else "'" + a.replace("'", "''") + "'" for a in argumanlar)


def guvenli_ad(s: str) -> str:
    return re.sub(r"[^\w.-]+", "_", s, flags=re.UNICODE).strip("_")[:60] or "video"


def saniye_coz(metin: str) -> float | None:
    """'90', '1:30', '0:01:30' → saniye; boş → None"""
    metin = metin.strip().replace(",", ".")
    if not metin:
        return None
    toplam = 0.0
    for parca in metin.split(":"):
        toplam = toplam * 60 + float(parca)
    return toplam
