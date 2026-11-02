from django.contrib import admin
from .models import (
    KanbanColumn, Project, KanbanTask, Estimate, EstimateLine,
    DellinShipment, VenueCalculationProfile, DigitalStageAsset
)


@admin.register(KanbanColumn)
class KanbanColumnAdmin(admin.ModelAdmin):
    list_display = ('order', 'title', 'is_completed_stage')


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('title', 'client', 'manager', 'kanban_column', 'is_delivery_arrived', 'deadline')


@admin.register(KanbanTask)
class KanbanTaskAdmin(admin.ModelAdmin):
    list_display = ('title', 'project', 'kanban_column', 'assigned_to', 'assigned_by', 'is_completed')


@admin.register(Estimate)
class EstimateAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'planned_cost_rub', 'total_client_price_rub', 'gross_margin_pct')


@admin.register(DellinShipment)
class DellinShipmentAdmin(admin.ModelAdmin):
    list_display = ('tracking_number', 'project', 'vendor', 'status', 'eta_date')


@admin.register(VenueCalculationProfile)
class VenueCalculationProfileAdmin(admin.ModelAdmin):
    list_display = ('name', 'daily_hours', 'is_dmx_counter')


@admin.register(DigitalStageAsset)
class DigitalStageAssetAdmin(admin.ModelAdmin):
    list_display = ('serial_number', 'item', 'project', 'venue_profile', 'operating_hours', 'max_resource_hours')