# P0 Environment Report

Tanggal probe: 2026-10-07
Host: Ubuntu `192.168.100.192`

## Hasil

| Area | Temuan | Implikasi |
|---|---|---|
| MT5 | Terminal berjalan melalui Wine dan dikelola systemd | Bridge dapat ditempatkan di terminal yang sama |
| Simbol | Simbol broker aktif adalah `XAUUSDc` | Nama simbol harus berasal dari konfigurasi, bukan hard-code `XAUUSD` |
| Snapshot | Quote, posisi, order, dan candle M5/M15/H1/H4/D1 tersedia | Cukup untuk probe dan SHADOW awal |
| Candle | Snapshot lama menyertakan candle aktif indeks 0 | Engine wajib membuang candle yang belum tertutup; bridge v2 sebaiknya tidak menerbitkannya |
| Symbol spec | Tick size/value, lot step, stop level, dan filling mode belum lengkap | Bridge v2 wajib menambah seluruh properti sebelum DEMO |
| Hermes | CLI one-shot tersedia; profil dapat dipilih dan laporan usage dapat ditulis ke JSON | Adapter awal memakai argv tanpa shell, timeout, dan parser respons ketat |
| Eksekusi | Trade manager lama dapat bertukar berkas perintah/hasil dengan EA | Hanya menjadi bukti jalur integrasi; kode lama tidak disalin |

## Kontrak bridge v2 minimum

Setiap snapshot harus memiliki `schema_version`, `snapshot_id`, `captured_at`, `server_time`, `symbol`, `quote`, `symbol_spec`, `account`, `positions`, `orders`, dan candle tertutup per timeframe. `symbol_spec` wajib memuat digits, point, tick size, tick value, contract size, volume min/max/step, stops level, freeze level, dan filling modes.

Setiap permintaan order harus memiliki `request_id`, `setup_id`, `created_at`, `expires_at`, mode, simbol, jenis aksi, arah, volume, harga, SL, TP, deviasi maksimum, dan komentar. EA harus menyimpan `request_id` yang sudah diproses agar retry tidak menggandakan order.

## Kekurangan sebelum SHADOW

- Bridge v2 belum dibuat.
- Belum ada penyimpanan PostgreSQL/Parquet.
- Belum ada rekonsiliasi order/fill versi baru.
- Belum ada dataset replay milik proyek baru.

## Kekurangan sebelum DEMO

- Validasi symbol spec dan pembulatan volume/harga belum diuji terhadap broker.
- Idempotency, restart recovery, partial fill, requote, dan disconnect belum diuji.
- Batas kerugian harian harus memakai ledger broker yang direkonsiliasi.
- Pemilik harus menerima konfigurasi strategi dan risiko yang dibekukan.
