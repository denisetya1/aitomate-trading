# AI Trading Engine

Engine baru untuk satu strategi XAUUSD. Strategi, risiko, harga entry, SL, TP, dan ukuran lot dihitung secara deterministik. Hermes hanya boleh mengembalikan `APPROVE`, `REJECT`, atau `ABSTAIN` untuk kandidat yang sudah lengkap.

Mode operasi saat ini adalah `REAL` otomatis dengan volume tetap `0.01` lot. Kandidat melewati pemeriksaan data, strategi, risiko, dan validasi Vission sebelum ditulis ke bridge MT5. EA menolak request kedaluwarsa, simbol yang berbeda, volume selain `0.01`, SL/TP yang tidak valid, dan request duplikat.

Eksekusi memakai `deploy/vission/vission-trade`; perintah ini kembali memeriksa umur quote, spread, posisi aktif, ukuran risiko, dan reward/risk sebelum meneruskan order ke MT5. Telegram menerima laporan hasil beserta receipt broker setelah setiap percobaan eksekusi.

`aitomate-scanner.timer` memeriksa snapshot setiap 30 detik. Pemindai diam ketika tidak ada setup, data basi, risiko ditolak, atau Vission tidak menyetujui kandidat. Percobaan eksekusi memiliki jeda 15 menit dan batas satu posisi atau pending order.

Scanner juga memantau seluruh posisi pada akun MT5 Auto. Setiap tiket baru, termasuk posisi manual, mengirim satu notifikasi Telegram yang memuat sumber, arah, lot, harga buka, SL, TP, dan floating profit.

Setiap kandidat yang lolos pemeriksaan data dan risiko dikirim ke Telegram sebelum penilaian AI. Keputusan Vission dikirim terpisah sebagai `APPROVE`, `REJECT`, atau `ABSTAIN`, lengkap dengan probabilitas dan alasannya. Eksekusi tetap otomatis setelah persetujuan dan gate pembelajaran.

Instance MT5 Auto membersihkan layout chart `Default` saat startup, lalu membuka satu chart XAUUSDc H1 dengan `AITradingBridgeV2`. Ini mencegah chart duplikat bertambah setiap service direstart.

`AITradingBridgeV2` menyimpan screenshot chart MT5 Auto setiap 30 detik ke `MQL5/Files/AITradingEngineV2/latest.png`. Vission memakai screenshot ini bersama snapshot MT5 Auto dan tidak membaca screenshot `TelegramTradeManager` dari akun manual.

## Learning loop

Vission memberikan probabilitas TP tercapai sebelum SL. Selama 30 hasil pertama, gate memakai probabilitas AI dengan ambang minimum 70% dan expected R minimum 0.20. Setelah tersedia sedikitnya 30 trade tertutup per arah, probabilitas dikalibrasi dengan posterior beta-binomial dan hasil riil broker. Sistem berhenti mengeksekusi bila expectancy historis tidak positif atau batas bawah probabilitas turun di bawah 45%.

Setiap prediksi, fitur, deal broker, profit bersih, dan R-multiple disimpan di PostgreSQL. Data ini dipakai untuk evaluasi walk-forward dan promosi versi strategi; strategi live tidak menulis ulang parameternya sendiri dari beberapa kemenangan terbaru.

## Status

- Mode awal: `REPLAY`
- Eksekusi order: adapter replay saja
- Akun live: dinonaktifkan
- Simbol broker yang terdeteksi pada mesin MT5: `XAUUSDc`
- Integrasi Hermes awal: agent `vission` melalui proses CLI terisolasi, tanpa shell

## Menjalankan pemeriksaan

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m ai_trading_engine.cli doctor --market-json /path/to/market.json
```

`doctor` hanya membaca data, menyamarkan nilai akun, dan tidak mengirim order.

## Batas sistem

1. MT5 bridge menghasilkan snapshot harga, spesifikasi simbol, candle tertutup, posisi, order, dan hasil eksekusi.
2. Engine memvalidasi data dan membuat kandidat dari aturan `trend-pullback-v1.0.0`.
3. Risk gate memutuskan apakah kandidat layak dikirim ke validator.
4. Hermes menilai konteks kandidat tanpa mengubah parameter transaksi.
5. Execution adapter memeriksa ulang quote dan risiko sebelum tindakan apa pun.

Lihat [ADR batas sistem](docs/adr/0001-system-boundaries.md), [laporan P0](docs/p0-environment-report.md), dan [aturan strategi](docs/strategy-v1.md).

## PostgreSQL

Database lokal dijalankan dengan Docker Compose dan hanya mendengarkan pada `127.0.0.1:5433`.

```bash
cd deploy/postgres
cp .env.example .env
# ganti POSTGRES_PASSWORD sebelum menjalankan container
docker compose up -d
docker compose ps
```

Volume `aitomate_postgres_data` menyimpan data di luar lifecycle container. Skema awal mencakup snapshot pasar, kandidat trading, keputusan Vission, event eksekusi, risk state harian, dan heartbeat engine.
