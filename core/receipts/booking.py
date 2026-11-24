"""Booking confirmation email sent after a successful appointment commit."""
import logging
from datetime import datetime
from functools import partial

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from core.models import LichKham, ThongBao
from .delivery import sandbox_connection

logger = logging.getLogger(__name__)


def schedule_booking_confirmation(appointment, recipient):
    """Queue a best-effort confirmation; email failure never rolls back booking."""
    try:
        validate_email((recipient or '').strip())
    except ValidationError:
        logger.info('Booking confirmation skipped: no valid account email; appointment_id=%s.', appointment.pk)
        return

    transaction.on_commit(
        partial(_send_booking_confirmation_safely, appointment.pk, recipient.strip()),
        robust=True,
    )


def notify_appointment_change(appointment, *, cancelled=False):
    """Persist an in-app notice and queue a best-effort email after the caller commits."""
    patient = appointment.benh_nhan
    if patient.user_id:
        title = 'Lịch khám đã được hủy' if cancelled else 'Lịch khám đã được thay đổi'
        body = (f'Lịch khám #{appointment.pk} tại {appointment.bac_si.chi_nhanh.ten} đã được hủy.'
                if cancelled else
                f'Lịch khám #{appointment.pk} được chuyển sang {appointment.gio_kham:%H:%M}, '
                f'ngày {appointment.ngay_kham:%d/%m/%Y}, bác sĩ {appointment.bac_si.ho_ten}.')
        ThongBao.objects.create(
            nguoi_nhan=patient.user, loai='lich_kham', tieu_de=title, noi_dung=body,
            lien_ket='/lich-kham-sap-toi/',
        )
    recipient = (patient.user.email or '').strip() if patient.user_id else ''
    if recipient:
        try:
            validate_email(recipient)
        except ValidationError:
            return
        context = {
            'patient': patient.ho_ten,
            'booking_id': appointment.pk,
            'branch': appointment.bac_si.chi_nhanh.ten,
            'doctor': appointment.bac_si.ho_ten,
            'appointment_at': f'{appointment.gio_kham:%H:%M}, {appointment.ngay_kham:%d/%m/%Y}',
            'cancelled': cancelled,
        }
        transaction.on_commit(
            partial(_send_appointment_change_safely, context, recipient), robust=True,
        )


def _send_appointment_change_safely(context, recipient):
    try:
        cancelled = context['cancelled']
        text_body = render_to_string('core/receipts/appointment_changed_email.txt', context)
        html_body = render_to_string('core/receipts/appointment_changed_email.html', context)
        connection = sandbox_connection()
        message = EmailMultiAlternatives(
            subject=('EyeViora thông báo hủy lịch khám' if cancelled else 'EyeViora thông báo thay đổi lịch khám'),
            body=text_body, from_email=settings.DEFAULT_FROM_EMAIL, to=[recipient], connection=connection,
        )
        message.attach_alternative(html_body, 'text/html')
        message.send(fail_silently=False)
    except Exception:
        logger.error('Appointment update email failed; appointment_id=%s.', context.get('booking_id'))


def _send_booking_confirmation_safely(appointment_id, recipient):
    try:
        appointment = (
            LichKham.objects.select_related('benh_nhan', 'bac_si', 'bac_si__chi_nhanh')
            .prefetch_related('dich_vu_dat_truoc_nhieu')
            .get(pk=appointment_id)
        )
        services = list(appointment.dich_vu_dat_truoc_nhieu.all())
        if not services and appointment.dich_vu_dat_truoc_id:
            services = [appointment.dich_vu_dat_truoc]

        local_start = timezone.make_aware(
            datetime.combine(appointment.ngay_kham, appointment.gio_kham),
            timezone.get_current_timezone(),
        )
        context = {
            'patient': appointment.benh_nhan.ho_ten,
            'booking_id': appointment.pk,
            'branch': appointment.bac_si.chi_nhanh.ten,
            'branch_address': appointment.bac_si.chi_nhanh.dia_chi,
            'doctor': appointment.bac_si.ho_ten,
            'appointment_at': local_start.strftime('%H:%M, %d/%m/%Y'),
            'services': services,
        }
        text_body = render_to_string('core/receipts/booking_confirmation_email.txt', context)
        html_body = render_to_string('core/receipts/booking_confirmation_email.html', context)
        connection = sandbox_connection()
        message = EmailMultiAlternatives(
            subject=f'EyeViora xác nhận lịch khám #{appointment.pk}',
            body=text_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[recipient],
            connection=connection,
        )
        message.attach_alternative(html_body, 'text/html')
        message.send(fail_silently=False)
    except Exception:
        # Do not expose SMTP details or let an email error affect the booking.
        logger.error('Booking confirmation email failed; appointment_id=%s.', appointment_id)
