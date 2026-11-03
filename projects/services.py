import re
import json
import urllib.request
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
import pandas as pd
from django.utils import timezone
from catalog.models import Category, SubCategory, Item, Vendor, CurrencyRate
from projects.models import Estimate, EstimateLine, DellinShipment


# Нормализованные резервные курсы за 1 единицу иностранной валюты (без повторного деления на номинал)[cite: 1]
FALLBACK_CBR_RATES = {
    "RUB": ("Российский рубль", 1, Decimal("1.0000")),
    "EUR": ("Евро", 1, Decimal("105.4000")),
    "USD": ("Доллар США", 1, Decimal("96.8000")),
    "CNY": ("Китайский юань", 1, Decimal("13.8500")),
    "TMT": ("Туркменский манат", 1, Decimal("27.6500")),
    "TRY": ("Турецкая лира", 10, Decimal("2.8500")),
    "KZT": ("Казахстанский тенге", 100, Decimal("0.2000")),
    "BYN": ("Белорусский рубль", 1, Decimal("29.4000")),
    "AED": ("Дирхам ОАЭ", 1, Decimal("26.3500")),
    "GBP": ("Фунт стерлингов", 1, Decimal("124.5000")),
}


def sync_currencies_from_cbr_api() -> dict:
    """
    Синхронизация курсов валют с JSON-шлюзом ЦБ РФ (cbr-xml-daily.ru)[cite: 1, 2].
    Для ответа API курс нормализуется к 1 единице валюты: R_unit = Value / Nominal
    с банковским округлением до 4 знаков (ROUND_HALF_UP)[cite: 1].
    """
    updated_codes = []
    source = "API ЦБ РФ (cbr-xml-daily.ru)"
    api_data = {}

    try:
        req = urllib.request.Request(
            "https://www.cbr-xml-daily.ru/daily_json.js",
            headers={"User-Agent": "UstageERP/1.0"},
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            api_data = payload.get("Valute", {})
    except Exception:
        source = "Резервный справочник котировок ЦБ РФ (офлайн-режим)"

    # Гарантируем базовую валюту RUB
    CurrencyRate.objects.update_or_create(
        code="RUB",
        defaults={"name": "Российский рубль", "nominal": 1, "rate": Decimal("1.0000"), "is_active": True},
    )

    for code, (fallback_name, fallback_nom, fallback_unit_rate) in FALLBACK_CBR_RATES.items():
        if code == "RUB":
            continue
        existing = CurrencyRate.objects.filter(code=code).first()
        is_active_flag = existing.is_active if existing else (code in ("EUR", "USD", "CNY", "TMT"))

        if code in api_data:
            raw_val = Decimal(str(api_data[code].get("Value", fallback_unit_rate)))
            raw_nom = int(api_data[code].get("Nominal", 1)) or 1
            name_str = api_data[code].get("Name", fallback_name)
            unit_rate = (raw_val / Decimal(raw_nom)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        else:
            # В резервном словаре курс уже указан за 1 единицу валюты[cite: 1]
            name_str = fallback_name
            raw_nom = fallback_nom
            unit_rate = fallback_unit_rate.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

        CurrencyRate.objects.update_or_create(
            code=code,
            defaults={
                "name": name_str,
                "nominal": raw_nom,
                "rate": unit_rate,
                "is_active": is_active_flag,
            },
        )
        updated_codes.append(code)

    return {"source": source, "updated_count": len(updated_codes), "codes": updated_codes}


def auto_detect_category_subcategory(text_row: str):
    """Автоматическая классификация позиции прайс-листа по 7 разделам и 21 подразделу[cite: 1, 2]"""
    low = (text_row or "").lower()
    if any(k in low for k in ("лебедк", "ферм", "бархат", "одежд", "струбцин", "тросик", "механик")):
        cat = Category.objects.filter(code="stage_mech").first()
    elif any(k in low for k in ("прожектор", "голова", "beam", "wash", "spot", "dmx", "свет", "par")):
        cat = Category.objects.filter(code="lighting").first()
    elif any(k in low for k in ("экран", "проектор", "видео", "ptz", "led-кабинет")):
        cat = Category.objects.filter(code="video").first()
    elif any(k in low for k in ("кабел", "разъем", "xlr", "speakon", "powercon", "коммутац", "рэк")):
        cat = Category.objects.filter(code="cables").first()
    elif any(k in low for k in ("дым", "туман", "искр", "конфетти", "спецэффект")):
        cat = Category.objects.filter(code="sfx").first()
    else:
        cat = Category.objects.filter(code="sound").first() or Category.objects.first()

    subcat = SubCategory.objects.filter(category=cat).first() if cat else SubCategory.objects.first()
    return cat, subcat


def parse_vendor_excel_price(file_obj, vendor: Vendor, category=None, subcategory=None) -> dict:
    """
    Пакетный парсинг прайс-листов поставщиков (.xlsx / .xls) через pandas[cite: 1, 2].
    Выполняет строгую валидацию числовых ячеек и отбрасывает некорректные строки[cite: 1].
    """
    df = pd.read_excel(file_obj)
    df = df.dropna(how="all")

    imported_count = 0
    skipped_count = 0

    for _, row in df.iterrows():
        sku_raw = str(row.get("SKU", row.get("Артикул", ""))).strip()
        name_raw = str(row.get("Name", row.get("Наименование", ""))).strip()
        if not sku_raw or sku_raw.lower() == "nan" or not name_raw or name_raw.lower() == "nan":
            skipped_count += 1
            continue

        brand_raw = str(row.get("Brand", row.get("Бренд", vendor.name))).strip()
        specs_raw = str(row.get("Specs", row.get("Характеристики", row.get("ТТХ", "")))).strip()
        if specs_raw.lower() == "nan":
            specs_raw = ""

        try:
            retail_val = Decimal(str(row.get("Retail", row.get("Розница", row.get("РРЦ", "0"))))).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            disc_val = Decimal(str(row.get("Discount", row.get("Скидка", vendor.base_discount)))).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            qty_val = int(float(str(row.get("Stock", row.get("Остаток", 10)))))
        except (InvalidOperation, ValueError, TypeError):
            skipped_count += 1
            continue

        if retail_val <= Decimal("0.00") or qty_val < 0 or disc_val < Decimal("0.00"):
            skipped_count += 1
            continue

        target_cat = category
        target_subcat = subcategory
        if not target_cat or not target_subcat:
            target_cat, target_subcat = auto_detect_category_subcategory(f"{brand_raw} {name_raw} {specs_raw}")

        if not target_cat or not target_subcat:
            skipped_count += 1
            continue

        purch_val = (retail_val * (Decimal("1.00") - disc_val / Decimal("100.00"))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        Item.objects.update_or_create(
            sku=sku_raw,
            defaults={
                "brand": brand_raw,
                "name": name_raw,
                "category": target_cat,
                "subcategory": target_subcat,
                "vendor": vendor,
                "retail_price": retail_val,
                "discount_pct": disc_val,
                "purchase_price": purch_val,
                "stock_qty": qty_val,
                "stock_status": "in_stock" if qty_val > 3 else ("low_stock" if qty_val > 0 else "out_of_stock"),
                "specs_text": specs_raw,
            },
        )
        imported_count += 1

    return {"imported": imported_count, "skipped": skipped_count}


def parse_theater_tz_and_sync(project) -> dict:
    """Извлечение габаритов сцены WxDxH и вместимости зала из текста ТЗ театра[cite: 1, 2]"""
    text = project.theater_tz_text or ""
    dim_match = re.search(r"(\d{1,2})\s*[xх×]\s*(\d{1,2})", text.lower())
    if dim_match:
        project.stage_width = Decimal(dim_match.group(1))
        project.stage_depth = Decimal(dim_match.group(2))

    h_match = re.search(r"высот[а-я]*\s*(\d{1,2})", text.lower())
    if h_match:
        project.stage_height = Decimal(h_match.group(1))

    seats_match = re.search(r"(\d{2,4})\s*мест", text.lower())
    if seats_match:
        project.audience_seats = int(seats_match.group(1))

    project.save()
    return {
        "width": project.stage_width,
        "depth": project.stage_depth,
        "height": project.stage_height,
        "seats": project.audience_seats,
    }


def calculate_stage_auto_rigger(project, apply_to_estimate: bool = False) -> dict:
    """
    Параметрический калькулятор сценической обвязки и нагрузок (Ustage Auto-Rigger)[cite: 1, 2].
    Коэффициенты 2.5 м, 3.0 м, 15 м и запас 1.15 являются расчетными допущениями
    предпроектной сметной модели[cite: 1, 2]:
    L_DMX = ceil((2.5 * N_light + 3.0 * N_sound + W + D + 2*H + 15) * 1.15)[cite: 1, 2]
    """
    estimate, _ = Estimate.objects.get_or_create(project=project)
    lines = estimate.lines.select_related("item", "item__category").all()

    n_light = 0
    n_sound = 0
    total_weight_kg = Decimal("0.00")
    total_power_watts = 0

    for line in lines:
        if line.item:
            total_weight_kg += line.item.weight_kg * Decimal(line.quantity)
            total_power_watts += line.item.power_watts * line.quantity
            cat_code = line.item.category.code if line.item.category else ""
            if "light" in cat_code or "свет" in line.item.category.name.lower():
                n_light += line.quantity
            elif "sound" in cat_code or "звук" in line.item.category.name.lower():
                n_sound += line.quantity

    if n_light == 0 and n_sound == 0:
        n_light = 8
        n_sound = 4

    w = project.stage_width
    d = project.stage_depth
    h = project.stage_height

    raw_dmx = (
        Decimal("2.5") * Decimal(n_light)
        + Decimal("3.0") * Decimal(n_sound)
        + w
        + d
        + (Decimal("2.0") * h)
        + Decimal("15.0")
    ) * Decimal("1.15")
    dmx_meters = int(raw_dmx.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    clamps_count = n_light * 2
    safety_cables_count = n_light * 2
    connectors_count = (n_light + n_sound) * 2
    total_power_kw = (Decimal(total_power_watts) / Decimal("1000.00")).quantize(Decimal("0.01"))

    if apply_to_estimate:
        # Удаляем старые авто-добавленные строки обвязки перед пересчетом
        estimate.lines.filter(is_auto_rigged=True).delete()

        cable_item = Item.objects.filter(name__icontains="кабель").first() or Item.objects.filter(
            category__code="cables"
        ).first()
        clamp_item = Item.objects.filter(name__icontains="струбцин").first()

        if cable_item:
            spools = max(1, (dmx_meters + 99) // 100)
            EstimateLine.objects.create(
                estimate=estimate,
                item=cable_item,
                quantity=spools,
                is_protected=True,
                markup_pct=Decimal("25.00"),
                vat_rate=Decimal("22.00"),
                is_auto_rigged=True,
            )
        else:
            EstimateLine.objects.create(
                estimate=estimate,
                is_manual_item=True,
                manual_sku="AUTO-DMX-512",
                manual_name=f"Кабель управления DMX-512 110 Ом ({dmx_meters} м с запасом 15%)",
                manual_specs="Волновое сопротивление 110 Ом, двойной экран, расчет Auto-Rigger",
                manual_vendor_name="Склад коммутации СПб",
                manual_currency_code="RUB",
                manual_purchase_price=Decimal(str(dmx_meters * 185)),
                quantity=1,
                is_protected=True,
                markup_pct=Decimal("25.00"),
                vat_rate=Decimal("22.00"),
                is_auto_rigged=True,
            )

        if clamp_item and clamps_count > 0:
            EstimateLine.objects.create(
                estimate=estimate,
                item=clamp_item,
                quantity=clamps_count,
                is_protected=True,
                markup_pct=Decimal("25.00"),
                vat_rate=Decimal("22.00"),
                is_auto_rigged=True,
            )

    return {
        "dmx_meters": dmx_meters,
        "clamps_count": clamps_count,
        "safety_cables_count": safety_cables_count,
        "connectors_count": connectors_count,
        "total_weight_kg": total_weight_kg,
        "total_power_kw": total_power_kw,
        "weight_ok": total_weight_kg <= project.max_rigging_weight_kg,
        "power_ok": total_power_kw <= project.max_power_kw,
    }


def generate_44fz_blind_spec(item_or_line) -> str:
    """
    Формирование обезличенного описания позиции для закупок по 44-ФЗ / 223-ФЗ[cite: 1, 2].
    Удаляет коммерческий бренд и артикул, оставляя технические характеристики и пометку «или эквивалент»[cite: 1, 2].
    """
    if isinstance(item_or_line, EstimateLine):
        if item_or_line.item:
            item = item_or_line.item
            subcat_title = item.subcategory.name if item.subcategory else "Сценическое оборудование"
            specs = item.specs_text or "Профессиональное сценическое исполнение"
            return f"{subcat_title}: {specs} (или эквивалент)"
        return f"{item_or_line.manual_name}: {item_or_line.manual_specs} (или эквивалент)"

    if isinstance(item_or_line, Item):
        subcat_title = item_or_line.subcategory.name if item_or_line.subcategory else "Сценическое оборудование"
        return f"{subcat_title}: {item_or_line.specs_text} (или эквивалент)"

    return "Профессиональное сценическое оборудование (или эквивалент)"


def find_tender_analogs_44fz(item: Item):
    """
    Предварительный подбор вариантов замены по 44-ФЗ[cite: 1, 2]:
    1) Проверяет явно привязанный эталонный аналог (recommended_analog) в наличии[cite: 1];
    2) Возвращает до 3 позиций из того же профильного подраздела (SubCategory) со статусом «В наличии»[cite: 1].
    Финальную техническую эквивалентность, КТРУ и нацрежим проверяет профильный специалист[cite: 1].
    """
    results = []
    if item.recommended_analog and item.recommended_analog.stock_status == "in_stock":
        results.append(item.recommended_analog)

    subcat_qs = (
        Item.objects.filter(subcategory=item.subcategory, stock_status="in_stock")
        .exclude(id=item.id)
        .order_by("retail_price")[:3]
    )
    for candidate in subcat_qs:
        if candidate not in results and len(results) < 3:
            results.append(candidate)
    return results


def track_shipment_by_carrier_api(shipment: DellinShipment) -> dict:
    """
    Сервисный модуль программной имитации (эмуляции) статусов отслеживания накладных
    ТК «Деловые Линии» и курьерской службы «СДЭК» по формату трек-номера[cite: 1, 2].
    """
    track = (shipment.tracking_number or "").strip().upper()
    today = timezone.now().date()

    if "ARRIVED" in track or track.endswith("99") or "СПБ" in track:
        shipment.status = "Груз прибыл на терминал / склад Санкт-Петербург (готов к выдаче)"
        shipment.eta_date = today
    elif shipment.carrier == "cdek":
        shipment.status = "СДЭК: В пути между сортировочными центрами"
        shipment.eta_date = today + timedelta(days=3)
    else:
        shipment.status = "ТК «Деловые Линии»: Магистральная перевозка в Санкт-Петербург"
        shipment.eta_date = today + timedelta(days=5)

    shipment.save()

    # Обновляем сводный индикатор прибытия на карточке проекта
    project = shipment.project
    all_shipments = project.dellin_shipments.all()
    if all_shipments.exists():
        all_arrived = all("прибыл" in s.status.lower() for s in all_shipments)
        project.is_delivery_arrived = all_arrived
        project.save(update_fields=["is_delivery_arrived"])

    return {
        "tracking_number": shipment.tracking_number,
        "carrier": shipment.get_carrier_display(),
        "status": shipment.status,
        "eta_date": shipment.eta_date,
    }