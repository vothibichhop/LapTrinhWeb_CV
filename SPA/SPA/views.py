
from datetime import date, timedelta

import random
import uuid
from django.views.decorators.http import require_POST, require_GET
from datetime import date, datetime, timedelta
from django.utils.text import slugify


from django.shortcuts import get_object_or_404, redirect, render
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth.models import User
from services.models import Service, Booking, CustomerProfile, ChatRoom, Message, Review

from .forms import CustomerProfileForm, LoginForm, RegisterForm
import random
from django.db import transaction
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from .forms import CustomerProfileForm, LoginForm, RegisterForm

from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404

BOOKING_TIME_SLOTS = [f"{hour:02d}:00" for hour in range(8, 17)]


def get_customer_booking_status(status):
    if status == "Đang Xử Lý":
        return "Đã đặt"
    return status


def sync_completed_bookings(user=None):
    now = timezone.localtime()
    queryset = Booking.objects.filter(status="Đang Xử Lý")
    if user is not None and user.is_authenticated:
        queryset = queryset.filter(customer=user)

    for booking in queryset:
        appointment_at = timezone.make_aware(
            datetime.combine(booking.booking_date, booking.booking_time),
            timezone.get_current_timezone(),
        )
        if now >= appointment_at + timedelta(hours=3):
            booking.status = "Hoàn Thành"
            booking.save(update_fields=["status"])


def can_cancel_booking(booking):
    if booking.status != "Đang Xử Lý":
        return False
    return timezone.now() <= booking.created_at + timedelta(hours=24)



def redirect_by_role(request):
    if not request.user.is_authenticated:
        return redirect("about_page")
    if request.user.is_staff:
        return redirect("service_dashboard")
    return redirect("customer_account")


def manager_required(view_func):
    @login_required(login_url="user_login")
    def wrapped(request, *args, **kwargs):
        if not request.user.is_staff:
            messages.warning(request, "Trang này chỉ dành cho quản lý.")
            return redirect("customer_account")
        return view_func(request, *args, **kwargs)

    return wrapped


def customer_required(view_func):
    @login_required(login_url="user_login")
    def wrapped(request, *args, **kwargs):
        if request.user.is_staff:
            return redirect("service_dashboard")
        return view_func(request, *args, **kwargs)

    return wrapped


def home_entry(request):
    return redirect_by_role(request)


def format_service_price(value):
    return f"{value:,}".replace(",", ".")


def category_label(category):
    labels = {
        Service.CATEGORY_FACE: "Da mặt",
        Service.CATEGORY_BODY: "Body",
        Service.CATEGORY_HAIR: "Triệt lông",
    }
    return labels.get(category, category)


def serialize_service(service):
    return {
        "id": service.id,
        "name": service.name,
        "slug": service.slug,
        "short_description": service.short_description,
        "description": service.description,
        "category": service.category,
        "category_label": category_label(service.category),
        "duration_minutes": service.duration_minutes,
        "price": service.price,
        "price_label": format_service_price(service.price),
        "rating": service.rating,
        "image_url": service.image_url,
        "status": service.get_status_display(),
    }

# ĐÃ CẬP NHẬT ĐỂ BƠM DATA CHO POPUP 2 CỘT BÊN HTML
def build_customer_history(user=None):
    if user is None or not user.is_authenticated:
        return []

    sync_completed_bookings(user)
    profile, _ = CustomerProfile.objects.get_or_create(user=user)

    history = []
    for booking in Booking.objects.filter(customer=user).select_related("service").order_by("-created_at"):
        history.append(
            {
                "date": booking.booking_date.strftime("%d/%m/%Y"),
                "time": booking.booking_time.strftime("%H:%M"),
                "service": booking.service.name if booking.service else "Dịch vụ",
                "package": booking.package_name,
                "sessions": booking.sessions,
                "desc": booking.package_description or "",
                "status": get_customer_booking_status(booking.status),
                "price": f"{format_service_price(booking.total_price)}đ",
                "c_name": profile.display_name,
                "c_phone": profile.phone or "Chưa cập nhật",
                "c_email": user.email,
                "c_notes": booking.notes or "Không có ghi chú thêm",
            }
        )

    return history
def build_customer_history(user=None):
    if user is None or not user.is_authenticated:
        return []

    sync_completed_bookings(user)
    profile, _ = CustomerProfile.objects.get_or_create(user=user)
    history = []
    for booking in Booking.objects.filter(customer=user).select_related("service").order_by("-created_at"):
        history.append(
            {
                "date": booking.booking_date.strftime("%d/%m/%Y"),
                "time": booking.booking_time.strftime("%H:%M"),
                "service": booking.service.name if booking.service else "Dịch vụ",
                "package": booking.package_name,
                "sessions": booking.sessions,
                "desc": booking.package_description or "",
                "status": get_customer_booking_status(booking.status),
                "raw_status": booking.status,
                "booking_id": booking.id,
                "can_cancel": can_cancel_booking(booking),
                "cancel_url": reverse("cancel_booking", args=[booking.id]),
                "price": f"{format_service_price(booking.total_price)}đ",
                "c_name": profile.display_name,
                "c_phone": profile.phone or "Chưa cập nhật",
                "c_email": user.email,
                "c_notes": booking.notes or "Không có ghi chú thêm",
            }
        )
    return history


def user_login(request):
    if request.user.is_authenticated:
        return redirect_by_role(request)

    if request.method == 'POST':
        form = LoginForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data.get('email')
            password = form.cleaned_data.get('password')

            try:
                user = User.objects.get(email=email)
                user = authenticate(request, username=user.username, password=password)

                if user is not None:
                    login(request, user)
                    messages.success(request, f'Chào mừng {user.first_name or user.username}!')
                    if user.is_staff:
                        return redirect('service_dashboard')
                    return redirect('customer_account')
                else:
                    messages.error(request, 'Mật khẩu không chính xác.')
            except User.DoesNotExist:
                messages.error(request, 'Email này không tồn tại.')

    else:
        form = LoginForm()

    return render(request, 'login.html', {'form': form})


def user_register(request):
    if request.user.is_authenticated:
        return redirect_by_role(request)

    if request.method == 'POST':
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.username = form.cleaned_data.get('email')
            user.save()
            CustomerProfile.objects.get_or_create(
                user=user,
                defaults={
                    "full_name": f"{user.last_name} {user.first_name}".strip(),
                    "member_since": user.date_joined.date(),
                    "loyalty_points": 120,
                    "avatar_url": "https://images.unsplash.com/photo-1494790108377-be9c29b29330?auto=format&fit=crop&w=400&q=80",
                },
            )

            messages.success(request, 'Đăng ký thành công! Vui lòng đăng nhập.')
            return redirect('user_login')
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f'{error}')

    else:
        form = RegisterForm()

    return render(request, 'register.html', {'form': form})


def user_logout(request):
    logout(request)
    messages.success(request, 'Bạn đã đăng xuất thành công!')
    return redirect('user_login')


@manager_required
def service_dashboard(request):
    # LẤY DỮ LIỆU THẬT TỪ DATABASE
    services = Service.objects.all()
    return render(request, 'service_dashboard.html', {'services': services})


@manager_required
def appointment_dashboard(request):
    appointments = [
        {
            "id": 1,
            "customer": "Nguyễn Thị Trà My",
            "phone": "0901234567",
            "service": "Trị mụn chuyên sâu",
            "date": "25/02/2026",
            "time": "16:00",
            "status": "Đang Tiến Hành",
            "status_class": "green",
            "note": "Khách yêu cầu phòng riêng",
        },
        {
            "id": 2,
            "customer": "Phạm Thị Hoài",
            "phone": "0905050323",
            "service": "Triệt lông công nghệ Diode",
            "date": "10/02/2026",
            "time": "9:00",
            "status": "Hoàn Thành",
            "status_class": "blue",
            "note": "Đã hoàn tất liệu trình theo lịch đặt",
        },
        {
            "id": 3,
            "customer": "Võ Bích Hợp",
            "phone": "0328775385",
            "service": "Gội đầu dưỡng sinh",
            "date": "10/02/2026",
            "time": "13:00",
            "status": "Hoàn Thành",
            "status_class": "blue",
            "note": "Khách thanh toán tại quầy",
        },
        {
            "id": 4,
            "customer": "Nguyễn Thị Hoa",
            "phone": "0384726564",
            "service": "Post-Acne Recovery Therapy",
            "date": "11/02/2026",
            "time": "9:00",
            "status": "Hoàn Thành",
            "status_class": "blue",
            "note": "Khách đặt lại lịch tái khám",
        },
        {
            "id": 5,
            "customer": "Lê Thị Bé Như",
            "phone": "0376258537",
            "service": "Acne Detox Therapy",
            "date": "11/02/2026",
            "time": "13:00",
            "status": "Đã Hủy",
            "status_class": "red",
            "note": "Khách báo hủy trước 2 giờ",
        },
        {
            "id": 6,
            "customer": "Nguyễn Cao Sang",
            "phone": "0387642458",
            "service": "Acne Detox Therapy",
            "date": "12/02/2026",
            "time": "13:00",
            "status": "Hoàn Thành",
            "status_class": "blue",
            "note": "Khách yêu cầu xuất hóa đơn",
        },
        {
            "id": 7,
            "customer": "Đoàn Thanh Nhã",
            "phone": "0927462684",
            "service": "Post-Acne Recovery Therapy",
            "date": "9/02/2026",
            "time": "7:00",
            "status": "Đã Hủy",
            "status_class": "red",
            "note": "Khách đến trễ nên lịch bị hủy",
        },
        {
            "id": 8,
            "customer": "Trần Thị Yến",
            "phone": "0912345678",
            "service": "Chăm sóc da mặt cao cấp",
            "date": "15/02/2026",
            "time": "10:00",
            "status": "Đang Tiến Hành",
            "status_class": "green",
            "note": "Khách sử dụng voucher giảm 20%",
        },
        {
            "id": 9,
            "customer": "Lương Thị Nhà",
            "phone": "0923456789",
            "service": "Massage body thư giãn",
            "date": "14/02/2026",
            "time": "14:30",
            "status": "Hoàn Thành",
            "status_class": "blue",
            "note": "Khách thanh toán bằng thẻ",
        },
        {
            "id": 10,
            "customer": "Ngô Hồng Duyên",
            "phone": "0934567890",
            "service": "Triệt lông công nghệ Diode",
            "date": "13/02/2026",
            "time": "11:00",
            "status": "Hoàn Thành",
            "status_class": "blue",
            "note": "Liệu trình 5 buổi, hoàn tất buổi thứ 3",
        },
        {
            "id": 11,
            "customer": "Bùi Thị Mỹ",
            "phone": "0945678901",
            "service": "Gội đầu dưỡng sinh",
            "date": "16/02/2026",
            "time": "15:00",
            "status": "Hoàn Thành",
            "status_class": "blue",
            "note": "Khách mua thêm tinh dầu",
        },
        {
            "id": 12,
            "customer": "Đỗ Quỳnh Anh",
            "phone": "0956789012",
            "service": "Post-Acne Recovery Therapy",
            "date": "17/02/2026",
            "time": "9:30",
            "status": "Hoàn Thành",
            "status_class": "blue",
            "note": "Khách yêu cầu tư vấn thêm",
        },
        {
            "id": 13,
            "customer": "Vũ Thị Hương",
            "phone": "0967890123",
            "service": "Acne Detox Therapy",
            "date": "18/02/2026",
            "time": "16:00",
            "status": "Đang Tiến Hành",
            "status_class": "green",
            "note": "Khách lần đầu đến",
        },
        {
            "id": 14,
            "customer": "Trịnh Thị Loan",
            "phone": "0978901234",
            "service": "Chăm sóc da mặt cao cấp",
            "date": "19/02/2026",
            "time": "13:00",
            "status": "Đã Hủy",
            "status_class": "red",
            "note": "Khách hủy 1 giờ trước",
        },
        {
            "id": 15,
            "customer": "Phan Thị Thảo",
            "phone": "0989012345",
            "service": "Massage body thư giãn",
            "date": "20/02/2026",
            "time": "10:30",
            "status": "Hoàn Thành",
            "status_class": "blue",
            "note": "Khách đặt lịch tiếp tục",
        },
    ]
    modal_state = request.GET.get("modal", "")
    return render(
        request,
        "appointment_dashboard.html",
        {
            "appointments": appointments,
            "modal_state": modal_state,
        },
    )


@manager_required
def customer_dashboard(request):
    customers = [
        {
            "id": 1,
            "name": "Nguyễn Thị Lan",
            "gender": "Nữ",
            "age": "32",
            "phone": "0901234567",
            "points": "500 điểm",
            "email": "nguyen.lan@email.com",
            "address": "Mỹ An, Ngũ Hành Sơn, TP Đà Nẵng",
            "history": [
                {"date": "30/01/2026", "service": "Chăm sóc da mặt cao cấp", "status": "Hoàn Thành",
                 "price": "1.000.000"},
                {"date": "15/02/2026", "service": "Post-Acne Recovery Therapy", "status": "Hoàn Thành",
                 "price": "1.200.000"},
            ]
        },
        {
            "id": 2,
            "name": "Phạm Thị Hoài",
            "gender": "Nữ",
            "age": "28",
            "phone": "0905050323",
            "points": "200 điểm",
            "email": "pham.hoai@email.com",
            "address": "Thanh Khê, TP Đà Nẵng",
            "history": [
                {"date": "10/02/2026", "service": "Triệt lông công nghệ Diode", "status": "Hoàn Thành",
                 "price": "800.000"},
            ]
        },
        {
            "id": 3,
            "name": "Võ Bích Hợp",
            "gender": "Nữ",
            "age": "35",
            "phone": "0328775385",
            "points": "1.000 điểm",
            "email": "vo.bich@email.com",
            "address": "Hải Châu, TP Đà Nẵng",
            "history": [
                {"date": "10/02/2026", "service": "Gội đầu dưỡng sinh", "status": "Hoàn Thành", "price": "300.000"},
                {"date": "20/02/2026", "service": "Massage body thư giãn", "status": "Hoàn Thành", "price": "800.000"},
            ]
        },
        {
            "id": 4,
            "name": "Nguyễn Thị Hoa",
            "gender": "Nữ",
            "age": "26",
            "phone": "0384726564",
            "points": "300 điểm",
            "email": "nguyen.hoa@email.com",
            "address": "Sơn Trà, TP Đà Nẵng",
            "history": [
                {"date": "11/02/2026", "service": "Post-Acne Recovery Therapy", "status": "Hoàn Thành",
                 "price": "1.200.000"},
            ]
        },
        {
            "id": 5,
            "name": "Lê Thị Bé Như",
            "gender": "Nữ",
            "age": "30",
            "phone": "0376258537",
            "points": "600 điểm",
            "email": "le.be.nhu@email.com",
            "address": "Liên Chiểu, TP Đà Nẵng",
            "history": [
                {"date": "05/02/2026", "service": "Acne Detox Therapy", "status": "Hoàn Thành", "price": "950.000"},
                {"date": "12/02/2026", "service": "Chăm sóc da mặt cao cấp", "status": "Hoàn Thành",
                 "price": "1.000.000"},
            ]
        },
        {
            "id": 6,
            "name": "Nguyễn Cao Sang",
            "gender": "Nam",
            "age": "29",
            "phone": "0387642458",
            "points": "100 điểm",
            "email": "nguyen.sang@email.com",
            "address": "Cẩm Lệ, TP Đà Nẵng",
            "history": [
                {"date": "12/02/2026", "service": "Acne Detox Therapy", "status": "Hoàn Thành", "price": "950.000"},
            ]
        },
        {
            "id": 7,
            "name": "Đoàn Thanh Nhã",
            "gender": "Nam",
            "age": "27",
            "phone": "0927462684",
            "points": "100 điểm",
            "email": "doan.nha@email.com",
            "address": "Ngũ Hành Sơn, TP Đà Nẵng",
            "history": [
                {"date": "02/02/2026", "service": "Chăm sóc da mặt cao cấp", "status": "Hoàn Thành",
                 "price": "1.000.000"},
            ]
        },
        {
            "id": 8,
            "name": "Trần Thị Yến",
            "gender": "Nữ",
            "age": "31",
            "phone": "0912345678",
            "points": "750 điểm",
            "email": "tran.yen@email.com",
            "address": "Thanh Khê, TP Đà Nẵng",
            "history": [
                {"date": "15/02/2026", "service": "Chăm sóc da mặt cao cấp", "status": "Hoàn Thành",
                 "price": "1.000.000"},
                {"date": "18/02/2026", "service": "Acne Detox Therapy", "status": "Hoàn Thành", "price": "950.000"},
            ]
        },
        {
            "id": 9,
            "name": "Lương Thị Nhà",
            "gender": "Nữ",
            "age": "24",
            "phone": "0923456789",
            "points": "450 điểm",
            "email": "luong.nha@email.com",
            "address": "Hải Châu, TP Đà Nẵng",
            "history": [
                {"date": "14/02/2026", "service": "Massage body thư giãn", "status": "Hoàn Thành", "price": "800.000"},
            ]
        },
        {
            "id": 10,
            "name": "Ngô Hồng Duyên",
            "gender": "Nữ",
            "age": "33",
            "phone": "0934567890",
            "points": "900 điểm",
            "email": "ngo.duyen@email.com",
            "address": "Sơn Trà, TP Đà Nẵng",
            "history": [
                {"date": "13/02/2026", "service": "Triệt lông công nghệ Diode", "status": "Hoàn Thành",
                 "price": "800.000"},
                {"date": "20/02/2026", "service": "Triệt lông công nghệ Diode", "status": "Hoàn Thành",
                 "price": "800.000"},
            ]
        },
        {
            "id": 11,
            "name": "Bùi Thị Mỹ",
            "gender": "Nữ",
            "age": "25",
            "phone": "0945678901",
            "points": "350 điểm",
            "email": "bui.my@email.com",
            "address": "Liên Chiểu, TP Đà Nẵng",
            "history": [
                {"date": "16/02/2026", "service": "Gội đầu dưỡng sinh", "status": "Hoàn Thành", "price": "300.000"},
            ]
        },
        {
            "id": 12,
            "name": "Đỗ Quỳnh Anh",
            "gender": "Nữ",
            "age": "29",
            "phone": "0956789012",
            "points": "550 điểm",
            "email": "do.anh@email.com",
            "address": "Cẩm Lệ, TP Đà Nẵng",
            "history": [
                {"date": "17/02/2026", "service": "Post-Acne Recovery Therapy", "status": "Hoàn Thành",
                 "price": "1.200.000"},
            ]
        },
        {
            "id": 13,
            "name": "Vũ Thị Hương",
            "gender": "Nữ",
            "age": "27",
            "phone": "0967890123",
            "points": "250 điểm",
            "email": "vu.huong@email.com",
            "address": "Thanh Khê, TP Đà Nẵng",
            "history": [
                {"date": "18/02/2026", "service": "Acne Detox Therapy", "status": "Hoàn Thành", "price": "950.000"},
            ]
        },
        {
            "id": 14,
            "name": "Trịnh Thị Loan",
            "gender": "Nữ",
            "age": "34",
            "phone": "0978901234",
            "points": "150 điểm",
            "email": "trinh.loan@email.com",
            "address": "Hải Châu, TP Đà Nẵng",
            "history": [
                {"date": "08/02/2026", "service": "Gội đầu dưỡng sinh", "status": "Hoàn Thành", "price": "300.000"},
                {"date": "15/02/2026", "service": "Triệt lông công nghệ Diode", "status": "Hoàn Thành",
                 "price": "800.000"},
            ]
        },
        {
            "id": 15,
            "name": "Phan Thị Thảo",
            "gender": "Nữ",
            "age": "28",
            "phone": "0989012345",
            "points": "800 điểm",
            "email": "phan.thao@email.com",
            "address": "Sơn Trà, TP Đà Nẵng",
            "history": [
                {"date": "20/02/2026", "service": "Massage body thư giãn", "status": "Hoàn Thành", "price": "800.000"},
                {"date": "25/02/2026", "service": "Chăm sóc da mặt cao cấp", "status": "Hoàn Thành",
                 "price": "1.000.000"},
            ]
        },
        {
            "id": 16,
            "name": "Nguyễn Thanh Huyền",
            "gender": "Nữ",
            "age": "30",
            "phone": "0990123456",
            "points": "420 điểm",
            "email": "nguyen.huyen@email.com",
            "address": "Liên Chiểu, TP Đà Nẵng",
            "history": [
                {"date": "14/02/2026", "service": "Acne Detox Therapy", "status": "Hoàn Thành", "price": "950.000"},
            ]
        },
        {
            "id": 17,
            "name": "Hoàng Thị Linh",
            "gender": "Nữ",
            "age": "26",
            "phone": "0901111111",
            "points": "680 điểm",
            "email": "hoang.linh@email.com",
            "address": "Cẩm Lệ, TP Đà Nẵng",
            "history": [
                {"date": "13/02/2026", "service": "Gội đầu dưỡng sinh", "status": "Hoàn Thành", "price": "300.000"},
                {"date": "19/02/2026", "service": "Triệt lông công nghệ Diode", "status": "Hoàn Thành",
                 "price": "800.000"},
            ]
        },
        {
            "id": 18,
            "name": "Phạm Minh Châu",
            "gender": "Nữ",
            "age": "32",
            "phone": "0902222222",
            "points": "520 điểm",
            "email": "pham.chau@email.com",
            "address": "Ngũ Hành Sơn, TP Đà Nẵng",
            "history": [
                {"date": "16/02/2026", "service": "Post-Acne Recovery Therapy", "status": "Hoàn Thành",
                 "price": "1.200.000"},
            ]
        },
        {
            "id": 19,
            "name": "Cao Thị Phương",
            "gender": "Nữ",
            "age": "29",
            "phone": "0903333333",
            "points": "380 điểm",
            "email": "cao.phuong@email.com",
            "address": "Thanh Khê, TP Đà Nẵng",
            "history": [
                {"date": "17/02/2026", "service": "Chăm sóc da mặt cao cấp", "status": "Hoàn Thành",
                 "price": "1.000.000"},
            ]
        },
        {
            "id": 20,
            "name": "Lê Minh Hiền",
            "gender": "Nữ",
            "age": "27",
            "phone": "0904444444",
            "points": "620 điểm",
            "email": "le.hien@email.com",
            "address": "Hải Châu, TP Đà Nẵng",
            "history": [
                {"date": "18/02/2026", "service": "Massage body thư giãn", "status": "Hoàn Thành", "price": "800.000"},
                {"date": "22/02/2026", "service": "Acne Detox Therapy", "status": "Hoàn thành", "price": "950.000"},
            ]
        },
    ]
    return render(request, "customer_dashboard.html", {"customers": customers})


@manager_required
def customer_detail(request, customer_id):
    customer = {
        "id": customer_id,
        "name": "Nguyễn Thị Lan",
        "gender": "Nữ",
        "age": "32",
        "email": "nguyenlanvn@gmail.com",
        "address": "Mỹ An, Ngũ Hành Sơn, TP Đà Nẵng",
        "history": [
            {
                "date": "30/01/2026",
                "service": "Chăm sóc da mặt cao cấp",
                "status": "Hoàn Thành",
                "price": "1.000.000",
            },
            {
                "date": "11/02/2026",
                "service": "Post-Acne Recovery Therapy",
                "status": "Hoàn Thành",
                "price": "1.200.000",
            },
        ],
    }
    return render(request, "customer_detail.html", {"customer": customer})


@manager_required
def feedback_dashboard(request):
    names = [
        "Hương Nguyễn", "Luyện Đặng", "Tuyết Sương", "Linh Phương", "Quỳnh Anh",
        "Minh Hoa", "Trúc Nhan", "Thanh Vân", "Khuê Ngôn", "Hà Linh",
        "Tú Anh", "Xuân Hương", "Diệp Chi", "Vân Anh", "Hồng Nhân",
        "Thảo Vy", "Bảo Anh", "Phương Thảo", "Khánh Linh", "Minh Châu",
        "Anh Tuấn", "Bảo Ngân", "Châu Giang", "Đức Minh", "Gia Hân",
        "Hải Yến", "Ích Nhân", "Khắc Quân", "Liêu Phương", "Minh Tú",
        "Ngân Hà", "Oanh Lê", "Phúc Lâm", "Quốc Trung", "Rin Shimizu",
        "Sơn Tùng", "Trâm Anh", "Uyên Thy", "Việt Anh", "Vy Kiều",
        "Thanh Xuân", "Hương Giang", "Mỹ Duyên", "Ngọc Trinh", "Phương Oanh",
        "Quỳnh Như", "Thủy Tiên", "Vy Oanh", "Xuan Hương", "Yến Nhi"
    ]

    services = [
        "Chăm sóc da mặt cao cấp",
        "Massage body thư giãn",
        "Triệt lông công nghệ Diode",
        "Gội đầu dưỡng sinh",
        "Trị mụn chuyên sâu",
        "Acne Detox Therapy",
        "Post-Acne Recovery Therapy",
        "Chăm sóc da mặt Collagen",
        "Triệt lông Laser Diode",
    ]

    contents = [
        "Dịch vụ tuyệt vời! Nhân viên rất chuyên nghiệp và tận tâm. Kết quả vượt mong đợi. Sẽ quay lại nhiều lần nữa!",
        "Rất hài lòng với dịch vụ. Mình cảm thấy thư giãn và thoải mái. Giá cả hợp lý, nhân viên thân thiện.",
        "Liệu trình rất hiệu quả. Sau vài buổi, tôi đã thấy kết quả rõ rệt. Chắc chắn sẽ tiếp tục sử dụng.",
        "Không gian sạch sẽ, thoáng mát. Nhân viên tư vấn kỹ lưỡng. Dịch vụ chất lượng cao, đáng giá tiền.",
        "Trải nghiệm tuyệt vời! Cảm thấy được chăm sóc kỹ lưỡng. Sẽ giới thiệu cho bạn bè.",
        "Khá tốt, tuy nhiên cần cải thiện một chút về thái độ phục vụ.",
        "Dịch vụ bình thường, không có gì nổi bật. Kết quả tạm được.",
        "Không hài lòng với kết quả. Dù giá khá mắc nhưng hiệu quả không như mong đợi.",
        "Nhân viên rất chu đáo. Tôi cảm thấy được chúc mừng bởi sự tử tế của họ.",
        "Liệu trình phục hồi da rất tốt. Làn da tôi sáng mịn hơn rất nhiều.",
    ]

    times = [
        "1 giờ trước", "3 giờ trước", "5 giờ trước", "1 ngày trước", "2 ngày trước",
        "3 ngày trước", "5 ngày trước", "1 tuần trước", "2 tuần trước", "3 tuần trước",
        "1 tháng trước", "1.5 tháng trước", "2 tháng trước", "2.5 tháng trước", "3 tháng trước",
    ]

    avatar_classes = ["avatar-peach", "avatar-neutral", "avatar-rose", "avatar-sun", "avatar-sea"]

    feedbacks = []
    ratings = [5.0, 5.0, 5.0, 5.0, 4.0, 4.0, 4.0, 3.0, 2.0, 1.0]
    statuses = ["Đã phản hồi", "Chưa phản hồi"]

    for i in range(1, 121):
        rating = float(ratings[(i - 1) % len(ratings)])
        status = statuses[(i - 1) % len(statuses)]
        status_class = "green" if status == "Đã phản hồi" else "yellow"

        feedbacks.append({
            "id": i,
            "name": names[(i - 1) % len(names)],
            "time": times[(i - 1) % len(times)],
            "service": services[(i - 1) % len(services)],
            "rating": f"{rating}",
            "status": status,
            "status_class": status_class,
            "content": contents[(i - 1) % len(contents)] + f" (Đánh giá #{i})",
            "avatar_class": avatar_classes[(i - 1) % len(avatar_classes)],
        })

    total_reviews = len(feedbacks)
    star_distribution = {5: 0, 4: 0, 3: 0, 2: 0, 1: 0}
    total_rating = 0

    for feedback in feedbacks:
        rating = int(float(feedback["rating"]))
        star_distribution[rating] += 1
        total_rating += float(feedback["rating"])

    average_rating = round(total_rating / total_reviews, 1) if total_reviews > 0 else 0

    star_stats = []
    for star_num in [5, 4, 3, 2, 1]:
        count = star_distribution[star_num]
        percentage = (count / total_reviews * 100) if total_reviews > 0 else 0
        star_stats.append({
            "star": star_num,
            "count": count,
            "percentage": int(percentage)
        })

    return render(request, "feedback_dashboard.html", {
        "feedbacks": feedbacks,
        "average_rating": average_rating,
        "total_reviews": total_reviews,
        "star_stats": star_stats
    })


def get_consultation_data():
    return {
        1: {
            "id": 1,
            "name": "Mai Hồng Ngọc",
            "avatar_class": "chat-avatar-one",
            "messages": [
                {"side": "left", "text": "Cho em hỏi về dịch vụ bên mình bao nhiêu......", "time": "19:30"},
            ],
        },
        2: {
            "id": 2,
            "name": "Trần Thiên Hà",
            "avatar_class": "avatar-neutral",
            "messages": [
                {"side": "left", "text": "Shop ơi tư vấn này giúp e với .....", "time": "Hôm qua"},
            ],
        },
        3: {
            "id": 3,
            "name": "Ngô Thanh Vân",
            "avatar_class": "chat-avatar-two",
            "messages": [
                {"side": "left", "text": "Shop ơi tư vấn này giúp e với .....", "time": "23/01/2026"},
            ],
        },
        4: {
            "id": 4,
            "name": "Lê Thư Ý",
            "avatar_class": "chat-avatar-three",
            "messages": [
                {"divider": "23/01/2026"},
                {"side": "left", "text": "Shop ơi", "time": ""},
                {
                    "side": "right",
                    "text": "Dạ em chào chị, chị cần bên em tư vấn dịch vụ nào ạ?",
                    "time": "09:03",
                },
                {"side": "left", "text": "Mình muốn đặt lịch gội đầu dưỡng sinh vào cuối tuần này", "time": ""},
                {
                    "side": "right",
                    "text": "Dạ cuối tuần bên em còn slot 15:00 và 17:00, chị chọn giờ nào để em giữ lịch nhé.",
                    "time": "09:05",
                },
            ],
        },
        5: {
            "id": 5,
            "name": "Lê Trà Thư",
            "avatar_class": "avatar-neutral",
            "messages": [
                {"divider": "23/01/2026"},
                {"side": "left", "text": "Ngày mai nha", "time": ""},
                {
                    "side": "right",
                    "text": "Dạ em đã note lịch ngày mai cho chị rồi ạ.",
                    "time": "21:40",
                },
                {"side": "left", "text": "Khoảng 10h chị qua được không em?", "time": ""},
                {
                    "side": "right",
                    "text": "Dạ được chị nha, em giữ lịch 10:00 và sẽ nhắn xác nhận trước 30 phút ạ.",
                    "time": "21:42",
                },
            ],
        },
    }


@manager_required
def consultation_dashboard(request):
    # Lấy toàn bộ phòng chat, sắp xếp cái nào mới nhắn lên đầu
    rooms = ChatRoom.objects.all().order_by('-updated_at')

    conversations = []
    for room in rooms:
        # Lấy tin nhắn cuối cùng của phòng này
        last_msg = Message.objects.filter(chat_room=room).order_by('-timestamp').first()

        conversations.append({
            'id': room.id,
            'name': room.customer.get_full_name() if room.customer.get_full_name() else room.customer.username,
            'last_message': last_msg.content if last_msg else "Chưa có tin nhắn",
            'time': last_msg.timestamp.strftime('%H:%M') if last_msg else "",
            'avatar_class': "avatar-default"  # Hoặc logic avatar của bạn
        })

    return render(request, 'consultation_dashboard.html', {'conversations': conversations})


@manager_required
def consultation_detail(request, room_id):
    # 1. Lấy đúng phòng chat từ ID trên thanh địa chỉ
    room = get_object_or_404(ChatRoom, id=room_id)

    # 2. Lấy lịch sử tin nhắn thật để không bị trống khi vừa vào
    messages = Message.objects.filter(chat_room=room).order_by('timestamp')

    msg_list = []
    for m in messages:
        msg_list.append({
            'text': m.content,
            'side': 'right' if m.sender == request.user else 'left',
            'time': m.timestamp.strftime('%H:%M')
        })

    context = {
        'room_id': room.id,
        'conversation': {
            'name': room.customer.get_full_name() or room.customer.username,
            'avatar_class': "avatar-default",
            'messages': msg_list  # Trả dữ liệu thật ở đây để HTML vẽ ra ngay
        }
    }
    return render(request, 'consultation_detail.html', context)


@manager_required
def feedback_detail(request, feedback_id):
    feedback = {
        "id": feedback_id,
        "name": "Luyện Đặng",
        "date": "19/01/2025",
        "service": "Massage body thư giãn",
        "rating": "4.0",
        "status": "Chưa phản hồi",
        "content": "Dịch vụ massage rất tốt! Nhân viên massage chuyên nghiệp, lực tay vừa phải. Tinh dầu thơm nhẹ nhàng không gây kích ứng. Sau 60 phút massage, cơ thể mình thư giãn hẳn, giảm đau mỏi vai gáy rất nhiều. Giá cả hợp lý, spa sạch sẽ thoáng mát.",
    }
    return render(request, "feedback_detail.html", {"feedback": feedback})


@customer_required
def customer_account(request):
    profile, _ = CustomerProfile.objects.get_or_create(
        user=request.user,
        defaults={
            "full_name": f"{request.user.last_name} {request.user.first_name}".strip() or request.user.username,
            "member_since": request.user.date_joined.date(),
            "loyalty_points": 320,
            "avatar_url": "https://images.unsplash.com/photo-1494790108377-be9c29b29330?auto=format&fit=crop&w=400&q=80",
        },
    )

    if request.method == "POST":
        form = CustomerProfileForm(request.POST, instance=profile)
        if form.is_valid():
            profile = form.save()
            request.user.first_name = form.cleaned_data["full_name"]
            request.user.save(update_fields=["first_name"])
            messages.success(request, "Thông tin tài khoản đã được cập nhật.")
            return redirect("customer_account")
    else:
        form = CustomerProfileForm(instance=profile)

    context = {
        "form": form,
        "profile": profile,
        "history_items": build_customer_history(request.user),
        "active_tab": request.GET.get("tab", "profile"),
        "member_since_label": profile.member_since.strftime("%m/%Y") if profile.member_since else "",
        "display_name": profile.display_name,
    }
    return render(request, "customer_account.html", context)

def build_booking_calendar(days=31):
    today = date.today()
    end_date = today + timedelta(days=days - 1)
    first_day = today.replace(day=1)

    items = []
    for _ in range(first_day.weekday()):
        items.append({"blank": True})

    current = first_day
    while current <= end_date:
        is_in_range = today <= current <= end_date
        items.append(
            {
                "blank": False,
                "day": current.day,
                "iso": current.isoformat(),
                "disabled": not is_in_range,
                "is_today": current == today,
            }
        )
        current += timedelta(days=1)

    return items


@customer_required
def booking_page(request):
    if request.method == "POST":
        service_id = request.POST.get("service_id")
        booking_date_raw = request.POST.get("booking_date")
        booking_time_raw = request.POST.get("booking_time")

        try:
            service = Service.objects.get(id=service_id, status="Hoạt động")
            booking_date = datetime.strptime(booking_date_raw, "%Y-%m-%d").date()
            booking_time = datetime.strptime(booking_time_raw, "%H:%M").time()
        except Exception:
            messages.error(request, "Thông tin đặt lịch không hợp lệ.")
            return redirect("booking")

        with transaction.atomic():
            exists = Booking.objects.filter(
                booking_date=booking_date,
                booking_time=booking_time
            ).exclude(status="Đã Hủy").exists()

            if exists:
                messages.error(request, "Khung giờ đã được đặt.")
                return redirect("booking")

            Booking.objects.create(
                customer=request.user,
                service=service,
                booking_date=booking_date,
                booking_time=booking_time,
            )

        messages.success(request, "Đặt lịch thành công.")
        return redirect("/tai-khoan/?tab=history")

    services = Service.objects.filter(status=Service.STATUS_ACTIVE)

    return render(request, "booking.html", {
        "services": services,
        "calendar_days": build_booking_calendar(),
    })
@customer_required
def cancel_booking(request, booking_id):
    if request.method != "POST":
        return redirect("/tai-khoan/?tab=history")

    sync_completed_bookings(request.user)
    booking = get_object_or_404(Booking, id=booking_id, customer=request.user)
    if not can_cancel_booking(booking):
        messages.error(request, "Lịch này đã quá thời hạn hủy hoặc không thể hủy.")
        return redirect("/tai-khoan/?tab=history")

    booking.status = "Đã Hủy"
    booking.save(update_fields=["status"])

    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        "booking_slots",
        {
            "type": "slots_update",
            "date": booking.booking_date.isoformat(),
            "slots": get_booked_slots_for_date(booking.booking_date),
        },
    )
    messages.success(request, "Đã hủy lịch đặt.")
    return redirect("/tai-khoan/?tab=history")


def build_booking_calendar(days=31):
    today = date.today()
    end_date = today + timedelta(days=days - 1)
    first_day = today.replace(day=1)
    items = []
    for _ in range(first_day.weekday()):
        items.append({"blank": True})

    current = first_day
    while current <= end_date:
        is_in_range = today <= current <= end_date
        items.append(
            {
                "blank": False,
                "day": current.day,
                "iso": current.isoformat(),
                "disabled": not is_in_range,
                "is_today": current == today,
            }
        )
        current += timedelta(days=1)
    return items


def get_booked_slots_for_date(selected_date):
    return sorted({
        booking.booking_time.strftime("%H:%M")
        for booking in Booking.objects.filter(booking_date=selected_date).exclude(status="Đã Hủy")
    })


@customer_required
def booking_page(request):
    if request.method == "POST":
        service_id = request.POST.get("service_id")
        booking_date_raw = request.POST.get("booking_date")
        booking_time_raw = request.POST.get("booking_time")
        package_name = request.POST.get("package_name", "").strip()
        sessions = request.POST.get("sessions", "").strip()
        package_description = request.POST.get("package_description", "").strip()
        total_price_raw = request.POST.get("total_price", "0")
        notes = request.POST.get("notes", "").strip()

        try:
            service = Service.objects.get(id=service_id, status=Service.STATUS_ACTIVE)
            booking_date = datetime.strptime(booking_date_raw, "%Y-%m-%d").date()
            booking_time = datetime.strptime(booking_time_raw, "%H:%M").time()
            today = date.today()
            if booking_date < today or booking_date > today + timedelta(days=30):
                raise ValueError
            if booking_time.strftime("%H:%M") not in BOOKING_TIME_SLOTS:
                raise ValueError
            total_price = int("".join(ch for ch in total_price_raw if ch.isdigit()) or service.price)
        except (Service.DoesNotExist, TypeError, ValueError):
            messages.error(request, "Thông tin đặt lịch chưa hợp lệ. Vui lòng kiểm tra lại.")
            return redirect("booking")

        with transaction.atomic():
            is_taken = Booking.objects.select_for_update().filter(
                booking_date=booking_date,
                booking_time=booking_time,
            ).exclude(status="Đã Hủy").exists()
            if is_taken:
                messages.error(request, "Khung giờ này vừa được đặt. Vui lòng chọn khung giờ khác.")
                return redirect("booking")

            Booking.objects.create(
                customer=request.user,
                service=service,
                package_name=package_name,
                sessions=sessions,
                package_description=package_description,
                booking_date=booking_date,
                booking_time=booking_time,
                total_price=total_price,
                notes=notes,
            )
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                "booking_slots",
                {
                    "type": "slots_update",
                    "date": booking_date.isoformat(),
                    "slots": get_booked_slots_for_date(booking_date),
                },
            )
        messages.success(request, "Đặt lịch thành công.")
        return redirect("/tai-khoan/?tab=history")

    db_services = Service.objects.filter(status=Service.STATUS_ACTIVE)
    services = [
        {
            "id": service.id,
            "name": service.name,
            "description": service.short_description,
            "duration": f"{service.duration_minutes} phút",
            "rating": service.rating,
            "price": f"{format_service_price(service.price)}đ",
            "raw_price": service.price,
            "tone": service.category,
            "category": service.category,
            "image_url": service.image_url or "/static/images/service-fallback.svg",
        }
        for service in db_services

    ]

    package_catalog = [
        {
            "id": "basic",
            "name": "Gói cơ bản",
            "sessions": "1-2",
            "price": "Từ 800.000đ",
            "multiplier": 1,
            "result": "Phù hợp cho lần đầu trải nghiệm",
            "benefits": [
                "Làm sạch cơ bản",
                "Massage mặt thư giãn",
                "Đắp mặt nạ Collagen",
                "Dưỡng ẩm nhanh",
            ],
        },
        {
            "id": "standard",
            "name": "Gói tiêu chuẩn",
            "sessions": "3-5",
            "price": "Từ 1.300.000đ",
            "multiplier": 1.35,
            "result": "Hiệu quả rõ rệt sau 1 tháng",
            "benefits": [
                "Tất cả quyền lợi gói cơ bản",
                "Tẩy tế bào chết chuyên sâu",
                "Serum dưỡng da cao cấp",
                "Tư vấn chăm sóc tại nhà",
            ],
        },
        {
            "id": "advanced",
            "name": "Gói cao cấp",
            "sessions": "8-10",
            "price": "Từ 2.500.000đ",
            "multiplier": 2.1,
            "result": "Chăm sóc toàn diện, hiệu quả lâu dài",
            "benefits": [
                "Tất cả quyền lợi gói tiêu chuẩn",
                "Công nghệ điều trị tiên tiến",
                "Massage vai gáy miễn phí",
                "Tặng bộ skincare mini",
            ],
        },
        {
            "id": "vip",
            "name": "Gói VIP",
            "sessions": "12-15",
            "price": "Từ 3.500.000đ",
            "multiplier": 3,
            "result": "Trải nghiệm đẳng cấp 5 sao",
            "benefits": [
                "Tất cả quyền lợi gói cao cấp",
                "Phòng riêng VIP sang trọng",
                "Thủ công nghệ trị liệu mới",
                "Tặng bộ skincare cao cấp",
            ],
            "theme": "vip",
        },
    ]

    packages_by_service = {}
    for service in services:
        packages = []
        for item in package_catalog:
            package = dict(item)
            price = int(service["raw_price"] * package.pop("multiplier"))
            package["price"] = f"{format_service_price(price)}đ"
            package["price_value"] = price
            package["benefits"] = [
                text.replace("dịch vụ", service["name"].lower()) for text in package["benefits"]
            ]
            packages.append(package)
        packages_by_service[str(service["id"])] = packages

    booked_slots = Booking.objects.filter(
        booking_date__gte=date.today(),
        booking_date__lte=date.today() + timedelta(days=30),
    ).exclude(status="Đã Hủy")
    time_slots_by_date = {}
    for booking in booked_slots:
        day_key = booking.booking_date.isoformat()
        time_slots_by_date.setdefault(day_key, set()).add(booking.booking_time.strftime("%H:%M"))
    time_slots_by_date = {
        day_key: sorted(slots)
        for day_key, slots in time_slots_by_date.items()
    }
    profile, _ = CustomerProfile.objects.get_or_create(user=request.user)

    context = {
        "services": services,
        "packages_by_service": packages_by_service,
        "time_slots_by_date": time_slots_by_date,
        "booking_time_slots": BOOKING_TIME_SLOTS,
        "calendar_days": build_booking_calendar(),
        "current_month_label": date.today().strftime("%m/%Y"),
        "customer_info": {
            "full_name": profile.display_name,
            "phone": profile.phone or "Chưa cập nhật",
            "email": request.user.email,
        },
        "history": build_customer_history(),
        "history": build_customer_history(request.user),
    }
    return render(request, "booking.html", context)


@customer_required
def booking_slots(request):
    day = request.GET.get("date", "")
    try:
        selected_date = datetime.strptime(day, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return JsonResponse({"booked_slots": [], "slots": BOOKING_TIME_SLOTS}, status=400)

    today = date.today()
    if selected_date < today or selected_date > today + timedelta(days=30):
        return JsonResponse({"booked_slots": [], "slots": BOOKING_TIME_SLOTS}, status=400)

    return JsonResponse({"booked_slots": get_booked_slots_for_date(selected_date), "slots": BOOKING_TIME_SLOTS})

def see_service(request):
    services = [
        serialize_service(service)
        # Thay thế bằng "Hoạt động"
        for service in Service.objects.filter(status=Service.STATUS_ACTIVE)
    ]
    return render(request, "see_service.html", {"services": services})

def service_detail(request, slug):
    service = get_object_or_404(Service, slug=slug, status="Hoạt động")
    related_services = [
        serialize_service(item)

        for item in Service.objects.filter(status="Hoạt động", category=service.category)
        .exclude(pk=service.pk)[:3]
    ]
    return render(
        request,
        "service_detail.html",
        {
            "service": serialize_service(service),
            "related_services": related_services,
        },
    )


@customer_required
def customer_consultation_page(request):
    faq_items = [
        {
            "question": "Da nhạy cảm có thể làm liệu trình massage hoặc chăm sóc mặt không?",
            "answer": "Chúng tôi có các liệu trình dành riêng cho da nhạy cảm, sử dụng sản phẩm dịu nhẹ và nhân viên sẽ tư vấn kỹ trước khi thực hiện.",
        },
        {
            "question": "Liệu trình làm trắng da có an toàn cho da nhạy cảm không?",
            "answer": "Spa sử dụng sản phẩm chuyên dụng cho da nhạy cảm và kỹ thuật viên được đào tạo để giảm nguy cơ kích ứng trước khi tiến hành toàn bộ liệu trình.",
        },
        {
            "question": "Sau khi lăn kim hoặc peel da, tôi cần bao lâu để da phục hồi hoàn toàn?",
            "answer": "Thời gian phục hồi tùy vào cơ địa và độ sâu của liệu trình, thường từ 3 đến 7 ngày nếu chăm sóc đúng cách tại nhà.",
        },
    ]

    chat_room, created = ChatRoom.objects.get_or_create(
        customer=request.user,
        defaults={'manager': User.objects.filter(is_staff=True).first()}
    )

    messages = Message.objects.filter(chat_room=chat_room).order_by('timestamp')
    chat_messages = []
    for msg in messages:
        side = 'right' if msg.sender == request.user else 'left'
        chat_messages.append({
            'side': side,
            'text': msg.content,
            'time': msg.timestamp.strftime('%H:%M'),
        })

    return render(
        request,
        "customer_consultation.html",
        {
            "faq_items": faq_items,
            "chat_messages": chat_messages,
            "room_id": chat_room.id,
        },
    )


def about_page(request):
    return render(request, 'about.html')


def get_public_reviews():
    reviews = []
    names = ["Nguyễn Hà My", "Trần Khánh Linh", "Lê Bảo Ngọc", "Phạm Thu Hằng", "Võ Minh Anh", "Đoàn Ngọc Thảo",
             "Trâm Anh", "Lan Ngọc", "Kim Chi", "Thanh Trúc"]
    services = ["Chăm sóc da mặt", "Massage body", "Triệt lông vĩnh viễn", "Điều trị mụn"]

    for i in range(1, 121):
        r_type = "service" if i % 3 != 0 else "shop"
        rating = random.choices([5, 4, 3, 2, 1], weights=[80, 15, 3, 1, 1])[0]

        images = []
        if i in [1, 5, 12]:
            images = ["https://images.unsplash.com/photo-1515377905703-c4788e51af15?auto=format&fit=crop&w=200&q=80"]

        reviews.append({
            "id": i,
            "name": f"{random.choice(names)} {i}",
            "time": f"{random.randint(1, 20)} ngày trước",
            "rating": rating,
            "service": random.choice(services) if r_type == "service" else "Đánh giá không gian Spa",
            "content": f"Khách hàng số {i}: Dịch vụ rất tuyệt vời, nhân viên nhiệt tình chu đáo. Spa làm việc rất chuyên nghiệp, 10 điểm không có nhưng!",
            "image_urls": images,
            "review_type": r_type
        })
    return reviews


def public_review_page(request):
    reviews = Review.objects.all().order_by('-time')
    total_reviews = reviews.count()

    stars = {5: 0, 4: 0, 3: 0, 2: 0, 1: 0}
    total_stars_points = 0
    with_images = 0

    for r in reviews:
        total_stars_points += r.rating
        if r.rating in stars:
            stars[r.rating] += 1
        if r.image_urls:
            with_images += 1

    average_rating = round(total_stars_points / total_reviews, 1) if total_reviews > 0 else 0

    def get_percent(count):
        return round((count / total_reviews) * 100) if total_reviews > 0 else 0

    context = {
        "reviews": reviews,
        "highlighted_reviews": reviews[:3],
        "average_rating": average_rating,
        "total_reviews": total_reviews,
        "with_images": with_images,
        "star_5_count": stars[5], "star_5_percent": get_percent(stars[5]),
        "star_4_count": stars[4], "star_4_percent": get_percent(stars[4]),
        "star_3_count": stars[3], "star_3_percent": get_percent(stars[3]),
        "star_2_count": stars[2], "star_2_percent": get_percent(stars[2]),
        "star_1_count": stars[1], "star_1_percent": get_percent(stars[1]),
    }
    return render(request, "public_reviews.html", context)


@require_POST
def api_update_price(request):
    try:
        service_id = request.POST.get('service_id')
        new_price_str = request.POST.get('new_price', '0')

        if not new_price_str.strip(): new_price_str = '0'
        new_price = int(new_price_str.replace('.', '').replace(',', '').strip())

        service = get_object_or_404(Service, id=service_id)
        service.price = new_price

        # LƯU DATABASE THẬT
        service.save()

        formatted_price = f"{new_price:,}".replace(',', '.')
        return JsonResponse({
            'status': 'success',
            'new_price_formatted': formatted_price,
            'raw_price': new_price
        })
    except Exception as e:
        print("=== LỖI UPDATE GIÁ ===", repr(e))
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)

@require_POST
def api_update_service(request):
    try:
        service_id = request.POST.get('service_id')
        name = request.POST.get('name')
        description = request.POST.get('description')
        image = request.FILES.get('image')

        service = get_object_or_404(Service, id=service_id)
        if name:
            service.name = name
        if description:
            service.description = description
            service.short_description = description[:100] + '...' if len(description)>100 else description

        if image:
            service.image_url = image

        # ĐÂY LÀ DÒNG CHỐNG LỖI F5 BỊ MẤT DỮ LIỆU
        service.save()

        return JsonResponse({
            'status': 'success',
            'new_name': service.name,
            'new_desc': service.description,
        })
    except Exception as e:
        print("=== LỖI UPDATE INFO ===", repr(e))
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)

@require_POST
def api_create_service(request):
    try:
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        price_str = request.POST.get('price', '0')
        image = request.FILES.get('image')

        if not name:
            raise ValueError("Tên dịch vụ không được để trống")

        if not price_str.strip(): price_str = '0'
        new_price = int(price_str.replace('.', '').replace(',', '').strip())

        base_slug = slugify(name) or "dich-vu"
        safe_slug = f"{base_slug}-{uuid.uuid4().hex[:6]}"

        # Trích xuất 100 ký tự đầu làm mô tả ngắn
        short_desc = description[:100] + '...' if len(description) > 100 else description

        # TẠO VÀ LƯU DATABASE THẬT
        service = Service(
            name=name,
            slug=safe_slug,
            short_description=short_desc,
            description=description,
            price=new_price,
            category=getattr(Service, 'CATEGORY_FACE', 'face'),
            duration_minutes=60,
            status=getattr(Service, 'STATUS_ACTIVE', 'active'),
            rating=5.0
        )

        # FIX LỖI 400 KHI KHÔNG UPLOAD ẢNH:
        if image:
            service.image_url = image
            # Nếu model của bạn bắt buộc có ảnh, có thể thêm else: service.image_url = "default.jpg"

        service.save()
        return JsonResponse({'status': 'success'})

    except Exception as e:
        print("=== LỖI CREATE ===", repr(e))
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)


@require_GET
def api_get_chat_messages(request, room_id):
    try:
        room = get_object_or_404(ChatRoom, id=room_id)
        messages = Message.objects.filter(chat_room=room).order_by('timestamp')
        data = [{'id': m.id, 'content': m.content, 'sender_id': m.sender.id, 'time': m.timestamp.strftime('%H:%M')} for
                m in messages]
        return JsonResponse({'status': 'success', 'messages': data})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)


@require_POST
def api_send_chat_message(request):
    try:
        room_id = request.POST.get('room_id')
        content = request.POST.get('content', '').strip()
        if not content: return JsonResponse({'status': 'error', 'message': 'Trống'}, status=400)

        room = get_object_or_404(ChatRoom, id=room_id)
        msg = Message.objects.create(chat_room=room, sender=request.user, content=content)
        room.updated_at = timezone.now()
        room.save()
        return JsonResponse({'status': 'success', 'time': msg.timestamp.strftime('%H:%M')})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)


def consultation_dashboard(request):
    # Sắp xếp phòng chat có tin nhắn mới nhất lên đầu
    rooms = ChatRoom.objects.all().order_by('-updated_at')
    conversations = []

    for room in rooms:
        last_msg = Message.objects.filter(chat_room=room).order_by('-timestamp').first()

        # MẶC ĐỊNH: Nếu tin nhắn cuối cùng là của khách thì tính là tin mới (in đậm)
        is_new = False
        if last_msg and last_msg.sender != request.user:
            is_new = True

        conversations.append({
            'id': room.id,
            'name': room.customer.get_full_name() or room.customer.username,
            'last_message': last_msg.content if last_msg else "Chưa có tin nhắn",
            'time': last_msg.timestamp.strftime('%H:%M') if last_msg else "",
            'is_new': is_new,  # Gửi biến này sang HTML
            'avatar_class': "avatar-default"
        })
    return render(request, 'consultation_dashboard.html', {'conversations': conversations})