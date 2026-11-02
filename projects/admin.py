from django.contrib import admin
from projects.models import (
    KanbanColumn, Project, KanbanTask, TaskComment, TaskAttachment,
    Estimate, EstimateLine, DellinShipment, VenueCalculationProfile, DigitalStageAsset
)

@admin.register(KanbanColumn)
class KanbanColumnAdmin(admin.ModelAdmin):
    list_display = ('id', 'order', 'title', 'is_logistics_stage', 'is_completed_stage')

@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'client', 'client_budget_rub', 'manager', 'kanban_column', 'is_delivery_arrived')
    search_fields = ('title', 'client')

@admin.register(KanbanTask)
class KanbanTaskAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'project', 'kanban_column', 'assigned_to', 'due_date', 'is_completed')
    list_filter = ('is_completed', 'kanban_column')

@admin.register(Estimate)
class EstimateAdmin(admin.ModelAdmin):
    list_display = ('id', 'project', 'version', 'total_client_price_rub', 'gross_margin_pct', 'protection_ratio_pct', 'ceo_force_unblock_kp')

@admin.register(EstimateLine)
class EstimateLineAdmin(admin.ModelAdmin):
    list_display = ('id', 'estimate', 'display_name', 'quantity', 'is_protected', 'markup_pct', 'vat_rate_pct', 'client_line_total_rub')

@admin.register(DellinShipment)
class DellinShipmentAdmin(admin.ModelAdmin):
    list_display = ('id', 'carrier', 'tracking_number', 'project', 'status', 'eta_date', 'is_auto_created')
    list_filter = ('carrier', 'status')

@admin.register(VenueCalculationProfile)
class VenueCalculationProfileAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'daily_hours')

@admin.register(DigitalStageAsset)
class DigitalStageAssetAdmin(admin.ModelAdmin):
    list_display = ('serial_number', 'venue_name', 'item', 'zone_installed', 'installed_date', 'max_resource_hours')
    search_fields = ('serial_number', 'venue_name')