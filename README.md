# BosNRoll — Günlük 5N1K Teknoloji Bülteni

Her gün gündemdeki teknoloji haberlerinden yaklaşık 5 dakikalık, yatay bir YouTube videosu üretir.
Her haber **5N1K** (Kim, Ne, Nerede, Ne zaman, Nasıl, Neden) ile anlatılır ve ardından **mantığı sorgulanır**:
iddiayı kim yapıyor, başlık haberin içeriğiyle örtüşüyor mu, kıyaslama adil mi?

## Akış

```
RSS haberleri ──► Claude: haber seçimi + 5N1K senaryosu ──► VoxCPM2: kanal sahibinin sesiyle seslendirme
      ──► HTML şablonundan kareler (Playwright) ──► FFmpeg: video ──► YouTube (private)
```

| Adım | Dosya | Not |
|---|---|---|
| Haber toplama | `src/bosnroll/news.py` | Kaynaklar `config.yaml` içinde; kullanılan haberler `episodes/seen_urls.json`'a yazılır |
| Senaryo | `src/bosnroll/script.py` | Claude API, JSON şemasıyla; haber metni dışında bilgi uydurmaması istenir |
| Seslendirme | `src/bosnroll/tts.py` | [VoxCPM2](https://huggingface.co/openbmb/VoxCPM2) ile kanal sahibinin sesinin klonu (Apache 2.0; CPU'da 5 dk ses ≈ 35-40 dk). Hızlı yedek: [EMA Lightning](https://huggingface.co/canberkkkkkk/ema-lightning) (`tts.engine: ema`). Cümle cümle üretildiği için altyazı zamanlaması kesin |
| Görseller | `src/bosnroll/templates/scene.html` | Kanalın neon turuncu kimliği; giriş, başlık, 5N1K kartları, mantık sorgusu, karar, kapanış ve kapak görseli |
| Video | `src/bosnroll/render.py` | 4K (3840×2160; `video.scale: 1` ile 1080p), altta ilerleme çizgisi, `assets/music.mp3` varsa alttan müzik |
| Yükleme | `src/bosnroll/upload.py` | Video **private** yüklenir; YouTube Studio'dan kontrol edip yayınlarsınız |

## Kurulum (bir kez)

1. **Claude API anahtarı:** [console.anthropic.com](https://console.anthropic.com) → API Keys.
   GitHub'da *Settings → Secrets and variables → Actions* altına `ANTHROPIC_API_KEY` olarak ekleyin.
2. **YouTube API:**
   - [Google Cloud Console](https://console.cloud.google.com)'da proje açın, **YouTube Data API v3**'ü etkinleştirin.
   - *OAuth consent screen* oluşturun, kendinizi test kullanıcısı ekleyin.
   - *Credentials → OAuth client ID → Desktop app* oluşturup JSON'u `client_secret.json` olarak indirin.
   - Kendi bilgisayarınızda: `pip install -r requirements.txt && PYTHONPATH=src python -m bosnroll auth client_secret.json`
   - Çıkan refresh token'ı ve JSON'daki client id/secret'ı `YT_REFRESH_TOKEN`, `YT_CLIENT_ID`, `YT_CLIENT_SECRET` secret'ları olarak ekleyin.
   - Not: Doğrulanmamış (audit edilmemiş) API projelerinin yüklediği videolar private kalır. Otomatik yayın isterseniz
     Google'ın [API denetimine](https://support.google.com/youtube/contact/yt_api_form) başvurun. Private yükleme zaten onay adımı olarak işimize yarıyor.
   - Özel kapak görseli için kanalın telefonla doğrulanmış olması gerekir.
3. **Ses kaydı:** Repo herkese açık olduğu için ses kaydı `assets/voice/voice.tar.gpg` olarak şifreli duruyor.
   Şifreyi `VOICE_PASSPHRASE` secret'ı olarak ekleyin. Yeni bir kayıtla değiştirmek için ~10 sn'lik temiz bir kaydı
   `assets/voice/ref.wav` (16 kHz mono), metnini `assets/voice/ref.txt` olarak koyup şifreleyin:
   `tar -C assets/voice -cf - ref.wav ref.txt | gpg --symmetric --cipher-algo AES256 -o assets/voice/voice.tar.gpg`
4. İş akışı (`.github/workflows/daily.yml`) her gün 07:47'de (İstanbul) çalışır. *Actions → Günlük bülten → Run workflow* ile elle de başlatılabilir.

## Yerelde çalıştırma

```bash
pip install -r requirements.txt
python -m playwright install chromium
export PYTHONPATH=src ANTHROPIC_API_KEY=...
python -m bosnroll script   # episodes/<tarih>/episode.json
python -m bosnroll video    # episodes/<tarih>/video.mp4, thumbnail.jpg, subtitles.srt
python -m bosnroll upload
```

`episode.json` elle düzenlenebilir; `video` komutu düzenlenmiş senaryodan yeniden üretir.

## Ayarlar

- **Telaffuz:** Yanlış okunan yabancı kelimeleri `config.yaml` → `lexicon` altına ekleyin (`iPhone: ayfon`). Altyazıda orijinal yazım kalır.
- **Süre:** `script.target_words` (EMA Lightning ≈ 2,4 kelime/sn; 700 kelime ≈ 5 dk).
- **Hız:** `tts.speed`.
- **Müzik:** Telifsiz bir parçayı `assets/music.mp3` olarak koyun (ör. YouTube Ses Kitaplığı).

## Lisanslar

- Seslendirme modelleri VoxCPM2 ve EMA Lightning: Apache 2.0
- Yazı tipleri Oswald ve Inter: SIL Open Font License (`assets/fonts/`)
