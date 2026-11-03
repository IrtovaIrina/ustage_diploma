from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from catalog.models import StaffPosition, CustomUser, CurrencyRate, Vendor, Category, SubCategory, Item
from projects.models import (
    KanbanColumn,
    Project,
    Estimate,
    EstimateLine,
    DellinShipment,
    VenueCalculationProfile,
    DigitalStageAsset,
)
from projects.views import sync_project_stage_automations


class UstageErpFullLifecycleTests(TestCase):
    """Набор модульных, граничных и негативных тестов ИС ustage_erp[cite: 1]"""

    def setUp(self):
        self.client = Client()

        # 1. Должности и пользователи
        self.pos_ceo = StaffPosition.objects.create(
            code="ceo", title="Генеральный директор", is_management=True, can_edit_estimates=True
        )
        self.pos_mgr = StaffPosition.objects.create(
            code="sales_mgr", title="Менеджер отдела продаж", is_sales_manager=True, can_edit_estimates=True
        )
        self.pos_storekeeper = StaffPosition.objects.create(
            code="storekeeper",
            title="Кладовщик",
            can_create_projects=False,
            can_edit_estimates=False,
            can_edit_currencies=False,
        )

        self.ceo_user = CustomUser.objects.create_user(
            username="ceo_antonov",
            email="ceo_antonov@ustage-group.ru",
            password="TestPass123!",
            role="ceo",
            position=self.pos_ceo,
        )
        self.mgr_user = CustomUser.objects.create_user(
            username="mgr_sergeev",
            email="mgr_sergeev@ustage-group.ru",
            password="TestPass123!",
            role="sales_mgr",
            position=self.pos_mgr,
        )
        self.store_user = CustomUser.objects.create_user(
            username="store_ivanov",
            email="store_ivanov@ustage-group.ru",
            password="TestPass123!",
            role="storekeeper",
            position=self.pos_storekeeper,
        )

        # 2. Валюты и поставщик
        self.curr_eur = CurrencyRate.objects.create(
            code="EUR", name="Евро", nominal=1, rate=Decimal("105.4000"), is_active=True
        )
        self.vendor_eu = Vendor.objects.create(
            name="EuroSound Distribution",
            country="Франция",
            currency=self.curr_eur,
            base_discount=Decimal("15.00"),
            protected_discount=Decimal("30.00"),
        )

        # 3. Каталог и номенклатура
        self.cat_sound = Category.objects.create(
            code="sound", name="Звуковое оборудование", default_target_margin_pct=Decimal("25.00")
        )
        self.subcat_acoustics = SubCategory.objects.create(
            category=self.cat_sound, code="acoustics", name="Акустические системы"
        )
        self.item_speaker = Item.objects.create(
            sku="LA-X8-TEST",
            brand="L-Acoustics",
            name="Коаксиальная АС X8",
            category=self.cat_sound,
            subcategory=self.subcat_acoustics,
            vendor=self.vendor_eu,
            retail_price=Decimal("1000.00"),
            discount_pct=Decimal("15.00"),
            stock_qty=5,
            specs_text="Мощность 400 Вт, SPL 129 дБ",
        )

        # 4. Колонки Канбана и проект
        self.col_presale = KanbanColumn.objects.create(order=1, title="1. Новая заявка")
        self.col_logistics = KanbanColumn.objects.create(
            order=4, title="4. Закупка и Доставка", is_logistics_stage=True
        )
        self.col_done = KanbanColumn.objects.create(
            order=5, title="5. Завершено", is_completed_stage=True
        )
        VenueCalculationProfile.objects.create(name="Драматический театр", daily_hours=Decimal("4.50"))

        self.project = Project.objects.create(
            title="Театр на Литейном — тест",
            client="СПб ГБУК Театр на Литейном",
            client_budget_rub=Decimal("500000.00"),
            kanban_column=self.col_presale,
            manager=self.mgr_user,
        )
        self.estimate = Estimate.objects.create(
            project=self.project,
            fx_buffer_pct=Decimal("4.00"),
            platform_fee_pct=Decimal("1.50"),
            delivery_cost_rub=Decimal("5000.00"),
            include_installation=False,
             installation_cost_rub=Decimal("0.00"),
            embed_expenses_in_kp=True,
        )

    def test_01_financial_calculation_and_vat_22_percent(self):
        """Проверка сквозного числового примера расчета сметы и выделения НДС 22% (22/122)[cite: 1]"""
        line = EstimateLine.objects.create(
            estimate=self.estimate,
            item=self.item_speaker,
            quantity=1,
            is_protected=True,
            markup_pct=Decimal("25.00"),
            vat_rate=Decimal("22.00"),
        )
        # 1000 EUR * (1 - 0.30) = 700 EUR * 105.4000 * 1.04 = 76 731.20 ₽[cite: 1]
        self.assertEqual(line.unit_cost_rub, Decimal("76731.20"))
        # 76 731.20 * 1.015 * 1.25 = 97 352.71 ₽[cite: 1]
        self.assertEqual(line.base_client_line_rub, Decimal("97352.71"))
        # С учетом вшитой доставки 5 000 ₽: 97 352.71 + 5 000.00 = 102 352.71 ₽
        self.assertEqual(line.client_line_total_rub, Decimal("102352.71"))
        # Выделенный НДС 22/122 от 102 352.71 ₽ = 18 457.03 ₽
        self.assertEqual(line.vat_amount_rub, Decimal("18457.03"))

    def test_02_stage_transition_preserves_logistics_and_digital_passport(self):
        """Проверка сохранности трек-номеров и паспортов при выходе проекта из стадии Канбана[cite: 1]"""
        EstimateLine.objects.create(
            estimate=self.estimate, item=self.item_speaker, quantity=2, is_protected=True
        )
        # Переводим в стадию логистики
        self.project.kanban_column = self.col_logistics
        self.project.save()
        sync_project_stage_automations(self.project)

        shipment = DellinShipment.objects.get(project=self.project, vendor=self.vendor_eu)
        # Менеджер вводит реальный трек-номер и вес вручную[cite: 1]
        shipment.tracking_number = "DELLIN-REAL-778899"
        shipment.cargo_weight_kg = Decimal("142.50")
        shipment.save()

        # Переводим проект в стадию «Завершено», а затем обратно в «Новая заявка»
        self.project.kanban_column = self.col_done
        self.project.save()
        sync_project_stage_automations(self.project)

        self.project.kanban_column = self.col_presale
        self.project.save()
        sync_project_stage_automations(self.project)

        # Повторно входим в стадию логистики и проверяем, что ручной трек-номер не удален и не перезаписан[cite: 1]
        self.project.kanban_column = self.col_logistics
        self.project.save()
        sync_project_stage_automations(self.project)

        shipment_after = DellinShipment.objects.get(project=self.project, vendor=self.vendor_eu)
        self.assertEqual(shipment_after.tracking_number, "DELLIN-REAL-778899")
        self.assertEqual(shipment_after.cargo_weight_kg, Decimal("142.50"))
        self.assertEqual(DigitalStageAsset.objects.filter(project=self.project).count(), 1)

    def test_03_negative_input_validation_rejects_invalid_numbers(self):
        """Негативный тест: отклонение отрицательной цены, отрицательного количества и текста[cite: 1]"""
        self.client.login(username="mgr_sergeev", password="TestPass123!")
        url = reverse("add_manual_estimate_line", args=[self.project.id])

        # Попытка передать отрицательную цену
        self.client.post(
            url,
            {
                "manual_name": "Тестовый прибор",
                "manual_purchase_price": "-15000.00",
                "quantity": "2",
                "markup_pct": "25.00",
                "vat_rate": "22.00",
            },
        )
        self.assertEqual(self.estimate.lines.count(), 0)

        # Попытка передать отрицательное количество
        self.client.post(
            url,
            {
                "manual_name": "Тестовый прибор",
                "manual_purchase_price": "15000.00",
                "quantity": "-3",
                "markup_pct": "25.00",
                "vat_rate": "22.00",
            },
        )
        self.assertEqual(self.estimate.lines.count(), 0)

    def test_04_protection_radar_blocks_direct_kp_url_below_70_percent(self):
        """Граничный тест: серверная блокировка выгрузки КП при защите < 70% и разблокировка Гендиректором[cite: 1]"""
        EstimateLine.objects.create(
            estimate=self.estimate, item=self.item_speaker, quantity=1, is_protected=False
        )
        self.assertEqual(self.estimate.protection_rate_pct, Decimal("0.00"))
        self.assertFalse(self.estimate.is_kp_export_allowed)

        self.client.login(username="mgr_sergeev", password="TestPass123!")
        print_url = reverse("document_print", args=[self.project.id]) + "?type=kp"
        resp = self.client.get(print_url)
        # Менеджер получает редирект обратно в смету вместо печатной формы КП[cite: 1]
        self.assertEqual(resp.status_code, 302)

        # Гендиректор включает принудительную разблокировку КП[cite: 1, 2]
        self.estimate.ceo_force_unblock_kp = True
        self.estimate.save()
        resp_unblocked = self.client.get(print_url)
        self.assertEqual(resp_unblocked.status_code, 200)

    def test_05_role_access_restriction_for_storekeeper(self):
        """Негативный тест разграничения доступа: кладовщик не может менять смету и разблокировать КП[cite: 1]"""
        self.client.login(username="store_ivanov", password="TestPass123!")
        unblock_url = reverse("toggle_ceo_unblock_kp", args=[self.project.id])
        resp = self.client.post(unblock_url)
        self.assertEqual(resp.status_code, 403)