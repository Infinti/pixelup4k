# How the demo assets were made / Demo görselleri nasıl üretildi

All commands are for **PowerShell**, run in the repository folder after `kurulum.ps1`.
(In cmd.exe drop the leading `.\` and `&`.) `ffmpeg` below means `.\tools\ffmpeg\bin\ffmpeg.exe`.

Komutlar **PowerShell** içindir; `kurulum.ps1`'den sonra repo klasöründe çalıştırılır.
(cmd.exe'de baştaki `.\` ve `&` gerekmez.) Aşağıdaki `ffmpeg`, `.\tools\ffmpeg\bin\ffmpeg.exe` demektir.

Source / Kaynak: *Tears of Steel* — (CC) Blender Foundation | mango.blender.org (film: CC-BY 3.0;
soundtrack: CC-BY-ND 3.0 — no audio is used in these assets / bu görsellerde ses yok).

## 1. Reference and degraded clip / Referans ve bozuk klip
```powershell
# Official 1080p release (583 MB zip) / resmi 1080p sürüm
curl.exe -L -o tears_of_steel_1080p.mov.zip https://download.blender.org/demo/movies/ToS/tears_of_steel_1080p.mov.zip
Expand-Archive tears_of_steel_1080p.mov.zip -DestinationPath .\klipler

# Reference: 10 s from 06:51, lossless (x264 qp 0) / referans: 06:51'den 10 sn, kayıpsız
.\tools\ffmpeg\bin\ffmpeg.exe -ss 411 -i .\klipler\tears_of_steel_1080p.mov -t 10 -map 0:v:0 -c:v libx264 -qp 0 -preset veryfast .\klipler\tos_ref.mkv

# Deliberately degraded to 1.2 Mbps (x264 medium, single-pass ABR) / bilerek 1.2 Mbps'e bozuldu
.\tools\ffmpeg\bin\ffmpeg.exe -i .\klipler\tos_ref.mkv -c:v libx264 -b:v 1200k -an .\klipler\tos_bozuk_1200k.mp4
```

## 2. Processing / İşleme
```powershell
foreach ($g in 0, 1, 2, 3) {
    py -3.14 yerel\iyilestir.py .\klipler\tos_bozuk_1200k.mp4 --tur film --deblock-guc $g -o ".\cikti\tos_deblock$g.mkv"
}
```

## 3. Measurement (tables, charts) / Ölçüm (tablolar, grafikler)
matplotlib is optional and only needed for the charts / matplotlib yalnız grafikler için gerekir:
`py -3.14 -m pip install --user matplotlib`
```powershell
py -3.14 yerel\olc.py .\klipler\tos_ref.mkv .\cikti\tos_deblock0.mkv .\cikti\tos_deblock1.mkv .\cikti\tos_deblock2.mkv .\cikti\tos_deblock3.mkv `
    --taban .\klipler\tos_bozuk_1200k.mp4 --etiket "Degraded 1.2 Mbps" "Deblock 0" "Deblock 1 (default)" "Deblock 2" "Deblock 3" `
    --aralik 4-6 --ad readme_en --dil en
```
→ `cikti\olcum\readme_en\` (`olcum.md`, `vmaf_ortalama.png`, `vmaf_kare.png`). Turkish: `--dil tr`.

## 4. Detail crops / Detay kırpımları
Crop regions are in 4K output pixels (`X:Y:W:H`), each shown 2× (nearest neighbour).
Bölgeler 4K çıktı pikselinde (`X:Y:G:Y`), her biri 2 kat (en yakın komşu) büyütülür.
```powershell
$ortak = @(".\klipler\tos_bozuk_1200k.mp4", ".\cikti\tos_deblock1.mkv", ".\klipler\tos_ref.mkv", "--video-yok", "--detay-yan",
           "--sol-ad", "1.2 Mbps source", "--adlar", "Result (deblock 1)", "Original (reference)")
py -3.14 yerel\karsilastir.py @ortak --anlar 1   --kirp 2040:430:480:270  --ad docs_yuz      # face / yüz
py -3.14 yerel\karsilastir.py @ortak --anlar 5.2 --kirp 1150:1100:480:270 --ad docs_hareket  # fast motion / hızlı hareket
py -3.14 yerel\karsilastir.py @ortak --anlar 8   --kirp 200:900:480:270   --ad docs_ekran    # screen text / ekran yazısı
```
→ `cikti\karsilastirma\docs_*\t*_DETAY_yan.png`

## 5. Sliding comparison (WebP) / Kayar karşılaştırma (WebP)
```powershell
py -3.14 yerel\karsilastir.py .\klipler\tos_bozuk_1200k.mp4 .\cikti\tos_deblock1.mkv --mod kayar --kirp 1700:250:1280:534 `
    --anlar 1 --sol-ad "1.2 Mbps source" --adlar "Result" --ad docs_kayar
.\tools\ffmpeg\bin\ffmpeg.exe -i .\cikti\karsilastirma\docs_kayar\kayar_tos_deblock1.mkv -vf "fps=24,scale=960:-2:flags=lanczos" `
    -c:v libwebp -lossless 0 -quality 80 -compression_level 6 -loop 0 -an .\docs\demo_kayar.webp
```
A GIF of the same clip was 15 MB at 960 px (7,99 MB at 800 px / 10 fps with visible banding), so WebP (3,8 MB, 24 fps) is used.
Aynı klibin GIF'i 960 px'te 15 MB (800 px / 10 fps'te 7,99 MB, belirgin bantlanma) olduğu için WebP (3,8 MB, 24 fps) kullanıldı.
