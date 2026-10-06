# ADR 0001: Batas sistem dan otoritas keputusan

Status: diterima untuk REPLAY/SHADOW
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
