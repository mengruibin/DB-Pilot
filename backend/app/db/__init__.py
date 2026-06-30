"""
DB-Pilot 数据库适配器包。

提供可插拔的数据库适配器，通过 AdapterFactory 按 db_type 懒加载。
支持的数据库类型：MySQL、PostgreSQL、Oracle（依据 PRD §6.2）。
"""
