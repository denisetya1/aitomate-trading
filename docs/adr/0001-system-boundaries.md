# ADR 0001: Batas sistem dan otoritas keputusan

Status: diamendemen untuk REAL

Amendemen 2026-10-08: strategi aktif diubah menjadi scalping M15/M5. Bias memakai EMA20/EMA50 M15, momentum memakai EMA9/EMA21 M5, dan pemicu memakai breakout lima candle M5. Target ditetapkan 2,2R, scanner berjalan setiap 10 detik, batas spread XAUUSDc 0,400, dan jeda eksekusi 5 menit.

Amendemen 2026-10-07: Deni memilih aktivasi langsung `REAL` otomatis untuk akun MT5 Auto. Eksekusi tidak memerlukan konfirmasi manusia setelah strategi deterministik, pemeriksaan risiko, dan Vission semuanya menyetujui kandidat. Eksekusi tetap dibatasi pada XAUUSDc, volume tetap 0.01 lot, satu posisi atau pending order, quote maksimum lima detik, dan kandidat maksimum dua candle M5. Jalur `DEMO` dan `SHADOW` tidak menjadi prasyarat operasional setelah keputusan ini.
Tanggal: 2026-10-07

## Keputusan

Engine baru berdiri sebagai proyek terpisah. Implementasi trade manager Telegram lama tidak disalin. Bridge MT5 lama hanya dipakai sebagai bukti bahwa Wine, pembacaan quote, candle, posisi, order, dan penulisan berkas bersama dapat berjalan.

Aturan strategi dan risiko menjadi satu-satunya sumber parameter transaksi. Hermes menerima snapshot kandidat yang sudah lengkap dan hanya boleh menjawab:

- `APPROVE`: kandidat yang sama boleh melanjutkan pemeriksaan akhir.
- `REJECT`: kandidat dihentikan.
- `ABSTAIN`: kandidat dihentikan karena bukti tidak cukup atau validator gagal.

Respons Hermes tidak boleh mengubah simbol, arah, entry, lot, SL, TP, masa berlaku, atau ID setup. Respons yang rusak, terlambat, atau memiliki ID berbeda diperlakukan sebagai `ABSTAIN`.

## Urutan pengamanan

1. Hanya candle tertutup masuk ke strategi.
2. Data dan spesifikasi simbol divalidasi.
3. Strategi membuat kandidat deterministik.
4. Risk gate memeriksa kandidat sebelum Hermes.
5. Hermes menilai kandidat bila diaktifkan.
6. Quote, spread, masa berlaku, posisi, dan risiko diperiksa ulang.
7. Adapter eksekusi menerima perintah yang memiliki idempotency key.
8. Reconciliation membandingkan permintaan dengan keadaan MT5.

## Mode

- `REPLAY`: data historis atau fixture, tanpa koneksi order.
- `SHADOW`: data pasar berjalan, keputusan dicatat, tanpa order.
- `DEMO`: order hanya ke akun demo yang diizinkan secara eksplisit.
- `REAL`: dikunci dalam kode dan konfigurasi awal. Aktivasi memerlukan keputusan terpisah setelah bukti replay, shadow, demo, dan rekonsiliasi tersedia.

## Konsekuensi

Kegagalan Hermes tidak menghentikan pengumpulan data, tetapi menahan transaksi yang membutuhkan validasi AI. Strategi tetap dapat diuji tanpa Hermes. Keputusan dan alasan disimpan agar setiap kandidat dapat direproduksi.
