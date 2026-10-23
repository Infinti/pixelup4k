# Film için yüksek kaliteli upscale hattı: DPIR deblock → realesr-general-x4v3 (4x) → hedef boyuta küçült.
# yerel\iyilestir.py bunu vspipe ile çalıştırır; ayarlar ortam değişkenleriyle gelir:
#   PIXEL_FILM_KAYNAK  girdi dosyası
#   PIXEL_FILM_DEBLOCK DPIR gücü (0 = kapalı; 0-255 ölçeği, vs-dpir varsayılanı 50)
#   PIXEL_FILM_HEDEF   hedef kutu "3840x2160" (en-boy korunur, kaynaktan küçükse büyütülmez)
# Ölçüm (1920x800 film, RTX 5070 Laptop): yalnız general-x4v3 6.5 kare/sn, DPIR 30 ile 3.5 kare/sn.
# TensorRT motorları kaynak boyutu başına bir kez derlenir (~1 dk).
import os
import sys

import vapoursynth as vs

sys.path.insert(0, os.environ["PIXEL_KOK"])
import vsmlrt  # noqa: E402
from vsmlrt import Backend, DPIRModel  # noqa: E402

core = vs.core
KAYNAK = os.environ["PIXEL_FILM_KAYNAK"]
DEBLOCK = float(os.environ.get("PIXEL_FILM_DEBLOCK", "0"))
HW, HH = (int(x) for x in os.environ.get("PIXEL_FILM_HEDEF", "3840x2160").split("x"))
MODEL = os.path.join(os.environ["PIXEL_KOK"], "models", "film", "realesr-general-x4v3.onnx")

src = core.bs.VideoSource(KAYNAK)
be = Backend.TRT(fp16=True, num_streams=2, use_cuda_graph=True)
rgb = src.resize.Bicubic(format=vs.RGBH, matrix_in_s="709")
if DEBLOCK > 0:
    rgb = vsmlrt.DPIR(rgb, strength=DEBLOCK, model=DPIRModel.drunet_deblocking_color, backend=be)
buyuk = vsmlrt.inference(rgb, MODEL, backend=be)
k = min(HW / src.width, HH / src.height)
w, h = round(src.width * k / 2) * 2, round(src.height * k / 2) * 2
buyuk.resize.Spline36(w, h, format=vs.YUV420P10, matrix_s="709").std.AssumeFPS(src).set_output()
