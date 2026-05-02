from django.contrib import admin
from django.urls import path
from services.views import public_reviews
from . import views

from .views import (
    home_entry,
    user_login,
    user_register,
    user_logout,
    appointment_dashboard,
    consultation_dashboard,
    consultation_detail,
    customer_dashboard,
    customer_detail,
    customer_account,
    cancel_booking,
    booking_page,
    booking_slots,
    feedback_dashboard,
    feedback_detail,
    service_dashboard,
    see_service,
    service_detail,
    customer_consultation_page,
    about_page,
    # Xóa chữ public_review_page ở đây rồi nha
)

urlpatterns = [
    path('', home_entry, name='home_entry'),
    path('login/', user_login, name='user_login'),
    path('register/', user_register, name='user_register'),
    path('logout/', user_logout, name='user_logout'),
    path('quan-ly/dich-vu/', service_dashboard, name='service_dashboard'),
    path('quan-ly/lich-hen/', appointment_dashboard, name='appointment_dashboard'),
    path('quan-ly/phan-hoi/', feedback_dashboard, name='feedback_dashboard'),
    path('quan-ly/phan-hoi/tu-van/', views.consultation_dashboard, name='consultation_dashboard'),
    path('quan-ly/phan-hoi/tu-van/<int:room_id>/', views.consultation_detail, name='consultation_detail'),
    path('quan-ly/phan-hoi/<int:feedback_id>/', feedback_detail, name='feedback_detail'),
    path('quan-ly/khach-hang/', customer_dashboard, name='customer_dashboard'),
    path('quan-ly/khach-hang/<int:customer_id>/', customer_detail, name='customer_detail'),
    path('tai-khoan/', customer_account, name='customer_account'),
    path('tai-khoan/huy-lich/<int:booking_id>/', cancel_booking, name='cancel_booking'),
    path('dat-lich/', booking_page, name='booking'),
    path('dat-lich/khung-gio/', booking_slots, name='booking_slots'),
    path('tu-van/', customer_consultation_page, name='customer_consultation'),
    path('services/', see_service, name='see_service'),
    path('services/<slug:slug>/', service_detail, name='service_detail'),
    path('gioi-thieu/', about_page, name='about_page'),
    
    # CHỖ NÀY ĐÃ TRỎ VÀO HÀM MỚI CỦA TAO
    path('danh-gia/', public_reviews, name='public_review_page'),

    # Legacy aliases kept for older templates/scripts.
    path('lich-hen/', appointment_dashboard),
    path('phan-hoi/', feedback_dashboard),
    path('phan-hoi/tu-van/', consultation_dashboard),
    path('phan-hoi/tu-van/<int:room_id>/', consultation_detail),
    path('phan-hoi/<int:feedback_id>/', feedback_detail),
    path('khach-hang/', customer_dashboard),
    path('khach-hang/<int:customer_id>/', customer_detail),

    path('admin/', admin.site.urls),
path('api/services/create/', views.api_create_service, name='api_create_service'),
    path('api/services/update/', views.api_update_service, name='api_update_service'),
    path('api/services/update-price/', views.api_update_price, name='api_update_price'),
path('api/chat/<int:room_id>/get/', views.api_get_chat_messages, name='api_get_chat'),
    path('api/chat/send/', views.api_send_chat_message, name='api_send_chat'),
]
