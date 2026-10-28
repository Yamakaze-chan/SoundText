# SoundText

**Phiên âm, ghi lại và dịch lời thoại theo thời gian thực trên Windows**  
**Tài liệu dành cho:** SoundText **v10.10 — 8 GB Safe EXE**  
**Giao diện:** CustomTkinter · **Nhận diện:** Gipformer / SenseVoice · **Dịch:** NLLB INT8 ONNX · **Xử lý:** CPU, local-first

> **Trạng thái:** Đây là README dành cho **mã nguồn v10.10** trong gói `SoundText_v10_10_8GB_Safe_EXE_FullSource.zip`, không phải tài liệu chung cho mọi bản SoundText trước đây. Tài liệu được đối chiếu với `main.py`, `SoundText.bat`, `SoundText.spec`, script build và các module `app/` của bản đó. Một số bước, đặc biệt EXE trên Windows, cần được kiểm tra trên máy đích; không nên hiểu việc build thành công là toàn bộ tính năng đã được chứng nhận hoạt động.

---

## Mục lục

1. [SoundText là gì?](#1-soundtext-là-gì)
2. [Các chức năng chính](#2-các-chức-năng-chính)
3. [Yêu cầu hệ thống](#3-yêu-cầu-hệ-thống)
4. [Cấu trúc mã nguồn và bản portable](#4-cấu-trúc-mã-nguồn-và-bản-portable)
5. [Cài đặt môi trường Python / Miniconda](#5-cài-đặt-môi-trường-python--miniconda)
6. [Chuẩn bị model AI](#6-chuẩn-bị-model-ai)
7. [Chạy SoundText từ source](#7-chạy-soundtext-từ-source)
8. [Hướng dẫn sử dụng giao diện](#8-hướng-dẫn-sử-dụng-giao-diện)
9. [Cửa sổ phụ đề nổi](#9-cửa-sổ-phụ-đề-nổi)
10. [File đầu ra, lưu phiên và phục hồi](#10-file-đầu-ra-lưu-phiên-và-phục-hồi)
11. [Cấu hình ứng dụng](#11-cấu-hình-ứng-dụng)
12. [Branding, icon và splash screen](#12-branding-icon-và-splash-screen)
13. [Kiến trúc kỹ thuật và các module](#13-kiến-trúc-kỹ-thuật-và-các-module)
14. [Đóng gói Windows EXE bằng PyInstaller](#14-đóng-gói-windows-exe-bằng-pyinstaller)
15. [Chế độ an toàn cho máy 8 GB RAM](#15-chế-độ-an-toàn-cho-máy-8-gb-ram)
16. [Kiểm thử và chẩn đoán](#16-kiểm-thử-và-chẩn-đoán)
17. [Các lỗi thường gặp và cách khắc phục](#17-các-lỗi-thường-gặp-và-cách-khắc-phục)
18. [Cập nhật, sao lưu và phân phối](#18-cập-nhật-sao-lưu-và-phân-phối)
19. [Giới hạn hiện tại và lưu ý bảo mật](#19-giới-hạn-hiện-tại-và-lưu-ý-bảo-mật)
20. [Checklist trước khi phát hành](#20-checklist-trước-khi-phát-hành)

---

## 1. SoundText là gì?

**SoundText** là ứng dụng desktop dành cho Windows, giúp:

- Thu lời nói từ **microphone** (MIC / YOU), **âm thanh đang phát trên máy** (REMOTE / SYSTEM) hoặc **cả hai nguồn**.
- Nhận diện tiếng Việt, tiếng Nhật, tiếng Anh, với lựa chọn tự nhận diện Nhật/Anh (**AUTO**) cho nhánh SenseVoice.
- Hiển thị lời thoại trong giao diện chính, đồng thời cho phép mở **hai cửa sổ phụ đề nổi độc lập**: lời gốc và bản dịch tiếng Việt.
- **Ghi & dịch** hoặc **Chỉ ghi lời thoại**, tùy từng phiên.
- Ghi lại nội dung cuộc họp thành **một file TXT dễ đọc**, song song với các file kỹ thuật phục vụ tìm lỗi và phục hồi.
- Phân biệt người nói trên nhánh **REMOTE** khi có các model diarization.

Chương trình hoạt động theo hướng **xử lý cục bộ**. Sau khi đã chuẩn bị đầy đủ model và thư viện, việc nhận diện/dịch thông thường không cần gửi nội dung hội thoại tới dịch vụ đám mây. **Các bước tải model và cài thư viện vẫn cần kết nối Internet.**

### Khái niệm MIC, YOU, REMOTE, SYSTEM

| Tên hiển thị / tên kỹ thuật | Ý nghĩa |
|---|---|
| MIC / YOU / `mic` | Âm thanh đi vào thiết bị microphone của máy. |
| REMOTE / SYSTEM / `system` | Âm thanh đầu ra Windows được thu bằng loopback, ví dụ video hoặc cuộc họp đang phát trên máy. |
| BOTH / MIC + REMOTE | Bật đồng thời hai nguồn độc lập. |
| ASR | Nhận dạng giọng nói thành chữ. |
| VAD | Phát hiện đoạn âm thanh có tiếng nói. |
| Diarization | Gắn nhãn người nói như S1, S2...; **không đồng nghĩa** tách sạch hai giọng đang nói chồng nhau. |
| Final / Partial | Câu nhận diện hoàn tất / văn bản tạm thời đang nói. |

## 2. Các chức năng chính

### 2.1 Thu âm theo lựa chọn

SoundText có ba chế độ nguồn:

| Chế độ | Microphone | Âm thanh máy | Ví dụ |
|---|---|---|---|
| **Chỉ MIC** | Bật | Tắt | Ghi lời mình nói. |
| **Chỉ âm thanh máy** | Tắt | Bật | Xem video, nghe cuộc họp online bằng tai nghe. |
| **MIC + REMOTE** | Bật | Bật | Ghi cả lời mình và cuộc họp đang phát. |

**Quan trọng:** chế độ **Chỉ âm thanh máy** không mở thiết bị microphone. Ở chế độ **MIC + REMOTE**, SoundText không còn dùng bộ so khớp âm thanh REMOTE để lọc lời nói trên MIC, nhằm tránh làm mất tiếng. Nếu loa ngoài phát lớn, microphone vật lý vẫn có thể thu lại tiếng từ loa: dùng **tai nghe** sẽ hạn chế hiện tượng ghi lặp tốt hơn.

### 2.2 Hai chế độ nội dung

- **Ghi & dịch:** nhận dạng lời gốc và, với câu tiếng Nhật/Anh hoàn tất, dịch sang tiếng Việt bằng NLLB ONNX.
- **Chỉ ghi lời thoại:** chỉ nhận dạng và lưu lời gốc; **không tạo worker dịch** cho phiên này.

Chọn chế độ nội dung **trước khi nhấn Bắt đầu ghi âm**. Muốn đổi sang chế độ còn lại, hãy nhấn **Dừng & lưu**, sau đó tạo phiên mới. Chỉ ẩn cửa sổ dịch **không** tương đương với tắt engine dịch.

### 2.3 Nhận diện người nói

- Nhánh **REMOTE** có thể dùng model ONNX segmentation và speaker embedding để gắn speaker ID.
- Nhánh **MIC** trong v10.10 **không chạy speaker diarization** theo chính sách đã đơn giản hóa từ v10.3.
- Nếu thiếu model speaker, chức năng này có thể bị bỏ qua trong khi ASR chính vẫn tiếp tục hoạt động.
- Nhãn speaker là ước lượng, đặc biệt dễ sai khi chỉ có một từ ngắn, người nói chồng nhau hoặc tín hiệu lẫn tạp âm.

### 2.4 Điều khiển bằng nút Start/Stop

Mở ứng dụng **không tự bắt đầu ghi âm**. Splash có thể nạp trước các model đã chọn; khi vào giao diện chính, bạn vẫn phải bấm **Bắt đầu ghi âm** để mở thiết bị thu và tạo phiên. Nhấn **Dừng & lưu** để kết thúc và xuất các file nội dung.

## 3. Yêu cầu hệ thống

### 3.1 Nền tảng được nhắm tới

- **Windows 10/11 64-bit**.
- CPU x64 (ưu tiên các model và runtime **CPU-only**).
- Microphone nếu muốn thu MIC.
- Thiết bị phát âm thanh Windows hỗ trợ loopback nếu muốn thu REMOTE.
- Ổ đĩa còn đủ chỗ cho model, môi trường Python và/hoặc `_internal/` của EXE.
- Internet khi **cài dependency** và **tải model** lần đầu.

### 3.2 Python

**Để build EXE, dùng Python 3.11 64-bit** là lựa chọn được khuyến nghị. Script hiện tại chấp nhận **3.10, 3.11 hoặc 3.12**, nhưng không chấp nhận Python 3.14.

> Chạy bằng Python source và đóng gói EXE là hai môi trường khác nhau. Chẳng hạn source có thể đang chạy trong Conda `soundtext` với Python 3.14, trong khi script build cần một môi trường `soundtext-build` Python 3.11. Đừng thay đổi môi trường đang chạy ổn chỉ để build.

### 3.3 Máy chỉ có 8 GB RAM

SoundText v10.10 có chính sách **tiết kiệm bộ nhớ cho EXE Windows khi RAM vật lý không vượt quá 10 GiB**. Chế độ này giảm số model được nạp trước, số luồng tính toán và trì hoãn NLLB. **Đây là biện pháp giảm rủi ro, không bảo đảm EXE không thể thiếu RAM hoặc bị crash.** Xem [mục 15](#15-chế-độ-an-toàn-cho-máy-8-gb-ram).

Không nên mở đồng thời nhiều ứng dụng nặng, trình duyệt nhiều tab hoặc các phép kiểm thử model NLLB khi RAM sắp đầy.

### 3.4 Thư viện chính

Các gói được khai báo trong `requirements.txt` gồm:

| Nhóm | Thư viện tiêu biểu | Vai trò |
|---|---|---|
| Giao diện | `customtkinter`, `Pillow` | Cửa sổ chính, phụ đề, icon, splash. |
| Âm thanh | `PyAudioWPatch`, `soxr`, `numpy` | Thu MIC/loopback và đổi sample rate. |
| ASR tiếng Việt | `sherpa-onnx`, `sherpa-onnx-core` | Chạy Gipformer. |
| ASR tiếng Nhật/Anh | `funasr`, `torch`, `torchaudio` | Chạy SenseVoice. |
| VAD / speaker | `onnxruntime` | Chạy các model ONNX. |
| Dịch | `transformers`, `optimum-onnx[onnxruntime]`, `sentencepiece`, `accelerate` | Tokenizer và NLLB ONNX. |
| Tải model | `huggingface_hub`, `requests` | Chuẩn bị tài nguyên trong `models/`. |
| Đóng gói | `pyinstaller`, `pyinstaller-hooks-contrib` | Chỉ cần trong môi trường build, theo `requirements-exe.txt`. |

Không nên tự nâng phiên bản hàng loạt các thư viện Torch/Transformers/Optimum trên bản đang hoạt động tốt: thay đổi phiên bản có thể gây lỗi import hoặc DLL.

## 4. Cấu trúc mã nguồn và bản portable

### 4.1 Thư mục source

```text
SoundText/
├── SoundText.bat                   # Launcher/menu duy nhất ở thư mục gốc
├── main.py                         # CLI + điểm gọi UI/pipeline khi chạy source
├── entry_soundtext.py              # Điểm khởi chạy riêng cho EXE PyInstaller
├── SoundText.spec                  # Quy tắc đóng gói onedir
├── requirements.txt               # Thư viện chạy app
├── requirements-exe.txt           # Thư viện build
├── branding.default.json          # Mẫu metadata, không ghi đè lựa chọn cá nhân
├── config_stt_vi_ja.default.json   # Mẫu cấu hình nhận diện/dịch
├── branding.json                   # Tự sinh lần đầu, chỉnh trực tiếp tại đây
├── config_stt_vi_ja.json          # Tự sinh lần đầu, chỉnh trực tiếp tại đây
├── README.md                       # Tài liệu này
├── README_EXE.md                   # Tài liệu build bổ sung của source gốc
├── README_8GB_SAFE.md              # Ghi chú bộ nhớ cho v10.10
├── assets/
│   ├── default-branding/          # Icon/splash mặc định
│   └── branding/                  # Ảnh do người dùng tự đặt (nếu có)
├── models/                         # Tất cả trọng số và cache model
│   ├── gipformer1.5-65m-rnnt/
│   ├── sensevoice-small/
│   ├── silero-vad/
│   ├── nllb-trilingual-en-vi-ja-onnx/
│   ├── speaker-diarization/
│   └── speaker-embedding/
├── app/
│   ├── asr/                       # Gipformer, SenseVoice, worker ASR
│   ├── audio/                     # Thiết bị thu, resample, xử lý tín hiệu
│   ├── vad/                       # Silero VAD và phân đoạn lời nói
│   ├── speakers/                  # Diarization REMOTE
│   ├── translation/               # NLLB ONNX và worker dịch
│   ├── transcript/                # Gắn/cập nhật lời thoại
│   ├── postprocess/               # Chuẩn hóa/ghép văn bản
│   ├── storage/                   # Lưu và xuất phiên họp
│   ├── reliability/               # Journal/checkpoint
│   ├── ui/                        # CustomTkinter, phụ đề, splash
│   ├── pipeline_meeting.py        # Điều phối luồng thu → VAD → ASR → MT
│   ├── runtime_paths.py           # Đường dẫn chạy source/EXE
│   └── runtime_memory.py          # Chính sách RAM cho EXE
├── scripts/                        # Tải model, diagnose, build, công cụ
├── tests/                          # Kiểm thử tự động
├── output/                         # Tạo khi chạy: phiên họp, TXT, log
├── build/                          # Tạo khi build: file tạm, icon/metadata EXE
└── dist/                           # Tạo khi build: ứng dụng portable
```

**Lưu ý:** gói source không bao gồm model lớn. Trong bản cập nhật, `branding.json` và `config_stt_vi_ja.json` có thể **chưa tồn tại** cho đến lần chạy đầu; chương trình sẽ tạo từ file `.default.json` nếu thiếu.

### 4.2 Thư mục EXE sau khi build

```text
dist/
└── SoundText/
    ├── SoundText.exe
    ├── _internal/                   # Python runtime, DLL, package files
    ├── branding.json
    ├── config_stt_vi_ja.json
    ├── branding.default.json
    ├── config_stt_vi_ja.default.json
    ├── assets/
    │   ├── default-branding/
    │   └── branding/               # Nếu source có ảnh cá nhân
    ├── models/                     # PHẢI chép model từ source vào
    ├── output/                     # Phiên họp/log khi chạy EXE
    └── README_EXE.md
```

**Không được chỉ mang `SoundText.exe` sang máy khác.** Ứng dụng dùng định dạng **one-folder**, cần toàn bộ thư mục `dist/SoundText/` cùng `_internal/`, tài nguyên, config và model. Đặt model ở **ngang cấp EXE** trong `models/`, không đặt dưới `_internal/`.

## 5. Cài đặt môi trường Python / Miniconda

Các lệnh dưới đây dành cho **Miniconda Prompt/CMD trên Windows**, không phải PowerShell hoặc bash. Ví dụ thư mục dự án:

```text
C:\Users\Admin\AI\soundtext1
```

Nếu thư mục thực tế khác, hãy thay đúng đường dẫn của bạn.

### 5.1 Kiểm tra môi trường hiện tại

```cmd
python --version
where python
conda env list
```

Nếu Python đang là **3.14.x**, hãy tạo môi trường mới cho build thay vì cố xóa điều kiện kiểm tra phiên bản trong script.

### 5.2 Tạo môi trường Python 3.11 riêng

```cmd
conda create -n soundtext-build python=3.11 -y
conda activate soundtext-build
python --version
```

Kết quả mong muốn là `Python 3.11.x`. Sau đó vào dự án:

```cmd
cd /d C:\Users\Admin\AI\soundtext1
```

### 5.3 Cài thư viện ứng dụng

```cmd
python -m pip install -r requirements.txt
```

Riêng để build EXE:

```cmd
python -m pip install -r requirements-exe.txt
```

`pip` phải là pip của **môi trường Conda đang kích hoạt**. Dùng `python -m pip` giúp tránh cài nhầm thư viện vào môi trường Python khác.

### 5.4 Kiểm tra thư viện và model nhẹ

```cmd
python scripts\diagnose.py
```

Lệnh này kiểm tra tình trạng file và package; **không** chủ động tải model hay mở microphone. Kiểm tra import/DLL sâu hơn (nặng hơn):

```cmd
python scripts\diagnose.py --imports
```

Nếu máy 8 GB đang thiếu RAM, **đừng chạy nhiều phép kiểm tra nặng đồng thời**.

## 6. Chuẩn bị model AI

### 6.1 Danh sách model hiện hành

| Thư mục / file | Vai trò | Dùng khi nào |
|---|---|---|
| `models/gipformer1.5-65m-rnnt/` | ASR tiếng Việt | Thường dùng cho MIC tiếng Việt. |
| `models/sensevoice-small/` | ASR tiếng Nhật/Anh/AUTO | Thường dùng cho REMOTE. |
| `models/silero-vad/silero_vad.onnx` | Tách các vùng có tiếng nói | Cả hai nguồn. |
| `models/nllb-trilingual-en-vi-ja-onnx/` | Dịch sang tiếng Việt | Khi phiên bật **Ghi & dịch**. |
| `models/speaker-diarization/segmentation.onnx` | Phân đoạn speaker | Tùy chọn, nhánh REMOTE. |
| `models/speaker-embedding/redimnet_b2_vox2.onnx` | So khớp giọng người nói | Tùy chọn, nhánh REMOTE. |

Các model **không tải tự động trong EXE khi đang chạy**. Cần chuẩn bị trước trên máy có Internet, sau đó sao chép đúng thư mục khi phát hành portable.

### 6.2 Tải các model nhận diện và VAD

Từ source:

```cmd
python scripts\prepare_models.py
```

Hoặc mở `SoundText.bat` và chọn **8**.

Script đọc các nguồn được khai báo trong code:

- Gipformer: `g-group-ai-lab/gipformer1.5-65M-rnnt`.
- SenseVoice: `FunAudioLLM/SenseVoiceSmall`.
- Silero VAD: file ONNX từ dự án Silero VAD.

**File quan trọng:**

```text
models/gipformer1.5-65m-rnnt/
  encoder.int8.onnx
  decoder.int8.onnx
  joiner.int8.onnx
  tokens.txt
  bpe.model
  config.json

models/sensevoice-small/
  config.yaml
  model.pt
  chn_jpn_yue_eng_ko_spectok.bpe.model
  am.mvn

models/silero-vad/
  silero_vad.onnx
```

### 6.3 Tải NLLB ONNX

```cmd
python scripts\prepare_translation_onnx.py
```

Hoặc chọn **10** trong `SoundText.bat`.

Script tải bản NLLB theo repo được khai báo: `belumind/nllb-trilingual-en-vi-ja-onnx`. Các file quan trọng gồm:

```text
models/nllb-trilingual-en-vi-ja-onnx/
  config.json
  tokenizer.json
  tokenizer_config.json
  sentencepiece.bpe.model
  encoder_model_quantized.onnx
  decoder_model_quantized.onnx
  decoder_with_past_model_quantized.onnx
  ... (các file cấu hình hỗ trợ khác)
```

Script có hỗ trợ tải tiếp `.part` nếu kết nối đứt. Tránh xóa model đang hoàn chỉnh khi chỉ gặp lỗi thư viện runtime.

Kiểm tra một câu tiếng Nhật từ **Python source**:

```cmd
python scripts\test_translation.py --backend nllb_onnx --language ja --text "こんにちは。"
```

Hoặc thử tiếng Anh:

```cmd
python scripts\test_translation.py --backend nllb_onnx --language en --text "Hello everyone."
```

**Trên máy 8 GB:** test này **có thể dùng nhiều RAM** vì tải model thật. Chỉ chạy khi còn đủ bộ nhớ, không mở cùng lúc một phiên SoundText khác.

### 6.4 Tải model phân biệt người nói

```cmd
python scripts\prepare_speakers.py
```

Hoặc menu **9**. Script tải hai model ONNX vào `speaker-diarization` và `speaker-embedding`. Không tải lại nếu file đủ kích thước cần thiết. Sau khi tải, kiểm tra `speakers.enabled` trong cấu hình nếu muốn dùng diarization REMOTE.

### 6.5 Cache model và model cũ

Các cache Hugging Face và Torch được hướng vào **`./models/`** thông qua `SoundText.bat` và `app/runtime_paths.py`, thay vì mặc định rải ở nhiều vị trí hệ thống.

Các thư mục như `lfm2.5-1.2b-instruct`, `sensevoice-onnx` hoặc `translation` từ các bản thử nghiệm cũ **không nằm trong danh sách 6 nhóm model chính của v10.10**. Chỉ dọn khi đã xác nhận cấu hình hiện tại không trỏ tới chúng và có bản sao lưu. Không tự xóa bất cứ thư mục nào chỉ vì tên trông lạ.

## 7. Chạy SoundText từ source

### 7.1 Cách đơn giản nhất: menu `SoundText.bat`

Trong Miniconda Prompt, kích hoạt môi trường đã cài dependency:

```cmd
conda activate soundtext-build
cd /d C:\Users\Admin\AI\soundtext1
SoundText.bat
```

Hoặc double-click `SoundText.bat` nếu `python` trên PATH đã thuộc môi trường đúng. Đối với người dùng Miniconda, **mở từ Miniconda Prompt** là cách dễ kiểm soát Python đang được sử dụng nhất.

#### Các mục menu thực tế của bản v10.10

| Mục | Chức năng |
|---|---|
| `1` | Mở SoundText, mặc định cấu hình MIC + REMOTE; **không tự ghi âm**. |
| `2` | Mở giao diện với MIC được chọn trước. |
| `3` | Mở giao diện với REMOTE được chọn trước. |
| `4` | Kiểm tra model và thư viện nhanh. |
| `5` | Liệt kê thiết bị âm thanh. |
| `6` | Ghi thử microphone 12 giây, xuất WAV kiểm tra. |
| `7` | Khôi phục một thư mục phiên có sẵn. |
| `8` | Chuẩn bị model ASR và Silero VAD. |
| `9` | Chuẩn bị model speaker ONNX. |
| `10` | Chuẩn bị NLLB ONNX. |
| `11` | Cài dependency từ `requirements.txt`. |
| `12` | Chạy với lựa chọn kênh âm thanh thủ công. |
| `13` | Dọn cache Python tạm, **không xóa** `models/` hay phiên họp. |
| `14` | Kiểm tra import thư viện/DLL sâu hơn. |
| `15` | Thử tải các model ASR (tốn tài nguyên). |
| `16` | Test một câu tiếng Nhật bằng NLLB. |
| `17` | Chạy không phân biệt speaker. |
| `18` | Không tách speaker sớm; vẫn có thể diarize câu đã hoàn tất. |
| `19` | Chỉ cài CustomTkinter và Pillow. |
| `0` | Thoát menu. |

**Ghi chú:** menu 9 có thể dùng nhãn cũ đề cập MIC + REMOTE; **logic v10.10 chỉ diarize REMOTE**. Tên menu không phải mô tả chính xác chính sách diarization mới.

### 7.2 Chạy trực tiếp bằng Python

```cmd
python main.py run --config config_stt_vi_ja.json
```

Chọn trước chỉ REMOTE:

```cmd
python main.py run --config config_stt_vi_ja.json --audio-mode remote
```

Chỉ MIC:

```cmd
python main.py run --config config_stt_vi_ja.json --audio-mode you
```

Tắt speaker để ưu tiên hiệu năng:

```cmd
python main.py run --config config_stt_vi_ja.json --audio-mode remote --no-speakers
```

Chạy không có giao diện:

```cmd
python main.py run --config config_stt_vi_ja.json --no-ui
```

**Chú ý:** chế độ `--no-ui` chạy pipeline trực tiếp, không theo cơ chế chờ bạn nhấn Start của GUI. Hãy dùng khi bạn hiểu rõ và muốn ghi âm ngay từ dòng lệnh.

### 7.3 Xem danh sách thiết bị và thử MIC

```cmd
python main.py devices
python main.py probe --config config_stt_vi_ja.json --probe-source mic --seconds 12
```

Probe xuất ra `output/probe_mic.wav`. Dùng kết quả này để xác nhận microphone nào đang được chọn và tín hiệu có bị quá nhỏ/nhiễu hay không.

### 7.4 Chọn thiết bị thủ công bằng index

Đầu tiên chạy `python main.py devices`, tìm đúng `IDX`. Sau đó:

```cmd
python main.py run --config config_stt_vi_ja.json --audio-mode both --mic-device-index 1 --system-device-index 6
```

**`1` và `6` chỉ là ví dụ.** Chỉ số thiết bị khác nhau tùy máy và có thể thay đổi sau khi cắm/rút tai nghe, thay driver hoặc khởi động lại Windows.

### 7.5 Đổi cấu hình kênh stereo khi cần

Các chế độ được CLI hỗ trợ:

- MIC: `auto`, `mic_speech`, `mean`, `loudest`, `left`, `right`.
- REMOTE: `auto`, `stereo_speech`, `mean`, `loudest`, `left`, `right`.

Ví dụ:

```cmd
python main.py run --config config_stt_vi_ja.json --audio-mode remote --system-channel-mode stereo_speech
```

Thông thường nên để mặc định `mic_speech` và `stereo_speech` trước, chỉ chỉnh khi một kênh bị lệch hoặc mất tiếng.

## 8. Hướng dẫn sử dụng giao diện

### 8.1 Khởi động

1. Mở SoundText từ menu **1**, **2** hoặc **3**.
2. Splash hiển thị ảnh trong `branding.json` và trạng thái tải các model thích hợp.
3. Sau khi chuẩn bị xong, cửa sổ chính xuất hiện ở trạng thái **Sẵn sàng / chưa ghi âm**.
4. Kiểm tra hai ô ngôn ngữ và nguồn thu trước khi bấm Start.

Ở **bản EXE 8 GB**, splash không ép nạp cả hai ASR và NLLB cùng lúc; một số model có thể chỉ nạp khi cần. Vì thế màn hình chính sẵn sàng không nhất thiết có nghĩa **mọi** trọng số dịch đã nằm trong RAM.

### 8.2 Chọn nguồn thu

- **MIC:** chỉ lấy âm từ microphone.
- **REMOTE:** chỉ lấy âm Windows loopback; không cần nói vào microphone.
- **MIC + REMOTE:** ghi cả hai.

Nếu bạn đang xem phim hoặc cần phụ đề lời người khác nói trên máy, thường chọn **REMOTE** sẽ phù hợp nhất.

### 8.3 Chọn ngôn ngữ

- **MIC** mặc định `vi` (Gipformer).
- **REMOTE** mặc định `ja` (SenseVoice).
- Có thể chọn `ja`, `en`, `auto` cho đường nhận diện SenseVoice theo lựa chọn hỗ trợ của giao diện.

Chế độ AUTO ở đây là tự nhận diện **Nhật/Anh** theo cơ chế SenseVoice đã cấu hình; đừng xem nó là bộ nhận dạng mọi ngôn ngữ.

### 8.4 Chọn nội dung

- **Ghi & dịch** để có bản tiếng Việt.
- **Chỉ ghi lời thoại** để ghi transcript mà không tiêu tốn CPU/RAM cho NLLB trong phiên đó.

### 8.5 Bắt đầu và dừng

1. Nhấn **Bắt đầu ghi âm**.
2. Chờ trạng thái chuyển sang đang ghi.
3. Phát âm thanh hoặc bắt đầu nói. Lời thoại được cập nhật trên giao diện.
4. Khi kết thúc, nhấn **Dừng & lưu**.
5. Nhấn **Mở thư mục lưu** để xem TXT. Có thể tạo phiên mới mà không cần thoát ứng dụng.

Không đóng máy hoặc kết thúc tiến trình bằng Task Manager trong khi SoundText đang lưu, trừ khi chương trình thực sự bị treo. Nếu bị dừng đột ngột, xem mục [khôi phục phiên](#104-phục-hồi-phiên-bị-gián-đoạn).

### 8.6 Xem lịch sử và sao chép

- Giao diện chính hiển thị MIC và REMOTE thành các khu vực riêng.
- Lịch sử hội thoại có nội dung gốc, bản dịch nếu có, mốc thời gian/nguồn phù hợp.
- Nút **Sao chép hội thoại** đưa nội dung đang hiển thị vào clipboard.
- Các câu REMOTE liên tiếp cùng speaker trong phụ đề nổi được trình bày liền mạch hơn để dễ đọc.

## 9. Cửa sổ phụ đề nổi

### 9.1 Hai cửa sổ độc lập

SoundText sử dụng **hai cửa sổ nổi riêng biệt**:

1. **Lời thoại gốc (STT):** có thể cập nhật cả câu tạm thời khi ASR đang nhận diện.
2. **Bản dịch tiếng Việt:** với Nhật/Anh, chỉ hiển thị bản dịch khi có câu ASR **Final**; không tự coi lời Nhật/Anh tạm thời là bản dịch.

Bạn có thể kéo, đổi kích thước và sắp xếp từng cửa sổ riêng. Khi không cần bản dịch, chọn **Chỉ lời thoại** thay vì buộc phải giữ cửa sổ thứ hai.

### 9.2 Lựa chọn hiển thị

| Chế độ phụ đề | Kết quả |
|---|---|
| **Cả hai** | Hiện cửa STT và cửa bản dịch. |
| **Chỉ lời thoại** | Chỉ hiện STT gốc. |
| **Chỉ bản dịch** | Chỉ hiện bản dịch tiếng Việt, khi chế độ phiên có dịch. |

Khi phiên ở chế độ **Chỉ ghi lời thoại**, phụ đề chỉ cho chọn cửa STT.

### 9.3 Các thiết lập hiển thị

- Cỡ chữ STT và bản dịch **riêng**, trong khoảng **5–50**.
- Tự cuộn theo nội dung mới.
- Cuộn đồng bộ hai cửa nếu đang bật **Cả hai**.
- Tự điều chỉnh chiều cao để hiển thị văn bản dài.
- Bố trí **cạnh nhau**, **chồng dọc** hoặc **bên phải**.

Nếu tự cuộn không theo câu mới, thử cuộn cửa sổ xuống cuối hoặc kiểm tra lựa chọn theo dõi nội dung. Nếu bật cuộn đồng bộ, hai cửa đang hiển thị phải cùng ở chế độ **Cả hai**.

### 9.4 Phân biệt “ẩn bản dịch” và “không dịch”

- **Chỉ bản dịch / Chỉ lời thoại** là cài đặt **hiển thị phụ đề**.
- **Ghi & dịch / Chỉ ghi lời thoại** là cài đặt **xử lý nội dung của phiên**.

Muốn tiết kiệm RAM và bỏ NLLB, phải chọn **Chỉ ghi lời thoại trước khi Start**, không phải chỉ ẩn cửa bản dịch.

## 10. File đầu ra, lưu phiên và phục hồi

### 10.1 Thư mục phiên

Mỗi phiên được lưu trong `output/meeting_YYYYMMDD_HHMMSS/`, ví dụ:

```text
output/
├── logs/
│   └── startup.log                      # Chủ yếu cho EXE: log khởi động
└── meeting_20261028_154733/
    ├── Noi_dung/
    │   └── Noi_dung_cuoc_hop.txt       # FILE NÊN GỬI/ĐỌC HẰNG NGÀY
    ├── transcript.txt                  # Transcript đã chuẩn hóa/gộp câu
    ├── transcript.jsonl                # Các đoạn nhận diện theo sự kiện
    ├── transcript.srt                  # Phụ đề SRT lời gốc
    ├── raw_transcript.txt              # Bản nhận diện thô, khi bật preserve_raw
    ├── raw_transcript.jsonl
    ├── raw_transcript.srt
    ├── translations.jsonl              # Kết quả dịch khi có
    ├── bilingual_transcript.txt        # Lời gốc và dịch xen kẽ
    ├── you_transcript.txt              # Nhánh MIC
    ├── remote_transcript.txt           # Nhánh REMOTE
    ├── health.jsonl                    # Sự kiện sức khỏe pipeline nếu phát sinh
    └── ...                             # Journal/checkpoint và file phục hồi
```

Số lượng file cụ thể phụ thuộc nội dung phiên và cấu hình. File log khởi động nằm trong `output/logs/` **của đúng bản bạn đang chạy**: source và EXE có thể dùng **hai thư mục output khác nhau**.

### 10.2 File TXT dành cho người dùng

**Đường dẫn chính:**

```text
output/meeting_YYYYMMDD_HHMMSS/Noi_dung/Noi_dung_cuoc_hop.txt
```

Nội dung được viết UTF-8, có mốc thời gian, nguồn, speaker nếu có, lời gốc và bản dịch khi có. Ví dụ minh họa:

```text
NỘI DUNG CUỘC HỌP
======================================

[00:00:12 – 00:00:16] Âm thanh máy · S1 · Tiếng Anh
Lời thoại: Good morning everyone.
Tiếng Việt: Chào buổi sáng mọi người.

[00:00:18 – 00:00:21] Microphone · Tiếng Việt
Chào mọi người, chúng ta bắt đầu nhé.
```

Trong chế độ **Chỉ ghi lời thoại**, không có các dòng dịch được tạo mới. Nút **Mở thư mục lưu** ưu tiên mở `Noi_dung/` của phiên gần nhất, nơi chỉ có một TXT dành cho người dùng.

### 10.3 File kỹ thuật có tác dụng gì?

- **`.jsonl`:** mỗi dòng là một bản ghi JSON, thuận tiện debug, phân tích hoặc xử lý bằng script.
- **`.srt`:** phụ đề có thời điểm bắt đầu/kết thúc, có thể dùng với phần mềm video tương thích.
- **`raw_*`:** giữ câu gốc trước khi chuẩn hóa, có ích khi ASR sai hoặc cần đối chiếu.
- **`bilingual_transcript.txt`:** bản gốc và tiếng Việt trong một file kỹ thuật.
- **Journal/checkpoint:** phục vụ khôi phục khi phiên bị gián đoạn.

Không nên xóa file JSONL/journal **đang ghi**. Chúng không dành cho người dùng phổ thông nhưng rất hữu ích khi báo lỗi.

### 10.4 Phục hồi phiên bị gián đoạn

Từ source:

```cmd
python main.py recover --config config_stt_vi_ja.json --session "output\meeting_20261028_154733"
```

Hoặc chọn menu **7**, nhập đường dẫn thư mục phiên. Thao tác sẽ đọc dữ liệu đã có và dựng lại các bản transcript có thể phục hồi.

Việc phục hồi **không thể tái tạo âm thanh hoặc đoạn lời chưa từng được ASR ghi thành Final**; nó chỉ làm việc từ dữ liệu đã được lưu.

## 11. Cấu hình ứng dụng

### 11.1 Phân biệt file mẫu và file đang sử dụng

| File | Công dụng |
|---|---|
| `config_stt_vi_ja.default.json` | Mẫu mặc định, có trong bản phát hành. |
| `config_stt_vi_ja.json` | **File đang có hiệu lực** khi chạy ứng dụng. |
| `branding.default.json` | Mẫu thương hiệu. |
| `branding.json` | **Tên/icon/splash đang có hiệu lực**. |

Nếu chưa có file đang dùng, SoundText tự tạo bằng mẫu ở lần chạy đầu. Khi cập nhật mã, **đừng ghi đè hai file cá nhân** này nếu không muốn mất lựa chọn trước đây.

### 11.2 Các nhóm thiết lập quan trọng

`config_stt_vi_ja.json` gồm các nhóm:

| Nhóm | Các khóa đáng chú ý | Ý nghĩa |
|---|---|---|
| `audio` | `mic_device_index`, `system_device_index`, `target_sample_rate`, `mic_channel_mode`, `system_channel_mode` | Thiết bị, kênh và xử lý tín hiệu thu. |
| `vad` | `model_path`, `threshold`, `mic_threshold`, `min_silence_ms` | Phát hiện vùng tiếng nói. |
| `asr` | `gipformer_model_path`, `sensevoice_model_path`, `cpu_threads`, `gipformer_precision` | Nhận diện và hiệu năng. |
| `meeting` | `mic_language`, `system_language`, `audio_mode`, `ui_enabled` | Chọn nguồn/ngôn ngữ/giao diện. |
| `translation` | `enabled`, `backend`, `nllb_model_path`, `cpu_threads` | Bật/tắt dịch và model NLLB. |
| `speakers` | `enabled`, `segmentation_path`, `embedding_path`, `similarity_threshold` | Speaker diarization REMOTE. |
| `postprocess` | `enabled`, `preserve_raw`, `merge_gap_s` | Chuẩn hóa và ghép câu. |
| `reliability` | `preset`, `checkpoint_interval_s`, `atomic_artifacts` | Lưu an toàn/checkpoint. |
| `storage` | `output_dir` | Thư mục đầu ra. |

### 11.3 Ví dụ tùy chỉnh tối thiểu

Ví dụ **không thay thế toàn bộ file**: chỉ thay các giá trị tương ứng trong `config_stt_vi_ja.json` hiện có:

```json
{
  "meeting": {
    "mic_language": "vi",
    "system_language": "ja",
    "audio_mode": "remote"
  },
  "translation": {
    "enabled": true,
    "backend": "nllb_onnx",
    "nllb_model_path": "models/nllb-trilingual-en-vi-ja-onnx"
  },
  "speakers": {
    "enabled": true
  }
}
```

**Đây là ví dụ minh họa một phần cấu hình, không phải nội dung JSON đầy đủ để chép đè.** Hãy giữ nguyên các nhóm còn lại từ file mặc định, vì hệ thống còn dùng rất nhiều khóa khác.

### 11.4 Không chỉnh ngưỡng tùy tiện

- Giảm ngưỡng VAD quá sâu có thể tăng nhận nhầm tạp âm.
- Tăng ngưỡng quá cao có thể mất giọng nhỏ.
- Thay `cpu_threads` có thể ảnh hưởng RAM, tốc độ và độ ổn định.
- Cấu hình `mic_guard` vẫn còn trong JSON để tương thích, nhưng đường xử lý MIC hiện không dùng bộ so khớp/chống vọng với REMOTE như các bản cũ.
- Không nên chỉnh trực tiếp đường dẫn model sang thư mục lạ khi chuẩn bị EXE portable.

## 12. Branding, icon và splash screen

SoundText cho phép đổi thương hiệu **qua file JSON**, không cần mở hộp thoại cấu hình trên UI.

### 12.1 File `branding.json`

Ví dụ:

```json
{
  "app_name": "SoundText",
  "app_version": "1.0.0",
  "publisher": "SoundText",
  "tagline": "Phụ đề và biên bản hội thoại trực tiếp",
  "icon_path": "assets/branding/my-icon.png",
  "splash_path": "assets/branding/my-splash.png",
  "show_splash": true,
  "splash_duration_ms": 300
}
```

Tạo thư mục `assets/branding/` nếu chưa có và chép ảnh vào đó. Nên sử dụng **đường dẫn tương đối** trong JSON để bản portable chạy được trên máy khác.

Đường dẫn Windows tuyệt đối như `C:\Users\...` không phù hợp khi phân phối. Nếu buộc phải dùng, trong JSON hãy dùng dấu `/` (ví dụ `C:/Pictures/logo.png`) hoặc viết mỗi dấu gạch chéo ngược thành **hai dấu** (`C:\\Pictures\\logo.png`).

### 12.2 Icon hiển thị ở đâu?

| Thành phần | Khi nào cập nhật? |
|---|---|
| Icon cửa sổ chính và hai cửa phụ | Đọc `branding.json` khi mở ứng dụng; khởi động lại để thấy thay đổi. |
| Splash khi mở ứng dụng | Đọc `splash_path` khi khởi động. |
| Icon chính của file `SoundText.exe` trong Explorer | Nhúng lúc **build**; cần build lại. |
| Tên/phần mềm/nhà phát hành trong Windows File Details | Nhúng từ `app_name`, `app_version`, `publisher` lúc **build**; cần build lại. |
| Icon file `.bat` | Không tự đổi theo branding. |

### 12.3 Splash screen hoạt động thế nào?

- `show_splash: true` cho phép hiển thị **ảnh** splash tùy chỉnh; `false` bỏ qua ảnh. **Trong v10.10, cửa sổ tiến trình tải model vẫn xuất hiện**, chỉ thay ảnh bằng tiêu đề ứng dụng. Vì vậy `false` không đồng nghĩa bỏ toàn bộ màn hình chờ.
- Ảnh được đọc theo `splash_path`; nếu sai/mất file, giao diện tải sẽ hiện cảnh báo thay vì im lặng thay bằng ảnh khác.
- Splash chờ giai đoạn chuẩn bị model. **`splash_duration_ms` là khóa legacy**, không phải bộ đếm buộc splash đóng sau đúng số mili giây.
- Trên EXE 8 GB, splash **không** tải toàn bộ NLLB để giảm RAM; lần dịch đầu có thể chậm hơn.

### 12.4 Các định dạng ảnh

- Icon: nên dùng PNG vuông, nền trong suốt (ví dụ 256×256 hoặc 512×512); ICO cũng được thư viện hỗ trợ ở một số vị trí.
- Splash: PNG, JPG/JPEG hoặc GIF trong `assets/branding/`; ảnh lớn sẽ được thu nhỏ để phù hợp cửa sổ tải.

**Không** thêm tùy chọn `--splash` riêng của PyInstaller vào build vì sẽ tạo **splash thứ hai**, không liên quan `branding.json`.

## 13. Kiến trúc kỹ thuật và các module

### 13.1 Luồng xử lý tổng quan

```text
                   ┌──────────────────┐
    Microphone ───►│   PyAudioWPatch  │
                   │ + soxr resampler │
    System Audio ─►│   (WASAPI loop)  │
                   └────────┬─────────┘
                            │
                      Silero VAD
                            │
             ┌──────────────┴──────────────┐
             │                             │
       MIC tiếng Việt                 REMOTE Nhật/Anh
         Gipformer                     SenseVoice
             │                             │
             │                   Diarization ONNX
             │                      (REMOTE only)
             └──────────────┬──────────────┘
                            │
                      ASR Final
                            │
                ┌───────────┴───────────┐
                │                       │
           Transcript               Dịch bật?
                │                       │
                │               NLLB INT8 ONNX
                │                       │
                └───────────┬───────────┘
                            │
               UI + phụ đề + SessionStore
                            │
                 TXT / SRT / JSONL / journal
```

Đây là sơ đồ logic rút gọn; trong code, pipeline có các worker, hàng đợi và sự kiện riêng để tránh xử lý nặng ngay trên luồng giao diện.

### 13.2 Các module quan trọng

| File/module | Vai trò chính |
|---|---|
| `main.py` | Parse CLI, đọc config, quyết định UI/không UI. |
| `entry_soundtext.py` | Điểm khởi động frozen EXE, log startup, chế độ kiểm tra import, giới hạn RAM. |
| `app/pipeline_meeting.py` | Vòng đời phiên, thu âm, hàng đợi, ASR/MT, tích hợp lưu trữ. |
| `app/ui/ctk_window.py` | Giao diện CustomTkinter, nút Start/Stop, lịch sử, hai cửa nổi, splash. |
| `app/ui/recording_controller.py` | Điều khiển phiên không phụ thuộc UI; chạy pipeline trong thread. |
| `app/ui/model_preloader.py` | Chuẩn bị model phía sau splash; bàn giao phiên model đã tải. |
| `app/ui/branding.py`, `app/ui/window_icon.py` | Đọc branding, hiển thị icon. |
| `app/asr/gipformer_engine.py` | Gipformer ONNX tiếng Việt. |
| `app/asr/sensevoice_engine.py` | SenseVoice qua FunASR. |
| `app/asr/deferred_engine.py` | Nạp trì hoãn ASR (đặc biệt EXE RAM thấp). |
| `app/vad/silero_onnx.py`, `app/vad/segmenter.py` | VAD và phân đoạn tiếng nói. |
| `app/speakers/diarizer.py`, `app/speakers/worker.py` | Gắn nhãn speaker REMOTE. |
| `app/translation/nllb_onnx_engine.py`, `app/translation/worker.py` | Dịch NLLB ONNX và worker. |
| `app/storage/session_store.py` | JSONL append-first, TXT dễ đọc, SRT, phục hồi. |
| `app/reliability/journal.py` | Nhật ký phiên/checkpoint. |
| `app/runtime_paths.py` | Chọn project root/source root/EXE root. |
| `app/runtime_memory.py` | Đọc RAM trống, giới hạn commit cho frozen EXE. |

### 13.3 Nguyên tắc phát triển đã ổn định

Khi chỉnh UI, tài liệu hoặc build, **hạn chế sửa thuật toán** của ASR, VAD, NLLB, diarization và lưu phiên nếu không thật sự cần. Thay đổi một thành phần nặng nên được kiểm thử riêng trước khi tích hợp, vì máy đích chỉ có 8 GB RAM.

## 14. Đóng gói Windows EXE bằng PyInstaller

### 14.1 Vì sao chọn one-folder?

SoundText sử dụng Torch, FunASR, ONNX Runtime và nhiều DLL lớn. Dự án chọn định dạng **onedir** (một thư mục chứa EXE và runtime) vì:

- Không phải giải nén toàn bộ thư viện lớn mỗi lần mở như dạng `onefile`.
- Dễ kiểm tra xem thiếu DLL, mã nguồn `.py` hay file `version.txt` ở đâu.
- Model nằm ngoài EXE, dễ thay thế và cập nhật.
- `branding.json` và `config_stt_vi_ja.json` có thể chỉnh mà không phải build lại toàn bộ.

`SoundText.spec` là cấu hình build chuẩn. **Không cần tự thêm lệnh `--onefile`, `--splash` hoặc `--add-data` tùy tiện** khi chưa kiểm tra ảnh hưởng.

### 14.2 Chuẩn bị trước khi build

1. Tạo/cài môi trường Python 3.11 64-bit (xem [mục 5](#5-cài-đặt-môi-trường-python--miniconda)).
2. Xác nhận `python -m pip install -r requirements.txt` đã thành công.
3. Xác nhận `branding.json` hợp lệ; `icon_path` phải trỏ đến ảnh có thật.
4. Kiểm tra `main.py` không có lỗi cú pháp.
5. Sao lưu thư mục `dist/SoundText/` cũ nếu đã chép model hoặc có log/phiên cần giữ.

Lệnh kiểm tra nhanh:

```cmd
conda activate soundtext-build
cd /d C:\Users\Admin\AI\soundtext1
python -m py_compile main.py entry_soundtext.py
python -m compileall -q main.py entry_soundtext.py app scripts
python scripts\diagnose.py
```

### 14.3 Lệnh build chuẩn

Chạy từ **thư mục gốc dự án**, có folder `scripts`:

```cmd
conda activate soundtext-build
cd /d C:\Users\Admin\AI\soundtext1
.\scripts\build_windows_exe.bat
```

> **CMD/Miniconda Prompt:** dùng `\` trong đường dẫn. Nếu chạy `scripts/build_windows_exe.bat`, CMD có thể báo `'scripts' is not recognized...`. Lệnh đúng trong hướng dẫn này là `.\scripts\build_windows_exe.bat`.

### 14.4 Script build làm những gì?

Script của v10.10 có năm nhóm bước:

1. **Kiểm tra Windows/Python/kiến trúc:** Python 3.10–3.12 x64 và các thư viện quan trọng.
2. **Chuẩn bị PyInstaller:** cài `requirements-exe.txt`.
3. **Đọc branding:** tạo `build/soundtext-exe.ico` và `build/soundtext-version.txt`, kiểm tra import `main.py`.
4. **Đóng gói và smoke-check nhẹ:** chạy `PyInstaller --clean SoundText.spec`, kiểm tra `_internal/funasr/version.txt` và `--check-bundle`.
5. **Sao chép tài nguyên có thể chỉnh:** cấu hình, branding, ảnh mặc định/ảnh cá nhân nếu có; tạo `models/`, `output/` rỗng trong thư mục EXE.

**Mục tiêu chống quá tải:** v10.10 **không tự chạy các kiểm tra import FunASR/NLLB nặng ngay sau build**. Bạn chỉ chạy thủ công khi cần và khi RAM còn đủ.

### 14.5 Sao chép model vào bản EXE

Sau khi build thành công, chép **toàn bộ nội dung bên trong** `models/` của source sang:

```text
C:\Users\Admin\AI\soundtext1\dist\SoundText\models\
```

Bạn có thể dùng File Explorer hoặc `robocopy`:

```cmd
robocopy "models" "dist\SoundText\models" /E
```

`robocopy` có mã thoát đặc biệt: nhiều mã từ 0–7 vẫn biểu thị thành công hoặc có file được copy; đừng mặc định xem mọi mã khác 0 là lỗi.

Cấu trúc chính xác phải là:

```text
dist\SoundText\models\gipformer1.5-65m-rnnt\encoder.int8.onnx
dist\SoundText\models\sensevoice-small\model.pt
dist\SoundText\models\silero-vad\silero_vad.onnx
dist\SoundText\models\nllb-trilingual-en-vi-ja-onnx\config.json
```

**Sai:** `dist\SoundText\_internal\models\...` hoặc `dist\SoundText\models\models\...`.

### 14.6 Chạy bản EXE

Mở bằng File Explorer:

```text
dist\SoundText\SoundText.exe
```

Hoặc từ CMD:

```cmd
start "" "dist\SoundText\SoundText.exe"
```

EXE có `console=False` nên log lỗi khởi động thường ghi vào:

```text
dist\SoundText\output\logs\startup.log
```

### 14.7 Các lệnh kiểm tra EXE (từ nhẹ đến nặng)

**Nhẹ, không tải model:**

```cmd
start "" /wait "dist\SoundText\SoundText.exe" --check-bundle
```

**Có import thư viện, có thể tốn RAM:**

```cmd
start "" /wait "dist\SoundText\SoundText.exe" --check-funasr-source
start "" /wait "dist\SoundText\SoundText.exe" --check-translation-runtime
```

**Nặng, tải NLLB thật và dịch một câu:**

```cmd
start "" /wait "dist\SoundText\SoundText.exe" --check-translation-model
```

> **Trên 8 GB RAM:** không bắt đầu bằng `--check-translation-model`. Đầu tiên chạy app chế độ **REMOTE + Chỉ ghi lời thoại** trong thời gian ngắn, kiểm tra RAM; rồi mới cân nhắc kiểm tra NLLB. Các flag kiểm tra khi chạy EXE không mở thiết bị ghi âm, nhưng việc import/model load vẫn có thể tiêu tốn nhiều RAM.

### 14.8 Build sạch khi cần

Chỉ làm sau khi đã **sao lưu bản `dist/SoundText/` cũ** (đặc biệt model và output đã chép vào đó):

```cmd
cd /d C:\Users\Admin\AI\soundtext1
rmdir /s /q build
rmdir /s /q dist
.\scripts\build_windows_exe.bat
```

Xóa `build/` và `dist/` chỉ xóa dữ liệu **do build sinh ra**, nhưng nếu bạn đã copy `models/`, log hoặc dữ liệu phiên vào `dist/`, chúng cũng sẽ bị xóa! **Không** chạy lệnh này nếu chưa sao lưu.

### 14.9 Phân phối portable

- Chép **cả thư mục** `dist\SoundText\`, không chỉ file `.exe`.
- Giữ `_internal/` nguyên vẹn.
- Đặt `models/` và `assets/branding/` đúng vị trí; kiểm tra đường dẫn tương đối.
- Đừng đặt bản portable trong `C:\Program Files` nếu ứng dụng không có quyền tạo `output/` và chỉnh JSON. Nên đặt tại thư mục người dùng có quyền ghi.
- Thử mở EXE qua shortcut Windows hoặc từ một thư mục làm việc khác để xác minh đường dẫn tương đối đã được chuẩn hóa.
- Nên thử trên máy Windows sạch trước khi chia sẻ. Dependency bản địa có thể khác máy build.

## 15. Chế độ an toàn cho máy 8 GB RAM

### 15.1 Vì sao EXE có thể khác bản Python thuần?

PyInstaller đóng gói các thư viện và native runtime thành một môi trường riêng. Sự khác biệt giữa **môi trường Python source** (ví dụ Python 3.14) và **môi trường build** (Python 3.11) cũng có thể làm khác phiên bản Torch, ONNX Runtime, Optimum hoặc cơ chế tải model. Bản EXE lỗi không nhất thiết chứng minh model bị hỏng.

Một tiến trình duy nhất vẫn có thể dùng lượng RAM rất lớn. Khi Windows thiếu RAM nghiêm trọng, desktop/taskbar có thể treo hoặc biến mất. **Không nên cố tái hiện sự cố tràn RAM trên máy chính chỉ để lấy log.**

### 15.2 Chính sách v10.10 cho EXE RAM thấp

Khi đang chạy dưới dạng **EXE Windows** và RAM vật lý **≤ 10 GiB**, source áp dụng:

- Cố gắng đặt **giới hạn commit của tiến trình khoảng 5 GiB** bằng Windows Job Object, trước khi import thư viện nặng.
- Hạn chế ASR tối đa **4 thread**, dịch tối đa **1 thread**, một số native thread pool giảm về **2**.
- Splash chỉ tải trước **một** ASR: ưu tiên SenseVoice ở REMOTE/BOTH, Gipformer ở MIC-only. ASR còn lại sẽ tải khi nguồn đó thực sự có lời nói.
- **Không preload NLLB trong splash**; chỉ tải khi gặp câu cần dịch.
- NLLB dùng cấu hình ONNX tiết kiệm bộ nhớ: hạn chế thread, cache và memory arena.
- Ghi các mốc RAM bằng các dòng `[RAM]`, `[RAM SAFE]` hoặc `[RAM WARN]` trong `startup.log`.
- Kiểm tra RAM khả dụng trước một số bước tải nặng; nếu không đủ, có thể bỏ bước dịch và báo lỗi thay vì cố tiếp tục.

**Đánh đổi:** câu MIC đầu tiên trong BOTH và câu dịch đầu tiên có thể chậm hơn. Điều này có chủ đích để giảm RAM đỉnh.

### 15.3 Các giới hạn phải hiểu đúng

- Job Object cap là giới hạn **committed memory**, không phải phép đo chính xác lượng RAM vật lý đang chiếm.
- Giới hạn không bảo đảm ngăn tất cả sự cố: thư viện native có thể throw, crash hoặc bị từ chối cấp phát.
- Nếu hiện `[RAM WARN] Process cap not installed`, đừng xem máy đã được bảo vệ.
- Chính sách này **không kích hoạt ở Python source không đóng băng**.
- Chế độ dịch có thể không chạy nếu bộ nhớ trống còn quá ít; lúc đó hãy dùng **Chỉ ghi lời thoại**.

### 15.4 Quy trình thử an toàn trên máy 8 GB

1. **Sao lưu EXE/source cũ đang chạy tốt.** Không chạy lại bản EXE từng làm Windows treo.
2. Kiểm tra Task Manager → Performance → Memory; đóng ứng dụng nặng.
3. Chạy bản EXE mới; kiểm tra `output/logs/startup.log` có `[RAM SAFE]` và số RAM ở `[RAM]`.
4. Chọn **REMOTE + Chỉ ghi lời thoại**, thực hiện một phiên ngắn, quan sát RAM của `SoundText.exe`.
5. Kiểm tra `Noi_dung_cuoc_hop.txt` đã lưu đúng.
6. Chỉ sau khi các bước trên ổn định mới thử **Ghi & dịch** với một câu ngắn, và dừng nếu mức RAM tăng bất thường.

Nếu Windows mất phản hồi, **đừng thử lặp lại**: dừng tiến trình SoundText từ Task Manager khi còn điều khiển được, phục hồi Explorer hoặc khởi động lại Windows nếu cần. Quay về bản Python đang ổn định và đọc log trước.

## 16. Kiểm thử và chẩn đoán

### 16.1 Kiểm tra cú pháp

```cmd
python -m compileall -q main.py entry_soundtext.py app scripts
```

Không in gì và trả mã 0 thường biểu thị không có lỗi cú pháp. Kiểm tra này không bảo đảm các model chạy được.

### 16.2 Unit test

Các file trong `tests/` sử dụng **pytest**. Nếu môi trường chưa cài:

```cmd
python -m pip install pytest
```

Chạy:

```cmd
python -m pytest -q tests
```

Một số bài sẽ tự bỏ qua hoặc phụ thuộc giao diện/OS. **Test pass không tương đương kiểm chứng ASR/MT thực tế với model nặng và thiết bị ghi âm Windows.**

### 16.3 Diagnose và kiểm tra model

```cmd
python scripts\diagnose.py
python main.py check --config config_stt_vi_ja.json
```

`python main.py check` **có tải engine ASR**, vì vậy chậm hơn bước kiểm tra hiện diện file và có thể dùng nhiều RAM.

### 16.4 Đọc log của đúng bản chạy

- Source: xem log trên console và các file trong `output/` của thư mục source.
- EXE: `dist/SoundText/output/logs/startup.log`.
- Phiên họp: `output/meeting_.../`, gồm JSONL, file kỹ thuật và TXT.

Khi gửi báo lỗi, nên gửi **full traceback**, phiên bản Python, môi trường Conda, RAM máy và trạng thái (source/EXE). Có thể che đường dẫn chứa tên người dùng và thông tin riêng tư trong transcript.

## 17. Các lỗi thường gặp và cách khắc phục

### 17.1 CMD báo `'scripts' is not recognized ...`

**Nguyên nhân:** dùng đường dẫn kiểu Unix trong Miniconda Prompt Windows hoặc chưa vào thư mục gốc.

```cmd
cd /d C:\Users\Admin\AI\soundtext1
.\scripts\build_windows_exe.bat
```

### 17.2 `AssertionError: Use 64-bit Python 3.10-3.12`

Kiểm tra:

```cmd
python --version
python -c "import struct; print(struct.calcsize('P')*8)"
```

Nếu đang dùng **Python 3.14**, tạo `soundtext-build` với Python **3.11 x64**; không nên xóa assertion chỉ để tiếp tục build.

### 17.3 `SyntaxError: f-string: expecting '}'` trong `main.py`

Đây là lỗi dấu nháy trong source cũ, không phải lỗi PyInstaller. Dùng mã nguồn v10.10 đã sửa và chạy:

```cmd
python -m py_compile main.py
```

Không nên chỉ bỏ kiểm tra import `main.py` để che lỗi cú pháp.

### 17.4 EXE báo `ModuleNotFoundError: No module named 'main'`

Bản `SoundText.spec` mới có `main` trong `hiddenimports` và script build có bước `--check-bundle`. Cần build lại từ source/spec mới; **không** chép EXE cũ lẫn với `_internal` mới.

### 17.5 `FileNotFoundError: ... _internal\funasr\version.txt`

FunASR cần một file dữ liệu `version.txt` khi import. Spec mới sử dụng `collect_data_files` để đưa file này vào `_internal/funasr/`, và script build kiểm tra nó trước khi báo thành công.

Kiểm tra:

```cmd
dir "dist\SoundText\_internal\funasr\version.txt"
```

Nếu thiếu, kiểm tra package FunASR trong môi trường Conda build và build lại với `SoundText.spec` v10.10. Chép file thủ công có thể sửa tạm, nhưng sẽ bị mất sau lần build sạch tiếp theo.

### 17.6 `OSError: could not get source code` khi chạy EXE

Một số thư viện như **FunASR**, **Transformers**, **Optimum** hoặc cơ chế đăng ký model cần đọc file Python `.py` bằng `inspect`, trong khi bytecode đóng gói không đủ.

Spec hiện dùng `module_collection_mode='pyz+py'` cho những nhóm package cần nguồn và thu thập source/data thích hợp. Nếu lỗi còn xuất hiện:

- Mở **full traceback** ở `dist\SoundText\output\logs\startup.log`.
- Xác định **package và file đầu tiên trong traceback**; không mặc định mọi lỗi `could not get source code` đều do FunASR.
- Thử các flag chẩn đoán **theo thứ tự nhẹ → nặng** trong [mục 14.7](#147-các-lệnh-kiểm-tra-exe-từ-nhẹ-đến-nặng).
- Không tải lại model NLLB nếu chưa có chứng cứ trọng số bị thiếu/hỏng.

### 17.7 `[MODEL ERROR] Gipformer model directory not found`

Model không có cạnh EXE hoặc thư mục bị lồng sai. Kiểm tra:

```cmd
dir "dist\SoundText\models\gipformer1.5-65m-rnnt"
```

Cần thấy `encoder.int8.onnx`, `decoder.int8.onnx`, `joiner.int8.onnx`, `tokens.txt`. Source có model **không có nghĩa** thư mục `dist/SoundText/models/` cũng đã có model; script build không tự chép trọng số lớn.

### 17.8 `[TRANSLATION WARN] Missing/incompatible translation runtime`

Phân biệt hai nguyên nhân:

- **Thiếu trọng số:** thiếu `models/nllb-...` hoặc các file `.onnx`.
- **Runtime import/source lỗi:** `transformers`/`optimum`/`torch` lỗi DLL hoặc thiếu source trong EXE.

Với source, dùng `scripts/test_translation.py`. Với EXE, đọc traceback và `--check-translation-runtime` (test import), sau đó **nếu RAM đủ** mới `--check-translation-model` (test model thật). Không khuyên cài lại toàn bộ dependency trong lúc chưa biết package nào lỗi.

### 17.9 `ffmpeg is not installed` khi chạy FunASR

Đây có thể chỉ là **notice**, không phải nguyên nhân build thất bại nếu torchaudio đang xử lý âm thanh bình thường. Không cần cài FFmpeg chỉ vì thấy dòng thông báo này. Chỉ điều tra khi có lỗi thực tế liên quan backend đọc file audio.

### 17.10 Splash không hiện ảnh mình cấu hình

1. Kiểm tra `branding.json` **cạnh EXE**, không chỉ file trong source.
2. Xem `splash_path` có trỏ tới file thật trong `assets/branding/` hay không.
3. Nếu dùng đường dẫn tương đối, tính từ **thư mục chứa EXE**.
4. Xem dòng `[SPLASH] Using configured art:` hoặc `[SPLASH WARN]` trong `startup.log`.
5. Kiểm tra JSON có hợp lệ; tránh `\` không escape trong Windows path.

### 17.11 Icon cửa sổ đúng nhưng icon EXE vẫn cũ

Icon hiển thị runtime khác **icon nhúng vào file EXE khi build**. Sửa `branding.json`, chạy lại build và thay toàn bộ thư mục EXE, sau đó kiểm tra icon/file properties. Windows Explorer có thể dùng icon cache nên đôi khi cần refresh Explorer.

### 17.12 Chỉ chọn REMOTE nhưng MIC vẫn thu

Về thiết kế, chế độ **REMOTE** chỉ mở loopback. Kiểm tra đã chọn đúng nguồn trước khi Start, log có `[AUDIO] SYSTEM capture started` và không có `[AUDIO] MIC capture started`, đồng thời thanh MIC ở 0. Nếu vẫn có MIC, gửi log nguồn thu và phiên bản EXE/source đang dùng; đừng tự thêm bộ lọc mic phức tạp gây mất tiếng.

### 17.13 MIC + REMOTE xuất hiện lời thoại trùng

Nếu dùng **loa ngoài**, microphone vật lý có thể thu tiếng loa. SoundText v10.10 đã bỏ bộ lọc chống trùng mạnh trên MIC để tránh cắt mất giọng thật. Dùng **tai nghe**, kiểm tra đúng thiết bị microphone và tránh chọn Stereo Mix/loopback làm MIC.

### 17.14 Phụ đề hoặc bản dịch chậm

- Bản gốc có thể cập nhật theo partial; bản dịch Nhật/Anh thường đợi **ASR Final**.
- VAD cần chờ ngừng nói hoặc điểm cắt câu để tạo Final.
- Lần dịch đầu trên EXE 8 GB có thể chậm vì NLLB được **nạp theo yêu cầu**.
- Tăng số CPU thread không nhất thiết cải thiện; có thể làm RAM tăng.

### 17.15 EXE chạy quá nhiều RAM, Windows đen màn hình hoặc mất taskbar

**Dừng thử EXE lỗi.** Nếu còn điều khiển được, mở Task Manager (`Ctrl + Shift + Esc`), **End task SoundText.exe**, rồi Restart **Windows Explorer** để khôi phục taskbar. Nếu Windows không phản hồi, dùng màn hình bảo mật `Ctrl + Alt + Del` hoặc khởi động lại máy.

Sau khi Windows ổn định:

1. Quay lại bản Python hoặc EXE đã từng chạy ổn.
2. Kiểm tra phiên bản v10.10 và log `[RAM SAFE]`/`[RAM WARN]`.
3. Thử **chỉ REMOTE + chỉ ghi lời thoại** trên một phiên ngắn, có Task Manager quan sát.
4. Không ép chạy NLLB khi RAM thiếu và không dùng các bài test tải model nặng để tái hiện lỗi trên máy chính.

**Đừng hiểu “95 test pass” hoặc “build thành công” là cam kết EXE an toàn RAM trên mọi máy.** Kiểm thử model thật và Native DLL trên Windows 8 GB là một bước riêng.

### 17.16 Không thấy `Noi_dung_cuoc_hop.txt`

- Nhấn **Dừng & lưu** và chờ quá trình lưu hoàn tất.
- Nhấn **Mở thư mục lưu**; nếu đang xem `output/` của source, kiểm tra bạn có thực ra chạy EXE dưới `dist/SoundText/` hay không.
- Mỗi phiên có thư mục `meeting_...` riêng. Vào thư mục phiên mới nhất, rồi `Noi_dung/`.
- Kiểm tra `output/meeting_.../transcript.jsonl` xem có câu Final được ghi không; nếu không có Final, TXT có thể chỉ báo chưa có lời thoại.

## 18. Cập nhật, sao lưu và phân phối

### 18.1 Những gì luôn nên sao lưu

```text
branding.json
config_stt_vi_ja.json
assets/branding/
models/
output/
```

Ngoài ra, nên giữ một thư mục **source/EXE gần nhất chạy ổn định** để quay lại nếu bản build mới lỗi.

### 18.2 Cập nhật source

- Giải nén bản source mới đè **mã nguồn cần cập nhật**, nhưng không xóa `models/`, `output/` và ảnh cá nhân.
- Bản ZIP v10.10 có các file `.default.json` thay vì cố ý ghi đè cấu hình cá nhân.
- Kiểm tra script mới có thay đổi model hoặc version dependency trước khi build.
- Tránh trộn các bản vá có nguồn gốc khác nhau mà không đối chiếu, vì script/spec có thể không đồng bộ với source.

### 18.3 Cập nhật EXE

- **Không** chép mỗi `SoundText.exe` mới đè lên `_internal/` cũ.
- Nên giữ cả thư mục `dist/SoundText/` mới như một bộ riêng, copy đúng `models/`, config và ảnh.
- Không ghi đè model hoặc dữ liệu người dùng nếu không cần.
- Kiểm tra bản EXE mới chạy ổn rồi mới xóa bản cũ.

### 18.4 Về dung lượng

Bản ZIP source nhẹ vì **không kèm model**. Khi phát hành portable, dung lượng sẽ tăng đáng kể do `_internal/` (Torch, ONNX, DLL) và `models/`. Định dạng one-folder là lựa chọn có chủ ý để dễ chẩn đoán và cập nhật, không phải lỗi đóng gói.

## 19. Giới hạn hiện tại và lưu ý bảo mật

1. **Không phải công cụ tách sạch nhiều giọng chồng nhau:** diarization chỉ gắn nhãn speaker, không bảo đảm ASR ghi đủ lời khi 2–3 người nói cùng lúc trên một luồng đã trộn.
2. **Độ chính xác phụ thuộc chất lượng âm thanh:** tiếng vọng qua loa, tiếng ồn, microphone nhỏ hoặc nội dung nhạc/phim phức tạp có thể làm sai nhận diện.
3. **Dịch có độ trễ:** đặc biệt khi đợi Final hoặc lần tải NLLB đầu tiên trên EXE RAM thấp.
4. **Không có bản build EXE nào được đảm bảo chỉ nhờ pytest:** cần kiểm tra trên Windows 64-bit với cùng phiên bản dependency, RAM và driver âm thanh dự kiến.
5. **Model/license:** trước khi phát hành sản phẩm rộng rãi, kiểm tra điều kiện cấp phép của **từng model và dependency** theo nguồn của chúng. README này **không tự cấp quyền phân phối** các trọng số hoặc thư viện bên thứ ba.
6. **Quyền riêng tư:** nội dung phiên họp có thể chứa thông tin nhạy cảm; `output/` và các file JSONL/journal có thể lưu lời thoại gốc và bản dịch. Chỉ chia sẻ TXT/JSON với người được phép xem. Không đính kèm toàn bộ `output/` vào báo lỗi công khai nếu chứa dữ liệu thật.
7. **Phụ thuộc nền tảng:** thu loopback dùng cơ chế âm thanh Windows; không coi các lệnh này là hướng dẫn chạy tương đương trên Linux/macOS.

## 20. Checklist trước khi phát hành

### A. Source Python

- [ ] Có Python 3.11 64-bit trong môi trường build.
- [ ] `python -m pip install -r requirements.txt` hoàn tất.
- [ ] `python scripts\diagnose.py` không báo thiếu model/thư viện quan trọng.
- [ ] `python -m compileall -q main.py entry_soundtext.py app scripts` không lỗi.
- [ ] Chạy source và thử **REMOTE + Chỉ ghi lời thoại** thành công.
- [ ] Thử **MIC**, **MIC + REMOTE**, hai cửa phụ đề, Start/Stop và xuất TXT nếu thiết bị/nguồn có sẵn.

### B. Branding

- [ ] `branding.json` hợp lệ, icon và splash trỏ tới ảnh thật.
- [ ] Dùng đường dẫn tương đối để có thể chuyển máy.
- [ ] `app_version`, `publisher`, `app_name` đúng trước khi build.
- [ ] Đã khởi động lại để kiểm tra icon/splash runtime.

### C. Build EXE

- [ ] Build bằng `.\scripts\build_windows_exe.bat` trong đúng môi trường Conda.
- [ ] EXE đi cùng `_internal/`.
- [ ] Có `_internal\funasr\version.txt`.
- [ ] Có đầy đủ `dist\SoundText\models\...`.
- [ ] `--check-bundle` chạy thành công.
- [ ] Bản EXE chạy bình thường khi mở qua File Explorer/shortcut.
- [ ] `dist\SoundText\output\logs\startup.log` không có lỗi khởi động đáng kể.

### D. Kiểm tra an toàn cho máy 8 GB

- [ ] Đã sao lưu source/EXE phiên bản ổn định.
- [ ] `startup.log` có `[RAM SAFE]` thay vì `[RAM WARN]` khi áp dụng giới hạn.
- [ ] Đã chạy **REMOTE + Chỉ ghi lời thoại** trong phiên ngắn, quan sát RAM.
- [ ] Đã kiểm tra `Noi_dung_cuoc_hop.txt` sau **Dừng & lưu**.
- [ ] Chỉ thử NLLB khi còn RAM trống thích hợp; chấp nhận khả năng chế độ dịch bị từ chối để tránh cạn bộ nhớ.
- [ ] Không chạy lại bản EXE từng khiến Windows treo chỉ để thử lại.

---

## Tóm tắt lệnh nhanh

```cmd
:: Mở dự án trong Miniconda Prompt
conda activate soundtext-build
cd /d C:\Users\Admin\AI\soundtext1

:: Cài thư viện
python -m pip install -r requirements.txt
python -m pip install -r requirements-exe.txt

:: Tải model (cần Internet lần đầu)
python scripts\prepare_models.py
python scripts\prepare_speakers.py
python scripts\prepare_translation_onnx.py

:: Kiểm tra nhẹ
python scripts\diagnose.py

:: Mở giao diện từ source (chưa tự ghi âm)
python main.py run --config config_stt_vi_ja.json

:: Build portable Windows EXE
.\scripts\build_windows_exe.bat

:: Chép model vào thư mục EXE sau khi build
robocopy "models" "dist\SoundText\models" /E

:: Test import EXE (không tải model nặng)
start "" /wait "dist\SoundText\SoundText.exe" --check-bundle
```

**Giấy phép:** gói source v10.10 đang được tham chiếu trong README này không có file `LICENSE` riêng; trước khi phát hành công khai, chủ dự án cần xác định giấy phép cho mã nguồn và kiểm tra giấy phép/trọng số của các model, dependency bên thứ ba.

**SoundText ưu tiên:** bản ghi dễ đọc, xử lý cục bộ, hai nguồn âm thanh rõ ràng, phụ đề dễ theo dõi và khả năng dùng bền bỉ trên máy Windows không có GPU mạnh. Nếu phải đánh đổi giữa tốc độ tải model và độ ổn định trên 8 GB RAM, hãy ưu tiên **độ ổn định và giữ nguyên bản ghi**.
