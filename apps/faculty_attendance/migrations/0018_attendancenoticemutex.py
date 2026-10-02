from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('faculty_attendance', '0017_attendancestaffnotice')]

    operations = [migrations.CreateModel(
        name='AttendanceNoticeMutex',
        fields=[('campus', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
            primary_key=True, serialize=False, to='tenants.campus'))],
        options={'db_table': 'faculty_attendance_notice_mutexes'},
    )]
