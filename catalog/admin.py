from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import StaffPosition, CustomUser, CurrencyRate, Vendor, Category, SubCategory, Item

admin.site.site_header = "ООО «ЮСТЕЙДЖ» — Управление базой данных"


@admin.register(StaffPosition)
class StaffPositionAdmin(admin.ModelAdmin):
    list_display = ('code', 'title', 'is_management', 'is_sales_manager')


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    list_display = ('tab_number', 'username', 'first_name', 'position', 'role', 'is_staff')


@admin.register(CurrencyRate)
class CurrencyRateAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'rate', 'updated_at')


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ('name', 'country', 'currency', 'base_discount', 'protected_discount')


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'default_target_margin_pct')


@admin.register(SubCategory)
class SubCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'code', 'calc_norm_per_100_seats')
    list_filter = ('category',)


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ('sku', 'brand', 'name', 'category', 'subcategory', 'vendor', 'retail_price', 'purchase_price', 'stock_status')
    list_filter = ('category', 'subcategory', 'vendor', 'stock_status')