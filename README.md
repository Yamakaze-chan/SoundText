[Tiếng Việt](README.md) | [English](README_EN.md)

# SoundText

**Phiên âm và dịch hội thoại trực tiếp trên Windows.**

SoundText là ứng dụng desktop giúp bạn ghi lại lời nói từ microphone, âm thanh máy tính hoặc cả hai. Nội dung được hiển thị trong lúc nghe, có thể dịch tiếng Nhật và tiếng Anh sang tiếng Việt, và được lưu thành bản ghi dễ đọc sau mỗi phiên.

Mình bắt đầu xây dựng SoundText với một nhu cầu khá đơn giản: theo dõi nội dung cuộc họp, video và bài giảng mà không phải liên tục chuyển qua lại giữa ứng dụng nghe và công cụ dịch. SoundText hướng đến việc làm những công việc đó ngay trên máy tính, với giao diện dễ dùng và không cần GPU.

![UI SoundText](docs/images/1.jpg)
![Sub-windows](docs/images/2.png)
![Results](docs/images/3.png)

## Tính năng

- **Ghi âm linh hoạt:** chọn microphone (MIC), âm thanh máy tính (REMOTE) hoặc cả hai. Ứng dụng chỉ bắt đầu ghi khi bạn nhấn **Bắt đầu ghi âm**.
- **Nhận diện tiếng Việt, Nhật và Anh:** hiển thị lời thoại trong quá trình sử dụng; hỗ trợ chế độ tự nhận diện Nhật/Anh.
- **Ghi lời thoại hoặc ghi kèm bản dịch:** chọn **Chỉ ghi lời thoại** nếu không cần dịch, hoặc **Ghi & dịch** để dịch nội dung Nhật/Anh sang tiếng Việt.
- **Hai cửa sổ phụ đề nổi:** lời thoại gốc và bản dịch nằm ở hai cửa sổ độc lập. Bạn có thể chỉ mở cửa cần thiết, kéo đến vị trí phù hợp và điều chỉnh cỡ chữ.
- **Nhận biết người nói ở nguồn REMOTE:** gắn nhãn người nói khi đủ thông tin; microphone không sử dụng bước phân biệt người nói.
- **Lưu bản ghi dễ đọc:** xuất file `Noi_dung/Noi_dung_cuoc_hop.txt` cùng mốc thời gian, nguồn âm thanh, lời gốc và bản dịch nếu có. Các file kỹ thuật vẫn được giữ để kiểm tra lỗi.
- **Tùy chỉnh tên và hình ảnh ứng dụng:** chỉnh `branding.json` để thay tên hiển thị, icon và splash screen.

SoundText phù hợp cho việc ghi chú cuộc họp, theo dõi video hoặc bài học bằng tiếng Nhật/Anh, và lưu lại những cuộc trao đổi cần xem lại sau này.

## Ứng dụng hoạt động như thế nào?

SoundText xử lý nhận diện giọng nói và dịch thuật **trên máy tính của bạn** sau khi các model đã được tải về. Bạn không cần gửi âm thanh cuộc họp lên một dịch vụ nhận diện trực tuyến để sử dụng các tính năng này.

Việc xử lý cục bộ có những giới hạn: tốc độ còn phụ thuộc CPU và RAM; tiếng ồn, giọng nói chồng nhau hoặc câu nói quá ngắn có thể làm giảm độ chính xác. Nếu dùng máy 8 GB RAM, nên bắt đầu với một nguồn âm thanh và chế độ chỉ ghi lời thoại, sau đó mới thử bật dịch.

## Yêu cầu hệ thống

| Thành phần | Yêu cầu / khuyến nghị |
| --- | --- |
| Hệ điều hành | Windows 10/11, 64-bit |
| Python | Python 3.11 (64-bit) được khuyến nghị để chạy source và build EXE |
| Phần cứng | CPU x64; không bắt buộc có GPU |
| RAM | 8 GB có thể sử dụng với chế độ tiết kiệm bộ nhớ; nhiều RAM hơn sẽ thuận lợi khi bật dịch |
| Âm thanh | Microphone hoặc thiết bị âm thanh Windows hỗ trợ loopback |

## Chạy từ mã nguồn

### 1. Chuẩn bị môi trường

Mở Miniconda Prompt tại thư mục dự án:

```bat
conda create -n soundtext python=3.11 -y
conda activate soundtext
python -m pip install -r requirements.txt
```

### 2. Chuẩn bị model

Chạy `SoundText.bat` và sử dụng các mục trong menu:

| Mục | Công dụng |
| --- | --- |
| `8` | Tải model nhận diện: Gipformer, SenseVoice và Silero VAD |
| `9` | Chuẩn bị model phân biệt người nói |
| `10` | Chuẩn bị model dịch NLLB ONNX |
| `4` | Kiểm tra model và thư viện |
| `1` | Mở giao diện SoundText |

Model không được đóng gói sẵn cùng source. Sau khi tải, chúng được lưu trong thư mục `models/`.

### 3. Bắt đầu sử dụng

Khởi chạy:

```bat
SoundText.bat
```

Chọn `1` trong menu. Khi giao diện mở, chọn nguồn âm thanh, ngôn ngữ và chế độ nội dung, sau đó nhấn **Bắt đầu ghi âm**. Để kết thúc phiên, nhấn **Dừng & lưu**.

Nút **Mở thư mục lưu** sẽ giúp bạn tìm bản ghi TXT. Nhật ký, JSON và những dữ liệu dùng để khôi phục hoặc kiểm tra lỗi vẫn được giữ riêng.

## Các công nghệ được sử dụng

| Thành phần | Vai trò |
| --- | --- |
| CustomTkinter | Giao diện desktop |
| Gipformer | Nhận diện giọng nói tiếng Việt |
| SenseVoice / FunASR | Nhận diện giọng nói tiếng Nhật và tiếng Anh |
| Silero VAD | Phát hiện vùng có tiếng nói |
| NLLB ONNX | Dịch tiếng Nhật/Anh sang tiếng Việt |
| ONNX speaker models | Hỗ trợ gắn nhãn người nói ở nguồn REMOTE |
| PyAudioWPatch / WASAPI loopback | Thu âm microphone và âm thanh máy tính |
| PyInstaller | Đóng gói bản Windows portable |

## Build bản Windows EXE

SoundText sử dụng PyInstaller ở chế độ **one-folder**: file thực thi và các thư viện được đặt trong cùng một thư mục phát hành; model được giữ ở ngoài EXE để có thể cập nhật riêng.

Trong môi trường Python 3.11 đã cài dependencies, chạy:

```bat
python -m pip install -r requirements-exe.txt
.\scripts\build_windows_exe.bat
```

Kết quả dự kiến nằm tại `dist\SoundText\`. Sao chép model đã tải vào `dist\SoundText\models\`, rồi chạy `dist\SoundText\SoundText.exe`.

**Khi phân phối ứng dụng, cần giữ nguyên toàn bộ thư mục `dist\SoundText`, không chỉ copy riêng file `.exe`.**

## Cấu trúc dự án

```text
SoundText/
├── main.py                       # Điểm vào ứng dụng Python
├── SoundText.bat                 # Menu khởi chạy và chuẩn bị môi trường
├── branding.default.json        # Cấu hình mẫu cho tên, icon, splash
├── config_stt_vi_ja.default.json # Cấu hình mẫu cho ghi âm và nhận diện
├── app/                          # Giao diện, audio, ASR, dịch và lưu dữ liệu
├── scripts/                      # Công cụ chuẩn bị model và build EXE
├── models/                       # Model đã tải (không đưa lên Git)
└── output/                       # Bản ghi và nhật ký
```

Sau lần chạy đầu tiên, bạn có thể sửa `branding.json` để thay tên hiển thị và hình ảnh của ứng dụng. Riêng icon và metadata nhúng vào file EXE cần được thiết lập trước khi build.

## Quyền riêng tư và những điều cần lưu ý

SoundText xử lý âm thanh trên máy sau khi các model đã được chuẩn bị. Trong quá trình cài đặt hoặc tải model, ứng dụng có thể cần kết nối Internet. Dữ liệu phiên được lưu ở `output/`; hãy kiểm tra trước khi chia sẻ log hoặc đưa thư mục này lên repository.

Khả năng nhận diện, dịch và phân biệt người nói không phải lúc nào cũng chính xác, đặc biệt khi nhiều người nói cùng lúc. Model lớn cũng có thể sử dụng đáng kể RAM. Với Windows 8 GB, bản EXE cần được kiểm tra cẩn thận trước khi bật đồng thời nhiều nguồn và chế độ dịch.

Nếu định phân phối SoundText cùng model, hãy kiểm tra giấy phép của từng model và thư viện liên quan.

## Đóng góp và báo lỗi

Nếu bạn gặp lỗi, có thể mở Issue kèm phiên bản SoundText, phiên bản Windows/Python, các bước tái hiện và phần log liên quan. Hãy xóa thông tin cá nhân hoặc nội dung cuộc họp trước khi đăng log công khai.

Mình hoan nghênh các góp ý giúp SoundText ổn định hơn, dễ sử dụng hơn và tương thích tốt hơn với nhiều cấu hình Windows.

Email liên hệ: nhatvipmason@gmail.com