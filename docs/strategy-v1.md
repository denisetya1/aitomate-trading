# Strategi trend-pullback-v1.0.0

Status: dibekukan untuk implementasi REPLAY. Perubahan angka membuat versi strategi baru.

## Pasar dan waktu

- Satu simbol broker: `XAUUSDc`.
- Semua keputusan menggunakan candle tertutup.
- H1 menentukan arah, M15 menentukan pullback, M5 menentukan breakout dan retest.

## Arah H1

Buy hanya bila EMA 50 di atas EMA 200, dua swing high terkonfirmasi terakhir naik, dan dua swing low terkonfirmasi terakhir naik. Sell menggunakan syarat kebalikan. Swing memakai tiga candle di kiri dan tiga di kanan; karena itu swing baru sah setelah tiga candle berikutnya tertutup.

## Pullback M15

EMA 20 menjadi zona pullback. Toleransi zona adalah `0.25 × ATR(14)`. Untuk buy, low menyentuh zona dan close tetap di atas EMA. Untuk sell, high menyentuh zona dan close tetap di bawah EMA.

## Trigger M5

Breakout harus melewati swing terkonfirmasi terakhir dengan buffer `0.10 × ATR(14)`. Retest harus terjadi paling lambat tiga candle M5 tertutup setelah breakout, menyentuh level dalam toleransi `0.15 × ATR(14)`, lalu kembali close di sisi arah breakout.

## SL, target, dan masa berlaku

- SL berada di luar swing M5 lawan arah dengan buffer `0.20 × ATR(14)`.
- Target adalah level struktur H1 terkonfirmasi berikutnya yang berada di sisi profit.
- Kandidat ditolak bila tidak ada target struktur atau net reward/risk di bawah 1.5 setelah estimasi spread.
- Kandidat kedaluwarsa setelah tiga candle M5.

## Risiko

- Risiko awal `0.25%` equity per transaksi.
- Maksimum satu posisi atau order aktif yang berasal dari engine.
- Tidak menambah posisi rugi.
- Batas rugi harian `1%` dari equity awal hari.
- Ukuran lot dihitung dari tick size dan tick value broker, lalu dibulatkan ke bawah sesuai volume step.

## Peran Hermes

Hermes menilai apakah snapshot mendukung kandidat dan apakah ada konflik yang tampak pada data yang diberikan. Hermes tidak boleh membuat setup baru atau mengubah parameter kandidat.
