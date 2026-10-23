"""ffprobe/ffmpeg sarmalayıcıları: akış bilgisi, ses/altyazı izleri, kare-hassas klip kesme, ilerleme."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

from ortak.araclar import calistir, tam_hiz, zorunlu


def akis_bilgisi(ffprobe: str, dosya: Path) -> dict:
    r = calistir([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                  "stream=codec_name,profile,width,height,avg_frame_rate,r_frame_rate,pix_fmt,nb_frames,bit_rate"
                  ":format=duration,bit_rate", "-of", "json", str(dosya)])
    if r.returncode != 0:
        sys.exit(f"ffprobe başarısız:\n{r.stderr}")
    j = json.loads(r.stdout)
    s = j["streams"][0]
    pay, payda = (s.get("avg_frame_rate") or "0/1").split("/")
    fps = float(pay) / float(payda) if float(payda) else 0.0
    return {"kodek": s.get("codec_name"), "profil": s.get("profile"), "w": s.get("width"), "h": s.get("height"),
            "fps": fps, "pix_fmt": s.get("pix_fmt"), "sure": float(j["format"].get("duration") or 0)}


def renk_argumanlari(ffprobe: str, dosya: Path) -> list[str]:
    """Dosyanın renk etiketlerini ffmpeg çıktı seçeneklerine çevirir (bilinmeyenler için BT.709 / sınırlı aralık).

    Açıkça verilmezse ffmpeg 8, yuv420p10le → p010le gibi salt paketleme dönüşümünde matrisi BT.601 (bt470bg)
    olarak etiketliyor; değerler BT.709 kaldığı için oynatıcı yanlış matrisle çözüp pembe/kırmızı sahneleri
    koyulaştırıyordu (test edilen anime bölümünde yeşil kanal −7).
    """
    r = calistir([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                  "stream=color_space,color_primaries,color_transfer,color_range", "-of", "json", str(dosya)])
    s = (json.loads(r.stdout or "{}").get("streams") or [{}])[0]

    def deger(anahtar: str, varsayilan: str) -> str:
        v = s.get(anahtar)
        return v if v and v not in ("unknown", "reserved", "unspecified") else varsayilan
    return ["-colorspace", deger("color_space", "bt709"), "-color_primaries", deger("color_primaries", "bt709"),
            "-color_trc", deger("color_transfer", "bt709"), "-color_range", deger("color_range", "tv")]


def renk_setparams(renk: list[str]) -> str:
    """renk_argumanlari çıktısını setparams süzgecine çevirir (süzgeç zincirinin sonuna eklenir).

    ffmpeg 8'de -color_trc / -color_primaries çıkış bayrakları süzgeçten gelen karenin özelliklerini ezmiyor:
    kaynakta bu iki etiket yoksa (birçok web videosu) Video2X → ton sabitleme çıktısı 'unknown' kalıyordu.
    """
    d = dict(zip(renk[::2], renk[1::2]))
    return (f"setparams=range={d['-color_range']}:color_primaries={d['-color_primaries']}:"
            f"color_trc={d['-color_trc']}:colorspace={d['-colorspace']}")


def izleri_listele(ffprobe: str, dosya: Path) -> list[dict]:
    """Ses ve altyazı akışları: mutlak indeks, tür, kodek, dil, başlık, varsayılan mı."""
    r = calistir([ffprobe, "-v", "error", "-show_entries",
                  "stream=index,codec_type,codec_name,channels:stream_tags=language,title:stream_disposition=default",
                  "-of", "json", str(dosya)])
    izler = []
    for s in json.loads(r.stdout or "{}").get("streams", []):
        tur = {"audio": "ses", "subtitle": "altyazi"}.get(s.get("codec_type"))
        if not tur:
            continue
        etiket = s.get("tags", {})
        izler.append({"indeks": s["index"], "tur": tur, "kodek": s.get("codec_name") or "?",
                      "dil": etiket.get("language", "und"), "baslik": etiket.get("title", ""),
                      "kanal": s.get("channels"), "varsayilan": bool(s.get("disposition", {}).get("default"))})
    return izler


def iz_etiketi(iz: dict) -> str:
    parca = [f"#{iz['indeks']}", iz["dil"], iz["kodek"]]
    if iz.get("kanal"):
        parca.append(f"{iz['kanal']}ch")
    if iz["baslik"]:
        parca.append(f"“{iz['baslik']}”")
    if iz["varsayilan"]:
        parca.append("(varsayılan)")
    return " ".join(parca)


DIL_IPUCU = {"jpn": "jpn", "ja": "jpn", "jp": "jpn", "japanese": "jpn", "eng": "eng", "en": "eng",
             "english": "eng", "tur": "tur", "tr": "tur", "turkish": "tur", "turkce": "tur"}


def iz_secimi(deger: str) -> list[int] | None:
    """'hepsi' → None, 'yok' → [], '2,4' → [2, 4]"""
    if deger == "hepsi":
        return None
    if deger == "yok":
        return []
    try:
        return [int(x) for x in deger.split(",") if x.strip()]
    except ValueError:
        sys.exit(f"İz listesi anlaşılamadı: {deger} (hepsi | yok | 2,4)")


def izleri_birlestir(ffmpeg: str, ffprobe: str, video: Path, kaynak: Path | None, hedef: Path,
                     ses: list[int] | None = None, altyazi: list[int] | None = None, ekler: list[Path] = (),
                     baslangic: float | None = None, sure: float | None = None,
                     varsayilan_ses: int | None = None, varsayilan_altyazi: int | None = None) -> Path:
    """İşlenmiş videoya kaynaktaki seçili ses/altyazı izlerini ve harici dosyaları ekler; video yeniden kodlanmaz.

    ses/altyazi: kaynaktaki mutlak akış indeksleri; None = hepsi, [] = hiçbiri.
    varsayilan_*: kaynak indeksi (harici dosyalar için -1, -2, … sırayla); None = kaynaktaki işaretler korunur.
    Kısmi işlemede (baslangic/sure) kaynak izleri aynı aralıktan alınır; ses FLAC'a çevrilir (kopyalanan ses
    anahtar kareye kadar geri taşıp senkronu kaydırıyordu), altyazı kopyalanır.
    Tam işlemede kaynağın video başlangıç kayması (ör. 0.007 sn) videoya da uygulanır: ses aynı hizada kalır.
    """
    izler = izleri_listele(ffprobe, kaynak) if kaynak else []
    secili_ses = [i["indeks"] for i in izler if i["tur"] == "ses" and (ses is None or i["indeks"] in ses)]
    secili_alt = [i["indeks"] for i in izler if i["tur"] == "altyazi" and (altyazi is None or i["indeks"] in altyazi)]
    kismi = baslangic is not None or sure is not None

    def baslangic_zamani(dosya: Path) -> float:
        r = calistir([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=start_time",
                      "-of", "csv=p=0", str(dosya)])
        try:
            return float(r.stdout.strip())
        except ValueError:
            return 0.0

    # Videonun kendi başlangıç kayması sıfırlanır (daha önce birleştirilmiş, kaymalı bir dosya gelebilir;
    # ör. kesimden önce başlayan altyazı satırı yüzünden 0.48 sn), sonra tam işlemede kaynağınki uygulanır.
    kayma = (baslangic_zamani(kaynak) if kaynak and not kismi else 0.0) - baslangic_zamani(video)
    komut = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
    if abs(kayma) > 0.0005:
        komut += ["-itsoffset", f"{kayma:.6f}"]
    komut += ["-i", str(video)]
    eslem = ["-map", "0:v:0"]
    # max_interleave_delta 0: ffmpeg 8 varsayılanında (10 sn) seyrek altyazı izleri yüzünden tüm ses dosyanın başına
    # yazılıyordu (4K bölümde ilk 50 MB); oynatıcılar sarınca sesi bulamıyordu (mpv sessiz, MPC-BE sona atlıyor).
    kodlar = ["-c", "copy", "-max_interleave_delta", "0"]
    # Çıktıdaki sırayla: (tür, kaynak indeksi veya -n harici)
    cikis: list[tuple[str, int]] = []
    if kaynak and (secili_ses or secili_alt):
        if kismi:
            komut += ["-ss", f"{baslangic or 0:.3f}"] + (["-t", f"{sure:.3f}"] if sure else [])
        komut += ["-i", str(kaynak)]
        for i in secili_ses:
            eslem += ["-map", f"1:{i}"]
            cikis.append(("ses", i))
        for i in secili_alt:
            eslem += ["-map", f"1:{i}"]
            cikis.append(("altyazi", i))
        if kismi:
            kodlar += ["-c:a", "flac"]
    dil_ayar: list[str] = []
    girdi_no = 1 + (1 if kaynak and (secili_ses or secili_alt) else 0)
    for n, ek in enumerate(ekler, 1):
        komut += ["-i", str(ek)]
        ek_izler = izleri_listele(ffprobe, ek)
        dil = next((DIL_IPUCU[p] for p in reversed(ek.stem.lower().replace("_", ".").split("."))
                    if p in DIL_IPUCU), None)
        for iz in ek_izler:
            eslem += ["-map", f"{girdi_no}:{iz['indeks']}"]
            cikis.append((iz["tur"], -n))
            if dil and iz["dil"] == "und":
                tur_harf = "a" if iz["tur"] == "ses" else "s"
                sira = sum(1 for t, _ in cikis if t == iz["tur"]) - 1
                dil_ayar += [f"-metadata:s:{tur_harf}:{sira}", f"language={dil}"]
        girdi_no += 1
    # ASS altyazıların gömülü yazı tipleri. En sonda eşlenmeli: ffmpeg 8'de ek (attachment) akışlarından sonra
    # eşlenen akışların paketleri yanlış indekse gidiyor ("Received a packet for an attachment stream").
    if kaynak and secili_alt:
        eslem += ["-map", "1:t?"]

    disp: list[str] = []
    for tur, varsayilan in (("ses", varsayilan_ses), ("altyazi", varsayilan_altyazi)):
        if varsayilan is None:
            continue
        harf = "a" if tur == "ses" else "s"
        for sira, (_, kimlik) in enumerate([c for c in cikis if c[0] == tur]):
            disp += [f"-disposition:{harf}:{sira}", "default" if kimlik == varsayilan else "0"]
    if hedef.suffix.lower() != ".mkv":
        hedef = hedef.with_suffix(".mkv")
    komut += [*eslem, *kodlar, *dil_ayar, *disp, str(hedef)]
    r = calistir(komut)
    if r.returncode != 0:
        sys.exit(f"Ses/altyazı birleştirme başarısız:\n{r.stderr[-1500:]}")
    return hedef


def klip_kes(ffmpeg: str, dosya: Path, bas: float, sure: float, hedef: Path, mod: str = "kayipsiz") -> None:
    if mod == "kopya":
        # Hızlı ama başlangıç önceki anahtar kareye kayar
        vkod = ["-c", "copy", "-avoid_negative_ts", "make_zero"]
    else:
        # Kayıpsız x264: kodu çözülmüş kareler (artefaktlar dahil) birebir korunur, kesim kare-hassas.
        # Ses de yeniden kodlanır (FLAC): kopyalanan ses anahtar kareye kadar geriye taşar ve senkron kayar.
        vkod = ["-c:v", "libx264", "-qp", "0", "-preset", "veryfast", "-c:a", "flac"]
    komut = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{bas:.3f}", "-i", str(dosya),
             "-t", f"{sure:.3f}", "-map", "0:v:0", "-map", "0:a:0?", *vkod, str(hedef)]
    r = calistir(komut)
    if r.returncode != 0:
        sys.exit(f"ffmpeg klip kesme başarısız:\n{r.stderr}")


def ffmpeg_ilerlemeli(komut: list[str], toplam_kare: int, ad: str) -> tuple[int, str]:
    """ffmpeg'i çalıştırır; yüzde değiştikçe '[ilerleme] <ad> %.. kare a/b kalan ss:dd:sn' basar (arayüz okur).

    -loglevel error ile ffmpeg hiçbir şey yazmıyordu: 4K ton sabitleme uzun filmde ~1 saat sessiz kalıp takılmış
    gibi görünüyordu. -progress pipe:1 her ~0.5 sn 'frame=N' satırı verir; diğer satırlar hata metnidir.
    Dönüş: (çıkış kodu, hata metni).
    """
    p = subprocess.Popen([komut[0], "-progress", "pipe:1", "-nostats", *komut[1:]], stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    tam_hiz(p)
    t0, son_yuzde, hata = time.perf_counter(), -1, []
    for satir in p.stdout:  # type: ignore[union-attr]
        if m := re.match(r"frame=(\d+)", satir):
            kare = int(m[1])
            yuzde = min(99, kare * 100 // max(toplam_kare, 1))
            if yuzde != son_yuzde and kare:
                son_yuzde = yuzde
                kalan = int((time.perf_counter() - t0) / kare * max(toplam_kare - kare, 0))
                print(f"[ilerleme] {ad} %{yuzde} kare {kare}/{toplam_kare} kalan "
                      f"{kalan // 3600:02d}:{kalan % 3600 // 60:02d}:{kalan % 60:02d}", flush=True)
        elif not re.match(r"\w+=", satir):
            hata.append(satir.rstrip())
    p.wait()
    return p.returncode, "\n".join(hata)


def kare_sayisi(dosya: Path) -> int:
    b = akis_bilgisi(zorunlu("ffprobe"), dosya)
    return max(1, round(b["sure"] * b["fps"]))
