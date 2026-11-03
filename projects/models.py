from decimal import Decimal
from django.db import models
from django.conf import settings
from django.utils import timezone
from catalog.models import Item, Vendor, CurrencyRate


class KanbanColumn(models.Model):
    """Динамически настраиваемые колонки (разделы) Канбан-доски с триггерами автоматизации[cite: 2]"""
    code = models.CharField("Системный код", max_length=40, unique=True)
    title = models.CharField("Название раздела Канбан-доски", max_length=100)
    order = models.PositiveIntegerField("Порядок колонки", default=1)
    color_hex = models.CharField("Акцентный цвет шапки", max_length=20, default="#0066cc")
    is_logistics_stage = models.BooleanField(
        "Этап отслеживания поставки (авто-добавление в Логистику)",
        default=False
    )
    is_completed_stage = models.BooleanField(
        "Этап завершения (авто-перенос в Цифровой паспорт объекта)",
        default=False
    )

    class Meta:
        ordering = ['order', 'id']
        verbose_name = "Раздел Канбан-доски"
        verbose_name_plural = "1. Разделы Канбан-доски"

    def __str__(self):
        return f"{self.order}. {self.title}"


class Project(models.Model):
    """Карточка проекта комплексного оснащения сцены (сущность выполнения подтвержденного Заказа)[cite: 1, 2]"""
    STATUS_CHOICES = (
        ('1_lead', '1. Новая заявка / Разбор ТЗ театра'),
        ('2_protection', '2. Защита проекта у производителей'),
        ('3_tender', '3. КП и Тендер 44-ФЗ / 223-ФЗ'),
        ('4_supply', '4. Закупка и Доставка (Деловые Линии / СДЭК)'),
        ('5_done', '5. Объект сдан / На сервисном ТО'),
    )

    title = models.CharField("Название проекта / Объекта", max_length=200)
    client = models.CharField("Заказчик (Театр / Филармония / ДК)", max_length=200)
    order_number = models.CharField(
        "Номер коммерческого заказа / тендера",
        max_length=60,
        blank=True,
        default="",
        help_text="Связь с внешним заказом клиента или номером закупки ЕИС"
    )
    client_budget_rub = models.DecimalField(
        "Бюджет, который готов потратить театр (лимит заказчика), ₽",
        max_digits=14,
        decimal_places=2,
        default=Decimal('0.00')
    )
    status = models.CharField("Системный статус", max_length=25, choices=STATUS_CHOICES, default='1_lead')
    kanban_column = models.ForeignKey(
        KanbanColumn,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='projects',
        verbose_name="Раздел Канбан-доски"
    )
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='managed_projects',
        verbose_name="Ответственный менеджер"
    )
    approved_by_engineer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='engineered_projects',
        verbose_name="Проверено главным инженером"
    )
    engineering_Requested = models.BooleanField("Запрошена инженерная проверка", default=False)
    is_delivery_arrived = models.BooleanField("Поставка прибыла (сигнал из Логистики)", default=False)

    # Текст ТЗ театра и габариты сценической коробки для Ustage Auto-Rigger[cite: 2]
    theater_tz_text = models.TextField(
        "Входящее ТЗ театра (текст для авторазбора)",
        blank=True,
        default="Зеркало сцены 14х10 м, высота до колосников 8 м, зал на 450 мест. Световое, звуковое и механическое оснащение."
    )
    stage_width = models.DecimalField("Ширина зеркала сцены (м)", max_digits=6, decimal_places=2, default=14.00)
    stage_depth = models.DecimalField("Глубина сцены (м)", max_digits=6, decimal_places=2, default=10.00)
    stage_height = models.DecimalField("Высота подвеса / колосников (м)", max_digits=6, decimal_places=2, default=8.00)
    hall_seats = models.PositiveIntegerField("Количество мест в зрительном зале", default=450)
    max_rigging_load_kg = models.DecimalField(
        "Допустимая нагрузка на фермы и штанкеты (кг)",
        max_digits=8,
        decimal_places=2,
        default=6500.00
    )
    max_power_kw = models.DecimalField("Выделенная электрическая мощность (кВт)", max_digits=6, decimal_places=2, default=220.00)

    is_government_44fz = models.BooleanField("Госзакупка по 44-ФЗ / 223-ФЗ (нацрежим ПП № 1875)", default=True)
    created_at = models.DateTimeField("Дата создания", auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Проект оснащения"
        verbose_name_plural = "2. Проекты оснащения объектов"

    @property
    def shipments_progress_summary(self) -> str:
        """Сводка по поставкам разных поставщиков проекта[cite: 2]"""
        qs = self.dellin_shipments.all()
        total = qs.count()
        if total == 0:
            return ""
        arrived = qs.filter(status='arrived').count()
        latest_eta = qs.exclude(eta_date__isnull=True).order_by('-eta_date').first()
        eta_str = f", крайний срок: {latest_eta.eta_date.strftime('%d.%m.%Y')}" if latest_eta and latest_eta.eta_date else ""
        return f"Поставки от вендоров: прибыло {arrived} из {total}{eta_str}"

    def __str__(self):
        return f"{self.title} ({self.client})"


class KanbanTask(models.Model):
    """Рабочие задачи сотрудников на Канбан-доске с описанием, вложениями и комментариями[cite: 2]"""
    PRIORITY_CHOICES = (
        ('high', 'Высокий приоритет'),
        ('normal', 'Плановая задача'),
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='tasks',
        null=True,
        blank=True,
        verbose_name="Связанный проект из списка"
    )
    project_name_manual = models.CharField(
        "Название проекта (прописанное вручную)",
        max_length=200,
        blank=True,
        default=""
    )
    kanban_column = models.ForeignKey(
        KanbanColumn,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='tasks',
        verbose_name="Раздел Канбан-доски"
    )
    title = models.CharField("Текст поручения / Задачи", max_length=240)
    description = models.TextField(
        "Подробное описание задачи и чек-лист работ",
        blank=True,
        default="Проверить исходные данные по проекту, согласовать параметры с техническим отделом и прикрепить итоговый файл."
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        verbose_name="Исполнитель"
    )
    priority = models.CharField("Приоритет", max_length=15, choices=PRIORITY_CHOICES, default='normal')
    due_date = models.DateField("Срок выполнения", default=timezone.now)
    is_completed = models.BooleanField("Выполнено", default=False)
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        ordering = ['is_completed', 'due_date', '-id']
        verbose_name = "Рабочая задача Канбана"
        verbose_name_plural = "3. Рабочие задачи на Канбан-доске"

    @property
    def display_project_title(self) -> str:
        if self.project_name_manual and self.project_name_manual.strip():
            return self.project_name_manual.strip()
        if self.project:
            return self.project.title
        return "Общекорпоративная задача ООО «Юстейдж»"

    def __str__(self):
        return f"Задача #{self.id}: {self.title}"


class TaskComment(models.Model):
    """Комментарии сотрудников внутри карточки рабочей задачи[cite: 2]"""
    task = models.ForeignKey(KanbanTask, on_delete=models.CASCADE, related_name='comments', verbose_name="Задача")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, verbose_name="Автор")
    text = models.TextField("Текст комментария")
    created_at = models.DateTimeField("Дата и время", auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        verbose_name = "Комментарий к задаче"
        verbose_name_plural = "Комментарии к задачам"

    def __str__(self):
        return f"Комментарий к задаче #{self.task_id}"


class TaskAttachment(models.Model):
    """Прикрепленные файлы и чертежи внутри карточки рабочей задачи[cite: 2]"""
    task = models.ForeignKey(KanbanTask, on_delete=models.CASCADE, related_name='attachments', verbose_name="Задача")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, verbose_name="Загрузил")
    file = models.FileField("Файл документа / чертежа", upload_to='task_files/%Y/%m/')
    filename = models.CharField("Имя файла", max_length=255)
    uploaded_at = models.DateTimeField("Дата загрузки", auto_now_add=True)

    class Meta:
        ordering = ['-uploaded_at']
        verbose_name = "Вложение задачи"
        verbose_name_plural = "Файлы задач"

    def __str__(self):
        return self.filename


class Estimate(models.Model):
    """Инженерно-коммерческая смета проекта со скрытыми расходами, НДС и разблокировкой КП Гендиректором[cite: 2]"""
    project = models.OneToOneField(Project, on_delete=models.CASCADE, related_name='estimate', verbose_name="Проект")
    version = models.PositiveIntegerField("Редакция сметы", default=1)
    hidden_overhead_coeff = models.DecimalField(
        "Коэффициент скрытых расходов",
        max_digits=4,
        decimal_places=2,
        default=Decimal('1.00')
    )
    fx_buffer_pct = models.DecimalField(
        "Валютный буфер к курсу ЦБ РФ (внутренний курс поставщика), %",
        max_digits=5,
        decimal_places=2,
        default=Decimal('4.00')
    )
    platform_fee_pct = models.DecimalField(
        "Комиссия торговой площадки / банка (вшивается в КП), %",
        max_digits=5,
        decimal_places=2,
        default=Decimal('1.50')
    )
    delivery_cost_rub = models.DecimalField(
        "Стоимость доставки до объекта, ₽",
        max_digits=12,
        decimal_places=2,
        default=Decimal('45000.00')
    )
    installation_cost_rub = models.DecimalField(
        "Индивидуальная стоимость монтажа и ПНР на сцене, ₽",
        max_digits=12,
        decimal_places=2,
        default=Decimal('120000.00')
    )
    embed_expenses_in_kp = models.BooleanField(
        "Вшить доставку и монтаж пропорционально в стоимость позиций КП (скрыто от заказчика)",
        default=True
    )
    include_installation_separate = models.BooleanField(
        "Выводить монтаж отдельной строкой в КП (если не вшит в позиции)",
        default=False
    )
    ceo_force_unblock_kp = models.BooleanField(
        "Принудительно разблокировано Генеральным директором (обход лимита 70%)",
        default=False
    )

    class Meta:
        verbose_name = "Смета проекта"
        verbose_name_plural = "4. Сметы и спецификации"

    @property
    def total_equipment_cost_rub(self) -> Decimal:
        """Плановая себестоимость закупки оборудования (с учетом скидок и валютного буфера)[cite: 2]"""
        total = sum((line.line_cost_rub for line in self.lines.all()), Decimal('0.00'))
        return total.quantize(Decimal('0.01'))

    @property
    def total_cost_rub(self) -> Decimal:
        """Полная плановая себестоимость проекта (оборудование + доставка + монтаж, если применимо)"""
        base = self.total_equipment_cost_rub + Decimal(str(self.delivery_cost_rub or 0))
        if self.embed_expenses_in_kp or self.include_installation_separate:
            base += Decimal(str(self.installation_cost_rub or 0))
        return base.quantize(Decimal('0.01'))

    @property
    def total_actual_cost_rub(self) -> Decimal:
        total = sum((line.line_actual_cost_rub for line in self.lines.all()), Decimal('0.00'))
        base = total + Decimal(str(self.delivery_cost_rub or 0))
        if self.embed_expenses_in_kp or self.include_installation_separate:
            base += Decimal(str(self.installation_cost_rub or 0))
        return base.quantize(Decimal('0.01'))

    @property
    def raw_lines_base_client_rub(self) -> Decimal:
        """Сумма всех позиций с наценкой и комиссией площадки до распределения доставки и монтажа"""
        total = sum((line.base_client_line_rub for line in self.lines.all()), Decimal('0.00'))
        return total.quantize(Decimal('0.01'))

    @property
    def total_embedded_overhead_rub(self) -> Decimal:
        """Сумма доставки и монтажа, которая пропорционально вшивается в цену оборудования в КП[cite: 2]"""
        if not self.embed_expenses_in_kp:
            return Decimal('0.00')
        return (Decimal(str(self.delivery_cost_rub or 0)) + Decimal(str(self.installation_cost_rub or 0))).quantize(Decimal('0.01'))

    @property
    def total_client_rub(self) -> Decimal:
        """Итоговая отпускная сумма КП для заказчика (включая НДС и вшитые расходы)[cite: 2]"""
        total = sum((line.client_line_total_rub for line in self.lines.all()), Decimal('0.00'))
        if not self.embed_expenses_in_kp and self.include_installation_separate:
            total += Decimal(str(self.installation_cost_rub or 0))
        return total.quantize(Decimal('0.01'))

    @property
    def total_vat_rub(self) -> Decimal:
        """Итоговая сумма НДС по смете (выделяется из цен позиций по ставкам 22%, 10%, 7%, 5%)[cite: 1, 2]"""
        total = sum((line.vat_amount_rub for line in self.lines.all()), Decimal('0.00'))
        if not self.embed_expenses_in_kp and self.include_installation_separate:
            inst = Decimal(str(self.installation_cost_rub or 0))
            total += (inst * Decimal('22') / Decimal('122')).quantize(Decimal('0.01'))
        return total.quantize(Decimal('0.01'))

    @property
    def total_client_net_of_vat_rub(self) -> Decimal:
        """Выручка по КП, очищенная от НДС[cite: 1]"""
        return (self.total_client_rub - self.total_vat_rub).quantize(Decimal('0.01'))

    @property
    def gross_margin_rub(self) -> Decimal:
        return (self.total_client_rub - self.total_cost_rub).quantize(Decimal('0.01'))

    @property
    def gross_margin_pct(self) -> Decimal:
        """Валовая маржинальность от общей суммы КП[cite: 1, 2]"""
        client = self.total_client_rub
        if client == Decimal('0.00'):
            return Decimal('0.00')
        return ((self.gross_margin_rub / client) * Decimal('100')).quantize(Decimal('0.1'))

    @property
    def net_margin_ex_vat_pct(self) -> Decimal:
        """Экономическая маржинальность по выручке без НДС[cite: 1]"""
        net_rev = self.total_client_net_of_vat_rub
        if net_rev <= Decimal('0.00'):
            return Decimal('0.00')
        return (((net_rev - self.total_cost_rub) / net_rev) * Decimal('100')).quantize(Decimal('0.1'))

    @property
    def is_budget_exceeded(self) -> bool:
        """Превышен ли бюджет театра, который он готов потратить[cite: 2]"""
        budget = self.project.client_budget_rub
        if not budget or budget <= Decimal('0.00'):
            return False
        return self.total_client_rub > budget

    @property
    def budget_overrun_rub(self) -> Decimal:
        if not self.is_budget_exceeded:
            return Decimal('0.00')
        return (self.total_client_rub - self.project.client_budget_rub).quantize(Decimal('0.01'))

    @property
    def protection_rate_pct(self) -> Decimal:
        lines = self.lines.all()
        total_cnt = lines.count()
        if total_cnt == 0:
            return Decimal('0.0')
        protected_cnt = lines.filter(is_protected=True).count()
        return ((Decimal(protected_cnt) / Decimal(total_cnt)) * Decimal('100')).quantize(Decimal('0.1'))

    @property
    def is_kp_export_allowed(self) -> bool:
        """Выгрузка КП разрешена при защите >= 70% ИЛИ если Гендиректор нажал спец-разблокировку[cite: 2]"""
        if self.ceo_force_unblock_kp:
            return True
        return self.protection_rate_pct >= Decimal('70.0')

    @property
    def total_weight_kg(self) -> Decimal:
        return sum(
            (line.item.weight_kg * line.quantity for line in self.lines.all() if line.item),
            Decimal('0.00')
        ).quantize(Decimal('0.1'))

    @property
    def total_power_kw(self) -> Decimal:
        watts = sum(
            (line.item.power_w * line.quantity for line in self.lines.all() if line.item),
            Decimal('0.00')
        )
        return (watts / Decimal('1000')).quantize(Decimal('0.01'))

    def __str__(self):
        return f"Смета #{self.id} — {self.project.title}"


class EstimateLine(models.Model):
    """Позиция спецификации проекта (из каталога либо добавленная вручную нестандартная позиция)[cite: 2]"""
    VAT_CHOICES = (
        (Decimal('22.00'), 'НДС 22% (осн. ставка с 2026 г.)'),
        (Decimal('10.00'), 'НДС 10%'),
        (Decimal('7.00'), 'НДС 7% (УСН)'),
        (Decimal('5.00'), 'НДС 5% (УСН)'),
        (Decimal('0.00'), 'Без НДС'),
    )

    estimate = models.ForeignKey(Estimate, on_delete=models.CASCADE, related_name='lines')
    item = models.ForeignKey(
        Item,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        verbose_name="Оборудование из каталога"
    )
    # Поля для ручного добавления нестандартного оборудования напрямую в КП[cite: 2]
    is_manual_item = models.BooleanField("Введено вручную (не из каталога)", default=False)
    manual_sku = models.CharField("Артикул (ручной ввод)", max_length=60, blank=True, default="")
    manual_brand = models.CharField("Бренд (ручной ввод)", max_length=80, blank=True, default="")
    manual_name = models.CharField("Наименование позиции (ручной ввод)", max_length=255, blank=True, default="")
    manual_specs = models.TextField("Характеристики ТТХ (ручной ввод)", blank=True, default="")
    manual_vendor_name = models.CharField("Поставщик (ручной ввод)", max_length=120, blank=True, default="Спецпоставка РФ")
    manual_currency_code = models.CharField("Код валюты закупки", max_length=10, default="RUB")
    manual_purchase_price = models.DecimalField(
        "Цена закупки (в валюте)",
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00')
    )

    quantity = models.PositiveIntegerField("Количество, шт./м", default=1)
    is_protected = models.BooleanField("Защита проекта подтверждена вендором", default=True)
    markup_pct = models.DecimalField("Целевая наценка (%)", max_digits=5, decimal_places=2, default=Decimal('25.00'))
    vat_rate = models.DecimalField("Ставка НДС (%)", max_digits=5, decimal_places=2, choices=VAT_CHOICES, default=Decimal('22.00'))
    actual_unit_cost_rub = models.DecimalField(
        "Фактическая цена закупки 1 ед. по счету, ₽",
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00')
    )
    is_auto_rigged = models.BooleanField("Добавлено модулем Ustage Auto-Rigger", default=False)

    class Meta:
        ordering = ['id']
        verbose_name = "Строка сметы"
        verbose_name_plural = "Строки сметы"

    @property
    def display_sku(self) -> str:
        if self.item:
            return self.item.sku
        return self.manual_sku or f"MAN-{self.id or 0}"

    @property
    def display_brand(self) -> str:
        if self.item:
            return self.item.brand
        return self.manual_brand or "Индивидуальный заказ"

    @property
    def display_name(self) -> str:
        if self.item:
            return self.item.name
        return self.manual_name or "Нестандартное сценическое оборудование"

    @property
    def display_specs(self) -> str:
        if self.item:
            return self.item.specs_text
        return self.manual_specs

    @property
    def display_vendor_name(self) -> str:
        if self.item and self.item.vendor:
            return self.item.vendor.name
        return self.manual_vendor_name or "Поставщик по договору"

    @property
    def unit_cost_rub(self) -> Decimal:
        """Себестоимость 1 ед. в рублях с учетом скидки поставщика и валютного буфера (ЦБ + 3-5%)[cite: 2]"""
        fx_buf = Decimal('1') + (Decimal(str(self.estimate.fx_buffer_pct or 0)) / Decimal('100'))
        if self.item:
            vendor = self.item.vendor
            discount = vendor.protected_discount if self.is_protected else vendor.base_discount
            discount_factor = Decimal('1') - (Decimal(str(discount)) / Decimal('100'))
            cost_in_curr = Decimal(str(self.item.retail_price)) * discount_factor
            rate = Decimal(str(vendor.currency.rate))
            if vendor.currency.code == 'RUB':
                fx_buf = Decimal('1')
            return (cost_in_curr * rate * fx_buf).quantize(Decimal('0.01'))
        else:
            rate_obj = CurrencyRate.objects.filter(code=self.manual_currency_code).first()
            rate = Decimal(str(rate_obj.rate)) if rate_obj else Decimal('1.00')
            if self.manual_currency_code == 'RUB':
                fx_buf = Decimal('1')
            return (Decimal(str(self.manual_purchase_price)) * rate * fx_buf).quantize(Decimal('0.01'))

    @property
    def line_cost_rub(self) -> Decimal:
        return (self.unit_cost_rub * self.quantity).quantize(Decimal('0.01'))

    @property
    def line_actual_cost_rub(self) -> Decimal:
        unit_act = self.actual_unit_cost_rub if self.actual_unit_cost_rub > Decimal('0') else self.unit_cost_rub
        return (unit_act * self.quantity).quantize(Decimal('0.01'))

    @property
    def base_client_line_rub(self) -> Decimal:
        """Сумма строки с наценкой и комиссией площадки до распределения доставки и монтажа"""
        markup_factor = Decimal('1') + (Decimal(str(self.markup_pct)) / Decimal('100'))
        fee_factor = Decimal('1') + (Decimal(str(self.estimate.platform_fee_pct or 0)) / Decimal('100'))
        coeff = Decimal(str(self.estimate.hidden_overhead_coeff or 1))
        return (self.line_cost_rub * coeff * fee_factor * markup_factor).quantize(Decimal('0.01'))

    @property
    def client_line_total_rub(self) -> Decimal:
        """Итоговая цена строки для заказчика (с пропорционально вшитой доставкой и монтажом)[cite: 2]"""
        base_line = self.base_client_line_rub
        overhead = self.estimate.total_embedded_overhead_rub
        if overhead > Decimal('0.00'):
            total_base = self.estimate.raw_lines_base_client_rub
            if total_base > Decimal('0.00'):
                share = overhead * (base_line / total_base)
                return (base_line + share).quantize(Decimal('0.01'))
        return base_line

    @property
    def client_unit_price_rub(self) -> Decimal:
        """Цена за 1 шт. в КП для заказчика (включая вшитые расходы и НДС)"""
        if self.quantity <= 0:
            return Decimal('0.00')
        return (self.client_line_total_rub / Decimal(self.quantity)).quantize(Decimal('0.01'))

    @property
    def vat_amount_rub(self) -> Decimal:
        """Сумма НДС в том числе внутри отпускной цены строки (например, 22/122)[cite: 1, 2]"""
        rate = Decimal(str(self.vat_rate or 0))
        if rate <= Decimal('0.00'):
            return Decimal('0.00')
        return (self.client_line_total_rub * rate / (Decimal('100') + rate)).quantize(Decimal('0.01'))


class DellinShipment(models.Model):
    """Отгрузки по трек-номерам через API ТК «Деловые Линии» и «СДЭК» (с разделением по поставщикам)[cite: 2]"""
    CARRIER_CHOICES = (
        ('dellin', 'ТК «Деловые Линии»'),
        ('cdek', 'Курьерская служба «СДЭК»'),
    )
    DELIVERY_STATUS = (
        ('accepted', 'Принят на терминале отправителя'),
        ('in_transit', 'В пути (межтерминальная перевозка)'),
        ('customs', 'Таможенное оформление / СВХ'),
        ('arrived', 'Груз прибыл на терминал / ПВЗ Санкт-Петербург'),
        ('delivered', 'Доставлено на склад ООО «Юстейдж» (Харченко, 18)'),
    )

    carrier = models.CharField("Транспортная компания", max_length=20, choices=CARRIER_CHOICES, default='dellin')
    tracking_number = models.CharField("Трек-номер / Накладная", max_length=60, unique=True)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='dellin_shipments', verbose_name="Проект")
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, verbose_name="Поставщик груза")
    equipment_summary = models.CharField(
        "Оборудование в поставке (можно редактировать)",
        max_length=255,
        default="Партия сценического оборудования по спецификации проекта"
    )
    route_info = models.CharField("Маршрут следования", max_length=160, default="Москва — Санкт-Петербург (Склад Харченко, 18)")
    cargo_weight_kg = models.DecimalField("Масса груза, кг", max_digits=8, decimal_places=1, default=420.0)
    status = models.CharField("Статус доставки по API", max_length=25, choices=DELIVERY_STATUS, default='in_transit')
    eta_date = models.DateField("Ожидаемая дата прибытия", default=timezone.now)
    is_auto_created = models.BooleanField("Создано автоматически из Канбана", default=False)
    last_checked_at = models.DateTimeField("Последний опрос API", auto_now=True)

    class Meta:
        ordering = ['eta_date', '-id']
        verbose_name = "Отгрузка (Деловые Линии / СДЭК)"
        verbose_name_plural = "5. Логистика: ТК «Деловые Линии» и «СДЭК»"

    def __str__(self):
        return f"[{self.get_carrier_display()}: {self.tracking_number}] {self.project.title}"


class VenueCalculationProfile(models.Model):
    """Справочник суточных нормативов выработки для разных типов театров и залов[cite: 2]"""
    name = models.CharField("Тип площадки / Профиль театра", max_length=150, unique=True)
    daily_hours = models.DecimalField(
        "Среднесуточная наработка оборудования (часов в сутки)",
        max_digits=4,
        decimal_places=2,
        default=Decimal('4.50')
    )
    description = models.CharField("Описание режима репетиций и спектаклей", max_length=250, blank=True, default="")

    class Meta:
        ordering = ['id']
        verbose_name = "Профиль расчета наработки театра"
        verbose_name_plural = "6. Справочник профилей театров (расчет моточасов)"

    def __str__(self):
        return f"{self.name} ({self.daily_hours} ч/сутки)"


class DigitalStageAsset(models.Model):
    """Цифровой паспорт объекта (Digital Stage Twin) — учет приборов по S/N и расчет ресурса по профилю театра[cite: 2]"""
    serial_number = models.CharField("Индивидуальный серийный номер (S/N)", max_length=80, unique=True)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='stage_assets',
        null=True,
        blank=True,
        verbose_name="Проект оснащения"
    )
    venue_name = models.CharField("Объект / Театр (можно задать вручную)", max_length=200, default="")
    item = models.ForeignKey(Item, on_delete=models.CASCADE, verbose_name="Установленный прибор")
    zone_installed = models.CharField("Точка подвеса на сцене", max_length=120, default="Софит №1 / Портал сцены")
    installed_date = models.DateField("Дата ввода в эксплуатацию", default=timezone.now)
    venue_profile = models.ForeignKey(
        VenueCalculationProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Профиль эксплуатации театра"
    )
    current_hours = models.PositiveIntegerField("Базовая наработка при монтаже, ч", default=0)
    max_resource_hours = models.PositiveIntegerField("Паспортный ресурс до замены / ТО, ч", default=2000)
    is_auto_created = models.BooleanField("Добавлено автоматически при завершении проекта", default=False)

    class Meta:
        ordering = ['-id']
        verbose_name = "Единица оборудования в Цифровом паспорте"
        verbose_name_plural = "7. Цифровой паспорт объектов (Digital Stage Twin)"

    @property
    def display_venue(self) -> str:
        if self.venue_name and self.venue_name.strip():
            return self.venue_name.strip()
        if self.project:
            return f"{self.project.client} ({self.project.title})"
        return "Площадка на сервисном обслуживании"

    @property
    def calculated_hours(self) -> int:
        """Автоматический расчет выработки моточасов от даты установки по суточному нормативу профиля театра[cite: 2]"""
        today = timezone.now().date()
        days_active = max((today - self.installed_date).days, 1)
        daily_norm = self.venue_profile.daily_hours if self.venue_profile else Decimal('4.00')
        total = Decimal(self.current_hours) + (Decimal(days_active) * daily_norm)
        return int(total)

    @property
    def wear_pct(self) -> int:
        if self.max_resource_hours == 0:
            return 100
        return min(int((self.calculated_hours / self.max_resource_hours) * 100), 100)

    @property
    def needs_service(self) -> bool:
        return self.wear_pct >= 80

    def __str__(self):
        return f"{self.item.name} [S/N: {self.serial_number}] — {self.wear_pct}%"