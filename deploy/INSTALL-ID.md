# Pemasangan pada tiap profil Chrome

Alamat API privat: `https://your-node.your-tailnet.ts.net:8443`

1. Buka `chrome://extensions` pada profil yang berisi akun Gemini tujuan.
2. Aktifkan **Developer mode**, pilih **Load unpacked**, lalu pilih folder `build/chrome-sync` di repo ini. Jika menggunakan ZIP, ekstrak dahulu lalu pilih folder yang berisi `manifest.json`.
3. Klik kanan ikon ekstensi, pilih **Gemini sync settings**.
4. Masukkan alamat API di atas dan kode pairing sementara dari administrator.
5. Izinkan akses Google dan server privat saat Chrome meminta persetujuan.
6. Biarkan Chrome aktif. Perubahan cookie dikirim otomatis; tidak perlu mengekspor JSON.
7. Refresh tab bersifat opsional. Pilih tab Gemini yang sudah terbuka, interval 30 menit, lalu aktifkan jika diperlukan. Refresh tab latar belakang tetap dapat memutus jawaban yang sedang dibuat.

Kode pairing tidak disimpan di dokumen ini. Administrator membuatnya pada server melalui CLI penerima; masa berlaku sepuluh menit dan hanya satu penggunaan. Token perangkat tidak boleh dibagikan.

Setiap profil tambahan harus dipasangkan ke target backend tersendiri, bukan ke target profil lain. Pairing target pertama menggunakan `main-profile`. Akun Google yang berubah memerlukan penanganan ulang oleh administrator agar akun berbeda tidak otomatis menimpa sesi lama.

Pengujian otomatis dengan Chromium memakai profil sementara, bukan profil Google pengguna. Pemasangan pada profil asli dan persetujuan izin Chrome harus dilakukan pengguna sendiri.
