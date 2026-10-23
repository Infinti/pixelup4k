r"""Yerel video iyileştirici için küçük tıklamalı arayüz (tkinter). Başlatmak için kökteki arayuz.bat.

Tek sekme, "Video iyileştir": yerel\iyilestir.py'yi seçilen ayarlarla çalıştırır (varsayılan: yalnız 4K upscale);
ses/altyazı izleri seçilip harici dosya eklenebilir. Çalıştırılan tam komut günlüğe yazılır; aynısı PowerShell'de
(proje klasöründe) olduğu gibi kullanılabilir.
"""
from __future__ import annotations

import ctypes
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ortak.araclar import CIKTI, KOK, VIDEO_UZANTILARI, arac_bul, ps_komut, saniye_coz, tam_hiz  # noqa: E402
from ortak.video import akis_bilgisi, iz_etiketi, izleri_listele  # noqa: E402

YEREL = Path(__file__).resolve().parent
# pythonw ile açılınca alt süreçler için konsollu python.exe kullanılır (çıktı okunabilsin)
PYTHON = str(Path(sys.executable).with_name("python.exe")) if sys.executable.lower().endswith("pythonw.exe") \
    else sys.executable
VIDEO_TURLERI = [("Video", " ".join(f"*{u}" for u in VIDEO_UZANTILARI)), ("Tümü", "*.*")]
IZ_TURLERI = [("Ses / altyazı", "*.ass *.ssa *.srt *.vtt *.sup *.mka *.flac *.opus *.aac *.ac3 *.m4a *.mp3 *.wav"),
              ("Tümü", "*.*")]
FPS_SECENEK = {"Değiştirme (kaynak fps)": None, "72 fps (×3)": 72, "120 fps (×5)": 120, "144 fps (×6)": 144}
FILM_MODEL = "DPIR + ESRGAN general (film)"
MODEL_SECENEK = {"ESRGAN animevideov3 (anime)": "esrgan",
                 FILM_MODEL: "general",
                 "Real-CUGAN (anime, deblock ayarlı)": "cugan", "Anime4K (hızlı, hafif)": "anime4k"}
ADIM_ADI = {"ton": "ton sabitleme", "sahne": "sahne kesmesi düzeltmesi",
            "hazırlık": "eski kaynak hazırlığı"}
# Kısa tutulur (kutuda kesilmesin); ayrıntı README'de: hızlı = ara kareler 1080p'de, 4K bölümde ~1 sa;
# kaliteli = Video2X tam 4K, ~12 sa
ARA_KARE_SECENEK = {"Hızlı (1080p ara kare)": "hizli", "Kaliteli (tam 4K, ~10× yavaş)": "kalite"}
# Kaba süre tahmini için kaynak kare başına saniye (RTX 5070 Laptop, arka planda başka GPU işi varken ölçüldü)
SN_UPSCALE = {"esrgan": 0.42, "general": 0.27, "cugan": 0.55, "anime4k": 0.12}  # general: DPIR açıkken (süre güçten bağımsız; 1920x800)
SN_ARA_KARE_4K = 0.31   # Video2X RIFE 4.26, 4K, üretilen her ara kare için
SN_ARA_KARE_1080 = 0.12  # tahmin (ölçülmedi)
# Hızlı yöntem, ÇIKTI karesi başına: 4K ×3'te 21 kare/sn (arkada Video2X işi GPU'yu paylaşırken)
SN_HIZLI_4K = 0.045
SN_HIZLI_1080 = 0.015  # tahmin (ölçülmedi)


def sure_yaz(sn: float) -> str:
    sn = int(sn)
    return f"{sn // 3600} sa {sn % 3600 // 60:02d} dk" if sn >= 3600 else f"{sn // 60} dk {sn % 60:02d} sn"


class IzSecici(ttk.LabelFrame):
    """Kaynaktaki ses/altyazı izleri için onay kutuları, varsayılan iz seçimi ve harici dosyalar."""

    def __init__(self, ana: tk.Misc, baslik: str) -> None:
        super().__init__(ana, text=baslik, padding=8)
        self.izler: list[dict] = []
        self.secim: dict[int, tk.BooleanVar] = {}
        self.ekler: list[tuple[Path, str]] = []  # (yol, tür: ses/altyazi)
        self.liste = ttk.Frame(self)
        self.liste.grid(row=0, column=0, columnspan=3, sticky="w")
        self.bos = ttk.Label(self.liste, text="Kaynak seçilince izler burada listelenir.", foreground="gray")
        self.bos.grid(sticky="w")
        ttk.Label(self, text="Varsayılan ses:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.v_ses = ttk.Combobox(self, state="readonly", width=60)
        self.v_ses.grid(row=1, column=1, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(self, text="Varsayılan altyazı:").grid(row=2, column=0, sticky="w")
        self.v_alt = ttk.Combobox(self, state="readonly", width=60)
        self.v_alt.grid(row=2, column=1, columnspan=2, sticky="w")
        ttk.Button(self, text="Harici ses/altyazı ekle…", command=self.ek_sec).grid(row=3, column=0, sticky="w",
                                                                                   pady=(6, 0))
        ttk.Button(self, text="Harici listeyi temizle", command=self.ek_temizle).grid(row=3, column=1, sticky="w",
                                                                                    pady=(6, 0))
        self.ek_etiket = ttk.Label(self, text="", foreground="gray")
        self.ek_etiket.grid(row=4, column=0, columnspan=3, sticky="w")
        self.ses_harita: dict[str, int | None] = {}
        self.alt_harita: dict[str, int | None] = {}

    def yukle(self, dosya: Path, tur: str = "anime") -> None:
        for w in self.liste.winfo_children():
            w.destroy()
        self.secim.clear()
        self.izler = []
        self.izler = izleri_listele(arac_bul("ffprobe") or "ffprobe", dosya)
        if not self.izler:
            ttk.Label(self.liste, text="Kaynakta ses/altyazı izi yok.", foreground="gray").grid(sticky="w")
        for n, iz in enumerate(self.izler):
            v = tk.BooleanVar(value=True)
            self.secim[iz["indeks"]] = v
            ad = ("Ses      " if iz["tur"] == "ses" else "Altyazı  ") + iz_etiketi(iz)
            ttk.Checkbutton(self.liste, text=ad, variable=v, command=self.varsayilanlari_guncelle).grid(
                row=n, column=0, sticky="w")
        self.varsayilanlari_guncelle()
        # Anime'de Japonca ses varsa onu varsayılan yap
        if tur == "anime":
            for etiket, kimlik in self.ses_harita.items():
                if kimlik is not None and kimlik >= 0 and \
                        next(i for i in self.izler if i["indeks"] == kimlik)["dil"] == "jpn":
                    self.v_ses.set(etiket)
                    break

    def ek_sec(self) -> None:
        for yol in filedialog.askopenfilenames(title="Harici ses / altyazı", filetypes=IZ_TURLERI):
            p = Path(yol)
            izler = izleri_listele(arac_bul("ffprobe") or "ffprobe", p)
            tur = izler[0]["tur"] if izler else "altyazi"
            self.ekler.append((p, tur))
        self.varsayilanlari_guncelle()

    def ek_temizle(self) -> None:
        self.ekler.clear()
        self.varsayilanlari_guncelle()

    def varsayilanlari_guncelle(self) -> None:
        for kutu, harita, tur in ((self.v_ses, self.ses_harita, "ses"), (self.v_alt, self.alt_harita, "altyazi")):
            eski = kutu.get()
            harita.clear()
            harita["(kaynaktaki gibi)"] = None
            for iz in self.izler:
                if iz["tur"] == tur and self.secim[iz["indeks"]].get():
                    harita[iz_etiketi(iz)] = iz["indeks"]
            for n, (p, t) in enumerate(self.ekler, 1):
                if t == tur:
                    harita[f"harici: {p.name}"] = -n
            kutu["values"] = list(harita)
            kutu.set(eski if eski in harita else "(kaynaktaki gibi)")
        self.ek_etiket["text"] = ("Harici: " + ", ".join(p.name for p, _ in self.ekler)) if self.ekler else ""

    def argumanlar(self) -> list[str]:
        arg: list[str] = []
        if self.izler:
            for tur, secenek in (("ses", "--ses"), ("altyazi", "--altyazi")):
                hepsi = [i["indeks"] for i in self.izler if i["tur"] == tur]
                secili = [i for i in hepsi if self.secim[i].get()]
                deger = "hepsi" if secili == hepsi else ("yok" if not secili else ",".join(map(str, secili)))
                arg += [secenek, deger]
        vs, va = self.ses_harita.get(self.v_ses.get()), self.alt_harita.get(self.v_alt.get())
        if vs is not None:
            arg += ["--varsayilan-ses", str(vs)]
        if va is not None:
            arg += ["--varsayilan-altyazi", str(va)]
        for p, _ in self.ekler:
            arg += ["--ek", str(p)]
        return arg


class Uygulama(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Pixel Arttırıcı — yerel video")
        # Boyutlar 100% ölçek için; Windows ölçeklemesi (4K TV'de %250-300) kadar büyütülür, ekrana sığdırılır
        k = float(self.tk.call("tk", "scaling")) / (96 / 72)
        g = min(int(1000 * k), int(self.winfo_screenwidth() * 0.9))
        y = min(int(940 * k), int(self.winfo_screenheight() * 0.9))
        self.geometry(f"{g}x{y}")
        self.minsize(int(g * 0.8), int(y * 0.7))
        self.surec: subprocess.Popen | None = None
        self.kuyruk: queue.Queue[str | None] = queue.Queue()
        self.son_cikti: Path | None = None
        self.asama_toplam = 1

        defter = ttk.Notebook(self)
        defter.pack(fill="x", padx=10, pady=(10, 4))
        self.sekme_iyilestir(defter)

        alt = ttk.Frame(self, padding=(10, 0, 10, 10))
        alt.pack(fill="both", expand=True)
        dugmeler = ttk.Frame(alt)
        dugmeler.pack(fill="x")
        self.b_durdur = ttk.Button(dugmeler, text="Durdur", command=self.durdur, state="disabled")
        self.b_durdur.pack(side="left")
        self.b_oynat = ttk.Button(dugmeler, text="Çıktıyı oynat", command=self.oynat, state="disabled")
        self.b_oynat.pack(side="left", padx=6)
        ttk.Button(dugmeler, text="Çıktı klasörünü aç", command=self.klasor_ac).pack(side="left")
        self.durum = ttk.Label(alt, text="Hazır.")
        self.durum.pack(fill="x", pady=(8, 2))
        self.ilerleme = ttk.Progressbar(alt, maximum=100)
        self.ilerleme.pack(fill="x")
        self.gunluk = tk.Text(alt, height=12, wrap="word", font=("Consolas", 9))
        kaydir = ttk.Scrollbar(alt, command=self.gunluk.yview)
        self.gunluk["yscrollcommand"] = kaydir.set
        kaydir.pack(side="right", fill="y", pady=(6, 0))
        self.gunluk.pack(fill="both", expand=True, pady=(6, 0))
        self.protocol("WM_DELETE_WINDOW", self.kapat)
        self.after(100, self.kuyrugu_isle)

    # ---------- sekme 1: video iyileştir ----------
    def sekme_iyilestir(self, defter: ttk.Notebook) -> None:
        f = ttk.Frame(defter, padding=10)
        defter.add(f, text="Video iyileştir")
        f.columnconfigure(1, weight=1)
        r = 0
        ttk.Label(f, text="Kaynak video:").grid(row=r, column=0, sticky="w")
        self.kaynak = tk.StringVar()
        e = ttk.Entry(f, textvariable=self.kaynak)
        e.grid(row=r, column=1, sticky="ew", padx=6)
        e.bind("<FocusOut>", lambda _: self.kaynak_yukle())
        e.bind("<Return>", lambda _: self.kaynak_yukle())
        ttk.Button(f, text="Dosya seç…", command=self.kaynak_sec).grid(row=r, column=2)
        r += 1
        self.bilgi = ttk.Label(f, text="", foreground="gray")
        self.bilgi.grid(row=r, column=1, columnspan=2, sticky="w", padx=6)
        r += 1

        ayar = ttk.Frame(f)
        ayar.grid(row=r, column=0, columnspan=3, sticky="ew", pady=6)
        self.tur = tk.StringVar(value="anime")
        ttk.Label(ayar, text="Tür:").grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(ayar, text="Anime", variable=self.tur, value="anime", command=self.tur_degisti).grid(
            row=0, column=1, sticky="w")
        ttk.Radiobutton(ayar, text="Film", variable=self.tur, value="film", command=self.tur_degisti).grid(
            row=0, column=2, sticky="w")

        self.upscale = tk.BooleanVar(value=True)
        ttk.Checkbutton(ayar, text="4K upscale (blok temizleme dahil)", variable=self.upscale,
                        command=self.tahmin_guncelle).grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(ayar, text="Model:").grid(row=2, column=0, sticky="w")
        self.model = ttk.Combobox(ayar, state="readonly", values=list(MODEL_SECENEK), width=34)
        self.model.set(next(iter(MODEL_SECENEK)))
        self.model.grid(row=2, column=1, columnspan=2, sticky="w")
        self.model.bind("<<ComboboxSelected>>", lambda _: self.tahmin_guncelle())
        ttk.Label(ayar, text="Deblock (film/CUGAN):").grid(row=2, column=3, sticky="e", padx=(16, 4))
        self.deblock = ttk.Combobox(ayar, state="readonly", values=["0", "1", "2", "3"], width=4)
        self.deblock.set("3")
        self.deblock.grid(row=2, column=4, sticky="w")

        ttk.Label(ayar, text="Kare hızı:").grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.fps = ttk.Combobox(ayar, state="readonly", values=list(FPS_SECENEK), width=34)
        self.fps.set(next(iter(FPS_SECENEK)))
        self.fps.grid(row=3, column=1, columnspan=2, sticky="w", pady=(6, 0))
        self.fps.bind("<<ComboboxSelected>>", lambda _: self.tahmin_guncelle())
        ttk.Label(ayar, text="Ara kare:").grid(row=3, column=3, sticky="e", padx=(16, 4), pady=(6, 0))
        self.ara_kare = ttk.Combobox(ayar, state="readonly", values=list(ARA_KARE_SECENEK), width=32)
        self.ara_kare.set(next(iter(ARA_KARE_SECENEK)))
        self.ara_kare.grid(row=3, column=4, sticky="w", pady=(6, 0))
        self.ara_kare.bind("<<ComboboxSelected>>", lambda _: self.tahmin_guncelle())

        ttk.Label(ayar, text="Kodek / CQ:").grid(row=4, column=0, sticky="w", pady=(6, 0))
        self.codec = ttk.Combobox(ayar, state="readonly", values=["hevc", "av1"], width=8)
        self.codec.set("hevc")
        self.codec.grid(row=4, column=1, sticky="w", pady=(6, 0))
        self.cq = tk.StringVar(value="19")
        ttk.Spinbox(ayar, from_=10, to=35, textvariable=self.cq, width=5).grid(row=4, column=2, sticky="w",
                                                                           pady=(6, 0))
        ttk.Label(ayar, text="Aralık (sn veya dk:sn, boş = tamamı):").grid(row=5, column=0, columnspan=2,
                                                                         sticky="w", pady=(6, 0))
        self.bas, self.bit = tk.StringVar(), tk.StringVar()
        aralik = ttk.Frame(ayar)
        aralik.grid(row=5, column=2, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Entry(aralik, textvariable=self.bas, width=9).pack(side="left")
        ttk.Label(aralik, text=" – ").pack(side="left")
        ttk.Entry(aralik, textvariable=self.bit, width=9).pack(side="left")
        for v in (self.bas, self.bit):
            v.trace_add("write", lambda *_: self.tahmin_guncelle())
        self.film_notu = ttk.Label(ayar, text="", foreground="#b35c00")
        self.film_notu.grid(row=6, column=0, columnspan=5, sticky="w")
        r += 1

        self.iz1 = IzSecici(f, "Ses ve altyazı (video bittikten sonra eklenir, kare üretimini etkilemez)")
        self.iz1.grid(row=r, column=0, columnspan=3, sticky="ew", pady=6)
        r += 1
        ttk.Label(f, text="Çıktı (boş = otomatik, cikti\\ altında):").grid(row=r, column=0, sticky="w")
        self.cikti = tk.StringVar()
        ttk.Entry(f, textvariable=self.cikti).grid(row=r, column=1, sticky="ew", padx=6)
        ttk.Button(f, text="Farklı kaydet…", command=lambda: self.kayit_sec(self.cikti)).grid(row=r, column=2)
        r += 1
        self.tahmin = ttk.Label(f, text="", font=("Segoe UI", 10, "bold"))
        self.tahmin.grid(row=r, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.b_baslat = ttk.Button(f, text="Başlat", command=self.baslat)
        self.b_baslat.grid(row=r, column=2, sticky="e", pady=(8, 0))
        self.kaynak_bilgi: dict | None = None

    # ---------- dosya seçimleri ----------
    def dosya_sec(self, degisken: tk.StringVar, klasor: Path = KOK) -> str:
        yol = filedialog.askopenfilename(title="Video seç", initialdir=str(klasor), filetypes=VIDEO_TURLERI)
        if yol:
            degisken.set(str(Path(yol)))
        return yol

    def kayit_sec(self, degisken: tk.StringVar) -> None:
        yol = filedialog.asksaveasfilename(title="Çıktı", initialdir=str(CIKTI), defaultextension=".mkv",
                                           filetypes=[("Matroska", "*.mkv")])
        if yol:
            degisken.set(str(Path(yol)))

    def kaynak_sec(self) -> None:
        if self.dosya_sec(self.kaynak):
            self.kaynak_yukle()

    def kaynak_yukle(self) -> None:
        k = self.kaynak.get().strip().strip('"')
        if not k:
            return
        p = Path(k)
        if not p.is_file():
            self.kaynak_bilgi = None
            self.bilgi["text"] = "Dosya bulunamadı (yalnız yerel dosyalar desteklenir)."
            return
        try:
            self.kaynak_bilgi = akis_bilgisi(arac_bul("ffprobe") or "ffprobe", p)
        except SystemExit as hata:
            self.bilgi["text"] = f"Okunamadı: {hata}"
            return
        b = self.kaynak_bilgi
        self.bilgi["text"] = f"{b['w']}x{b['h']} @ {b['fps']:.3f} fps, {b['kodek']} {b['pix_fmt']}, " \
                             f"{b['sure'] / 60:.1f} dk"
        self.iz1.yukle(p, self.tur.get())
        self.tahmin_guncelle()

    def tur_degisti(self) -> None:
        if self.tur.get() == "film":
            self.model.set(FILM_MODEL)
            self.deblock.set("1")
            self.film_notu["text"] = ("Film: DPIR blok temizleme (deblock 0 = kapalı, 3 = en güçlü; yüksek değer cildi "
                                      "pürüzsüzleştirir) + realesr-general-x4v3.")
        else:
            self.model.set(next(iter(MODEL_SECENEK)))
            self.deblock.set("3")
            self.film_notu["text"] = ""
        self.tahmin_guncelle()

    # ---------- süre tahmini ----------
    def tahmin_guncelle(self) -> None:
        b = self.kaynak_bilgi
        if not b or not b["fps"]:
            self.tahmin["text"] = ""
            return
        try:
            bas = saniye_coz(self.bas.get()) or 0.0
            bit = saniye_coz(self.bit.get())
        except ValueError:
            self.tahmin["text"] = "Aralık anlaşılamadı."
            return
        sure = (bit if bit is not None else b["sure"]) - bas
        sn = self.sure_tahmini(b, sure)
        self.tahmin["text"] = "Hiçbir işlem seçili değil." if sn is None else \
            f"Tahmini süre: ≈ {sure_yaz(sn)} (kaba; RTX 5070 Laptop'ta ölçülen hızlarla)"

    def sure_tahmini(self, b: dict, sure: float) -> float | None:
        """"Video iyileştir" ayarlarıyla `sure` saniyelik kaynağın kaba işleme süresi (sn); işlem seçili değilse None."""
        kare = max(0.0, sure) * b["fps"]
        hedef = FPS_SECENEK[self.fps.get()]
        upscale = self.upscale.get()
        if not upscale and not hedef:
            return None
        sn = kare * SN_UPSCALE[MODEL_SECENEK[self.model.get()]] if upscale else 0.0
        if hedef:
            carpan = max(2, round(hedef / b["fps"]))
            dort_k = upscale or b["h"] > 1440
            if ARA_KARE_SECENEK[self.ara_kare.get()] == "hizli":
                sn += kare * carpan * (SN_HIZLI_4K if dort_k else SN_HIZLI_1080)
            else:
                sn += kare * (carpan - 1) * (SN_ARA_KARE_4K if dort_k else SN_ARA_KARE_1080)
        return sn

    def ayar_argumanlari(self) -> list[str] | None:
        """"Video iyileştir" sekmesindeki tür/model/fps/kodek ayarları (aralık, iz ve çıktı hariç)."""
        hedef = FPS_SECENEK[self.fps.get()]
        if not self.upscale.get() and not hedef:
            messagebox.showwarning("İşlem yok", "\"Video iyileştir\" sekmesinde 4K upscale veya bir kare hızı seç.")
            return None
        arg = ["--tur", self.tur.get()]
        if self.upscale.get():
            model = MODEL_SECENEK[self.model.get()]
            arg += ["--model", model]
            if model in ("cugan", "general"):
                arg += ["--deblock-guc", self.deblock.get()]
        if hedef:
            arg += ["--hedef-fps", str(hedef), "--fps-yontem", ARA_KARE_SECENEK[self.ara_kare.get()]]
            if not self.upscale.get():
                arg.append("--sadece-fps")
        return arg + ["--codec", self.codec.get(), "--cq", self.cq.get()]

    # ---------- çalıştırma ----------
    def baslat(self) -> None:
        if self.surec:
            return
        k = self.kaynak.get().strip().strip('"')
        if not k:
            messagebox.showwarning("Kaynak yok", "Önce bir video dosyası seç.")
            return
        ayar = self.ayar_argumanlari()
        if ayar is None:
            return
        komut = [PYTHON, "-u", str(YEREL / "iyilestir.py"), k, *ayar]
        try:
            bas, bit = saniye_coz(self.bas.get()), saniye_coz(self.bit.get())
        except ValueError:
            messagebox.showwarning("Aralık", "Başlangıç/bitiş saniye (90) veya dk:sn (1:30) olmalı.")
            return
        if bas is not None:
            komut += ["--baslangic", f"{bas:g}"]
        if bit is not None:
            komut += ["--bitis", f"{bit:g}"]
        komut += self.iz1.argumanlar()
        if self.cikti.get().strip():
            komut += ["-o", self.cikti.get().strip()]
        self.calistir(komut, self.b_baslat)

    def calistir(self, komut: list[str], dugme: ttk.Button) -> None:
        # Günlüğe, terminalde (PowerShell, proje klasöründe) aynen çalıştırılabilecek biçimde yaz (py -3.14 ile)
        gosterim = ["py", "-3.14", *[str(Path(x).relative_to(KOK)) if x.startswith(str(KOK)) and x.endswith(".py")
                                     else x for x in komut[2:]]]
        self.yaz("\n> " + ps_komut(gosterim) + "\n")
        ortam = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        self.surec = subprocess.Popen(komut, cwd=str(KOK), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      text=True, encoding="utf-8", errors="replace", env=ortam,
                                      creationflags=subprocess.CREATE_NO_WINDOW)
        tam_hiz(self.surec)
        self.aktif_dugme = dugme
        dugme["state"] = "disabled"
        self.b_durdur["state"] = "normal"
        self.b_oynat["state"] = "disabled"
        self.son_cikti = None
        self.ilerleme["value"] = 0
        self.asama_no, self.asama_toplam = 1, 1
        self.durum["text"] = "Çalışıyor…"
        threading.Thread(target=self.oku, args=(self.surec,), daemon=True).start()

    def oku(self, surec: subprocess.Popen) -> None:
        for satir in surec.stdout:  # type: ignore[union-attr]
            self.kuyruk.put(satir.rstrip("\n"))
        surec.wait()
        self.kuyruk.put(None)

    def kuyrugu_isle(self) -> None:
        try:
            while True:
                satir = self.kuyruk.get_nowait()
                if satir is None:
                    self.bitti()
                    continue
                self.satir_isle(satir)
        except queue.Empty:
            pass
        self.after(150, self.kuyrugu_isle)

    def satir_isle(self, satir: str) -> None:
        if m := re.match(r"\[asama\] (\d+)/(\d+) (\S+)", satir):
            self.asama_no, self.asama_toplam, self.asama_ad = int(m[1]), int(m[2]), m[3]
            self.ilerleme["value"] = (self.asama_no - 1) / self.asama_toplam * 100
            self.durum["text"] = f"Aşama {self.asama_no}/{self.asama_toplam}: {self.asama_ad} başlıyor…"
        elif m := re.match(r"\[ilerleme\] (\S*) %(\d+) kare (\d+)/(\d+) kalan (\S+)", satir):
            yuzde = int(m[2])
            no = getattr(self, "asama_no", 1)
            self.ilerleme["value"] = ((no - 1) + yuzde / 100) / self.asama_toplam * 100
            # Aşama içi ffmpeg adımları (ton, sahne, hazırlık) çubuğu o adım için baştan doldurur
            ad = ADIM_ADI.get(m[1], m[1])
            self.durum["text"] = (f"Aşama {no}/{self.asama_toplam}: {ad} — %{yuzde} "
                                  f"(kare {m[3]}/{m[4]}), bu aşama için kalan ≈ {m[5]}")
            return  # günlüğü ilerleme satırlarıyla doldurma
        elif satir.startswith(("[ton]", "[sahne]", "[iz]", "[kes]", "[hazirlik]")):
            self.durum["text"] = satir
        if m := re.match(r"\[sonuç\] (.+)", satir):
            self.son_cikti = Path(m[1].strip())
        self.yaz(satir + "\n")

    def bitti(self) -> None:
        kod = self.surec.returncode if self.surec else -1
        self.surec = None
        self.aktif_dugme["state"] = "normal"
        self.b_durdur["state"] = "disabled"
        if kod == 0:
            self.ilerleme["value"] = 100
            self.durum["text"] = f"Bitti: {self.son_cikti}" if self.son_cikti else "Bitti."
            if self.son_cikti and self.son_cikti.is_file():
                self.b_oynat["state"] = "normal"
        else:
            self.durum["text"] = f"Durdu / hata (çıkış kodu {kod}). Ayrıntı günlükte."

    def durdur(self) -> None:
        if self.surec and messagebox.askyesno("Durdur", "İşlem durdurulsun mu? Yarım kalan dosyalar silinmez."):
            subprocess.run(["taskkill", "/PID", str(self.surec.pid), "/T", "/F"], capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)

    def oynat(self) -> None:
        if self.son_cikti and self.son_cikti.is_file():
            os.startfile(self.son_cikti)

    def klasor_ac(self) -> None:
        klasor = self.son_cikti.parent if self.son_cikti else CIKTI
        os.startfile(klasor)

    def yaz(self, metin: str) -> None:
        self.gunluk.insert("end", metin)
        self.gunluk.see("end")

    def kapat(self) -> None:
        if self.surec:
            if not messagebox.askyesno("Çıkış", "İşlem sürüyor. Durdurup çıkılsın mı?"):
                return
            subprocess.run(["taskkill", "/PID", str(self.surec.pid), "/T", "/F"], capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        self.destroy()


def main() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # 4K ekranda bulanık görünmesin
    except (AttributeError, OSError):
        pass
    Uygulama().mainloop()


if __name__ == "__main__":
    main()
