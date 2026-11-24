# Data-preserving terminology cleanup. Legacy insurance/classification columns are
# retained physically (nullable) so historical values remain available for audit.
from django.db import migrations, models
from django.db.models import Q, F
from django.db.models.lookups import GreaterThanOrEqual, LessThanOrEqual
from django.db.models import Func, Value


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0043_danhgialuotkham'),
    ]

    operations = [
        migrations.RenameModel(old_name='BenhVien', new_name='ChiNhanh'),
        migrations.RenameModel(old_name='BenhVienHinhAnh', new_name='ChiNhanhHinhAnh'),
        migrations.RenameModel(old_name='GioLamViecBenhVien', new_name='GioLamViecChiNhanh'),
        migrations.RemoveIndex(model_name='bacsi', name='core_bacsi_benh_vi_001881_idx'),
        migrations.RemoveIndex(model_name='chinhanhhinhanh', name='core_benhvi_benh_vi_6956dc_idx'),
        migrations.RemoveIndex(model_name='lichsukhambenh', name='core_lichsu_benh_vi_90728d_idx'),
        migrations.RemoveConstraint(model_name='dichvuchinhanh', name='clinic_service_unique'),
        migrations.RemoveConstraint(model_name='giaodichthanhtoan', name='giaodich_valid_method'),
        migrations.RemoveConstraint(model_name='chinhanhhinhanh', name='benhvien_single_cover_image'),
        migrations.RenameField(model_name='bacsi', old_name='benh_vien', new_name='chi_nhanh'),
        migrations.RenameField(model_name='khoa', old_name='benh_vien', new_name='chi_nhanh'),
        migrations.RenameField(model_name='lichsukhambenh', old_name='benh_vien', new_name='chi_nhanh'),
        migrations.RenameField(model_name='giaodichthanhtoan', old_name='benh_vien', new_name='chi_nhanh'),
        migrations.RenameField(model_name='quanlychinhanh', old_name='benh_vien', new_name='chi_nhanh'),
        migrations.RenameField(model_name='dichvuchinhanh', old_name='benh_vien', new_name='chi_nhanh'),
        migrations.RenameField(model_name='letanchinhanh', old_name='benh_vien', new_name='chi_nhanh'),
        migrations.RenameField(model_name='chinhanhhinhanh', old_name='benh_vien', new_name='chi_nhanh'),
        migrations.RenameField(model_name='giolamviecchinhanh', old_name='benh_vien', new_name='chi_nhanh'),
        # RenameField doesn't reliably rewrite fields embedded in historical
        # Index/constraint state. Recreate them so reverse migration restores
        # the old field definition after restoring the old field name.
        migrations.AddIndex(model_name='bacsi', index=models.Index(fields=['chi_nhanh', 'chuyen_khoa'], name='core_bacsi_chi_nha_124d62_idx')),
        migrations.AddIndex(model_name='chinhanhhinhanh', index=models.Index(fields=['chi_nhanh', 'thu_tu'], name='core_chinha_chi_nha_575c96_idx')),
        migrations.AddIndex(model_name='lichsukhambenh', index=models.Index(fields=['chi_nhanh', '-ngay_kham'], name='core_lichsu_chi_nha_103dac_idx')),
        migrations.AddConstraint(model_name='dichvuchinhanh', constraint=models.UniqueConstraint(fields=('dich_vu', 'chi_nhanh'), name='clinic_service_unique')),
        migrations.AddConstraint(model_name='giaodichthanhtoan', constraint=models.CheckConstraint(condition=Q(Q(('het_han_luc__isnull', False), ('phuong_thuc', 'VNPAY')), Q(('chi_nhanh__isnull', False), ('het_han_luc__isnull', True), ('ma_giao_dich_vnpay', ''), ('ma_phan_hoi', ''), ('phuong_thuc', 'CASH'), ('tmn_code', ''), ('trang_thai', 'success'), ('trang_thai_vnpay', ''), ('vnp_pay_date', '')), _connector='OR'), name='giaodich_valid_method')),
        migrations.RemoveConstraint(model_name='chinhanh', name='benhvien_gio_mo_lt_gio_dong'),
        migrations.AddConstraint(model_name='chinhanh', constraint=models.CheckConstraint(condition=Q(gio_mo__lt=F('gio_dong')), name='chinhanh_gio_mo_lt_gio_dong')),
        migrations.RemoveConstraint(model_name='chinhanh', name='benhvien_vi_tri_wgs84_range'),
        migrations.AddConstraint(model_name='chinhanh', constraint=models.CheckConstraint(condition=(GreaterThanOrEqual(Func(F('vi_tri'), function='ST_X'), Value(-180)) & LessThanOrEqual(Func(F('vi_tri'), function='ST_X'), Value(180)) & GreaterThanOrEqual(Func(F('vi_tri'), function='ST_Y'), Value(-90)) & LessThanOrEqual(Func(F('vi_tri'), function='ST_Y'), Value(90))), name='chinhanh_vi_tri_wgs84_range')),
        migrations.AddConstraint(model_name='chinhanhhinhanh', constraint=models.UniqueConstraint(fields=('chi_nhanh',), condition=Q(la_anh_dai_dien=True), name='chinhanh_single_cover_image')),
        migrations.RemoveConstraint(model_name='giolamviecchinhanh', name='giolamviec_benhvien_gio_mo_lt_gio_dong'),
        migrations.AddConstraint(model_name='giolamviecchinhanh', constraint=models.CheckConstraint(condition=Q(nghi=True) | Q(gio_mo__lt=F('gio_dong')), name='giolamviec_chinhanh_gio_mo_lt_gio_dong')),
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RemoveIndex(model_name='chinhanh', name='core_benhvi_loai_hi_1592f1_idx'),
                migrations.RemoveIndex(model_name='chinhanh', name='core_benhvi_phuong_70e0cd_idx'),
                migrations.RemoveConstraint(model_name='hoadon', name='hoadon_insurance_amount'),
                migrations.RemoveConstraint(model_name='hoadon', name='hoadon_zero_settlement'),
                migrations.RemoveConstraint(model_name='chitiethoadon', name='cthd_insurance_limits'),
                migrations.RemoveField(model_name='chinhanh', name='cap_cuu_24h'),
                migrations.RemoveField(model_name='chinhanh', name='co_bhyt'),
                migrations.RemoveField(model_name='chinhanh', name='loai_hinh'),
                migrations.RemoveField(model_name='benhnhan', name='bhyt'),
                migrations.RemoveField(model_name='hoadon', name='bhyt_ho_tro'),
                migrations.RemoveField(model_name='hoadon', name='bhyt_snapshot'),
                migrations.RemoveField(model_name='hoadon', name='bhyt_da_xac_minh'),
                migrations.RemoveField(model_name='hoadon', name='bhyt_ty_le_xac_minh'),
                migrations.RemoveField(model_name='hoadon', name='hoan_tat_bhyt'),
                migrations.RemoveField(model_name='chitiethoadon', name='quy_tac_bhyt'),
                migrations.RemoveField(model_name='chitiethoadon', name='ty_le_bhyt'),
                migrations.RemoveField(model_name='chitiethoadon', name='bhyt_ho_tro'),
                migrations.RemoveField(model_name='chitiethoadon', name='bhyt_snapshot'),
                migrations.DeleteModel(name='QuyTacBHYT'),
                migrations.DeleteModel(name='BaoHiemYTe'),
                migrations.AddConstraint(
                    model_name='hoadon',
                    constraint=models.CheckConstraint(
                        condition=Q(so_tien__gte=1, so_tien__lte=9999999999) | Q(so_tien=0, trang_thai='paid'),
                        name='hoadon_self_pay_amount',
                    ),
                ),
                migrations.AlterModelOptions(name='chinhanh', options={'verbose_name': 'Chi nh?nh', 'verbose_name_plural': 'Chi nh?nh'}),
                migrations.AlterModelOptions(name='chinhanhhinhanh', options={'verbose_name': '?nh chi nh?nh', 'verbose_name_plural': '?nh chi nh?nh'}),
                migrations.AlterModelOptions(name='giolamviecchinhanh', options={'verbose_name': 'Gi? l?m vi?c chi nh?nh', 'verbose_name_plural': 'Gi? l?m vi?c chi nh?nh'}),
            ],
        ),
        migrations.RunSQL(
            sql=[
                'ALTER TABLE core_chinhanh ALTER COLUMN loai_hinh DROP NOT NULL',
                'ALTER TABLE core_chinhanh ALTER COLUMN co_bhyt DROP NOT NULL',
                'ALTER TABLE core_chinhanh ALTER COLUMN cap_cuu_24h DROP NOT NULL',
                'ALTER TABLE core_hoadon ALTER COLUMN bhyt_ho_tro DROP NOT NULL',
                'ALTER TABLE core_hoadon ALTER COLUMN bhyt_snapshot DROP NOT NULL',
                'ALTER TABLE core_hoadon ALTER COLUMN bhyt_da_xac_minh DROP NOT NULL',
                'ALTER TABLE core_hoadon ALTER COLUMN hoan_tat_bhyt DROP NOT NULL',
                'ALTER TABLE core_chitiethoadon ALTER COLUMN ty_le_bhyt DROP NOT NULL',
                'ALTER TABLE core_chitiethoadon ALTER COLUMN bhyt_ho_tro DROP NOT NULL',
                'ALTER TABLE core_chitiethoadon ALTER COLUMN bhyt_snapshot DROP NOT NULL',
                'ALTER TABLE core_quytacbhyt DROP CONSTRAINT core_quytacbhyt_dich_vu_id_009cbb04_fk_core_danhmucdichvu_id',
                'ALTER TABLE core_chitiethoadon DROP CONSTRAINT core_chitiethoadon_quy_tac_bhyt_id_cc1fc905_fk_core_quyt',
            ],
            reverse_sql=[
                "UPDATE core_chinhanh SET loai_hinh='tu' WHERE loai_hinh IS NULL",
                'UPDATE core_chinhanh SET co_bhyt=FALSE WHERE co_bhyt IS NULL',
                'UPDATE core_chinhanh SET cap_cuu_24h=FALSE WHERE cap_cuu_24h IS NULL',
                'UPDATE core_hoadon SET bhyt_ho_tro=0 WHERE bhyt_ho_tro IS NULL',
                "UPDATE core_hoadon SET bhyt_snapshot='{}'::jsonb WHERE bhyt_snapshot IS NULL",
                'UPDATE core_hoadon SET bhyt_da_xac_minh=FALSE WHERE bhyt_da_xac_minh IS NULL',
                'UPDATE core_hoadon SET hoan_tat_bhyt=FALSE WHERE hoan_tat_bhyt IS NULL',
                'UPDATE core_chitiethoadon SET ty_le_bhyt=0 WHERE ty_le_bhyt IS NULL',
                'UPDATE core_chitiethoadon SET bhyt_ho_tro=0 WHERE bhyt_ho_tro IS NULL',
                "UPDATE core_chitiethoadon SET bhyt_snapshot='{}'::jsonb WHERE bhyt_snapshot IS NULL",
                'ALTER TABLE core_chinhanh ALTER COLUMN loai_hinh SET NOT NULL',
                'ALTER TABLE core_chinhanh ALTER COLUMN co_bhyt SET NOT NULL',
                'ALTER TABLE core_chinhanh ALTER COLUMN cap_cuu_24h SET NOT NULL',
                'ALTER TABLE core_hoadon ALTER COLUMN bhyt_ho_tro SET NOT NULL',
                'ALTER TABLE core_hoadon ALTER COLUMN bhyt_snapshot SET NOT NULL',
                'ALTER TABLE core_hoadon ALTER COLUMN bhyt_da_xac_minh SET NOT NULL',
                'ALTER TABLE core_hoadon ALTER COLUMN hoan_tat_bhyt SET NOT NULL',
                'ALTER TABLE core_chitiethoadon ALTER COLUMN ty_le_bhyt SET NOT NULL',
                'ALTER TABLE core_chitiethoadon ALTER COLUMN bhyt_ho_tro SET NOT NULL',
                'ALTER TABLE core_chitiethoadon ALTER COLUMN bhyt_snapshot SET NOT NULL',
                'ALTER TABLE core_quytacbhyt ADD CONSTRAINT core_quytacbhyt_dich_vu_id_009cbb04_fk_core_danhmucdichvu_id FOREIGN KEY (dich_vu_id) REFERENCES core_danhmucdichvu (id) DEFERRABLE INITIALLY DEFERRED',
                'ALTER TABLE core_chitiethoadon ADD CONSTRAINT core_chitiethoadon_quy_tac_bhyt_id_cc1fc905_fk_core_quyt FOREIGN KEY (quy_tac_bhyt_id) REFERENCES core_quytacbhyt (id) DEFERRABLE INITIALLY DEFERRED',
            ],
        ),
    ]
