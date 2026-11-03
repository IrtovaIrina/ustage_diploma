from decimal import Decimal
from django.db import models
from django.contrib.auth.models import AbstractUser


class StaffPosition(models.Model):
    """Справочник ролей и должностей ООО 'Юстейдж' с гибкой матрицей прав доступа[cite: 2]"""
    code = models.CharField("Код роли", max_length=30, unique=True)
    title = models.CharField("Название должности", max_length=120)
    is_management = models.BooleanField("Руководящая должность (видит планы и статистику)", default=False)
    is_sales_manager = models.BooleanField("Менеджер по продажам (участвует в статистике KPI)", default=False)

    # Права на 1. Канбан-доску[cite: 2]
    can_view_kanban = models.BooleanField("Просмотр Канбан-доски", default=True)
    can_create_project = models.BooleanField("Создание проектов на Канбане", default=False)
    can_create_task = models.BooleanField("Создание рабочих задач на Канбане", default=True)
    can_manage_kanban_columns = models.BooleanField("Настройка разделов (колонок) Канбана", default=False)

    # Права на Сметы и ТЗ[cite: 2]
    can_view_estimates = models.BooleanField("Просмотр смет и ТЗ проектов", default=True)
    can_edit_estimates = models.BooleanField("Редактирование смет, ТЗ и калькуляторов", default=False)
    can_approve_engineering = models.BooleanField("Инженерное визирование проектов", default=False)

    # Права на 2. Каталог, Прайсы, Валюты и Поставщиков[cite: 2]
    can_view_catalog = models.BooleanField("Просмотр каталога ustage-group.ru", default=True)
    can_import_prices = models.BooleanField("Импорт прайс-листов Excel и ред. каталога", default=False)
    can_view_currencies = models.BooleanField("Просмотр Мультивалютного контура ЦБ РФ", default=True)
    can_edit_currencies = models.BooleanField("Управление валютами в Мультивалютном контуре", default=False)
    can_view_vendors = models.BooleanField("Просмотр Реестра поставщиков", default=True)
    can_edit_vendors = models.BooleanField("Редактирование и удаление поставщиков", default=False)

    # Права на 3. Логистику[cite: 2]
    can_view_logistics = models.BooleanField("Просмотр модуля Логистики (Деловые Линии / СДЭК)", default=True)
    can_edit_logistics = models.BooleanField("Управление поставками и трек-номерами", default=False)

    # Права на 4. Цифровой паспорт и Профили театров[cite: 2]
    can_view_digital_twin = models.BooleanField("Просмотр Цифрового паспорта объекта", default=True)
    can_edit_digital_twin = models.BooleanField("Редактирование оборудования по S/N в Паспорте", default=False)
    can_view_venue_profiles = models.BooleanField("Просмотр Справочника профилей театров", default=True)
    can_edit_venue_profiles = models.BooleanField("Редактирование и удаление профилей театров", default=False)

    # Права на 5. Управление штатом и ролями[cite: 2]
    can_manage_staff = models.BooleanField("Управление штатом сотрудников и ролями", default=False)

    class Meta:
        verbose_name = "Должность / Роль"
        verbose_name_plural = "1. Справочник ролей и должностей (Матрица прав)"
        ordering = ['id']

    def __str__(self):
        return self.title


class CustomUser(AbstractUser):
    """Модель сотрудника ООО 'Юстейдж' со связью с должностью и планами KPI[cite: 2]"""
    ROLE_CHOICES = (
        ('ceo', 'Генеральный директор'),
        ('sales_head', 'Руководитель отдела продаж'),
        ('Wait_lead', 'Ведущий менеджер по продажам'),
        ('sales_mgr', 'Менеджер отдела продаж'),
        ('tech_head', 'Руководитель технического отдела'),
        ('engineer', 'Инженер сценического оборудования'),
        ('coordinator', 'Координатор проектов'),
        ('warehouse', 'Кладовщик'),
    )
    role = models.CharField("Системный код роли", max_length=25, choices=ROLE_CHOICES, default='sales_mgr')
    position = models.ForeignKey(
        StaffPosition,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='employees',
        verbose_name="Должность в штатном расписании"
    )
    tab_number = models.CharField("Табельный номер", max_length=20, default="00012")
    phone = models.CharField("Внутренний телефон", max_length=30, default="+7 (812) 409-49-91")
    department = models.CharField("Подразделение", max_length=100, default="Коммерческий отдел")
    can_manage_roles = models.BooleanField(
        "Делегировано право управления штатом и ролями (от Гендиректора)",
        default=False
    )
    password_reset_code = models.CharField("Одноразовый код сброса пароля", max_length=12, blank=True, default="")

    # Плановые показатели KPI для менеджеров (задаются руководством)[cite: 2]
    monthly_sales_plan_rub = models.DecimalField(
        "План продаж на месяц, ₽",
        max_digits=14,
        decimal_places=2,
        default=Decimal('5000000.00')
    )
    projects_plan_count = models.PositiveIntegerField("План по проектам (шт.)", default=3)
    tasks_plan_count = models.PositiveIntegerField("План по закрытым задачам (шт.)", default=5)

    class Meta:
        verbose_name = "Сотрудник"
        verbose_name_plural = "2. Штат сотрудников ООО «Юстейдж»"

    def get_role_title(self) -> str:
        if self.position:
            return self.position.title
        return dict(self.ROLE_CHOICES).get(self.role, self.role)

    @property
    def is_ceo(self) -> bool:
        if self.position and self.position.code == 'ceo':
            return True
        return self.role == 'ceo' or self.is_superuser

    @property
    def is_boss_role(self) -> bool:
        if self.position:
            return self.position.is_management or self.is_ceo
        return self.role in ('ceo', 'sales_head', 'tech_head') or self.is_superuser

    @property
    def is_boss(self) -> bool:
        """Алиас для совместимости с вызовами в представлениях и шаблонах[cite: 1]"""
        return self.is_boss_role

    @property
    def can_admin_staff_and_roles(self) -> bool:
        if self.is_ceo or self.can_manage_roles:
            return True
        if self.position and self.position.can_manage_staff:
            return True
        return False

    @property
    def can_admin_roles(self) -> bool:
        """Алиас для проверки прав администрирования ролей и штата[cite: 1]"""
        return self.can_admin_staff_and_roles

    def can_perm(self, perm_code: str) -> bool:
        """
        Универсальная проверка гранулярного права доступа по коду[cite: 1].
        Поддерживает вызовы как с префиксом 'can_', так и без него (например, 'edit_estimates').
        """
        if self.is_superuser or self.is_ceo:
            return True

        attr_name = perm_code if perm_code.startswith('can_') else f'can_{perm_code}'

        # Если у пользователя сам класс имеет такое свойство (например, can_create_project_card)
        if hasattr(self, attr_name) and attr_name != 'can_perm':
            val = getattr(self, attr_name)
            return val() if callable(val) else bool(val)

        if self.position and hasattr(self.position, attr_name):
            return bool(getattr(self.position, attr_name))

        # Резервные правила по умолчанию, если должность не привязана
        fallback_map = {
            'can_view_kanban': True,
            'can_create_project': self.role in ('ceo', 'sales_head', 'Wait_lead', 'sales_mgr', 'tech_head', 'coordinator'),
            'can_create_task': True,
            'can_manage_kanban_columns': self.is_boss_role,
            'can_view_estimates': True,
            'can_edit_estimates': self.role in ('ceo', 'sales_head', 'Wait_lead', 'sales_mgr', 'tech_head', 'engineer', 'coordinator'),
            'can_approve_engineering': self.role in ('ceo', 'tech_head', 'engineer'),
            'can_view_catalog': True,
            'can_import_prices': self.role in ('ceo', 'sales_head', 'coordinator'),
            'can_view_currencies': self.role != 'warehouse',
            'can_edit_currencies': self.role in ('ceo', 'sales_head', 'coordinator'),
            'can_view_vendors': True,
            'can_edit_vendors': self.role in ('ceo', 'sales_head', 'coordinator'),
            'can_view_logistics': True,
            'can_edit_logistics': self.role in ('ceo', 'sales_head', 'Wait_lead', 'coordinator', 'warehouse'),
            'can_view_digital_twin': True,
            'can_edit_digital_twin': self.role in ('ceo', 'tech_head', 'engineer', 'warehouse'),
            'can_view_venue_profiles': self.role != 'warehouse',
            'can_edit_venue_profiles': self.role in ('ceo', 'tech_head', 'engineer'),
            'can_manage_staff': self.can_admin_staff_and_roles,
        }
        return fallback_map.get(attr_name, False)

    # Удобные проверки разрешений для шаблонов и контроллеров[cite: 1]
    @property
    def can_create_project_card(self) -> bool:
        return self.can_perm('create_project')

    @property
    def can_edit_kanban_cols(self) -> bool:
        return self.can_perm('manage_kanban_columns')

    @property
    def can_edit_project_estimate(self) -> bool:
        return self.can_perm('edit_estimates')

    @property
    def can_import_catalog_excel(self) -> bool:
        return self.can_perm('import_prices')

    @property
    def can_view_currency_block(self) -> bool:
        return self.can_perm('view_currencies')

    @property
    def can_edit_currency_block(self) -> bool:
        return self.can_perm('edit_currencies')

    @property
    def can_view_vendor_block(self) -> bool:
        return self.can_perm('view_vendors')

    @property
    def can_edit_vendor_block(self) -> bool:
        return self.can_perm('edit_vendors')

    @property
    def can_edit_logistics_block(self) -> bool:
        return self.can_perm('edit_logistics')

    @property
    def can_edit_digital_twin_block(self) -> bool:
        return self.can_perm('edit_digital_twin')

    @property
    def can_view_profiles_block(self) -> bool:
        return self.can_perm('view_venue_profiles')

    @property
    def can_edit_profiles_block(self) -> bool:
        return self.can_perm('edit_venue_profiles')

    def __str__(self):
        full_name = f"{self.first_name} {self.last_name}".strip() or self.username
        return f"{full_name} ({self.get_role_title()})"


class CurrencyRate(models.Model):
    """Хранилище мировых валют ЦБ РФ (выводятся в активную таблицу по флагу is_active)[cite: 2]"""
    code = models.CharField("Код валюты (ISO)", max_length=10, unique=True)
    name = models.CharField("Наименование валюты", max_length=60)
    nominal = models.PositiveIntegerField("Номинал", default=1)
    rate = models.DecimalField("Официальный курс ЦБ РФ к RUB", max_digits=12, decimal_places=4)
    is_active = models.BooleanField("Выводить в рабочую таблицу валют", default=True)
    updated_at = models.DateTimeField("Дата синхронизации с ЦБ РФ", auto_now=True)

    class Meta:
        verbose_name = "Курс валюты ЦБ РФ"
        verbose_name_plural = "3. Справочник валют ЦБ РФ"
        ordering = ['code']

    def __str__(self):
        return f"{self.code} — {self.rate} ₽"


class Vendor(models.Model):
    """Поставщики и заводы-производители сценического комплекса[cite: 2]"""
    name = models.CharField("Поставщик / Производитель", max_length=120)
    country = models.CharField("Страна логистики", max_length=60)
    currency = models.ForeignKey(CurrencyRate, on_delete=models.PROTECT, verbose_name="Валюта прайса")
    base_discount = models.DecimalField("Базовая дилерская скидка (%)", max_digits=5, decimal_places=2, default=15.00)
    protected_discount = models.DecimalField(
        "Скидка при Защите Проекта (%)",
        max_digits=5,
        decimal_places=2,
        default=30.00
    )
    contact_info = models.CharField("Контакты дистрибьютора / API", max_length=200, blank=True)

    class Meta:
        verbose_name = "Поставщик (Вендор)"
        verbose_name_plural = "4. Реестр поставщиков"
        ordering = ['id']

    def __str__(self):
        return f"{self.name} ({self.country}, {self.currency.code})"


class Category(models.Model):
    """Разделы верхнего уровня строго по каталогу https://ustage-group.ru/catalog/[cite: 2]"""
    code = models.CharField("Системный код раздела", max_length=40, unique=True)
    name = models.CharField("Раздел каталога (ustage-group.ru)", max_length=150)
    default_target_margin_pct = models.DecimalField(
        "Целевая маржинальность направления (%)",
        max_digits=5,
        decimal_places=2,
        default=25.00
    )

    class Meta:
        verbose_name = "Раздел каталога (Уровень 1)"
        verbose_name_plural = "5. Разделы каталога ustage-group.ru"
        ordering = ['id']

    def __str__(self):
        return self.name


class SubCategory(models.Model):
    """Подразделы второго уровня (строго привязанные к разделу Category)[cite: 2]"""
    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        related_name='subcategories',
        verbose_name="Родительский раздел"
    )
    code = models.CharField("Код подраздела", max_length=50, unique=True)
    name = models.CharField("Подраздел оборудования", max_length=160)
    calc_norm_per_100_seats = models.DecimalField(
        "Норматив подбора на 100 мест зала (ед.)",
        max_digits=6,
        decimal_places=2,
        default=2.00
    )

    class Meta:
        verbose_name = "Подраздел каталога (Уровень 2)"
        verbose_name_plural = "6. Подразделы каталога ustage-group.ru"
        ordering = ['category__id', 'id']

    def __str__(self):
        return f"{self.category.name} → {self.name}"


class Item(models.Model):
    """Номенклатурная позиция каталога с закупочной, розничной ценой и контролем наличия[cite: 2]"""
    STOCK_CHOICES = (
        ('in_stock', 'В наличии на складе поставщика'),
        ('low_stock', 'Мало (требуется бронь)'),
        ('out_of_stock', 'Нет в наличии у поставщика'),
    )

    sku = models.CharField("Артикул (SKU)", max_length=60, unique=True)
    brand = models.CharField("Бренд (для коммерческой сметы)", max_length=80, default="Ustage Pro")
    name = models.CharField("Наименование оборудования", max_length=255)
    category = models.ForeignKey(Category, on_delete=models.CASCADE, verbose_name="Раздел каталога")
    subcategory = models.ForeignKey(
        SubCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Подраздел каталога"
    )
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, verbose_name="Поставщик")

    retail_price = models.DecimalField("Розничная цена прайса (РРЦ в вал.)", max_digits=12, decimal_places=2)
    discount_pct = models.DecimalField("Базовая скидка по прайсу (%)", max_digits=5, decimal_places=2, default=15.00)
    purchase_price = models.DecimalField(
        "Закупочная цена со скидкой (в вал.)",
        max_digits=12,
        decimal_places=2,
        default=0.00
    )

    stock_status = models.CharField(
        "Наличие у поставщика",
        max_length=20,
        choices=STOCK_CHOICES,
        default='in_stock'
    )
    stock_qty = models.PositiveIntegerField("Доступный остаток у вендора, шт.", default=20)
    recommended_analog = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Рекомендуемый аналог (для замены и 44-ФЗ)"
    )

    specs_text = models.TextField("Характеристики (текстом из прайса)", blank=True)
    technical_specs = models.JSONField("Структурированные параметры (вес, мощность, ТТХ)", default=dict)
    is_russian_registry = models.BooleanField("Реестр Минпромторга РФ (ПП № 1875)", default=False)
    default_resource_hours = models.PositiveIntegerField(
        "Паспортный ресурс лампы / механизма (моточасы)",
        default=2000
    )

    class Meta:
        verbose_name = "Позиция оборудования"
        verbose_name_plural = "7. Номенклатура каталога"
        ordering = ['category__id', 'sku']

    def save(self, *args, **kwargs):
        if self.retail_price and (not self.purchase_price or self.purchase_price <= Decimal('0')):
            disc = Decimal(str(self.discount_pct or 0)) / Decimal('100')
            self.purchase_price = (Decimal(str(self.retail_price)) * (Decimal('1') - disc)).quantize(Decimal('0.01'))
        super().save(*args, **kwargs)

    @property
    def weight_kg(self) -> Decimal:
        return Decimal(str(self.technical_specs.get('weight_kg', 5.0)))

    @property
    def power_w(self) -> Decimal:
        return Decimal(str(self.technical_specs.get('power_w', 150.0)))

    def __str__(self):
        return f"[{self.sku}] {self.brand} {self.name}"