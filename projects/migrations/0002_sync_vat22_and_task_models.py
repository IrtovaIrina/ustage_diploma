from decimal import Decimal
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("projects", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="project",
            name="order_number",
            field=models.CharField(blank=True, default="", max_length=80, verbose_name="Номер коммерческого заказа / тендера"),
        ),
        migrations.AddField(
            model_name="project",
            name="client_budget_rub",
            field=models.DecimalField(decimal_places=2, default=Decimal("5000000.00"), max_digits=14, verbose_name="Предельный бюджет заказчика (театра), ₽"),
        ),
        migrations.AddField(
            model_name="estimate",
            name="ceo_force_unblock_kp",
            field=models.BooleanField(default=False, verbose_name="Принудительная разблокировка КП решением Гендиректора"),
        ),
        migrations.AddField(
            model_name="estimate",
            name="installation_vat_rate",
            field=models.DecimalField(
                choices=[
                    (Decimal("22.00"), "НДС 22% (основная ставка с 2026 г.)"),
                    (Decimal("10.00"), "НДС 10%"),
                    (Decimal("7.00"), "НДС 7% (УСН)"),
                    (Decimal("5.00"), "НДС 5% (УСН)"),
                    (Decimal("0.00"), "Без НДС"),
                ],
                decimal_places=2,
                default=Decimal("22.00"),
                max_digits=5,
                verbose_name="Ставка НДС на монтаж, %",
            ),
        ),
        migrations.AlterField(
            model_name="estimateline",
            name="vat_rate",
            field=models.DecimalField(
                choices=[
                    (Decimal("22.00"), "22%"),
                    (Decimal("10.00"), "10%"),
                    (Decimal("7.00"), "7%"),
                    (Decimal("5.00"), "5%"),
                    (Decimal("0.00"), "Без НДС"),
                ],
                decimal_places=2,
                default=Decimal("22.00"),
                max_digits=5,
                verbose_name="Ставка НДС, %",
            ),
        ),
        migrations.CreateModel(
            name="TaskComment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("text", models.TextField(verbose_name="Текст комментария")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Дата публикации")),
                ("author", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL, verbose_name="Автор комментария")),
                ("task", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="comments", to="projects.kanbantask", verbose_name="Рабочая задача")),
            ],
            options={
                "verbose_name": "Комментарий к задаче",
                "verbose_name_plural": "Комментарии к задачам",
                "ordering": ["created_at"],
            },
        ),
        migrations.CreateModel(
            name="TaskAttachment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("file", models.FileField(upload_to="task_files/", verbose_name="Рабочий файл")),
                ("filename", models.CharField(max_length=255, verbose_name="Имя файла")),
                ("uploaded_at", models.DateTimeField(auto_now_add=True, verbose_name="Дата загрузки")),
                ("task", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="attachments", to="projects.kanbantask", verbose_name="Рабочая задача")),
                ("uploaded_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL, verbose_name="Загрузил сотрудник")),
            ],
            options={
                "verbose_name": "Файл задачи",
                "verbose_name_plural": "Файлы задач",
                "ordering": ["-uploaded_at"],
            },
        ),
    ]