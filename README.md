# GİZA veri servisi

GİZA Energy Suite'in kullandığı piyasa verileri ve sürüm bilgisi.

- `veri/ptf/aylik/YYYY-AA.json` — dönemin saatlik PTF'si (TL/MWh)
- `veri/ptf/gunluk/YYYY-AA-GG.json` — günün 24 saatlik PTF'si (ertesi gün ~14:00'te eklenir)
- `veri/yekdem.json` — gerçekleşen YEKDEM birim maliyeti (TL/MWh)
- `veri/manifest.json` + `manifest.sig` — dosyaların SHA-256 özetleri, GİZA veri anahtarıyla imzalı
- `surum/surum.json` + `surum.sig` — güncel program sürümü, GİZA sürüm anahtarıyla imzalı

Kaynak: EPİAŞ Şeffaflık Platformu. Veriler her gün GitHub Actions ile güncellenir
(`.github/workflows/veri.yml`). Program imzası doğrulanmayan hiçbir dosyayı kullanmaz.
