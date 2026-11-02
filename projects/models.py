from decimal import Decimal, ROUND_HALF_UP
from datetime import date
from django.db import models
from catalog.models import CustomUser, Vendor, Item, CurrencyRate


class KanbanColumn(models.Model):
    order = models.PositiveIntegerField(default=1, verbose_name="Порядок")
    title = models.CharField(max_length=120, verbose_name="Название раздела Канбана")
    color_hex = models.CharField(max_length=20, default="#0066CC", verbose_name="Цвет полосы")
    is_logistics_stage = models.BooleanField(default=False, verbose_name="Отслеживание поставки")
    is_completed_stage = models.BooleanField(default=False, verbose_name="Завершено")

    class Meta:
        ordering = ['order', 'id']
        verbose_name = "Раздел Канбана"
        verbose_name_plural = "Разделы Канбана"

    def __str__(self):
        return self.title


class Project(models.Model):
    title = models.CharField(max_length=255, verbose_name="Наименование проекта")
    client = models.CharField(max_length=255, verbose_name="Заказчик (Театр / ДК)")
    client_budget_rub = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal('0.00'),
        verbose_name="Бюджет, который готов потратить театр (₽)"
    )
    kanban_column = models.ForeignKey(
        KanbanColumn, on_delete=models.SET_NULL, null=True, blank=True, related_name='projects'
    )
    manager = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name='managed_projects'
    )
    theater_tz_text = models.TextField(blank=True, default="", verbose_name="Текст ТЗ от театра")
    stage_width = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('14.00'))
    stage_depth = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('10.00'))
    stage_height = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('7.00'))
    hall_seats = models.PositiveIntegerField(default=400)
    max_rigging_weight_kg = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal('6500.00'))
    max_power_kw = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal('220.00'))
    is_delivery_arrived = models.BooleanField(default=False)
    is_engineered_checked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def shipments_summary(self):
        qs = self.shipments.all()
        total = qs.count()
        if total == 0:
            return None
        arrived = qs.filter(status__in=['arrived_terminal', 'delivered_warehouse']).count()
        pending = qs.exclude(status__in=['arrived_terminal', 'delivered_warehouse']).order_by('eta_date')
        next_eta = pending.first().eta_date if pending.exists() else None
        return {
            'total': total,
            'arrived': arrived,
            'all_arrived': arrived == total and total > 0,
            'next_eta': next_eta,
        }

    def __str__(self):
        return self.title


class KanbanTask(models.Model):
    project = models.ForeignKey(
        Project, on_delete=models.SET_NULL, null=True, blank=True, related_name='tasks'
    )
    project_name_manual = models.CharField(max_length=255, blank=True, default="")
    kanban_column = models.ForeignKey(
        KanbanColumn, on_delete=models.SET_NULL, null=True, blank=True, related_name='tasks'
    )
    assigned_to = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_tasks'
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    due_date = models.DateField(default=date.today)
    is_completed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


class TaskComment(models.Model):
    task = models.ForeignKey(KanbanTask, on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']


class TaskAttachment(models.Model):
    task = models.ForeignKey(KanbanTask, on_delete=models.CASCADE, related_name='attachments')
    uploaded_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True)
    file = models.FileField(upload_to='task_files/')
    filename = models.CharField(max_length=255)
    uploaded_at = models.DateTimeField(auto_now_add=True)


class Estimate(models.Model):
    project = models.OneToOneField(Project, on_delete=models.CASCADE, related_name='estimate')
    version = models.PositiveIntegerField(default=1)
    ceo_force_unblock_kp = models.BooleanField(
        default=False, verbose_name="Разблокировано решением Гендиректора"
    )
    fx_buffer_pct = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('4.00'),
        verbose_name="Валютный буфер к курсу ЦБ РФ (%)"
    )
    platform_fee_pct = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('1.50'),
        verbose_name="Комиссия площадки / ЭТП (%)"
    )
    delivery_cost_rub = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('35000.00'),
        verbose_name="Доставка и доп. расходы на логистику (₽)"
    )
    installation_cost_rub = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'),
        verbose_name="Стоимость монтажа и ПНР (₽)"
    )
    embed_hidden_costs = models.BooleanField(
        default=True, verbose_name="Вшить доставку и монтаж в цену позиций КП"
    )
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def base_lines_cost_rub(self):
        total = Decimal('0.00')
        for line in self.lines.all():
            total += line.planned_line_cost_rub
        return total.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    @property
    def hidden_extra_rub_total(self):
        extra = self.delivery_cost_rub or Decimal('0.00')
        if self.embed_hidden_costs:
            extra += (self.installation_cost_rub or Decimal('0.00'))
        return extra.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    @property
    def total_planned_cost_rub(self):
        equip_cost = self.base_lines_cost_rub
        total_cost = equip_cost + (self.delivery_cost_rub or Decimal('0.00')) + (self.installation_cost_rub or Decimal('0.00'))
        fee_mult = Decimal('1.00') + ((self.platform_fee_pct or Decimal('0.00')) / Decimal('100.00'))
        return (total_cost * fee_mult).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    @property
    def total_client_price_rub(self):
        total = Decimal('0.00')
        for line in self.lines.all():
            total += line.client_line_total_rub
        if not self.embed_hidden_costs and (self.installation_cost_rub or Decimal('0.00')) > Decimal('0.00'):
            total += self.installation_cost_rub
        return total.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    @property
    def total_vat_rub(self):
        total = Decimal('0.00')
        for line in self.lines.all():
            total += line.vat_amount_rub
        if not self.embed_hidden_costs and (self.installation_cost_rub or Decimal('0.00')) > Decimal('0.00'):
            total += (self.installation_cost_rub * Decimal('22.00') / Decimal('122.00'))
        return total.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    @property
    def total_without_vat_rub(self):
        return (self.total_client_price_rub - self.total_vat_rub).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )

    @property
    def gross_margin_rub(self):
        return (self.total_client_price_rub - self.total_planned_cost_rub).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )

    @property
    def gross_margin_pct(self):
        client_total = self.total_client_price_rub
        if client_total <= Decimal('0.00'):
            return Decimal('0.0')
        pct = ((client_total - self.total_planned_cost_rub) / client_total) * Decimal('100.0')
        return pct.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)

    @property
    def protection_ratio_pct(self):
        qs = self.lines.all()
        total_cnt = qs.count()
        if total_cnt == 0:
            return Decimal('100.0')
        prot_cnt = qs.filter(is_protected=True).count()
        pct = (Decimal(prot_cnt) / Decimal(total_cnt)) * Decimal('100.0')
        return pct.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)

    @property
    def is_kp_export_allowed(self):
        return self.ceo_force_unblock_kp or (self.protection_ratio_pct >= Decimal('70.0'))

    @property
    def is_budget_exceeded(self):
        budget = self.project.client_budget_rub or Decimal('0.00')
        if budget <= Decimal('0.00'):
            return False
        return self.total_client_price_rub > budget

    @property
    def budget_overrun_rub(self):
        budget = self.project.client_budget_rub or Decimal('0.00')
        if budget <= Decimal('0.00') or self.total_client_price_rub <= budget:
            return Decimal('0.00')
        return (self.total_client_price_rub - budget).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


class EstimateLine(models.Model):
    estimate = models.ForeignKey(Estimate, on_delete=models.CASCADE, related_name='lines')
    item = models.ForeignKey(Item, on_delete=models.SET_NULL, null=True, blank=True)
    is_manual_item = models.BooleanField(default=False, verbose_name="Добавлено вручную")
    manual_name = models.CharField(max_length=255, blank=True, default="", verbose_name="Наименование (ручной ввод)")
    manual_specs = models.TextField(blank=True, default="", verbose_name="ТТХ позиции (ручной ввод)")
    manual_vendor_name = models.CharField(max_length=200, blank=True, default="", verbose_name="Поставщик (ручной ввод)")
    manual_currency_code = models.CharField(max_length=10, blank=True, default="RUB", verbose_name="Код валюты")
    manual_unit_price = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'), verbose_name="Закупочная цена (в валюте)"
    )

    quantity = models.PositiveIntegerField(default=1)
    is_protected = models.BooleanField(default=True)
    markup_pct = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('25.00'))
    vat_rate_pct = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('22.00'), verbose_name="Ставка НДС (%)"
    )
    is_auto_rigged = models.BooleanField(default=False)

    @property
    def display_name(self):
        if self.item and not self.is_manual_item:
            return f"{self.item.brand} {self.item.name}"
        return self.manual_name or "Нестандартная позиция оборудования"

    @property
    def display_specs(self):
        if self.item and not self.is_manual_item:
            return self.item.specs_text
        return self.manual_specs or "Параметры заданы индивидуально по ТЗ"

    @property
    def display_vendor(self):
        if self.item and not self.is_manual_item and self.item.vendor:
            return self.item.vendor.name
        return self.manual_vendor_name or "Индивидуальный поставщик"

    @property
    def currency_rate_with_buffer(self):
        fx_buf = Decimal('1.00') + ((self.estimate.fx_buffer_pct or Decimal('0.00')) / Decimal('100.00'))
        if self.item and not self.is_manual_item and self.item.vendor and self.item.vendor.currency:
            code = self.item.vendor.currency.code
            rate = self.item.vendor.currency.rate
        else:
            code = (self.manual_currency_code or 'RUB').upper()
            curr_obj = CurrencyRate.objects.filter(code=code).first()
            rate = curr_obj.rate if curr_obj else Decimal('1.0000')
        if code == 'RUB':
            return Decimal('1.0000')
        return (rate * fx_buf).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)

    @property
    def planned_unit_cost_rub(self):
        rate_eff = self.currency_rate_with_buffer
        if self.item and not self.is_manual_item:
            vendor = self.item.vendor
            disc = vendor.protected_discount if self.is_protected else vendor.base_discount
            unit_curr = self.item.retail_price * (Decimal('1.00') - (disc / Decimal('100.00')))
        else:
            unit_curr = self.manual_unit_price or Decimal('0.00')
        return (unit_curr * rate_eff).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    @property
    def planned_line_cost_rub(self):
        return (self.planned_unit_cost_rub * Decimal(self.quantity)).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )

    @property
    def client_line_total_rub(self):
        base_cost = self.planned_line_cost_rub
        fee_mult = Decimal('1.00') + ((self.estimate.platform_fee_pct or Decimal('0.00')) / Decimal('100.00'))
        markup_mult = Decimal('1.00') + ((self.markup_pct or Decimal('0.00')) / Decimal('100.00'))
        direct_client_sum = base_cost * fee_mult * markup_mult

        total_equip_cost = Decimal('0.00')
        for l in self.estimate.lines.all():
            total_equip_cost += l.planned_line_cost_rub
        embedded_share = Decimal('0.00')
        if total_equip_cost > Decimal('0.00'):
            embedded_share = self.estimate.hidden_extra_rub_total * (base_cost / total_equip_cost)

        return (direct_client_sum + embedded_share).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    @property
    def client_unit_price_rub(self):
        if self.quantity <= 0:
            return Decimal('0.00')
        return (self.client_line_total_rub / Decimal(self.quantity)).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )

    @property
    def vat_amount_rub(self):
        vat = self.vat_rate_pct or Decimal('0.00')
        if vat <= Decimal('0.00'):
            return Decimal('0.00')
        amt = self.client_line_total_rub * vat / (Decimal('100.00') + vat)
        return amt.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


class DellinShipment(models.Model):
    CARRIER_CHOICES = [
        ('dellin', 'ТК «Деловые Линии»'),
        ('cdek', 'Курьерская служба «СДЭК»'),
    ]
    STATUS_CHOICES = [
        ('awaiting_dispatch', 'Ожидает отгрузки от поставщика'),
        ('in_transit', 'В пути между терминалами'),
        ('arrived_terminal', 'Груз прибыл на терминал / ПВЗ Санкт-Петербург'),
        ('delivered_warehouse', 'Доставлено на склад ООО «Юстейдж» (СПб)'),
    ]
    carrier = models.CharField(max_length=20, choices=CARRIER_CHOICES, default='dellin')
    tracking_number = models.CharField(max_length=80)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='shipments')
    vendor = models.ForeignKey(Vendor, on_delete=models.SET_NULL, null=True, blank=True)
    equipment_summary = models.TextField(default="")
    route_info = models.CharField(max_length=255, default="Отгрузка со склада поставщика → Санкт-Петербург")
    cargo_weight_kg = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal('45.00'))
    status = models.CharField(max_length=40, choices=STATUS_CHOICES, default='in_transit')
    eta_date = models.DateField(default=date.today)
    is_auto_created = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.project_id:
            all_ships = DellinShipment.objects.filter(project_id=self.project_id)
            arrived_any = all_ships.filter(status__in=['arrived_terminal', 'delivered_warehouse']).exists()
            Project.objects.filter(id=self.project_id).update(is_delivery_arrived=arrived_any)


class VenueCalculationProfile(models.Model):
    name = models.CharField(max_length=160, verbose_name="Профиль театра / площадки")
    daily_hours = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('4.00'))

    def __str__(self):
        return self.name


class DigitalStageAsset(models.Model):
    serial_number = models.CharField(max_length=100)
    project = models.ForeignKey(Project, on_delete=models.SET_NULL, null=True, blank=True, related_name='stage_assets')
    venue_name = models.CharField(max_length=255, blank=True, default="")
    item = models.ForeignKey(Item, on_delete=models.CASCADE)
    zone_installed = models.CharField(max_length=160, default="Верхняя механизация / Софит №1")
    installed_date = models.DateField(default=date.today)
    venue_profile = models.ForeignKey(VenueCalculationProfile, on_delete=models.SET_NULL, null=True, blank=True)
    max_resource_hours = models.PositiveIntegerField(default=2000)
    is_auto_created = models.BooleanField(default=False)

    @property
    def operating_hours(self):
        days = max((date.today() - self.installed_date).days, 1)
        daily = self.venue_profile.daily_hours if self.venue_profile else Decimal('4.00')
        return int((Decimal(days) * daily).quantize(Decimal('1'), rounding=ROUND_HALF_UP))

    @property
    def wear_percentage(self):
        if self.max_resource_hours <= 0:
            return 0
        pct = int((self.operating_hours / self.max_resource_hours) * 100)
        return min(pct, 100)

    @property
    def predictive_status(self):
        w = self.wear_percentage
        if w >= 80:
            return {'color': 'red', 'label': f'КРИТИЧЕСКИЙ ИЗНОС {w}% — Требуется замена / ТО'}
        elif w >= 55:
            return {'color': 'yellow', 'label': f'ПЛАНОВЫЙ КОНТРОЛЬ {w}% — Рекомендовать ТО'}
        return {'color': 'green', 'label': f'РЕСУРС В НОРМЕ {w}%'}