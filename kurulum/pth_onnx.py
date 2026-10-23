r"""Real-ESRGAN realesr-general-x4v3.pth (resmi sürüm) → ONNX. PyTorch gerekmez: yalnız numpy + onnx.

    tools\vapoursynth\python.exe -I kurulum\pth_onnx.py <girdi.pth> <cikti.onnx>

Model SRVGGNetCompact (num_feat 64, num_conv 32, 4x): conv + PReLU zinciri → pixel shuffle (DepthToSpace CRD) +
girdinin en yakın komşu 4x büyütmesi. Grafik, PyTorch'un torch.onnx.export ile ürettiğinin aynısıdır (opset 17,
dinamik h/w); vs-mlrt TensorRT ile kullanılır (yerel\film_hat.vpy).

.pth bir zip: archive/data.pkl (pickle) + archive/data/<n> (ham tensör baytları). Pickle'ı açarken yalnız
tensör yeniden kurma için gereken birkaç ada izin verilir; dosya başka bir şey çağırmaya çalışırsa okuma durur
(indirilen dosya kod çalıştıramaz).
"""
from __future__ import annotations

import io
import pickle
import sys
import zipfile
from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

DEPOLAMA = {"FloatStorage": np.float32, "HalfStorage": np.float16}


class _Depolama:
    def __init__(self, ad: str) -> None:
        self.ad = ad


def _tensor_kur(depo, kayma, boyut, adim, *_):
    dizi = depo[kayma:kayma + int(np.prod(boyut))] if boyut else depo[kayma:kayma + 1]
    beklenen = tuple(int(np.prod(boyut[i + 1:])) for i in range(len(boyut)))
    if tuple(adim) != beklenen:
        raise ValueError(f"bitişik olmayan tensör desteklenmiyor: boyut {boyut}, adım {adim}")
    return dizi.reshape(boyut)


class _GuvenliOkuyucu(pickle.Unpickler):
    def __init__(self, dosya, arsiv: zipfile.ZipFile, kok: str) -> None:
        super().__init__(dosya)
        self.arsiv, self.kok = arsiv, kok

    def find_class(self, modul: str, ad: str):
        if (modul, ad) == ("collections", "OrderedDict"):
            import collections
            return collections.OrderedDict
        if (modul, ad) == ("torch._utils", "_rebuild_tensor_v2"):
            return _tensor_kur
        if modul == "torch" and ad in DEPOLAMA:
            return _Depolama(ad)
        raise pickle.UnpicklingError(f"izin verilmeyen ad: {modul}.{ad}")

    def persistent_load(self, kimlik):
        # ('storage', <tür>, <anahtar>, <cihaz>, <öğe sayısı>)
        _, tur, anahtar, _, adet = kimlik
        ham = self.arsiv.read(f"{self.kok}/data/{anahtar}")
        return np.frombuffer(ham, dtype=DEPOLAMA[tur.ad], count=adet)


def agirliklari_oku(pth: Path) -> dict[str, np.ndarray]:
    with zipfile.ZipFile(pth) as arsiv:
        pkl = next(n for n in arsiv.namelist() if n.endswith("/data.pkl"))
        veri = _GuvenliOkuyucu(io.BytesIO(arsiv.read(pkl)), arsiv, pkl.rsplit("/", 1)[0]).load()
    # Real-ESRGAN sürümleri ağırlıkları 'params_ema' veya 'params' altında saklar
    sd = veri.get("params_ema") or veri.get("params") or veri
    return {k: np.asarray(v, dtype=np.float32) for k, v in sd.items()}


def onnx_kur(sd: dict[str, np.ndarray], olcek: int = 4) -> onnx.ModelProto:
    son = max(int(k.split(".")[1]) for k in sd if k.endswith(".weight") and sd[k].ndim == 4)
    dugumler, baslangic = [], []
    x = "input"
    for i in range(0, son + 1):
        ad = f"body.{i}"
        if f"{ad}.weight" not in sd:
            continue
        w = sd[f"{ad}.weight"]
        if w.ndim == 4:  # Conv 3x3
            baslangic += [numpy_helper.from_array(w, f"{ad}.weight"),
                          numpy_helper.from_array(sd[f"{ad}.bias"], f"{ad}.bias")]
            y = f"/{ad}/Conv_output_0"
            dugumler.append(helper.make_node("Conv", [x, f"{ad}.weight", f"{ad}.bias"], [y], name=f"/{ad}/Conv",
                                             dilations=[1, 1], group=1, kernel_shape=[3, 3], pads=[1, 1, 1, 1],
                                             strides=[1, 1]))
        else:  # PReLU: kanal başına eğim, (C,1,1) ile yayılır
            baslangic.append(numpy_helper.from_array(w.reshape(-1, 1, 1), f"{ad}.prelu"))
            y = f"/{ad}/PRelu_output_0"
            dugumler.append(helper.make_node("PRelu", [x, f"{ad}.prelu"], [y], name=f"/{ad}/PRelu"))
        x = y
    dugumler += [
        helper.make_node("DepthToSpace", [x], ["/upsampler/DepthToSpace_output_0"], blocksize=olcek, mode="CRD",
                         name="/upsampler/DepthToSpace"),
        helper.make_node("Constant", [], ["/Constant_output_0"], name="/Constant",
                         value=numpy_helper.from_array(np.array([1, 1, olcek, olcek], np.float32))),
        helper.make_node("Resize", ["input", "", "/Constant_output_0"], ["/Resize_output_0"], name="/Resize",
                         coordinate_transformation_mode="asymmetric", cubic_coeff_a=-0.75, mode="nearest",
                         nearest_mode="floor"),
        helper.make_node("Add", ["/upsampler/DepthToSpace_output_0", "/Resize_output_0"], ["output"], name="/Add"),
    ]
    graf = helper.make_graph(
        dugumler, "realesr-general-x4v3",
        [helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, 3, "h", "w"])],
        [helper.make_tensor_value_info("output", TensorProto.FLOAT, [1, 3, "h4", "w4"])], baslangic)
    model = helper.make_model(graf, opset_imports=[helper.make_opsetid("", 17)], producer_name="pixel-arttirici")
    model.ir_version = 8
    onnx.checker.check_model(model)
    return model


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit("kullanım: pth_onnx.py <girdi.pth> <cikti.onnx>")
    girdi, cikti = Path(sys.argv[1]), Path(sys.argv[2])
    sd = agirliklari_oku(girdi)
    model = onnx_kur(sd)
    cikti.parent.mkdir(parents=True, exist_ok=True)
    onnx.save(model, cikti)
    conv = sum(1 for n in model.graph.node if n.op_type == "Conv")
    prelu = sum(1 for n in model.graph.node if n.op_type == "PRelu")
    print(f"[onnx] {cikti.name}: {conv} Conv, {prelu} PReLU, {sum(v.size for v in sd.values())} parametre")


if __name__ == "__main__":
    main()
