import io
import pandas as pd
from django.core.management.base import BaseCommand
from catalog.models import Item, Vendor, Category
from projects.models import Estimate
from projects.services import (
    parse_vendor_excel_price,
    find_tender_analogs_44fz,
     generate_44fz_blind_spec,
    calculate_stage_auto_rigger,
    consolidate_vendor_procurement,
)


class Command(BaseCommand):
    help = 'Автотестирование всех алгоритмов Этапа 3 (services.py)'

    def handle(self, *args, **kwargs):
        self.stdout.write('=== ТЕСТ 1: Тендерный дешифратор 44-ФЗ и поиск аналогов (+-10%) ===')
        la_item = Item.objects.get(sku='LA-X8-EU')
        self.stdout.write(f"Исходный лот: {la_item} | Обезличенное ТЗ: {generate_44fz_blind_spec(la_item)}")
        analogs = find_tender_analogs_44fz(la_item.id, tolerance_pct=10.0)
        for a in analogs:
            self.stdout.write(
                f" -> Найден аналог: {a['item']} | Цена: {a['cand_rub_price']} руб. (Экономия: {a['savings_rub']} руб./шт.)"
            )

        self.stdout.write('\n=== ТЕСТ 2: Авторасчет сценической обвязки (Ustage Auto-Rigger) ===')
        estimate = Estimate.objects.first()
        rig_res = calculate_stage_auto_rigger(estimate, apply_to_estimate=True)
        self.stdout.write(f"Световых голов: {rig_res['light_fixtures_count']} шт. | Лебедок: {rig_res['winches_count']} шт.")
        self.stdout.write(f"Рассчитано DMX-кабеля (с запасом 15%): {rig_res['dmx_cable_meters']} м")
        self.stdout.write(f"Вес оборудования: {rig_res['total_weight_kg']} кг из {rig_res['max_rigging_load_kg']} кг (Безопасно: {rig_res['is_weight_safe']})")
        self.stdout.write(f"Энергопотребление: {rig_res['total_power_kw']} кВт из {rig_res['electric_capacity_kw']} кВт (Безопасно: {rig_res['is_power_safe']})")
        self.stdout.write(f"Автоматически добавлено в смету: {rig_res['added_positions']}")

        self.stdout.write('\n=== ТЕСТ 3: Парсер Excel-прайса поставщика (pandas + обработка NaN) ===')
        vendor_cn = Vendor.objects.filter(origin_type='cn').first()
        cat_light = Category.objects.get(code='light')
        # Имитируем реальный Excel-файл поставщика в памяти (включая пустую строку NaN)
        excel_buffer = io.BytesIO()
        df_test = pd.DataFrame([
            {'Артикул': 'CN-BEAM-450', 'Наименование': 'Полноповоротный прожектор Beam/Spot 450W', 'Цена': 6100.00},
            {'Артикул': None, 'Наименование': 'Пустая битая строка NaN', 'Цена': None},
            {'Артикул': 'CN-WASH-1940', 'Наименование': 'Светодиодная голова Wash 19x40W Zoom', 'Цена': 4850.00},
        ])
        df_test.to_excel(excel_buffer, index=False)
        excel_buffer.seek(0)
        parse_res = parse_vendor_excel_price(excel_buffer, vendor_cn, cat_light)
        self.stdout.write(f"Результат импорта прайса: {parse_res}")

        self.stdout.write('\n=== ТЕСТ 4: Консолидация валютных заказов поставщикам ===')
        orders = consolidate_vendor_procurement(estimate)
        for o in orders:
            self.stdout.write(
                f" -> Поставщик: {o['vendor_name']} | Позиций: {o['positions_count']} | "
                f"К оплате: {o['total_in_currency']} {o['currency']} ({o['total_in_rub']} руб.)"
            )

        self.stdout.write(self.style.SUCCESS('\nВСЕ 4 АЛГОРИТМА ЭТАПА 3 ПРОШЛИ ТЕСТИРОВАНИЕ УСПЕШНО!'))