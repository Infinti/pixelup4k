<#
Pixel Arttırıcı — temiz bir Windows makinesine araçları ve modelleri kurar (tools\, models\).

    powershell -ExecutionPolicy Bypass -File kurulum.ps1 [-VideoCompare] [-IndirilenleriSakla] [-GereksinimDenetiminiAtla]

- -VideoCompare: isteğe bağlı karşılaştırma izleyicisi video-compare'i de kurar (tools\video-compare, GPL-2.0,
  ~110 MB). İki videoyu aynı karede kayar çizgiyle oynatır; README'deki "Kendin incele" bölümü.

- Her dosya resmi kaynağından indirilir; boyutu ve SHA-256'sı aşağıdaki listeyle denetlenir, uyuşmazsa kurulum durur.
- Zaten kurulmuş bileşen atlanır (tools\.kurulum\<bileşen>.tamam). Yarıda kalan indirme kaldığı yerden sürer.
- Sistem geneline bir şey yazılmaz: araçlar bu klasördedir. Tek istisna: sistem Python'unda numpy yoksa
  kullanıcı alanına (pip --user) kurulur.
- realesr-general-x4v3, Real-ESRGAN'ın resmi .pth sürümünden ONNX'e çevrilir (kurulum\pth_onnx.py; PyTorch gerekmez).
#>
[CmdletBinding()]
param(
    [switch]$VideoCompare,
    [switch]$IndirilenleriSakla,
    [switch]$GereksinimDenetiminiAtla
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # Windows PowerShell 5.1'de ilerleme çubuğu indirmeyi kat kat yavaşlatıyor

$KOK = $PSScriptRoot
$TOOLS = Join-Path $KOK "tools"
$INDIR = Join-Path $TOOLS "_indir"
$ISARET = Join-Path $TOOLS ".kurulum"
$VS = Join-Path $TOOLS "vapoursynth"
$VS_PY = Join-Path $VS "python.exe"
$VS_SP = Join-Path $VS "Lib\site-packages"
$VS_EKLENTI = Join-Path $VS_SP "vapoursynth\plugins"

$EN_DUSUK_SURUCU = 580   # CUDA 13.x (vs-mlrt v15.16: TensorRT 10.16, CUDA 13.2)
$EN_DUSUK_SM = 7.5       # TensorRT 10: Turing (RTX 20) ve sonrası
$GEREKEN_GB = 10         # en yüksek: indirmeler (~3,6 GB) + kurulu hâl (~4,5 GB); sonunda indirmeler silinir

# ad, adres, bayt, sha256. Yayıncının hash'i varsa onunla aynı; yoksa (Video2X, Python, model paketleri,
# Real-ESRGAN .pth) çalışan bir kurulumla bayt bayt karşılaştırılıp sabitlendi.
$DOSYALAR = @(
    @{ Ad = "7zr.exe"; Url = "https://github.com/ip7z/7zip/releases/download/26.04/7zr.exe"
       Bayt = 602624; Sha = "256feca8e274e5da655e2a284fabafd9f554365eb164862089dacd4e8276d282" }
    @{ Ad = "ffmpeg-8.1.1-essentials_build.zip"; Url = "https://github.com/GyanD/codexffmpeg/releases/download/8.1.1/ffmpeg-8.1.1-essentials_build.zip"
       Bayt = 109282242; Sha = "6f58ce889f59c311410f7d2b18895b33c03456463486f3b1ebc93d97a0f54541" }
    @{ Ad = "video2x-windows-amd64.zip"; Url = "https://github.com/k4yt3x/video2x/releases/download/6.4.0/video2x-windows-amd64.zip"
       Bayt = 196989277; Sha = "0337b6dcae2bad2fd13e43fbdb73ef728d2f6289f37cc3617e87ac3a1a5cf793" }
    @{ Ad = "python-3.14.0-embed-amd64.zip"; Url = "https://www.python.org/ftp/python/3.14.0/python-3.14.0-embed-amd64.zip"
       Bayt = 11994671; Sha = "8d4d3590c10449d78aa4375f534e6d5f3027d67fdc362dd1a882279db6f90fdf" }
    @{ Ad = "pip-26.2.1-py3-none-any.whl"; Url = "https://files.pythonhosted.org/packages/f3/6e/1736e5b4ae2b778ef2f81c47d797de9f891d4d8acb047a24ca37a60294dd/pip-26.2.1-py3-none-any.whl"
       Bayt = 1816632; Sha = "71138adf1f4ca900cdb7d289c21b7494329f2332b6d85f0e1c42108c0384ed3e" }
    @{ Ad = "VapourSynth64-Portable-R79.zip"; Url = "https://github.com/vapoursynth/vapoursynth/releases/download/R79/VapourSynth64-Portable-R79.zip"
       Bayt = 23160674; Sha = "625b3410d903943107291592e90d6f521f829ebb8291d952ee91b8d674bbb153" }
    @{ Ad = "scripts.v15.16.7z"; Url = "https://github.com/AmusementClub/vs-mlrt/releases/download/v15.16/scripts.v15.16.7z"
       Bayt = 17732; Sha = "d07dae0a00cb8dbf4f00358f640f630ff5d933de44d27050e7acce4f31cc3560" }
    @{ Ad = "VSTRT-Windows-x64.v15.16.7z"; Url = "https://github.com/AmusementClub/vs-mlrt/releases/download/v15.16/VSTRT-Windows-x64.v15.16.7z"
       Bayt = 486704; Sha = "c4e64e69e87553bf15a7acd29d76debd006954e39ee07ef0e6d526947c5d34b1" }
    @{ Ad = "vsmlrt-cuda.v15.16.7z.001"; Url = "https://github.com/AmusementClub/vs-mlrt/releases/download/v15.16/vsmlrt-cuda.v15.16.7z.001"
       Bayt = 2147483647; Sha = "4792727ddb54b3b647496f2932663592ae6cc18f89cd6a95fa51a25b22cd62b5" }
    @{ Ad = "vsmlrt-cuda.v15.16.7z.002"; Url = "https://github.com/AmusementClub/vs-mlrt/releases/download/v15.16/vsmlrt-cuda.v15.16.7z.002"
       Bayt = 464467988; Sha = "d2ab31feba5c165e9a3bdf02e1455d1232263ffe96bd83ad892aca01029e1495" }
    @{ Ad = "rife_v4.26.7z"; Url = "https://github.com/AmusementClub/vs-mlrt/releases/download/external-models/rife_v4.26.7z"
       Bayt = 19591599; Sha = "dfdabd84a2a3db773f87604b8cc255e94a6a72f13550d910ccd3b4ee2606cd4f" }
    @{ Ad = "dpir_v3.7z"; Url = "https://github.com/AmusementClub/vs-mlrt/releases/download/model-20211209/dpir_v3.7z"
       Bayt = 482255023; Sha = "010d267c9f6321d2aa4b88d9c99c01db12c215ef708a50385beb8e49cce38445" }
    @{ Ad = "video-compare-20261004-win10-x86_64.zip"; Url = "https://github.com/pixop/video-compare/releases/download/20261004/video-compare-20261004-win10-x86_64.zip"
       Bayt = 110735693; Sha = "3d757f9db6d367f92a330f325d476f0284bdea709a8a700bbc2487861ce65c50" }
    @{ Ad = "realesr-general-x4v3.pth"; Url = "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth"
       Bayt = 4885111; Sha = "8dc7edb9ac80ccdc30c3a5dca6616509367f05fbc184ad95b731f05bece96292" }
)
# pth_onnx.py'nin onnx 1.23.2 ile ürettiği dosya (belirlenimci; çıktıları önceki ONNX ile birebir aynı doğrulandı)
$FILM_ONNX_SHA = "5f6a18d67fc81db95240d2c7d8a04e491ded45e9ab5efffcecbd40ff8edfee60"

# VapourSynth'in Python'una kurulacak paketler (pip --require-hashes; yalnız hazır wheel)
$VS_PAKETLER = @"
vapoursynth-bestsource==21.0 --hash=sha256:f1003f5e610acc80ae1bd23752ba182455f807922c83db9958a299991f272ea4
numpy==2.5.4 --hash=sha256:2ca144f15135b6212a5c47b1e2aeca6e412f102f95a2d5d88d8aec77eb255de3
onnx==1.23.2 --hash=sha256:9b382ba898a7c142a0801d03cf04ecabced96c1543c7b643a86f0928143802de
protobuf==7.36.2 --hash=sha256:a300819d441e078a5608c0d3c709796bb548136058fda017ae51d425b44fd353 --hash=sha256:bdb3a345d48db958e6ce1f18e508beb0cc981d64f24088427549c866cd039f1e
ml_dtypes==0.6.0 --hash=sha256:d574c2b28921dc72e869df248f1a278f6eee176a1f237c8642e1a71eb15f3977
typing_extensions==4.16.0 --hash=sha256:481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8
"@
# Sistem Python'u (arayüz ve betikler) için yalnız numpy gerekir (ortak\sahne.py)
$SISTEM_PAKETLER = "numpy==2.5.4 --hash=sha256:2ca144f15135b6212a5c47b1e2aeca6e412f102f95a2d5d88d8aec77eb255de3"

function Yaz([string]$Metin, [string]$Renk = "Gray") { Write-Host $Metin -ForegroundColor $Renk }
function Hata([string]$Metin) { Write-Host "HATA: $Metin" -ForegroundColor Red; exit 1 }

function Calistir([string]$Exe, [string[]]$Arg) {
    & $Exe @Arg
    if ($LASTEXITCODE -ne 0) { Hata "'$Exe $($Arg -join ' ')' başarısız (çıkış kodu $LASTEXITCODE)" }
}

# Yalnız çıkış kodu önemli bir yoklama. Windows PowerShell 5.1'de Stop modunda yönlendirilen stderr satırı
# ölümcül hataya dönüşüyor; yoklama süresince Continue'ya geçilir.
function Yokla([string]$Exe, [string[]]$Arg) {
    $eski = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Exe @Arg 2>&1 | Out-Null; return ($LASTEXITCODE -eq 0) } finally { $ErrorActionPreference = $eski }
}

# ---------- gereksinimler ----------
function Gereksinimler {
    Yaz "[1/7] Gereksinimler denetleniyor" Cyan
    if (-not [Environment]::Is64BitOperatingSystem) { Hata "64 bit Windows gerekli." }

    $smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if (-not $smi) { Hata "nvidia-smi bulunamadı: NVIDIA ekran kartı ve sürücüsü gerekli (https://www.nvidia.com/drivers)." }
    $satir = (& nvidia-smi --query-gpu=name,driver_version,compute_cap --format=csv,noheader | Select-Object -First 1)
    $ad, $surucu, $sm = ($satir -split ",") | ForEach-Object { $_.Trim() }
    Yaz "  GPU: $ad, sürücü $surucu, compute $sm"
    if ($ad -notmatch "RTX") { Hata "NVIDIA RTX ekran kartı gerekli (TensorRT ve NVENC 10-bit HEVC için). Bulunan: $ad" }
    if ([double]::Parse($sm, [Globalization.CultureInfo]::InvariantCulture) -lt $EN_DUSUK_SM) { Hata "compute $sm < $EN_DUSUK_SM (RTX 20 ve sonrası gerekli)." }
    if ([int]($surucu -split "\.")[0] -lt $EN_DUSUK_SURUCU) { Hata "Sürücü $surucu çok eski; en az $EN_DUSUK_SURUCU gerekli (CUDA 13). https://www.nvidia.com/drivers" }

    $pyOk = (Get-Command py -ErrorAction SilentlyContinue) -and (Yokla "py" @("-3.14", "-c", "import tkinter"))
    if (-not $pyOk) { Hata "Python 3.14 (tkinter dahil) bulunamadı. python.org'dan 'Windows installer (64-bit)' ile kur; 'py launcher' seçili kalsın." }
    Yaz "  Python: $(& py -3.14 -c 'import sys; print(sys.version.split()[0])')"

    $surucuHarf = (Get-Item $KOK).PSDrive.Name
    $bos = [math]::Round((Get-PSDrive $surucuHarf).Free / 1GB, 1)
    Yaz "  ${surucuHarf}: boş alan $bos GB (kurulum sırasında ~$GEREKEN_GB GB gerekir, sonunda ~4,5 GB kalır)"
    if ($bos -lt $GEREKEN_GB) { Hata "Yetersiz disk alanı: $bos GB boş, $GEREKEN_GB GB gerekli." }
}

# ---------- indirme ----------
function Hash([string]$Yol) { (Get-FileHash -Algorithm SHA256 -LiteralPath $Yol).Hash.ToLower() }

function Indir([string]$Ad, [switch]$Sessiz) {
    $d = $DOSYALAR | Where-Object { $_.Ad -eq $Ad }
    $hedef = Join-Path $INDIR $Ad
    if (Test-Path -LiteralPath $hedef) {
        if ((Hash $hedef) -eq $d.Sha) { if (-not $Sessiz) { Yaz "  var: $Ad" }; return $hedef }
        # Tam boyda ama hash'i tutmayan dosya sürdürülemez; küçükse yarım kalmış indirmedir, kaldığı yerden sürer
        if ((Get-Item -LiteralPath $hedef).Length -ge $d.Bayt) { Remove-Item -LiteralPath $hedef }
    }
    Yaz ("  indiriliyor: {0} ({1:N0} MB)" -f $Ad, ($d.Bayt / 1MB))
    # curl.exe Windows 10 1803+ ile gelir; -C - yarıda kalan dosyayı sürdürür
    & curl.exe -L --fail --retry 3 --retry-delay 5 -C - -sS -o $hedef $d.Url
    if ($LASTEXITCODE -ne 0) { Hata "$Ad indirilemedi ($($d.Url))" }
    if ((Get-Item -LiteralPath $hedef).Length -ne $d.Bayt) {
        Remove-Item -LiteralPath $hedef; Hata "$Ad boyutu beklenenden farklı; dosya silindi, yeniden dene."
    }
    $h = Hash $hedef
    if ($h -ne $d.Sha) { Remove-Item -LiteralPath $hedef; Hata "$Ad SHA-256 uyuşmuyor (beklenen $($d.Sha), gelen $h); dosya silindi." }
    return $hedef
}

function Tamam([string]$Bilesen) { Test-Path -LiteralPath (Join-Path $ISARET "$Bilesen.tamam") }
function Isaretle([string]$Bilesen, [string]$Surum) {
    Set-Content -LiteralPath (Join-Path $ISARET "$Bilesen.tamam") -Value $Surum -Encoding UTF8
}

function ZipAc([string]$Zip, [string]$Hedef) {
    if (Test-Path -LiteralPath $Hedef) { Remove-Item -LiteralPath $Hedef -Recurse -Force }
    Expand-Archive -LiteralPath $Zip -DestinationPath $Hedef -Force
}

function YediAc([string]$Arsiv, [string]$Hedef, [string[]]$Icerik = @()) {
    $z = Indir "7zr.exe" -Sessiz
    Calistir $z (@("x", "-y", "-bso0", "-bsp0", "-o$Hedef", $Arsiv) + $Icerik)
}

# ---------- bileşenler ----------
function Kur-Ffmpeg {
    if (Tamam "ffmpeg") { Yaz "  ffmpeg zaten kurulu"; return }
    $gecici = Join-Path $INDIR "_ffmpeg"
    ZipAc (Indir "ffmpeg-8.1.1-essentials_build.zip") $gecici
    $bin = Get-ChildItem -LiteralPath $gecici -Directory | Select-Object -First 1 | ForEach-Object { Join-Path $_.FullName "bin" }
    $hedef = Join-Path $TOOLS "ffmpeg\bin"
    New-Item -ItemType Directory -Force $hedef | Out-Null
    Copy-Item (Join-Path $bin "ffmpeg.exe"), (Join-Path $bin "ffprobe.exe") $hedef -Force
    Remove-Item -LiteralPath $gecici -Recurse -Force
    if (-not (Yokla (Join-Path $hedef "ffmpeg.exe") @("-hide_banner", "-h", "filter=libvmaf"))) {
        Hata "ffmpeg'de libvmaf yok (beklenmiyordu)."
    }
    Isaretle "ffmpeg" "8.1.1-essentials"
    Yaz "  ffmpeg 8.1.1 kuruldu (libvmaf var)" Green
}

function Kur-Video2X {
    if (Tamam "video2x") { Yaz "  Video2X zaten kurulu"; return }
    $hedef = Join-Path $TOOLS "video2x"
    ZipAc (Indir "video2x-windows-amd64.zip") $hedef
    if (-not (Test-Path (Join-Path $hedef "video2x.exe"))) { Hata "video2x.exe arşivde beklenen yerde değil." }
    Isaretle "video2x" "6.4.0"
    Yaz "  Video2X 6.4.0 kuruldu" Green
}

function Kur-VapourSynth {
    if (Tamam "vapoursynth") { Yaz "  VapourSynth zaten kurulu"; return }
    # VapourSynth'in resmi portable kurulum betiğiyle aynı adımlar, ama sürümler ve hash'ler sabit
    if (Test-Path -LiteralPath $VS) { Remove-Item -LiteralPath $VS -Recurse -Force }
    Expand-Archive -LiteralPath (Indir "python-3.14.0-embed-amd64.zip") -DestinationPath $VS -Force
    Add-Content -LiteralPath (Join-Path $VS "python314._pth") -Value "Lib\site-packages" -Encoding UTF8
    $pip = Indir "pip-26.2.1-py3-none-any.whl"
    # pip kendi wheel'inin içinden çalışabilir: get-pip.py (sürümsüz) yerine sabit sürüm. "<whl>\pip" ile
    # başlatılınca Windows'ta kendini kurmayı reddediyor (argv[0] = pip); runpy ile modül olarak başlatılır
    Calistir $VS_PY @("-I", "-c", "import sys, runpy; sys.path.insert(0, sys.argv.pop(1)); runpy.run_module('pip', run_name='__main__')",
                      $pip, "install", "--no-index", "--no-deps", "--no-warn-script-location", $pip)
    Expand-Archive -LiteralPath (Indir "VapourSynth64-Portable-R79.zip") -DestinationPath $VS -Force
    $whl = Get-ChildItem -LiteralPath (Join-Path $VS "wheel") -Filter "VapourSynth-79-*.whl" | Select-Object -First 1
    Calistir $VS_PY @("-I", "-m", "pip", "install", "--no-index", "--no-deps", "--no-warn-script-location", $whl.FullName)
    $req = Join-Path $INDIR "vs_paketler.txt"
    Set-Content -LiteralPath $req -Value $VS_PAKETLER -Encoding ASCII
    # --no-deps: bestsource 22+ VapourSynth'i R80+'a yükseltip vs-mlrt eklentilerini bozuyor; bağımlılıklar listede
    Calistir $VS_PY @("-I", "-m", "pip", "install", "--no-deps", "--only-binary=:all:", "--require-hashes",
                      "--no-warn-script-location", "--disable-pip-version-check", "-r", $req)
    Isaretle "vapoursynth" "R79 + Python 3.14.0"
    Yaz "  VapourSynth R79 (Python 3.14.0 portable) kuruldu" Green
}

function Kur-VsMlrt {
    if (Tamam "vsmlrt") { Yaz "  vs-mlrt zaten kurulu"; return }
    New-Item -ItemType Directory -Force $VS_EKLENTI | Out-Null
    YediAc (Indir "scripts.v15.16.7z") $VS_SP @("vsmlrt.py")
    YediAc (Indir "VSTRT-Windows-x64.v15.16.7z") $VS_EKLENTI
    Indir "vsmlrt-cuda.v15.16.7z.002" | Out-Null
    # Parçalı arşiv: 7zr ilk parçadan başlayıp ikinciyi kendisi bulur
    YediAc (Indir "vsmlrt-cuda.v15.16.7z.001") $VS_EKLENTI
    if (-not (Test-Path (Join-Path $VS_EKLENTI "vsmlrt-cuda\nvinfer_10.dll"))) { Hata "TensorRT kütüphaneleri beklenen yerde değil (vsmlrt-cuda\nvinfer_10.dll)." }
    Isaretle "vsmlrt" "v15.16 (TensorRT 10.16, CUDA 13.2)"
    Yaz "  vs-mlrt v15.16 (TensorRT 10.16) kuruldu" Green
}

function Kur-Modeller {
    if (Tamam "modeller") { Yaz "  Modeller zaten kurulu"; return }
    $m = Join-Path $VS_EKLENTI "models"
    # Yalnız gereken iki model: RIFE 4.26 (hızlı ara kare, 2. uygulama) ve DPIR blok temizleme (film)
    YediAc (Indir "rife_v4.26.7z") $m @("rife_v2\rife_v4.26.onnx")
    YediAc (Indir "dpir_v3.7z") $m @("dpir\drunet_deblocking_color.onnx")
    foreach ($f in "rife_v2\rife_v4.26.onnx", "dpir\drunet_deblocking_color.onnx") {
        if (-not (Test-Path (Join-Path $m $f))) { Hata "Model açılamadı: $f" }
    }
    $onnx = Join-Path $KOK "models\film\realesr-general-x4v3.onnx"
    Calistir $VS_PY @("-I", (Join-Path $KOK "kurulum\pth_onnx.py"), (Indir "realesr-general-x4v3.pth"), $onnx)
    $h = Hash $onnx
    if ($h -ne $FILM_ONNX_SHA) { Hata "realesr-general-x4v3.onnx beklenen dosya değil (SHA-256 $h)." }
    Isaretle "modeller" "rife v4.26, dpir v3, realesr-general-x4v3 (v0.2.5.0)"
    Yaz "  Modeller kuruldu: RIFE 4.26, DPIR deblock, realesr-general-x4v3 (resmi .pth'den)" Green
}

function Kur-SistemNumpy {
    if (Yokla "py" @("-3.14", "-c", "import numpy")) { Yaz "  sistem Python'unda numpy var"; return }
    $req = Join-Path $INDIR "sistem_paketler.txt"
    Set-Content -LiteralPath $req -Value $SISTEM_PAKETLER -Encoding ASCII
    Calistir "py" @("-3.14", "-m", "pip", "install", "--user", "--only-binary=:all:", "--require-hashes",
                    "--disable-pip-version-check", "-r", $req)
    Yaz "  numpy sistem Python'una (kullanıcı alanı) kuruldu" Green
}

function Kur-VideoCompare {
    if (Tamam "video-compare") { Yaz "  video-compare zaten kurulu"; return }
    $hedef = Join-Path $TOOLS "video-compare"
    ZipAc (Indir "video-compare-20261004-win10-x86_64.zip") $hedef
    if (-not (Test-Path (Join-Path $hedef "video-compare.exe"))) { Hata "video-compare.exe arşivde beklenen yerde değil." }
    Isaretle "video-compare" "20261004"
    Yaz "  video-compare 20261004 kuruldu (tools\video-compare\video-compare.exe)" Green
}

function Dogrula {
    Yaz "[7/7] Doğrulama" Cyan
    $ff = Join-Path $TOOLS "ffmpeg\bin\ffmpeg.exe"
    Yaz ("  " + (& $ff -hide_banner -version | Select-Object -First 1))
    Yaz ("  " + (& (Join-Path $TOOLS "video2x\video2x.exe") --version | Select-Object -First 1))
    $env:PATH = "$VS;$VS_SP\vapoursynth;$env:PATH"
    # Çok satırlı kod -c ile verilmez (5.1 tırnakları bozabiliyor); geçici dosyaya yazılır
    $betik = Join-Path $INDIR "dogrula.py"
    Set-Content -LiteralPath $betik -Encoding ASCII -Value @'
import vapoursynth as vs, vsmlrt
c = vs.core
eksik = [e for e in ("trt", "bs") if not hasattr(c, e)]
if eksik:
    raise SystemExit("eksik eklenti: " + ", ".join(eksik))
trt = c.trt.Version()["tensorrt_version"]
trt = trt.decode() if isinstance(trt, bytes) else trt
print("  VapourSynth R%d | vs-mlrt %s | TensorRT %s" % (c.core_version.release_major, vsmlrt.__version__, trt))
'@
    Calistir $VS_PY @("-I", $betik)
    Yaz "Kurulum tamam. Arayüz: arayuz.bat (çift tıkla) ya da PowerShell'de .\arayuz.bat" Green
}

# ---------- akış ----------
New-Item -ItemType Directory -Force $TOOLS, $INDIR, $ISARET | Out-Null
if ($GereksinimDenetiminiAtla) { Yaz "[1/7] Gereksinim denetimi atlandı" Yellow } else { Gereksinimler }
Yaz "[2/7] ffmpeg" Cyan;           Kur-Ffmpeg
Yaz "[3/7] Video2X" Cyan;          Kur-Video2X
Yaz "[4/7] VapourSynth" Cyan;      Kur-VapourSynth
Yaz "[5/7] vs-mlrt (TensorRT)" Cyan; Kur-VsMlrt
Yaz "[6/7] Modeller ve numpy" Cyan; Kur-Modeller; Kur-SistemNumpy
if ($VideoCompare) { Yaz "[+] video-compare (isteğe bağlı)" Cyan; Kur-VideoCompare }
Dogrula
if (-not $IndirilenleriSakla) {
    Remove-Item -LiteralPath $INDIR -Recurse -Force
    Yaz "İndirilen arşivler silindi (tools\_indir). Saklamak için: -IndirilenleriSakla" DarkGray
}
