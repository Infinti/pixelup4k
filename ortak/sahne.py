"""Sahne kesmesi tespiti (RIFE'ın kesme üzerinden ara kare üretmemesi için).

Neden kendi tespitimiz: Video2X'in tek eşikli fark ölçüsü (-t) iki test klibinde iki yönden de başarısız oldu:
-t 10 karanlık bir kesmeyi kaçırdı (iki sahne eriyerek geçti), -t 5 ise anime'nin üçerli çizilmiş hızlı
hareketini ve yavaş pan'ı kesme sayıp klibin çoğunda hiç ara kare üretmedi. ffmpeg scdet de aynı sorunu yaşıyor
(karanlık kesme 4.7, üçerli aksiyon 8.2 skor).

Kural (160x90 karelerde, iki klipte doğrulandı):
- Renk histogramı farkı (kanal başına 16 kutu, L1/3) >= HIST_ESIK → kesme.
  Ölçüm: kesmeler 0.62–1.09; kesme olmayan en yüksek 0.24 (ışık patlaması), hızlı aksiyon 0.05–0.07.
- Ya da hareket dengelemeli fark (±12 px kaydırmada en iyi örtüşme) > DENGELI_MIN ve komşu hareketli
  geçişlerin medyanının ORAN katı → kesme (renkleri benzer iki sahne arasındaki kesme için). Tekrar eden
  kareler (anime ikişerli/üçerli çizim) komşu medyanına katılmaz; yoksa her hareket adımı kesme sayılır.
Yanlış pozitifin bedeli küçük (o geçişte ara kare yerine bekleme), kaçırılan kesmenin bedeli eriyen geçiş;
bu yüzden kural algılamaya meyilli.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np

W, H = 160, 90
HIST_ESIK = 0.35
DENGELI_MIN = 8.0
ORAN = 4.0
KAYMA = 12
TEKRAR_ESIK = 0.5
PENCERE = 12


def _kareler(ffmpeg: str, dosya: Path) -> np.ndarray:
    komut = [ffmpeg, "-v", "error", "-nostdin", "-i", str(dosya), "-map", "0:v:0",
             "-vf", f"setpts=N/FRAME_RATE/TB,scale={W}:{H}:flags=area,format=rgb24", "-f", "rawvideo", "-"]
    from ortak.araclar import tam_hiz  # betik olarak çalıştırılınca sys.path __main__'de ayarlanıyor
    with subprocess.Popen(komut, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as p:
        tam_hiz(p)
        ham, hata = p.communicate()
    if p.returncode != 0:
        raise subprocess.CalledProcessError(p.returncode, komut, ham, hata)
    return np.frombuffer(ham, dtype=np.uint8).reshape(-1, H, W, 3)


def _histogram(k: np.ndarray) -> np.ndarray:
    return np.concatenate([np.histogram(k[..., c], 16, (0, 256))[0] for c in range(3)]) / (H * W)


def _dengeli_fark(a: np.ndarray, b: np.ndarray) -> float:
    m = KAYMA
    ic = a[m:-m, m:-m]
    return min(float(np.abs(ic - b[m + dy:H - m + dy, m + dx:W - m + dx]).mean())
               for dy in range(-m, m + 1, 2) for dx in range(-m, m + 1, 2))


def kesmeleri_bul(ffmpeg: str, dosya: Path, onbellek: Path | None = None) -> list[int]:
    """Yeni sahnenin İLK karesinin indekslerini (0 tabanlı) döndürür."""
    if onbellek and onbellek.is_file() and onbellek.stat().st_mtime >= dosya.stat().st_mtime:
        return json.loads(onbellek.read_text(encoding="utf-8"))["kesmeler"]
    k = _kareler(ffmpeg, dosya).astype(np.float32)
    n = len(k)
    hist = [_histogram(x) for x in k]
    hd = np.array([np.abs(hist[i] - hist[i + 1]).sum() / 3 for i in range(n - 1)])
    ham = np.array([float(np.abs(k[i] - k[i + 1]).mean()) for i in range(n - 1)])
    # Dengeli fark pahalı: yalnız ham farkı belirgin geçişlerde hesapla, gerisi ham farkla aynı kabul edilir
    dengeli = ham.copy()
    for i in np.nonzero(ham > DENGELI_MIN)[0]:
        dengeli[i] = _dengeli_fark(k[i], k[i + 1])

    kesmeler: list[int] = []
    for i in range(n - 1):
        if hd[i] >= HIST_ESIK:
            kesmeler.append(i + 1)
            continue
        if dengeli[i] <= DENGELI_MIN:
            continue
        lo, hi = max(0, i - PENCERE), min(n - 1, i + PENCERE + 1)
        komsu = [dengeli[j] for j in range(lo, hi) if j != i and ham[j] > TEKRAR_ESIK]
        taban = float(np.median(komsu)) if komsu else 0.0
        if taban == 0.0 or dengeli[i] > ORAN * taban:
            kesmeler.append(i + 1)
    if onbellek:
        onbellek.write_text(json.dumps({"dosya": dosya.name, "kare": n, "kesmeler": kesmeler}), encoding="utf-8")
    return kesmeler


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from ortak.araclar import zorunlu
    from ortak.video import akis_bilgisi
    for yol in sys.argv[1:]:
        p = Path(yol)
        ks = kesmeleri_bul(zorunlu("ffmpeg"), p)
        fps = akis_bilgisi(zorunlu("ffprobe"), p)["fps"] or 24000 / 1001
        print(f"{p.name}: {len(ks)} kesme → " + ", ".join(f"{i} ({i / fps:.2f} sn)" for i in ks))
