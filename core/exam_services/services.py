import hashlib
import json
from ipaddress import ip_address

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q

from core.models import DanhMucDichVu, DichVuThucHien, HoaDon, LichKham, LogHeThong, PhieuKham
from core.billing.services import BillingError, bill_exam, billing_context, invoice_lock_reason, is_consultation
from core.payments.access import can_collect, require_scope
from core.payments.services import _expire_pending_locked, require_completed
from .forms import PerformedServiceFormSet


class ExamServiceError(ValidationError):
    pass


def revision(rows):
    # Hash notes for stale-form detection; never put note contents into audit logs.
    values = sorted((row.pk, row.dich_vu_id, row.so_luong, row.ghi_chu,
                     row.la_dich_vu_dat_truoc, row.da_xac_nhan_bac_si,
                     row.thoi_gian_cap_nhat.isoformat()) for row in rows)
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def lock_reason(appointment):
    try:
        require_completed(appointment)
    except Exception as exc:
        return str(exc)
    invoice = HoaDon.objects.filter(lich_kham_id=appointment.pk).first()
    if invoice:
        return invoice_lock_reason(invoice)
    return ''


def form_context(appointment, phieu=None, data=None):
    rows = list(phieu.dich_vu_thuc_hien.filter(da_huy=False).select_related('dich_vu', 'nguoi_thuc_hien')) if phieu else []
    costs = billing_context(phieu, rows)
    frozen = {row.dich_vu_id: row for row in rows}
    existing_ids = [row.dich_vu_id for row in rows]
    submitted = data is not None and any(key.startswith('services-') for key in data)
    formset = PerformedServiceFormSet(
        data if submitted else None, prefix='services',
        initial=[{'dich_vu': row.dich_vu_id, 'so_luong': row.so_luong, 'ghi_chu': row.ghi_chu} for row in rows],
        form_kwargs={'existing_ids': existing_ids},
    )
    catalog = DanhMucDichVu.objects.filter(Q(dang_ap_dung=True) | Q(pk__in=existing_ids)).order_by('ma_dich_vu')
    return {
        **costs,
        'services_formset': formset,
        'services_submitted': submitted,
        'services_revision': data.get('services_revision', '') if submitted else revision(rows),
        'services_lock_reason': lock_reason(appointment),
        'performed_services': rows,
        'services_catalog': [dict(id=entry.pk, code=entry.ma_dich_vu, name=entry.ten,
                                  price=str(frozen[entry.pk].display_price if entry.pk in frozen else entry.don_gia),
                                  consultation=is_consultation(entry.nhom), active=entry.dang_ap_dung) for entry in catalog],
    }


def doctor_form_context(appointment, phieu=None, data=None):
    """Clinical service form, limited to the responsible doctor and branch assignments."""
    rows = list(phieu.dich_vu_thuc_hien.filter(da_huy=False).select_related('dich_vu')) if phieu else []
    existing_ids = [row.dich_vu_id for row in rows]
    allowed_ids = set(DanhMucDichVu.objects.filter(
        dang_ap_dung=True,
        chi_nhanh_ap_dung__chi_nhanh_id=appointment.bac_si.chi_nhanh_id,
        chi_nhanh_ap_dung__dang_ap_dung=True,
        chi_nhanh_ap_dung__bac_si_duoc_phep__bac_si_id=appointment.bac_si_id,
        chi_nhanh_ap_dung__bac_si_duoc_phep__dang_ap_dung=True,
    ).values_list('pk', flat=True))
    booked_ids = set(appointment.dich_vu_dat_truoc_nhieu.values_list('pk', flat=True))
    if appointment.dich_vu_dat_truoc_id:
        booked_ids.add(appointment.dich_vu_dat_truoc_id)
    if booked_ids:
        # Existing appointment commitments remain selectable even if a catalog
        # assignment was later disabled; the doctor still must confirm delivery.
        allowed_ids.update(booked_ids)
    submitted = data is not None and any(key.startswith('services-') for key in data)
    getlist = getattr(data, 'getlist', None)
    if not data:
        submitted_booked_ids = []
    elif getlist:
        submitted_booked_ids = getlist('booked_performed')
    else:
        submitted_values = data.get('booked_performed', [])
        submitted_booked_ids = submitted_values if isinstance(submitted_values, (list, tuple)) else [submitted_values]
    formset = PerformedServiceFormSet(
        data if submitted else None, prefix='services',
        initial=[{'dich_vu': row.dich_vu_id, 'so_luong': row.so_luong, 'ghi_chu': row.ghi_chu} for row in rows],
        form_kwargs={'existing_ids': list(set(existing_ids) | booked_ids),
                     'allowed_ids': allowed_ids},
    )
    return {
        'services_formset': formset,
        'services_revision': data.get('services_revision', '') if submitted else revision(rows),
        'booked_services': list(appointment.dich_vu_dat_truoc_nhieu.all().order_by('ten')) or
                           ([appointment.dich_vu_dat_truoc] if appointment.dich_vu_dat_truoc_id else []),
        'booked_performed_ids': ({int(value) for value in submitted_booked_ids if str(value).isdigit()} if submitted else
                                 {row.dich_vu_id for row in rows if row.la_dich_vu_dat_truoc}),
        'doctor_services_submitted': submitted,
        'doctor_service_rows': rows,
        'doctor_services_catalog': list(DanhMucDichVu.objects.filter(pk__in=allowed_ids).order_by('ma_dich_vu')),
    }


@transaction.atomic
def sync_doctor_services(appointment_id, doctor, user, phieu, formset, submitted_revision,
                         booked_performed=False, remote_addr=''):
    """Record services delivered by the assigned doctor before exam completion."""
    appointment = LichKham.objects.select_for_update().select_related('bac_si').get(
        pk=appointment_id, bac_si=doctor,
    )
    if not user.is_active or not doctor.user_id or doctor.user_id != user.pk:
        raise PermissionDenied('Bác sĩ không hợp lệ.')
    if appointment.trang_thai != 'dang':
        raise ExamServiceError('Chỉ bác sĩ phụ trách mới ghi nhận dịch vụ khi lịch đang khám.')
    if HoaDon.objects.select_for_update().filter(lich_kham=appointment).exists():
        raise ExamServiceError('Đã có hóa đơn; dịch vụ đang bị khóa để bảo toàn thanh toán.')
    phieu = PhieuKham.objects.select_for_update().get(pk=phieu.pk, lich_kham=appointment,
                                                       benh_nhan_id=appointment.benh_nhan_id)
    current = list(DichVuThucHien.objects.select_for_update().filter(
        phieu_kham=phieu, da_huy=False,
    ).order_by('pk'))
    if submitted_revision != revision(current):
        raise ExamServiceError('Danh sách dịch vụ đã thay đổi. Vui lòng tải lại trước khi lưu.')
    if not formset.is_valid():
        raise ExamServiceError('Danh sách dịch vụ chưa hợp lệ. Kiểm tra dịch vụ và số lượng.')
    desired = {}
    for form in formset.forms:
        data = form.cleaned_data
        if data and not data.get('DELETE') and data.get('dich_vu'):
            desired[data['dich_vu'].pk] = (data['so_luong'], data['ghi_chu'])
    booked_ids = set(appointment.dich_vu_dat_truoc_nhieu.values_list('pk', flat=True))
    if appointment.dich_vu_dat_truoc_id:
        booked_ids.add(appointment.dich_vu_dat_truoc_id)
    if isinstance(booked_performed, bool):
        confirmed_booked_ids = {appointment.dich_vu_dat_truoc_id} if booked_performed and appointment.dich_vu_dat_truoc_id else set()
    else:
        confirmed_booked_ids = {int(value) for value in booked_performed if str(value).isdigit()} & booked_ids
    for booked_id in booked_ids - confirmed_booked_ids:
        desired.pop(booked_id, None)
    for booked_id in confirmed_booked_ids:
        desired.setdefault(booked_id, (1, 'Dịch vụ đặt trước được bác sĩ xác nhận đã thực hiện.'))
    before = {row.dich_vu_id: (row.so_luong, row.ghi_chu) for row in current}
    before_source = {row.dich_vu_id: (row.la_dich_vu_dat_truoc, row.da_xac_nhan_bac_si) for row in current}
    desired_source = {service_id: (service_id in confirmed_booked_ids, True) for service_id in desired}
    if before == desired and all(before_source.get(key) == desired_source[key] for key in desired) and len(before) == len(desired):
        return
    allowed = set(DanhMucDichVu.objects.filter(
        pk__in=desired, dang_ap_dung=True,
        chi_nhanh_ap_dung__chi_nhanh_id=doctor.chi_nhanh_id,
        chi_nhanh_ap_dung__dang_ap_dung=True,
        chi_nhanh_ap_dung__bac_si_duoc_phep__bac_si_id=doctor.pk,
        chi_nhanh_ap_dung__bac_si_duoc_phep__dang_ap_dung=True,
    ).values_list('pk', flat=True))
    allowed.update(booked_ids)
    catalog = {entry.pk: entry for entry in DanhMucDichVu.objects.select_for_update().filter(
        pk__in=desired,
    ).order_by('pk')}
    if any(key not in catalog or key not in allowed and key not in before for key in desired):
        raise ExamServiceError('Dịch vụ không được phân công cho bác sĩ tại chi nhánh hoặc đã ngừng áp dụng.')
    for row in current:
        if row.dich_vu_id not in desired:
            audit_line(row, 'delete', user, row.so_luong, remote_addr)
            if row.chi_tiet_hoa_don.exists():
                row.da_huy = True
                row.save(update_fields=['da_huy', 'thoi_gian_cap_nhat'])
            else:
                row.delete()
        else:
            quantity, note = desired[row.dich_vu_id]
            source, confirmed = desired_source[row.dich_vu_id]
            if (row.so_luong, row.ghi_chu, row.la_dich_vu_dat_truoc, row.da_xac_nhan_bac_si) != (quantity, note, source, confirmed):
                old_quantity, old_source = row.so_luong, (row.la_dich_vu_dat_truoc, row.da_xac_nhan_bac_si)
                row.so_luong, row.ghi_chu = quantity, note
                row.la_dich_vu_dat_truoc, row.da_xac_nhan_bac_si = source, confirmed
                row.full_clean()
                row.save(update_fields=['so_luong', 'ghi_chu', 'la_dich_vu_dat_truoc', 'da_xac_nhan_bac_si', 'thoi_gian_cap_nhat'])
                audit_line(row, 'update', user, old_quantity, remote_addr,
                           old_source != (source, confirmed) or before[row.dich_vu_id][1] != note,
                           before_source=old_source)
    for key in sorted(desired.keys() - before.keys()):
        quantity, note = desired[key]
        source, confirmed = desired_source[key]
        row = DichVuThucHien(
            phieu_kham=phieu, dich_vu=catalog[key], so_luong=quantity, ghi_chu=note,
            nguoi_thuc_hien=doctor, nguoi_ghi_nhan=None,
            la_dich_vu_dat_truoc=source,
            da_xac_nhan_bac_si=confirmed,
        )
        row.full_clean()
        row.save()
        audit_line(row, 'create', user, remote_addr=remote_addr)


def audit_line(row, action, user, before_quantity=None, remote_addr='', note_changed=False, before_source=None):
    try:
        address = str(ip_address(remote_addr))
    except ValueError:
        address = None
    identifiers = {'phieu_kham_id': row.phieu_kham_id, 'dich_vu_id': row.dich_vu_id,
                   'ma_dich_vu': row.dich_vu.ma_dich_vu, 'ten_dich_vu': row.dich_vu.ten,
                   'bac_si_id': row.nguoi_thuc_hien_id,
                   'nguoi_ghi_nhan_id': row.nguoi_ghi_nhan_id,
                   'la_dich_vu_dat_truoc': row.la_dich_vu_dat_truoc,
                   'da_xac_nhan_bac_si': row.da_xac_nhan_bac_si}
    LogHeThong.objects.create(
        model_name='DichVuThucHien', object_id=row.pk, hanh_dong=action,
        du_lieu_cu={**identifiers, 'so_luong': before_quantity,
                    'la_dich_vu_dat_truoc': before_source[0] if before_source is not None else row.la_dich_vu_dat_truoc,
                    'da_xac_nhan_bac_si': before_source[1] if before_source is not None else row.da_xac_nhan_bac_si}
        if action != 'create' else None,
        du_lieu_moi={**identifiers, 'so_luong': row.so_luong} if action != 'delete' else None,
        nguoi_thuc_hien=user, ip_address=address,
        ghi_chu='Ghi nhận dịch vụ thực hiện' + ('; có sửa ghi chú' if note_changed else ''),
    )


@transaction.atomic
def sync_services(phieu, user, formset, submitted_revision, remote_addr=''):
    # Same first lock as create_invoice/start_payment; invoice creation and service
    # changes cannot pass their checks concurrently for the same appointment.
    appointment = LichKham.objects.select_for_update().get(pk=phieu.lich_kham_id)
    if not can_collect(user):
        raise PermissionDenied('Chỉ quản lý chi nhánh hoặc tài khoản có quyền thu tiền được ghi nhận dịch vụ.')
    require_scope(user, appointment)
    # Serialize with IPN too: IPN locks invoice before checking/setting its state.
    invoice = HoaDon.objects.select_for_update().filter(lich_kham=appointment).first()
    if invoice:
        _expire_pending_locked(invoice, actor=user)
    phieu = PhieuKham.objects.select_for_update().get(pk=phieu.pk, lich_kham=appointment)
    current = list(DichVuThucHien.objects.select_for_update().filter(phieu_kham=phieu, da_huy=False).order_by('pk'))
    if any(row.da_xac_nhan_bac_si for row in current):
        raise ExamServiceError('Bác sĩ đã xác nhận dịch vụ chuyên môn; thu ngân không thể chỉnh sửa danh sách này.')
    if submitted_revision != revision(current):
        raise ExamServiceError('Danh sách dịch vụ đã thay đổi. Vui lòng tải lại trang trước khi lưu tiếp.')
    if not formset.is_valid():
        raise ExamServiceError('Danh sách dịch vụ chưa hợp lệ. Vui lòng kiểm tra số lượng và dịch vụ đã chọn.')
    desired = {}
    for form in formset.forms:
        data = form.cleaned_data
        if data and not data.get('DELETE') and data.get('dich_vu'):
            desired[data['dich_vu'].pk] = (data['so_luong'], data['ghi_chu'])
    before = {row.dich_vu_id: (row.so_luong, row.ghi_chu) for row in current}
    if before == desired:
        return
    reason = lock_reason(appointment)
    if reason:
        raise ExamServiceError(reason)
    # Re-read and lock authoritative catalog state at commit time. No client price.
    catalog = {entry.pk: entry for entry in DanhMucDichVu.objects.select_for_update().filter(pk__in=desired).order_by('pk')}
    if any(key not in catalog or (not catalog[key].dang_ap_dung and key not in before) for key in desired):
        raise ExamServiceError('Có dịch vụ đã ngừng áp dụng hoặc không còn tồn tại. Vui lòng chọn lại.')
    for row in current:
        if row.dich_vu_id not in desired:
            audit_line(row, 'delete', user, row.so_luong, remote_addr)
            if row.chi_tiet_hoa_don.exists():
                row.da_huy = True
                row.save(update_fields=['da_huy', 'thoi_gian_cap_nhat'])
            else:
                row.delete()
        elif before[row.dich_vu_id] != desired[row.dich_vu_id]:
            old_quantity, old_note = row.so_luong, row.ghi_chu
            row.so_luong, row.ghi_chu = desired[row.dich_vu_id]
            row.full_clean()
            row.save(update_fields=['so_luong', 'ghi_chu', 'thoi_gian_cap_nhat'])
            audit_line(row, 'update', user, old_quantity, remote_addr, old_note != row.ghi_chu)
    for key in sorted(desired.keys() - before.keys()):
        quantity, note = desired[key]
        row = DichVuThucHien(phieu_kham=phieu, dich_vu=catalog[key], so_luong=quantity,
                            ghi_chu=note, nguoi_thuc_hien_id=appointment.bac_si_id,
                            nguoi_ghi_nhan_id=user.pk)
        row.full_clean()
        row.save()
        audit_line(row, 'create', user, remote_addr=remote_addr)
    if appointment.trang_thai == 'xong':
        try:
            bill_exam(phieu.pk, user, recalculate=True, remote_addr=remote_addr)
        except BillingError as exc:
            raise ExamServiceError(exc.messages) from exc
