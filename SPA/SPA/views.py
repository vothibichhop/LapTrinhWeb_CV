import json
import random
import uuid
from django.views.decorators.http import require_GET, require_POST
from datetime import date, datetime, timedelta
from django.utils.text import slugify


from django.shortcuts import get_object_or_404, redirect, render
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth.models import User
from django.db.models import Count, Avg
from services.models import Service, Booking, CustomerProfile, ChatRoom, Message, Review

from .forms import CustomerProfileForm, LoginForm, RegisterForm
from django.db import models, transaction
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

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


MANAGER_STATUS_META = {
    "Đang Xử Lý": ("Đang tiến hành", "green"),
    "Hoàn Thành": ("Hoàn thành", "blue"),
    "Đã Hủy": ("Đã hủy", "red"),
}


def get_manager_status_choices():
    return [
        {"value": "Đang Xử Lý", "label": "Đang tiến hành"},
        {"value": "Hoàn Thành", "label": "Hoàn thành"},
        {"value": "Đã Hủy", "label": "Đã hủy"},
    ]


def get_manager_status_meta(status):
    return MANAGER_STATUS_META.get(status, (status, "green"))


def build_manager_appointment_item(booking):
    profile = getattr(booking.customer, "customer_profile", None)
    customer_name = profile.display_name if profile else (
        f"{booking.customer.last_name} {booking.customer.first_name}".strip() or booking.customer.username
    )
    phone = profile.phone if profile and profile.phone else "Chưa cập nhật"
    birth_date = profile.birth_date.strftime("%d/%m/%Y") if profile and profile.birth_date else "Chưa cập nhật"
    address = profile.address if profile and profile.address else "Chưa cập nhật"
    customer_notes = profile.notes if profile and profile.notes else "Không có ghi chú"
    status_label, status_class = get_manager_status_meta(booking.status)

    return {
        "id": booking.id,
        "customer": customer_name,
        "phone": phone,
        "email": booking.customer.email or "Chưa cập nhật",
        "birth_date": birth_date,
        "address": address,
        "service": booking.service.name if booking.service else "Dịch vụ",
        "package": booking.package_name,
        "sessions": booking.sessions,
        "package_description": booking.package_description or "Không có mô tả gói",
        "date": booking.booking_date.strftime("%d/%m/%Y"),
        "time": booking.booking_time.strftime("%H:%M"),
        "price": f"{format_service_price(booking.total_price)}đ",
        "status": status_label,
        "raw_status": booking.status,
        "status_class": status_class,
        "note": booking.notes or "Không có ghi chú đặt lịch",
        "customer_notes": customer_notes,
        "update_url": reverse("api_update_booking_status", args=[booking.id]),
    }


def calculate_booking_points(user):
    completed_total = Booking.objects.filter(customer=user, status="Hoàn Thành").aggregate(
        total=models.Sum("total_price")
    )["total"] or 0
    return completed_total // 10000


def sync_customer_points(user):
    profile, _ = CustomerProfile.objects.get_or_create(
        user=user,
        defaults={
            "full_name": f"{user.last_name} {user.first_name}".strip() or user.username,
            "member_since": user.date_joined.date(),
        },
    )
    earned_points = calculate_booking_points(user)
    if earned_points > profile.loyalty_points:
        profile.loyalty_points = earned_points
        profile.save(update_fields=["loyalty_points"])
    return profile


def build_manager_customer_history(user):
    sync_completed_bookings(user)
    history = []
    for booking in Booking.objects.filter(customer=user).select_related("service").order_by("-booking_date", "-booking_time"):
        status_label, _ = get_manager_status_meta(booking.status)
        history.append({
            "date": booking.booking_date.strftime("%d/%m/%Y"),
            "service": booking.service.name if booking.service else "Dịch vụ",
            "status": status_label,
            "price": f"{format_service_price(booking.total_price)}đ",
        })
    return history


def build_manager_customer_item(user):
    profile = sync_customer_points(user)
    history = build_manager_customer_history(user)
    today = date.today()
    age = "Chưa cập nhật"
    if profile.birth_date:
        age = today.year - profile.birth_date.year - (
            (today.month, today.day) < (profile.birth_date.month, profile.birth_date.day)
        )
    name = profile.display_name
    return {
        "id": user.id,
        "name": name,
        "gender": "Chưa cập nhật",
        "age": age,
        "phone": profile.phone or "Chưa cập nhật",
        "points": f"{profile.loyalty_points} điểm",
        "raw_points": profile.loyalty_points,
        "email": user.email or "Chưa cập nhật",
        "address": profile.address or "Chưa cập nhật",
        "history": history,
        "history_json": json.dumps(history, ensure_ascii=False),
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
    sync_completed_bookings()
    appointments = [
        build_manager_appointment_item(booking)
        for booking in Booking.objects.select_related("customer", "service", "customer__customer_profile")
        .order_by("-booking_date", "-booking_time", "-created_at")
    ]
    modal_state = request.GET.get("modal", "")
    return render(
        request,
        "appointment_dashboard.html",
        {
            "appointments": appointments,
            "modal_state": modal_state,
            "status_choices": get_manager_status_choices(),
        },
    )

@manager_required
def customer_dashboard(request):
    sync_completed_bookings()
    users = User.objects.filter(is_staff=False).select_related("customer_profile").order_by("username")
    customers = [build_manager_customer_item(user) for user in users]
    return render(request, "customer_dashboard.html", {"customers": customers})

@manager_required
def customer_detail(request, customer_id):
    user = get_object_or_404(User, id=customer_id, is_staff=False)
    customer = build_manager_customer_item(user)
    return render(request, "customer_detail.html", {"customer": customer})

@manager_required
def feedback_dashboard(request):
    # LẤY DỮ LIỆU ĐÁNH GIÁ THẬT TỪ DATABASE
    reviews = Review.objects.all().order_by('-time')
    total_reviews = reviews.count()
    average_rating = 0.0
    if total_reviews > 0:
        avg = reviews.aggregate(Avg('rating'))['rating__avg']
        average_rating = round(avg, 1) if avg else 0.0
    stars = {5: 0, 4: 0, 3: 0, 2: 0, 1: 0}
    star_counts = reviews.values('rating').annotate(count=Count('rating'))
    for item in star_counts:
        if item['rating'] in stars:
            stars[item['rating']] = item['count']
    def get_percent(count, total):
        return round((count / total) * 100) if total > 0 else 0
    star_stats = []
    for star_num in [5, 4, 3, 2, 1]:
        count = stars[star_num]
        percentage = get_percent(count, total_reviews)
        star_stats.append({
            "star": star_num,
            "count": count,
            "percentage": percentage
        })
    return render(request, "feedback_dashboard.html", {
        "feedbacks": reviews,
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
            'is_new': bool(last_msg and last_msg.sender != request.user),
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
    sync_completed_bookings(request.user)
    profile = sync_customer_points(request.user)

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
@manager_required
def api_update_booking_status(request, booking_id):
    sync_completed_bookings()
    booking = get_object_or_404(Booking, id=booking_id)
    new_status = request.POST.get("status", "").strip()
    valid_statuses = {choice["value"] for choice in get_manager_status_choices()}

    if new_status not in valid_statuses:
        return JsonResponse({"status": "error", "message": "Trạng thái không hợp lệ."}, status=400)

    booking.status = new_status
    booking.save(update_fields=["status"])
    sync_completed_bookings()
    booking.refresh_from_db(fields=["status"])
    status_label, status_class = get_manager_status_meta(booking.status)

    return JsonResponse({
        "status": "success",
        "booking_status": booking.status,
        "status_label": status_label,
        "status_class": status_class,
    })


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
