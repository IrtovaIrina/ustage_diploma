from decimal import Decimal
from django.db import models
from django.contrib.auth.models import AbstractUser


class StaffPosition(models.Model):
    code = models.CharField(max_length=30, unique=True, verbose_name='Код роли')
    title = models.CharField(max_length=150, verbose_name='Наименование должности')
    is_management = models.BooleanField(default=False, verbose_name='Руководитель')
    is_sales_manager = models.BooleanField(default=True, verbose_name='Менеджер по продажам')

    view_kanban = models.BooleanField(default=True, verbose_name='Просмотр: Канбан')
    edit_kanban = models.BooleanField(default=True, verbose_name='Редактирование: Канбан')
    view_estimates = models.BooleanField(default=True, verbose_name='Просмотр: Сметы и ТЗ')
    edit_estimates = models.BooleanField(default=True, verbose_name='Редактирование: Сметы и ТЗ')
    view_catalog = models.BooleanField(default=True, verbose_name='Просмотр: Каталог')
    edit_catalog = models.BooleanField(default=False, verbose_name='Редактирование: Импорт прайсов')
    view_logistics = models.BooleanField(default=True, verbose_name='Просмотр: Логистика')
    edit_logistics = models.BooleanField(default=False, verbose_name='Редактирование: Логистика')
    view_twin = models.BooleanField(default=True, verbose_name='Просмотр: Цифровой паспорт')
    edit_twin = models.BooleanField(default=False, verbose_name='Редактирование: Цифровой паспорт')

    view_currencies = models.BooleanField(default=True, verbose_name='Просмотр: Мультивалютный контур')
    edit_currencies = models.BooleanField(default=False, verbose_name='Редактирование/Удаление: Мультивалютный контур')

    view_vendors = models.BooleanField(default=True, verbose_name='Просмотр: Реестр поставщиков')
    edit_vendors = models.BooleanField(default=False, verbose_name='Редактирование/Удаление: Реестр поставщиков')

    view_venue_profiles = models.BooleanField(default=True, verbose_name='Просмотр: Справочник профилей театров')
    edit_venue_profiles = models.BooleanField(default=False, verbose_name='Редактирование/Удаление: Справочник профилей театров')

    def __str__(self):
        return self.title


class CustomUser(AbstractUser):
    ROLE_CHOICES = (
        ('ceo', 'Генеральный директор'),
        ('rop', 'Руководитель отдела продаж'),
        ('lead_sales', 'Ведущий менеджер по продажам'),
        ('sales', 'Менеджер отдела продаж'),
        ('tech_head', 'Руководитель технического отдела'),
        ('engineer', 'Инженер сценического оборудования'),
        ('coordinator', 'Координатор проектов'),
        ('warehouse', 'Кладовщик'),
    )
    role = models.CharField(max_length=30, default='sales', verbose_name='Системный код роли')
    position = models.ForeignKey(
        StaffPosition, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='employees', verbose_name='Должность по штату'
    )
    tab_number = models.CharField(max_length=15, default='00010', verbose_name='Табельный номер')
    can_manage_roles = models.BooleanField(
        default=False,
        verbose_name='Право управления ролями и штатом'
    )

    # Пункт 2: Плановые показатели менеджера (задаются руководителем)
    monthly_sales_plan_rub = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal('12000000.00'),
        verbose_name='План продаж по проектам, ₽'
    )
    projects_plan_count = models.PositiveIntegerField(
        default=3,
        verbose_name='План по количеству проектов, шт.'
    )
    tasks_plan_count = models.PositiveIntegerField(
        default=4,
        verbose_name='План по закрытым задачам, шт.'
    )

    @property
    def position_title(self) -> str:
        if self.position:
            return self.position.title
        return dict(self.ROLE_CHOICES).get(self.role, self.role)

    @property
    def is_ceo(self) -> bool:
        return self.role == 'ceo' or (self.position and self.position.code == 'ceo') or self.is_superuser

    @property
    def is_boss_role(self) -> bool:
        if self.position:
            return self.position.is_management or self.is_ceo
        return self.role in ('ceo', 'rop', 'tech_head') or self.is_superuser

    @property
    def can_admin_staff_and_roles(self) -> bool:
        return self.is_ceo or self.can_manage_roles or self.role == 'rop'

    @property
    def can_create_projects(self) -> bool:
        pos_code = self.position.code if self.position else self.role
        return pos_code not in ('engineer', 'warehouse')

    @property
    def can_view_currencies_block(self) -> bool:
        return self.is_ceo or not self.position or self.position.view_currencies

    @property
    def can_edit_currencies_block(self) -> bool:
        return self.is_ceo or (self.position and self.position.edit_currencies)

    @property
    def can_view_vendors_registry(self) -> bool:
        return self.is_ceo or not self.position or self.position.view_vendors

    @property
    def can_edit_vendors_registry(self) -> bool:
        return self.is_ceo or (self.position and self.position.edit_vendors)

    @property
    def can_view_venue_profiles_block(self) -> bool:
        return self.is_ceo or not self.position or self.position.view_venue_profiles

    @property
    def can_edit_venue_profiles_block(self) -> bool:
        return self.is_ceo or (self.position and self.position.edit_venue_profiles)

    @property
    def can_import_prices(self) -> bool:
        return self.is_ceo or (self.position and self.position.edit_catalog)

    @property
    def is_sales_mgr(self) -> bool:
        if self.position:
            return self.position.is_sales_manager and not self.position.is_management
        return self.role in ('lead_sales', 'sales')

    def __str__(self):
        return f"{self.first_name or self.username} ({self.position_title})"


class CurrencyRate(models.Model):
    code = models.CharField(max_length=5, unique=True, verbose_name='Код валюты ЦБ РФ')
    name = models.CharField(max_length=80, default='Валюта', verbose_name='Наименование валюты')
    nominal = models.PositiveIntegerField(default=1, verbose_name='Номинал ЦБ')
    rate = models.DecimalField(max_digits=10, decimal_places=4, verbose_name='Курс ЦБ РФ к рублю')
    is_active = models.BooleanField(default=True, verbose_name='Выгружена на экран')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['code']

    def __str__(self):
        return f"{self.code} — {self.name} ({self.rate} ₽)"


class Vendor(models.Model):
    name = models.CharField(max_length=150, verbose_name='Поставщик / Дистрибьютор')
    country = models.CharField(max_length=80, default='Китай', verbose_name='Страна')
    contact_info = models.CharField(max_length=150, default='sale@ustage-group.ru', verbose_name='Контакты')
    currency = models.ForeignKey(CurrencyRate, on_delete=models.PROTECT, verbose_name='Валюта прайса')
    base_discount = models.DecimalField(max_digits=5, decimal_places=2, default=15.00, verbose_name='Базовая скидка (%)')
    protected_discount = models.DecimalField(max_digits=5, decimal_places=2, default=32.00, verbose_name='Скидка Защиты проекта (%)')

    def __str__(self):
        return f"{self.name} ({self.country}, {self.currency.code})"


class Category(models.Model):
    code = models.CharField(max_length=30, unique=True, verbose_name='Код раздела')
    name = models.CharField(max_length=150, verbose_name='Раздел каталога ustage-group.ru')
    default_target_margin_pct = models.DecimalField(max_digits=5, decimal_places=2, default=25.00, verbose_name='Целевая наценка (%)')

    def __str__(self):
        return self.name


class SubCategory(models.Model):
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='subcategories', verbose_name='Раздел')
    code = models.CharField(max_length=40, unique=True, verbose_name='Код подраздела')
    name = models.CharField(max_length=150, verbose_name='Подраздел ustage-group.ru')
    calc_norm_per_100_seats = models.PositiveIntegerField(default=2, verbose_name='Норматив калькулятора')

    def __str__(self):
        return f"{self.category.name} → {self.name}"


class Item(models.Model):
    STOCK_CHOICES = (
        ('in_stock', 'В наличии у поставщика'),
        ('low_stock', 'Мало'),
        ('out_of_stock', 'Нет в наличии'),
    )
    sku = models.CharField(max_length=60, unique=True, verbose_name='Артикул')
    brand = models.CharField(max_length=100, verbose_name='Бренд')
    name = models.CharField(max_length=255, verbose_name='Наименование с сайта ustage-group.ru')
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='items', verbose_name='Раздел')
    subcategory = models.ForeignKey(SubCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name='items', verbose_name='Подраздел')
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name='items', verbose_name='Поставщик')

    retail_price = models.DecimalField(max_digits=12, decimal_places=2, default=1000.00, verbose_name='Розничная цена')
    discount_pct = models.DecimalField(max_digits=5, decimal_places=2, default=15.00, verbose_name='Скидка (%)')
    purchase_price = models.DecimalField(max_digits=12, decimal_places=2, default=850.00, verbose_name='Закупочная цена')

    stock_status = models.CharField(max_length=20, choices=STOCK_CHOICES, default='in_stock', verbose_name='Наличие')
    stock_qty = models.PositiveIntegerField(default=15, verbose_name='Остаток у поставщика')

    recommended_analog = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, verbose_name='Аналог')
    specs_text = models.CharField(max_length=350, default='Мощность 400 Вт, вес 12 кг', verbose_name='Характеристики')
    technical_specs = models.JSONField(default=dict, blank=True, verbose_name='ТТХ JSON')

    @property
    def base_price(self) -> Decimal:
        return self.retail_price

    def __str__(self):
        return f"[{self.sku}] {self.brand} {self.name}"