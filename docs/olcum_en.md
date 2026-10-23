Reference: `tears_of_steel_1080p_film_411s.mkv` (240 frames). VMAF models vmaf_v0.6.1 and vmaf_v0.6.1neg (NEG does not reward sharpening/contrast gain). Processed videos downscaled to the reference resolution with lanczos.

### Whole clip

| video | resolution | bitrate¹ | VMAF | VMAF NEG | VMAF min | PSNR-Y (dB) | SSIM | frames |
|---|---|--:|--:|--:|--:|--:|--:|--:|
| Degraded 1.2 Mbps (baseline) | 1920x800 | 1,070 kb/s | **75.87** | **73.95** | 30.44 | 37.96 | 0.9732 | 240/240 |
| Deblock 0 | 3840x1600 | 16,593 kb/s | **82.33** | **72.32** | 34.51 | 35.92 | 0.9716 | 240/240 |
| Deblock 1 (default) | 3840x1600 | 16,918 kb/s | **82.39** | **70.91** | 35.25 | 35.20 | 0.9700 | 240/240 |
| Deblock 2 | 3840x1600 | 17,062 kb/s | **82.26** | **70.41** | 35.26 | 34.95 | 0.9693 | 240/240 |
| Deblock 3 | 3840x1600 | 17,057 kb/s | **82.13** | **69.96** | 35.19 | 34.74 | 0.9686 | 240/240 |

¹ Bitrate is for information only: the output is 4K/10-bit and therefore larger; it is not a quality comparison.

### Section 4–6 s

| video | VMAF | VMAF NEG | VMAF min | PSNR-Y (dB) | SSIM | frames |
|---|--:|--:|--:|--:|--:|--:|
| Degraded 1.2 Mbps (baseline) | **58.80** | **57.22** | 30.44 | 33.74 | 0.9366 | 48 |
| Deblock 0 | **64.19** | **57.23** | 34.51 | 32.08 | 0.9338 | 48 |
| Deblock 1 (default) | **64.32** | **56.40** | 35.25 | 31.65 | 0.9320 | 48 |
| Deblock 2 | **64.17** | **56.06** | 35.26 | 31.49 | 0.9311 | 48 |
| Deblock 3 | **64.13** | **55.72** | 35.19 | 31.34 | 0.9301 | 48 |
