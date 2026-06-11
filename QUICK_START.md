# 📖 HƯỚNG DẪN CÀI ĐẶT VÀ SỬ DỤNG HỆ THỐNG AI TẠP CHÍ

Chào mừng bạn đến với hệ thống **AI Magazine Generator** - Nền tảng tự động dàn trang và chuyển đổi tài liệu Word (.docx) thành tạp chí PDF chuyên nghiệp có giao diện lật sách (Flipbook) 3D, hỗ trợ xác thực người dùng và nạp tiền tự động.

---

## 🛠️ PHẦN 1: HƯỚNG DẪN CÀI ĐẶT & KHỞI CHẠY (Dành cho Lập trình viên)

### 1. Chuẩn bị Môi trường
Dự án yêu cầu cài đặt sẵn **Python 3.8 đến 3.12**.

### 2. Cài đặt các Thư viện phụ thuộc
Mở terminal tại thư mục gốc của dự án và chạy lệnh:
```bash
pip install -r requirements.txt
```
*(Lưu ý: Nếu sử dụng cơ sở dữ liệu MySQL, bạn cần cài đặt thêm thư viện driver kết nối bằng lệnh `pip install PyMySQL`)*.

### 3. Cấu hình biến môi trường
1. Nhân bản file `.env.example` thành file `.env` nằm tại thư mục gốc.
2. Mở file `.env` và thiết lập các thông số:
   * **`SECRET_KEY`**: Chuỗi ký tự ngẫu nhiên dùng để bảo mật session.
   * **`SQLALCHEMY_DATABASE_URI`**: Đường dẫn kết nối cơ sở dữ liệu. 
     * Sử dụng SQLite (Đơn giản nhất): `sqlite:///users.db`
     * Sử dụng MySQL: `mysql+pymysql://username:password@localhost:3306/ai_tapchi`
   * **Cấu hình thanh toán SePay**: Điền `SEEPAY_MERCHANT_ID`, `SEEPAY_API_KEY`, `SEEPAY_SECRET_KEY` để tự động xác nhận giao dịch.
   * **Cấu hình Đăng nhập Google (Tùy chọn)**: Điền `GOOGLE_CLIENT_ID` và `GOOGLE_CLIENT_SECRET` để bật nút đăng nhập bằng Google OAuth.

### 4. Tạo tài khoản Quản trị viên (Admin)
Chạy script sau để khởi tạo cơ sở dữ liệu và tự động tạo tài khoản Admin mặc định:
```bash
python create_admin.py
```
*Tài khoản Admin mặc định được tạo:*
* **Email**: `admin@gmail.com`
* **Mật khẩu**: `123456`

### 5. Khởi chạy Ứng dụng
Chạy lệnh sau để khởi động Web Server:
```bash
python app.py
```
Ứng dụng sẽ hoạt động tại địa chỉ: **`http://localhost:5000`**

---

## 🚀 PHẦN 2: LUỒNG TÍNH NĂNG DÀNH CHO NGƯỜI DÙNG

Hệ thống hoạt động theo một quy trình khép kín từ lúc đăng ký tài khoản, nạp tiền đến thiết kế và xuất bản tạp chí.

### 1. Đăng ký & Đăng nhập
* Người dùng có thể đăng ký tài khoản cục bộ thông qua email/mật khẩu hoặc đăng nhập nhanh bằng **Tài khoản Google** (OAuth).

### 2. Quản lý Số dư & Nạp tiền (Billing)
* Mỗi lượt tạo tạp chí sẽ tiêu tốn một khoản tiền ảo cố định (mặc định là `10,000 VND`, có thể thay đổi bởi Admin).
* Để nạp tiền, người dùng truy cập trang **Nạp tiền** (`/billing`), nhập số tiền mong muốn hoặc chọn một **Gói Nạp Tiền** được định sẵn.
* Hệ thống sẽ hiển thị mã QR thanh toán cùng nội dung chuyển khoản tự động có cú pháp: `MAG[MÃ_GIAO_DỊCH]`.
* Khi người dùng chuyển khoản đúng cú pháp, **Webhook SePay** sẽ tự động bắt giao dịch thời gian thực và cộng tiền vào tài khoản người dùng ngay lập tức.

### 3. Quy trình thiết kế & Xuất bản Tạp chí (5 bước)

#### Bước 1: Chọn Phong Cách Tạp Chí (Template)
Chọn 1 trong 5 mẫu thiết kế cao cấp:
* **VOGUE** - Sang trọng (tông màu tối, ảnh tràn viền).
* **MINIMAL** - Tối giản (tông màu trắng, nhiều khoảng trống nghệ thuật).
* **ELEGANT** - Thanh lịch (tông màu nâu/vàng, font chữ Serif cổ điển).
* **MODERN** - Hiện đại (tông màu xanh dương, chữ in đậm khỏe khoắn).
* **LUXURY** - Cao cấp (tông màu đỏ thẫm/đen hoàng gia).

#### Bước 2: Thiết Kế Trang Bìa & Trang Bìa Sau
* **Tiêu đề Tạp Chí & Dòng chữ phụ**: Nhập nội dung tiêu đề chính của tạp chí để cập nhật trực quan trên canvas xem trước.
* **Ảnh Bìa Trước**: Tải lên hoặc kéo thả ảnh chân dung (dọc) làm bìa chính.
* **Ảnh Bìa Sau (Back Cover)**: Tải lên hoặc kéo thả ảnh làm bìa sau. Nếu không tải lên, trang bìa sau sẽ được để trống với nền màu xám/trung tính sang trọng thay vì lấy ảnh nội dung cuối.

#### Bước 3: Chuẩn bị nội dung file Word (.docx)
Để AI dàn trang chuẩn xác và đẹp mắt nhất, hãy định dạng file Word như sau:
1. **Tiêu đề bài viết**: Dòng đầu tiên của tệp, **VIẾT HOA TOÀN BỘ**.
2. **Sapo (Đoạn mở đầu)**: 2-3 câu viết ngay dưới tiêu đề. Hệ thống sẽ tự động tạo chữ cái phóng to (Drop Cap) ở chữ đầu tiên.
3. **Tiêu đề mục (Heading)**: Viết HOA và In đậm ở một dòng riêng trước mỗi phần nội dung mới.
4. **Câu trích dẫn (Pullquote)**: Đặt trong dấu `"ngoặc kép"` ở một dòng riêng để biến thành khối trích dẫn nghệ thuật.
5. **Hình ảnh**: Chèn ảnh ngay sau đoạn văn minh họa kèm theo chú thích ảnh bắt đầu bằng chữ `Hình: ` ở ngay dưới ảnh (Ví dụ: *Hình: Xu hướng thời trang 2026*).
6. *Lưu ý*: Tránh chèn bảng biểu (Table), Text box, SmartArt phức tạp.

#### Bước 4: Tải lên & Xử lý ngầm (Background Job)
* Kéo thả file `.docx` của bạn vào khu vực upload (cho phép upload tối đa 10 file cùng lúc cho nhiều chương).
* Nhập tiêu đề và mô tả tóm tắt cho từng chương.
* Nhấp nút **✨ XUẤT TẠP CHÍ PDF**.
* Hệ thống kiểm tra số dư: Nếu đủ tiền, tài khoản sẽ bị trừ phí và tác vụ tạo PDF sẽ được chuyển vào một luồng xử lý ngầm (Thread).
* Tiến trình xử lý hiển thị trực quan qua 5 bước:
  1. Đọc file Word (20%)
  2. Trích xuất tài nguyên ảnh (50%)
  3. Dàn trang tự động 2 cột (75%)
  4. Tạo trang bìa nghệ thuật (90%)
  5. Xuất bản tệp PDF hoàn chỉnh (98%)

#### Bước 5: Xem Flipbook 3D và Tải xuống PDF
* Khi tiến trình hoàn tất 100%, bạn có thể nhấp **Tải xuống PDF** hoặc chọn **Xem trực tiếp**.
* Trình xem trực tuyến sẽ hiển thị tạp chí dưới dạng **Sách lật 3D (Flipbook)** mô phỏng hiệu ứng lật trang thực tế trên giấy, hỗ trợ chuyển trang bằng chuột hoặc phím mũi tên.
* Bạn cũng có thể tải lên tệp PDF có sẵn bất kỳ từ máy tính cá nhân để xem bằng trình phát lật trang này.

---

## 👑 PHẦN 3: CÁC TÍNH NĂNG DÀNH CHO ADMIN (Trang Quản trị)

Khi đăng nhập bằng tài khoản Admin (như `admin@gmail.com`), bạn sẽ có quyền truy cập vào trang Admin Dashboard để quản lý toàn bộ hệ thống:

### 1. Quản lý Người dùng
* Xem danh sách tất cả thành viên trong hệ thống (thời gian đăng ký, đăng nhập cuối cùng).
* Thực hiện Khóa/Mở khóa tài khoản người dùng khi có vi phạm.
* Cộng tiền/Trừ tiền (Nạp tiền thủ công) vào tài khoản của người dùng.

### 2. Quản lý Giao dịch & Lịch sử
* Xem lịch sử tạp chí của tất cả người dùng trong hệ thống (Tên tạp chí, template đã dùng, ngày tạo).
* Quản lý danh sách các yêu cầu nạp tiền (Trạng thái: Đang chờ, Đã thanh toán, Đã hủy).

### 3. Cấu hình Hệ thống
Admin có thể tùy chỉnh các tham số vận hành mà không cần sửa code:
* **LLM Provider**: Lựa chọn nhà cung cấp AI dịch thuật/tối ưu hóa nội dung (OpenRouter, OpenAI, Gemini, DeepSeek).
* **LLM Model**: Chọn model AI chạy dàn trang (mặc định: `openai/gpt-4o-mini`).
* **API Keys**: Điền các mã token kết nối API tương ứng với nhà cung cấp đã chọn.
* **Pricing Settings**: Thay đổi giá tiền của mỗi lượt tạo tạp chí (mặc định `10,000 VND`).
* **Pricing Packages**: Tùy biến danh sách các gói nạp tiền hiển thị cho người dùng ở trang Billing dưới định dạng JSON.

---

## ❓ KHẮC PHỤC LỖI THƯỜNG GẶP (Troubleshooting)

### 1. Lỗi 413 - Request Entity Too Large
* **Nguyên nhân**: File Word hoặc ảnh bìa tải lên có dung lượng quá lớn vượt giới hạn cấu hình của Server.
* **Khắc phục**: Ứng dụng đã được nâng hạn mức giới hạn lên `500MB` trong `app.py`. Nếu chạy qua Nginx hoặc Proxy khác, hãy cấu hình thêm `client_max_body_size 500M;`.

### 2. Lỗi Font chữ tiếng Việt bị lỗi hoặc ô vuông trong file PDF
* **Nguyên nhân**: Do ReportLab không tìm thấy hoặc đăng ký font chữ Unicode hỗ trợ tiếng Việt.
* **Khắc phục**: Hệ thống đã được tích hợp phông chữ `Arial` hỗ trợ Unicode đầy đủ. Hãy đảm bảo file phông chữ `arial.ttf` nằm đúng vị trí quy định trong thư mục static/fonts của dự án.

### 3. Lỗi không lật được trang bìa sau cùng (Back Cover)
* **Nguyên nhân**: Số lượng trang tạp chí là số lẻ làm thư viện Flipbook không hiển thị được trang đôi bìa sau.
* **Khắc phục**: Hệ thống tự động kiểm tra và chèn thêm 1 trang trắng trang nhã trước bìa sau nếu tổng số trang là số lẻ, đảm bảo kết cấu lật trang hoàn hảo.
