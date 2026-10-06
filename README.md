# AI Trading Engine

Engine baru untuk satu strategi XAUUSD. Strategi, risiko, harga entry, SL, TP, dan ukuran lot dihitung secara deterministik. Hermes hanya boleh mengembalikan `APPROVE`, `REJECT`, atau `ABSTAIN` untuk kandidat yang sudah lengkap.

## Status

- Mode awal: `REPLAY`
- Eksekusi order: adapter replay saja
- Akun live: dinonaktifkan
- Simbol broker yang terdeteksi pada mesin MT5: `XAUUSDc`
- Integrasi Hermes awal: proses CLI terisolasi, tanpa shell

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
