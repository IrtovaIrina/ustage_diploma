from decimal import Decimal
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="StaffPosition",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=40, unique=True, verbose_name="Код должности")),
                ("title", models.CharField(max_length=150, verbose_name="Наименование должности")),
                ("department", models.CharField(default="Коммерческий отдел", max_length=120, verbose_name="Подразделение")),
                ("is_management", models.BooleanField(default=False, verbose_name="Руководящая должность (KPI и планы)")),
                ("is_sales_manager", models.BooleanField(default=False, verbose_name="Менеджер по продажам (участвует в KPI)")),
                ("can_create_projects", models.BooleanField(default=True, verbose_name="Создание и ред. проектов")),
                ("can_manage_kanban_columns", models.BooleanField(default=False, verbose_name="Настройка колонок Канбана")),
                ("can_edit_estimates", models.BooleanField(default=True, verbose_name="Редактирование смет и ТЗ")),
                ("can_import_excel", models.BooleanField(default=False, verbose_name="Импорт прайс-листов Excel")),
                ("can_edit_catalog", models.BooleanField(default=False, verbose_name="Редактирование каталога")),
                ("can_view_logistics", models.BooleanField(default=True, verbose_name="Просмотр логистики (ДЛ/СДЭК)")),
                ("can_edit_logistics", models.BooleanField(default=False, verbose_name="Редактирование логистики")),
                ("can_view_digital_twin", models.BooleanField(default=True, verbose_name="Просмотр Цифрового паспорта")),
                ("can_edit_digital_twin", models.BooleanField(default=False, verbose_name="Редактирование Цифрового паспорта")),
                ("can_view_currencies", models.BooleanField(default=True, verbose_name="Просмотр Мультивалютного контура ЦБ РФ")),
                ("can_edit_currencies", models.BooleanField(default=False, verbose_name="Управление валютами ЦБ РФ")),
                ("can_view_vendors", models.BooleanField(default=True, verbose_name="Просмотр Реестра поставщиков")),
                ("can_edit_vendors", models.BooleanField(default=False, verbose_name="Редактирование Реестра поставщиков")),
                ("can_view_venue_profiles", models.BooleanField(default=True, verbose_name="Просмотр Справочника профилей театров")),
                ("can_edit_venue_profiles", models.BooleanField(default=False, verbose_name="Редактирование профилей театров")),
            ],
            options={
                "verbose_name": "Должность и роль",
                "verbose_name_plural": "Справочник ролей и должностей",
                "ordering": ["id"],
            },
        ),
        migrations.AddField(
            model_name="customuser",
            name="position",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="employees",
                to="catalog.staffposition",
                verbose_name="Штатная должность",
            ),
        ),
        migrations.AddField(
            model_name="customuser",
            name="can_manage_roles",
            field=models.BooleanField(default=False, verbose_name="Делегировано право управления штатом и ролями"),
        ),
        migrations.AddField(
            model_name="customuser",
            name="monthly_sales_plan_rub",
            field=models.DecimalField(decimal_places=2, default=Decimal("10000000.00"), max_digits=14, verbose_name="План продаж на месяц, ₽"),
        ),
        migrations.AddField(
            model_name="customuser",
            name="projects_plan_count",
            field=models.PositiveIntegerField(default=3, verbose_name="План по числу проектов"),
        ),
        migrations.AddField(
            model_name="customuser",
            name="tasks_plan_count",
            field=models.PositiveIntegerField(default=5, verbose_name="План по числу задач"),
        ),
        migrations.AddField(
            model_name="customuser",
            name="reset_otp_code",
            field=models.CharField(blank=True, default="", max_length=10, verbose_name="Одноразовый код сброса пароля"),
        ),
        migrations.AddField(
            model_name="customuser",
            name="reset_otp_created_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Время генерации OTP"),
        ),
        migrations.AddField(
            model_name="currencyrate",
            name="is_active",
            field=models.BooleanField(default=True, verbose_name="Выводить в рабочей таблице"),
        ),
    ]