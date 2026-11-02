import random
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.core.mail import send_mail

from catalog.models import StaffPosition, CustomUser, CurrencyRate, Vendor, Category, SubCategory, Item
from projects.models import (
    KanbanColumn, Project, KanbanTask, TaskComment, TaskAttachment,
    Estimate, EstimateLine, DellinShipment, VenueCalculationProfile, DigitalStageAsset
)
from projects.services import (
    to_decimal, sync_currencies_from_cbr_api, parse_vendor_excel_price,
    calculate_stage_auto_rigger, apply_auto_rigger_lines_to_estimate,
    parse_theater_tz_and_sync, generate_44fz_blind_spec,
    track_shipment_by_carrier_api, sync_project_stage_automations
)


def get_active_user(request):
    if request.user.is_authenticated:
        return request.user
    return CustomUser.objects.first()


def _base_ctx(request, active_tab='kanban'):
    user = get_active_user(request)
    return {
        'active_user': user,
        'active_tab': active_tab,
        'is_ceo': user.is_ceo if user else False,
        'is_boss': user.is_boss if user else False,
        'can_admin_roles': user.can_admin_roles if user else False,
    }


# ==============================================================================
# АВТОРИЗАЦИЯ ПО КОРПОРАТИВНОЙ ПОЧТЕ И ДВУХЭТАПНЫЙ OTP-СБРОС ПАРОЛЯ
# ==============================================================================
def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    error_msg = None
    if request.method == 'POST':
        email_in = request.POST.get('email', '').strip().lower()
        password_in = request.POST.get('password', '').strip()

        user_obj = CustomUser.objects.filter(email__iexact=email_in).first()
        if not user_obj:
            error_msg = "Сотрудник с такой корпоративной почтой (@ustage-group.ru) не найден в штате ООО «Юстейдж»."
        elif not user_obj.check_password(password_in):
            error_msg = "Неверный пароль. Проверьте раскладку или воспользуйтесь восстановлением через почту."
        else:
            login(request, user_obj)
            messages.success(request, f"Вы успешно вошли как: {user_obj.first_name} ({user_obj.email})")
            return redirect('dashboard')

    return render(request, 'login.html', {'error_msg': error_msg})


def logout_view(request):
    logout(request)
    return redirect('login')


def forgot_password_view(request):
    step = request.session.get('reset_step', 1)
    target_email = request.session.get('reset_email', '')
    error_msg = None
    info_msg = None

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'Restart':
            request.session.pop('reset_step', None)
            request.session.pop('reset_email', None)
            request.session.pop('reset_otp', None)
            return redirect('forgot_password')

        if step == 1:
            email_in = request.POST.get('email', '').strip().lower()
            user_obj = CustomUser.objects.filter(email__iexact=email_in).first()
            if not user_obj:
                error_msg = "Указанный адрес корпоративной почты не зарегистрирован в базе сотрудников ООО «Юстейдж»."
            else:
                otp_code = f"{random.randint(100000, 999999)}"
                request.session['reset_step'] = 2
                request.session['reset_email'] = user_obj.email
                request.session['reset_otp'] = otp_code
                step = 2
                target_email = user_obj.email
                try:
                    send_mail(
                        subject="USTAGE GROUP — Код восстановления корпоративного пароля",
                        message=f"Здравствуйте, {user_obj.first_name}!\nВаш одноразовый код подтверждения: {otp_code}",
                        from_email="security@ustage-group.ru",
                        recipient_list=[user_obj.email],
                        fail_silently=True,
                    )
                except Exception:
                    pass
                info_msg = f"Одноразовый 6-значный код отправлен на {user_obj.email} (код для демонстрации на стенде: {otp_code})."

        elif step == 2:
            otp_in = request.POST.get('otp_code', '').strip()
            new_pass = request.POST.get('new_password', '').strip()
            saved_otp = request.session.get('reset_otp')
            user_obj = CustomUser.objects.filter(email__iexact=target_email).first()

            if not user_obj or otp_in != saved_otp:
                error_msg = "Неверный одноразовый код подтверждения."
            elif len(new_pass) < 4:
                error_msg = "Новый пароль должен содержать не менее 4 символов."
            else:
                user_obj.set_password(new_pass)
                user_obj.save()
                request.session.pop('reset_step', None)
                request.session.pop('reset_email', None)
                request.session.pop('reset_otp', None)
                messages.success(request, "Пароль успешно изменен! Войдите с новым паролем.")
                return redirect('login')

    return render(request, 'forgot_password.html', {
        'step': step,
        'target_email': target_email,
        'error_msg': error_msg,
        'info_msg': info_msg,
    })


# ==============================================================================
# 1. КАНБАН-ДОСКА ПРОЕКТОВ И ЗАДАЧ + СТАТИСТИКА KPI МЕНЕДЖЕРОВ
# ==============================================================================
def _build_managers_kpi_stats():
    mgrs = CustomUser.objects.select_related('position').filter(
        position__is_sales_manager=True
    ).order_by('id')
    stats = []
    for m in mgrs:
        projs = Project.objects.filter(manager=m).select_related('kanban_column', 'estimate')
        total_projects = projs.count()
        completed_projects = 0
        fact_revenue = Decimal('0.00')
        completed_revenue = Decimal('0.00')

        for p in projs:
            est = getattr(p, 'estimate', None)
            p_sum = est.total_client_price_rub if est else Decimal('0.00')
            fact_revenue += p_sum
            if p.kanban_column and p.kanban_column.is_completed_stage:
                completed_projects += 1
                completed_revenue += p_sum

        tasks_qs = KanbanTask.objects.filter(assigned_to=m)
        total_tasks = tasks_qs.count()
        done_tasks = tasks_qs.filter(is_completed=True).count()

        plan_rub = m.monthly_sales_plan_rub or Decimal('1.00')
        rev_pct = int(((fact_revenue / plan_rub) * Decimal('100.0')).quantize(Decimal('1'), rounding=ROUND_HALF_UP)) if plan_rub > 0 else 100
        rev_bar_pct = min(rev_pct, 100)

        if rev_pct >= 80:
            status_color = 'green'
            status_label = f"Выполнено {rev_pct}% плана"
        elif rev_pct >= 45:
            status_color = 'yellow'
            status_label = f"В работе ({rev_pct}% плана)"
        else:
            status_color = 'red'
            status_label = f"Отставание ({rev_pct}% плана)"

        stats.append({
            'manager': m,
            'total_projects': total_projects,
            'completed_projects': completed_projects,
            'total_tasks': total_tasks,
            'done_tasks': done_tasks,
            'fact_revenue': fact_revenue.quantize(Decimal('0.01')),
            'completed_revenue': completed_revenue.quantize(Decimal('0.01')),
            'rev_pct': rev_pct,
            'rev_bar_pct': rev_bar_pct,
            'status_color': status_color,
            'status_label': status_label,
        })
    return stats


@login_required
def dashboard(request):
    ctx = _base_ctx(request, 'kanban')
    user = ctx['active_user']

    selected_mgr_id = request.GET.get('manager_id')
    selected_mgr_id = int(selected_mgr_id) if (selected_mgr_id and selected_mgr_id.isdigit()) else None

    all_columns = list(KanbanColumn.objects.all())
    kanban_columns = []
    for col in all_columns:
        projs_qs = Project.objects.filter(kanban_column=col).select_related('manager', 'estimate')
        tasks_qs = KanbanTask.objects.filter(kanban_column=col).select_related('assigned_to', 'project')
        if selected_mgr_id:
            projs_qs = projs_qs.filter(manager_id=selected_mgr_id)
            tasks_qs = tasks_qs.filter(assigned_to_id=selected_mgr_id)

        cards = []
        for p in projs_qs:
            est, _ = Estimate.objects.get_or_create(project=p)
            cards.append({'project': p, 'estimate': est})

        t_list = list(tasks_qs)
        kanban_columns.append({
            'col': col,
            'cards': cards,
            'tasks': t_list,
            'total_items': len(cards) + len(t_list),
        })

    pos_Code = user.position.code if (user and user.position) else ''
    can_create_projects = pos_Code not in ('engineer', 'warehouse')

    ctx.update({
        'all_columns': all_columns,
        'kanban_columns': kanban_columns,
        'sales_managers': CustomUser.objects.all().order_by('first_name'),
        'selected_mgr_id': selected_mgr_id,
        'can_create_projects': can_create_projects,
        'managers_kpi_stats': _build_managers_kpi_stats() if ctx['is_boss'] else [],
    })
    return render(request, 'dashboard.html', ctx)


@login_required
@require_POST
def update_manager_plan(request):
    user = get_active_user(request)
    if not user.is_boss:
        return redirect('dashboard')

    mgr_id = request.POST.get('manager_id')
    mgr = get_object_or_404(CustomUser, id=mgr_id)
    mgr.monthly_sales_plan_rub = to_decimal(request.POST.get('monthly_sales_plan_rub'), '10000000.00')
    mgr.projects_plan_count = max(int(request.POST.get('projects_plan_count') or 3), 1)
    mgr.tasks_plan_count = max(int(request.POST.get('tasks_plan_count') or 5), 1)
    mgr.save()
    messages.success(request, f"Плановые показатели KPI для {mgr.first_name} обновлены.")
    return redirect('dashboard')


@login_required
@require_POST
def create_kanban_project(request):
    user = get_active_user(request)
    title = request.POST.get('project_title', '').strip()
    client = request.POST.get('client_name', '').strip()
    budget = to_decimal(request.POST.get('client_budget_rub'), '0.00')
    tz_text = request.POST.get('theater_tz_text', '').strip()
    col_id = request.POST.get('column_id')
    mgr_id = request.POST.get('manager_id')

    col = KanbanColumn.objects.filter(id=col_id).first() or KanbanColumn.objects.first()
    mgr = CustomUser.objects.filter(id=mgr_id).first() or user

    if title and client:
        proj = Project.objects.create(
            title=title,
            client=client,
            client_budget_rub=budget,
            kanban_column=col,
            manager=mgr,
            theater_tz_text=tz_text,
        )
        est = Estimate.objects.create(project=proj)
        if tz_text:
            parse_theater_tz_and_sync(est, tz_text, client_budget_rub=budget)
        sync_project_stage_automations(proj)
        messages.success(request, f"Проект «{proj.title}» создан на Канбан-доске.")
    return redirect('dashboard')


@login_required
@require_POST
def create_kanban_task(request):
    user = get_active_user(request)
    title = request.POST.get('title', '').strip()
    proj_text = request.POST.get('project_title_text', '').strip()
    desc = request.POST.get('description', '').strip()
    col_id = request.POST.get('column_id')
    assigned_id = request.POST.get('assigned_to_id')

    col = KanbanColumn.objects.filter(id=col_id).first() or KanbanColumn.objects.first()
    assignee = CustomUser.objects.filter(id=assigned_id).first() or user
    linked_proj = Project.objects.filter(title__icontains=proj_text).first() if proj_text else None

    if title:
        task = KanbanTask.objects.create(
            project=linked_proj,
            project_name_manual=proj_text,
            kanban_column=col,
            assigned_to=assignee,
            title=title,
            description=desc,
            due_date=date.today() + timedelta(days=5),
            is_completed=col.is_completed_stage if col else False,
        )
        if 'attachment' in request.FILES:
            f = request.FILES['attachment']
            TaskAttachment.objects.create(task=task, uploaded_by=user, file=f, filename=f.name)
        messages.success(request, f"Задача «{task.title}» размещена на Канбан-доске.")
    return redirect('dashboard')


@login_required
@require_POST
def move_kanban_card(request, project_id):
    proj = get_object_or_404(Project, id=project_id)
    col_id = request.POST.get('column_id')
    new_col = KanbanColumn.objects.filter(id=col_id).first()
    if new_col:
        proj.kanban_column = new_col
        proj.save()
        sync_project_stage_automations(proj)
    return redirect('dashboard')


@login_required
@require_POST
def move_or_toggle_task(request, task_id):
    task = get_object_or_404(KanbanTask, id=task_id)
    if 'delete_task' in request.POST:
        task.delete()
        return redirect('dashboard')

    if 'toggle_done' in request.POST:
        task.is_completed = not task.is_completed
        if task.is_completed:
            done_col = KanbanColumn.objects.filter(is_completed_stage=True).first()
            if done_col:
                task.kanban_column = done_col
        task.save()
        return redirect('dashboard')

    col_id = request.POST.get('column_id')
    new_col = KanbanColumn.objects.filter(id=col_id).first()
    if new_col:
        task.kanban_column = new_col
        task.is_completed = new_col.is_completed_stage
        task.save()
    return redirect('dashboard')


@login_required
@require_POST
def manage_kanban_columns(request):
    user = get_active_user(request)
    if not user.is_boss:
        return redirect('dashboard')

    action = request.POST.get('action')
    if action == 'add':
        KanbanColumn.objects.create(
            order=int(request.POST.get('order') or 6),
            title=request.POST.get('title', 'Новый раздел').strip(),
            is_logistics_stage=bool(request.POST.get('is_logistics_stage')),
            is_completed_stage=bool(request.POST.get('is_completed_stage')),
        )
    elif action == 'update':
        col = get_object_or_404(KanbanColumn, id=request.POST.get('column_id'))
        col.order = int(request.POST.get('order') or col.order)
        col.title = request.POST.get('title', col.title).strip()
        col.is_logistics_stage = bool(request.POST.get('is_logistics_stage'))
        col.is_completed_stage = bool(request.POST.get('is_completed_stage'))
        col.save()
        for p in col.projects.all():
            sync_project_stage_automations(p)
    elif action == 'delete':
        col = get_object_or_404(KanbanColumn, id=request.POST.get('column_id'))
        fallback = KanbanColumn.objects.exclude(id=col.id).first()
        if fallback:
            col.projects.update(kanban_column=fallback)
            col.tasks.update(kanban_column=fallback)
            col.delete()
    return redirect('dashboard')


@login_required
def task_detail(request, task_id):
    ctx = _base_ctx(request, 'kanban')
    user = ctx['active_user']
    task = get_object_or_404(KanbanTask, id=task_id)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'update_info':
            task.title = request.POST.get('title', task.title).strip()
            task.project_name_manual = request.POST.get('project_name_manual', task.project_name_manual).strip()
            task.description = request.POST.get('description', task.description).strip()
            col_id = request.POST.get('column_id')
            ass_id = request.POST.get('assigned_to_id')
            if col_id:
                col_obj = KanbanColumn.objects.filter(id=col_id).first()
                if col_obj:
                    task.kanban_column = col_obj
                    task.is_completed = col_obj.is_completed_stage
            if ass_id:
                task.assigned_to_id = int(ass_id)
            task.save()
            messages.success(request, "Параметры рабочей задачи сохранены.")
        elif action == 'add_comment':
            txt = request.POST.get('comment_text', '').strip()
            if txt:
                TaskComment.objects.create(task=task, author=user, text=txt)
        elif action == 'upload_file' and 'attachment' in request.FILES:
            f = request.FILES['attachment']
            TaskAttachment.objects.create(task=task, uploaded_by=user, file=f, filename=f.name)
        elif action == 'delete_file':
            att_id = request.POST.get('attachment_id')
            TaskAttachment.objects.filter(id=att_id, task=task).delete()
        return redirect('task_detail', task_id=task.id)

    ctx.update({
        'task': task,
        'all_columns': KanbanColumn.objects.all(),
        'employees': CustomUser.objects.all().order_by('first_name'),
    })
    return render(request, 'task_detail.html', ctx)


# ==============================================================================
# 2. РАБОЧЕЕ МЕСТО ПРОЕКТА: СМЕТА, ТЗ ТЕАТРА, ВШИТЫЕ РАСХОДЫ, НДС И 44-ФЗ
# ==============================================================================
def _render_estimate_workspace(request, estimate):
    ctx = _base_ctx(request, 'kanban')
    cat_id = request.GET.get('cat_id') or request.POST.get('cat_id')
    subcat_id = request.GET.get('subcat_id') or request.POST.get('subcat_id')
    q = (request.GET.get('q') or request.POST.get('q') or '').strip()

    items_qs = Item.objects.select_related('category', 'subcategory', 'vendor', 'vendor__currency').all()
    subcats_qs = SubCategory.objects.select_related('category').all()

    selected_cat_id = int(cat_id) if (cat_id and str(cat_id).isdigit()) else None
    selected_subcat_id = int(subcat_id) if (subcat_id and str(subcat_id).isdigit()) else None

    if selected_cat_id:
        items_qs = items_qs.filter(category_id=selected_cat_id)
        subcats_qs = subcats_qs.filter(category_id=selected_cat_id)
    if selected_subcat_id:
        items_qs = items_qs.filter(subcategory_id=selected_subcat_id)
    if q:
        items_qs = (
            items_qs.filter(name__icontains=q)
            | items_qs.filter(brand__icontains=q)
            | items_qs.filter(sku__icontains=q)
            | items_qs.filter(specs_text__icontains=q)
        )

    ctx.update({
        'estimate': estimate,
        'project': estimate.project,
        'rigger_calc': calculate_stage_auto_rigger(estimate),
        'categories': Category.objects.all(),
        'subcategories': subcats_qs,
        'catalog_items': items_qs[:40],
        'selected_cat_id': selected_cat_id,
        'selected_subcat_id': selected_subcat_id,
        'search_query': q,
    })
    if request.headers.get('HX-Request'):
        return render(request, 'partials/estimate_workspace.html', ctx)
    return render(request, 'estimate_detail.html', ctx)


@login_required
def estimate_detail(request, estimate_id):
    est = get_object_or_404(Estimate.objects.select_related('project'), id=estimate_id)
    return _render_estimate_workspace(request, est)


@login_required
@require_POST
def update_project_budget(request, project_id):
    proj = get_object_or_404(Project, id=project_id)
    proj.client_budget_rub = to_decimal(request.POST.get('client_budget_rub'), '0.00')
    proj.save()
    return _render_estimate_workspace(request, proj.estimate)


@login_required
@require_POST
def update_estimate_commercial_params(request, estimate_id):
    est = get_object_or_404(Estimate, id=estimate_id)
    est.fx_buffer_pct = to_decimal(request.POST.get('fx_buffer_pct'), '4.00')
    est.platform_fee_pct = to_decimal(request.POST.get('platform_fee_pct'), '1.50')
    est.delivery_cost_rub = to_decimal(request.POST.get('delivery_cost_rub'), '35000.00')
    est.installation_cost_rub = to_decimal(request.POST.get('installation_cost_rub'), '0.00')
    est.embed_hidden_costs = bool(request.POST.get('embed_hidden_costs'))
    est.save()
    return _render_estimate_workspace(request, est)


@login_required
@require_POST
def add_manual_estimate_line(request, estimate_id):
    est = get_object_or_404(Estimate, id=estimate_id)
    manual_name = request.POST.get('manual_name', '').strip()
    if manual_name:
        EstimateLine.objects.create(
            estimate=est,
            item=None,
            is_manual_item=True,
            manual_name=manual_name,
            manual_specs=request.POST.get('manual_specs', '').strip(),
            manual_vendor_name=request.POST.get('manual_vendor_name', 'По запросу').strip(),
            manual_currency_code=request.POST.get('manual_currency_code', 'RUB').strip(),
            manual_unit_price=to_decimal(request.POST.get('manual_unit_price'), '10000.00'),
            quantity=max(int(request.POST.get('quantity') or 1), 1),
            is_protected=True,
            markup_pct=to_decimal(request.POST.get('markup_pct'), '25.00'),
            vat_rate_pct=to_decimal(request.POST.get('vat_rate_pct'), '20.00'),
        )
    return _render_estimate_workspace(request, est)


@login_required
@require_POST
def analyze_tz_and_autoselect(request, estimate_id):
    est = get_object_or_404(Estimate, id=estimate_id)
    parse_theater_tz_and_sync(
        est,
        request.POST.get('theater_tz_text', ''),
        hall_seats=request.POST.get('hall_seats'),
        client_budget_rub=request.POST.get('client_budget_rub'),
    )
    return _render_estimate_workspace(request, est)


@login_required
@require_POST
def apply_auto_rigger(request, estimate_id):
    est = get_object_or_404(Estimate, id=estimate_id)
    proj = est.project
    proj.stage_width = to_decimal(request.POST.get('stage_width'), str(proj.stage_width))
    proj.stage_depth = to_decimal(request.POST.get('stage_depth'), str(proj.stage_depth))
    proj.stage_height = to_decimal(request.POST.get('stage_height'), str(proj.stage_height))
    proj.save()
    apply_auto_rigger_lines_to_estimate(est)
    return _render_estimate_workspace(request, est)


@login_required
@require_POST
def add_estimate_line(request, estimate_id):
    est = get_object_or_404(Estimate, id=estimate_id)
    item_obj = get_object_or_404(Item, id=request.POST.get('item_id'))
    qty = max(int(request.POST.get('quantity') or 1), 1)
    line, created = EstimateLine.objects.get_or_create(
        estimate=est,
        item=item_obj,
        is_manual_item=False,
        defaults={
            'quantity': qty,
            'is_protected': True,
            'markup_pct': item_obj.category.default_target_margin_pct if item_obj.category else Decimal('25.00'),
            'vat_rate_pct': Decimal('20.00'),
        }
    )
    if not created:
        line.quantity += qty
        line.save()
    return _render_estimate_workspace(request, est)


@login_required
@require_POST
def update_estimate_line(request, line_id):
    line = get_object_or_404(EstimateLine, id=line_id)
    line.quantity = max(int(request.POST.get('quantity') or line.quantity), 1)
    line.is_protected = bool(request.POST.get('is_protected'))
    line.markup_pct = to_decimal(request.POST.get('markup_pct'), str(line.markup_pct))
    if 'vat_rate_pct' in request.POST:
        line.vat_rate_pct = to_decimal(request.POST.get('vat_rate_pct'), str(line.vat_rate_pct))
    line.save()
    return _render_estimate_workspace(request, line.estimate)


@login_required
@require_POST
def delete_estimate_line(request, line_id):
    line = get_object_or_404(EstimateLine, id=line_id)
    est = line.estimate
    line.delete()
    return _render_estimate_workspace(request, est)


@login_required
@require_POST
def replace_with_analog(request, line_id):
    line = get_object_or_404(EstimateLine, id=line_id)
    if line.item and line.item.recommended_analog:
        line.item = line.item.recommended_analog
        line.save()
    return _render_estimate_workspace(request, line.estimate)


@login_required
@require_POST
def ceo_toggle_unblock_kp(request, estimate_id):
    user = get_active_user(request)
    est = get_object_or_404(Estimate, id=estimate_id)
    if user.is_ceo:
        est.ceo_force_unblock_kp = not est.ceo_force_unblock_kp
        est.save()
    return redirect('estimate_detail', estimate_id=est.id)


@login_required
def document_print(request, estimate_id, doc_type):
    est = get_object_or_404(Estimate.objects.select_related('project'), id=estimate_id)
    ctx = _base_ctx(request, 'kanban')
    ctx.update({
        'estimate': est,
        'project': est.project,
        'doc_type': doc_type,
        'blind_rows': generate_44fz_blind_spec(est) if doc_type == 'tz44' else [],
        'today': date.today(),
    })
    return render(request, 'document_print.html', ctx)


# ==============================================================================
# 3. КАТАЛОГ USTAGE-GROUP.RU, МУЛЬТИВАЛЮТНЫЙ КОНТУР ЦБ РФ И ИМПОРТ EXCEL
# ==============================================================================
@login_required
def catalog_vendors(request):
    ctx = _base_ctx(request, 'catalog')
    user = ctx['active_user']

    cat_id = request.GET.get('cat_id')
    subcat_id = request.GET.get('subcat_id')
    q = request.GET.get('q', '').strip()

    selected_cat_id = int(cat_id) if (cat_id and cat_id.isdigit()) else None
    selected_subcat_id = int(subcat_id) if (subcat_id and subcat_id.isdigit()) else None

    items_qs = Item.objects.select_related('category', 'subcategory', 'vendor', 'vendor__currency').all()
    subcats_qs = SubCategory.objects.select_related('category').all()

    if selected_cat_id:
        items_qs = items_qs.filter(category_id=selected_cat_id)
        subcats_qs = subcats_qs.filter(category_id=selected_cat_id)
    if selected_subcat_id:
        items_qs = items_qs.filter(subcategory_id=selected_subcat_id)
    if q:
        items_qs = (
            items_qs.filter(name__icontains=q)
            | items_qs.filter(brand__icontains=q)
            | items_qs.filter(sku__icontains=q)
            | items_qs.filter(specs_text__icontains=q)
        )

    ctx.update({
        'currencies': CurrencyRate.objects.filter(is_active=True).order_by('code'),
        'all_currencies_db': CurrencyRate.objects.all().order_by('code'),
        'vendors': Vendor.objects.select_related('currency').all(),
        'categories': Category.objects.all(),
        'subcategories': subcats_qs,
        'all_subcategories': SubCategory.objects.select_related('category').all(),
        'items': items_qs,
        'selected_cat_id': selected_cat_id,
        'selected_subcat_id': selected_subcat_id,
        'search_query': q,
        'can_view_currencies': user.can_perm('view_currencies'),
        'can_edit_currencies': user.can_perm('edit_currencies'),
        'can_view_vendors': user.can_perm('view_vendors'),
        'can_edit_vendors': user.can_perm('edit_vendors'),
    })
    return render(request, 'catalog_vendors.html', ctx)


@login_required
@require_POST
def sync_cbr_currencies(request):
    codes = sync_currencies_from_cbr_api()
    messages.success(request, f"Курсы валют синхронизированы с API ЦБ РФ ({', '.join(codes[:6])}...).")
    return redirect('catalog_vendors')


@login_required
@require_POST
def update_currency(request):
    remove_id = request.POST.get('remove_currency_id')
    if remove_id:
        curr = get_object_or_404(CurrencyRate, id=remove_id)
        if curr.code != 'RUB':
            curr.is_active = False
            curr.save()
            messages.success(request, f"Валюта {curr.code} скрыта с экрана.")
    return redirect('catalog_vendors')


@login_required
@require_POST
def add_currency(request):
    ids = request.POST.getlist('activate_currency_ids')
    if ids:
        CurrencyRate.objects.filter(id__in=ids).update(is_active=True)
        messages.success(request, "Выбранные валюты справочника ЦБ РФ добавлены в таблицу.")
    return redirect('catalog_vendors')


@login_required
@require_POST
def manage_vendors(request):
    user = get_active_user(request)
    if not user.can_perm('edit_vendors'):
        return redirect('catalog_vendors')

    action = request.POST.get('action')
    if action == 'add_vendor':
        curr = CurrencyRate.objects.filter(id=request.POST.get('currency_id')).first() or CurrencyRate.objects.first()
        Vendor.objects.create(
            name=request.POST.get('name', '').strip(),
            country=request.POST.get('country', '').strip(),
            contact_info=request.POST.get('contact_info', '').strip(),
            currency=curr,
            base_discount=to_decimal(request.POST.get('base_discount'), '15.00'),
            protected_discount=to_decimal(request.POST.get('protected_discount'), '30.00'),
        )
        messages.success(request, "Поставщик добавлен в Реестр ООО «Юстейдж».")
    elif action == 'update_vendor':
        v = get_object_or_404(Vendor, id=request.POST.get('vendor_id'))
        v.name = request.POST.get('name', v.name).strip()
        v.country = request.POST.get('country', v.country).strip()
        v.contact_info = request.POST.get('contact_info', v.contact_info).strip()
        if request.POST.get('currency_id'):
            v.currency_id = int(request.POST.get('currency_id'))
        v.base_discount = to_decimal(request.POST.get('base_discount'), str(v.base_discount))
        v.protected_discount = to_decimal(request.POST.get('protected_discount'), str(v.protected_discount))
        v.save()
        messages.success(request, f"Данные поставщика «{v.name}» обновлены.")
    elif action == 'delete_vendor':
        Vendor.objects.filter(id=request.POST.get('vendor_id')).delete()
    return redirect('catalog_vendors')


@login_required
@require_POST
def upload_price(request):
    vendor = get_object_or_404(Vendor, id=request.POST.get('vendor_id'))
    cat_id = request.POST.get('category_id')
    subcat_id = request.POST.get('subcategory_id')

    cat = Category.objects.filter(id=cat_id).first() if (cat_id and cat_id.isdigit()) else None
    subcat = SubCategory.objects.filter(id=subcat_id).first() if (subcat_id and subcat_id.isdigit()) else None

    if 'excel_file' in request.FILES:
        cnt = parse_vendor_excel_price(request.FILES['excel_file'], vendor, cat, subcat)
        mode_str = f"в подраздел «{subcat.name}»" if subcat else "с автораспределением по подразделам"
        messages.success(request, f"Импортировано и обновлено {cnt} поз. от поставщика «{vendor.name}» ({mode_str}).")
    return redirect('catalog_vendors')


# ==============================================================================
# 4. ЛОГИСТИКА ТК «ДЕЛОВЫЕ ЛИНИИ» И «СДЭК»
# ==============================================================================
@login_required
def logistics_dellin(request):
    ctx = _base_ctx(request, 'logistics')
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add_shipment':
            carrier = request.POST.get('carrier', 'dellin')
            track_num = request.POST.get('tracking_number', '').strip()
            proj = get_object_or_404(Project, id=request.POST.get('project_id'))
            vendor = Vendor.objects.filter(id=request.POST.get('vendor_id')).first()
            equip = request.POST.get('equipment_summary', '').strip()
            weight = to_decimal(request.POST.get('cargo_weight_kg'), '50.0')

            api_res = track_shipment_by_carrier_api(carrier, track_num)
            DellinShipment.objects.create(
                carrier=carrier,
                tracking_number=track_num,
                project=proj,
                vendor=vendor,
                equipment_summary=equip,
                route_info=api_res['route_info'],
                cargo_weight_kg=weight,
                status=api_res['status'],
                eta_date=api_res['eta_date'],
                is_auto_created=False,
            )
            messages.success(request, f"Отгрузка {track_num} добавлена и проверена по API.")
        elif action == 'update_shipment':
            sh = get_object_or_404(DellinShipment, id=request.POST.get('shipment_id'))
            sh.carrier = request.POST.get('carrier', sh.carrier)
            sh.tracking_number = request.POST.get('tracking_number', sh.tracking_number).strip()
            sh.equipment_summary = request.POST.get('equipment_summary', sh.equipment_summary).strip()
            sh.route_info = request.POST.get('route_info', sh.route_info).strip()
            sh.cargo_weight_kg = to_decimal(request.POST.get('cargo_weight_kg'), str(sh.cargo_weight_kg))
            sh.status = request.POST.get('status', sh.status)
            if request.POST.get('eta_date'):
                sh.eta_date = request.POST.get('eta_date')
            sh.save()
        elif action == 'check_api':
            sh = get_object_or_404(DellinShipment, id=request.POST.get('shipment_id'))
            api_res = track_shipment_by_carrier_api(sh.carrier, sh.tracking_number)
            sh.status = api_res['status']
            sh.route_info = api_res['route_info']
            sh.eta_date = api_res['eta_date']
            sh.save()
            messages.success(request, f"Статус накладной {sh.tracking_number} обновлен через API.")
        elif action == 'delete_shipment':
            DellinShipment.objects.filter(id=request.POST.get('shipment_id')).delete()
        return redirect('logistics_dellin')

    ctx.update({
        'shipments': DellinShipment.objects.select_related('project', 'vendor').all().order_by('-id'),
        'projects': Project.objects.all(),
        'vendors': Vendor.objects.all(),
    })
    return render(request, 'logistics_dellin.html', ctx)


# ==============================================================================
# 5. ЦИФРОВОЙ ПАСПОРТ ОБЪЕКТА (ПРОГНОЗНЫЙ УЧЕТ РЕСУРСА ОБОРУДОВАНИЯ)
# ==============================================================================
@login_required
def digital_twin(request):
    ctx = _base_ctx(request, 'twin')
    user = ctx['active_user']

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add_profile' and user.can_perm('edit_venue_profiles'):
            VenueCalculationProfile.objects.create(
                name=request.POST.get('name', '').strip(),
                daily_hours=to_decimal(request.POST.get('daily_hours'), '3.50'),
            )
        elif action == 'update_profile' and user.can_perm('edit_venue_profiles'):
            p = get_object_or_404(VenueCalculationProfile, id=request.POST.get('profile_id'))
            p.name = request.POST.get('name', p.name).strip()
            p.daily_hours = to_decimal(request.POST.get('daily_hours'), str(p.daily_hours))
            p.save()
        elif action == 'delete_profile' and user.can_perm('edit_venue_profiles'):
            VenueCalculationProfile.objects.filter(id=request.POST.get('profile_id')).delete()
        elif action == 'add_asset':
            v_name = request.POST.get('venue_name', '').strip()
            linked_proj = Project.objects.filter(title__icontains=v_name).first()
            DigitalStageAsset.objects.create(
                serial_number=request.POST.get('serial_number', '').strip(),
                project=linked_proj,
                venue_name=v_name,
                item_id=int(request.POST.get('item_id')),
                zone_installed=request.POST.get('zone_installed', '').strip(),
                venue_profile_id=int(request.POST.get('venue_profile_id')),
                max_resource_hours=max(int(request.POST.get('max_resource_hours') or 2000), 100),
                is_auto_created=False,
            )
        elif action == 'update_asset':
            a = get_object_or_404(DigitalStageAsset, id=request.POST.get('asset_id'))
            a.serial_number = request.POST.get('serial_number', a.serial_number).strip()
            a.venue_name = request.POST.get('venue_name', a.venue_name).strip()
            a.zone_installed = request.POST.get('zone_installed', a.zone_installed).strip()
            a.item_id = int(request.POST.get('item_id') or a.item_id)
            a.venue_profile_id = int(request.POST.get('venue_profile_id') or a.venue_profile_id)
            a.max_resource_hours = max(int(request.POST.get('max_resource_hours') or a.max_resource_hours), 100)
            a.save()
        elif action == 'delete_asset':
            DigitalStageAsset.objects.filter(id=request.POST.get('asset_id')).delete()
        return redirect('digital_twin')

    ctx.update({
        'profiles': VenueCalculationProfile.objects.all(),
        'assets': DigitalStageAsset.objects.select_related('project', 'item', 'venue_profile').all(),
        'projects': Project.objects.all(),
        'items': Item.objects.all(),
        'can_view_venue_profiles': user.can_perm('view_venue_profiles'),
        'can_edit_venue_profiles': user.can_perm('edit_venue_profiles'),
    })
    return render(request, 'digital_twin.html', ctx)


# ==============================================================================
# 6. СОТРУДНИКИ, МАТРИЦА РОЛЕЙ И ЛИЧНЫЙ ПРОФИЛЬ
# ==============================================================================
@login_required
def staff_management(request):
    ctx = _base_ctx(request, 'staff')
    user = ctx['active_user']

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'update_own_profile':
            new_pass = request.POST.get('new_password', '').strip()
            if new_pass:
                user.set_password(new_pass)
                user.save()
                login(request, user)
                messages.success(request, "Ваш пароль успешно обновлен.")
        elif action == 'add_employee' and user.can_admin_roles:
            pos = get_object_or_404(StaffPosition, id=request.POST.get('position_id'))
            emp = CustomUser.objects.create(
                tab_number=request.POST.get('tab_number', '00030').strip(),
                username=request.POST.get('username', '').strip(),
                first_name=request.POST.get('first_name', '').strip(),
                email=request.POST.get('email', '').strip().lower(),
                position=pos,
            )
            emp.set_password(request.POST.get('password', 'admin12345').strip())
            emp.save()
            messages.success(request, f"Сотрудник {emp.first_name} добавлен в штат.")
        elif action == 'update_employee' and user.can_admin_roles:
            emp = get_object_or_404(CustomUser, id=request.POST.get('employee_id'))
            emp.tab_number = request.POST.get('tab_number', emp.tab_number).strip()
            emp.first_name = request.POST.get('first_name', emp.first_name).strip()
            emp.email = request.POST.get('email', emp.email).strip().lower()
            emp.position_id = int(request.POST.get('position_id') or emp.position_id)
            if user.is_ceo:
                emp.can_manage_roles = bool(request.POST.get('can_manage_roles'))
            new_p = request.POST.get('new_password', '').strip()
            if new_p:
                emp.set_password(new_p)
            emp.save()
            messages.success(request, f"Профиль сотрудника {emp.first_name} обновлен.")
        elif action == 'delete_employee' and user.can_admin_roles:
            emp = get_object_or_404(CustomUser, id=request.POST.get('employee_id'))
            if emp.id != user.id:
                emp.delete()
        elif action in ('add_position', 'update_position') and user.can_admin_roles:
            if action == 'add_position':
                pos = StaffPosition(code=request.POST.get('code', '').strip())
            else:
                pos = get_object_or_404(StaffPosition, id=request.POST.get('position_id'))
            pos.title = request.POST.get('title', pos.title).strip()
            for flag in [
                'is_management', 'is_sales_manager',
                'view_currencies', 'edit_currencies',
                'view_vendors', 'edit_vendors',
                'view_venue_profiles', 'edit_venue_profiles',
                'view_kanban', 'edit_kanban',
                'view_estimates', 'edit_estimates',
                'view_catalog', 'edit_catalog',
                'view_logistics', 'edit_logistics',
                'view_twin', 'edit_twin'
            ]:
                setattr(pos, flag, bool(request.POST.get(flag)))
            pos.save()
            messages.success(request, f"Права роли «{pos.title}» сохранены.")
        elif action == 'delete_position' and user.can_admin_roles:
            StaffPosition.objects.filter(id=request.POST.get('position_id')).exclude(code='ceo').delete()
        return redirect('staff_management')

    ctx.update({
        'employees': CustomUser.objects.select_related('position').all().order_by('tab_number'),
        'positions': StaffPosition.objects.all(),
    })
    return render(request, 'staff_management.html', ctx)