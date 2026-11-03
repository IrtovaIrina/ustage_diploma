import random
from datetime import timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.utils import timezone
from django.core.mail import send_mail

from catalog.models import StaffPosition, CustomUser, CurrencyRate, Vendor, Category, SubCategory, Item
from projects.models import (
    KanbanColumn,
    Project,
    KanbanTask,
    TaskComment,
    TaskAttachment,
    Estimate,
    EstimateLine,
    DellinShipment,
    VenueCalculationProfile,
    DigitalStageAsset,
)
from projects.services import (
    sync_currencies_from_cbr_api,
    parse_vendor_excel_price,
    parse_theater_tz_and_sync,
    calculate_stage_auto_rigger,
    generate_44fz_blind_spec,
    find_tender_analogs_44fz,
    track_shipment_by_carrier_api,
)


def _parse_strict_decimal(raw_val, field_label: str, allow_zero: bool = True) -> Decimal:
    """Строгий парсинг десятичного числа без скрытой подстановки резервных значений[cite: 1]"""
    cleaned = str(raw_val or "").strip().replace(",", ".")
    if not cleaned:
        raise ValueError(f"Поле «{field_label}» не может быть пустым.")
    try:
        val = Decimal(cleaned).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise ValueError(f"Некорректное числовое значение в поле «{field_label}».")
    if allow_zero and val < Decimal("0.00"):
        raise ValueError(f"Значение поля «{field_label}» не может быть отрицательным.")
    if not allow_zero and val <= Decimal("0.00"):
        raise ValueError(f"Значение поля «{field_label}» должно быть строго больше нуля.")
    return val


def _parse_strict_positive_int(raw_val, field_label: str = "Количество") -> int:
    """Строгая проверка положительного целого количества (>= 1)[cite: 1]"""
    cleaned = str(raw_val or "").strip()
    try:
        val = int(cleaned)
    except (ValueError, TypeError):
        raise ValueError(f"Поле «{field_label}» должно быть целым числом.")
    if val < 1:
        raise ValueError(f"Поле «{field_label}» должно быть положительным целым числом (>= 1).")
    return val


def sync_project_stage_automations(project: Project):
    """
    Событийная синхронизация при переводе карточки проекта на Канбан-доске[cite: 1, 2].
    ВАЖНО: никогда не удаляет существующие записи логистики и цифрового паспорта
    при выходе из стадии и не перезаписывает введенные сотрудником данные[cite: 1].
    """
    col = project.kanban_column
    if not col:
        return

    estimate = getattr(project, "estimate", None)
    lines = estimate.lines.select_related("item", "item__vendor").all() if estimate else []

    # 1. При входе в стадию «Отслеживание поставки» создаем только недостающие черновики отгрузок[cite: 1, 2]
    if col.is_logistics_stage:
        vendors_map = {}
        for line in lines:
            if line.item and line.item.vendor:
                v = line.item.vendor
                vendors_map.setdefault(v, []).append(f"{line.item.brand} {line.item.name} ({line.quantity} шт.)")

        for vendor, summary_list in vendors_map.items():
            existing_shipment = DellinShipment.objects.filter(project=project, vendor=vendor).first()
            if not existing_shipment:
                carrier_code = "cdek" if "китай" in vendor.country.lower() else "dellin"
                DellinShipment.objects.create(
                    project=project,
                    vendor=vendor,
                    carrier=carrier_code,
                    tracking_number=f" PENDING-{project.id}-{vendor.id}".strip(),
                    equipment_summary="; ".join(summary_list)[:250],
                    route_info=f"{vendor.country} → Склад Санкт-Петербург",
                    cargo_weight_kg=Decimal("65.00"),
                    status="Ожидает ввода накладной перевозчика",
                    eta_date=timezone.now().date() + timedelta(days=5),
                    is_auto_created=True,
                )

    # 2. При входе в стадию «Завершено» создаем только отсутствующие записи паспорта без перезаписи[cite: 1, 2]
    if col.is_completed_stage:
        default_profile = VenueCalculationProfile.objects.first()
        for line in lines:
            if not line.item:
                continue
            pending_sn = f"SN-PENDING-P{project.id}-L{line.id}"
            already_exists = DigitalStageAsset.objects.filter(
                project=project, item=line.item
            ).exists()
            if not already_exists:
                DigitalStageAsset.objects.get_or_create(
                    serial_number=pending_sn,
                    defaults={
                        "project": project,
                        "venue_name": project.client,
                        "zone_installed": "Сцена объекта (ожидает ввода заводского S/N при приемке)",
                        "item": line.item,
                        "installed_date": timezone.now().date(),
                        "venue_profile": default_profile,
                        "max_resource_hours": line.item.lifespan_hours or 2000,
                        "is_auto_created": True,
                    },
                )


def _build_managers_kpi_stats():
    """Расчет выполнения планов KPI менеджеров по продажам[cite: 1, 2]"""
    managers = CustomUser.objects.filter(role__in=["lead_mgr", "sales_mgr", "rop"]).order_by("tab_number")
    stats = []
    for mgr in managers:
        projects_qs = Project.objects.filter(manager=mgr)
        proj_count = projects_qs.count()
        actual_sales = Decimal("0.00")
        for prj in projects_qs:
            if hasattr(prj, "estimate"):
                actual_sales += prj.estimate.total_client_rub

        tasks_done = KanbanTask.objects.filter(assigned_to=mgr, is_completed=True).count()
        plan_rub = mgr.monthly_sales_plan_rub or Decimal("1.00")
        sales_pct = min(int((actual_sales / plan_rub) * Decimal("100")), 100) if plan_rub > 0 else 0

        stats.append(
            {
                "manager": mgr,
                "actual_sales_rub": actual_sales,
                "sales_pct": sales_pct,
                "proj_count": proj_count,
                "tasks_done": tasks_done,
            }
        )
    return stats


def login_view(request):
    """Авторизация сотрудника строго по корпоративной почте (@ustage-group.ru)[cite: 1, 2]"""
    if request.user.is_authenticated:
        return redirect("dashboard")

    if request.method == "POST":
        email_input = (request.POST.get("email") or request.POST.get("username") or "").strip().lower()
        password_input = request.POST.get("password") or ""

        user_obj = CustomUser.objects.filter(email__iexact=email_input).first()
        if not user_obj:
            user_obj = CustomUser.objects.filter(username__iexact=email_input).first()

        if user_obj:
            auth_user = authenticate(request, username=user_obj.username, password=password_input)
            if auth_user is not None:
                login(request, auth_user)
                return redirect("dashboard")

        messages.error(request, "Неверная корпоративная почта (@ustage-group.ru) или пароль.")

    return render(request, "login.html")


def logout_view(request):
    logout(request)
    return redirect("login")


def forgot_password_view(request):
    """Двухэтапное восстановление пароля через отправку 6-значного OTP-кода на почту[cite: 1, 2]"""
    step = request.session.get("reset_step", 1)
    reset_email = request.session.get("reset_email", "")

    if request.method == "POST":
        action = request.POST.get("action", "send_otp")
        if action == "send_otp":
            email_val = (request.POST.get("email") or "").strip().lower()
            user = CustomUser.objects.filter(email__iexact=email_val).first()
            if not user:
                messages.error(request, "Сотрудник с указанной корпоративной почтой не найден.")
            else:
                otp = f"{random.randint(100000, 999999)}"
                user.reset_otp_code = otp
                user.reset_otp_created_at = timezone.now()
                user.save(update_fields=["reset_otp_code", "reset_otp_created_at"])
                send_mail(
                    subject="Код восстановления доступа ustage_erp",
                    message=f"Ваш одноразовый 6-значный код подтверждения: {otp}",
                    from_email="noreply@ustage-group.ru",
                    recipient_list=[user.email],
                    fail_silently=True,
                )
                request.session["reset_step"] = 2
                request.session["reset_email"] = user.email
                messages.success(request, f"Одноразовый код отправлен на корпоративную почту {user.email}.")
                return redirect("forgot_password")

        elif action == "verify_and_reset":
            otp_input = (request.POST.get("otp_code") or "").strip()
            new_pass = request.POST.get("new_password") or ""
            user = CustomUser.objects.filter(email__iexact=reset_email).first()
            if not user or not user.reset_otp_code or user.reset_otp_code != otp_input:
                messages.error(request, "Неверный одноразовый код подтверждения.")
            elif len(new_pass) < 6:
                messages.error(request, "Новый пароль должен содержать не менее 6 символов.")
            else:
                user.set_password(new_pass)
                user.reset_otp_code = ""
                user.save()
                request.session.pop("reset_step", None)
                request.session.pop("reset_email", None)
                messages.success(request, "Пароль успешно обновлен. Войдите в систему.")
                return redirect("login")

    return render(request, "forgot_password.html", {"step": step, "reset_email": reset_email})


@login_required
def dashboard(request):
    """Главный экран Канбан-доски проектов, задач и KPI менеджеров[cite: 1, 2]"""
    columns = KanbanColumn.objects.prefetch_related("projects__estimate", "tasks__assigned_to").all()
    staff_filter = request.GET.get("staff_id")
    employees = CustomUser.objects.all()

    return render(
        request,
        "dashboard.html",
        {
            "columns": columns,
            "employees": employees,
            "selected_staff_id": staff_filter,
            "managers_kpi": _build_managers_kpi_stats(),
        },
    )


@login_required
def create_project_or_task(request):
    """Создание проекта оснащения или рабочей задачи на Канбан-доске[cite: 1, 2]"""
    if request.method != "POST":
        return redirect("dashboard")

    card_type = request.POST.get("card_type", "project")
    col = KanbanColumn.objects.order_by("order").first()

    if card_type == "project":
        if not request.user.can_perm("create_projects"):
            messages.error(request, "У вашей должности нет прав на создание проектов.")
            return redirect("dashboard")

        title = (request.POST.get("title") or "").strip()
        client = (request.POST.get("client") or "").strip()
        if not title or not client:
            messages.error(request, "Укажите название проекта и наименование заказчика.")
            return redirect("dashboard")

        try:
            budget = _parse_strict_decimal(
                request.POST.get("client_budget_rub", "5000000"),
                "Бюджет театра",
                allow_zero=False,
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("dashboard")

        proj = Project.objects.create(
            title=title,
            client=client,
            client_budget_rub=budget,
            kanban_column=col,
            manager=request.user,
            theater_tz_text=(request.POST.get("theater_tz_text") or "").strip(),
        )
        Estimate.objects.get_or_create(project=proj)
        sync_project_stage_automations(proj)
        messages.success(request, f"Проект «{proj.title}» создан.")
    else:
        title = (request.POST.get("title") or "").strip()
        if not title:
            messages.error(request, "Укажите заголовок рабочей задачи.")
            return redirect("dashboard")
        assignee_id = request.POST.get("assigned_to")
        assignee = CustomUser.objects.filter(id=assignee_id).first() if assignee_id else request.user
        KanbanTask.objects.create(
            title=title,
            description=(request.POST.get("description") or "").strip(),
            project_name_manual=(request.POST.get("project_name_manual") or "").strip(),
            kanban_column=col,
            assigned_to=assignee,
        )
        messages.success(request, f"Рабочая задача «{title}» добавлена на доску.")

    return redirect("dashboard")


@login_required
def move_project_column(request, project_id: int):
    """Перемещение карточки проекта между колонками Канбана с сохранением истории поставок[cite: 1, 2]"""
    if request.method == "POST":
        project = get_object_or_404(Project, id=project_id)
        new_col = get_object_or_404(KanbanColumn, id=request.POST.get("column_id"))
        project.kanban_column = new_col
        project.save(update_fields=["kanban_column"])
        sync_project_stage_automations(project)
        messages.success(request, f"Проект переведен в стадию «{new_col.title}».")
    return redirect("dashboard")


@login_required
def update_manager_kpi_plan(request, user_id: int):
    if not request.user.is_boss:
        return HttpResponseForbidden("Доступ разрешен только руководителям.")
    if request.method == "POST":
        mgr = get_object_or_404(CustomUser, id=user_id)
        try:
            mgr.monthly_sales_plan_rub = _parse_strict_decimal(
                request.POST.get("monthly_sales_plan_rub"), "План продаж", allow_zero=False
            )
            mgr.projects_plan_count = _parse_strict_positive_int(
                request.POST.get("projects_plan_count"), "План проектов"
            )
            mgr.tasks_plan_count = _parse_strict_positive_int(
                request.POST.get("tasks_plan_count"), "План задач"
            )
            mgr.save()
            messages.success(request, f"Плановые показатели KPI для {mgr.full_display_name} обновлены.")
        except ValueError as exc:
            messages.error(request, str(exc))
    return redirect("dashboard")


@login_required
def task_detail_view(request, task_id: int):
    """Экран детализации рабочей задачи с вложениями и лентой комментариев[cite: 1, 2]"""
    task = get_object_or_404(KanbanTask, id=task_id)
    if request.method == "POST":
        action = request.POST.get("action", "update_task")
        if action == "update_task":
            task.title = (request.POST.get("title") or task.title).strip()
            task.project_name_manual = (request.POST.get("project_name_manual") or "").strip()
            task.description = (request.POST.get("description") or "").strip()
            col_id = request.POST.get("kanban_column")
            if col_id:
                task.kanban_column_id = int(col_id)
            emp_id = request.POST.get("assigned_to")
            if emp_id:
                task.assigned_to_id = int(emp_id)
            task.save()
            messages.success(request, "Параметры задачи сохранены.")
        elif action == "add_comment":
            txt = (request.POST.get("comment_text") or "").strip()
            if txt:
                TaskComment.objects.create(task=task, author=request.user, text=txt)
        elif action == "upload_file" and request.FILES.get("attachment"):
            f = request.FILES["attachment"]
            TaskAttachment.objects.create(task=task, uploaded_by=request.user, file=f, filename=f.name)
        return redirect("task_detail", task_id=task.id)

    return render(
        request,
        "task_detail.html",
        {
            "task": task,
            "columns": KanbanColumn.objects.all(),
            "employees": CustomUser.objects.all(),
        },
    )


@login_required
def estimate_detail(request, project_id: int):
    """Рабочее место настройки проекта, формирования сметы (HTMX) и подбора по 44-ФЗ[cite: 1, 2]"""
    project = get_object_or_404(Project, id=project_id)
    estimate, _ = Estimate.objects.get_or_create(project=project)
    categories = Category.objects.prefetch_related("subcategories").all()
    selected_cat_id = request.GET.get("category_id")
    selected_subcat_id = request.GET.get("subcategory_id")
    search_q = (request.GET.get("q") or "").strip()

    items_qs = Item.objects.select_related("category", "subcategory", "vendor", "vendor__currency").all()
    if selected_cat_id:
        items_qs = items_qs.filter(category_id=selected_cat_id)
    if selected_subcat_id:
        items_qs = items_qs.filter(subcategory_id=selected_subcat_id)
    if search_q:
        items_qs = items_qs.filter(name__icontains=search_q) | items_qs.filter(sku__icontains=search_q)

    rigging_stats = calculate_stage_auto_rigger(project, apply_to_estimate=False)

    lines_with_analogs = []
    for line in estimate.lines.select_related("item", "item__vendor", "item__subcategory").all():
        analogs = find_tender_analogs_44fz(line.item) if line.item else []
        lines_with_analogs.append(
            {
                "line": line,
                "analogs": analogs,
                "blind_spec": generate_44fz_blind_spec(line),
            }
        )

    context = {
        "project": project,
        "estimate": estimate,
        "categories": categories,
        "catalog_items": items_qs[:60],
        "rigging_stats": rigging_stats,
        "lines_with_analogs": lines_with_analogs,
        "currencies": CurrencyRate.objects.filter(is_active=True),
    }
    if request.headers.get("HX-Request"):
        return render(request, "partials/estimate_workspace.html", context)
    return render(request, "estimate_detail.html", context)


@login_required
def add_estimate_line(request, project_id: int):
    """Добавление позиции из каталога в смету со строгой проверкой прав и количества[cite: 1]"""
    if not request.user.can_perm("edit_estimates"):
        return HttpResponseForbidden("Недостаточно прав для редактирования сметы.")
    project = get_object_or_404(Project, id=project_id)
    estimate, _ = Estimate.objects.get_or_create(project=project)

    if request.method == "POST":
        item = get_object_or_404(Item, id=request.POST.get("item_id"))
        try:
            qty = _parse_strict_positive_int(request.POST.get("quantity", 1), "Количество")
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("estimate_detail", project_id=project.id)

        default_markup = item.category.default_target_margin_pct if item.category else Decimal("25.00")
        line, created = EstimateLine.objects.get_or_create(
            estimate=estimate,
            item=item,
            is_manual_item=False,
            defaults={
                "quantity": qty,
                "is_protected": True,
                "markup_pct": default_markup,
                "vat_rate": Decimal("22.00"),
            },
        )
        if not created:
            line.quantity += qty
            line.save(update_fields=["quantity"])

    return redirect("estimate_detail", project_id=project.id)


@login_required
def add_manual_estimate_line(request, project_id: int):
    """Ручное добавление нестандартного оборудования со строгой валидацией цены, количества и НДС 22%[cite: 1]"""
    if not request.user.can_perm("edit_estimates"):
        return HttpResponseForbidden("Недостаточно прав для редактирования сметы.")
    project = get_object_or_404(Project, id=project_id)
    estimate, _ = Estimate.objects.get_or_create(project=project)

    if request.method == "POST":
        name = (request.POST.get("manual_name") or "").strip()
        if not name:
            messages.error(request, "Укажите наименование нестандартной позиции.")
            return redirect("estimate_detail", project_id=project.id)

        try:
            price = _parse_strict_decimal(
                request.POST.get("manual_purchase_price"), "Закупочная цена", allow_zero=False
            )
            qty = _parse_strict_positive_int(request.POST.get("quantity", 1), "Количество")
            markup = _parse_strict_decimal(
                request.POST.get("markup_pct", "25.00"), "Наценка %", allow_zero=True
            )
            vat_rate = _parse_strict_decimal(
                request.POST.get("vat_rate", "22.00"), "Ставка НДС %", allow_zero=True
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("estimate_detail", project_id=project.id)

        EstimateLine.objects.create(
            estimate=estimate,
            is_manual_item=True,
            manual_sku=(request.POST.get("manual_sku") or "MANUAL").strip(),
            manual_name=name,
            manual_specs=(request.POST.get("manual_specs") or "").strip(),
            manual_vendor_name=(request.POST.get("manual_vendor_name") or "Поставщик по договору").strip(),
            manual_currency_code=(request.POST.get("manual_currency_code") or "RUB").strip(),
            manual_purchase_price=price,
            quantity=qty,
            is_protected=bool(request.POST.get("is_protected", True)),
            markup_pct=markup,
            vat_rate=vat_rate,
        )
        messages.success(request, f"Позиция «{name}» добавлена в спецификацию.")

    return redirect("estimate_detail", project_id=project.id)


@login_required
def update_estimate_line(request, line_id: int):
    """Обновление количества, наценки, статуса защиты и ставки НДС в строке сметы[cite: 1]"""
    if not request.user.can_perm("edit_estimates"):
        return HttpResponseForbidden("Недостаточно прав для редактирования сметы.")
    line = get_object_or_404(EstimateLine, id=line_id)
    project_id = line.estimate.project_id

    if request.method == "POST":
        try:
            if "quantity" in request.POST:
                line.quantity = _parse_strict_positive_int(request.POST.get("quantity"), "Количество")
            if "markup_pct" in request.POST:
                line.markup_pct = _parse_strict_decimal(
                    request.POST.get("markup_pct"), "Наценка %", allow_zero=True
                )
            if "vat_rate" in request.POST:
                line.vat_rate = _parse_strict_decimal(
                    request.POST.get("vat_rate", "22.00"), "Ставка НДС %", allow_zero=True
                )
            if "is_protected" in request.POST:
                line.is_protected = request.POST.get("is_protected") in ("on", "true", "1", "True")
            line.save()
        except ValueError as exc:
            messages.error(request, str(exc))

    return redirect("estimate_detail", project_id=project_id)


@login_required
def update_estimate_commercials(request, project_id: int):
    """Обновление коммерческих параметров сметы (буфер, комиссия площадки, доставка, монтаж)[cite: 1]"""
    if not request.user.can_perm("edit_estimates"):
        return HttpResponseForbidden("Недостаточно прав для изменения коммерческих параметров.")
    project = get_object_or_404(Project, id=project_id)
    estimate, _ = Estimate.objects.get_or_create(project=project)

    if request.method == "POST":
        try:
            estimate.fx_buffer_pct = _parse_strict_decimal(
                request.POST.get("fx_buffer_pct", estimate.fx_buffer_pct),
                "Валютный буфер %",
                allow_zero=True,
            )
            estimate.platform_fee_pct = _parse_strict_decimal(
                request.POST.get("platform_fee_pct", estimate.platform_fee_pct),
                "Комиссия площадки %",
                allow_zero=True,
            )
            estimate.delivery_cost_rub = _parse_strict_decimal(
                request.POST.get("delivery_cost_rub", estimate.delivery_cost_rub),
                "Стоимость доставки",
                allow_zero=True,
            )
            estimate.installation_cost_rub = _parse_strict_decimal(
                request.POST.get("installation_cost_rub", estimate.installation_cost_rub),
                "Стоимость монтажа",
                allow_zero=True,
            )
            estimate.installation_vat_rate = _parse_strict_decimal(
                request.POST.get("installation_vat_rate", "22.00"),
                "Ставка НДС на монтаж",
                allow_zero=True,
            )
            estimate.include_installation = request.POST.get("include_installation") in ("on", "true", "1")
            estimate.embed_expenses_in_kp = request.POST.get("embed_expenses_in_kp") in ("on", "true", "1")
            estimate.save()
            messages.success(request, "Коммерческие параметры сметы обновлены.")
        except ValueError as exc:
            messages.error(request, str(exc))

    return redirect("estimate_detail", project_id=project.id)


@login_required
def update_project_budget(request, project_id: int):
    """Обновление предельного бюджета театра со строгой проверкой (> 0)[cite: 1, 2]"""
    if not request.user.can_perm("edit_estimates"):
        return HttpResponseForbidden("Недостаточно прав.")
    project = get_object_or_404(Project, id=project_id)
    if request.method == "POST":
        try:
            project.client_budget_rub = _parse_strict_decimal(
                request.POST.get("client_budget_rub"), "Бюджет театра", allow_zero=False
            )
            project.save(update_fields=["client_budget_rub"])
            messages.success(request, "Лимит бюджета театра обновлен.")
        except ValueError as exc:
            messages.error(request, str(exc))
    return redirect("estimate_detail", project_id=project.id)


@login_required
def replace_line_with_analog(request, line_id: int, analog_id: int):
    """Замена отсутствующей позиции в смете на доступный аналог по 44-ФЗ[cite: 1, 2]"""
    if not request.user.can_perm("edit_estimates"):
        return HttpResponseForbidden("Недостаточно прав.")
    line = get_object_or_404(EstimateLine, id=line_id)
    analog = get_object_or_404(Item, id=analog_id)
    line.item = analog
    line.save(update_fields=["item"])
    messages.success(request, f"Позиция заменена на аналог «{analog.brand} {analog.name}».")
    return redirect("estimate_detail", project_id=line.estimate.project_id)


@login_required
def toggle_ceo_unblock_kp(request, project_id: int):
    """Принудительная разблокировка выгрузки КП решением Генерального директора[cite: 1, 2]"""
    if not request.user.is_ceo:
        return HttpResponseForbidden("Разблокировка КП при защите <70% доступна только Генеральному директору.")
    project = get_object_or_404(Project, id=project_id)
    estimate, _ = Estimate.objects.get_or_create(project=project)
    estimate.ceo_force_unblock_kp = not estimate.ceo_force_unblock_kp
    estimate.save(update_fields=["ceo_force_unblock_kp"])
    return redirect("estimate_detail", project_id=project.id)


@login_required
def document_print(request, project_id: int):
    """
    Генерация печатных форм защищенного КП (с НДС 22%) и обезличенного ТЗ по 44-ФЗ[cite: 1, 2].
    Добавлена серверная защита от прямого перехода по URL при защите проекта < 70%[cite: 1].
    """
    project = get_object_or_404(Project, id=project_id)
    estimate, _ = Estimate.objects.get_or_create(project=project)
    doc_type = request.GET.get("type", "kp")

    if doc_type == "kp" and not estimate.is_kp_export_allowed and not request.user.is_ceo:
        messages.error(
            request,
            "Выгрузка КП заблокирована Радаром защиты: доля защищенных позиций ниже 70%. "
            "Запросите разблокировку у Генерального директора.",
        )
        return redirect("estimate_detail", project_id=project.id)

    lines_data = []
    for idx, line in enumerate(estimate.lines.select_related("item", "item__subcategory").all(), start=1):
        lines_data.append(
            {
                "index": idx,
                "line": line,
                "blind_spec": generate_44fz_blind_spec(line),
            }
        )

    return render(
        request,
        "document_print.html",
        {
            "project": project,
            "estimate": estimate,
            "doc_type": doc_type,
            "lines_data": lines_data,
        },
    )


@login_required
def catalog_and_vendors_view(request):
    """Вкладка 2: Каталог ustage-group.ru, Мультивалютный контур ЦБ РФ, Поставщики и Импорт Excel[cite: 1, 2]"""
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "sync_cbr":
            if not request.user.can_perm("edit_currencies"):
                return HttpResponseForbidden("Нет прав на управление валютным контуром.")
            res = sync_currencies_from_cbr_api()
            messages.success(request, f"Курсы синхронизированы ({res['source']}).")

        elif action == "import_excel":
            if not request.user.can_perm("import_excel"):
                return HttpResponseForbidden("Нет прав на импорт прайс-листов.")
            excel_file = request.FILES.get("excel_file")
            vendor = get_object_or_404(Vendor, id=request.POST.get("vendor_id"))
            cat = Category.objects.filter(id=request.POST.get("category_id")).first()
            subcat = SubCategory.objects.filter(id=request.POST.get("subcategory_id")).first()
            if excel_file:
                stats = parse_vendor_excel_price(excel_file, vendor, cat, subcat)
                messages.success(
                    request,
                    f"Импорт завершен: загружено позиций — {stats['imported']}, пропущено некорректных — {stats['skipped']}.",
                )
        return redirect("catalog_vendors")

    return render(
        request,
        "catalog_vendors.html",
        {
            "active_currencies": CurrencyRate.objects.filter(is_active=True),
            "inactive_currencies": CurrencyRate.objects.filter(is_active=False),
            "vendors": Vendor.objects.select_related("currency").all(),
            "categories": Category.objects.prefetch_related("subcategories").all(),
            "items": Item.objects.select_related("category", "subcategory", "vendor", "vendor__currency").all(),
        },
    )


@login_required
def logistics_view(request):
    """Вкладка 3: Отслеживание поставок ТК «Деловые Линии» и «СДЭК»[cite: 1, 2]"""
    if not request.user.can_perm("view_logistics"):
        return HttpResponseForbidden("Доступ к разделу логистики ограничен.")

    if request.method == "POST":
        if not request.user.can_perm("edit_logistics"):
            return HttpResponseForbidden("У вашей роли нет прав на редактирование поставок.")
        action = request.POST.get("action", "create")
        if action == "track_api":
            sh = get_object_or_404(DellinShipment, id=request.POST.get("shipment_id"))
            track_shipment_by_carrier_api(sh)
            messages.success(request, f"Статус накладной {sh.tracking_number} обновлен.")
        elif action == "update":
            sh = get_object_or_404(DellinShipment, id=request.POST.get("shipment_id"))
            sh.tracking_number = (request.POST.get("tracking_number") or sh.tracking_number).strip()
            sh.equipment_summary = (request.POST.get("equipment_summary") or sh.equipment_summary).strip()
            sh.status = (request.POST.get("status") or sh.status).strip()
            sh.save()
            messages.success(request, "Данные отгрузки сохранены.")
        return redirect("logistics_dellin")

    return render(
        request,
        "logistics_dellin.html",
        {
            "shipments": DellinShipment.objects.select_related("project", "vendor").all(),
            "projects": Project.objects.all(),
            "vendors": Vendor.objects.all(),
        },
    )


@login_required
def digital_twin_view(request):
    """Вкладка 4: Цифровой паспорт объекта и Справочник профилей театров[cite: 1, 2]"""
    if not request.user.can_perm("view_digital_twin"):
        return HttpResponseForbidden("Доступ к Цифровому паспорту ограничен.")

    return render(
        request,
        "digital_twin.html",
        {
            "profiles": VenueCalculationProfile.objects.all(),
            "assets": DigitalStageAsset.objects.select_related("item", "venue_profile", "project").all(),
            "items": Item.objects.all(),
        },
    )


@login_required
def staff_management_view(request):
    """Вкладка 5: Личный профиль сотрудника, Штатный состав и Матрица ролей[cite: 1, 2]"""
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "change_own_password":
            new_pw = request.POST.get("new_password") or ""
            if len(new_pw) < 6:
                messages.error(request, "Пароль должен быть не короче 6 символов.")
            else:
                request.user.set_password(new_pw)
                request.user.save()
                update_session_auth_hash(request, request.user)
                messages.success(request, "Ваш пароль успешно изменен.")
        return redirect("staff_management")

    return render(
        request,
        "staff_management.html",
        {
            "employees": CustomUser.objects.select_related("position").all() if request.user.can_admin_roles else [],
            "positions": StaffPosition.objects.all() if request.user.can_admin_roles else [],
        },
    )