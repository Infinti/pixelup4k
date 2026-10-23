# Hızlı ara kare hattı (--fps-yontem hizli).
# Gerçek kareler dokunulmadan geçer; yalnız ARA kareler küçük boyutta (varsayılan 1080p) RIFE TensorRT ile hesaplanıp
# kaynak boyutuna büyütülür. Hareket sırasında göz ara karedeki ayrıntı farkını görmez; 4K'da Video2X RIFE'ye göre
# kat kat hızlı. Sahne kesmelerinde ara kare yerine önceki kare tekrarlanır (ayrı "sahne" turu gerekmez).
# yerel\iyilestir.py bunu vspipe ile çalıştırır; ayarlar ortam değişkenleriyle gelir:
#   PIXEL_RIFE_KAYNAK   girdi (genelde 4K upscale ara dosyası)
#   PIXEL_RIFE_CARPAN   kare çarpanı (2-6)
#   PIXEL_RIFE_MODEL    vsmlrt RIFEModel adı (v4_26, v4_17_lite)
#   PIXEL_RIFE_ARA      ara kare kutusu: 1080 | 1440 | 2160 (oran korunur, kaynaktan büyütülmez)
#   PIXEL_RIFE_KESMELER kesme listesi json'u (yerel\iyilestir.kesmeleri_bul); yoksa kesme işlenmez
# TensorRT motoru boyut/model başına bir kez derlenir (~3-4 dk).
import json
import os
import sys

import vapoursynth as vs

sys.path.insert(0, os.environ["PIXEL_KOK"])
import vsmlrt  # noqa: E402
from vsmlrt import Backend, RIFEModel  # noqa: E402

core = vs.core
KAYNAK = os.environ["PIXEL_RIFE_KAYNAK"]
CARPAN = int(os.environ.get("PIXEL_RIFE_CARPAN", "3"))
MODEL = getattr(RIFEModel, os.environ.get("PIXEL_RIFE_MODEL", "v4_26"))
KW, KH = {"1080": (1920, 1080), "1440": (2560, 1440), "2160": (3840, 2160)}[os.environ.get("PIXEL_RIFE_ARA", "1080")]
# Yeni RIFE modelleri (4.1x+) boyutların 128'in katı olmasını ister
HIZA = 128

src = core.bs.VideoSource(KAYNAK)
k = min(KW / src.width, KH / src.height, 1.0)
aw, ah = round(src.width * k / 2) * 2, round(src.height * k / 2) * 2
pw, ph = -(-aw // HIZA) * HIZA - aw, -(-ah // HIZA) * HIZA - ah
kucuk = src.resize.Bicubic(aw, ah, format=vs.RGBH, matrix_in_s="709")
if pw or ph:
    kucuk = kucuk.std.AddBorders(right=pw, bottom=ph)

isaret: set[int] = set()
if yol := os.environ.get("PIXEL_RIFE_KESMELER"):
    with open(yol, encoding="utf-8") as f:
        # kesme c = yeni sahnenin ilk karesi; vs-mlrt _SceneChangeNext=1 olan kare ile sonraki arasında ara kare
        # üretmez, soldaki kareyi tekrarlar → işaret c-1'e
        isaret = {c - 1 for c in json.load(f)["kesmeler"] if 0 < c < src.num_frames}

    def isaretle(n: int, f: vs.VideoFrame) -> vs.VideoFrame:
        cikti = f.copy()
        cikti.props["_SceneChangeNext"] = int(n in isaret)
        return cikti

    # Python geri çağrısı yalnız 16x16'lık boş karede çalışır; işaret RIFE girdisine yerel CopyFrameProps ile aktarılır
    bos = core.std.BlankClip(format=vs.GRAY8, width=16, height=16, length=src.num_frames)
    kucuk = core.std.CopyFrameProps(kucuk, core.std.ModifyFrame(bos, bos, isaretle), props=["_SceneChangeNext"])

be = Backend.TRT(fp16=True, num_streams=2, use_cuda_graph=True, output_format=1)
ara = vsmlrt.RIFE(kucuk, multi=CARPAN, model=MODEL, backend=be, _implementation=2)
if pw or ph:
    ara = ara.std.Crop(right=pw, bottom=ph)
ara = ara.resize.Bicubic(src.width, src.height, format=src.format.id, matrix_s="709")
if isaret:
    # Kesmede tekrarlanan kare de 1080p'den büyütülmüş olmasın: o konumlara dokunulmamış kaynak kare konur.
    # Seçim kare numarasından (prop_src ile kare özelliğine bakmak her kareyi önceden istetiyordu: 46 → 32 kare/sn)
    tekrar = core.std.Interleave([src] * CARPAN)
    rife = ara
    ara = core.std.FrameEval(rife, lambda n: tekrar if n // CARPAN in isaret else rife)
# vsmlrt.RIFE çıktısı: her kaynak kareden sonra (CARPAN-1) ara kare; gerçek kareler yerine dokunulmamış kaynak konur
core.std.Interleave([src, *(ara.std.SelectEvery(CARPAN, j) for j in range(1, CARPAN))]).std.AssumeFPS(
    fpsnum=src.fps_num * CARPAN, fpsden=src.fps_den).set_output()
