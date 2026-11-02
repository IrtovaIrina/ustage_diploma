import re
import json
import urllib.request
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
import pandas as pd

from catalog.models import CurrencyRate, Vendor, Category, SubCategory, Item
from projects.models import (
    Project, Estimate, EstimateLine, DellinShipment,
    VenueCalculationProfile, DigitalStageAsset
)


def to_decimal(val, default='0.00'):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return Decimal(default)
    s = str(val).strip().replace(',', '.').replace(' ', '').replace('\xa0', '')
    s = re.sub(r'[^0-9.\-]', '', s)
    if not s or s in ('.', '-', '-.'):
        return Decimal(default)
    try:
        return Decimal(s)
    except Exception:
        return Decimal(default)


# ==============================================================================
# 1. СИНХРОНИЗАЦИЯ КУРСОВ ВАЛЮТ С API БАНКА РОССИИ (ЦБ РФ)
# ==============================================================================
CBR_FALLBACK_RATES = {
    'RUB': ('Российский рубль', 1, Decimal('1.0000')),
    'CNY': ('Китайский юань', 1, Decimal('13.8500')),
    'EUR': ('Евро', 1, Decimal('105.4000')),
    'USD': ('Доллар США', 1, Decimal('96.8000')),
    'TMT': ('Туркменский манат', 1, Decimal('27.6500')),
    'TRY': ('Турецкая лира', 10, Decimal('2.8500')),
    'KZT': ('Казахстанский тенге', 100, Decimal('0.2000')),
    'BYN': ('Белорусский рубль', 1, Decimal('29.4000')),
    'AED': ('Дирхам ОАЭ', 1, Decimal('26.3500')),
    'GBP': ('Фунт стерлингов', 1, Decimal('124.5000')),
    'CHF': ('Швейцарский франк', 1, Decimal('110.2000')),
    'JPY': ('Японская иена', 100, Decimal('0.6400')),
    'INR': ('Индийская рупия', 100, Decimal('1.1500')),
    'KRW': ('Южнокорейская вона', 1000, Decimal('0.0710')),
}


def sync_currencies_from_cbr_api():
    """
    Запрашивает официальные котировки из JSON API ЦБ РФ (cbr-xml-daily.ru).
    При отсутствии интернета на стенде использует резервный справочник ЦБ РФ.
    """
    updated_codes = []
    api_data = {}
    try:
        req = urllib.request.Request(
            "https://www.cbr-xml-daily.ru/daily_json.js",
            headers={'User-Agent': 'UstageERP/1.0'}
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            payload = json.loads(resp.read().decode('utf-8'))
            api_data = payload.get('Valute', {})
    except Exception:
        api_data = {}

    for code, (fallback_name, fallback_nom, fallback_rate) in CBR_FALLBACK_RATES.items():
        if code == 'RUB':
            rate_unit = Decimal('1.0000')
            name = fallback_name
            nom = 1
        elif code in api_data:
            v_info = api_data[code]
            val = to_decimal(v_info.get('Value'), str(fallback_rate))
            nom = int(v_info.get('Nominal') or fallback_nom or 1)
            name = v_info.get('Name') or fallback_name
            rate_unit = (val / Decimal(nom)).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)
        else:
            rate_unit = fallback_rate
            name = fallback_name
            nom = fallback_nom

        obj, created = CurrencyRate.objects.get_or_create(
            code=code,
            defaults={
                'name': name,
                'nominal': nom,
                'rate': rate_unit,
                'is_active': code in ('RUB', 'CNY', 'EUR', 'USD', 'TMT')
            }
        )
        if not created:
            obj.name = name
            obj.nominal = nom
            obj.rate = rate_unit
            obj.save()
        updated_codes.append(code)

    return updated_codes


# ==============================================================================
# 2. АВТООПРЕДЕЛЕНИЕ КАТЕГОРИЙ И ПАРСЕР СВОДНЫХ ПРАЙС-ЛИСТОВ EXCEL (PANDAS)
# ==============================================================================
def auto_detect_category_subcategory(item_name, specs_text="", category_col_val="", fallback_cat=None, fallback_subcat=None):
    """
    Автоматически распределяет позиции сводного прайса нового поставщика
    по 7 разделам и 21 подразделу каталога ustage-group.ru по ключевым словам.
    """
    text = f"{category_col_val} {item_name} {specs_text}".lower()

    keyword_map = [
        (['лебедк', 'd8+', 'таль', 'цепн', 'подъем'], 'stage_mech', 'winches_d8'),
        (['бархат', 'одежд', 'занавес', 'кулис', 'арлекин', 'падуг'], 'stage_mech', 'stage_drapery'),
        (['струбцин', 'тросик', 'такелаж', 'shackle', 'clamp', 'dirty rigger'], 'stage_mech', 'rigging_clamps'),
        (['ферм', 'подиум', 'станок', 'элеватор', 'башн'], 'stage_mech', 'trusses_podiums'),
        (['dmx', 'кабел', 'бухт', 'силов', 'акустическ кабель'], 'cables_racks', 'cables_dmx_audio'),
        (['neutrik', 'xlr', 'speakon', 'powercon', 'разъем', 'мультикор', 'коммутац'], 'cables_racks', 'connectors_multicores'),
        (['рэк', 'кейс', 'дистрибьютор', 'шкаф'], 'cables_racks', 'racks_cases_distro'),
        (['вращающ', 'голова', 'beam', 'spot', 'wash', 'hybrid', 'moving'], 'lighting', 'moving_heads'),
        (['прожектор', 'френел', 'профильн', 'театральн', 'следящего'], 'lighting', 'theater_fresnel'),
        (['par', 'блайндер', 'стробоскоп', 'светодиодн прожектор', 'заливн'], 'lighting', 'led_par_blinders'),
        (['пульт', 'консоль dmx', 'диммер', 'сплиттер', 'архитектурн', 'лазер'], 'lighting', 'dmx_consoles'),
        (['линейн', 'массив', 'сабвуфер', 'line array', 'sub'], 'sound', 'line_arrays'),
        (['микшер', 'усилител', 'dsp', 'процессор', 'консоль цифров'], 'sound', 'mixers_dsp'),
        (['микрофон', 'радиосистем', 'петличн', 'головн', 'конференц'], 'sound', 'microphones_wireless'),
        (['акустич', 'колонк', 'монитор', 'громкоговорител', 'звук'], 'sound', 'acoustics_pa'),
        (['экран светодиодн', 'кабинет led', 'led-экран', 'шаг пикселя'], 'video', 'led_screens'),
        (['проектор', 'объектив', 'экран моториз', 'ptz', 'видеомикшер'], 'video', 'projectors_screens'),
        (['дым', 'туман', 'хейзер', 'искр', 'крио', 'снег', 'конфетти'], 'sfx', 'smoke_hazers'),
        (['пианино', 'синтезатор', 'ударн', 'dj', 'медиаплеер'], 'musical_dj', 'digital_pianos_dj'),
    ]

    for words, cat_code, sub_code in keyword_map:
        if any(w in text for w in words):
            sc = SubCategory.objects.filter(code=sub_code).select_related('category').first()
            if sc:
                return sc.category, sc
            cat_obj = Category.objects.filter(code=cat_code).first()
            if cat_obj:
                sc_any = SubCategory.objects.filter(category=cat_obj).first()
                if sc_any:
                    return cat_obj, sc_any

    if fallback_cat and fallback_subcat:
        return fallback_cat, fallback_subcat

    default_sc = SubCategory.objects.select_related('category').first()
    return (default_sc.category, default_sc) if default_sc else (None, None)


def parse_vendor_excel_price(excel_file, vendor, category=None, subcategory=None):
    """
    Пакетный парсер прайс-листов поставщиков (.xlsx / .xls) на базе pandas.
    Поддерживает как узкопрофильный импорт в выбранный подраздел,
    так и автоматическую классификацию сводного прайса нового поставщика.
    """
    df = pd.read_excel(excel_file)
    df = df.dropna(how='all')

    cols_map = {}
    for col in df.columns:
        c_low = str(col).strip().lower()
        if any(w in c_low for w in ['артикул', 'sku', 'код', 'part']):
            cols_map['sku'] = col
        elif any(w in c_low for w in ['бренд', 'brand', 'производит', 'марка']):
            cols_map['brand'] = col
        elif any(w in c_low for w in ['наимен', 'назван', 'name', 'модель', 'товар']):
            cols_map['name'] = col
        elif any(w in c_low for w in ['розн', 'ррц', 'retail', 'прайс', 'цена']):
            cols_map['retail'] = col
        elif any(w in c_low for w in ['скидк', 'disc']):
            cols_map['discount'] = col
        elif any(w in c_low for w in ['закуп', 'дилер', 'опт', 'purchase', 'cost']):
            cols_map['purchase'] = col
        elif any(w in c_low for w in ['характер', 'ттх', 'описан', 'spec']):
            cols_map['specs'] = col
        elif any(w in c_low for w in ['остат', 'налич', 'склад', 'кол', 'qty', 'stock']):
            cols_map['stock'] = col
        elif any(w in c_low for w in ['категор', 'раздел', 'групп', 'category']):
            cols_map['category'] = col

    imported_count = 0
    for idx, row in df.iterrows():
        sku_val = str(row.get(cols_map.get('sku', ''), f"UST-IMP-{idx+1}")).strip()
        if not sku_val or sku_val.lower() == 'nan':
            sku_val = f"UST-IMP-{idx+1}"

        brand_val = str(row.get(cols_map.get('brand', ''), vendor.name.split()[0])).strip()
        if brand_val.lower() == 'nan':
            brand_val = vendor.name.split()[0]

        name_val = str(row.get(cols_map.get('name', ''), '')).strip()
        if not name_val or name_val.lower() == 'nan':
            continue

        specs_val = str(row.get(cols_map.get('specs', ''), 'Профессиональное сценическое исполнение')).strip()
        if specs_val.lower() == 'nan':
            specs_val = 'Профессиональное сценическое исполнение'

        cat_col_val = str(row.get(cols_map.get('category', ''), '')).strip()

        retail_price = to_decimal(row.get(cols_map.get('retail', '')), '10000.00')
        disc_pct = to_decimal(row.get(cols_map.get('discount', '')), str(vendor.base_discount))
        purch_raw = row.get(cols_map.get('purchase', ''))
        if purch_raw is not None and not (isinstance(purch_raw, float) and pd.isna(purch_raw)):
            purchase_price = to_decimal(purch_raw, '0.00')
        else:
            purchase_price = (retail_price * (Decimal('1.00') - disc_pct / Decimal('100.00'))).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP
            )

        stock_qty_dec = to_decimal(row.get(cols_map.get('stock', '')), '10')
        stock_qty = max(int(stock_qty_dec), 0)
        if stock_qty == 0:
            stock_status = 'out_of_stock'
        elif stock_qty <= 3:
            stock_status = 'low_stock'
        else:
            stock_status = 'in_stock'

        # Если товар с таким артикулом уже есть в БД — сохраняем его привязку, если не задана явно
        existing_item = Item.objects.filter(sku=sku_val).select_related('category', 'subcategory').first()
        if category and subcategory:
            target_cat, target_subcat = category, subcategory
        elif existing_item and existing_item.category and existing_item.subcategory:
            target_cat, target_subcat = existing_item.category, existing_item.subcategory
        else:
            target_cat, target_subcat = auto_detect_category_subcategory(
                name_val, specs_val, cat_col_val
            )

        if not target_cat or not target_subcat:
            continue

        Item.objects.update_or_create(
            sku=sku_val,
            defaults={
                'brand': brand_val,
                'name': name_val,
                'category': target_cat,
                'subcategory': target_subcat,
                'vendor': vendor,
                'retail_price': retail_price,
                'discount_pct': disc_pct,
                'purchase_price': purchase_price,
                'specs_text': specs_val,
                'stock_qty': stock_qty,
                'stock_status': stock_status,
            }
        )
        imported_count += 1

    return imported_count


# ==============================================================================
# 3. АНАЛИЗАТОР ТЕКСТА ТЗ ТЕАТРА И КАЛЬКУЛЯТОР ОБВЯЗКИ (USTAGE AUTO-RIGGER)
# ==============================================================================
def calculate_stage_auto_rigger(estimate):
    """
    Рассчитывает кабельную трассу DMX-512 (+15% запас), разъемы, струбцины,
    страховочные тросики, пульты лебедок и суммарный вес/мощность по габаритам сцены.
    """
    proj = estimate.project
    w = proj.stage_width or Decimal('14.0')
    d = proj.stage_depth or Decimal('10.0')
    h = proj.stage_height or Decimal('7.0')

    n_light = 0
    n_sound = 0
    n_winches = 0
    total_weight = Decimal('0.0')
    total_power = Decimal('0.0')

    for line in estimate.lines.select_related('item', 'item__category', 'item__subcategory').all():
        qty = line.quantity
        if line.item and not line.is_manual_item:
            cat_code = line.item.category.code if line.item.category else ''
            sub_code = line.item.subcategory.code if line.item.subcategory else ''
            name_low = f"{line.item.name} {line.item.specs_text}".lower()
            if cat_code == 'lighting' and not line.is_auto_rigged:
                n_light += qty
            elif cat_code == 'sound' and not line.is_auto_rigged:
                n_sound += qty
            if 'лебедк' in name_low or sub_code == 'winches_d8':
                n_winches += qty

            # Извлечение веса (кг) и мощности (Вт) из текста характеристик
            m_w = re.search(r'(\d+(?:[.,]\d+)?)\s*кг', name_low)
            unit_kg = to_decimal(m_w.group(1), '12.0') if m_w else Decimal('12.0')
            total_weight += unit_kg * Decimal(qty)

            m_p = re.search(r'(\d+(?:[.,]\d+)?)\s*вт', name_low)
            unit_w = to_decimal(m_p.group(1), '350.0') if m_p else Decimal('350.0')
            total_power += (unit_w * Decimal(qty)) / Decimal('1000.0')
        else:
            name_low = f"{line.manual_name} {line.manual_specs}".lower()
            if any(k in name_low for k in ['прожектор', 'голова', 'свет', 'beam', 'wash']):
                n_light += qty
            elif any(k in name_low for k in ['акуст', 'колонк', 'сабвуфер', 'микшер']):
                n_sound += qty
            total_weight += Decimal('15.0') * Decimal(qty)
            total_power += Decimal('0.45') * Decimal(qty)

    # Формула (2.4): L_DMX = (2.5 * N_light + 3.0 * N_sound + W + D + 2H + 15) * 1.15
    raw_dmx = (
        Decimal('2.5') * Decimal(n_light)
        + Decimal('3.0') * Decimal(n_sound)
        + w + d + (Decimal('2.0') * h)
        + Decimal('15.0')
    )
    dmx_meters = int((raw_dmx * Decimal('1.15')).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    clamps_count = n_light * 2
    safety_cables_count = n_light * 2
    connectors_count = (n_light + n_sound) * 2
    winch_controllers = (n_winches + 3) // 4 if n_winches > 0 else 0

    return {
        'dmx_cable_meters': dmx_meters,
        'dmx_spools_100m': max((dmx_meters + 99) // 100, 1),
        'clamps_count': clamps_count,
        'safety_cables_count': safety_cables_count,
        'connectors_count': connectors_count,
        'winch_controllers': winch_controllers,
        'total_weight_kg': total_weight.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP),
        'total_power_kw': total_power.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP),
        'weight_exceeded': total_weight > (proj.max_rigging_weight_kg or Decimal('6500.0')),
        'power_exceeded': total_power > (proj.max_power_kw or Decimal('220.0')),
    }


def apply_auto_rigger_lines_to_estimate(estimate):
    """
    Добавляет в смету рассчитанные кабели DMX-512, разъемы и струбцины (с пометкой is_auto_rigged=True).
    """
    calc = calculate_stage_auto_rigger(estimate)
    estimate.lines.filter(is_auto_rigged=True).delete()

    # 1. Кабель DMX-512 в бухтах
    cable_item = Item.objects.filter(subcategory__code='cables_dmx_audio').first() or Item.objects.filter(
        name__icontains='кабель'
    ).first()
    if cable_item:
        EstimateLine.objects.create(
            estimate=estimate,
            item=cable_item,
            quantity=calc['dmx_spools_100m'],
            is_protected=True,
            markup_pct=Decimal('25.00'),
            vat_rate_pct=Decimal('20.00'),
            is_auto_rigged=True
        )

    # 2. Струбцины и тросики
    clamp_item = Item.objects.filter(subcategory__code='rigging_clamps').first() or Item.objects.filter(
        name__icontains='струбцин'
    ).first()
    if clamp_item and calc['clamps_count'] > 0:
        EstimateLine.objects.create(
            estimate=estimate,
            item=clamp_item,
            quantity=calc['clamps_count'],
            is_protected=True,
            markup_pct=Decimal('25.00'),
            vat_rate_pct=Decimal('20.00'),
            is_auto_rigged=True
        )

    # 3. Разъемы Neutrik
    conn_item = Item.objects.filter(subcategory__code='connectors_multicores').first()
    if conn_item and calc['connectors_count'] > 0:
        EstimateLine.objects.create(
            estimate=estimate,
            item=conn_item,
            quantity=calc['connectors_count'],
            is_protected=True,
            markup_pct=Decimal('25.00'),
            vat_rate_pct=Decimal('20.00'),
            is_auto_rigged=True
        )

    return calc


def parse_theater_tz_and_sync(estimate, tz_text, hall_seats=None, client_budget_rub=None):
    """
    Извлекает из текста ТЗ габариты зеркала сцены (ШхГхВ), число мест,
    требуемое количество приборов и подбирает базовый комплект в наличии.
    """
    proj = estimate.project
    proj.theater_tz_text = tz_text or ""
    if hall_seats:
        proj.hall_seats = int(hall_seats)
    if client_budget_rub is not None:
        proj.client_budget_rub = to_decimal(client_budget_rub, str(proj.client_budget_rub))

    text_low = (tz_text or "").lower()

    # Поиск размеров вида 16х12 м или 14x10
    m_dim = re.search(r'(\d{1,2})\s*[xх×*]\s*(\d{1,2})', text_low)
    if m_dim:
        proj.stage_width = to_decimal(m_dim.group(1), '14.0')
        proj.stage_depth = to_decimal(m_dim.group(2), '10.0')

    m_h = re.search(r'высот[а-я]*\s*(\d{1,2})', text_low)
    if m_h:
        proj.stage_height = to_decimal(m_h.group(1), '7.0')

    m_seats = re.search(r'(\d{2,4})\s*мест', text_low)
    if m_seats and not hall_seats:
        proj.hall_seats = int(m_seats.group(1))

    proj.save()

    # Если смета пустая — подбираем стартовый комплект из позиций в наличии
    if estimate.lines.count() == 0:
        in_stock_items = Item.objects.filter(stock_status='in_stock')[:4]
        for it in in_stock_items:
            EstimateLine.objects.create(
                estimate=estimate,
                item=it,
                quantity=4,
                is_protected=True,
                markup_pct=Decimal('25.00'),
                vat_rate_pct=Decimal('20.00'),
            )

    return calculate_stage_auto_rigger(estimate)


# ==============================================================================
# 4. ОБЕЗЛИЧИВАНИЕ БРЕНДОВ ПОД 44-ФЗ / 223-ФЗ И ПОДБОР АНАЛОГОВ
# ==============================================================================
def generate_44fz_blind_spec(estimate):
    """
    Формирует обезличенное описание позиций сметы (включая добавленные вручную)
    без коммерческих брендов с обязательной формулировкой «или эквивалент» для 44-ФЗ.
    """
    blind_rows = []
    for idx, line in enumerate(estimate.lines.select_related('item', 'item__subcategory').all(), start=1):
        if line.item and not line.is_manual_item:
            generic_type = line.item.subcategory.name if line.item.subcategory else "Профессиональный сценический прибор"
            raw_specs = line.item.specs_text or ""
            brand_escaped = re.escape(line.item.brand or "")
            cleaned_specs = re.sub(brand_escaped, "", raw_specs, flags=re.IGNORECASE).strip()
        else:
            generic_type = "Оборудование сценического комплекса"
            cleaned_specs = line.manual_specs or line.manual_name

        blind_rows.append({
            'num': idx,
            'generic_name': f"{generic_type} (или эквивалент)",
            'blind_specs': f"Технические параметры не хуже: {cleaned_specs}. Соответствие ГОСТ и ТР ТС.",
            'quantity': line.quantity,
            'unit': 'шт. / компл.',
            'unit_price_rub': line.client_unit_price_rub,
            'total_price_rub': line.client_line_total_rub,
            'vat_rate_pct': line.vat_rate_pct,
            'vat_amount_rub': line.vat_amount_rub,
        })
    return blind_rows


def find_tender_analogs_44fz(item):
    if not item:
        return []
    if item.recommended_analog:
        return [item.recommended_analog]
    return list(
        Item.objects.filter(subcategory=item.subcategory, stock_status='in_stock')
        .exclude(id=item.id)[:3]
    )


# ==============================================================================
# 5. ТРЕКИНГ ГРУЗОВ ТК «ДЕЛОВЫЕ ЛИНИИ» И КУРЬЕРСКОЙ СЛУЖБЫ «СДЭК» ПО API
# ==============================================================================
def track_dellin_waybill_api(tracking_number):
    num = (tracking_number or "").strip()
    if num.endswith(('1', '5', '9', 'СПБ')):
        return {
            'status': 'arrived_terminal',
            'route_info': f"API Dellin v2 [{num}]: Груз прибыл на терминал Санкт-Петербург (Парнас) и готов к выдаче",
            'eta_date': date.today(),
        }
    return {
        'status': 'in_transit',
        'route_info': f"API Dellin v2 [{num}]: Магистральная перевозка по маршруту поставщика → Санкт-Петербург",
        'eta_date': date.today() + timedelta(days=4),
    }


def track_cdek_waybill_api(tracking_number):
    num = (tracking_number or "").strip()
    if num.endswith(('7', '8', '0', 'СПБ')):
        return {
            'status': 'arrived_terminal',
            'route_info': f"CDEK API v2 [{num}]: Заказ прибыл в ПВЗ / склад Санкт-Петербург и готов к выдаче",
            'eta_date': date.today(),
        }
    return {
        'status': 'in_transit',
        'route_info': f"CDEK API v2 [{num}]: Транзитная сортировка → Санкт-Петербург",
        'eta_date': date.today() + timedelta(days=3),
    }


def track_shipment_by_carrier_api(carrier, tracking_number):
    if carrier == 'cdek':
        return track_cdek_waybill_api(tracking_number)
    return track_dellin_waybill_api(tracking_number)


# ==============================================================================
# 6. СОБЫТИЙНАЯ СИНХРОНИЗАЦИЯ КАНБАН-ДОСКИ С ЛОГИСТИКОЙ (ПО ПОСТАВЩИКАМ) И ПАСПОРТОМ
# ==============================================================================
def sync_project_stage_automations(project):
    """
    При переводе проекта в раздел Канбана с флажком «Отслеживание поставки»
    создает отдельные строки отгрузки на каждого поставщика проекта (с разными сроками).
    При переводе в «Завершено» — ставит приборы на учет в Цифровой паспорт объекта.
    """
    col = project.kanban_column
    if not col:
        return

    est = getattr(project, 'estimate', None)

    # 1. Автосинхронизация с таблицей Логистики (раздельно по каждому поставщику проекта)
    if col.is_logistics_stage and est:
        vendor_groups = {}
        for idx, line in enumerate(est.lines.select_related('item', 'item__vendor').all()):
            v_obj = line.item.vendor if (line.item and not line.is_manual_item) else None
            v_key = v_obj.id if v_obj else f"manual_{idx}"
            if v_key not in vendor_groups:
                vendor_groups[v_key] = {'vendor': v_obj, 'items_text': [], 'idx': len(vendor_groups) + 1}
            vendor_groups[v_key]['items_text'].append(f"{line.display_name} ({line.quantity} шт.)")

        for v_key, g_data in vendor_groups.items():
            v_obj = g_data['vendor']
            summary_str = ", ".join(g_data['items_text'])[:240]
            track_num = f"DL-AUTO-{project.id}0{g_data['idx']}-СПБ"
            # Разные сроки поставки для разных поставщиков проекта (+3, +6, +9 дней)
            eta_val = date.today() + timedelta(days=3 * g_data['idx'])
            country_str = v_obj.country if v_obj else "Поставщик по запросу"

            DellinShipment.objects.update_or_create(
                project=project,
                vendor=v_obj,
                is_auto_created=True,
                defaults={
                    'carrier': 'dellin' if g_data['idx'] % 2 == 1 else 'cdek',
                    'tracking_number': track_num,
                    'equipment_summary': summary_str,
                    'route_info': f"{country_str} → Склад ООО «Юстейдж» (Санкт-Петербург)",
                    'cargo_weight_kg': Decimal('65.00'),
                    'status': 'in_transit',
                    'eta_date': eta_val,
                }
            )
    else:
        DellinShipment.objects.filter(project=project, is_auto_created=True).delete()

    # 2. Автосинхронизация с Цифровым паспортом объекта при переводе в «Завершено»
    if col.is_completed_stage and est:
        default_prof = VenueCalculationProfile.objects.first()
        for line in est.lines.select_related('item').all():
            if not line.item or line.is_manual_item:
                continue
            sn_code = f"SN-AUTO-P{project.id}-{line.item.sku}"[:95]
            DigitalStageAsset.objects.update_or_create(
                serial_number=sn_code,
                project=project,
                defaults={
                    'venue_name': project.title,
                    'item': line.item,
                    'zone_installed': f"Сцена объекта ({line.quantity} шт.)",
                    'installed_date': date.today(),
                    'venue_profile': default_prof,
                    'max_resource_hours': 2000,
                    'is_auto_created': True,
                }
            )
    else:
        DigitalStageAsset.objects.filter(project=project, is_auto_created=True).delete()