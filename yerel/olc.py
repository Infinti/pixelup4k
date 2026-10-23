r"""Objektif kalite ölçümü: işlenmiş videoları orijinal (referans) klibe karşı VMAF, VMAF NEG, PSNR ve SSIM ile ölçer.

    py -3.14 yerel\olc.py <referans> <islenmis> [<islenmis2> ...] [--taban <bozuk>] [--etiket A B ...]
        [--aralik 4-6 ...] [--ad isim] [--dil tr|en]

- İşlenmiş video referansın çözünürlüğüne lanczos ile indirilir (4K → 1080p); ölçüm referans boyutunda yapılır.
- --taban: işlenmemiş (bozuk) kaynak; aynı ölçüm ona da yapılır ve tabloda/grafikte taban çizgisi olarak durur.
- --aralik: tüm klibin yanında bu saniye aralıkları için ayrı ortalamalar (ör. hareketli bölüm). Saniyeler
  referansın zaman çizelgesindedir.
- İki VMAF modeli aynı geçişte: vmaf_v0.6.1 ve vmaf_v0.6.1neg. NEG ("no enhancement gain") keskinleştirme ve kontrast
  artışını ödüllendirmez; yalnız kaynağa yakınlığı ölçer. İkisi arasındaki fark, kazancın ne kadarının "algılanan
  netlik", ne kadarının gerçek onarım olduğunu gösterir.
- Kare hizalaması: iki tarafın zaman damgası kare sırasından yeniden üretilir (başlangıç zamanı farkları,
  0.042 sn / 0 sn gibi, eşleşmeyi kaydırmasın). Ardından ±3 karelik kayma PSNR ile denenir, en iyi eşleşen kullanılır
  (ör. kesim bir kare erken başladıysa). İşlenmiş video kaynağın tam katı fps'deyse (RIFE ×3 → 72 fps) yalnız
  gerçek kareler (her k'ıncı) ölçülür.
- ffmpeg libvmaf ile derlenmiş olmalı (kurulum.ps1'in kurduğu gyan.dev 8.1.1 essentials derlemesinde var).
- Çıktı: cikti\olcum\<ad>\olcum.md (tablolar), olcum.json, vmaf_ortalama.png ve vmaf_kare.png (matplotlib varsa).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ortak.araclar import CIKTI, calistir, guvenli_ad, zorunlu  # noqa: E402
from ortak.video import akis_bilgisi  # noqa: E402

KAYMA_ARALIK = 3
KAYMA_KARE = 48  # kayma denemesi ilk bu kadar karede, küçük boyutta yapılır
# libvmaf JSON'undaki kare metrikleri → sonuç anahtarları
METRIKLER = {"vmaf": "vmaf", "vmaf_neg": "vmaf_neg", "psnr_y": "psnr_y", "float_ssim": "ssim"}

METIN = {
    "tr": {"dosya": "video", "coz": "çözünürlük", "bit": "bit hızı", "kare": "ölçülen kare",
           "vmaf": "VMAF", "neg": "VMAF NEG", "vmaf_min": "VMAF en düşük", "psnr": "PSNR-Y (dB)", "ssim": "SSIM",
           "taban": "taban", "tum": "Tüm klip", "aralik": "Bölüm {a}–{b} sn",
           "baslik": "Ortalama VMAF ve VMAF NEG (0–100) — referans: orijinal klip",
           "kare_baslik": "Kare kare VMAF", "x": "süre (sn)", "y": "puan",
           "referans": "Referans: `{ad}` ({n} kare). VMAF modelleri vmaf_v0.6.1 ve vmaf_v0.6.1neg (NEG: keskinleştirme/"
                        "kontrast artışını ödüllendirmez). İşlenmiş videolar referans çözünürlüğüne lanczos ile indirildi.",
           "bit_not": "Bit hızı yalnız bilgi amaçlıdır: çıktı 4K/10-bit kodlandığı için büyüktür, kaliteyle karşılaştırılmaz."},
    "en": {"dosya": "video", "coz": "resolution", "bit": "bitrate", "kare": "frames",
           "vmaf": "VMAF", "neg": "VMAF NEG", "vmaf_min": "VMAF min", "psnr": "PSNR-Y (dB)", "ssim": "SSIM",
           "taban": "baseline", "tum": "Whole clip", "aralik": "Section {a}–{b} s",
           "baslik": "Mean VMAF and VMAF NEG (0–100) — reference: original clip",
           "kare_baslik": "Per-frame VMAF", "x": "time (s)", "y": "score",
           "referans": "Reference: `{ad}` ({n} frames). VMAF models vmaf_v0.6.1 and vmaf_v0.6.1neg (NEG does not reward "
                        "sharpening/contrast gain). Processed videos downscaled to the reference resolution with lanczos.",
           "bit_not": "Bitrate is for information only: the output is 4K/10-bit and therefore larger; it is not a "
                      "quality comparison."},
}


def kare_hizi(ffprobe: str, dosya: Path) -> tuple[int, int]:
    r = calistir([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate",
                  "-of", "csv=p=0", str(dosya)])
    pay, payda = (int(x) for x in (r.stdout.strip() or "24000/1001").split("/"))
    return pay, payda


def secim_adimi(ref_hiz: tuple[int, int], hiz: tuple[int, int]) -> int:
    """İşlenmiş kare hızı referansın tam katıysa k (yalnız gerçek kareler ölçülür), değilse 1."""
    oran = (hiz[0] * ref_hiz[1]) / (hiz[1] * ref_hiz[0])
    k = round(oran)
    if k >= 1 and abs(oran - k) < 1e-3:
        return k
    sys.exit(f"Kare hızı referansın tam katı değil ({oran:.3f}×); kareler eşleştirilemez.")


def paket_sayisi(ffprobe: str, dosya: Path) -> int:
    """Video karesi sayısı (paket sayımı: çözmeden, hızlı)."""
    r = calistir([ffprobe, "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                  "stream=nb_read_packets", "-of", "csv=p=0", str(dosya)])
    return int(r.stdout.strip().split(",")[0])


def suzgec(w: int, h: int, adim: int, kayma: int, ref_mi: bool, kare: int | None = None) -> str:
    """Bir girdinin zinciri: zaman damgasını kare sırasından üret, gerçek kareleri seç, kaymayı uygula, boyutla."""
    parca = []
    if adim > 1:
        parca.append(f"select='not(mod(n,{adim}))'")
    # kayma > 0: işlenmiş i. kare ↔ referans i+kayma. kareyle eşleşir → referanstan baştaki kareler atılır
    at = kayma if ref_mi else -kayma
    if at > 0:
        parca.append(f"trim=start_frame={at}")
    if kare:
        parca.append(f"trim=end_frame={kare}")
    parca += ["setpts=N/FRAME_RATE/TB" if adim == 1 else f"setpts=N/(FRAME_RATE/{adim})/TB",
              f"scale={w}:{h}:flags=lanczos", "format=yuv420p10le"]
    return ",".join(parca)


def kayma_bul(ffmpeg: str, ref: Path, isl: Path, adim: int, ref_w: int, ref_h: int) -> int:
    """±KAYMA_ARALIK karelik kaymaları ilk KAYMA_KARE karede 480 px genişlikte PSNR ile dener."""
    w, h = 480, max(2, round(480 * ref_h / ref_w / 2) * 2)
    en_iyi, en_iyi_psnr = 0, -1.0
    for k in range(-KAYMA_ARALIK, KAYMA_ARALIK + 1):
        fg = (f"[0:v]{suzgec(w, h, 1, k, True)},trim=end_frame={KAYMA_KARE}[r];"
              f"[1:v]{suzgec(w, h, adim, k, False)},trim=end_frame={KAYMA_KARE}[i];[i][r]psnr")
        r = calistir([ffmpeg, "-hide_banner", "-nostdin", "-i", str(ref), "-i", str(isl), "-lavfi", fg, "-f", "null", "-"])
        m = re.search(r"PSNR .*? average:([\d.]+|inf)", r.stderr)
        psnr = float("inf") if m and m[1] == "inf" else float(m[1]) if m else -1.0
        if psnr > en_iyi_psnr:
            en_iyi, en_iyi_psnr = k, psnr
    return en_iyi


def olc(ffmpeg: str, ffprobe: str, ref: Path, isl: Path, klasor: Path, ad: str) -> dict:
    rb = akis_bilgisi(ffprobe, ref)
    ib = akis_bilgisi(ffprobe, isl)
    adim = secim_adimi(kare_hizi(ffprobe, ref), kare_hizi(ffprobe, isl))
    w, h = rb["w"], rb["h"]
    kayma = kayma_bul(ffmpeg, ref, isl, adim, w, h)
    log = f"{ad}.json"
    # Ölçülecek kare sayısı önceden hesaplanıp iki taraf da açıkça kesilir. libvmaf'ın shortest=1'i B-frame'li
    # mp4'te dosya sonu sinyali son kareyle aynı zamana düşünce son kareyi ölçmüyordu (240 karelik klipte 239).
    n_ref = paket_sayisi(ffprobe, ref) - max(kayma, 0)
    n_isl = -(-paket_sayisi(ffprobe, isl) // adim) - max(-kayma, 0)
    n = min(n_ref, n_isl)
    # libvmaf: ilk girdi bozulmuş (işlenmiş), ikinci girdi referans. Model listesi tek tırnak içinde ve içindeki
    # ':' kaçışlı; aksi hâlde 'name' libvmaf'ın kendi seçeneği sanılıyor ("Option not found").
    fg = (f"[1:v]{suzgec(w, h, adim, kayma, False, n)}[i];[0:v]{suzgec(w, h, 1, kayma, True, n)}[r];"
          f"[i][r]libvmaf=log_fmt=json:log_path={log}:n_threads={os.cpu_count() or 4}:ts_sync_mode=nearest:"
          "model='version=vmaf_v0.6.1\\:name=vmaf|version=vmaf_v0.6.1neg\\:name=vmaf_neg':"
          "feature=name=psnr|name=float_ssim")
    print(f"[olc] {isl.name}: {ib['w']}x{ib['h']} → {w}x{h}" + (f", her {adim}. kare" if adim > 1 else "")
          + (f", kayma {kayma:+d} kare" if kayma else ""), flush=True)
    r = calistir([ffmpeg, "-hide_banner", "-nostdin", "-i", str(ref), "-i", str(isl), "-lavfi", fg, "-f", "null", "-"],
                 cwd=str(klasor))
    if r.returncode != 0 or not (klasor / log).is_file():
        if re.search(r"No such filter: '?libvmaf", r.stderr):
            sys.exit("Bu ffmpeg libvmaf içermiyor. kurulum.ps1'in kurduğu tools\\ffmpeg'i kullan ya da libvmaf'lı bir "
                     "derleme (ör. gyan.dev 'essentials' veya 'full') kur.")
        sys.exit(f"VMAF ölçümü başarısız ({isl.name}):\n{r.stderr[-1500:]}")
    j = json.loads((klasor / log).read_text(encoding="utf-8"))
    kareler = {ad_: [f["metrics"][k] for f in j["frames"]] for k, ad_ in METRIKLER.items()}
    ref_fps = rb["fps"]
    return {"dosya": isl.name, "w": ib["w"], "h": ib["h"], "fps": ib["fps"], "adim": adim,
            "kayma": kayma, "kare": len(j["frames"]), "ref_kare": paket_sayisi(ffprobe, ref),
            "bit_hizi_kbps": round(isl.stat().st_size * 8 / ib["sure"] / 1000) if ib["sure"] else None,
            # Kare zamanı referansın zaman çizelgesinde (aralıklar böyle tanımlı)
            "kare_sn": [(i + max(kayma, 0)) / ref_fps for i in range(len(j["frames"]))],
            "kareler": kareler}


def ozet(s: dict, bas: float | None = None, bit: float | None = None) -> dict:
    """Kare değerlerinin ortalaması (libvmaf'ın 'mean' havuzlamasıyla aynı); bas/bit verilirse yalnız o aralık."""
    sec = [i for i, t in enumerate(s["kare_sn"]) if (bas is None or t >= bas) and (bit is None or t < bit)]
    o = {k: sum(v[i] for i in sec) / len(sec) for k, v in s["kareler"].items()} if sec else {}
    o["vmaf_min"] = min(s["kareler"]["vmaf"][i] for i in sec) if sec else float("nan")
    o["kare"] = len(sec)
    return o


def tablolar(sonuclar: list[dict], araliklar: list[tuple[float, float]], m: dict) -> str:
    def ad(s: dict) -> str:
        return f"{s['etiket']} ({m['taban']})" if s["taban"] else s["etiket"]

    md = [f"### {m['tum']}", "",
          f"| {m['dosya']} | {m['coz']} | {m['bit']}¹ | {m['vmaf']} | {m['neg']} | {m['vmaf_min']} | {m['psnr']} | "
          f"{m['ssim']} | {m['kare']} |", "|---|---|--:|--:|--:|--:|--:|--:|--:|"]
    for s in sonuclar:
        o = s["ozet"]
        kare = f"{s['kare']}/{s['ref_kare']}" + (f", {s['kayma']:+d}" if s["kayma"] else "")
        md.append(f"| {ad(s)} | {s['w']}x{s['h']} | {s['bit_hizi_kbps']:,} kb/s | **{o['vmaf']:.2f}** | "
                  f"**{o['vmaf_neg']:.2f}** | {o['vmaf_min']:.2f} | {o['psnr_y']:.2f} | {o['ssim']:.4f} | {kare} |")
    md += ["", f"¹ {m['bit_not']}", ""]
    for (a, b), anahtar in zip(araliklar, (f"{a:g}-{b:g}" for a, b in araliklar)):
        md += [f"### {m['aralik'].format(a=f'{a:g}', b=f'{b:g}')}", "",
               f"| {m['dosya']} | {m['vmaf']} | {m['neg']} | {m['vmaf_min']} | {m['psnr']} | {m['ssim']} | {m['kare']} |",
               "|---|--:|--:|--:|--:|--:|--:|"]
        for s in sonuclar:
            o = s["araliklar"][anahtar]
            md.append(f"| {ad(s)} | **{o['vmaf']:.2f}** | **{o['vmaf_neg']:.2f}** | {o['vmaf_min']:.2f} | "
                      f"{o['psnr_y']:.2f} | {o['ssim']:.4f} | {o['kare']} |")
        md.append("")
    return "\n".join(md)


# Grafik renkleri: dataviz referans paleti (açık yüzey). Taban nötr gri, işlenmişler kategorik sırayla.
YUZEY, METIN1, METIN2, IZGARA, VURGU = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df", "#efeeea"
TABAN_RENK = "#8f8e88"
SERI_RENK = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]


def _eksen(ax, m: dict) -> None:
    ax.set_ylim(0, 100)
    ax.set_ylabel(m["y"])
    ax.grid(axis="y", color=IZGARA, linewidth=0.8, zorder=0)
    for k in ("top", "right", "left"):
        ax.spines[k].set_visible(False)
    ax.tick_params(axis="both", length=0)


def grafikler(sonuclar: list[dict], araliklar: list[tuple[float, float]], klasor: Path, m: dict) -> list[Path]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("[olc] grafik için matplotlib gerekli (isteğe bağlı): py -3.14 -m pip install --user matplotlib")
        return []
    plt.rcParams.update({"font.family": "Segoe UI", "font.size": 10, "axes.edgecolor": IZGARA,
                         "axes.labelcolor": METIN2, "xtick.color": METIN2, "ytick.color": METIN2,
                         "text.color": METIN1, "figure.facecolor": YUZEY, "axes.facecolor": YUZEY})

    # 1) Ortalama VMAF ve VMAF NEG: gruplu çubuk (renk = metrik; videolar x ekseninde)
    fig, ax = plt.subplots(figsize=(7.5, 4.2), dpi=160)
    gen = 0.36
    for j, (anahtar, ad, renk) in enumerate((("vmaf", m["vmaf"], SERI_RENK[0]), ("vmaf_neg", m["neg"], SERI_RENK[1]))):
        xs = [i + (j - 0.5) * (gen + 0.03) for i in range(len(sonuclar))]
        cubuk = ax.bar(xs, [s["ozet"][anahtar] for s in sonuclar], width=gen, color=renk, label=ad, zorder=2)
        for c in cubuk:
            ax.text(c.get_x() + c.get_width() / 2, c.get_height() + 1.2, f"{c.get_height():.1f}", ha="center",
                    va="bottom", color=METIN1, fontsize=9, fontweight="semibold")
    ax.set_xticks(range(len(sonuclar)), [s["etiket"] + (f"\n({m['taban']})" if s["taban"] else "") for s in sonuclar])
    _eksen(ax, m)
    ax.legend(loc="upper left", bbox_to_anchor=(0, 1.0), frameon=False, ncol=2, fontsize=9)
    ax.set_title(m["baslik"], loc="left", color=METIN1, fontsize=11, fontweight="semibold", pad=12)
    fig.tight_layout()
    cubuk_png = klasor / "vmaf_ortalama.png"
    fig.savefig(cubuk_png, facecolor=YUZEY)
    plt.close(fig)

    # 2) Kare kare VMAF: çizgi; --aralik bölgeleri açık zeminle işaretlenir
    renk, n = [], 0
    for s in sonuclar:
        renk.append(TABAN_RENK if s["taban"] else SERI_RENK[n % len(SERI_RENK)])
        n += 0 if s["taban"] else 1
    fig, ax = plt.subplots(figsize=(7.5, 3.6), dpi=160)
    for a, b in araliklar:
        ax.axvspan(a, b, color=VURGU, zorder=0, linewidth=0)
        ax.text((a + b) / 2, 97, m["aralik"].format(a=f"{a:g}", b=f"{b:g}"), ha="center", va="top", color=METIN2,
                fontsize=8.5)
    for s, r in zip(sonuclar, renk):
        ax.plot(s["kare_sn"], s["kareler"]["vmaf"], color=r, linewidth=1.4, label=s["etiket"],
                zorder=3 if not s["taban"] else 2)
    _eksen(ax, m)
    ax.set_xlim(0, max(s["kare_sn"][-1] for s in sonuclar))
    ax.set_xlabel(m["x"])
    ax.set_ylabel("VMAF")
    # matplotlib lejantı sütun sütun doldurur; birden çok satır olursa sıra satır satır okunsun diye yeniden dizilir
    tut, ad = ax.get_legend_handles_labels()
    ncol = min(5, len(tut))
    nsat = -(-len(tut) // ncol)
    sira = [s * ncol + c for c in range(ncol) for s in range(nsat) if s * ncol + c < len(tut)]
    ax.legend([tut[i] for i in sira], [ad[i] for i in sira], loc="lower left", frameon=False, ncol=ncol, fontsize=8.5)
    ax.set_title(m["kare_baslik"], loc="left", color=METIN1, fontsize=11, fontweight="semibold", pad=10)
    fig.tight_layout()
    kare_png = klasor / "vmaf_kare.png"
    fig.savefig(kare_png, facecolor=YUZEY)
    plt.close(fig)
    return [cubuk_png, kare_png]


def aralik_coz(metin: str) -> tuple[float, float]:
    try:
        a, b = (float(x) for x in metin.replace(",", ".").split("-"))
    except ValueError:
        sys.exit(f"--aralik anlaşılamadı: {metin} (ör. 4-6)")
    if b <= a:
        sys.exit(f"--aralik bitişi başlangıçtan büyük olmalı: {metin}")
    return a, b


def main() -> None:
    ap = argparse.ArgumentParser(description="VMAF / VMAF NEG / PSNR / SSIM ölçümü (referansa karşı)")
    ap.add_argument("referans", help="orijinal (bozulmamış) klip")
    ap.add_argument("islenmis", nargs="+", help="ölçülecek işlenmiş videolar")
    ap.add_argument("--taban", help="işlenmemiş (bozuk) kaynak; taban çizgisi olarak ölçülür")
    ap.add_argument("--etiket", nargs="+", help="tablo/grafik adları (önce --taban'ınki, sonra işlenmişler sırayla)")
    ap.add_argument("--aralik", nargs="+", default=[], help="ayrıca ölçülecek saniye aralıkları, ör. 4-6")
    ap.add_argument("--ad", help="çıktı klasörü adı (varsayılan: referansın adı)")
    ap.add_argument("--dil", choices=["tr", "en"], default="tr", help="tablo ve grafik dili")
    a = ap.parse_args()
    sys.stdout.reconfigure(line_buffering=True)

    ffmpeg, ffprobe = zorunlu("ffmpeg"), zorunlu("ffprobe")
    ref = Path(a.referans).resolve()
    dosyalar = ([Path(a.taban).resolve()] if a.taban else []) + [Path(p).resolve() for p in a.islenmis]
    for p in [ref, *dosyalar]:
        if not p.is_file():
            sys.exit(f"Dosya yok: {p}")
    if a.etiket and len(a.etiket) != len(dosyalar):
        sys.exit(f"--etiket sayısı ({len(a.etiket)}) video sayısıyla ({len(dosyalar)}) aynı olmalı")
    araliklar = [aralik_coz(x) for x in a.aralik]
    m = METIN[a.dil]
    klasor = CIKTI / "olcum" / guvenli_ad(a.ad or ref.stem)
    klasor.mkdir(parents=True, exist_ok=True)

    sonuclar = []
    for i, p in enumerate(dosyalar):
        s = olc(ffmpeg, ffprobe, ref, p, klasor, f"vmaf_{i}_{guvenli_ad(p.stem)}")
        s["taban"] = bool(a.taban) and i == 0
        s["etiket"] = a.etiket[i] if a.etiket else p.stem
        s["ozet"] = ozet(s)
        s["araliklar"] = {f"{x:g}-{y:g}": ozet(s, x, y) for x, y in araliklar}
        o = s["ozet"]
        print(f"       VMAF {o['vmaf']:.2f} · NEG {o['vmaf_neg']:.2f} (en düşük {o['vmaf_min']:.2f}) · "
              f"PSNR-Y {o['psnr_y']:.2f} dB · SSIM {o['ssim']:.4f} · {s['kare']} kare", flush=True)
        sonuclar.append(s)

    md = m["referans"].format(ad=ref.name, n=sonuclar[0]["ref_kare"]) + "\n\n" + tablolar(sonuclar, araliklar, m)
    (klasor / "olcum.md").write_text(md, encoding="utf-8")
    (klasor / "olcum.json").write_text(json.dumps({"referans": ref.name, "sonuclar": sonuclar}, ensure_ascii=False,
                                                  indent=1), encoding="utf-8")
    pngler = grafikler(sonuclar, araliklar, klasor, m)
    print("\n" + md)
    print(f"[tamam] {klasor}" + (f" ({', '.join(p.name for p in pngler)})" if pngler else ""))


if __name__ == "__main__":
    main()
