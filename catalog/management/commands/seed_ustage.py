import os
import django
from decimal import Decimal
from datetime import date

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ustage_erp.settings')
django.setup()

from catalog.models import StaffPosition, CustomUser, CurrencyRate, Vendor, Category, SubCategory, Item
from projects.models import KanbanColumn, Project, KanbanTask, Estimate, EstimateLine, DellinShipment, \
    VenueCalculationProfile, DigitalStageAsset


def run_seed():
    print("Starting database seeding for ustage_erp...")

    # 1. Currencies
    curr_rub, _ = CurrencyRate.objects.get_or_create(code='RUB', defaults={'name': 'Российский рубль', 'nominal': 1,
                                                                           'rate': Decimal('1.0000'),
                                                                           'is_active': True})
    curr_cny, _ = CurrencyRate.objects.get_or_create(code='CNY', defaults={'name': 'Китайский юань', 'nominal': 1,
                                                                           'rate': Decimal('13.8500'),
                                                                           'is_active': True})
    curr_eur, _ = CurrencyRate.objects.get_or_create(code='EUR', defaults={'name': 'Евро', 'nominal': 1,
                                                                           'rate': Decimal('105.4000'),
                                                                           'is_active': True})
    curr_usd, _ = CurrencyRate.objects.get_or_create(code='USD', defaults={'name': 'Доллар США', 'nominal': 1,
                                                                           'rate': Decimal('96.8000'),
                                                                           'is_active': True})
    curr_tmt, _ = CurrencyRate.objects.get_or_create(code='TMT', defaults={'name': 'Туркменский манат', 'nominal': 1,
                                                                           'rate': Decimal('27.6500'),
                                                                           'is_active': True})

    # 2. Positions & Roles
    pos_ceo, _ = StaffPosition.objects.get_or_create(code='ceo', defaults={
        'title': 'Генеральный директор', 'is_management': True, 'is_sales_manager': False,
        'view_currencies': True, 'edit_currencies': True, 'view_vendors': True, 'edit_vendors': True,
        'view_venue_profiles': True, 'edit_venue_profiles': True,
        'view_kanban': True, 'edit_kanban': True, 'view_estimates': True, 'edit_estimates': True,
        'view_catalog': True, 'edit_catalog': True, 'view_logistics': True, 'edit_logistics': True,
        'view_twin': True, 'edit_twin': True,
    })
    pos_mgr, _ = StaffPosition.objects.get_or_create(code='sales_mgr', defaults={
        'title': 'Ведущий менеджер по продажам', 'is_management': False, 'is_sales_manager': True,
        'view_currencies': True, 'edit_currencies': False, 'view_vendors': True, 'edit_vendors': False,
        'view_venue_profiles': True, 'edit_venue_profiles': False,
        'view_kanban': True, 'edit_kanban': True, 'view_estimates': True, 'edit_estimates': True,
        'view_catalog': True, 'edit_catalog': False, 'view_logistics': True, 'edit_logistics': True,
        'view_twin': True, 'edit_twin': False,
    })

    # 3. Users
    u1, _ = CustomUser.objects.get_or_create(username='ceo_antonov', defaults={
        'tab_number': '00006', 'first_name': 'Антонов Антон Валериевич', 'email': 'ceo_antonov@ustage-group.ru',
        'position': pos_ceo, 'can_manage_roles': True, 'monthly_sales_plan_rub': Decimal('15000000.00'),
    })
    u1.set_password('admin12345')
    u1.save()

    print("Seed script ready.")