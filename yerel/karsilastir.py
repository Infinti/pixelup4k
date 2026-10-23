"""Orijinal ve işlenmiş klibi karşılaştırır: karşılaştırma videosu + sabit kare PNG'leri.

Kullanım:
    py -3.14 yerel\\karsilastir.py <orijinal> <islenmis> [<islenmis2> ...]
        [--mod bolunmus|ust-alt|kayar|yanyana] [--anlar 2,4.5,7] [--kirp X:Y:G:Y] [--ad isim]
        [--sol-ad "Kaynak"] [--adlar "Sonuç" ...] [--detay-yan]

- Orijinal, işlenmişin çözünürlüğüne lanczos ile büyütülür (TV'nin kendi ölçeklemesine yakın referans).
- bolunmus: sol yarı orijinal, sağ yarı işlenmiş (tam 4K, TV'de izlemek için en iyisi).
- ust-alt : üst yarı orijinal, alt yarı işlenmiş (ikisi de tam genişlik; soldan sağa ton farkı için).
- kayar   : ayırıcı çizgi zamanla sağa-sola kayar.
- yanyana : iki kare yan yana (yarı boyutta); detay için --kirp ile birlikte kullan.
- PNG'ler: her an için orijinal + her işlenmiş tam kare ve (varsa) kırpılmış detay bloğu.
- --kirp video kiplerine de uygulanır: karşılaştırma yalnız o bölgede yapılır (kayar çizgi bölge içinde gider-gelir;
  README GIF'i gibi küçük ama 4K ayrıntılı görseller için). Detay PNG'lerinde bölge ayrıca 2 kat büyütülür.
- --sol-ad / --adlar: görüntüye yazılan etiketler (varsayılan "Orijinal (lanczos)" ve dosya adları).
- --detay-yan: detay bloğu alt alta yerine yan yana ve her parça etiketli.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ortak.araclar import CIKTI, calistir, guvenli_ad, zorunlu  # noqa: E402

# drawtext yolu: ters bölü yerine /, sürücü iki noktası kaçışlı (C\\:/Windows/...)
FONT = (os.environ.get("WINDIR", r"C:\Windows").replace("\\", "/") + "/Fonts/arial.ttf").replace(":", "\\:", 1)


def boyut_al(ffprobe: str, dosya: Path) -> tuple[int, int, float]:
    r = calistir([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                  "stream=width,height:format=duration", "-of", "json", str(dosya)])
    j = json.loads(r.stdout)
    return j["streams"][0]["width"], j["streams"][0]["height"], float(j["format"].get("duration") or 0)


def ilk_kare_zamani(ffprobe: str, dosya: Path) -> float:
    """Kesilmiş klipler 0.042 sn'den, Video2X çıktıları 0'dan başlayabiliyor; -ss'yi buna göre kaydırmak gerek."""
    r = calistir([ffprobe, "-v", "error", "-select_streams", "v:0", "-read_intervals", "%+#1",
                  "-show_entries", "frame=pts_time", "-of", "csv=p=0", str(dosya)])
    try:
        return float(r.stdout.split()[0])
    except (IndexError, ValueError):
        return 0.0


def etiket(metin: str, x: str, boyut: int = 40) -> str:
    metin = metin.replace(":", "\\:").replace("'", "")
    return (f"drawtext=fontfile='{FONT}':text='{metin}':x={x}:y={boyut * 3 // 5}:fontsize={boyut}:fontcolor=white:"
            f"box=1:boxcolor=black@0.6:boxborderw={boyut // 4}")


def kare_hizi(ffprobe: str, dosya: Path) -> str:
    r = calistir([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=avg_frame_rate",
                  "-of", "csv=p=0", str(dosya)])
    return r.stdout.strip() or "24000/1001"


def video_uret(ffmpeg: str, orij: Path, isl: Path, w: int, h: int, mod: str, hedef: Path, ad: str,
               sol_ad: str = "Orijinal (lanczos)", kirp: str | None = None) -> None:
    # Zaman damgaları kare sırasından yeniden üretilir (N/FRAME_RATE). PTS-STARTPTS yetmiyordu: iki dosyanın
    # milisaniye yuvarlaması farklı (0.041 / 0.042) olunca eşleştirme işlenmiş tarafta önceki kareyi tekrar
    # gösteriyordu; pan sahnesinde sağ yarı her ~3.5 karede bir takılıp ~17 fps gibi oynuyordu.
    # Orijinal, işlenmişin kare hızına çoğaltılır: RIFE çıktısıyla karşılaştırma 24 fps'e kilitlenmesin.
    hiz = kare_hizi(zorunlu("ffprobe"), isl)
    zaman = "setpts=N/FRAME_RATE/TB"
    # Kırpma iki taraf aynı boyuta getirildikten sonra; sonraki yerleşim kırpılan bölgenin boyutuyla yapılır
    kc = ""
    if kirp:
        cw, ch, cx, cy = kirp_coz(kirp)
        kc = f",crop={cw}:{ch}:{cx}:{cy}"
    ref = f"[0:v]{zaman},fps={hiz},scale={w}:{h}:flags=lanczos{kc},format=gbrp[a]"
    yeni = f"[1:v]{zaman},scale={w}:{h}:flags=lanczos{kc},format=gbrp[b]"
    if kirp:
        w, h = cw, ch
    if mod == "bolunmus":
        ana = (f"{ref};{yeni};[a]crop={w // 2}:{h}:0:0[sol];[b]crop={w // 2}:{h}:{w // 2}:0[sag];"
               f"[sol][sag]hstack,drawbox=x={w // 2 - 2}:y=0:w=4:h={h}:color=white:t=fill,"
               f"{etiket(sol_ad, '30')},{etiket(ad, f'w-tw-30')}")
    elif mod == "ust-alt":
        # Üst yarı orijinal, alt yarı işlenmiş: iki yarı da tam genişlik, soldan sağa ton değişimi karşılaştırılabilir
        ana = (f"{ref};{yeni};[a]crop={w}:{h // 2}:0:0[ust];[b]crop={w}:{h // 2}:0:{h // 2}[alt];"
               f"[ust][alt]vstack,drawbox=x=0:y={h // 2 - 2}:w={w}:h=4:color=white:t=fill,"
               f"{etiket(sol_ad, '30')},"
               f"drawtext=fontfile='{FONT}':text='{ad.replace(':', chr(92) + ':')}':x=30:y={h // 2 + 24}:"
               "fontsize=40:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=10")
    elif mod == "kayar":
        # Ayırıcı 0.15W..0.85W arasında ~10 sn periyotla gider-gelir; solu orijinal, sağı işlenmiş
        konum = "W*(0.5+0.35*sin(T*0.628))"
        ana = (f"{ref};{yeni};[a][b]blend=all_expr='if(lt(abs(X-{konum}),2),255,if(lt(X,{konum}),A,B))',"
               f"{etiket(sol_ad, '30')},{etiket(ad, f'w-tw-30')}")
    else:
        ana = (f"{ref};{yeni};[a]scale={w // 2}:{h // 2}:flags=lanczos,{etiket(sol_ad, '20')}[a2];"
               f"[b]scale={w // 2}:{h // 2}:flags=lanczos,{etiket(ad, '20')}[b2];[a2][b2]hstack")
    komut = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(orij), "-i", str(isl),
             # Renk etiketi açık verilmezse ffmpeg 8 çıktıyı BT.601 etiketleyebiliyor (oynatıcıda koyu/morumsu)
             "-filter_complex", ana + ",format=p010le", "-colorspace", "bt709", "-color_primaries", "bt709",
             "-color_trc", "bt709", "-color_range", "tv", "-map", "0:a:0?", "-c:a", "copy",
             "-c:v", "hevc_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "18", "-shortest", str(hedef)]
    r = calistir(komut)
    if r.returncode != 0:
        sys.exit(f"Karşılaştırma videosu üretilemedi:\n{r.stderr[-1500:]}")


def kare_al(ffmpeg: str, dosya: Path, t: float, w: int, h: int, hedef: Path, kirp: str | None) -> None:
    """t, dosyanın ilk karesine göredir (her dosyada aynı kare indeksine denk gelsin diye)."""
    t += ilk_kare_zamani(zorunlu("ffprobe"), dosya)
    # 8-bit PNG: 10-bit kaynakta ffmpeg aksi halde 16-bit (4K'da ~20 MB) PNG yazıyor
    vf = f"scale={w}:{h}:flags=lanczos,format=rgb24"
    if kirp:
        cw, ch, cx, cy = kirp_coz(kirp)
        vf += f",crop={cw}:{ch}:{cx}:{cy},scale=iw*2:ih*2:flags=neighbor"
    r = calistir([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.3f}", "-i", str(dosya),
                  "-frames:v", "1", "-vf", vf, str(hedef)])
    if r.returncode != 0:
        sys.exit(f"Kare alınamadı ({dosya.name} @ {t}):\n{r.stderr[-800:]}")


def kirp_coz(kirp: str) -> tuple[int, int, int, int]:
    """'X:Y:G:Y' → ffmpeg crop sırası (genişlik, yükseklik, x, y)"""
    x, y, g, yuk = (int(v) for v in kirp.split(":"))
    return g, yuk, x, y


def detay_blogu(ffmpeg: str, pngler: list[Path], hedef: Path, adlar: list[str] | None = None,
                yan: bool = False) -> None:
    """Detay parçalarını birleştirir: alt alta (varsayılan) ya da yan yana; adlar verilirse her parça etiketlenir."""
    girdiler: list[str] = []
    for p in pngler:
        girdiler += ["-i", str(p)]
    parcalar = []
    for i in range(len(pngler)):
        zincir = [etiket(adlar[i], "16", 28)] if adlar else []
        if i < len(pngler) - 1:  # parçalar arasında 6 px beyaz ayraç
            zincir.append("pad=iw+6:ih:0:0:white" if yan else "pad=iw:ih+6:0:0:white")
        parcalar.append(f"[{i}:v]{','.join(zincir) or 'null'}[p{i}]")
    yigin = ";".join(parcalar) + ";" + "".join(f"[p{i}]" for i in range(len(pngler))) + \
        f"{'hstack' if yan else 'vstack'}=inputs={len(pngler)}"
    r = calistir([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *girdiler, "-filter_complex", yigin, str(hedef)])
    if r.returncode != 0:
        sys.exit(f"Detay bloğu üretilemedi:\n{r.stderr[-800:]}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Orijinal / işlenmiş video karşılaştırması")
    ap.add_argument("orijinal")
    ap.add_argument("islenmis", nargs="+", help="bir veya daha fazla işlenmiş video")
    ap.add_argument("--mod", choices=["bolunmus", "ust-alt", "kayar", "yanyana"], default="bolunmus")
    ap.add_argument("--anlar", default="", help="PNG için saniyeler, ör. 2,4.5,7 (varsayılan: %%20/%%50/%%80)")
    ap.add_argument("--kirp", help="detay bölgesi X:Y:GENİŞLİK:YÜKSEKLİK (işlenmiş çözünürlükte)")
    ap.add_argument("--ad", help="çıktı klasörü adı")
    ap.add_argument("--video-yok", action="store_true", help="sadece PNG üret")
    ap.add_argument("--sol-ad", default="Orijinal (lanczos)", help="görüntüde orijinal tarafın etiketi")
    ap.add_argument("--adlar", nargs="+", help="görüntüde işlenmiş videoların etiketleri (sırayla)")
    ap.add_argument("--detay-yan", action="store_true", help="detay bloğu yan yana (varsayılan: alt alta)")
    a = ap.parse_args()

    ffmpeg, ffprobe = zorunlu("ffmpeg"), zorunlu("ffprobe")
    orij = Path(a.orijinal).resolve()
    isler = [Path(p).resolve() for p in a.islenmis]
    for p in [orij, *isler]:
        if not p.is_file():
            sys.exit(f"Dosya yok: {p}")
    if a.adlar and len(a.adlar) != len(isler):
        sys.exit(f"--adlar sayısı ({len(a.adlar)}) işlenmiş video sayısıyla ({len(isler)}) aynı olmalı")
    adlar = a.adlar or [p.stem for p in isler]
    w, h, sure = boyut_al(ffprobe, isler[0])
    klasor = CIKTI / "karsilastirma" / guvenli_ad(a.ad or orij.stem)
    klasor.mkdir(parents=True, exist_ok=True)
    anlar = [float(x) for x in a.anlar.split(",") if x.strip()] or [round(sure * k, 2) for k in (0.2, 0.5, 0.8)]

    if not a.video_yok:
        for isl, ad in zip(isler, adlar):
            hedef = klasor / f"{a.mod}_{isl.stem}.mkv"
            print(f"[video] {a.mod}: {isl.name} → {hedef.name}")
            video_uret(ffmpeg, orij, isl, w, h, a.mod, hedef, ad, a.sol_ad, a.kirp)

    for t in anlar:
        tam: list[Path] = []
        detay: list[Path] = []
        for etiket_ad, dosya in [("orijinal", orij), *[(p.stem, p) for p in isler]]:
            hedef = klasor / f"t{t:06.2f}_{etiket_ad}.png"
            kare_al(ffmpeg, dosya, t, w, h, hedef, None)
            tam.append(hedef)
            if a.kirp:
                d = klasor / f"t{t:06.2f}_{etiket_ad}_detay.png"
                kare_al(ffmpeg, dosya, t, w, h, d, a.kirp)
                detay.append(d)
        if detay:
            detay_blogu(ffmpeg, detay, klasor / f"t{t:06.2f}_DETAY_{'yan' if a.detay_yan else 'ust-orijinal'}.png",
                        [a.sol_ad, *adlar], a.detay_yan)
        print(f"[png] t={t}: {len(tam)} kare" + (" + detay bloğu" if detay else ""))
    print(f"\nÇıktılar: {klasor}")


if __name__ == "__main__":
    main()
