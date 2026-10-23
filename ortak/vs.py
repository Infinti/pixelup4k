r"""Portable VapourSynth (tools\vapoursynth: R79 + vs-mlrt TensorRT + BestSource) yolları ve ortam değişkenleri.

Sistem geneline hiçbir şey yazılmaz: VSSCRIPT_PATH ve PATH yalnız başlatılan sürece verilir.
Not: bu kuruluma pip ile eklenti kurarken --no-deps kullan; aksi halde VapourSynth R80+'a yükselip vs-mlrt'nin
eski arayüzlü eklentilerini yüklemez.
"""
from __future__ import annotations

import os

from ortak.araclar import KOK, TOOLS

VS = TOOLS / "vapoursynth"
VS_PAKET = VS / "Lib" / "site-packages" / "vapoursynth"
VSPIPE = VS_PAKET / "vspipe.exe"


def vs_ortam(**ek: str) -> dict[str, str]:
    """Portable VapourSynth'i bulmak için süreç ortamı; ek anahtarlar (PIXEL_*) doğrudan eklenir."""
    e = dict(os.environ)
    e["PATH"] = os.pathsep.join([str(VS), str(VS_PAKET), e.get("PATH", "")])
    e["VSSCRIPT_PATH"] = str(VS_PAKET / "vsscript.dll")
    e["PIXEL_KOK"] = str(KOK)
    e.update(ek)
    return e
