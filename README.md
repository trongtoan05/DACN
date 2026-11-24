# EyeViora

**EyeViora** là ứng dụng web hỗ trợ đặt lịch và quản lý hoạt động của chuỗi phòng khám mắt. Hệ thống kết nối bệnh nhân, bác sĩ, lễ tân và quản trị viên trong các quy trình từ tra cứu chi nhánh đến khám, lập hóa đơn và thanh toán.

## Chức năng chính

- Tra cứu phòng khám, dịch vụ, bảng giá, vị trí bản đồ và chỉ đường.
- Đặt lịch với một hoặc nhiều dịch vụ, xem và quản lý lịch hẹn.
- Tiếp nhận bệnh nhân theo chi nhánh và quyền được phân công.
- Lập hồ sơ khám mắt, đơn thuốc, đơn kính và xác nhận dịch vụ đã thực hiện.
- Tạo hóa đơn sau khi lượt khám hoàn tất; hỗ trợ thu tiền mặt và VNPAY Sandbox.
- Quản lý chi nhánh, nhân sự, dịch vụ, tài khoản và báo cáo.
- Gửi email xác nhận lịch hẹn và chứng từ thanh toán khi được cấu hình.

Hệ thống hoạt động theo mô hình tự chi trả, không sử dụng BHYT. Chỉ các dịch vụ được xác nhận đã thực hiện mới được tính vào hóa đơn.

## Công nghệ

- Python 3.12
- Django 6.0 và GeoDjango
- PostgreSQL với PostGIS
- HTML, CSS, JavaScript và Django Templates
- Leaflet và OpenStreetMap
- VNPAY Sandbox cho kiểm thử thanh toán trực tuyến

Các thư viện Python được liệt kê trong [`requirements.txt`](requirements.txt).

## Cấu trúc source

```text
eyeviora/       Cấu hình, URL và điểm vào ASGI/WSGI của Django
core/           Model, nghiệp vụ, giao diện, static files và migrations
manage.py       Công cụ quản lý Django
requirements.txt
.env.example    Mẫu biến môi trường; không chứa thông tin đăng nhập thật
```

## Yêu cầu môi trường

- Windows 10/11 hoặc môi trường tương thích.
- Python 3.12.
- PostgreSQL và phần mở rộng PostGIS.
- Thư viện GDAL và GEOS tương thích với môi trường Python để sử dụng GeoDjango.

## Cài đặt và chạy trên Windows

Mở PowerShell tại thư mục chứa `manage.py`, sau đó thực hiện:

### 1. Tạo môi trường Python và cài thư viện

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 2. Tạo cơ sở dữ liệu

Trong PostgreSQL, tạo database `eyeviora_clinic` và bật PostGIS cho database đó. Có thể dùng pgAdmin để tạo database, sau đó mở Query Tool trên database vừa tạo và chạy:

```sql
CREATE EXTENSION IF NOT EXISTS postgis;
```

### 3. Tạo file cấu hình local

```powershell
Copy-Item .env.example .env
```

Mở `.env`, điền thông tin kết nối PostgreSQL và đường dẫn GDAL/GEOS đúng với máy. Tạo `SECRET_KEY` riêng bằng lệnh:

```powershell
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Đặt giá trị tạo được vào `SECRET_KEY` trong `.env`. Để chạy local, đặt `DEBUG=True`. Các thông tin VNPAY, SMTP và dịch vụ bản đồ là tùy chọn cho những chức năng tương ứng; chỉ sử dụng credentials riêng trong file `.env`.

### 4. Tạo bảng và tài khoản quản trị

```powershell
python manage.py migrate
python manage.py createsuperuser
```

### 5. Kiểm tra và khởi động website

```powershell
python manage.py check
python manage.py runserver
```

Mở <http://127.0.0.1:8000/> trong trình duyệt. Dừng máy chủ bằng `Ctrl+C` trong cửa sổ PowerShell.

## Kiểm tra và kiểm thử

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test core
```
