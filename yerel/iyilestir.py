"""Yerel video → deblock + 4K upscale (varsayılan) [+ RIFE ile fps artırma] → 10-bit NVENC.

Kullanım (tıklamalı arayüz: arayuz.bat):
    py -3.14 yerel\\iyilestir.py <dosya|URL> --tur anime [--hedef-fps 72|120] [--model cugan|esrgan|anime4k]
        [--ses 2] [--altyazi hepsi] [--varsayilan-ses 2] [--ek harici.tr.srt]
        [--baslangic SN --bitis SN] [--codec hevc|av1] [--cq 19]

Backend: Video2X 6 (tools\\video2x; anime) ve VapourSynth + vs-mlrt TensorRT (tools\\vapoursynth; film, hızlı
ara kare). Video izi önce tek başına üretilir, en sonda kaynaktaki seçili ses/altyazı
izleri (ve ASS yazı tipleri) yeniden kodlamadan eklenir; ses/altyazı kare üretimini etkilemez. Her çalıştırma
cikti\\iyilestir_log.csv'ye hız / süre / tahmin satırı ekler.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ortak.araclar import CIKTI, ESKI_UZANTILAR, KLIPLER, TOOLS, calistir, guvenli_ad, tam_hiz, url_mi, zorunlu  # noqa: E402
from ortak.sahne import kesmeleri_bul  # noqa: E402
from ortak.vs import VSPIPE, vs_ortam  # noqa: E402
from ortak.video import (akis_bilgisi, ffmpeg_ilerlemeli, iz_secimi, izleri_birlestir,  # noqa: E402
                         kare_sayisi, klip_kes, renk_argumanlari, renk_setparams)

VIDEO2X = TOOLS / "video2x" / "video2x.exe"
HEDEF_H = 2160
LOG = CIKTI / "iyilestir_log.csv"

# Real-CUGAN models-se: 2x'te 0..3 gürültü seviyesi var, 3x/4x'te yalnız 0 ve 3.
# Modelin "conservative" (-1) varyantı diskte var ama Video2X 6.4 -1'i kabul etmiyor.
CUGAN_SEVIYE = {2: {0, 1, 2, 3}, 3: {0, 3}, 4: {0, 3}}


def olcek_sec(h: int, istenen: str) -> int:
    if istenen != "auto":
        return int(istenen)
    for s in (2, 3, 4):
        if h * s >= HEDEF_H * 0.95:
            return s
    return 4


# Film: vs-mlrt TensorRT ile DPIR deblock → realesr-general-x4v3. --deblock-guc 0-3 → DPIR gücü (0-255 ölçeği)
FILM_VPY = Path(__file__).resolve().parent / "film_hat.vpy"
RIFE_VPY = Path(__file__).resolve().parent / "rife_hat.vpy"
DPIR_GUC = {0: 0, 1: 15, 2: 30, 3: 50}
# 1 (DPIR 15): ölçümde (VMAF NEG / PSNR) güç arttıkça kaynağa yakınlık düşüyor, göz testinde doku plastikleşiyor
FILM_VARSAYILAN_DEBLOCK = 1


def islemci_argumanlari(a: argparse.Namespace, olcek: int, w: int, h: int) -> tuple[list[str], str]:
    """Video2X işlemci argümanları ve çıktı dosyası için kısa model etiketi."""
    if a.model == "general":
        seviye = FILM_VARSAYILAN_DEBLOCK if a.deblock_guc is None else a.deblock_guc
        return [], f"general4x_dpir{DPIR_GUC[seviye]}"
    if a.model == "anime4k":
        # libplacebo hedef boyutu doğrudan alır; en-boy oranını koru
        hw = round(w * HEDEF_H / h / 2) * 2
        return ["-p", "libplacebo", "--libplacebo-shader", "anime4k-v4-a+a", "-w", str(hw), "-h", str(HEDEF_H)], "anime4k"
    if a.model == "esrgan":
        model = "realesr-animevideov3" if a.tur == "anime" else "realesrgan-plus"
        if model == "realesrgan-plus" and olcek != 4:
            print("[uyarı] realesrgan-plus yalnız 4x modeline sahip; ölçek 4 yapıldı (çıktı 4K'dan büyük olabilir)")
            olcek = 4
        if a.deblock_guc is not None:
            print("[uyarı] Real-ESRGAN'da gürültü/deblock ayarı yok; --deblock-guc yok sayıldı")
        return ["-p", "realesrgan", "--realesrgan-model", model, "-s", str(olcek)], f"esrgan{olcek}x"
    seviye = 3 if a.deblock_guc is None else a.deblock_guc
    if seviye not in CUGAN_SEVIYE[olcek]:
        yeni = 3 if seviye > 0 else 0
        print(f"[uyarı] Real-CUGAN {olcek}x'te deblock {seviye} yok; {yeni} kullanılıyor")
        seviye = yeni
    # "=" ile ver: "-n -1" yazılırsa Video2X -1'i yeni bir seçenek sanıyor
    return (["-p", "realcugan", "--realcugan-model", "models-se", "-s", str(olcek), f"--noise-level={seviye}"],
            f"cugan{olcek}x_dn{seviye}")


def eski_kaynak_hazirla(ffmpeg: str, ffprobe: str, girdi: Path, hedef: Path, taramali: str,
                        zorla: bool = False) -> Path | None:
    """DVD/SD kaynağı modele uygun hâle getirir; gerekmiyorsa None. Kayıpsız x264 (qp 0), yalnız video.

    - Taramalı (interlaced) görüntü: model iki alanı tek kare sanıp tarak dişlerini büyütür. idet ile ortadan 600
      kare ölçülür. NTSC film DVD'si (3:2 pulldown, tekrarlanan alanlar çok) → fieldmatch+decimate ile 23.976p'ye
      geri alınır; gerçek taramalı çekim → bwdif (iki-kat: her alan bir kare, 25i → 50p gerçek hareket).
    - Kare olmayan piksel (DVD SAR 64:45, 16:15, 32:27, 8:9): genişlik kare piksele çevrilir (720x576 16:9 →
      1024x576); yoksa çıktı basık/uzun görünür.
    - Renk: SD kaynak etiketsizse BT.601'dir; HD/4K çıktı BT.709 kabul edildiği için matris dönüştürülür.
    """
    r = calistir([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                  "stream=width,height,sample_aspect_ratio,color_space:format=duration", "-of", "json", str(girdi)])
    j = json.loads(r.stdout or "{}")
    s, sure = j["streams"][0], float(j.get("format", {}).get("duration") or 0)
    w, h = s["width"], s["height"]
    filtreler: list[str] = []
    notlar: list[str] = []

    if taramali != "kapali":
        r = calistir([ffmpeg, "-hide_banner", "-nostats", "-ss", f"{sure * 0.3:.1f}", "-i", str(girdi),
                      "-map", "0:v:0", "-vf", "idet", "-frames:v", "600", "-an", "-f", "null", "-"])
        # idet özeti iki kez basılıyor (ilki filtre kurulurken, hep 0); sonuncusu geçerli
        m = re.findall(r"Multi frame detection: TFF:\s*(\d+)\s+BFF:\s*(\d+)\s+Progressive:\s*(\d+)", r.stderr)
        t = re.findall(r"Repeated Fields: Neither:\s*(\d+)\s+Top:\s*(\d+)\s+Bottom:\s*(\d+)", r.stderr)
        tff, bff, prog = map(int, m[-1]) if m else (0, 0, 0)
        tekrar = (int(t[-1][1]) + int(t[-1][2])) / max(1, sum(map(int, t[-1]))) if t else 0.0
        if h == 480 and tekrar > 0.15:
            filtreler.append("fieldmatch=order=auto:combmatch=full,bwdif=deint=interlaced,decimate")
            notlar.append(f"NTSC film (3:2 pulldown, tekrar %{tekrar * 100:.0f}) → 23.976p'ye geri alındı")
        elif tff + bff > prog:
            cift = taramali == "iki-kat"
            filtreler.append(f"bwdif=mode={'send_field' if cift else 'send_frame'}:parity=auto:deint=all")
            notlar.append(f"taramalı (TFF {tff} / BFF {bff} / ilerlemeli {prog}) → birleştirildi"
                          + (", kare hızı 2 kat (her alan bir kare)" if cift else ""))

    sar = s.get("sample_aspect_ratio") or "1:1"
    pay, payda = (int(x) for x in sar.split(":")) if ":" in sar else (1, 1)
    if pay and payda and pay != payda:
        yeni_w = round(w * pay / payda / 2) * 2
        filtreler.append(f"scale={yeni_w}:{h}:flags=lanczos")
        notlar.append(f"piksel oranı {sar} → kare piksel {yeni_w}x{h}")
    filtreler.append("setsar=1")

    renk = renk_argumanlari(ffprobe, girdi)
    # fcc: bazı DVD kaydedicilerin etiketi; BT.601 ile neredeyse aynı matris
    if h <= 576 and s.get("color_space") in (None, "unknown", "smpte170m", "bt470bg", "fcc"):
        filtreler.append(f"colorspace=all=bt709:iall={'bt601-6-625' if h > 480 else 'bt601-6-525'}:"
                         "irange=tv:range=tv:fast=1")
        renk = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]
        notlar.append("renk BT.601 → BT.709")

    if len(filtreler) == 1 and not zorla:
        return None
    print(f"[hazirlik] eski/SD kaynak hazırlanıyor: {'; '.join(notlar) or 'MPEG-2 kabı MKV yapılıyor'}")
    # Çıkış kare sayısı: send_field 2 kat, decimate 5'te 4
    oran = 2 if "send_field" in filtreler[0] else 0.8 if "decimate" in filtreler[0] else 1
    kod, hata = ffmpeg_ilerlemeli(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-fflags", "+genpts", "-i", str(girdi),
         "-map", "0:v:0", "-vf", ",".join(filtreler), "-c:v", "libx264", "-qp", "0", "-preset", "veryfast",
         "-pix_fmt", "yuv420p", *renk, "-an", "-sn", str(hedef)], round(kare_sayisi(girdi) * oran), "hazırlık")
    if kod != 0:
        sys.exit(f"Hazırlık başarısız:\n{hata[-1500:]}")
    return hedef


def encoder_argumanlari(codec: str, cq: int, model: str) -> list[str]:
    # ncnn işlemcileri (CUGAN/ESRGAN) RGB çıktı verir; x2rgb10le ile RGB→YUV dönüşümü GPU'da (NVENC) yapılır.
    # p010le'ye CPU'da çevirmek kare başına ~0.2 sn ekliyordu (10 sn klipte 2.3 → 4.0 fps).
    pix = "p010le" if model == "anime4k" else "x2rgb10le"
    ortak = ["--pix-fmt", pix, "-e", "preset=p6", "-e", "rc=vbr", "-e", f"cq={cq}", "-e", "tune=hq"]
    if codec == "av1":
        return ["-c", "av1_nvenc", *ortak]
    return ["-c", "hevc_nvenc", "-e", "profile=main10", *ortak]


def ton_sabitle(ffmpeg: str, orijinal: Path, islenmis: Path, hedef: Path, codec: str, cq: int,
                izgara: tuple[int, int] = (32, 18), zaman_kare: int = 9, ara: bool = False) -> None:
    """İşlenmiş videonun kaba tonunu (düşük frekans) kare kare orijinalinkine eşitler.

    çıktı = işlenmiş + bulanık(orijinal) − bulanık(işlenmiş)
    Model karanlık karelerde tonu daha çok kaydırıyor (CUGAN dn3'te Y −4.5'e kadar); bu kayma sahneye göre
    değiştiği için "HDR açılmış gibi" ani ton artışı olarak hissediliyor. Fark 32x18'lik ızgarada (4K'da 120 px)
    hesaplanır ve `zaman_kare` kare boyunca ortalanır: ton kayması yavaş ve geniş alanlı bir olay, hareketli
    nesneler/altyazı ise küçük ve hızlı. 96x54 ızgara + zaman ortalamasız sürüm, altyazı ve yürüyen karakterlerin
    çevresinde düzeltmeyi her karede oynatıp yerel titreme ekliyordu (ölçüm: hücre başına +4'e kadar).
    Her iki girdinin zaman damgası kare sırasından yeniden üretilir (N/FRAME_RATE): kesilmiş klip 0.042 sn'den,
    Video2X çıktısı 0'dan başlıyor ve ms yuvarlamaları farklı; hizalanmazsa düzeltme kayar ve hayalet bırakır.
    """
    c = akis_bilgisi(zorunlu("ffprobe"), islenmis)
    W, H = c["w"], c["h"]
    # 4x model SD kaynağı 4K'dan büyük çıkarabiliyor (576 → 2304; TV'ler 3840x2160'dan büyüğünü oynatmayabilir):
    # en-boy korunarak 3840x2160'a sığdırılır
    k = min(1.0, 3840 / W, HEDEF_H / H)
    sigdir = f",scale={round(W * k / 2) * 2}:{round(H * k / 2) * 2}:flags=spline" if k < 1 else ""
    gw, gh = izgara
    renk = renk_argumanlari(zorunlu("ffprobe"), islenmis)
    # Fark küçük ızgarada hesaplanır, yalnız sonuç büyütülür; toplama doğrudan 10-bit 4:2:0'da yapılır.
    # 4K'da yuv444p16'ya çevirip iki girdiyi bulanıklaştırmak 10 sn klipte 64 sn sürüyordu, bu hâli 19 sn.
    fg = (f"[0:v]setpts=N/FRAME_RATE/TB,scale={gw}:{gh}:flags=area,format=yuv420p10le[ol];"
          "[1:v]setpts=N/FRAME_RATE/TB,format=yuv420p10le,split[p1][p2];"
          f"[p1]scale={gw}:{gh}:flags=area[pl];"
          "[ol][pl]blend=all_expr='clip(A-B+512,0,1023)',"
          f"tmix=frames={zaman_kare},scale={W}:{H}:flags=bicubic[d];"
          f"[p2][d]blend=all_expr='clip(A+B-512,0,1023)'{sigdir},{renk_setparams(renk)}[v]")
    enc = "av1_nvenc" if codec == "av1" else "hevc_nvenc"
    komut = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i", str(orijinal),
             "-hwaccel", "cuda", "-i", str(islenmis),
             "-filter_complex", fg, "-map", "[v]", *renk,
             "-c:v", enc, "-preset", "p6", "-rc", "vbr", "-cq", str(cq), "-tune", "hq",
             # Video2X dosya sonunda B-frame sırasını boşaltmıyor: B-frame'li NVENC girdinin son 2 karesini
             # okumuyor (sentetik test: 48 kare → ×5'te 236 yerine 226). Sonraki aşamaya gidecek dosyada kapat.
             # (Video2X'in kendi NVENC çıktısında --max-b-frames 0 "invalid param" veriyor; orada kayıp kalıyor,
             # yalnız videonun en sonunda 2 kare, senkronu etkilemez.)
             *(["-bf", "0"] if ara else []), str(hedef)]
    kod, hata = ffmpeg_ilerlemeli(komut, kare_sayisi(islenmis), "ton")
    if kod != 0:
        sys.exit(f"Ton sabitleme başarısız:\n{hata[-1500:]}")


def kesmede_beklet(ffmpeg: str, ffprobe: str, kaynak: Path, rife: Path, hedef: Path, kesmeler: list[int],
                   carpan: int, codec: str, cq: int, ara: bool = False) -> None:
    """RIFE çıktısında her kesmenin üzerindeki (carpan-1) eriyik ara kareyi atar; fps filtresi boşlukları önceki
    kareyle (eski sahnenin son karesi) doldurur. Kare sayısı ve ses senkronu değişmez.

    Video2X RIFE çıktısında kaynak kare i, çıktı karesi carpan*i'dir ((N-1)*m+1 kare; sentetik testle doğrulandı).
    Kesme c (yeni sahnenin ilk kaynak karesi) için atılacaklar: carpan*(c-1)+1 … carpan*(c-1)+carpan-1.
    """
    r = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate",
                        "-of", "csv=p=0", str(kaynak)], capture_output=True, text=True)
    pay, payda = (int(x) for x in (r.stdout.strip() or "24000/1001").split("/"))
    hiz = f"{pay * carpan}/{payda}"
    kare_sn = payda / (pay * carpan)
    araliklar = [f"between(t,{(carpan * (c - 1) + 0.5) * kare_sn:.6f},{(carpan * (c - 1) + carpan - 0.5) * kare_sn:.6f})"
                 for c in kesmeler if c > 0]
    # Uzun ifadeyi 50'lik select'lere böl (t zaman damgası select'ler boyunca değişmez). ffmpeg 8.1 ifade
    # ayrıştırıcısı 100 terimlik '+' zincirini reddediyor ("Error while parsing expression"; 99 geçiyor).
    secimler = [f"select='not({'+'.join(araliklar[i:i + 50])})'" for i in range(0, len(araliklar), 50)]
    renk = renk_argumanlari(ffprobe, rife)
    vf = ",".join(["setpts=N/FRAME_RATE/TB", *secimler, f"fps={hiz}", renk_setparams(renk)])
    # 1000+ kesmede ifade Windows komut satırı sınırını (32k) aşıyor: filtre dosyadan okunur (ffmpeg -/vf)
    vf_dosya = hedef.with_name(hedef.stem + "_vf.txt")
    vf_dosya.write_text(vf, encoding="ascii")
    enc = "av1_nvenc" if codec == "av1" else "hevc_nvenc"
    komut = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-hwaccel", "cuda", "-i", str(rife),
             "-/vf", str(vf_dosya), "-map", "0:v", *renk, "-pix_fmt", "p010le", "-c:v", enc, "-preset", "p6", "-rc", "vbr", "-cq", str(cq), "-tune", "hq",
             *(["-bf", "0"] if ara else []), str(hedef)]
    print(f"[sahne] {len(araliklar)} kesmede eriyik ara kareler eski sahnenin son karesiyle değiştiriliyor…")
    kod, hata = ffmpeg_ilerlemeli(komut, kare_sayisi(rife), "sahne")
    vf_dosya.unlink(missing_ok=True)
    if kod != 0:
        sys.exit(f"Kesme düzeltmesi başarısız:\n{hata[-1500:]}")


def film_hatti_calistir(ffmpeg: str, girdi: Path, hedef: Path, deblock: float, codec: str, cq: int,
                        kare_sayisi: int, ara: bool = False) -> tuple[float | None, int | None]:
    """vspipe (yerel\\film_hat.vpy: DPIR + realesr-general-x4v3) → ffmpeg NVENC. İlerlemeyi '[ilerleme]' basar."""
    ortam = vs_ortam(PIXEL_FILM_KAYNAK=str(girdi), PIXEL_FILM_DEBLOCK=f"{deblock:g}", PIXEL_FILM_HEDEF="3840x2160")
    print(f"[film] DPIR deblock {deblock:g} + realesr-general-x4v3 (vs-mlrt TensorRT); "
          "yeni boyutta ilk çalıştırmada motor derlenir (~1 dk)", flush=True)
    return vs_hatti_calistir(ffmpeg, FILM_VPY, ortam, girdi, hedef, codec, cq, kare_sayisi, "upscale", 1, ara,
                             "Film hattı")


def hizli_rife_calistir(ffmpeg: str, girdi: Path, hedef: Path, carpan: int, model: str, kesme_json: Path | None,
                        codec: str, cq: int, kare_sayisi: int, ara: bool = False) -> tuple[float | None, int | None]:
    """vspipe (yerel\\rife_hat.vpy: ara kareler 1080p'de RIFE TensorRT, gerçek kareler dokunulmadan) → NVENC.
    Kesmelerde önceki kare tekrarlanır; ayrı 'sahne' turu gerekmez."""
    # Bu makinede (TensorRT 10.16, RTX 50) 2. uygulamada doğru ara kare verdiği doğrulanan model 4.26;
    # 4.25-lite ve 4.6 bozuk (dikey çizgili) çıktı veriyordu
    vs_model = "v4_26"
    if model != "rife-v4.26":
        print(f"[fps] not: hızlı yöntemde {model} desteklenmiyor, rife-v4.26 kullanılıyor", flush=True)
    ortam = vs_ortam(PIXEL_RIFE_KAYNAK=str(girdi), PIXEL_RIFE_CARPAN=str(carpan), PIXEL_RIFE_MODEL=vs_model,
                     PIXEL_RIFE_ARA="1080", **({"PIXEL_RIFE_KESMELER": str(kesme_json)} if kesme_json else {}))
    print(f"[fps] hızlı yöntem: RIFE {vs_model} TensorRT, ara kareler 1080p'de, gerçek kareler dokunulmadan; "
          "ilk çalıştırmada motor derlenir (~1-4 dk)", flush=True)
    return vs_hatti_calistir(ffmpeg, RIFE_VPY, ortam, girdi, hedef, codec, cq, kare_sayisi * carpan, "fps", carpan,
                             ara, "Hızlı ara kare hattı")


def vs_hatti_calistir(ffmpeg: str, vpy: Path, ortam: dict[str, str], girdi: Path, hedef: Path, codec: str, cq: int,
                      kare_sayisi: int, asama: str, carpan: int, ara: bool, ad: str) -> tuple[float | None, int | None]:
    """vspipe → ffmpeg NVENC; vspipe'ın ilerlemesini '[ilerleme] <asama>' satırı olarak basar."""
    enc = "av1_nvenc" if codec == "av1" else "hevc_nvenc"
    vspipe = subprocess.Popen([str(VSPIPE), "-c", "y4m", "-p", str(vpy), "-"], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, env=ortam)
    # y4m başlığındaki kare hızı yuvarlanabiliyor (6817/284 → 24/1); 2 saatlik filmde ~1 sn ses kayması demek.
    # Kaynağın r_frame_rate'i (× çarpan) girişe zorla verilir.
    r = subprocess.run([zorunlu("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=r_frame_rate", "-of", "csv=p=0", str(girdi)], capture_output=True, text=True)
    pay, payda = (int(x) for x in (r.stdout.strip() or "24000/1001").split("/"))
    hiz = f"{pay * carpan}/{payda}"
    # Renk etiketi setparams ile: renk bilgisi olmayan y4m'de -colorspace/-color_range çıkış bayrakları 4K'da CPU
    # dönüşümü ekleyip hattı 26 → 12 kare/sn'ye düşürüyordu, üstelik primaries/trc "unknown" kalıyordu
    kodla = subprocess.Popen([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "yuv4mpegpipe", "-r", hiz,
                              "-i", "-",
                              "-vf", "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
                              "-pix_fmt", "p010le", "-c:v", enc, "-preset", "p6", "-rc", "vbr",
                              "-cq", str(cq), "-tune", "hq", *(["-bf", "0"] if ara else []), str(hedef)],
                             stdin=vspipe.stdout, stderr=subprocess.PIPE)
    tam_hiz(vspipe)
    tam_hiz(kodla)
    vspipe.stdout.close()  # type: ignore[union-attr]
    t0 = time.perf_counter()
    son_yuzde, kare, hata = -1, 0, []
    # vspipe -p ilerlemeyi "Frame: 12/240 (3.1 fps)" biçiminde \r ile yazar
    tampon = b""
    while True:
        parca = vspipe.stderr.read(256)  # type: ignore[union-attr]
        if not parca:
            break
        tampon += parca
        *satirlar, tampon = tampon.replace(b"\r", b"\n").split(b"\n")
        for ham in satirlar:
            satir = ham.decode("utf-8", "replace")
            if m := re.match(r"Output (\d+) (?:video )?frames", satir):
                # Kesin sayı: ilerleme satırı aralıklarla basıldığından son kareyi göstermeden bitebiliyor
                # (240 karelik klipte günlüğe 237-239 yazılıyordu; çıktıda kare eksiği yok)
                kare = int(m[1])
            elif m := re.search(r"Frame: (\d+)/(\d+)", satir):
                kare, toplam = int(m[1]), int(m[2]) or kare_sayisi
                yuzde = kare * 100 // max(toplam, 1)
                if yuzde != son_yuzde:
                    son_yuzde = yuzde
                    gecen = time.perf_counter() - t0
                    kalan = int(gecen / max(kare, 1) * (toplam - kare))
                    print(f"[ilerleme] {asama} %{yuzde} kare {kare}/{toplam} kalan "
                          f"{kalan // 3600:02d}:{kalan % 3600 // 60:02d}:{kalan % 60:02d}", flush=True)
            elif satir.strip() and "API3" not in satir and "is using API3" not in satir:
                hata.append(satir)
    vspipe.wait()
    _, kodla_hata = kodla.communicate()
    if vspipe.returncode != 0 or kodla.returncode != 0:
        sys.exit(f"{ad} başarısız:\n" + "\n".join(hata[-15:]) + "\n" +
                 kodla_hata.decode("utf-8", "replace")[-1500:])
    sure = time.perf_counter() - t0
    return (kare / sure if sure else None), kare


def nvidia_cihaz() -> str:
    """Video2X'in Vulkan cihaz sırası Windows'un o anki GPU tercihine göre değişebiliyor ('-d 0' bir çalıştırmada
    Intel iGPU'ya düştü, RIFE ~20 kat yavaşladı) → ayrık GPU'nun sırasını her çalıştırmada adından bul."""
    liste = subprocess.run([str(VIDEO2X), "--list-devices"], capture_output=True, text=True, encoding="utf-8",
                           errors="replace").stdout
    for m in re.finditer(r"^(\d+)\. (.+)\n\s*Type: (.+)$", liste, re.M):
        if "NVIDIA" in m[2] or "Discrete" in m[3]:
            print(f"[cihaz] Video2X cihaz {m[1]}: {m[2].strip()}", flush=True)
            return m[1]
    sys.exit("Video2X ayrık (NVIDIA) GPU bulamadı:\n" + liste)


def video2x_calistir(komut: list[str], asama: str = "") -> tuple[float | None, int | None, str]:
    """Video2X'i çalıştırır; ilerlemeyi yüzde değiştikçe '[ilerleme]' satırı olarak basar (arayüz bunu okur),
    özet satırlarını yakalar."""
    print("[video2x] " + " ".join(f'"{x}"' if " " in x else x for x in komut[1:]), flush=True)
    p = subprocess.Popen(komut, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace")
    tam_hiz(p)
    satirlar: list[str] = []
    son_yuzde = -1
    # Metin kipinde \r de satır sonu sayılır: Video2X'in yerinde güncellenen ilerleme satırı tek tek gelir
    for satir in p.stdout:  # type: ignore[union-attr]
        m = re.search(r"frame=(\d+)/(\d+) \(([\d.]+)%\).*?remaining=([\d:]+)", satir)
        if m:
            yuzde = int(float(m.group(3)))
            if yuzde != son_yuzde:
                son_yuzde = yuzde
                print(f"[ilerleme] {asama} %{yuzde} kare {m.group(1)}/{m.group(2)} kalan {m.group(4)}", flush=True)
            continue
        satirlar.append(satir.rstrip())
        if re.search(r"error|critical|Total|Average|processed", satir, re.I):
            print("  " + satir.rstrip(), flush=True)
    p.wait()
    metin = "\n".join(satirlar)
    fps = re.search(r"Average processing speed:\s*([\d.]+)", metin)
    kare = re.search(r"Total frames processed:\s*(\d+)", metin)
    # Video2X 6.4 başarıda da sıfır olmayan çıkış kodu dönebiliyor; başarıyı özet satırından anla
    if "processed successfully" not in metin:
        sys.exit("Video2X başarısız:\n" + "\n".join(satirlar[-15:]))
    return (float(fps.group(1)) if fps else None), (int(kare.group(1)) if kare else None), metin


def log_yaz(satir: dict) -> None:
    yeni = not LOG.exists()
    with LOG.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(satir.keys()))
        if yeni:
            w.writeheader()
        w.writerow(satir)


def main() -> None:
    ap = argparse.ArgumentParser(description="Offline video iyileştirici (deblock + 4K upscale)")
    ap.add_argument("kaynak", help="yerel video dosyası")
    ap.add_argument("--tur", choices=["film", "anime"], required=True)
    ap.add_argument("--model", choices=["esrgan", "general", "cugan", "anime4k"],
                    help="varsayılan: anime → esrgan (göz testinde seçildi: blokları temizliyor, ton kayması en az, "
                         "en hızlı); film → general (DPIR deblock + realesr-general-x4v3, vs-mlrt TensorRT)")
    ap.add_argument("--deblock-guc", type=int, choices=[0, 1, 2, 3],
                    help="blok temizleme: 0 yok, 3 en güçlü. general (film): DPIR 0/15/30/50 (varsayılan 1); "
                         "cugan: gürültü seviyesi (varsayılan 3)")
    ap.add_argument("--olcek", default="auto", choices=["auto", "2", "3", "4"])
    ap.add_argument("--baslangic", type=float, help="kısmi işleme başlangıcı (sn)")
    ap.add_argument("--bitis", type=float, help="kısmi işleme bitişi (sn)")
    ap.add_argument("--codec", choices=["hevc", "av1"], default="hevc")
    ap.add_argument("--cq", type=int, default=19, help="NVENC sabit kalite (düşük = daha kaliteli/büyük)")
    ap.add_argument("--hedef-fps", type=float,
                    help="RIFE ile hedef kare hızı; kaynağın tam katına yuvarlanır (23.976 → 72=×3, 120=×5, 144=×6)")
    ap.add_argument("--rife-model", default="rife-v4.26",
                    choices=["rife-v4.26", "rife-v4.25", "rife-v4.25-lite", "rife-v4.6"])
    ap.add_argument("--fps-yontem", choices=["hizli", "kalite"], default="hizli",
                    help="hizli: ara kareler 1080p'de RIFE TensorRT, gerçek kareler dokunulmaz (4K bölüm ~1 sa). "
                         "kalite: Video2X RIFE tam çözünürlükte (4K bölüm ×3 ~12 sa)")
    ap.add_argument("--kesme-serbest", action="store_true",
                    help="sahne kesmesi düzeltmesini kapat (kesmelerde iki sahne ara karelerle eriyerek geçer)")
    ap.add_argument("--sira", choices=["upscale-once", "fps-once"], default="upscale-once",
                    help="upscale-once: RIFE temiz 4K karelerle çalışır (kaliteli). fps-once: upscale "
                         "kat sayısı kadar fazla kare işler ama RIFE 1080p'de hızlıdır")
    ap.add_argument("--sadece-kalite", action="store_true", help="yalnız deblock+upscale")
    ap.add_argument("--sadece-fps", action="store_true", help="yalnız RIFE (kaynak çözünürlüğünde)")
    ap.add_argument("--ton-serbest", action="store_true",
                    help="ton sabitlemeyi kapat (modelin sahneye göre değişen ton kaymasını olduğu gibi bırakır)")
    ap.add_argument("--ham-sakla", action="store_true", help="ton sabitlemeden önceki ham çıktıyı silme")
    ap.add_argument("--taramali", choices=["iki-kat", "ayni", "kapali"], default="iki-kat",
                    help="taramalı (interlaced, DVD/TV kaydı) kaynakta alan birleştirme: iki-kat = her alan bir kare "
                         "(25i → 50 fps gerçek hareket, işleme 2 kat uzun), ayni = kare hızı aynı, kapali = dokunma")
    ap.add_argument("--ses", default="hepsi",
                    help="kaynaktan alınacak ses izleri: hepsi | yok | virgüllü akış indeksleri (ör. 2)")
    ap.add_argument("--altyazi", default="hepsi", help="altyazı izleri: hepsi | yok | indeksler (ör. 4,5)")
    ap.add_argument("--varsayilan-ses", type=int, help="oynatıcının ilk açacağı ses izi (kaynak indeksi)")
    ap.add_argument("--varsayilan-altyazi", type=int, help="varsayılan altyazı izi (kaynak indeksi)")
    ap.add_argument("--ek", action="append", default=[],
                    help="harici ses/altyazı dosyası (tekrarlanabilir); adında .tr/.en/.jp varsa dil etiketlenir")
    ap.add_argument("-o", "--cikti", help="çıktı dosyası (varsayılan: cikti\\<ad>_<tur>_<model>.mkv)")
    a = ap.parse_args()
    sys.stdout.reconfigure(line_buffering=True)  # arayüz çıktıyı satır satır okuyor
    tam_hiz()  # arayüz simge durumundayken Windows'un bu süreci yavaşlatmasın (alt süreçlere ayrıca uygulanır)
    if url_mi(a.kaynak):
        sys.exit("İnternet adresi desteklenmiyor: yalnız yerel video dosyaları işlenir. Videoyu önce indirip "
                 "dosya yolunu ver.")

    ses_izleri, altyazi_izleri = iz_secimi(a.ses), iz_secimi(a.altyazi)
    ekler = [Path(e).resolve() for e in a.ek]
    for e in ekler:
        if not e.is_file():
            sys.exit(f"Harici dosya yok: {e}")
    if a.sadece_fps and a.sadece_kalite:
        sys.exit("--sadece-fps ve --sadece-kalite birlikte kullanılamaz")
    if a.sadece_fps and not a.hedef_fps:
        sys.exit("--sadece-fps için --hedef-fps gerekli")
    if a.model is None and not a.sadece_fps:
        a.model = "general" if a.tur == "film" else "esrgan"
    if not VIDEO2X.is_file():
        sys.exit(f"Video2X yok: {VIDEO2X}")
    ffmpeg, ffprobe = zorunlu("ffmpeg"), zorunlu("ffprobe")

    kaynak = Path(a.kaynak).resolve()
    if not kaynak.is_file():
        sys.exit(f"Dosya yok: {kaynak}")
    tam_bilgi = akis_bilgisi(ffprobe, kaynak)
    ad = guvenli_ad(kaynak.stem)

    girdi = kaynak
    kismi = ""
    gecici = KLIPLER / "_gecici"
    gecici.mkdir(parents=True, exist_ok=True)
    kesit: Path | None = None  # kısmi işlemde kesilen ara klip; iş bitince silinir
    if a.baslangic is not None or a.bitis is not None:
        bas = a.baslangic or 0.0
        bit = a.bitis if a.bitis is not None else tam_bilgi["sure"]
        if bit <= bas:
            sys.exit("--bitis, --baslangic'tan büyük olmalı")
        girdi = kesit = gecici / f"{ad}_{int(bas)}-{int(bit)}.mkv"
        print(f"[kes] {bas:.1f}-{bit:.1f} sn kayıpsız kesiliyor → {girdi.name}")
        klip_kes(ffmpeg, kaynak, bas, bit - bas, girdi)
        kismi = f"_{int(bas)}-{int(bit)}s"

    hazir = eski_kaynak_hazirla(ffmpeg, ffprobe, girdi, gecici / f"{ad}{kismi}_hazir.mkv", a.taramali,
                                zorla=kaynak.suffix.lower() in ESKI_UZANTILAR)
    if hazir:
        girdi = hazir

    b = akis_bilgisi(ffprobe, girdi)
    asamalar: list[str] = []
    if not a.sadece_fps:
        asamalar.append("upscale")
    carpan = 1
    if a.hedef_fps:
        carpan = max(2, round(a.hedef_fps / b["fps"]))
        print(f"[fps] {b['fps']:.3f} × {carpan} = {b['fps'] * carpan:.2f} fps (istenen {a.hedef_fps:g})")
        if not a.sadece_kalite:
            asamalar.insert(0 if a.sira == "fps-once" else len(asamalar), "fps")

    asama_etiketi: dict[str, str] = {}
    if "upscale" in asamalar:
        _, asama_etiketi["upscale"] = islemci_argumanlari(a, olcek_sec(b["h"], a.olcek), b["w"], b["h"])
    if "fps" in asamalar:
        asama_etiketi["fps"] = (f"{a.rife_model.replace('rife-v', 'rife')}x{carpan}"
                                + ("-hizli" if a.fps_yontem == "hizli" else ""))
    etiket = "_".join(asama_etiketi[s] for s in asamalar)
    cikti = Path(a.cikti).resolve() if a.cikti else CIKTI / f"{ad}{kismi}_{a.tur}_{etiket}_{a.codec}.mkv"
    cikti.parent.mkdir(parents=True, exist_ok=True)
    ara_klasor = cikti.parent / "_ara"
    print(f"[bilgi] girdi {b['w']}x{b['h']} @ {b['fps']:.3f} fps, {b['sure']:.1f} sn → aşamalar: {' → '.join(asamalar)}")

    t0 = time.perf_counter()
    olcum: list[str] = []
    gecici_dosyalar: list[Path] = []
    simdiki = girdi
    for i, asama in enumerate(asamalar):
        son = i == len(asamalar) - 1
        # Ara aşamalar yüksek kalitede yazılır (cq 12): bir sonraki aşama tekrar sıkıştırma artefaktı görmesin
        cq = a.cq if son else 12
        # Son aşama da önce yalnız video olarak yazılır; izler en sonda eklenir
        ara_klasor.mkdir(exist_ok=True)
        # Ara dosyanın adı yalnız o aşamaya kadarki ayarları taşır: fps yöntemi/kodek değişse de biten upscale
        # yeniden kullanılır
        hedef = (ara_klasor / f"{cikti.stem}_video.mkv" if son else
                 ara_klasor / f"{ad}{kismi}_{a.tur}_{'_'.join(asama_etiketi[s] for s in asamalar[:i + 1])}_{asama}.mkv")
        # Aşama bitince .tamam yazılır; yarıda kesilen iş aynı ayarla yeniden başlarsa biten ara aşamalar atlanır.
        # Son aşama atlanmaz: CQ dosya adında yok, farklı CQ ile yeniden başlatılmış olabilir.
        tamam = hedef.with_suffix(".tamam")
        gecici_dosyalar += [hedef, tamam]
        print(f"[asama] {i + 1}/{len(asamalar)} {asama}")
        if not son and hedef.is_file() and tamam.is_file():
            print(f"[asama] {asama} önceki çalıştırmada tamamlanmış, atlanıyor → {hedef.name}")
            olcum.append(f"{asama}: önceki çalıştırmadan")
            simdiki = hedef
            continue
        sb = akis_bilgisi(ffprobe, simdiki)
        t1 = time.perf_counter()
        if asama == "upscale":
            isl, _ = islemci_argumanlari(a, olcek_sec(sb["h"], a.olcek), sb["w"], sb["h"])
            ham = hedef if a.ton_serbest else hedef.with_name(hedef.stem + "_ham.mkv")
            if a.model == "general":
                seviye = FILM_VARSAYILAN_DEBLOCK if a.deblock_guc is None else a.deblock_guc
                fps, kare = film_hatti_calistir(ffmpeg, simdiki, ham, DPIR_GUC[seviye], a.codec, cq,
                                                round(sb["sure"] * sb["fps"]), ara=not a.ton_serbest or not son)
            else:
                fps, kare, _ = video2x_calistir([str(VIDEO2X), "-i", str(simdiki), "-o", str(ham), "-d", nvidia_cihaz(),
                                                 *isl, *encoder_argumanlari(a.codec, cq, a.model)], asama)
            if not a.ton_serbest:
                print("[ton] kaba ton girdiye sabitleniyor…")
                ton_sabitle(ffmpeg, simdiki, ham, hedef, a.codec, cq, ara=not son)
                if not a.ham_sakla:
                    ham.unlink()
        else:
            # Video2X'in kendi sahne algılaması kapalı (-t 100): tek eşiği ya karanlık kesmeyi kaçırıyor ya da
            # hızlı hareketi kesme sayıyor. Kesmeler ortak.sahne ile kaynakta bulunup sonradan düzeltilir.
            kesmeler: list[int] = []
            kesme_json = ara_klasor / f"{girdi.stem}_kesmeler.json"
            if not a.kesme_serbest:
                kesmeler = kesmeleri_bul(ffmpeg, girdi, kesme_json)
                print(f"[sahne] {len(kesmeler)} kesme bulundu; bu noktalarda ara kare yerine önceki kare bekletilecek")
            if a.fps_yontem == "hizli":
                fps, kare = hizli_rife_calistir(ffmpeg, simdiki, hedef, carpan, a.rife_model,
                                                kesme_json if kesmeler else None, a.codec, cq,
                                                round(sb["sure"] * sb["fps"]), ara=not son)
            else:
                rife = ["-p", "rife", "--rife-model", a.rife_model, "-m", str(carpan), "-t", "100"]
                if sb["h"] > 1440:
                    rife.append("--rife-uhd")
                rife_hedef = hedef if not kesmeler else hedef.with_name(hedef.stem + "_rife.mkv")
                # Kesme düzeltmesinden önceki RIFE çıktısı da işaretlenir: sonraki adım çökerse uzun RIFE tekrarlanmaz
                rife_tamam = rife_hedef.with_suffix(".tamam")
                if kesmeler and rife_hedef.is_file() and rife_tamam.is_file():
                    print(f"[asama] RIFE önceki çalıştırmada tamamlanmış, atlanıyor → {rife_hedef.name}")
                    fps, kare = None, None
                else:
                    fps, kare, _ = video2x_calistir([str(VIDEO2X), "-i", str(simdiki), "-o", str(rife_hedef),
                                                     "-d", nvidia_cihaz(), *rife,
                                                     *encoder_argumanlari(a.codec, 12 if kesmeler else cq, "rife")],
                                                    asama)
                if kesmeler:
                    rife_tamam.touch()
                    kesmede_beklet(ffmpeg, ffprobe, girdi, rife_hedef, hedef, kesmeler, carpan, a.codec, cq,
                                   ara=not son)
                    rife_hedef.unlink()
                    rife_tamam.unlink()
        tamam.touch()
        olcum.append(f"{asama}: {time.perf_counter() - t1:.1f} sn ({kare} kare, işleme {fps:.2f} kare/sn)" if fps
                     else f"{asama}: {time.perf_counter() - t1:.1f} sn ({kare} kare)")
        simdiki = hedef
    print("[iz] ses/altyazı izleri ekleniyor (yeniden kodlama yok)…")
    kismi_aralik = {"baslangic": a.baslangic or 0.0, "sure": b["sure"]} if kismi else {}
    cikti = izleri_birlestir(ffmpeg, ffprobe, simdiki, kaynak, cikti, ses_izleri, altyazi_izleri, ekler,
                             varsayilan_ses=a.varsayilan_ses, varsayilan_altyazi=a.varsayilan_altyazi,
                             **kismi_aralik)
    sure = time.perf_counter() - t0
    for f in [*gecici_dosyalar, *(p for p in (hazir, kesit) if p)]:
        f.unlink(missing_ok=True)

    c = akis_bilgisi(ffprobe, cikti)
    oran = tam_bilgi["sure"] / b["sure"] if b["sure"] else 0
    bolum_oran = 24 * 60 / b["sure"] if b["sure"] else 0
    print(f"\n[sonuç] {cikti}")
    print(f"  çıktı: {c['w']}x{c['h']} @ {c['fps']:.2f} fps {c['kodek']} {c['pix_fmt']}, "
          f"{cikti.stat().st_size / 1e6:.1f} MB")
    print(f"  toplam {sure:.1f} sn — " + "; ".join(olcum))
    print(f"  tam kaynak ({tam_bilgi['sure'] / 60:.1f} dk) için tahmini süre: {sure * oran / 3600:.2f} saat")
    print(f"  24 dk'lık bir bölüm için tahmini süre: {sure * bolum_oran / 3600:.1f} saat")
    log_yaz({"zaman": f"{datetime.now():%Y-%m-%d %H:%M}", "kaynak": kaynak.name, "kismi": kismi.strip("_") or "tam",
             "tur": a.tur, "asamalar": "→".join(asamalar), "etiket": etiket, "codec": a.codec, "cq": a.cq,
             "girdi": f"{b['w']}x{b['h']}@{b['fps']:.2f}", "cikti": f"{c['w']}x{c['h']}@{c['fps']:.2f}",
             "sure_sn": f"{sure:.1f}", "olcum": " | ".join(olcum),
             "bolum24dk_tahmin_saat": f"{sure * bolum_oran / 3600:.2f}", "dosya": cikti.name})
    if "upscale" in asamalar and c["h"] != HEDEF_H:
        print(f"  not: çıktı yüksekliği {c['h']} (hedef {HEDEF_H}); TV son ölçeklemeyi yapar.")


if __name__ == "__main__":
    main()
