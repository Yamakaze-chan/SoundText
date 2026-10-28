# SoundText v10.4 — Sửa splash và chuẩn bị bản EXE portable

Bản cập nhật này **không chỉnh engine ASR, dịch NLLB, phân biệt giọng, VAD, luồng ghi âm, lưu TXT hoặc giao diện phụ đề**. Chỉ sửa đường dẫn/metadata và thêm quy trình build Windows.

- **Giữ cấu hình cá nhân khi cập nhật:** ZIP v10.4 chỉ chứa `branding.default.json` và `config_stt_vi_ja.default.json`, **không chứa** `branding.json` hay `config_stt_vi_ja.json` để tránh ghi đè splash/ngôn ngữ do bạn đã sửa. Nếu cài mới, app tự sao chép các file `.default.json` thành tên tương ứng lúc khởi động lần đầu. Ảnh mặc định được đưa vào `assets/default-branding/`; thư mục `assets/branding/` của bạn không bị chép đè.
- **Splash đúng ảnh:** ưu tiên đường dẫn trong `branding.json` (cho phép đường dẫn tuyệt đối hoặc tương đối, khuyến khích `assets/branding/my-splash.png`). Ảnh không tồn tại/không đọc được/JSON sai sẽ hiển thị cảnh báo ở cửa sổ đang tải, không im lặng đổi về hình stock. Splash tồn tại đến khi tải xong model; giao diện chính vẫn mở ở trạng thái chưa ghi âm.
- **EXE:** `SoundText.spec` và `scripts/build_windows_exe.bat` cho bản `onedir` trên Windows. `SoundText.exe` và `_internal/` đi cùng nhau; **model lớn và output bên ngoài**. `entry_soundtext.py` chuẩn hóa thư mục chạy dựa vào vị trí EXE nên shortcut không làm sai đường dẫn. Icon EXE và Windows File Details được tạo từ `branding.json` tại lúc build. Xem chi tiết trong `README_EXE.md`.
- **Chú ý:** đây là **bộ mã đã chuẩn bị build**, chưa có `SoundText.exe` được biên dịch thực tế do môi trường kiểm thử không phải Windows và không có đủ các DLL/runtime chuyên dụng. Bạn cần thực hiện build và thử trên PC Windows trước khi phân phối.

Nếu bạn đang dùng bản cũ, **sao lưu `branding.json`, `config_stt_vi_ja.json`, `assets/branding/`, `models/`, `output/`**. Giải nén ZIP v10.4 đè code. Sau khi khởi động, app tiếp tục dùng chính `branding.json` cũ. Với bản portable, **không được sao chép riêng EXE**; phải mang cả thư mục được tạo trong `dist/SoundText/`.

---

# SoundText v10.3 — MIC đơn giản, REMOTE gọn và tải model ở splash

## Thay đổi trong v10.3

- **MIC không sử dụng bộ so khớp/chống vọng âm thanh máy (`MicDuplexGuard`) và không chạy phân biệt speaker.** Microphone đi thẳng qua bước giảm nhiễu nhẹ đã có rồi đến Silero VAD và ASR. Nhánh REMOTE vẫn giữ phân biệt người nói như trước. Chế độ **Chỉ âm thanh máy** vẫn không khởi động microphone. Khi dùng loa ngoài, âm thanh phát ra vẫn có thể lọt vào MIC vật lý; bản này ưu tiên không làm mất/cắt nhầm giọng MIC, không tuyên bố khử vọng tuyệt đối.
- **Hai cửa sổ phụ đề nổi giữ nguyên.** Các câu REMOTE liên tiếp thuộc cùng speaker sẽ nối thành **một đoạn** (ngăn bằng dấu cách), thay vì lặp nhãn REMOTE và hai dòng trống sau mỗi câu. Khi đổi nguồn hoặc đổi speaker thì xuống dòng một lần. Bản dịch vẫn chờ ASR Final.
- **Splash chờ tải thật sự.** Sau khi chạy chương trình, cửa sổ chính được tạo ở trạng thái ẩn. Splash từ `branding.json` hiển thị và được cập nhật tiến độ trong khi luồng nền tải ASR cho hai nguồn, hai phiên VAD, model speaker cho REMOTE nếu bật, cùng NLLB ONNX **bao gồm trọng số** nếu dịch đang bật trong cấu hình. Sau khi quá trình này hoàn tất, cửa sổ chính mới hiện và splash đóng; không giữ thêm theo timer. **Việc thu âm vẫn phải nhấn Bắt đầu**.
- Model khởi tạo trước được **chuyển thẳng** cho phiên ghi âm đầu tiên; không chỉ warmup rồi tải lại. Nếu bạn thay ngôn ngữ ASR trước khi nhấn Bắt đầu, model khác lựa chọn ban đầu sẽ cần tải lại. Các phiên thứ hai trở đi cũng có thể cần khởi tạo lại model. Nếu thiếu model ASR/VAD bắt buộc, giao diện hiển thị lỗi và vô hiệu hóa nút Start thay vì báo sẵn sàng. Nếu model dịch/speaker tùy chọn không có, chương trình hiển thị cảnh báo và vẫn cho phép ghi âm.
- Giữ file `Noi_dung/Noi_dung_cuoc_hop.txt`, JSONL debug, `branding.json`, giao diện CustomTkinter và nút Start/Stop.

**Kiểm thử tại môi trường tạo ZIP:** 69 bài đạt, 5 bài bỏ qua do thiếu môi trường GUI/Windows và thiết bị thật. Model nặng, splash và microphone chưa được thử trực tiếp trên PC Windows/i5. Khi cài, giữ `models/`, `output/` và backup `config_stt_vi_ja.json`, `branding.json`.

---

# SoundText v10.2 — Audio source isolation + easy TXT export

This edition replaces the v7 PySide6 interface with **CustomTkinter**. All audio/ASR, Silero VAD, NLLB ONNX, speaker diarization, translation workers, transcript storage, and optimized JA ↔ AUTO language switching are kept from v7; no new AI models are required.


## v10.2: capture-source routing and simple TXT

- **Chỉ âm thanh máy** now starts and reads **only** WASAPI loopback. It does not open the microphone, update its volume meter, or feed the MIC diarizer/ASR. **Chỉ MIC** similarly does not open system loopback. Changes during a recording start/stop the corresponding physical device on a background thread, rather than merely hiding its transcript.
- **MIC + REMOTE** checks that a selected microphone is not `Stereo Mix` / WASAPI loopback (including explicit device indices). In v10.3 the echo guard no longer processes MIC, because it could suppress genuine speech. Audio playing through speakers can still leak into a real microphone; headphones are recommended.
- Each session has **`Noi_dung/Noi_dung_cuoc_hop.txt`**, an easy-to-read UTF-8 file with timestamps, source, optional speaker, original speech and Vietnamese translation when enabled. The **Mở thư mục lưu** button opens this clean `Noi_dung` folder. All the original JSONL, transcripts, recovery journal and diagnostic files remain **unchanged in the parent session directory**, so troubleshooting and interrupted-session recovery still work. No additional AI models or C++ libraries are required.
- The app still opens idle; source choices and the **Ghi & dịch / Chỉ ghi lời thoại** selector are unchanged.

### How to verify on Windows

1. Save a copy of your `config_stt_vi_ja.json` and `branding.json`, then extract the update over SoundText. Keep `models/` and `output/`.
2. Choose **Chỉ âm thanh máy** and press **Bắt đầu ghi âm**. The MIC meter should stay at zero; logs should show **SYSTEM capture started** but **not MIC capture started**.
3. Stop, choose **MIC + REMOTE**, and play system audio with your microphone silent. Check that MIC is not repeating the system feed. If you play sound through room speakers, acoustic leakage may remain; test headphones and the configured microphone device before tuning echo thresholds.
4. Press **Dừng & lưu** and **Mở thư mục lưu**: the Explorer window contains just `Noi_dung_cuoc_hop.txt`. The other technical and recovery files are one folder above.

## Key behavior

- SoundText starts **idle**: opening the window does not start audio capture, load ASR/MT models or create a new meeting. Press **Bắt đầu ghi âm** to start; press **Dừng & lưu** to finish. You can record another meeting without restarting the app.
- Light CustomTkinter interface with source controls and a clear session status. Technical model names and the former "XỬ LÝ CỤC BỘ" sidebar block have been removed from the main window; underlying engines remain unchanged.
- **Before starting a session**, choose **Ghi & dịch** (speech transcript plus Vietnamese translation) or **Chỉ ghi lời thoại** (transcript only, no translation engine/worker initialized). You cannot change recording mode mid-session; stop and start another session to change it. This is a per-session UI setting and does not overwrite `config_stt_vi_ja.json`.
- Floating subtitles remain **two independently movable, resizable windows** (original STT and Vietnamese translation). Each pane offers the same display selector: **Cả hai / Chỉ lời thoại / Chỉ bản dịch**. During **Chỉ ghi lời thoại**, only the original pane is available; the app does not translate in the background. Font sizes 5–50 stay separate, and automatic following, linked scrolling and expansion are retained. Original STT continues displaying ASR partials; when translation is on, Vietnamese appears after final results.
- **Mở thư mục lưu** and **Sao chép hội thoại** remain available. Branding is **file-only**: edit `branding.json` with a text editor; there is no branding dialog in the application.
- Splash does **not** use a fixed timer: if enabled, it stays visible until startup model preloading completes (or reports a critical error). Custom icons from `branding.json` now apply to the main window **and both floating subtitle windows**. On Windows, PNG icons are converted to a small cached ICO for titlebar/taskbar compatibility, then re-applied after CustomTkinter's delayed default icon.
- The app uses no PySide6. CustomTkinter relies on the Python-bundled Tk framework; Windows installations generally include Tk.

## Setup

1. Back up any custom `config_stt_vi_ja.json` or `branding.json` settings before extracting the archive over your installation. Do **not** delete `models/` or `output/`.
2. Open `SoundText.bat`, choose **19** to install **CustomTkinter + Pillow** only; or choose **11** to install all required Python packages.
3. Choose menu **1** for the application (or 2/3 to preselect the audio source). The app opens without recording. Choose source/language and **Nội dung: Ghi & dịch / Chỉ ghi lời thoại**, then click **Bắt đầu ghi âm**.
4. To stop and save, click **Dừng & lưu**. Use **Mở thư mục lưu** to open the latest session's `Noi_dung` folder with the single readable TXT (use the parent folder for debug files).
5. Menu **4** checks models and Python dependencies; menu **14** performs import checks.

All model downloads and caches stay under `./models/` using the existing batch launcher. This ZIP contains no AI model weights, recordings, or meeting data.

## Tests and limits

Pure Python tests and static syntax checking can run without the GUI dependency. This build was not exercised on the user's Windows/i5 installation or with a real sound device. The build environment could not download CustomTkinter, so **interactive appearance, window controls, and floating subtitle behavior need a Windows smoke test**. Keep v7 as backup until confirmed.

ASR, NLLB, diarization, VAD and language-switching models remain unchanged. v10.2 changes only audio device selection/echo detection, the session's readable export, and the GUI source meter/file-opening behavior.

## Edit app metadata outside the UI

Open `branding.json` in the SoundText project root using Notepad or your editor. For example:

```json
{
  "app_name": "SoundText",
  "tagline": "Phụ đề và biên bản hội thoại trực tiếp",
  "icon_path": "assets/branding/soundtext-icon.png",
  "splash_path": "assets/branding/soundtext-splash.png",
  "show_splash": true,
  "splash_duration_ms": 300
}
```

Save an **icon in PNG or ICO format** under `assets/branding/` and set `icon_path` to it. A splash may be PNG/JPG/GIF. You may also use an absolute path; on Windows use forward slashes (e.g. `C:/Users/You/Pictures/icon.png`) or escaped backslashes in JSON. Restart the app to load the new display name, window icon and splash art. On Windows, PNG icons are converted to an ICO under `assets/branding/.icon-cache/`, generated automatically. If an icon is missing or invalid, the program prints `[BRANDING WARN]` instead of silently failing. The icon on an existing `.bat` shortcut or packaged `.exe` is separate and will not change just by editing JSON. `splash_duration_ms` is maintained for backward compatibility but the splash is removed once the main UI is ready, not delayed for that amount. This edits the **running window metadata**, not Windows EXE version information or EXE icon.

## Floating subtitles

Click **Phụ đề nổi** to open the selected display mode. Use the **Cả hai / Chỉ lời thoại / Chỉ bản dịch** dropdown at the top of either pane to switch visible windows without touching the recording/translation pipeline. The choice **Chỉ lời thoại** hides the Vietnamese floating pane; it does **not** by itself disable translation. To avoid translation CPU usage entirely, set **Nội dung: Chỉ ghi lời thoại** **before** starting the session. Drag/resize panes independently; **Ẩn** or Escape hides the overlay. **Cuộn đôi** synchronizes scroll only when both panes are shown.

The underlying ASR/VAD/NLLB ONNX model algorithms and session export formats are unchanged. v10.3 modifies the microphone routing (speaker/echo bypass), startup resource preparation, controller handoff and REMOTE subtitle formatting. On Windows, please smoke-test the UI because CustomTkinter was not available in the build environment.
