r"""README WebP'leri: yakınlaştırılmış kayar karşılaştırma ve ağır çekim (kurulum.ps1'in ffmpeg'iyle).

    py -3.14 docs\webp_uret.py <bozuk.mp4> <sonuc_4k.mkv> <çıktı klasörü>

İki girdi de kare sırasından zaman damgası alır (setpts=N/FRAME_RATE/TB), kaynak 4K'ya lanczos ile büyütülür,
aynı 4K bölgesi kırpılıp büyütülür. Kayar çizgi: solu kaynak, sağı sonuç. Bölge ve kare aralıkları demo klibine
(docs\uret.md: Tears of Steel 06:51'den 10 sn) göredir.
"""
import os
import subprocess
import sys
from pathlib import Path

FFMPEG = str(Path(__file__).resolve().parent.parent / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe")
# drawtext yolu: ters bölü yerine /, sürücü iki noktası kaçışlı (C\:/Windows/...)
FONT = (os.environ.get("WINDIR", r"C:\Windows").replace("\\", "/") + "/Fonts/arial.ttf").replace(":", "\\:", 1)


def etiket(metin: str, x: str, y: str = "14") -> str:
    # metinde ':' olmamalı (drawtext seçenek ayırıcısı)
    return (f"drawtext=fontfile='{FONT}':text='{metin}':x={x}:y={y}:fontsize=26:fontcolor=white:"
            "box=1:boxcolor=black@0.6:boxborderw=6")


def uret(bozuk: str, sonuc: str, hedef: Path, bas_kare: int, bit_kare: int, kirp: str, yavas: int,
         ust_yazi: str, boyut: str = "960:400") -> None:
    hiz = f"setpts={yavas}*PTS," if yavas > 1 else ""
    # Kayar çizgi çıktı süresince bir tam tur atar (0.1W..0.9W)
    sure = (bit_kare - bas_kare) / 24 * yavas
    konum = f"W*(0.5+0.4*sin(2*PI*T/{sure:.3f}))"
    ortak = f"trim=start_frame={bas_kare}:end_frame={bit_kare},setpts=PTS-STARTPTS,{hiz}"
    fg = (f"[0:v]setpts=N/FRAME_RATE/TB,scale=3840:1600:flags=lanczos,{ortak}crop={kirp},"
          f"scale={boyut}:flags=lanczos,fps=24,format=gbrp[a];"
          f"[1:v]setpts=N/FRAME_RATE/TB,{ortak}crop={kirp},scale={boyut}:flags=lanczos,fps=24,format=gbrp[b];"
          # fps=24 blend'den önce: ağır çekimde video kareleri tekrar eder ama çizgi her çıktı karesinde ilerler
          f"[a][b]blend=all_expr='if(lt(abs(X-{konum}),1.5),255,if(lt(X,{konum}),A,B))',"
          f"{etiket('1.2 Mbps source', '14')},{etiket('Result', 'w-tw-14')},"
          f"{etiket(ust_yazi, '(w-tw)/2', 'h-th-14')}")
    r = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", bozuk, "-i", sonuc,
                        "-filter_complex", fg, "-c:v", "libwebp", "-lossless", "0", "-quality", "82",
                        "-compression_level", "6", "-loop", "0", "-an", str(hedef)], capture_output=True, text=True)
    if r.returncode:
        sys.exit(r.stderr[-1500:])
    print(hedef.name, f"{hedef.stat().st_size / 1e6:.2f} MB, {sure:.1f} sn")


if __name__ == "__main__":
    bozuk, sonuc, klasor = sys.argv[1], sys.argv[2], Path(sys.argv[3])
    klasor.mkdir(parents=True, exist_ok=True)
    # Robot kolu sahnesi (8. kareden önce başka sahne), 0,38–3,38 sn; kol başı ortada; 4K'da 640x267 → 1,5×
    uret(bozuk, sonuc, klasor / "demo_kayar.webp", 9, 81, "640:267:1205:440", 1, "4K output, 1.5x zoom")
    # Laboratuvar sahnesi, 7,5–8,75 sn (baş dönüyor); yüz ve gözlük; 4K pikselleri olduğu gibi; 1/4 hız
    uret(bozuk, sonuc, klasor / "demo_agir_cekim.webp", 180, 210, "960:540:2050:150", 4,
         "native 4K pixels, 1/4 speed", "960:540")
