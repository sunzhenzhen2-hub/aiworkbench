#!/usr/bin/env python3
"""生成 Excel 项目导入模板。"""

from pathlib import Path

from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
wb = Workbook()
ws = wb.active
ws.title = "项目导入"
ws.append(["项目名", "项目类型", "分级", "负责人用户名"])
ws.append(["示例项目A", "software", "green", "owner1"])
ws.append(["示例项目B", "engineering", "yellow", "owner1"])
wb.save(ROOT / "docs/project_import_template.xlsx")
print("模板已生成: docs/project_import_template.xlsx")
