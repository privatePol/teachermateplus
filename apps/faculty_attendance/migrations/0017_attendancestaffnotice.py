from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def seed_navigation(apps, schema_editor):
    Group = apps.get_model('navigation', 'MenuGroup')
    Item = apps.get_model('navigation', 'MenuItem')
    Link = apps.get_model('navigation', 'MenuItemPermission')
    Permission = apps.get_model('rbac', 'Permission')
    group, _ = Group.objects.get_or_create(portal='ADMIN', code='FACULTY_ATTENDANCE',
        defaults={'label': 'Faculty Attendance', 'icon': 'bi bi-calendar-check', 'sort_order': 65, 'is_active': True})
    item, _ = Item.objects.get_or_create(portal='ADMIN', code='ATTENDANCE_TERM_SUMMARY',
        defaults={'menu_group': group, 'label': 'AY/Term Attendance Summary',
                  'route_name': 'faculty_attendance:term_summary', 'sort_order': 60, 'is_active': True})
    Link.objects.get_or_create(menu_item=item, permission=Permission.objects.get(code='faculty_attendance.view'))


def unseed_navigation(apps, schema_editor):
    Item = apps.get_model('navigation', 'MenuItem')
    Link = apps.get_model('navigation', 'MenuItemPermission')
    for item in Item.objects.filter(portal='ADMIN', code='ATTENDANCE_TERM_SUMMARY', route_name='faculty_attendance:term_summary'):
        Link.objects.filter(menu_item=item).delete()
        item.delete()


class Migration(migrations.Migration):
    dependencies = [('faculty_attendance', '0016_alter_attendanceclosuredecision_reason_and_more'),
                    migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(name='AttendanceStaffNotice', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('updated_at', models.DateTimeField(auto_now=True)),
            ('event_key', models.CharField(max_length=100)),
            ('kind', models.CharField(choices=[('ABSENCE', 'Recorded absence'), ('TARDINESS', 'Monthly late follow-up')], max_length=12)),
            ('month', models.DateField(blank=True, null=True)),
            ('source_fingerprint', models.CharField(max_length=64)),
            ('payload', models.JSONField(default=dict)),
            ('is_active', models.BooleanField(default=True)),
            ('academic_year', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='academics.academicyear')),
            ('campus', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='tenants.campus')),
            ('faculty_user', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='attendance_followup_notices', to=settings.AUTH_USER_MODEL)),
            ('meeting', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='faculty_attendance.teachingmeeting')),
            ('recipient', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='attendance_staff_notices', to=settings.AUTH_USER_MODEL)),
            ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='tenants.tenant')),
            ('term', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='academics.term')),
        ], options={'db_table': 'faculty_attendance_staff_notices',
                    'indexes': [models.Index(fields=['recipient', 'campus', 'is_active'], name='idx_att_staff_notice_inbox')],
                    'constraints': [models.UniqueConstraint(fields=('tenant', 'campus', 'recipient', 'event_key'), name='uq_att_staff_notice_event')]}),
        migrations.RunPython(seed_navigation, unseed_navigation),
    ]
