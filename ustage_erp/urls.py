from django.contrib import admin
from django.urls import path
from projects import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.dashboard, name='dashboard'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('forgot-password/', views.forgot_password_view, name='forgot_password'),

    path('manager/update-plan/', views.update_manager_plan, name='update_manager_plan'),
    path('project/create/', views.create_kanban_project, name='create_kanban_project'),
    path('project/<int:project_id>/move/', views.move_kanban_card, name='move_kanban_card'),
    path('project/<int:project_id>/budget/', views.update_project_budget, name='update_project_budget'),

    path('estimate/<int:estimate_id>/', views.estimate_detail, name='estimate_detail'),
    path('estimate/<int:estimate_id>/update-commercial-params/', views.update_estimate_commercial_params, name='update_estimate_commercial_params'),
    path('estimate/<int:estimate_id>/add-manual-line/', views.add_manual_estimate_line, name='add_manual_estimate_line'),
    path('estimate/<int:estimate_id>/analyze-tz/', views.analyze_tz_and_autoselect, name='analyze_tz_and_autoselect'),
    path('estimate/<int:estimate_id>/auto-rigger/', views.apply_auto_rigger, name='apply_auto_rigger'),
    path('estimate/<int:estimate_id>/add-line/', views.add_estimate_line, name='add_estimate_line'),
    path('estimateline/<int:line_id>/update/', views.update_estimate_line, name='update_estimate_line'),
    path('estimateline/<int:line_id>/delete/', views.delete_estimate_line, name='delete_estimate_line'),
    path('estimateline/<int:line_id>/analog/', views.replace_with_analog, name='replace_with_analog'),
    path('estimate/<int:estimate_id>/ceo-unblock/', views.ceo_toggle_unblock_kp, name='ceo_toggle_unblock_kp'),
    path('estimate/<int:estimate_id>/print/<str:doc_type>/', views.document_print, name='document_print'),

    path('task/create/', views.create_kanban_task, name='create_kanban_task'),
    path('task/<int:task_id>/', views.task_detail, name='task_detail'),
    path('task/<int:task_id>/move-toggle/', views.move_or_toggle_task, name='move_or_toggle_task'),
    path('kanban/columns/', views.manage_kanban_columns, name='manage_kanban_columns'),

    path('catalog/', views.catalog_vendors, name='catalog_vendors'),
    path('catalog/sync-cbr/', views.sync_cbr_currencies, name='sync_cbr_currencies'),
    path('catalog/currency/update/', views.update_currency, name='update_currency'),
    path('catalog/currency/add/', views.add_currency, name='add_currency'),
    path('catalog/vendors/', views.manage_vendors, name='manage_vendors'),
    path('catalog/upload-price/', views.upload_price, name='upload_price'),

    path('logistics/', views.logistics_dellin, name='logistics_dellin'),
    path('digital-twin/', views.digital_twin, name='digital_twin'),
    path('staff/', views.staff_management, name='staff_management'),
]
