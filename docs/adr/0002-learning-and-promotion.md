# ADR 0002: Learning dan promosi strategi

Status: diterima

Vission belajar dari hasil broker melalui probabilitas yang dicatat untuk setiap kandidat. Sistem mencocokkan deal masuk dan keluar berdasarkan position ID, menghitung profit bersih setelah komisi dan swap, lalu menyimpan win/loss dan R-multiple.

Eksekusi membutuhkan probabilitas terkalibrasi minimal 70% dan expected R minimal 0.20. Sebelum ada 30 hasil tertutup per arah, probabilitas AI dipakai sebagai cold start. Setelah batas tersebut, posterior beta-binomial per arah dicampur dengan probabilitas AI. Gate juga mensyaratkan expectancy R positif dan batas bawah probabilitas 90% sedikitnya 45%.

Perubahan parameter atau prompt diperlakukan sebagai versi challenger. Challenger tidak boleh menggantikan versi live hanya berdasarkan win rate. Promosi memerlukan setidaknya 100 trade out-of-sample, expectancy positif setelah biaya, drawdown maksimum delapan R, dan evaluasi walk-forward tanpa look-ahead. Seluruh percobaan harus dicatat agar selection bias dapat dihitung.

Win rate bukan sasaran tunggal. Sistem membandingkan win rate bersama payoff ratio, expectancy R, drawdown, biaya, jumlah sampel, dan stabilitas lintas periode.
