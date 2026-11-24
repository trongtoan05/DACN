from django.conf import settings
from django.db import migrations


def copy_manager_assignments_to_reception(apps, schema_editor):
    """Turn legacy branch-manager assignments into receptionist assignments.

    The legacy table/model is intentionally retained for data recovery.
    Runtime authorization uses only LeTanChiNhanh.
    """
    Manager = apps.get_model('core', 'QuanLyChiNhanh')
    Receptionist = apps.get_model('core', 'LeTanChiNhanh')
    User = apps.get_model('auth', 'User')
    Doctor = apps.get_model('core', 'BacSi')
    Patient = apps.get_model('core', 'BenhNhan')
    alias = schema_editor.connection.alias
    for manager in Manager.objects.using(alias).order_by('pk').iterator():
        user = User.objects.using(alias).get(pk=manager.user_id)
        has_conflicting_role = (
            user.is_staff or user.is_superuser
            or Doctor.objects.using(alias).filter(user_id=user.pk).exists()
            or Patient.objects.using(alias).filter(user_id=user.pk).exists()
        )
        if has_conflicting_role:
            raise RuntimeError(
                'Cannot merge branch-manager profile for user id '
                f'{manager.user_id}: the account also has an admin, doctor, or patient role.'
            )
        receptionist = Receptionist.objects.using(alias).filter(user_id=manager.user_id).first()
        if receptionist:
            if receptionist.chi_nhanh_id != manager.chi_nhanh_id:
                raise RuntimeError(
                    'Cannot merge branch-manager profile for user id '
                    f'{manager.user_id}: it conflicts with an existing receptionist branch.'
                )
            continue
        Receptionist.objects.using(alias).create(
            user_id=manager.user_id,
            chi_nhanh_id=manager.chi_nhanh_id,
        )


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0047_clinic_runtime_labels'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # Reversing automatically cannot distinguish profiles copied here from
        # receptionist profiles that existed before; fail closed to preserve data.
        migrations.RunPython(copy_manager_assignments_to_reception),
    ]
